"""Plane B — strict AcquisitionSnapshot materialization (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_normalized_semantic_digest,
    acquisition_query_id,
    acquisition_source_fingerprint,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    AcquisitionSnapshot,
    ProcurementLineObservation,
    ProcurementSourceObservation,
    ProcurementTenderObservation,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    EVIDENCE_PLANE_ACQUISITION,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    ProcurementEvidenceRef,
    UnresolvedProcurementEvidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    accept_canonical_tender_key,
    rank_class_for_live,
    stable_content_id,
)


class AcquisitionPlaneError(ValueError):
    """Invalid acquisition snapshot identity or fingerprint mismatch."""


def _as_tuple_str(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(x) for x in value)
    return (str(value),)


def _query_from_dict(d: dict[str, Any]) -> AcquisitionQuery:
    return AcquisitionQuery(
        source_kind=str(d["source_kind"]),
        endpoint_kind=str(d["endpoint_kind"]),
        query_contract_version=str(d["query_contract_version"]),
        acquisition_query_id=str(d["acquisition_query_id"]),
        estado=d.get("estado"),
        fecha_ddmmaaaa=d.get("fecha_ddmmaaaa"),
        tender_code=d.get("tender_code"),
        year=d.get("year"),
        month=d.get("month"),
        range_start=d.get("range_start"),
        range_end=d.get("range_end"),
        endpoint_path=d.get("endpoint_path"),
    )


def _page_from_dict(d: dict[str, Any]) -> AcquisitionPage:
    return AcquisitionPage(
        page_id=str(d["page_id"]),
        source_kind=str(d["source_kind"]),
        endpoint_kind=str(d["endpoint_kind"]),
        acquisition_query_id=str(d["acquisition_query_id"]),
        range_position=dict(d.get("range_position") or {}),
        acquired_at_utc=d.get("acquired_at_utc"),
        raw_canonical_json_digest=str(d["raw_canonical_json_digest"]),
        original_bytes_digest=d.get("original_bytes_digest"),
        parser_input_digest=str(d["parser_input_digest"]),
        response_item_count=int(d["response_item_count"]),
        source_reported_total=d.get("source_reported_total"),
        http_status=d.get("http_status"),
        parser_status=str(d["parser_status"]),
        error_classification=d.get("error_classification"),
        completeness_status=str(d["completeness_status"]),
        envelope_meta=dict(d.get("envelope_meta") or {}),
        error_message=d.get("error_message"),
    )


def _source_from_dict(d: dict[str, Any]) -> ProcurementSourceObservation:
    return ProcurementSourceObservation(
        observation_id=str(d["observation_id"]),
        snapshot_id=str(d["snapshot_id"]),
        source_kind=str(d["source_kind"]),
        endpoint_kind=str(d["endpoint_kind"]),
        source_native_key=str(d["source_native_key"]),
        source_native_tender_key=str(d["source_native_tender_key"]),
        canonical_tender_key_candidate=d.get("canonical_tender_key_candidate"),
        canonical_candidate_kind=str(d.get("canonical_candidate_kind") or "none"),
        canonical_candidate_reason=str(d.get("canonical_candidate_reason") or ""),
        source_status_code=d.get("source_status_code"),
        source_status_name=d.get("source_status_name"),
        source_status_system=str(d.get("source_status_system") or ""),
        source_status_value=d.get("source_status_value"),
        publication_timestamp_raw=d.get("publication_timestamp_raw"),
        close_timestamp_raw=d.get("close_timestamp_raw"),
        buyer_display_raw=d.get("buyer_display_raw"),
        buyer_source_id=d.get("buyer_source_id"),
        package_id=d.get("package_id"),
        release_id=d.get("release_id"),
        ocid=d.get("ocid"),
        record_id=d.get("record_id"),
        release_kind=d.get("release_kind"),
        release_tags=_as_tuple_str(d.get("release_tags")),
        raw_payload_digest=str(d["raw_payload_digest"]),
        parser_version=str(d["parser_version"]),
        provenance_reason_codes=_as_tuple_str(d.get("provenance_reason_codes")),
        page_id=str(d["page_id"]),
    )


def _tender_from_dict(d: dict[str, Any]) -> ProcurementTenderObservation:
    related = d.get("related_processes") or []
    return ProcurementTenderObservation(
        tender_observation_id=str(d["tender_observation_id"]),
        source_observation_id=str(d["source_observation_id"]),
        source_kind=str(d["source_kind"]),
        normalized_tender_key=str(d["normalized_tender_key"]),
        source_native_tender_key=str(d["source_native_tender_key"]),
        canonical_tender_key_candidate=d.get("canonical_tender_key_candidate"),
        canonical_candidate_kind=str(d.get("canonical_candidate_kind") or "none"),
        canonical_candidate_reason=str(d.get("canonical_candidate_reason") or ""),
        title=d.get("title"),
        description=d.get("description"),
        buyer_display=d.get("buyer_display"),
        buyer_source_id=d.get("buyer_source_id"),
        publication_timestamp_raw=d.get("publication_timestamp_raw"),
        close_timestamp_raw=d.get("close_timestamp_raw"),
        source_status_code=d.get("source_status_code"),
        source_status_name=d.get("source_status_name"),
        source_process_stage=d.get("source_process_stage"),
        region=d.get("region"),
        currency=d.get("currency"),
        estimated_value=d.get("estimated_value"),
        procurement_method=d.get("procurement_method"),
        procurement_method_details=d.get("procurement_method_details"),
        related_processes=tuple(dict(x) for x in related),
        field_provenance=dict(d.get("field_provenance") or {}),
    )


def _line_from_dict(d: dict[str, Any]) -> ProcurementLineObservation:
    addl = d.get("additional_classifications") or []
    return ProcurementLineObservation(
        line_observation_id=str(d["line_observation_id"]),
        tender_observation_id=str(d["tender_observation_id"]),
        source_native_line_id=d.get("source_native_line_id"),
        description=d.get("description"),
        product=d.get("product"),
        category=d.get("category"),
        unspsc_or_classification=d.get("unspsc_or_classification"),
        additional_classifications=tuple(dict(x) for x in addl),
        quantity=d.get("quantity"),
        unit=d.get("unit"),
        ordinal=int(d["ordinal"]),
        field_provenance=dict(d.get("field_provenance") or {}),
    )


def materialize_acquisition_snapshot(payload: dict[str, Any]) -> AcquisitionSnapshot:
    """Strictly materialize and re-validate snapshot identity + fingerprints."""
    if not isinstance(payload, dict):
        raise AcquisitionPlaneError("acquisition snapshot must be a JSON object")
    required = (
        "snapshot_id",
        "query",
        "pages",
        "source_observations",
        "tender_observations",
        "line_observations",
        "completeness_status",
        "parser_version",
        "contract_version",
        "source_fingerprint",
        "normalized_semantic_digest",
    )
    missing = [k for k in required if k not in payload]
    if missing:
        raise AcquisitionPlaneError(f"acquisition snapshot missing keys: {missing}")

    query = _query_from_dict(dict(payload["query"]))
    recomputed_qid = acquisition_query_id(query.identity_payload())
    if recomputed_qid != query.acquisition_query_id:
        raise AcquisitionPlaneError("acquisition query identity mismatch")

    pages = tuple(_page_from_dict(dict(p)) for p in payload["pages"])
    for page in pages:
        if page.acquisition_query_id != query.acquisition_query_id:
            raise AcquisitionPlaneError("page acquisition_query_id mismatch")

    sources = tuple(_source_from_dict(dict(o)) for o in payload["source_observations"])
    tenders = tuple(_tender_from_dict(dict(t)) for t in payload["tender_observations"])
    lines = tuple(_line_from_dict(dict(line)) for line in payload["line_observations"])

    snap = AcquisitionSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        query=query,
        pages=pages,
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        completeness_status=str(payload["completeness_status"]),
        parser_version=str(payload["parser_version"]),
        contract_version=str(payload["contract_version"]),
        fixture_origin=str(payload.get("fixture_origin") or "unknown"),
        diagnostics=dict(payload.get("diagnostics") or {}),
        source_fingerprint=str(payload["source_fingerprint"]),
        normalized_semantic_digest=str(payload["normalized_semantic_digest"]),
        source_reported_total=payload.get("source_reported_total"),
        materialized_at_utc=payload.get("materialized_at_utc"),
    )

    recomputed_fp = acquisition_source_fingerprint(
        source_kind=query.source_kind,
        query_identity=query.identity_payload(),
        pages=pages,
        completeness_status=snap.completeness_status,
        source_reported_total=snap.source_reported_total,
    )
    if recomputed_fp != snap.source_fingerprint:
        raise AcquisitionPlaneError("acquisition source_fingerprint mismatch")

    recomputed_sem = acquisition_normalized_semantic_digest(
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        parser_version=snap.parser_version,
        contract_version=snap.contract_version,
    )
    if recomputed_sem != snap.normalized_semantic_digest:
        raise AcquisitionPlaneError("acquisition normalized_semantic_digest mismatch")

    return snap


def load_acquisition_snapshot_json(path: Path) -> AcquisitionSnapshot:
    text = Path(path).read_text(encoding="utf-8")
    payload = json.loads(text)
    return materialize_acquisition_snapshot(payload)


def _page_by_id(snap: AcquisitionSnapshot) -> dict[str, AcquisitionPage]:
    return {p.page_id: p for p in snap.pages}


def _tender_by_source_obs(snap: AcquisitionSnapshot) -> dict[str, ProcurementTenderObservation]:
    out: dict[str, ProcurementTenderObservation] = {}
    for t in snap.tender_observations:
        out[t.source_observation_id] = t
    return out


def snapshot_to_evidence(
    snap: AcquisitionSnapshot,
) -> tuple[list[ProcurementEvidenceRef], list[UnresolvedProcurementEvidence]]:
    """Split source observations into accepted evidence refs vs unresolved."""
    pages = _page_by_id(snap)
    tenders = _tender_by_source_obs(snap)
    refs: list[ProcurementEvidenceRef] = []
    unresolved: list[UnresolvedProcurementEvidence] = []

    for obs in snap.source_observations:
        page = pages.get(obs.page_id)
        page_status = page.completeness_status if page else "missing_page"
        acquired = page.acquired_at_utc if page else None
        tender = tenders.get(obs.observation_id)

        incomplete = page_status not in {"complete", "empty_page", "partial_page_failure"}
        if page is not None and page.error_classification and page_status == "malformed_response":
            incomplete = True
        if (
            incomplete
            or page_status in {"malformed_response", "failed"}
            or (page is not None and page.parser_status == "error")
        ):
            unresolved.append(
                UnresolvedProcurementEvidence(
                    unresolved_id=stable_content_id(
                        "unresolved",
                        {
                            "snapshot_id": snap.snapshot_id,
                            "observation_id": obs.observation_id,
                            "reason": "incomplete_or_failed_page",
                        },
                    ),
                    evidence_plane=EVIDENCE_PLANE_ACQUISITION,
                    source_kind=obs.source_kind,
                    endpoint_kind=obs.endpoint_kind,
                    source_record_id=obs.source_native_key,
                    snapshot_id=snap.snapshot_id,
                    observation_id=obs.observation_id,
                    unresolved_reason="incomplete_or_failed_page",
                    canonical_candidate_kind=obs.canonical_candidate_kind,
                    canonical_tender_key_candidate=obs.canonical_tender_key_candidate,
                    source_native_tender_key=obs.source_native_tender_key,
                    reason_codes=("incomplete_or_failed_page", page_status),
                    source_payload_digest=obs.raw_payload_digest,
                    acquired_at_utc=acquired,
                )
            )
            continue

        key, reject = accept_canonical_tender_key(
            candidate=obs.canonical_tender_key_candidate,
            candidate_kind=obs.canonical_candidate_kind,
        )
        if key is None:
            reason = reject or "live_canonical_candidate_missing"
            if obs.canonical_candidate_reason == "unresolved_ocid_only_no_mp_codigo":
                reason = "ocds_ocid_only_unresolved"
            elif obs.canonical_candidate_kind not in {
                "mercado_publico_codigo_externo",
                "none",
                "",
                None,
            }:
                reason = "unsupported_candidate_kind"
            elif obs.source_native_tender_key and not obs.canonical_tender_key_candidate:
                if reason == "live_canonical_candidate_missing":
                    reason = "source_native_identity_not_canonical"
            unresolved.append(
                UnresolvedProcurementEvidence(
                    unresolved_id=stable_content_id(
                        "unresolved",
                        {
                            "snapshot_id": snap.snapshot_id,
                            "observation_id": obs.observation_id,
                            "reason": reason,
                        },
                    ),
                    evidence_plane=EVIDENCE_PLANE_ACQUISITION,
                    source_kind=obs.source_kind,
                    endpoint_kind=obs.endpoint_kind,
                    source_record_id=obs.source_native_key,
                    snapshot_id=snap.snapshot_id,
                    observation_id=obs.observation_id,
                    unresolved_reason=reason,
                    canonical_candidate_kind=obs.canonical_candidate_kind,
                    canonical_tender_key_candidate=obs.canonical_tender_key_candidate,
                    source_native_tender_key=obs.source_native_tender_key,
                    reason_codes=(reason, obs.canonical_candidate_reason or ""),
                    source_payload_digest=obs.raw_payload_digest,
                    acquired_at_utc=acquired,
                )
            )
            continue

        rank_class = rank_class_for_live(
            source_kind=obs.source_kind,
            endpoint_kind=obs.endpoint_kind,
            release_kind=obs.release_kind,
        )
        pub = (
            (tender.publication_timestamp_raw if tender else None)
            or obs.publication_timestamp_raw
        )
        close = (
            (tender.close_timestamp_raw if tender else None) or obs.close_timestamp_raw
        )
        buyer = (tender.buyer_display if tender else None) or obs.buyer_display_raw
        buyer_id = (tender.buyer_source_id if tender else None) or obs.buyer_source_id
        title = tender.title if tender else None
        status_code = (
            (tender.source_status_code if tender else None) or obs.source_status_code
        )
        status_name = (
            (tender.source_status_name if tender else None) or obs.source_status_name
        )

        ref_id = stable_content_id(
            "evidence_ref",
            {
                "plane": EVIDENCE_PLANE_ACQUISITION,
                "snapshot_id": snap.snapshot_id,
                "observation_id": obs.observation_id,
                "canonical_tender_key": key,
            },
        )
        refs.append(
            ProcurementEvidenceRef(
                evidence_ref_id=ref_id,
                evidence_plane=EVIDENCE_PLANE_ACQUISITION,
                source_kind=obs.source_kind,
                endpoint_kind=obs.endpoint_kind,
                source_record_id=obs.source_native_key,
                canonical_tender_key=key,
                snapshot_id=snap.snapshot_id,
                observation_id=obs.observation_id,
                acquired_at_utc=acquired,
                source_status_code=status_code,
                source_status_name=status_name,
                source_status_value=obs.source_status_value or status_name,
                publication_timestamp_raw=pub,
                close_timestamp_raw=close,
                buyer_display_raw=buyer,
                buyer_source_id=buyer_id,
                title_raw=title,
                source_payload_digest=obs.raw_payload_digest,
                source_fingerprint=snap.source_fingerprint,
                normalized_semantic_digest=snap.normalized_semantic_digest,
                field_provenance={
                    "status": rank_class,
                    "close": rank_class,
                    "publication": rank_class,
                    "buyer_display": rank_class,
                    "buyer_source_id": rank_class,
                    "title": rank_class,
                },
                reason_codes=tuple(obs.provenance_reason_codes)
                + (("partial_page",) if page_status == "partial_page_failure" else ()),
                source_rank_class=rank_class,
                has_status=bool(status_code or status_name),
                has_close=bool(close),
                has_publication=bool(pub),
                has_buyer_display=bool(buyer),
                has_buyer_source_id=bool(buyer_id),
                has_title=bool(title),
                page_completeness=page_status,
            )
        )
    return refs, unresolved
