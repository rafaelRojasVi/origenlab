"""Baseline vs augmented reconciliation, coverage debt, and change classes."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from ..commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
)
from .constants import (
    CHANGE_ANNEX_CHANGED_CATEGORY,
    CHANGE_ANNEX_CREATED_CONFLICT,
    CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION,
    CHANGE_ANNEX_STRENGTHENED_EXISTING,
    CHANGE_BASELINE_POSITIVE_ANNEX_INCOMPLETE,
    CHANGE_NEW_ANNEX_POSITIVE,
    CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED,
    CHANGE_UNCHANGED_POSITIVE,
    COMPARISON_VERSION,
    COVERAGE_DEBT_OUTCOMES,
    EFFECT_CONTRADICTED_EXISTING,
    EFFECT_CREATED_NEW_CLAIM,
    EFFECT_STRENGTHENED_EXISTING,
    EFFECT_SUPPORTING_CONTEXT_ONLY,
    INTERPRETATION_ABSENCE_UNPROVEN,
    INTERPRETATION_NO_ANEXOS,
    INTERPRETATION_NO_CLAIM_COVERAGE_COMPLETE,
    INTERPRETATION_PRESENCE_PROVEN,
)
from .models import AnexoClaim, AnexoEvidenceSegment, CoverageDebt
from .risk import accessory_risk_codes

# Excerpt kept with each claim. Long enough to judge the match by eye, short
# enough that review packets stay reviewable.
EXCERPT_MAX_CHARS = 400


def build_coverage_debt(
    tender_id: str,
    *,
    bundle: dict[str, Any] | None,
    attachments: Sequence[dict[str, Any]] = (),
) -> CoverageDebt:
    """Summarize what could not be read for this tender.

    Absence of a bundle and an incomplete bundle are different states, and both
    are different from a complete one. Only a complete bundle can support any
    statement about what the annexes do *not* contain.
    """
    if bundle is None:
        return CoverageDebt(
            tender_id=tender_id,
            has_anexo_bundle=False,
            extraction_complete=False,
            attachments_discovered=0,
            attachments_downloaded=0,
            incomplete_reason_codes=(),
            debt_outcome_counts={},
            unread_attachment_ids=(),
        )

    outcome_counts = dict(bundle.get("outcome_counts") or {})
    debt = {
        code: int(outcome_counts[code])
        for code in COVERAGE_DEBT_OUTCOMES
        if int(outcome_counts.get(code, 0)) > 0
    }
    unread = tuple(
        sorted(
            str(a.get("attachment_id"))
            for a in attachments
            if str(a.get("outcome")) in COVERAGE_DEBT_OUTCOMES
        )
    )
    return CoverageDebt(
        tender_id=tender_id,
        has_anexo_bundle=True,
        extraction_complete=bool(bundle.get("extraction_complete")),
        attachments_discovered=int(bundle.get("attachments_discovered") or 0),
        attachments_downloaded=int(bundle.get("attachments_downloaded") or 0),
        incomplete_reason_codes=tuple(bundle.get("incomplete_reason_codes") or ()),
        debt_outcome_counts=debt,
        unread_attachment_ids=unread,
    )


def _claim_id(tender_id: str, segment_id: str, category: str) -> str:
    payload = f"anexo_claim\0{tender_id}\0{segment_id}\0{category}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def build_claims(
    pairs: Sequence[tuple[EvidenceUnitRelevanceDecision, AnexoEvidenceSegment]],
    *,
    baseline_categories: frozenset[str],
    augmented_categories: frozenset[str],
    conflict: bool,
    taxonomy_version: str,
    rules_version: str,
) -> tuple[AnexoClaim, ...]:
    """Emit one claim per (annex segment, recognized category).

    Segments that recognized no canonical category produce no claim; there is
    no path that yields a category without a resolvable chunk and locator.
    """
    claims: list[AnexoClaim] = []
    for decision, segment in pairs:
        if not decision.canonical_equipment_classes:
            continue
        excerpt = segment.text_raw[:EXCERPT_MAX_CHARS]
        rule_ids = tuple(sorted({span.rule_id for span in decision.matched_spans}))
        risk_codes = accessory_risk_codes(segment.text_raw, decision.matched_spans)
        for category in sorted(decision.canonical_equipment_classes):
            if category not in augmented_categories:
                effect = EFFECT_SUPPORTING_CONTEXT_ONLY
            elif category in baseline_categories:
                effect = EFFECT_STRENGTHENED_EXISTING
            elif conflict:
                effect = EFFECT_CONTRADICTED_EXISTING
            else:
                effect = EFFECT_CREATED_NEW_CLAIM
            claims.append(
                AnexoClaim(
                    claim_id=_claim_id(segment.tender_id, segment.segment_id, category),
                    tender_id=segment.tender_id,
                    matched_category=category,
                    attachment_id=segment.attachment_id,
                    evidence_attachment_id=segment.evidence_attachment_id,
                    safe_filename=segment.safe_filename,
                    attachment_sha256=segment.attachment_sha256,
                    detected_format=segment.detected_format,
                    document_role=segment.document_role,
                    container_attachment_id=segment.container_attachment_id,
                    member_path=segment.member_path,
                    chunk_id=segment.chunk_id,
                    contributing_chunk_ids=(segment.chunk_id,),
                    segment_id=segment.segment_id,
                    locator_type=segment.locator_type,
                    locator=dict(segment.locator),
                    segment_kind=segment.segment_kind,
                    segment_locator=dict(segment.segment_locator),
                    locator_display=segment.locator_display,
                    evidence_excerpt=excerpt,
                    evidence_text_sha256=hashlib.sha256(
                        segment.text_raw.encode("utf-8")
                    ).hexdigest(),
                    relevance_class=decision.relevance_class,
                    confidence_band=decision.confidence_band,
                    evidence_tier=decision.evidence_tier,
                    matched_rule_ids=rule_ids,
                    unit_decision_id=decision.unit_decision_id,
                    taxonomy_version=taxonomy_version,
                    rules_version=rules_version,
                    recognition_version=COMPARISON_VERSION,
                    existed_in_baseline=category in baseline_categories,
                    annex_effect=effect,
                    risk_reason_codes=risk_codes,
                )
            )
    claims.sort(key=lambda c: (c.tender_id, c.matched_category, c.claim_id))
    return tuple(claims)


def is_conflict(*, baseline_decision, augmented_decision) -> bool:
    """Did adding annex evidence push the tender into PR5D's own conflict state?

    PR5D signals irreconcilable evidence as `ambiguous` +
    `mixed_requires_review`. Reusing that signal keeps the conflict definition
    owned by the production rules rather than reinvented here.
    """
    became_mixed = (
        augmented_decision.relevance_class == "ambiguous"
        and augmented_decision.product_resolution_status == "mixed_requires_review"
    )
    was_mixed = (
        baseline_decision.relevance_class == "ambiguous"
        and baseline_decision.product_resolution_status == "mixed_requires_review"
    )
    return became_mixed and not was_mixed


def classify_change(
    *,
    baseline_categories: frozenset[str],
    augmented_categories: frozenset[str],
    annex_categories: frozenset[str],
    coverage: CoverageDebt,
    conflict: bool,
) -> tuple[str, str]:
    """Return `(change_class, interpretation)` for one tender.

    Interpretation is deliberately separate from the change class: it records
    what a *lack* of annex matches is allowed to mean. Incomplete extraction
    can never be read as proof that the annexes contain no matching equipment.
    """
    if not coverage.has_anexo_bundle:
        change = (
            CHANGE_UNCHANGED_POSITIVE
            if baseline_categories
            else CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED
        )
        return change, INTERPRETATION_NO_ANEXOS

    if annex_categories:
        interpretation = INTERPRETATION_PRESENCE_PROVEN
    elif coverage.is_complete:
        interpretation = INTERPRETATION_NO_CLAIM_COVERAGE_COMPLETE
    else:
        interpretation = INTERPRETATION_ABSENCE_UNPROVEN

    if conflict:
        return CHANGE_ANNEX_CREATED_CONFLICT, interpretation

    new_categories = augmented_categories - baseline_categories

    if not baseline_categories:
        if annex_categories:
            return CHANGE_NEW_ANNEX_POSITIVE, interpretation
        if not coverage.is_complete:
            return CHANGE_ANNEX_INCOMPLETE_NO_CONCLUSION, interpretation
        return CHANGE_UNCHANGED_NEGATIVE_OR_UNRESOLVED, interpretation

    if new_categories:
        return CHANGE_ANNEX_CHANGED_CATEGORY, interpretation
    if annex_categories & baseline_categories:
        return CHANGE_ANNEX_STRENGTHENED_EXISTING, interpretation
    if not coverage.is_complete:
        return CHANGE_BASELINE_POSITIVE_ANNEX_INCOMPLETE, interpretation
    return CHANGE_UNCHANGED_POSITIVE, interpretation
