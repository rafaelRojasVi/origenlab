"""Typed immutable plan rows for commercial procurement (PR4)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def _row(dc: Any) -> dict[str, Any]:
    return asdict(dc)


@dataclass(frozen=True)
class SignalRow:
    procurement_id: str
    source_system: str
    canonical_tender_key: str
    tender_key_kind: str
    buyer_name_raw: str | None
    buyer_name_norm: str | None
    buyer_domain_norm: str | None
    buyer_email_norm: str | None
    region: str | None
    title: str | None
    status_code: str | None
    status_name: str | None
    publication_at: str | None
    close_at: str | None
    procurement_context: str
    context_reason_code: str
    confidence: str
    line_item_count: int
    constituent_source_ids_json: str
    constituent_lines_fp: str
    first_seen_at: str | None
    last_seen_at: str | None
    review_status: str

    def to_db_row(self) -> dict[str, Any]:
        return _row(self)


@dataclass(frozen=True)
class AccountResolutionRow:
    resolution_id: str
    procurement_id: str
    resolution_status: str
    account_id: str | None
    link_route: str
    confidence: str
    reason_code: str
    auto_link_allowed: int
    review_status: str
    candidate_account_ids_json: str

    def to_db_row(self) -> dict[str, Any]:
        return _row(self)


@dataclass(frozen=True)
class EvidenceRow:
    evidence_id: str
    subject_kind: str
    subject_id: str
    source_system: str | None
    source_table: str
    source_record_id: str
    subject_key: str | None
    evidence_type: str
    evidence_at: str | None
    reason_code: str
    detail_json: str | None

    def to_db_row(self) -> dict[str, Any]:
        return _row(self)


@dataclass(frozen=True)
class ConflictRow:
    conflict_id: str
    procurement_id: str | None
    source_system: str | None
    source_record_id: str | None
    subject_kind: str
    subject_key: str | None
    account_id: str | None
    reason_code: str
    confidence: str
    detail_json: str | None
    created_at: str

    def to_db_row(self) -> dict[str, Any]:
        return _row(self)


@dataclass(frozen=True)
class EnrichmentCandidateRow:
    candidate_id: str
    procurement_id: str | None
    source_system: str | None
    source_record_id: str | None
    buyer_name_raw: str | None
    account_id: str | None
    reason_code: str
    confidence: str
    recommended_research_field: str
    priority: int
    operator_queue_eligible: int
    candidate_account_ids_json: str | None

    def to_db_row(self) -> dict[str, Any]:
        return _row(self)


@dataclass(frozen=True)
class BuildMetaRow:
    meta_key: str
    meta_value: str

    def to_db_row(self) -> dict[str, Any]:
        return _row(self)


@dataclass(frozen=True)
class ProcurementPlan:
    """Complete immutable build plan for a later persistence PR."""

    signals: tuple[SignalRow, ...]
    resolutions: tuple[AccountResolutionRow, ...]
    evidence: tuple[EvidenceRow, ...]
    conflicts: tuple[ConflictRow, ...]
    enrichment_candidates: tuple[EnrichmentCandidateRow, ...]
    build_meta: tuple[BuildMetaRow, ...]
    source_fingerprint: str
    source_fingerprint_components: dict[str, Any]
    build_plan_fingerprint: str
    plan_digest: str
    identity_fingerprint: str
    identity_fingerprint_algorithm_version: str
    as_of_date: str
    run_context: str
    metrics: dict[str, Any]

    def table_rows(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "commercial_procurement_signal": [r.to_db_row() for r in self.signals],
            "commercial_procurement_account_resolution": [r.to_db_row() for r in self.resolutions],
            "commercial_procurement_evidence": [r.to_db_row() for r in self.evidence],
            "commercial_procurement_conflict": [r.to_db_row() for r in self.conflicts],
            "commercial_procurement_enrichment_candidate": [
                r.to_db_row() for r in self.enrichment_candidates
            ],
            "commercial_procurement_build_meta": [r.to_db_row() for r in self.build_meta],
        }
