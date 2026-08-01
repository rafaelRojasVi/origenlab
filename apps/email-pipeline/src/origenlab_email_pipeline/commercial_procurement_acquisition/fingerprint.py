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
    SOURCE_FINGERPRINT_ALGORITHM,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
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


def acquisition_source_fingerprint(
    *,
    source_kind: str,
    query_identity: dict[str, Any],
    pages: Iterable[AcquisitionPage],
    completeness_status: str,
) -> str:
    """Hash of source kind + sanitized query + ordered page identities + digests.

    Excludes ticket, retry sleep, machine path, PID, logging timestamps,
    and materialization timestamp.
    """
    page_rows = [
        {
            "page_id": p.page_id,
            "raw_canonical_json_digest": p.raw_canonical_json_digest,
            "range_position": p.range_position,
            "completeness_status": p.completeness_status,
            "response_item_count": p.response_item_count,
            "source_reported_total": p.source_reported_total,
        }
        for p in pages
    ]
    payload = {
        "algorithm": SOURCE_FINGERPRINT_ALGORITHM,
        "source_kind": source_kind,
        "query_identity": query_identity,
        "pages": page_rows,  # order preserved as acquired
        "completeness_status": completeness_status,
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
    """Order-independent semantic digest of normalized observations."""
    src = sorted(
        (
            {
                "observation_id": o.observation_id,
                "source_kind": o.source_kind,
                "endpoint_kind": o.endpoint_kind,
                "source_native_key": o.source_native_key,
                "canonical_tender_key_candidate": o.canonical_tender_key_candidate,
                "source_status_code": o.source_status_code,
                "source_status_name": o.source_status_name,
                "source_status_system": o.source_status_system,
                "source_status_value": o.source_status_value,
                "publication_timestamp_raw": o.publication_timestamp_raw,
                "close_timestamp_raw": o.close_timestamp_raw,
                "buyer_source_id": o.buyer_source_id,
                "package_id": o.package_id,
                "release_id": o.release_id,
                "ocid": o.ocid,
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
                "title": t.title,
                "description": t.description,
                "buyer_source_id": t.buyer_source_id,
                "publication_timestamp_raw": t.publication_timestamp_raw,
                "close_timestamp_raw": t.close_timestamp_raw,
                "source_status_code": t.source_status_code,
                "source_status_name": t.source_status_name,
                "source_process_stage": t.source_process_stage,
                "region": t.region,
                "currency": t.currency,
                "estimated_value": t.estimated_value,
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
