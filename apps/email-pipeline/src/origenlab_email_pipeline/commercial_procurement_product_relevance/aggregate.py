"""Aggregate unit relevance decisions into one tender decision."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_live_relevance.constants import (
    NEGATIVE_RELEVANCE_CLASSES,
    STRONG_RELEVANCE_CLASSES,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_RELEVANCE_RULES_VERSION,
    PRODUCT_RELEVANCE_TAXONOMY_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    TenderRelevanceDecision,
)

_STRONG = STRONG_RELEVANCE_CLASSES
_NEGATIVE = NEGATIVE_RELEVANCE_CLASSES - {"unrelated"}  # unrelated is weak negative
_ABSTAIN = frozenset({"ambiguous", "laboratory_context_only"})


def aggregate_tender_decision(
    tender: CoalescedProcurementTender,
    unit_decisions: Iterable[EvidenceUnitRelevanceDecision],
    *,
    input_fingerprint: str,
) -> TenderRelevanceDecision:
    units = sorted(unit_decisions, key=lambda d: d.unit_decision_id)
    if not units:
        payload = {
            "coalesced_tender_id": tender.coalesced_tender_id,
            "relevance_class": "ambiguous",
            "aggregation": ["no_usable_units"],
        }
        decision_id = f"trd_{canonical_json_digest(payload)[:32]}"
        sem = canonical_json_digest(payload)
        return TenderRelevanceDecision(
            decision_id=decision_id,
            coalesced_tender_id=tender.coalesced_tender_id,
            relevance_class="ambiguous",
            canonical_equipment_classes=(),
            product_resolution_status="insufficient_product_text",
            evidence_tier="no_usable_product_text",
            confidence_band="abstain",
            positive_reason_codes=(),
            negative_reason_codes=(),
            ambiguity_reason_codes=("insufficient_product_text",),
            aggregation_reason_codes=("no_usable_units",),
            matched_spans=(),
            contributing_evidence_ref_ids=tuple(tender.evidence_ref_ids),
            unit_decision_ids=(),
            taxonomy_version=PRODUCT_RELEVANCE_TAXONOMY_VERSION,
            rules_version=PRODUCT_RELEVANCE_RULES_VERSION,
            input_fingerprint=input_fingerprint,
            semantic_fingerprint=sem,
            lifecycle_class_echo=tender.lifecycle_class,
            not_persisted=True,
        )

    strong = [u for u in units if u.relevance_class in _STRONG]
    negatives = [u for u in units if u.relevance_class in _NEGATIVE]
    abstain = [u for u in units if u.relevance_class in _ABSTAIN]
    unrelated = [u for u in units if u.relevance_class == "unrelated"]

    pos_codes: list[str] = []
    neg_codes: list[str] = []
    amb_codes: list[str] = []
    agg_codes: list[str] = []
    classes: list[str] = []
    spans = []
    contrib: list[str] = []

    for u in units:
        pos_codes.extend(u.positive_reason_codes)
        neg_codes.extend(u.negative_reason_codes)
        amb_codes.extend(u.ambiguity_reason_codes)
        classes.extend(u.canonical_equipment_classes)
        spans.extend(u.matched_spans)
        contrib.extend(u.contributing_evidence_ref_ids)

    if len(units) == 1:
        agg_codes.append("single_unit_decision")
        chosen = units[0]
        relevance = chosen.relevance_class
        resolution = chosen.product_resolution_status
        tier = chosen.evidence_tier
        band = chosen.confidence_band
    elif strong and negatives:
        # Strong durable equipment survives independent negative lines.
        agg_codes.append("strong_positive_survives_negative_lines")
        # Also flag mixed review when both sides are substantial.
        if len(strong) >= 1 and len(negatives) >= 1:
            # Prefer positive class from strongest unit; still note mixed.
            chosen = sorted(
                strong,
                key=lambda u: (
                    0 if u.relevance_class == "exact_catalog_product" else 1,
                    u.unit_decision_id,
                ),
            )[0]
            relevance = chosen.relevance_class
            resolution = chosen.product_resolution_status
            tier = chosen.evidence_tier
            band = chosen.confidence_band
            if any(u.relevance_class != chosen.relevance_class for u in strong):
                agg_codes.append("mixed_positive_and_negative_requires_review")
                amb_codes.append("conflicting_line_evidence")
                band = "abstain"
                resolution = "mixed_requires_review"
    elif strong:
        agg_codes.append("single_unit_decision" if len(strong) == 1 else "single_unit_decision")
        chosen = sorted(
            strong,
            key=lambda u: (
                0 if u.relevance_class == "exact_catalog_product" else 1,
                u.unit_decision_id,
            ),
        )[0]
        relevance = chosen.relevance_class
        resolution = chosen.product_resolution_status
        tier = max((u.evidence_tier for u in strong), key=_tier_rank)
        band = chosen.confidence_band
        if len(strong) > 1 and len({u.relevance_class for u in strong}) > 1:
            amb_codes.append("conflicting_line_evidence")
    elif negatives and not abstain:
        agg_codes.append("all_units_negative")
        # Prefer most specific negative
        order = [
            "consumable_or_reagent",
            "service_or_maintenance_only",
            "rental_or_comodato",
            "non_laboratory_false_positive",
        ]
        chosen = sorted(
            negatives,
            key=lambda u: (
                order.index(u.relevance_class)
                if u.relevance_class in order
                else 99,
                u.unit_decision_id,
            ),
        )[0]
        relevance = chosen.relevance_class
        resolution = "negative_class_only"
        tier = chosen.evidence_tier
        band = chosen.confidence_band
    elif abstain or (not strong and not negatives and unrelated and abstain):
        agg_codes.append("all_units_ambiguous_or_empty")
        relevance = "ambiguous"
        resolution = "insufficient_product_text" if any(
            u.product_resolution_status == "insufficient_product_text" for u in units
        ) else "context_required_unresolved"
        if any(u.product_resolution_status == "context_required_unresolved" for u in units):
            resolution = "context_required_unresolved"
        tier = min((u.evidence_tier for u in units), key=_tier_rank)
        band = "abstain"
        if not amb_codes:
            amb_codes.append("insufficient_product_text")
    elif unrelated and not strong and not negatives and not abstain:
        agg_codes.append("all_units_negative")
        relevance = "unrelated"
        resolution = "negative_class_only"
        tier = units[0].evidence_tier
        band = "low"
    else:
        # Mixed abstain + negative without strong positive → review
        agg_codes.append("mixed_positive_and_negative_requires_review")
        relevance = "ambiguous"
        resolution = "mixed_requires_review"
        tier = "line_product_text" if any(
            u.evidence_tier == "line_product_text" for u in units
        ) else units[0].evidence_tier
        band = "abstain"
        amb_codes.append("conflicting_line_evidence")

    # Empty usable text must never become silent unrelated at tender level.
    if (
        relevance == "unrelated"
        and all(
            u.evidence_tier == "no_usable_product_text"
            or u.product_resolution_status == "insufficient_product_text"
            for u in units
        )
    ):
        relevance = "ambiguous"
        resolution = "insufficient_product_text"
        band = "abstain"
        amb_codes.append("insufficient_product_text")
        agg_codes = ["no_usable_units"]

    payload = {
        "coalesced_tender_id": tender.coalesced_tender_id,
        "relevance_class": relevance,
        "classes": sorted(set(classes)),
        "resolution": resolution,
        "positive": sorted(set(pos_codes)),
        "negative": sorted(set(neg_codes)),
        "ambiguity": sorted(set(amb_codes)),
        "aggregation": sorted(set(agg_codes)),
        "unit_decision_ids": [u.unit_decision_id for u in units],
    }
    decision_id = f"trd_{canonical_json_digest(payload)[:32]}"
    sem = canonical_json_digest(payload)
    return TenderRelevanceDecision(
        decision_id=decision_id,
        coalesced_tender_id=tender.coalesced_tender_id,
        relevance_class=relevance,
        canonical_equipment_classes=tuple(sorted(set(classes))),
        product_resolution_status=resolution,
        evidence_tier=tier,
        confidence_band=band,
        positive_reason_codes=tuple(sorted(set(pos_codes))),
        negative_reason_codes=tuple(sorted(set(neg_codes))),
        ambiguity_reason_codes=tuple(sorted(set(amb_codes))),
        aggregation_reason_codes=tuple(sorted(set(agg_codes))),
        matched_spans=tuple(spans),
        contributing_evidence_ref_ids=tuple(sorted(set(contrib))),
        unit_decision_ids=tuple(u.unit_decision_id for u in units),
        taxonomy_version=PRODUCT_RELEVANCE_TAXONOMY_VERSION,
        rules_version=PRODUCT_RELEVANCE_RULES_VERSION,
        input_fingerprint=input_fingerprint,
        semantic_fingerprint=sem,
        lifecycle_class_echo=tender.lifecycle_class,
        not_persisted=True,
    )


def _tier_rank(tier: str) -> int:
    order = {
        "line_product_text": 3,
        "tender_description": 2,
        "title_only": 1,
        "no_usable_product_text": 0,
    }
    return order.get(tier, 0)


def group_unit_decisions_by_tender(
    unit_decisions: Iterable[EvidenceUnitRelevanceDecision],
) -> dict[str, list[EvidenceUnitRelevanceDecision]]:
    grouped: dict[str, list[EvidenceUnitRelevanceDecision]] = defaultdict(list)
    for d in unit_decisions:
        grouped[d.coalesced_tender_id].append(d)
    return dict(grouped)
