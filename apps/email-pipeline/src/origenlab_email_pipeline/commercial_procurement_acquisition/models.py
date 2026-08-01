"""Versioned acquisition models (evidence only — no relevance/account/contact)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AcquisitionQuery:
    source_kind: str
    endpoint_kind: str
    query_contract_version: str
    acquisition_query_id: str
    estado: str | None = None
    fecha_ddmmaaaa: str | None = None
    tender_code: str | None = None
    year: int | None = None
    month: int | None = None
    range_start: int | None = None
    range_end: int | None = None
    endpoint_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def identity_payload(self) -> dict[str, Any]:
        from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
            ENDPOINT_OCDS_MONTHLY_RANGE,
            OCDS_QUERY_CONTRACT_VERSION,
            OCDS_RANGE_SEMANTICS,
        )

        payload: dict[str, Any] = {
            "source_kind": self.source_kind,
            "endpoint_kind": self.endpoint_kind,
            "query_contract_version": self.query_contract_version,
            "estado": self.estado,
            "fecha_ddmmaaaa": self.fecha_ddmmaaaa,
            "tender_code": self.tender_code,
            "year": self.year,
            "month": self.month,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "endpoint_path": self.endpoint_path,
        }
        # Only acquisition_query_v2 OCDS range queries carry wire-semantics.
        # Historical acquisition_query_v1 identities must not gain this field.
        if (
            self.endpoint_kind == ENDPOINT_OCDS_MONTHLY_RANGE
            and self.query_contract_version == OCDS_QUERY_CONTRACT_VERSION
        ):
            payload["ocds_range_semantics"] = OCDS_RANGE_SEMANTICS
        return payload


@dataclass(frozen=True)
class AcquisitionPage:
    page_id: str
    source_kind: str
    endpoint_kind: str
    acquisition_query_id: str
    range_position: dict[str, Any]
    acquired_at_utc: str | None
    raw_canonical_json_digest: str
    original_bytes_digest: str | None
    parser_input_digest: str
    response_item_count: int
    source_reported_total: int | None
    http_status: int | None
    parser_status: str
    error_classification: str | None
    completeness_status: str
    envelope_meta: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProcurementSourceObservation:
    observation_id: str
    snapshot_id: str
    source_kind: str
    endpoint_kind: str
    source_native_key: str
    source_native_tender_key: str
    canonical_tender_key_candidate: str | None
    canonical_candidate_kind: str
    canonical_candidate_reason: str
    source_status_code: str | None
    source_status_name: str | None
    source_status_system: str
    source_status_value: str | None
    publication_timestamp_raw: str | None
    close_timestamp_raw: str | None
    buyer_display_raw: str | None
    buyer_source_id: str | None
    package_id: str | None
    release_id: str | None
    ocid: str | None
    record_id: str | None
    release_kind: str | None
    release_tags: tuple[str, ...]
    raw_payload_digest: str
    parser_version: str
    provenance_reason_codes: tuple[str, ...]
    page_id: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["provenance_reason_codes"] = list(self.provenance_reason_codes)
        d["release_tags"] = list(self.release_tags)
        return d


@dataclass(frozen=True)
class ProcurementTenderObservation:
    tender_observation_id: str
    source_observation_id: str
    source_kind: str
    normalized_tender_key: str
    source_native_tender_key: str
    canonical_tender_key_candidate: str | None
    canonical_candidate_kind: str
    canonical_candidate_reason: str
    title: str | None
    description: str | None
    buyer_display: str | None
    buyer_source_id: str | None
    publication_timestamp_raw: str | None
    close_timestamp_raw: str | None
    source_status_code: str | None
    source_status_name: str | None
    source_process_stage: str | None
    region: str | None
    currency: str | None
    estimated_value: str | None
    procurement_method: str | None
    procurement_method_details: str | None
    related_processes: tuple[dict[str, str], ...]
    field_provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["related_processes"] = [dict(x) for x in self.related_processes]
        return d


@dataclass(frozen=True)
class ProcurementLineObservation:
    line_observation_id: str
    tender_observation_id: str
    source_native_line_id: str | None
    description: str | None
    product: str | None
    category: str | None
    unspsc_or_classification: str | None
    additional_classifications: tuple[dict[str, str], ...]
    quantity: str | None
    unit: str | None
    ordinal: int
    field_provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["additional_classifications"] = [
            dict(x) for x in self.additional_classifications
        ]
        return d


@dataclass(frozen=True)
class AcquisitionSnapshot:
    snapshot_id: str
    query: AcquisitionQuery
    pages: tuple[AcquisitionPage, ...]
    source_observations: tuple[ProcurementSourceObservation, ...]
    tender_observations: tuple[ProcurementTenderObservation, ...]
    line_observations: tuple[ProcurementLineObservation, ...]
    completeness_status: str
    parser_version: str
    contract_version: str
    fixture_origin: str
    diagnostics: dict[str, Any]
    source_fingerprint: str
    normalized_semantic_digest: str
    # Authoritative source-reported total for this query scope (not page[0],
    # diagnostics, page_count, or observation counts).
    source_reported_total: int | None = None
    materialized_at_utc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "query": self.query.to_dict(),
            "pages": [p.to_dict() for p in self.pages],
            "source_observations": [o.to_dict() for o in self.source_observations],
            "tender_observations": [t.to_dict() for t in self.tender_observations],
            "line_observations": [line.to_dict() for line in self.line_observations],
            "completeness_status": self.completeness_status,
            "parser_version": self.parser_version,
            "contract_version": self.contract_version,
            "fixture_origin": self.fixture_origin,
            "diagnostics": self.diagnostics,
            "source_fingerprint": self.source_fingerprint,
            "normalized_semantic_digest": self.normalized_semantic_digest,
            "source_reported_total": self.source_reported_total,
            "materialized_at_utc": self.materialized_at_utc,
        }


@dataclass(frozen=True)
class DetailAttempt:
    """One detail query attempt within an AcquisitionRun (success or failure)."""

    attempt_id: str
    tender_code: str
    query: AcquisitionQuery
    status: str
    snapshot_id: str | None
    page_id: str | None
    error_classification: str | None
    error_message: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "tender_code": self.tender_code,
            "query": self.query.to_dict(),
            "status": self.status,
            "snapshot_id": self.snapshot_id,
            "page_id": self.page_id,
            "error_classification": self.error_classification,
            "error_message": self.error_message,
            "acquisition_query_id": self.query.acquisition_query_id,
        }


@dataclass(frozen=True)
class AcquisitionRun:
    """Composite run spanning multiple single-scope snapshots/attempts."""

    acquisition_run_id: str
    run_kind: str
    run_contract_version: str
    summary_snapshot_ids: tuple[str, ...]
    detail_attempts: tuple[DetailAttempt, ...]
    child_query_ids: tuple[str, ...]
    child_snapshot_ids: tuple[str, ...]
    child_page_ids: tuple[str, ...]
    requested_detail_count: int
    completed_detail_count: int
    failed_detail_count: int
    run_completeness: str
    run_source_fingerprint: str
    fixture_origin: str
    diagnostics: dict[str, Any]
    materialized_at_utc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "acquisition_run_id": self.acquisition_run_id,
            "run_kind": self.run_kind,
            "run_contract_version": self.run_contract_version,
            "summary_snapshot_ids": list(self.summary_snapshot_ids),
            "detail_attempts": [a.to_dict() for a in self.detail_attempts],
            "child_query_ids": list(self.child_query_ids),
            "child_snapshot_ids": list(self.child_snapshot_ids),
            "child_page_ids": list(self.child_page_ids),
            "requested_detail_count": self.requested_detail_count,
            "completed_detail_count": self.completed_detail_count,
            "failed_detail_count": self.failed_detail_count,
            "run_completeness": self.run_completeness,
            "run_source_fingerprint": self.run_source_fingerprint,
            "fixture_origin": self.fixture_origin,
            "diagnostics": self.diagnostics,
            "materialized_at_utc": self.materialized_at_utc,
        }


@dataclass(frozen=True)
class PartialDetailRunResult:
    """Typed Case D result — failed attempts are pages/attempts, never snapshots."""

    run: AcquisitionRun
    summary_snapshot: AcquisitionSnapshot
    detail_success_snapshot: AcquisitionSnapshot
    failed_detail_attempt: DetailAttempt
    failed_page: AcquisitionPage

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "summary_snapshot": self.summary_snapshot.to_dict(),
            "detail_success_snapshot": self.detail_success_snapshot.to_dict(),
            "failed_detail_attempt": self.failed_detail_attempt.to_dict(),
            "failed_page": self.failed_page.to_dict(),
        }
