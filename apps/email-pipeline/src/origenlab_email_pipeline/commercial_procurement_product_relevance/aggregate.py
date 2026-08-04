"""Aggregate unit relevance decisions into one tender decision."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

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
from origenlab_email_pipeline.commercial_procurement_product_relevance.semantic_payload import (
    digest_tender_semantic_payload,
    matched_rule_ids_from_spans,
    tender_semantic_payload,
)

_STRONG = STRONG_RELEVANCE_CLASSES
_NEGATIVE = NEGATIVE_RELEVANCE_CLASSES - {"unrelated"}  # unrelated is weak negative
_ABSTAIN = frozenset({"ambiguous", "laboratory_context_only"})


def aggregation_policy_spec() -> dict[str, Any]:
    """Declarative aggregation semantics shared by execution + fingerprints."""
    return {
        "version": "aggregation_policy_v2",
        "strong_classes": sorted(_STRONG),
        "negative_classes": sorted(_NEGATIVE),
        "abstain_classes": sorted(_ABSTAIN),
        "strong_class_precedence": [
            "exact_catalog_product",
            "strong_equipment_class",
            "compatible_equipment_class",
        ],
        "negative_class_precedence": [
            "consumable_or_reagent",
            "service_or_maintenance_only",
            "rental_or_comodato",
            "non_laboratory_false_positive",
        ],
        "evidence_tier_rank": {
            "line_product_text": 3,
            "tender_description": 2,
            "description_product_text": 2,
            "title_only": 1,
            "no_usable_product_text": 0,
        },
        "single_unit_behavior": {
            "id": "single_unit_decision",
            "outcome": "pass_through_unit_decision",
        },
        "mixed_conflict_behavior": {
            "negative_plus_abstention": {
                "id": "mixed_positive_and_negative_requires_review",
                "relevance_class": "ambiguous",
                "product_resolution_status": "mixed_requires_review",
                "confidence_band": "abstain",
                "ambiguity_reason_codes": ["conflicting_line_evidence"],
            },
            "conflicting_strong_canonical_classes": {
                "id": "mixed_positive_and_negative_requires_review",
                "relevance_class": "ambiguous",
                "product_resolution_status": "mixed_requires_review",
                "confidence_band": "abstain",
                "ambiguity_reason_codes": [
                    "conflicting_line_evidence",
                    "conflicting_canonical_equipment_classes",
                ],
            },
            "strong_plus_hard_negative": {
                "id": "strong_positive_survives_negative_lines",
                "note": "Independent durable-equipment line is not erased by a negative line",
            },
        },
        "manual_review_outcomes": {
            "confidence_band": "abstain",
            "product_resolution_status": "mixed_requires_review",
            "relevance_class": "ambiguous",
        },
        "policies": [
            {
                "id": "no_usable_units",
                "outcome": "ambiguous",
                "confidence_band": "abstain",
            },
            {
                "id": "strong_positive_survives_negative_lines",
                "when": "strong_and_negative_present",
                "outcome": "prefer_strongest_positive",
                "note": "Independent durable-equipment line is not erased by a negative line",
            },
            {
                "id": "mixed_positive_and_negative_requires_review",
                "when": (
                    "conflicting_strong_canonical_classes_or_negative_plus_abstention"
                ),
                "outcome": "ambiguous+abstain+mixed_requires_review",
            },
            {
                "id": "all_units_negative",
                "outcome": "most_specific_negative",
            },
            {
                "id": "all_units_ambiguous_or_empty",
                "outcome": "ambiguous+abstain",
            },
            {
                "id": "empty_never_silent_unrelated",
                "when": "all_units_insufficient_product_text",
                "force_outcome": "ambiguous",
            },
        ],
    }


def _tier_rank(tier: str) -> int:
    return int(aggregation_policy_spec()["evidence_tier_rank"].get(tier, 0))


def _strong_sort_key(u: EvidenceUnitRelevanceDecision) -> tuple[int, str]:
    order = aggregation_policy_spec()["strong_class_precedence"]
    try:
        idx = order.index(u.relevance_class)
    except ValueError:
        idx = 99
    return (idx, u.unit_decision_id)


def _negative_sort_key(u: EvidenceUnitRelevanceDecision) -> tuple[int, str]:
    order = aggregation_policy_spec()["negative_class_precedence"]
    try:
        idx = order.index(u.relevance_class)
    except ValueError:
        idx = 99
    return (idx, u.unit_decision_id)


def _canonical_class_sets_conflict(
    strong: list[EvidenceUnitRelevanceDecision],
) -> bool:
    sets = {frozenset(u.canonical_equipment_classes) for u in strong}
    return len(sets) > 1


def _apply_mixed_review(
    *,
    amb_codes: list[str],
    agg_codes: list[str],
    extra_ambiguity: list[str],
) -> tuple[str, str, str]:
    spec = aggregation_policy_spec()["manual_review_outcomes"]
    policy = aggregation_policy_spec()["mixed_conflict_behavior"][
        "conflicting_strong_canonical_classes"
    ]
    agg_codes.append(str(policy["id"]))
    for code in extra_ambiguity:
        amb_codes.append(code)
    return (
        str(spec["relevance_class"]),
        str(spec["product_resolution_status"]),
        str(spec["confidence_band"]),
    )


def aggregate_tender_decision(
    tender: CoalescedProcurementTender,
    unit_decisions: Iterable[EvidenceUnitRelevanceDecision],
    *,
    input_fingerprint: str,
) -> TenderRelevanceDecision:
    units = sorted(unit_decisions, key=lambda d: d.unit_decision_id)
    if not units:
        payload = tender_semantic_payload(
            coalesced_tender_id=tender.coalesced_tender_id,
            relevance_class="ambiguous",
            classes=[],
            resolution="insufficient_product_text",
            evidence_tier="no_usable_product_text",
            confidence_band="abstain",
            positive=[],
            negative=[],
            ambiguity=["insufficient_product_text"],
            aggregation=["no_usable_units"],
            matched_rule_ids=[],
            unit_decision_ids=[],
        )
        decision_id = f"trd_{canonical_json_digest(payload)[:32]}"
        sem = digest_tender_semantic_payload(payload)
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

    policy = aggregation_policy_spec()

    if len(units) == 1:
        agg_codes.append(str(policy["single_unit_behavior"]["id"]))
        chosen = units[0]
        relevance = chosen.relevance_class
        resolution = chosen.product_resolution_status
        tier = chosen.evidence_tier
        band = chosen.confidence_band
    elif strong and negatives:
        # Strong durable equipment survives independent negative lines.
        agg_codes.append("strong_positive_survives_negative_lines")
        chosen = sorted(strong, key=_strong_sort_key)[0]
        relevance = chosen.relevance_class
        resolution = chosen.product_resolution_status
        tier = chosen.evidence_tier
        band = chosen.confidence_band
        if _canonical_class_sets_conflict(strong) or any(
            u.relevance_class != chosen.relevance_class for u in strong
        ):
            relevance, resolution, band = _apply_mixed_review(
                amb_codes=amb_codes,
                agg_codes=agg_codes,
                extra_ambiguity=list(
                    policy["mixed_conflict_behavior"][
                        "conflicting_strong_canonical_classes"
                    ]["ambiguity_reason_codes"]
                ),
            )
    elif strong:
        agg_codes.append(str(policy["single_unit_behavior"]["id"]))
        chosen = sorted(strong, key=_strong_sort_key)[0]
        relevance = chosen.relevance_class
        resolution = chosen.product_resolution_status
        tier = max((u.evidence_tier for u in strong), key=_tier_rank)
        band = chosen.confidence_band
        if _canonical_class_sets_conflict(strong) or (
            len(strong) > 1 and len({u.relevance_class for u in strong}) > 1
        ):
            relevance, resolution, band = _apply_mixed_review(
                amb_codes=amb_codes,
                agg_codes=agg_codes,
                extra_ambiguity=list(
                    policy["mixed_conflict_behavior"][
                        "conflicting_strong_canonical_classes"
                    ]["ambiguity_reason_codes"]
                ),
            )
    elif negatives and abstain:
        # Negative + abstention must not collapse into all_units_ambiguous_or_empty.
        neg_abs = policy["mixed_conflict_behavior"]["negative_plus_abstention"]
        agg_codes.append(str(neg_abs["id"]))
        relevance = str(neg_abs["relevance_class"])
        resolution = str(neg_abs["product_resolution_status"])
        band = str(neg_abs["confidence_band"])
        for code in neg_abs["ambiguity_reason_codes"]:
            amb_codes.append(str(code))
        tier = (
            "line_product_text"
            if any(u.evidence_tier == "line_product_text" for u in units)
            else units[0].evidence_tier
        )
    elif negatives and not abstain:
        agg_codes.append("all_units_negative")
        chosen = sorted(negatives, key=_negative_sort_key)[0]
        relevance = chosen.relevance_class
        resolution = "negative_class_only"
        tier = chosen.evidence_tier
        band = chosen.confidence_band
    elif abstain:
        agg_codes.append("all_units_ambiguous_or_empty")
        relevance = "ambiguous"
        resolution = (
            "insufficient_product_text"
            if any(
                u.product_resolution_status == "insufficient_product_text"
                for u in units
            )
            else "context_required_unresolved"
        )
        if any(
            u.product_resolution_status == "context_required_unresolved" for u in units
        ):
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
        neg_abs = policy["mixed_conflict_behavior"]["negative_plus_abstention"]
        agg_codes.append(str(neg_abs["id"]))
        relevance = str(neg_abs["relevance_class"])
        resolution = str(neg_abs["product_resolution_status"])
        tier = (
            "line_product_text"
            if any(u.evidence_tier == "line_product_text" for u in units)
            else units[0].evidence_tier
        )
        band = str(neg_abs["confidence_band"])
        amb_codes.append("conflicting_line_evidence")

    # Empty usable text must never become silent unrelated at tender level.
    if relevance == "unrelated" and all(
        u.evidence_tier == "no_usable_product_text"
        or u.product_resolution_status == "insufficient_product_text"
        for u in units
    ):
        relevance = "ambiguous"
        resolution = "insufficient_product_text"
        band = "abstain"
        amb_codes.append("insufficient_product_text")
        agg_codes = ["no_usable_units"]

    payload = tender_semantic_payload(
        coalesced_tender_id=tender.coalesced_tender_id,
        relevance_class=relevance,
        classes=classes,
        resolution=resolution,
        evidence_tier=tier,
        confidence_band=band,
        positive=pos_codes,
        negative=neg_codes,
        ambiguity=amb_codes,
        aggregation=agg_codes,
        matched_rule_ids=matched_rule_ids_from_spans(spans),
        unit_decision_ids=[u.unit_decision_id for u in units],
    )
    decision_id = f"trd_{canonical_json_digest(payload)[:32]}"
    sem = digest_tender_semantic_payload(payload)
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


def group_unit_decisions_by_tender(
    unit_decisions: Iterable[EvidenceUnitRelevanceDecision],
) -> dict[str, list[EvidenceUnitRelevanceDecision]]:
    grouped: dict[str, list[EvidenceUnitRelevanceDecision]] = defaultdict(list)
    for d in unit_decisions:
        grouped[d.coalesced_tender_id].append(d)
    return dict(grouped)
