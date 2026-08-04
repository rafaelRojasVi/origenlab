"""Immutable intermediate models for PR5C coalescence / lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SelectedStatus:
    status_code: str | None
    status_name: str | None
    status_value: str | None
    source_status_system: str | None
    evidence_ref_id: str | None
    normalized_lifecycle_meaning: str | None
    source_rank_class: str | None
    internally_inconsistent: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProcurementEvidenceRef:
    evidence_ref_id: str
    evidence_plane: str
    source_kind: str
    endpoint_kind: str
    source_record_id: str
    canonical_tender_key: str | None
    tender_key_kind: str | None
    identity_namespace: str | None
    cross_source_join_eligible: bool
    snapshot_id: str | None
    acquisition_instance_id: str | None
    page_id: str | None
    observation_id: str | None
    acquired_at_utc: str | None
    source_status_code: str | None
    source_status_name: str | None
    source_status_value: str | None
    source_status_system: str | None
    normalized_status_meaning: str | None
    publication_timestamp_raw: str | None
    close_timestamp_raw: str | None
    publication_timestamp_utc: str | None
    close_timestamp_utc: str | None
    publication_santiago_date: str | None
    close_santiago_date: str | None
    publication_precision: str | None
    close_precision: str | None
    timestamp_parse_reasons: tuple[str, ...]
    buyer_display_raw: str | None
    buyer_source_id: str | None
    title_raw: str | None
    source_payload_digest: str | None
    source_fingerprint: str | None
    normalized_semantic_digest: str | None
    field_provenance: dict[str, str]
    reason_codes: tuple[str, ...]
    source_rank_class: str
    has_status: bool
    has_close: bool
    has_publication: bool
    has_buyer_display: bool
    has_buyer_source_id: bool
    has_title: bool
    pr4_procurement_id: str | None = None
    pr4_account_resolution_id: str | None = None
    pr4_evidence_ids: tuple[str, ...] = ()
    pr4_conflict_ids: tuple[str, ...] = ()
    constituent_source_ids: tuple[str, ...] = ()
    page_completeness: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason_codes"] = list(self.reason_codes)
        d["timestamp_parse_reasons"] = list(self.timestamp_parse_reasons)
        d["pr4_evidence_ids"] = list(self.pr4_evidence_ids)
        d["pr4_conflict_ids"] = list(self.pr4_conflict_ids)
        d["constituent_source_ids"] = list(self.constituent_source_ids)
        return d


@dataclass(frozen=True)
class CoalescenceConflict:
    conflict_id: str
    conflict_kind: str
    canonical_tender_key: str | None
    identity_namespace: str | None
    coalesced_tender_id: str | None
    evidence_ref_ids: tuple[str, ...]
    field_name: str | None
    reason_codes: tuple[str, ...]
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_ref_ids"] = list(self.evidence_ref_ids)
        d["reason_codes"] = list(self.reason_codes)
        return d


@dataclass(frozen=True)
class UnresolvedProcurementEvidence:
    unresolved_id: str
    evidence_plane: str
    source_kind: str
    endpoint_kind: str
    source_record_id: str
    snapshot_id: str | None
    acquisition_instance_id: str | None
    observation_id: str | None
    unresolved_reason: str
    canonical_candidate_kind: str | None
    canonical_tender_key_candidate: str | None
    source_native_tender_key: str | None
    tender_key_kind: str | None
    identity_namespace: str | None
    pr4_procurement_id: str | None
    reason_codes: tuple[str, ...]
    source_payload_digest: str | None
    acquired_at_utc: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason_codes"] = list(self.reason_codes)
        return d


@dataclass(frozen=True)
class CoalescedProcurementTender:
    coalesced_tender_id: str
    canonical_tender_key: str
    identity_namespace: str
    tender_key_kind: str
    candidate_source_kind: str
    pr4_procurement_id: str | None
    pr4_procurement_ids: tuple[str, ...]
    acquisition_snapshot_ids: tuple[str, ...]
    acquisition_instance_ids: tuple[str, ...]
    acquisition_observation_ids: tuple[str, ...]
    coalescence_status: str
    source_precedence_reason: str
    currentness_class: str
    lifecycle_class: str
    closing_soon_bucket: str
    publication_timestamp_selected: str | None
    close_timestamp_selected: str | None
    status_code_selected: str | None
    status_name_selected: str | None
    status_value_selected: str | None
    source_status_system_selected: str | None
    buyer_display_selected: str | None
    buyer_source_id_selected: str | None
    title_selected: str | None
    selected_field_provenance: dict[str, str]
    buyer_display_variance: bool
    lifecycle_status_evidence_ref_id: str | None
    lifecycle_close_evidence_ref_id: str | None
    lifecycle_publication_evidence_ref_id: str | None
    lifecycle_evidence_currentness_class: str | None
    lifecycle_reason_codes: tuple[str, ...]
    evidence_ref_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pr4_procurement_ids"] = list(self.pr4_procurement_ids)
        d["acquisition_snapshot_ids"] = list(self.acquisition_snapshot_ids)
        d["acquisition_instance_ids"] = list(self.acquisition_instance_ids)
        d["acquisition_observation_ids"] = list(self.acquisition_observation_ids)
        d["lifecycle_reason_codes"] = list(self.lifecycle_reason_codes)
        d["evidence_ref_ids"] = list(self.evidence_ref_ids)
        d["conflict_ids"] = list(self.conflict_ids)
        return d


@dataclass(frozen=True)
class Pr4PlaneBundle:
    signals: tuple[dict[str, Any], ...]
    account_resolutions: tuple[dict[str, Any], ...]
    evidence_rows: tuple[dict[str, Any], ...]
    conflict_rows: tuple[dict[str, Any], ...]
    enrichment_rows: tuple[dict[str, Any], ...]
    build_meta: dict[str, str]
    source_fingerprint: str
    build_plan_fingerprint: str
    semantic_plan_digest: str
    readback_semantic_digest: str
    schema_version: str
    build_contract: str
    identity_fingerprint: str
    as_of_date: str
    schema_status: str
    identity_diagnostic: dict[str, Any]


@dataclass(frozen=True)
class CandidatePlanResult:
    evidence_refs: tuple[ProcurementEvidenceRef, ...]
    coalesced_tenders: tuple[CoalescedProcurementTender, ...]
    unresolved: tuple[UnresolvedProcurementEvidence, ...]
    conflicts: tuple[CoalescenceConflict, ...]
    pr4: Pr4PlaneBundle
    acquisition_snapshot_ids: tuple[str, ...]
    acquisition_instance_ids: tuple[str, ...]
    as_of_utc: str
    freshness_threshold_hours: int
    input_source_fingerprint: str
    build_plan_fingerprint: str
    semantic_digest: str
    aggregate_reconciliation: dict[str, Any]
    field_precedence_matrix: dict[str, Any]
    lifecycle_policy: dict[str, Any]
    run_context: str
    planner_version: str
    case_construction_meta: dict[str, Any] = field(default_factory=dict)
