"""Assemble immutable acquisition snapshots and composite AcquisitionRun."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ACQUISITION_CONTRACT_VERSION,
    MANIFEST_VERSION,
    NORMALIZED_SEMANTIC_DIGEST_ALGORITHM,
    PARSER_VERSION,
    RUN_CONTRACT_VERSION,
    SOURCE_FINGERPRINT_ALGORITHM,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_normalized_semantic_digest,
    acquisition_page_id,
    acquisition_run_id,
    acquisition_run_source_fingerprint,
    acquisition_source_fingerprint,
    payload_digests,
    procurement_snapshot_id,
    query_identity_from_model,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    AcquisitionRun,
    AcquisitionSnapshot,
    DetailAttempt,
    ProcurementSourceObservation,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ocds import (
    parse_ocds_package,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    sanitize_error_message,
    ticket_safety_flags,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    build_ticket_detail_query,
    parse_ticket_detail_payload,
    parse_ticket_summary_payload,
)

SourceKindLiteral = Literal["ticket_summary", "ticket_detail", "ocds"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rewrite_source_snapshot_ids(
    sources: list[ProcurementSourceObservation], snap_id: str
) -> tuple[ProcurementSourceObservation, ...]:
    return tuple(
        ProcurementSourceObservation(
            **{
                **o.to_dict(),
                "snapshot_id": snap_id,
                "provenance_reason_codes": tuple(o.provenance_reason_codes),
                "release_tags": tuple(o.release_tags),
            }
        )
        for o in sources
    )


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
    """Build a snapshot from a single offline/fixture payload (one query scope)."""
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

    # fixture_origin is separate from content completeness — never rewrite status.
    snapshot_completeness = page.completeness_status

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
    return AcquisitionSnapshot(
        snapshot_id=snap_id,
        query=q,
        pages=(page,),
        source_observations=_rewrite_source_snapshot_ids(sources, snap_id),
        tender_observations=tuple(tenders),
        line_observations=tuple(lines),
        completeness_status=snapshot_completeness,
        parser_version=PARSER_VERSION,
        contract_version=ACQUISITION_CONTRACT_VERSION,
        fixture_origin=fixture_origin,
        diagnostics=diag,
        source_fingerprint=source_fp,
        normalized_semantic_digest=semantic,
        materialized_at_utc=materialized_at_utc or _utcnow(),
    )


def _detail_code_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    listado = payload.get("Listado")
    if isinstance(listado, list) and listado and isinstance(listado[0], dict):
        code = listado[0].get("CodigoExterno")
        return str(code) if code is not None else None
    return None


def build_partial_detail_run(
    *,
    summary_payload: Any,
    detail_success_payload: Any,
    detail_failure_code: str,
    detail_failure_error: str,
    fixture_origin: str,
    acquired_at_utc: str | None = None,
    materialized_at_utc: str | None = None,
    detail_success_code: str | None = None,
) -> tuple[AcquisitionRun, dict[str, AcquisitionSnapshot]]:
    """Case D: one summary snapshot + detail A success + detail B failed attempt.

    Each detail attempt has its own AcquisitionQuery / query ID.
    """
    summary = build_acquisition_snapshot(
        source_kind="ticket_summary",
        payload=summary_payload,
        fixture_origin=fixture_origin,
        acquired_at_utc=acquired_at_utc,
        materialized_at_utc=materialized_at_utc,
    )
    code_a = detail_success_code or _detail_code_from_payload(detail_success_payload)
    if not code_a:
        raise ValueError("detail_success_code required when payload lacks CodigoExterno")
    detail_ok = build_acquisition_snapshot(
        source_kind="ticket_detail",
        payload=detail_success_payload,
        fixture_origin=fixture_origin,
        acquired_at_utc=acquired_at_utc,
        materialized_at_utc=materialized_at_utc,
        tender_code=code_a,
    )

    fail_query = build_ticket_detail_query(tender_code=detail_failure_code)
    # Simulated transport/parser failure — fingerprint the actual failure payload,
    # not the error wording alone.
    fail_payload = {
        "simulated_failure": True,
        "codigo": detail_failure_code,
        "http_status": 500,
    }
    raw_digest, parser_digest, bytes_digest = payload_digests(fail_payload)
    fail_page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=fail_query.acquisition_query_id,
            range_position={"page": 0, "codigo": fail_query.tender_code},
            raw_canonical_json_digest=raw_digest,
        ),
        source_kind=fail_query.source_kind,
        endpoint_kind=fail_query.endpoint_kind,
        acquisition_query_id=fail_query.acquisition_query_id,
        range_position={"page": 0, "codigo": fail_query.tender_code},
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=raw_digest,
        original_bytes_digest=bytes_digest,
        parser_input_digest=parser_digest,
        response_item_count=0,
        source_reported_total=None,
        http_status=500,
        parser_status="error",
        error_classification="partial_detail_failure",
        completeness_status="partial_detail_failure",
        envelope_meta={},
        error_message=sanitize_error_message(detail_failure_error),
    )

    ok_code = detail_ok.query.tender_code or ""
    attempt_ok = DetailAttempt(
        attempt_id=f"detail_ok:{ok_code}",
        tender_code=ok_code,
        query=detail_ok.query,
        status="completed",
        snapshot_id=detail_ok.snapshot_id,
        page_id=detail_ok.pages[0].page_id if detail_ok.pages else None,
        error_classification=None,
        error_message=None,
    )
    attempt_fail = DetailAttempt(
        attempt_id=f"detail_fail:{fail_query.tender_code}",
        tender_code=fail_query.tender_code or detail_failure_code,
        query=fail_query,
        status="failed",
        snapshot_id=None,
        page_id=fail_page.page_id,
        error_classification="partial_detail_failure",
        error_message=sanitize_error_message(detail_failure_error),
    )

    child_query_ids = tuple(
        sorted(
            {
                summary.query.acquisition_query_id,
                detail_ok.query.acquisition_query_id,
                fail_query.acquisition_query_id,
            }
        )
    )
    # Distinct query IDs required: A success vs B failure.
    assert (
        detail_ok.query.acquisition_query_id != fail_query.acquisition_query_id
    ), "detail A and B must not share acquisition_query_id"

    child_snapshot_ids = (summary.snapshot_id, detail_ok.snapshot_id)
    child_page_ids = tuple(
        sorted(
            [p.page_id for p in summary.pages]
            + [p.page_id for p in detail_ok.pages]
            + [fail_page.page_id]
        )
    )

    run_kind = "ticket_summary_plus_detail_attempts"
    run_completeness = "partial_detail_failure"
    run_fp = acquisition_run_source_fingerprint(
        run_kind=run_kind,
        summary_snapshot_ids=[summary.snapshot_id],
        detail_attempts=[attempt_ok, attempt_fail],
        run_completeness=run_completeness,
    )
    run_id = acquisition_run_id(
        {
            "run_kind": run_kind,
            "summary_snapshot_ids": [summary.snapshot_id],
            "detail_attempt_ids": [attempt_ok.attempt_id, attempt_fail.attempt_id],
            "run_source_fingerprint": run_fp,
        }
    )
    diagnostics = {
        "summary_snapshot_complete": summary.completeness_status == "complete",
        "requested_detail_count": 2,
        "completed_detail_count": 1,
        "failed_detail_count": 1,
        "failed_detail_codes": [fail_query.tender_code],
        "retained_successful_evidence": True,
        "false_complete": False,
        "detail_a_query_id": detail_ok.query.acquisition_query_id,
        "detail_b_query_id": fail_query.acquisition_query_id,
        "failed_page_payload_digest": fail_page.raw_canonical_json_digest,
        "note": "each AcquisitionSnapshot retains one query scope; run is composite",
    }
    run = AcquisitionRun(
        acquisition_run_id=run_id,
        run_kind=run_kind,
        run_contract_version=RUN_CONTRACT_VERSION,
        summary_snapshot_ids=(summary.snapshot_id,),
        detail_attempts=(attempt_ok, attempt_fail),
        child_query_ids=child_query_ids,
        child_snapshot_ids=child_snapshot_ids,
        child_page_ids=child_page_ids,
        requested_detail_count=2,
        completed_detail_count=1,
        failed_detail_count=1,
        run_completeness=run_completeness,
        run_source_fingerprint=run_fp,
        fixture_origin=fixture_origin,
        diagnostics=diagnostics,
        materialized_at_utc=materialized_at_utc or _utcnow(),
    )
    children = {
        "summary": summary,
        "detail_a": detail_ok,
        "detail_b_failed_page": AcquisitionSnapshot(
            snapshot_id=f"failed_attempt:{fail_page.page_id}",
            query=fail_query,
            pages=(fail_page,),
            source_observations=(),
            tender_observations=(),
            line_observations=(),
            completeness_status="partial_detail_failure",
            parser_version=PARSER_VERSION,
            contract_version=ACQUISITION_CONTRACT_VERSION,
            fixture_origin=fixture_origin,
            diagnostics={"simulated_failure": True, "codigo": fail_query.tender_code},
            source_fingerprint=canonical_json_digest(
                {
                    "page_id": fail_page.page_id,
                    "raw": fail_page.raw_canonical_json_digest,
                }
            ),
            normalized_semantic_digest=canonical_json_digest({"empty": True}),
            materialized_at_utc=materialized_at_utc or _utcnow(),
        ),
    }
    return run, children


# Backward-compatible alias used by older walkthrough imports during transition.
def build_partial_detail_snapshot(**kwargs: Any) -> AcquisitionRun:
    run, _ = build_partial_detail_run(**kwargs)
    return run


def snapshot_manifest(
    snapshot: AcquisitionSnapshot,
    *,
    ticket_configured: bool,
) -> dict[str, Any]:
    safety = ticket_safety_flags(ticket_configured=ticket_configured)
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
        **safety,
        "production_apply": False,
        "production_mutation": False,
        "scheduler_changed": False,
        "current_pointer_published": False,
    }


def run_manifest(run: AcquisitionRun, *, ticket_configured: bool) -> dict[str, Any]:
    safety = ticket_safety_flags(ticket_configured=ticket_configured)
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_contract_version": run.run_contract_version,
        "acquisition_run_id": run.acquisition_run_id,
        "run_kind": run.run_kind,
        "fixture_origin": run.fixture_origin,
        "run_completeness": run.run_completeness,
        "run_source_fingerprint": run.run_source_fingerprint,
        "summary_snapshot_ids": list(run.summary_snapshot_ids),
        "child_query_ids": list(run.child_query_ids),
        "child_snapshot_ids": list(run.child_snapshot_ids),
        "child_page_ids": list(run.child_page_ids),
        "detail_attempts": [a.to_dict() for a in run.detail_attempts],
        "requested_detail_count": run.requested_detail_count,
        "completed_detail_count": run.completed_detail_count,
        "failed_detail_count": run.failed_detail_count,
        "diagnostics": run.diagnostics,
        "materialized_at_utc": run.materialized_at_utc,
        **safety,
        "production_apply": False,
        "production_mutation": False,
        "scheduler_changed": False,
    }
