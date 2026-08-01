"""Assemble immutable acquisition snapshots from pure parse results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ACQUISITION_CONTRACT_VERSION,
    MANIFEST_VERSION,
    NORMALIZED_SEMANTIC_DIGEST_ALGORITHM,
    PARSER_VERSION,
    SOURCE_FINGERPRINT_ALGORITHM,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_normalized_semantic_digest,
    acquisition_source_fingerprint,
    procurement_snapshot_id,
    query_identity_from_model,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    AcquisitionSnapshot,
    ProcurementSourceObservation,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ocds import (
    parse_ocds_package,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    parse_ticket_detail_payload,
    parse_ticket_summary_payload,
)

SourceKindLiteral = Literal["ticket_summary", "ticket_detail", "ocds"]


def build_acquisition_snapshot(
    *,
    source_kind: SourceKindLiteral,
    payload: Any,
    fixture_origin: str,
    acquired_at_utc: str | None = None,
    original_bytes: bytes | str | None = None,
    http_status: int | None = 200,
    query: AcquisitionQuery | None = None,
    tender_code: str | None = None,
    year: int = 2026,
    month: int = 1,
    range_start: int = 1,
    range_end: int = 1,
    extra_diagnostics: dict[str, Any] | None = None,
    materialized_at_utc: str | None = None,
) -> AcquisitionSnapshot:
    """Build a snapshot from a single offline/fixture payload (no network)."""
    if source_kind == "ticket_summary":
        q, page, sources, tenders, lines, diag = parse_ticket_summary_payload(
            payload,
            query=query,
            acquired_at_utc=acquired_at_utc,
            original_bytes=original_bytes,
            http_status=http_status,
        )
    elif source_kind == "ticket_detail":
        q, page, sources, tenders, lines, diag = parse_ticket_detail_payload(
            payload,
            query=query,
            acquired_at_utc=acquired_at_utc,
            original_bytes=original_bytes,
            http_status=http_status,
            tender_code=tender_code,
        )
    elif source_kind == "ocds":
        q, page, sources, tenders, lines, diag = parse_ocds_package(
            payload,
            query=query,
            acquired_at_utc=acquired_at_utc,
            original_bytes=original_bytes,
            http_status=http_status,
            year=year,
            month=month,
            range_start=range_start,
            range_end=range_end,
        )
    else:
        raise ValueError(f"unsupported source_kind: {source_kind}")

    if extra_diagnostics:
        diag = {**diag, **extra_diagnostics}

    completeness = page.completeness_status
    if fixture_origin.startswith("fixture") and completeness == "complete":
        # Fixture runs remain fixture_only at snapshot level when explicitly marked,
        # but retain page-level complete for successful parses.
        snapshot_completeness = completeness
    else:
        snapshot_completeness = completeness

    identity = query_identity_from_model(q)
    source_fp = acquisition_source_fingerprint(
        source_kind=q.source_kind,
        query_identity=identity,
        pages=[page],
        completeness_status=snapshot_completeness,
    )
    semantic = acquisition_normalized_semantic_digest(
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
    )
    snap_id = procurement_snapshot_id(
        acquisition_query_id_value=q.acquisition_query_id,
        source_fingerprint=source_fp,
    )
    # Rewrite snapshot_id on observations (they were built with pending).
    sources = tuple(
        ProcurementSourceObservation(**{**o.to_dict(), "snapshot_id": snap_id, "provenance_reason_codes": tuple(o.provenance_reason_codes)})
        for o in sources
    )
    tenders_t = tuple(tenders)
    lines_t = tuple(lines)

    materialized = materialized_at_utc or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    return AcquisitionSnapshot(
        snapshot_id=snap_id,
        query=q,
        pages=(page,),
        source_observations=sources,
        tender_observations=tenders_t,
        line_observations=lines_t,
        completeness_status=snapshot_completeness,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
        fixture_origin=fixture_origin,
        diagnostics=diag,
        source_fingerprint=source_fp,
        normalized_semantic_digest=semantic,
        materialized_at_utc=materialized,
    )


def build_partial_detail_snapshot(
    *,
    summary_payload: Any,
    detail_success_payload: Any,
    detail_failure_code: str,
    detail_failure_error: str,
    fixture_origin: str,
    acquired_at_utc: str | None = None,
) -> AcquisitionSnapshot:
    """Case D: summary ok + one detail ok + one detail failed."""
    summary = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=summary_payload,
        fixture_origin=fixture_origin,
        acquired_at_utc=acquired_at_utc,
    )
    detail_ok = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=detail_success_payload,
        fixture_origin=fixture_origin,
        acquired_at_utc=acquired_at_utc,
    )
    pages = list(summary.pages) + list(detail_ok.pages)
    # Synthetic failure page (no network — recorded diagnostic only).
    fail_query = detail_ok.query  # placeholder identity space
    from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
        acquisition_page_id,
    )
    from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
        canonical_json_digest,
    )

    fail_digest = canonical_json_digest(
        {"error": detail_failure_error, "codigo": detail_failure_code}
    )
    fail_page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=fail_query.acquisition_query_id,
            range_position={"page": 0, "codigo": detail_failure_code, "failed": True},
            raw_canonical_json_digest=fail_digest,
        ),
        source_kind=fail_query.source_kind,
        endpoint_kind=fail_query.endpoint_kind,
        acquisition_query_id=fail_query.acquisition_query_id,
        range_position={"page": 0, "codigo": detail_failure_code, "failed": True},
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=fail_digest,
        original_bytes_digest=None,
        parser_input_digest=fail_digest,
        response_item_count=0,
        source_reported_total=None,
        http_status=500,
        parser_status="error",
        error_classification="partial_detail_failure",
        completeness_status="partial_detail_failure",
        envelope_meta={},
    )
    pages.append(fail_page)

    sources = list(summary.source_observations) + list(detail_ok.source_observations)
    tenders = list(summary.tender_observations) + list(detail_ok.tender_observations)
    lines = list(detail_ok.line_observations)

    identity = query_identity_from_model(summary.query)
    completeness = "partial_detail_failure"
    source_fp = acquisition_source_fingerprint(
        source_kind=summary.query.source_kind,
        query_identity=identity,
        pages=pages,
        completeness_status=completeness,
    )
    semantic = acquisition_normalized_semantic_digest(
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
    )
    snap_id = procurement_snapshot_id(
        acquisition_query_id_value=summary.query.acquisition_query_id,
        source_fingerprint=source_fp,
    )
    diagnostics = {
        "summary_snapshot_complete": summary.completeness_status == "complete",
        "requested_detail_count": 2,
        "completed_detail_count": 1,
        "failed_detail_count": 1,
        "failed_detail_codes": [detail_failure_code],
        "detail_snapshot_completeness": "partial_detail_failure",
        "retained_successful_evidence": True,
        "false_complete": False,
    }
    return AcquisitionSnapshot(
        snapshot_id=snap_id,
        query=summary.query,
        pages=tuple(pages),
        source_observations=tuple(
            ProcurementSourceObservation(
                **{
                    **o.to_dict(),
                    "snapshot_id": snap_id,
                    "provenance_reason_codes": tuple(o.provenance_reason_codes),
                }
            )
            for o in sources
        ),
        tender_observations=tuple(tenders),
        line_observations=tuple(lines),
        completeness_status=completeness,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
        fixture_origin=fixture_origin,
        diagnostics=diagnostics,
        source_fingerprint=source_fp,
        normalized_semantic_digest=semantic,
        materialized_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def snapshot_manifest(
    snapshot: AcquisitionSnapshot,
    *,
    ticket_configured: bool,
) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "snapshot_contract_version": snapshot.contract_version,
        "snapshot_id": snapshot.snapshot_id,
        "source_kind": snapshot.query.source_kind,
        "endpoint_kind": snapshot.query.endpoint_kind,
        "sanitized_query_identity": query_identity_from_model(snapshot.query),
        "fixture_origin": snapshot.fixture_origin,
        "acquired_at_utc": snapshot.pages[0].acquired_at_utc if snapshot.pages else None,
        "materialized_at_utc": snapshot.materialized_at_utc,
        "parser_version": snapshot.parser_version,
        "page_count": len(snapshot.pages),
        "source_observation_count": len(snapshot.source_observations),
        "tender_observation_count": len(snapshot.tender_observations),
        "line_observation_count": len(snapshot.line_observations),
        "source_reported_total": snapshot.pages[0].source_reported_total
        if snapshot.pages
        else None,
        "completeness_status": snapshot.completeness_status,
        "page_diagnostics": [p.to_dict() for p in snapshot.pages],
        "source_fingerprint_algorithm": SOURCE_FINGERPRINT_ALGORITHM,
        "source_fingerprint": snapshot.source_fingerprint,
        "normalized_semantic_digest_algorithm": NORMALIZED_SEMANTIC_DIGEST_ALGORITHM,
        "normalized_semantic_digest": snapshot.normalized_semantic_digest,
        "ticket_configured": bool(ticket_configured),
        "authenticated_request_performed": False,
        "production_apply": False,
        "production_mutation": False,
        "scheduler_changed": False,
        "current_pointer_published": False,
    }
