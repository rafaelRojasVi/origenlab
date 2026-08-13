"""Guarded annex augmentation of the canonical production PR5D result.

This module owns orchestration and provenance binding only.  Product text is
classified by the canonical PR5D classifier and affected tenders are rolled up
by the canonical PR5D aggregator.  It does not acquire evidence, widen catalog
capability, persist state, or implement an operator queue.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    is_mercado_publico_codigo_shape,
    normalize_mercado_publico_codigo,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    TenderAttachmentBundle,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.compare import (
    build_claims,
    is_conflict,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.constants import (
    ANEXO_SOURCE_PLANE,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.models import (
    AnexoClaim,
    AnexoEvidenceSegment,
    CoverageDebt,
)
from origenlab_email_pipeline.commercial_procurement_anexo_recognition.segment import (
    segment_chunk,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    IDENTITY_NS_MERCADO_PUBLICO,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CandidatePlanResult,
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    STRONG_RELEVANCE_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance import (
    aggregate as relevance_aggregate,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance import (
    rules as relevance_rules,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evaluation import (
    is_manual_review_prediction,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.evidence_adapter import (
    MaterializationExpectation,
    recompute_unit_id,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.fingerprint import (
    all_fingerprints,
    relevance_input_fingerprint,
    relevance_rules_fingerprint,
    relevance_taxonomy_fingerprint,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    ProductRelevancePlanResult,
    ProductTextUnit,
    TenderRelevanceDecision,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.normalize import (
    normalize_product_text,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    coverage_from_bundle,
    resolve_evidence_chunks,
)

from .constants import ANEXO_INTEGRATION_VERSION
from .models import VerifiedAnexoEvidence


class AnexoIntegrationError(ValueError):
    """Annex evidence cannot be bound or reconciled without ambiguity."""


@dataclass(frozen=True)
class AnnexUnitBinding:
    """One verified annex segment bound to one production PR5C identity."""

    tender_id: str
    coalesced_tender_id: str
    tender_semantic_digest: str
    unit: ProductTextUnit
    decision: EvidenceUnitRelevanceDecision
    segment: AnexoEvidenceSegment
    applied: bool
    withholding_reason: str | None

    def provenance_dict(self) -> dict[str, Any]:
        """PII/token-safe lineage; deliberately excludes annex text/excerpts."""
        return {
            "tender_id": self.tender_id,
            "coalesced_tender_id": self.coalesced_tender_id,
            "tender_semantic_digest": self.tender_semantic_digest,
            "unit_id": self.unit.unit_id,
            "unit_decision_id": self.decision.unit_decision_id,
            "relevance_class": self.decision.relevance_class,
            "canonical_equipment_classes": list(
                self.decision.canonical_equipment_classes
            ),
            "positive_reason_codes": list(self.decision.positive_reason_codes),
            "negative_reason_codes": list(self.decision.negative_reason_codes),
            "ambiguity_reason_codes": list(self.decision.ambiguity_reason_codes),
            "matched_rule_ids": sorted(
                {span.rule_id for span in self.decision.matched_spans}
            ),
            "attachment_id": self.segment.attachment_id,
            "evidence_attachment_id": self.segment.evidence_attachment_id,
            "attachment_sha256": self.segment.attachment_sha256,
            "chunk_id": self.segment.chunk_id,
            "segment_id": self.segment.segment_id,
            "container_attachment_id": self.segment.container_attachment_id,
            "member_path": self.segment.member_path,
            "locator_type": self.segment.locator_type,
            "locator": dict(self.segment.locator),
            "segment_kind": self.segment.segment_kind,
            "segment_locator": dict(self.segment.segment_locator),
            "locator_display": self.segment.locator_display,
            "source_format": self.segment.detected_format,
            "evidence_tier": self.segment.evidence_tier,
            "applied": self.applied,
            "withholding_reason": self.withholding_reason,
        }


@dataclass(frozen=True)
class AnexoAugmentationResult:
    """Augmented PR5D plus the data required for downstream reconciliation."""

    plan: ProductRelevancePlanResult
    baseline_plan: ProductRelevancePlanResult
    evidence_digest: str
    bound_tender_ids: tuple[str, ...]
    unused_tender_ids: tuple[str, ...]
    affected_coalesced_tender_ids: tuple[str, ...]
    bindings: tuple[AnnexUnitBinding, ...]
    claims: tuple[AnexoClaim, ...]
    coverage_by_tender: dict[str, CoverageDebt]
    semantic_digest: str


def _canonical_tender_code(tender: CoalescedProcurementTender) -> str | None:
    """Return the ChileCompra native code only for an authoritative identity."""
    namespace = str(tender.identity_namespace or "").strip()
    # ``tender_key_kind`` is display/provenance metadata and deliberately
    # preserves PR4's ``codigo_externo`` on PR4/both-source groups.  The actual
    # cross-source identity contract is namespace + canonical MP-code shape.
    if namespace != IDENTITY_NS_MERCADO_PUBLICO:
        return None
    key = str(tender.canonical_tender_key or "").strip()
    normalized = normalize_mercado_publico_codigo(key)
    if not is_mercado_publico_codigo_shape(normalized):
        return None
    assert normalized is not None
    return normalized


def _production_identity_index(
    plan: CandidatePlanResult,
) -> dict[str, tuple[str, CoalescedProcurementTender]]:
    index: dict[str, tuple[str, CoalescedProcurementTender]] = {}
    for tender in plan.coalesced_tenders:
        code = _canonical_tender_code(tender)
        if code is None:
            continue
        normalized = code.casefold()
        prior = index.get(normalized)
        if (
            prior is not None
            and prior[1].coalesced_tender_id != tender.coalesced_tender_id
        ):
            raise AnexoIntegrationError(
                f"ambiguous production tender identity for annex code {code!r}"
            )
        index[normalized] = (code, tender)
    return index



def _segments_for_bundle(
    bundle: TenderAttachmentBundle,
) -> tuple[AnexoEvidenceSegment, ...]:
    """Segment only T1-B-resolved canonical evidence chunks.

    T1-B owns attachment/member/hash/locator validation.  E2 segmentation is
    reused only to restore row/block grain for the canonical PR5D classifier.
    """
    segments: list[AnexoEvidenceSegment] = []

    for source in resolve_evidence_chunks(bundle):
        chunk = source.chunk

        # segment_chunk() only needs the manifest fields below.  For archive
        # members the key is the member/chunk attachment identity, while the
        # verified portal container and member provenance remain on the chunk.
        attachment_row = {
            "attachment_id": chunk.attachment_id,
            "evidence_attachment_id": source.evidence_attachment_id,
            "safe_filename": source.safe_filename,
            "sha256": source.attachment_sha256,
            "detected_format": source.detected_format,
            "role_tag": source.document_role,
        }

        produced = segment_chunk(
            chunk.to_dict(),
            attachment_index={
                chunk.attachment_id: attachment_row,
            },
        )

        for segment in produced:
            if segment.tender_id != bundle.tender_id:
                raise AnexoIntegrationError(
                    f"cross-tender resolved segment: {segment.segment_id}"
                )
            if segment.chunk_id != chunk.chunk_id:
                raise AnexoIntegrationError(
                    f"segment/chunk provenance mismatch: {segment.segment_id}"
                )
            if segment.attachment_id != chunk.attachment_id:
                raise AnexoIntegrationError(
                    f"segment attachment mismatch: {segment.segment_id}"
                )
            if segment.evidence_attachment_id != source.evidence_attachment_id:
                raise AnexoIntegrationError(
                    f"segment evidence attachment mismatch: {segment.segment_id}"
                )
            if segment.attachment_sha256 != source.attachment_sha256:
                raise AnexoIntegrationError(
                    f"segment attachment digest mismatch: {segment.segment_id}"
                )
            if segment.detected_format != source.detected_format:
                raise AnexoIntegrationError(
                    f"segment source format mismatch: {segment.segment_id}"
                )
            if segment.container_attachment_id != chunk.container_attachment_id:
                raise AnexoIntegrationError(
                    f"segment container provenance mismatch: {segment.segment_id}"
                )
            if segment.member_path != chunk.member_path:
                raise AnexoIntegrationError(
                    f"segment member provenance mismatch: {segment.segment_id}"
                )

        segments.extend(produced)

    return tuple(segments)

def _annex_unit(
    *,
    tender: CoalescedProcurementTender,
    source_tender_id: str,
    segment: AnexoEvidenceSegment,
    tender_semantic_digest: str,
) -> ProductTextUnit:
    evidence_ref_id = (
        f"anexo:{source_tender_id}:{segment.chunk_id}:{segment.segment_id}"
    )
    expectation = MaterializationExpectation(
        coalesced_tender_id=tender.coalesced_tender_id,
        evidence_ref_id=evidence_ref_id,
        field_path=f"anexo[{segment.locator_type}].{segment.segment_kind}",
        text_raw=segment.text_raw,
        text_normalized=normalize_product_text(segment.text_raw),
        evidence_tier=segment.evidence_tier,
        source_plane=ANEXO_SOURCE_PLANE,
        link_status="linked",
        unresolved_reason=None,
        materialization_status="verified_annex_segment",
        partition="linked",
        snapshot_id=None,
        observation_id=f"anexo_bundle:{tender_semantic_digest}",
        tender_observation_id=source_tender_id,
        line_observation_id=(
            f"anexo_segment:{segment.segment_id}"
            if segment.evidence_tier == "line_product_text"
            else None
        ),
        pr4_procurement_id=None,
        contributing_evidence_ref_ids=(evidence_ref_id,),
        attempt_kind="verified_annex_segment",
    )
    unit = expectation.to_unit()
    if unit.unit_id != recompute_unit_id(unit):
        raise AnexoIntegrationError(
            f"non-recomputable annex unit identity for {segment.segment_id}"
        )
    return unit


def _coverage_for(verified: VerifiedAnexoEvidence, tender_id: str) -> CoverageDebt:
    coverage = coverage_from_bundle(verified.bundle(tender_id))
    return CoverageDebt(
        tender_id=tender_id,
        has_anexo_bundle=coverage.has_source_bundle,
        extraction_complete=coverage.extraction_complete,
        attachments_discovered=coverage.attachments_discovered,
        attachments_downloaded=coverage.attachments_downloaded,
        incomplete_reason_codes=coverage.incomplete_reason_codes,
        debt_outcome_counts=coverage.debt_outcome_counts,
        unread_attachment_ids=coverage.unread_attachment_ids,
    )


def _updated_counts(
    baseline: ProductRelevancePlanResult,
    *,
    linked_units: tuple[ProductTextUnit, ...],
    unit_decisions: tuple[EvidenceUnitRelevanceDecision, ...],
    tender_decisions: tuple[TenderRelevanceDecision, ...],
    applied_unit_count: int,
    withheld_unit_count: int,
) -> dict[str, Any]:
    counts = dict(baseline.counts)
    class_counts = Counter(d.relevance_class for d in tender_decisions)
    abstain = sum(
        1
        for decision in tender_decisions
        if is_manual_review_prediction(
            relevance_class=decision.relevance_class,
            confidence_band=decision.confidence_band,
            resolution_status=decision.product_resolution_status,
            ambiguity_reason_codes=decision.ambiguity_reason_codes,
            aggregation_reason_codes=decision.aggregation_reason_codes,
        )
    )
    counts.update(
        {
            "relevance_decisions": len(tender_decisions),
            "linked_units": len(linked_units),
            "unit_decisions": len(unit_decisions),
            "by_relevance_class": dict(sorted(class_counts.items())),
            "abstention_or_manual_review": abstain,
            "abstention_or_ambiguous": abstain,
        }
    )
    adapter = dict(counts.get("adapter") or {})
    adapter["annex_integration"] = {
        "integration_version": ANEXO_INTEGRATION_VERSION,
        "applied_unit_count": applied_unit_count,
        "withheld_unit_count": withheld_unit_count,
    }
    counts["adapter"] = adapter
    return counts


def augment_product_relevance_plan(
    *,
    pr5c: CandidatePlanResult,
    baseline: ProductRelevancePlanResult,
    verified: VerifiedAnexoEvidence,
) -> AnexoAugmentationResult:
    """Bind verified local annex evidence and augment the canonical PR5D plan.

    Incomplete extraction retains the established positive-only asymmetry: if
    an incomplete bundle has no strong positive annex unit, none of its annex
    units is allowed to alter the production decision.  The evidence remains
    accounted for in bindings and coverage debt.
    """
    if baseline.pr5c_semantic_digest != pr5c.semantic_digest:
        raise AnexoIntegrationError("baseline PR5D is not bound to supplied PR5C")
    if not baseline.reconciliation.get("ok"):
        raise AnexoIntegrationError("baseline PR5D reconciliation is not ok")

    production_ids = {t.coalesced_tender_id for t in pr5c.coalesced_tenders}
    baseline_tender_ids = [d.coalesced_tender_id for d in baseline.tender_decisions]
    if len(baseline_tender_ids) != len(set(baseline_tender_ids)):
        raise AnexoIntegrationError("duplicate baseline tender relevance decisions")
    if set(baseline_tender_ids) != production_ids:
        raise AnexoIntegrationError("baseline PR5D tender population differs from PR5C")

    identity_index = _production_identity_index(pr5c)
    bundle_ids = set(verified.tender_ids)
    # Preserve the evidence bundle's exact source ID while binding through the
    # authoritative case-insensitive Mercado Público code.  The strict loader
    # rejects normalized collisions; retain this defensive check because tests
    # and API callers can construct ``VerifiedAnexoEvidence`` directly.
    bound_source_ids = tuple(
        sorted(
            (
                source_id
                for source_id in bundle_ids
                if source_id.casefold() in identity_index
            ),
            key=str.casefold,
        )
    )
    source_by_production_id: dict[str, str] = {}
    for source_id in bound_source_ids:
        production_id = identity_index[source_id.casefold()][1].coalesced_tender_id
        prior = source_by_production_id.get(production_id)
        if prior is not None:
            raise AnexoIntegrationError(
                "multiple annex bundle identities bind to one production tender: "
                f"{prior!r}, {source_id!r}"
            )
        source_by_production_id[production_id] = source_id
    unused_source_ids = tuple(
        sorted(bundle_ids - set(bound_source_ids), key=str.casefold)
    )

    baseline_units_by_id = {u.unit_id: u for u in baseline.product_text_units}
    if len(baseline_units_by_id) != len(baseline.product_text_units):
        raise AnexoIntegrationError("duplicate baseline product text unit identities")
    baseline_decision_by_tender = {
        d.coalesced_tender_id: d for d in baseline.tender_decisions
    }

    pending: list[
        tuple[
            str,
            CoalescedProcurementTender,
            CoverageDebt,
            list[
                tuple[
                    ProductTextUnit, EvidenceUnitRelevanceDecision, AnexoEvidenceSegment
                ]
            ],
        ]
    ] = []
    coverage_by_tender: dict[str, CoverageDebt] = {}
    for source_tender_id in bound_source_ids:
        _canonical_code, tender = identity_index[source_tender_id.casefold()]
        digest = verified.tender_semantic_digests[source_tender_id]
        bundle = verified.bundle(source_tender_id)
        if bundle.semantic_digest != digest:
            raise AnexoIntegrationError(
                f"verified tender digest mismatch for {source_tender_id}"
            )
        segments = _segments_for_bundle(bundle)
        if len({s.segment_id for s in segments}) != len(segments):
            raise AnexoIntegrationError(
                f"duplicate annex segment identities for {source_tender_id}"
            )
        coverage = _coverage_for(verified, source_tender_id)
        coverage_by_tender[source_tender_id] = coverage
        pairs: list[
            tuple[ProductTextUnit, EvidenceUnitRelevanceDecision, AnexoEvidenceSegment]
        ] = []
        for segment in segments:
            if segment.tender_id != source_tender_id:
                raise AnexoIntegrationError(
                    f"cross-tender segment binding for {segment.segment_id}"
                )
            unit = _annex_unit(
                tender=tender,
                source_tender_id=source_tender_id,
                segment=segment,
                tender_semantic_digest=digest,
            )
            decision = relevance_rules.classify_product_text_unit(unit)
            if decision.unit_id != unit.unit_id:
                raise AnexoIntegrationError(
                    f"classifier returned a decision for the wrong annex unit {unit.unit_id}"
                )
            pairs.append((unit, decision, segment))
        pending.append((source_tender_id, tender, coverage, pairs))

    applied_units: list[ProductTextUnit] = []
    applied_decisions: list[EvidenceUnitRelevanceDecision] = []
    bindings: list[AnnexUnitBinding] = []
    applied_pairs_by_tender: dict[
        str, list[tuple[EvidenceUnitRelevanceDecision, AnexoEvidenceSegment]]
    ] = defaultdict(list)
    affected_ids: set[str] = set()

    for source_tender_id, tender, coverage, pairs in pending:
        has_strong_positive = any(
            decision.relevance_class in STRONG_RELEVANCE_CLASSES
            and bool(decision.canonical_equipment_classes)
            for _unit, decision, _segment in pairs
        )
        withhold_incomplete_negative_proof = (
            not coverage.is_complete and not has_strong_positive
        )
        digest = verified.tender_semantic_digests[source_tender_id]
        for unit, decision, segment in pairs:
            applied = not withhold_incomplete_negative_proof
            reason = (
                "negative_proof_withheld_incomplete_coverage"
                if withhold_incomplete_negative_proof
                else None
            )
            bindings.append(
                AnnexUnitBinding(
                    tender_id=source_tender_id,
                    coalesced_tender_id=tender.coalesced_tender_id,
                    tender_semantic_digest=digest,
                    unit=unit,
                    decision=decision,
                    segment=segment,
                    applied=applied,
                    withholding_reason=reason,
                )
            )
            if not applied:
                continue
            applied_units.append(unit)
            applied_decisions.append(decision)
            applied_pairs_by_tender[source_tender_id].append((decision, segment))
            affected_ids.add(tender.coalesced_tender_id)

    all_units = tuple(
        sorted(
            (*baseline.product_text_units, *applied_units),
            key=lambda unit: unit.unit_id,
        )
    )
    if len({u.unit_id for u in all_units}) != len(all_units):
        raise AnexoIntegrationError("annex unit identity collides with production PR5D")
    all_unit_decisions = tuple(
        sorted(
            (*baseline.unit_decisions, *applied_decisions),
            key=lambda decision: decision.unit_decision_id,
        )
    )
    if len({d.unit_decision_id for d in all_unit_decisions}) != len(all_unit_decisions):
        raise AnexoIntegrationError("annex unit-decision identity collision")

    input_fp = relevance_input_fingerprint(
        pr5c_semantic_digest=pr5c.semantic_digest,
        linked_units=all_units,
        unresolved_units=baseline.unresolved_units,
    )
    decisions_by_tender = relevance_aggregate.group_unit_decisions_by_tender(
        all_unit_decisions
    )
    tender_by_id = {t.coalesced_tender_id: t for t in pr5c.coalesced_tenders}
    final_tender_decisions: list[TenderRelevanceDecision] = []
    for tender_id in sorted(production_ids):
        if tender_id not in affected_ids:
            final_tender_decisions.append(baseline_decision_by_tender[tender_id])
            continue
        final_tender_decisions.append(
            relevance_aggregate.aggregate_tender_decision(
                tender_by_id[tender_id],
                decisions_by_tender.get(tender_id, []),
                input_fingerprint=input_fp,
            )
        )
    tender_decisions = tuple(
        sorted(final_tender_decisions, key=lambda d: d.decision_id)
    )

    # Re-prove the "untouched means byte-for-byte untouched" contract.
    final_by_tender = {d.coalesced_tender_id: d for d in tender_decisions}
    for tender_id in production_ids - affected_ids:
        if final_by_tender[tender_id] != baseline_decision_by_tender[tender_id]:
            raise AnexoIntegrationError(
                f"unaffected PR5D decision changed for {tender_id}"
            )

    fps = all_fingerprints(
        pr5c_semantic_digest=pr5c.semantic_digest,
        linked_units=all_units,
        unresolved_units=baseline.unresolved_units,
        tender_decisions=tender_decisions,
        unit_decisions=all_unit_decisions,
    )

    claims: list[AnexoClaim] = []
    for source_tender_id in bound_source_ids:
        _code, tender = identity_index[source_tender_id.casefold()]
        baseline_decision = baseline_decision_by_tender[tender.coalesced_tender_id]
        augmented_decision = final_by_tender[tender.coalesced_tender_id]
        pairs = applied_pairs_by_tender.get(source_tender_id, [])
        claims.extend(
            build_claims(
                pairs,
                baseline_categories=frozenset(
                    baseline_decision.canonical_equipment_classes
                ),
                augmented_categories=frozenset(
                    augmented_decision.canonical_equipment_classes
                ),
                conflict=is_conflict(
                    baseline_decision=baseline_decision,
                    augmented_decision=augmented_decision,
                ),
                taxonomy_version=relevance_taxonomy_fingerprint(),
                rules_version=relevance_rules_fingerprint(),
            )
        )
    claims_t = tuple(sorted(claims, key=lambda claim: claim.claim_id))

    binding_segment_ids = [binding.segment.segment_id for binding in bindings]
    binding_unit_ids = [binding.unit.unit_id for binding in bindings]
    binding_decision_unit_ids = [binding.decision.unit_id for binding in bindings]
    binding_reconciliation_ok = (
        len(binding_segment_ids) == len(set(binding_segment_ids))
        and len(binding_unit_ids) == len(set(binding_unit_ids))
        and binding_unit_ids == binding_decision_unit_ids
        and all(
            binding.unit.unit_id == recompute_unit_id(binding.unit)
            for binding in bindings
        )
        and sum(binding.applied for binding in bindings) == len(applied_units)
        and sum(not binding.applied for binding in bindings)
        == len(bindings) - len(applied_units)
    )
    if not binding_reconciliation_ok:
        raise AnexoIntegrationError("annex segment/unit/decision binding failed")

    integration_reconciliation = {
        "ok": True,
        "integration_contract_version": ANEXO_INTEGRATION_VERSION,
        "integration_mode": "enabled_local_verified",
        "annex_evidence_digest": verified.semantic_digest,
        "annex_bundle_tender_count": len(bundle_ids),
        "annex_bound_tender_count": len(bound_source_ids),
        "annex_unused_tender_count": len(unused_source_ids),
        "annex_applied_unit_count": len(applied_units),
        "annex_withheld_unit_count": sum(not binding.applied for binding in bindings),
        "affected_tender_count": len(affected_ids),
        "annex_binding_reconciliation": {
            "ok": True,
            "equation": (
                "verified_annex_segments = applied_annex_units ⊎ "
                "explicitly_withheld_annex_units; each segment binds exactly one "
                "recomputable unit and its canonical unit decision"
            ),
            "segment_count": len(binding_segment_ids),
            "unit_count": len(binding_unit_ids),
            "applied_count": len(applied_units),
            "withheld_count": len(bindings) - len(applied_units),
        },
        "unaffected_tender_decisions_preserved": True,
        "incomplete_coverage_negative_proof_withheld": True,
        "baseline_reconciliation_ok": True,
    }
    semantic_digest = canonical_json_digest(
        {
            "integration_contract_version": ANEXO_INTEGRATION_VERSION,
            "annex_evidence_digest": verified.semantic_digest,
            "baseline_pr5d_semantic_digest": baseline.fingerprints.get(
                "semantic_digest"
            ),
            "augmented_pr5d_semantic_digest": fps["semantic_digest"],
            "bound_tender_ids": list(bound_source_ids),
            "unused_tender_ids": list(unused_source_ids),
            "bindings": [binding.provenance_dict() for binding in bindings],
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "tender_id": claim.tender_id,
                    "matched_category": claim.matched_category,
                    "unit_decision_id": claim.unit_decision_id,
                    "annex_effect": claim.annex_effect,
                }
                for claim in claims_t
            ],
        }
    )
    reconciliation = {
        "ok": True,
        "baseline_reconciliation": dict(baseline.reconciliation),
        "annex_integration": integration_reconciliation,
    }
    counts = _updated_counts(
        baseline,
        linked_units=all_units,
        unit_decisions=all_unit_decisions,
        tender_decisions=tender_decisions,
        applied_unit_count=len(applied_units),
        withheld_unit_count=sum(not binding.applied for binding in bindings),
    )
    case_meta = dict(baseline.case_construction_meta)
    case_meta["annex_integration"] = {
        "integration_contract_version": ANEXO_INTEGRATION_VERSION,
        "semantic_digest": semantic_digest,
        "evaluation_packets_unchanged_from_baseline": True,
    }

    augmented = replace(
        baseline,
        product_text_units=all_units,
        unit_decisions=all_unit_decisions,
        tender_decisions=tender_decisions,
        reconciliation=reconciliation,
        fingerprints=fps,
        counts=counts,
        case_construction_meta=case_meta,
    )
    return AnexoAugmentationResult(
        plan=augmented,
        baseline_plan=baseline,
        evidence_digest=verified.semantic_digest,
        bound_tender_ids=bound_source_ids,
        unused_tender_ids=unused_source_ids,
        affected_coalesced_tender_ids=tuple(sorted(affected_ids)),
        bindings=tuple(sorted(bindings, key=lambda binding: binding.unit.unit_id)),
        claims=claims_t,
        coverage_by_tender=dict(sorted(coverage_by_tender.items())),
        semantic_digest=semantic_digest,
    )


__all__ = [
    "AnexoAugmentationResult",
    "AnexoIntegrationError",
    "AnnexUnitBinding",
    "augment_product_relevance_plan",
]
