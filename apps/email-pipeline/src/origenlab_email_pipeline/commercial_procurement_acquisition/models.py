"""Versioned acquisition models (evidence only — no relevance/account/contact)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _to_dict(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, list):
        return [_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Never persist credentials.
        d.pop("ticket", None)
        return d

    def identity_payload(self) -> dict[str, Any]:
        return {
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProcurementSourceObservation:
    observation_id: str
    snapshot_id: str
    source_kind: str
    endpoint_kind: str
    source_native_key: str
    canonical_tender_key_candidate: str
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
    raw_payload_digest: str
    parser_version: str
    provenance_reason_codes: tuple[str, ...]
    page_id: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["provenance_reason_codes"] = list(self.provenance_reason_codes)
        return d


@dataclass(frozen=True)
class ProcurementTenderObservation:
    tender_observation_id: str
    source_observation_id: str
    source_kind: str
    normalized_tender_key: str
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
    field_provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProcurementLineObservation:
    line_observation_id: str
    tender_observation_id: str
    source_native_line_id: str | None
    description: str | None
    product: str | None
    category: str | None
    unspsc_or_classification: str | None
    quantity: str | None
    unit: str | None
    ordinal: int
    field_provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
            "materialized_at_utc": self.materialized_at_utc,
        }
