"""Canonical semantic payloads shared by classification, aggregation, and fingerprints.

Material fields only — never raw matched_text or other sensitive wording.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    EvidenceUnitRelevanceDecision,
    MatchedEvidenceSpan,
    TenderRelevanceDecision,
)


def unit_semantic_payload(
    *,
    relevance_class: str,
    classes: Iterable[str],
    resolution: str,
    evidence_tier: str,
    confidence_band: str,
    positive: Iterable[str],
    negative: Iterable[str],
    ambiguity: Iterable[str],
    matched_rule_ids: Iterable[str],
) -> dict[str, Any]:
    return {
        "relevance_class": relevance_class,
        "classes": sorted(set(classes)),
        "resolution": resolution,
        "evidence_tier": evidence_tier,
        "confidence_band": confidence_band,
        "positive": sorted(set(positive)),
        "negative": sorted(set(negative)),
        "ambiguity": sorted(set(ambiguity)),
        "matched_rule_ids": sorted(set(matched_rule_ids)),
        "manual_review_semantics": {
            "confidence_band_abstain": confidence_band == "abstain",
            "resolution": resolution,
        },
    }


def unit_semantic_payload_from_decision(
    decision: EvidenceUnitRelevanceDecision,
) -> dict[str, Any]:
    return unit_semantic_payload(
        relevance_class=decision.relevance_class,
        classes=decision.canonical_equipment_classes,
        resolution=decision.product_resolution_status,
        evidence_tier=decision.evidence_tier,
        confidence_band=decision.confidence_band,
        positive=decision.positive_reason_codes,
        negative=decision.negative_reason_codes,
        ambiguity=decision.ambiguity_reason_codes,
        matched_rule_ids=[s.rule_id for s in decision.matched_spans],
    )


def digest_unit_semantic_payload(payload: Mapping[str, Any]) -> str:
    return canonical_json_digest(dict(payload))


def tender_semantic_payload(
    *,
    coalesced_tender_id: str,
    relevance_class: str,
    classes: Iterable[str],
    resolution: str,
    evidence_tier: str,
    confidence_band: str,
    positive: Iterable[str],
    negative: Iterable[str],
    ambiguity: Iterable[str],
    aggregation: Iterable[str],
    matched_rule_ids: Iterable[str],
    unit_decision_ids: Iterable[str],
) -> dict[str, Any]:
    return {
        "coalesced_tender_id": coalesced_tender_id,
        "relevance_class": relevance_class,
        "classes": sorted(set(classes)),
        "resolution": resolution,
        "evidence_tier": evidence_tier,
        "confidence_band": confidence_band,
        "positive": sorted(set(positive)),
        "negative": sorted(set(negative)),
        "ambiguity": sorted(set(ambiguity)),
        "aggregation": sorted(set(aggregation)),
        "matched_rule_ids": sorted(set(matched_rule_ids)),
        "unit_decision_ids": list(unit_decision_ids),
        "manual_review_semantics": {
            "confidence_band_abstain": confidence_band == "abstain",
            "resolution": resolution,
        },
    }


def tender_semantic_payload_from_decision(
    decision: TenderRelevanceDecision,
) -> dict[str, Any]:
    return tender_semantic_payload(
        coalesced_tender_id=decision.coalesced_tender_id,
        relevance_class=decision.relevance_class,
        classes=decision.canonical_equipment_classes,
        resolution=decision.product_resolution_status,
        evidence_tier=decision.evidence_tier,
        confidence_band=decision.confidence_band,
        positive=decision.positive_reason_codes,
        negative=decision.negative_reason_codes,
        ambiguity=decision.ambiguity_reason_codes,
        aggregation=decision.aggregation_reason_codes,
        matched_rule_ids=[s.rule_id for s in decision.matched_spans],
        unit_decision_ids=decision.unit_decision_ids,
    )


def digest_tender_semantic_payload(payload: Mapping[str, Any]) -> str:
    return canonical_json_digest(dict(payload))


def matched_rule_ids_from_spans(spans: Iterable[MatchedEvidenceSpan]) -> list[str]:
    return sorted({s.rule_id for s in spans})
