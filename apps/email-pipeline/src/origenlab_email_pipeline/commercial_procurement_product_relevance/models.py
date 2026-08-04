"""Immutable PR5D product-relevance models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MatchedEvidenceSpan:
    field_path: str
    rule_id: str
    matched_text: str
    start: int | None = None
    end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductTextUnit:
    """One normalized product-text evidence unit linked to a coalesced tender."""

    unit_id: str
    coalesced_tender_id: str
    evidence_ref_id: str | None
    link_status: str
    unresolved_reason: str | None
    field_path: str
    text_raw: str
    text_normalized: str
    evidence_tier: str
    source_plane: str
    snapshot_id: str | None
    observation_id: str | None
    tender_observation_id: str | None
    line_observation_id: str | None
    pr4_procurement_id: str | None
    contributing_evidence_ref_ids: tuple[str, ...]
    # Originating extraction-attempt identity (occurrence keyed in materialization ledger).
    attempt_id: str | None = None
    attempt_occurrence: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contributing_evidence_ref_ids"] = list(self.contributing_evidence_ref_ids)
        return d


@dataclass(frozen=True)
class MaterializationRecord:
    """One attempt occurrence → one linked or typed-unresolved unit."""

    attempt_occurrence: int
    attempt_id: str
    unit_id: str
    partition: str  # "linked" | "unresolved"
    materialization_status: str
    unresolved_reason: str | None = None
    field_path: str | None = None
    attempt_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class EvidenceUnitRelevanceDecision:
    unit_decision_id: str
    unit_id: str
    coalesced_tender_id: str
    relevance_class: str
    canonical_equipment_classes: tuple[str, ...]
    product_resolution_status: str
    evidence_tier: str
    confidence_band: str
    positive_reason_codes: tuple[str, ...]
    negative_reason_codes: tuple[str, ...]
    ambiguity_reason_codes: tuple[str, ...]
    matched_spans: tuple[MatchedEvidenceSpan, ...]
    contributing_evidence_ref_ids: tuple[str, ...]
    taxonomy_version: str
    rules_version: str
    unit_input_fingerprint: str
    unit_semantic_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["canonical_equipment_classes"] = list(self.canonical_equipment_classes)
        d["positive_reason_codes"] = list(self.positive_reason_codes)
        d["negative_reason_codes"] = list(self.negative_reason_codes)
        d["ambiguity_reason_codes"] = list(self.ambiguity_reason_codes)
        d["matched_spans"] = [s.to_dict() for s in self.matched_spans]
        d["contributing_evidence_ref_ids"] = list(self.contributing_evidence_ref_ids)
        return d


@dataclass(frozen=True)
class TenderRelevanceDecision:
    decision_id: str
    coalesced_tender_id: str
    relevance_class: str
    canonical_equipment_classes: tuple[str, ...]
    product_resolution_status: str
    evidence_tier: str
    confidence_band: str
    positive_reason_codes: tuple[str, ...]
    negative_reason_codes: tuple[str, ...]
    ambiguity_reason_codes: tuple[str, ...]
    aggregation_reason_codes: tuple[str, ...]
    matched_spans: tuple[MatchedEvidenceSpan, ...]
    contributing_evidence_ref_ids: tuple[str, ...]
    unit_decision_ids: tuple[str, ...]
    taxonomy_version: str
    rules_version: str
    input_fingerprint: str
    semantic_fingerprint: str
    # Explicitly not lead/outreach state — lifecycle is PR5C's dimension.
    lifecycle_class_echo: str | None = None
    not_persisted: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["canonical_equipment_classes"] = list(self.canonical_equipment_classes)
        d["positive_reason_codes"] = list(self.positive_reason_codes)
        d["negative_reason_codes"] = list(self.negative_reason_codes)
        d["ambiguity_reason_codes"] = list(self.ambiguity_reason_codes)
        d["aggregation_reason_codes"] = list(self.aggregation_reason_codes)
        d["matched_spans"] = [s.to_dict() for s in self.matched_spans]
        d["contributing_evidence_ref_ids"] = list(self.contributing_evidence_ref_ids)
        d["unit_decision_ids"] = list(self.unit_decision_ids)
        return d


@dataclass(frozen=True)
class ProductRelevancePlanResult:
    product_text_units: tuple[ProductTextUnit, ...]
    unresolved_units: tuple[ProductTextUnit, ...]
    unit_decisions: tuple[EvidenceUnitRelevanceDecision, ...]
    tender_decisions: tuple[TenderRelevanceDecision, ...]
    field_sufficiency: dict[str, Any]
    reconciliation: dict[str, Any]
    taxonomy_document: dict[str, Any]
    fingerprints: dict[str, str]
    counts: dict[str, Any]
    run_context: str
    planner_version: str
    as_of_utc: str
    pr5c_semantic_digest: str
    # Record-free aggregates only — never embed blind/sealed/ops records here.
    evaluation_meta: dict[str, Any] = field(default_factory=dict)
    case_construction_meta: dict[str, Any] = field(default_factory=dict)
    # Distinct record-bearing payloads — serialized only to dedicated artifact files.
    human_review_packet: tuple[dict[str, Any], ...] = ()
    sealed_scoring_manifest: tuple[dict[str, Any], ...] = ()
    operational_evaluation_records: tuple[dict[str, Any], ...] = ()
    materialization_ledger: tuple[dict[str, Any], ...] = ()

    def to_summary_dict(self) -> dict[str, Any]:
        """Record-free summary — no blind/sealed/ops rows or product wording."""
        return {
            "planner_version": self.planner_version,
            "run_context": self.run_context,
            "as_of_utc": self.as_of_utc,
            "pr5c_semantic_digest": self.pr5c_semantic_digest,
            "fingerprints": dict(self.fingerprints),
            "counts": dict(self.counts),
            "reconciliation": dict(self.reconciliation),
            "evaluation": {
                "queue_record_count": self.evaluation_meta.get("queue_record_count"),
                "metrics": self.evaluation_meta.get("metrics") or {},
                "sampling": self.evaluation_meta.get("sampling") or {},
                "taxonomy_validation": self.evaluation_meta.get("taxonomy_validation")
                or {},
            },
            "ok": bool(self.reconciliation.get("ok")),
            "not_persisted": True,
            "artifact_layout": {
                "blind_human_file": "human_review_packet.json",
                "sealed_scoring_file": "sealed_scoring_manifest.json",
                "operational_records_file": "operational_evaluation_records.json",
                "summary_is_record_free": True,
                "no_recombined_blind_and_sealed_file": True,
            },
        }
