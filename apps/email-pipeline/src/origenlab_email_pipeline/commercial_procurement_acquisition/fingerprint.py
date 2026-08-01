"""Deterministic acquisition IDs and snapshot fingerprints."""

from __future__ import annotations

from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
    canonical_json_dumps,
    sha256_hex,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ID_ALGORITHMS,
    NORMALIZED_SEMANTIC_DIGEST_ALGORITHM,
    RUN_SOURCE_FINGERPRINT_ALGORITHM,
    SOURCE_FINGERPRINT_ALGORITHM,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    DetailAttempt,
    ProcurementLineObservation,
    ProcurementSourceObservation,
    ProcurementTenderObservation,
)


def _id(kind: str, payload: dict[str, Any]) -> str:
    algo = ID_ALGORITHMS[kind]
    digest = sha256_hex(f"{algo}|{canonical_json_dumps(payload)}")
    return f"{kind}_{digest[:24]}"


def acquisition_query_id(query_identity: dict[str, Any]) -> str:
    return _id("acquisition_query_id", query_identity)


def acquisition_page_id(
    *,
    acquisition_query_id_value: str,
    range_position: dict[str, Any],
    raw_canonical_json_digest: str,
) -> str:
    return _id(
        "acquisition_page_id",
        {
            "acquisition_query_id": acquisition_query_id_value,
            "range_position": range_position,
            "raw_canonical_json_digest": raw_canonical_json_digest,
        },
    )


def procurement_source_observation_id(
    *,
    source_kind: str,
    endpoint_kind: str,
    source_native_key: str,
    package_id: str | None,
    release_id: str | None,
    release_kind: str | None,
    raw_payload_digest: str,
) -> str:
    return _id(
        "procurement_source_observation_id",
        {
            "source_kind": source_kind,
            "endpoint_kind": endpoint_kind,
            "source_native_key": source_native_key,
            "package_id": package_id or "",
            "release_id": release_id or "",
            "release_kind": release_kind or "",
            "raw_payload_digest": raw_payload_digest,
        },
    )


def procurement_tender_observation_id(
    *,
    source_observation_id: str,
    normalized_tender_key: str,
) -> str:
    return _id(
        "procurement_tender_observation_id",
        {
            "source_observation_id": source_observation_id,
            "normalized_tender_key": normalized_tender_key,
        },
    )


def procurement_line_observation_id(
    *,
    tender_observation_id: str,
    source_native_line_id: str | None,
    ordinal: int,
    description: str | None,
) -> str:
    return _id(
        "procurement_line_observation_id",
        {
            "tender_observation_id": tender_observation_id,
            "source_native_line_id": source_native_line_id or "",
            "ordinal": ordinal,
            "description": (description or "").strip().casefold(),
        },
    )


def procurement_snapshot_id(
    *,
    acquisition_query_id_value: str,
    source_fingerprint: str,
) -> str:
    return _id(
        "procurement_snapshot_id",
        {
            "acquisition_query_id": acquisition_query_id_value,
            "source_fingerprint": source_fingerprint,
        },
    )


def acquisition_run_id(payload: dict[str, Any]) -> str:
    return _id("acquisition_run_id", payload)


def acquisition_source_fingerprint(
    *,
    source_kind: str,
    query_identity: dict[str, Any],
    pages: Iterable[AcquisitionPage],
    completeness_status: str,
) -> str:
    page_rows = [
        {
            "page_id": p.page_id,
            "raw_canonical_json_digest": p.raw_canonical_json_digest,
            "parser_input_digest": p.parser_input_digest,
            "range_position": p.range_position,
            "completeness_status": p.completeness_status,
            "response_item_count": p.response_item_count,
            "source_reported_total": p.source_reported_total,
            "error_classification": p.error_classification,
        }
        for p in pages
    ]
    payload = {
        "algorithm": SOURCE_FINGERPRINT_ALGORITHM,
        "source_kind": source_kind,
        "query_identity": query_identity,
        "pages": page_rows,
        "completeness_status": completeness_status,
    }
    return canonical_json_digest(payload)


def acquisition_run_source_fingerprint(
    *,
    run_kind: str,
    summary_snapshot_ids: Iterable[str],
    detail_attempts: Iterable[DetailAttempt],
    run_completeness: str,
) -> str:
    attempts = [
        {
            "attempt_id": a.attempt_id,
            "tender_code": a.tender_code,
            "acquisition_query_id": a.query.acquisition_query_id,
            "status": a.status,
            "snapshot_id": a.snapshot_id,
            "page_id": a.page_id,
            "error_classification": a.error_classification,
        }
        for a in detail_attempts
    ]
    payload = {
        "algorithm": RUN_SOURCE_FINGERPRINT_ALGORITHM,
        "run_kind": run_kind,
        "summary_snapshot_ids": sorted(summary_snapshot_ids),
        "detail_attempts": sorted(attempts, key=lambda r: r["attempt_id"]),
        "run_completeness": run_completeness,
    }
    return canonical_json_digest(payload)


def acquisition_normalized_semantic_digest(
    *,
    source_observations: Iterable[ProcurementSourceObservation],
    tender_observations: Iterable[ProcurementTenderObservation],
    line_observations: Iterable[ProcurementLineObservation],
    parser_version: str,
    contract_version: str,
) -> str:
    src = sorted(
        (
            {
                "observation_id": o.observation_id,
                "source_kind": o.source_kind,
                "endpoint_kind": o.endpoint_kind,
                "source_native_key": o.source_native_key,
                "source_native_tender_key": o.source_native_tender_key,
                "canonical_tender_key_candidate": o.canonical_tender_key_candidate,
                "canonical_candidate_kind": o.canonical_candidate_kind,
                "canonical_candidate_reason": o.canonical_candidate_reason,
                "source_status_code": o.source_status_code,
                "source_status_name": o.source_status_name,
                "source_status_system": o.source_status_system,
                "source_status_value": o.source_status_value,
                "publication_timestamp_raw": o.publication_timestamp_raw,
                "close_timestamp_raw": o.close_timestamp_raw,
                "buyer_display_raw": o.buyer_display_raw,
                "buyer_source_id": o.buyer_source_id,
                "package_id": o.package_id,
                "release_id": o.release_id,
                "ocid": o.ocid,
                "record_id": o.record_id,
                "release_kind": o.release_kind,
                "release_tags": list(o.release_tags),
                "raw_payload_digest": o.raw_payload_digest,
                "provenance_reason_codes": list(o.provenance_reason_codes),
            }
            for o in source_observations
        ),
        key=lambda r: r["observation_id"],
    )
    tenders = sorted(
        (
            {
                "tender_observation_id": t.tender_observation_id,
                "source_observation_id": t.source_observation_id,
                "normalized_tender_key": t.normalized_tender_key,
                "source_native_tender_key": t.source_native_tender_key,
                "canonical_tender_key_candidate": t.canonical_tender_key_candidate,
                "canonical_candidate_kind": t.canonical_candidate_kind,
                "canonical_candidate_reason": t.canonical_candidate_reason,
                "title": t.title,
                "description": t.description,
                "buyer_display": t.buyer_display,
                "buyer_source_id": t.buyer_source_id,
                "publication_timestamp_raw": t.publication_timestamp_raw,
                "close_timestamp_raw": t.close_timestamp_raw,
                "source_status_code": t.source_status_code,
                "source_status_name": t.source_status_name,
                "source_process_stage": t.source_process_stage,
                "region": t.region,
                "currency": t.currency,
                "estimated_value": t.estimated_value,
                "procurement_method": t.procurement_method,
                "procurement_method_details": t.procurement_method_details,
                "related_processes": [dict(x) for x in t.related_processes],
                "field_provenance": t.field_provenance,
            }
            for t in tender_observations
        ),
        key=lambda r: r["tender_observation_id"],
    )
    lines = sorted(
        (
            {
                "line_observation_id": line.line_observation_id,
                "tender_observation_id": line.tender_observation_id,
                "source_native_line_id": line.source_native_line_id,
                "description": line.description,
                "product": line.product,
                "category": line.category,
                "unspsc_or_classification": line.unspsc_or_classification,
                "additional_classifications": [
                    dict(x) for x in line.additional_classifications
                ],
                "quantity": line.quantity,
                "unit": line.unit,
                "ordinal": line.ordinal,
                "field_provenance": line.field_provenance,
            }
            for line in line_observations
        ),
        key=lambda r: r["line_observation_id"],
    )
    payload = {
        "algorithm": NORMALIZED_SEMANTIC_DIGEST_ALGORITHM,
        "parser_version": parser_version,
        "contract_version": contract_version,
        "source_observations": src,
        "tender_observations": tenders,
        "line_observations": lines,
    }
    return canonical_json_digest(payload)


def query_identity_from_model(query: AcquisitionQuery) -> dict[str, Any]:
    return query.identity_payload()


def payload_digests(
    payload: Any,
    *,
    original_bytes: bytes | str | None = None,
) -> tuple[str, str, str | None]:
    """Return (raw_canonical_json_digest, parser_input_digest, original_bytes_digest).

    For malformed/non-JSON-object inputs, digests still represent the actual supplied
    value (wrapped when needed), never the error message.
    """
    from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
        original_bytes_digest,
    )

    try:
        raw_digest = canonical_json_digest(payload)
        parser_digest = raw_digest
    except (TypeError, ValueError):
        wrapped = {"__non_json_payload__": repr(payload)}
        raw_digest = canonical_json_digest(wrapped)
        parser_digest = raw_digest
    return raw_digest, parser_digest, original_bytes_digest(original_bytes)
