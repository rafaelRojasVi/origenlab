"""Plane B — strict AcquisitionSnapshot materialization (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ACQUISITION_CONTRACT_VERSION,
    ENDPOINT_OCDS_MONTHLY_MONTH,
    ENDPOINT_OCDS_MONTHLY_RANGE,
    ENDPOINT_PATH_OCDS_MONTH_TEMPLATE,
    ENDPOINT_PATH_OCDS_TEMPLATE,
    ENDPOINT_PATH_TICKET,
    ENDPOINT_TICKET_LICITACION_DETAIL,
    ENDPOINT_TICKET_LICITACIONES_SUMMARY,
    OCDS_MAX_PAGE_SIZE,
    OCDS_QUERY_CONTRACT_VERSION,
    PARSER_VERSION,
    QUERY_CONTRACT_VERSION,
    SOURCE_KIND_OCDS,
    SOURCE_KIND_TICKET_API,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_normalized_semantic_digest,
    acquisition_page_id,
    acquisition_query_id,
    acquisition_source_fingerprint,
    procurement_line_observation_id,
    procurement_snapshot_id,
    procurement_source_observation_id,
    procurement_tender_observation_id,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    AcquisitionSnapshot,
    ProcurementLineObservation,
    ProcurementSourceObservation,
    ProcurementTenderObservation,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.acquisition_instance import (
    acquisition_instance_event_payload,
    candidate_acquisition_instance_id,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    CANONICAL_KIND_MERCADO_PUBLICO,
    EVIDENCE_PLANE_ACQUISITION,
    IDENTITY_NS_MERCADO_PUBLICO,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    ProcurementEvidenceRef,
    UnresolvedProcurementEvidence,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    accept_canonical_tender_key,
    normalize_tender_timestamp,
    normalized_status_meaning,
    rank_class_for_live,
    stable_content_id,
)

SUPPORTED_ACQUISITION_CONTRACTS = frozenset({ACQUISITION_CONTRACT_VERSION})
SUPPORTED_PARSER_VERSIONS = frozenset({PARSER_VERSION})
SUPPORTED_QUERY_CONTRACTS = frozenset(
    {QUERY_CONTRACT_VERSION, OCDS_QUERY_CONTRACT_VERSION}
)


class AcquisitionPlaneError(ValueError):
    """Invalid acquisition snapshot identity or fingerprint mismatch."""


def _as_tuple_str(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(x) for x in value)
    return (str(value),)


def _require_non_bool_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AcquisitionPlaneError(f"{field} must be a non-bool integer")
    return value


def _require_nonneg_int(value: Any, *, field: str) -> int:
    n = _require_non_bool_int(value, field=field)
    if n < 0:
        raise AcquisitionPlaneError(f"{field} must be a non-negative integer")
    return n


def _optional_nonneg_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _require_nonneg_int(value, field=field)


def _query_from_dict(d: dict[str, Any]) -> AcquisitionQuery:
    year = d.get("year")
    month = d.get("month")
    range_start = d.get("range_start")
    range_end = d.get("range_end")
    if year is not None:
        year = _require_non_bool_int(year, field="query.year")
    if month is not None:
        month = _require_non_bool_int(month, field="query.month")
    if range_start is not None:
        range_start = _require_non_bool_int(range_start, field="query.range_start")
    if range_end is not None:
        range_end = _require_non_bool_int(range_end, field="query.range_end")
    return AcquisitionQuery(
        source_kind=str(d["source_kind"]),
        endpoint_kind=str(d["endpoint_kind"]),
        query_contract_version=str(d["query_contract_version"]),
        acquisition_query_id=str(d["acquisition_query_id"]),
        estado=d.get("estado"),
        fecha_ddmmaaaa=d.get("fecha_ddmmaaaa"),
        tender_code=d.get("tender_code"),
        year=year,
        month=month,
        range_start=range_start,
        range_end=range_end,
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
        response_item_count=_require_nonneg_int(
            d["response_item_count"], field="page.response_item_count"
        ),
        source_reported_total=_optional_nonneg_int(
            d.get("source_reported_total"), field="page.source_reported_total"
        ),
        http_status=(
            None
            if d.get("http_status") is None
            else _require_non_bool_int(d["http_status"], field="page.http_status")
        ),
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
        ordinal=_require_nonneg_int(d["ordinal"], field="line.ordinal"),
        field_provenance=dict(d.get("field_provenance") or {}),
    )


def _validate_versions(
    query: AcquisitionQuery, *, parser_version: str, contract_version: str
) -> None:
    if contract_version not in SUPPORTED_ACQUISITION_CONTRACTS:
        raise AcquisitionPlaneError(
            f"unsupported acquisition contract_version={contract_version!r}"
        )
    if parser_version not in SUPPORTED_PARSER_VERSIONS:
        raise AcquisitionPlaneError(
            f"unsupported parser_version={parser_version!r}"
        )
    if query.query_contract_version not in SUPPORTED_QUERY_CONTRACTS:
        raise AcquisitionPlaneError(
            f"unsupported query_contract_version={query.query_contract_version!r}"
        )


def _require_year_month(query: AcquisitionQuery) -> tuple[int, int]:
    if query.year is None or query.month is None:
        raise AcquisitionPlaneError("OCDS query requires year and month")
    year = _require_non_bool_int(query.year, field="query.year")
    month = _require_non_bool_int(query.month, field="query.month")
    if not (1 <= month <= 12):
        raise AcquisitionPlaneError("query.month must be 1..12")
    return year, month


def _validate_ticket_range_position(
    page: AcquisitionPage, *, expected: dict[str, Any]
) -> None:
    if page.range_position != expected:
        raise AcquisitionPlaneError("ticket page range_position mismatch")


def _validate_ticket_query_pages(
    query: AcquisitionQuery, pages: tuple[AcquisitionPage, ...]
) -> None:
    if query.query_contract_version != QUERY_CONTRACT_VERSION:
        raise AcquisitionPlaneError(
            "ticket query_contract_version must be acquisition_query_v1"
        )
    if query.source_kind != SOURCE_KIND_TICKET_API:
        raise AcquisitionPlaneError("ticket source_kind mismatch")
    if query.endpoint_path != ENDPOINT_PATH_TICKET:
        raise AcquisitionPlaneError("ticket endpoint_path mismatch")

    if query.endpoint_kind == ENDPOINT_TICKET_LICITACIONES_SUMMARY:
        expected_rp = {"page": 0}
    elif query.endpoint_kind == ENDPOINT_TICKET_LICITACION_DETAIL:
        tender_code = query.tender_code
        if tender_code is None or not str(tender_code).strip():
            raise AcquisitionPlaneError("ticket detail requires tender_code")
        expected_rp = {"page": 0, "codigo": tender_code}
    else:
        raise AcquisitionPlaneError(
            f"unsupported ticket endpoint_kind={query.endpoint_kind!r}"
        )

    for page in pages:
        if page.source_kind != SOURCE_KIND_TICKET_API:
            raise AcquisitionPlaneError("page source_kind mismatch vs ticket query")
        if page.endpoint_kind != query.endpoint_kind:
            raise AcquisitionPlaneError("page endpoint_kind mismatch vs ticket query")
        _validate_ticket_range_position(page, expected=expected_rp)


def _validate_ocds_lista_v2_query_pages(
    query: AcquisitionQuery, pages: tuple[AcquisitionPage, ...]
) -> None:
    if query.query_contract_version != OCDS_QUERY_CONTRACT_VERSION:
        raise AcquisitionPlaneError(
            "OCDS lista range requires acquisition_query_v2"
        )
    if query.source_kind != SOURCE_KIND_OCDS:
        raise AcquisitionPlaneError("OCDS lista range source_kind mismatch")
    if query.endpoint_kind != ENDPOINT_OCDS_MONTHLY_RANGE:
        raise AcquisitionPlaneError("OCDS lista range endpoint_kind mismatch")

    year, month = _require_year_month(query)
    if query.range_start is None or query.range_end is None:
        raise AcquisitionPlaneError("OCDS lista range requires range_start/range_end")
    range_start = _require_non_bool_int(query.range_start, field="query.range_start")
    range_end = _require_non_bool_int(query.range_end, field="query.range_end")
    if range_start < 0:
        raise AcquisitionPlaneError("query.range_start must be >= 0")
    if range_end < range_start:
        raise AcquisitionPlaneError("query.range_end must be >= range_start")
    limit = range_end - range_start + 1
    if limit < 1 or limit > OCDS_MAX_PAGE_SIZE:
        raise AcquisitionPlaneError(
            f"OCDS lista limit must be 1..{OCDS_MAX_PAGE_SIZE}"
        )
    expected_path = ENDPOINT_PATH_OCDS_TEMPLATE.format(
        year=year, month=month, start=range_start, end=limit
    )
    if query.endpoint_path != expected_path:
        raise AcquisitionPlaneError("OCDS lista endpoint_path mismatch")

    expected_rp = {"start": range_start, "end": range_end}
    for page in pages:
        if page.source_kind != SOURCE_KIND_OCDS:
            raise AcquisitionPlaneError("page source_kind mismatch vs OCDS query")
        if page.endpoint_kind != ENDPOINT_OCDS_MONTHLY_RANGE:
            raise AcquisitionPlaneError("page endpoint_kind mismatch vs OCDS query")
        if page.range_position != expected_rp:
            raise AcquisitionPlaneError("OCDS page range_position mismatch vs query")


def _validate_ocds_month_query_pages(
    query: AcquisitionQuery, pages: tuple[AcquisitionPage, ...]
) -> None:
    if query.query_contract_version != QUERY_CONTRACT_VERSION:
        raise AcquisitionPlaneError(
            "OCDS month package requires acquisition_query_v1"
        )
    if query.source_kind != SOURCE_KIND_OCDS:
        raise AcquisitionPlaneError("OCDS month source_kind mismatch")
    if query.endpoint_kind != ENDPOINT_OCDS_MONTHLY_MONTH:
        raise AcquisitionPlaneError("OCDS month endpoint_kind mismatch")
    if query.range_start is not None or query.range_end is not None:
        raise AcquisitionPlaneError(
            "OCDS month package requires range_start/range_end None"
        )
    year, month = _require_year_month(query)
    expected_path = ENDPOINT_PATH_OCDS_MONTH_TEMPLATE.format(year=year, month=month)
    if query.endpoint_path != expected_path:
        raise AcquisitionPlaneError("OCDS month endpoint_path mismatch")

    for page in pages:
        if page.source_kind != SOURCE_KIND_OCDS:
            raise AcquisitionPlaneError("page source_kind mismatch vs OCDS month query")
        if page.endpoint_kind not in {
            ENDPOINT_OCDS_MONTHLY_RANGE,
            ENDPOINT_OCDS_MONTHLY_MONTH,
        }:
            raise AcquisitionPlaneError(
                "OCDS month child page endpoint_kind unsupported"
            )


def _validate_ocds_lista_v1_historical(
    query: AcquisitionQuery, pages: tuple[AcquisitionPage, ...]
) -> None:
    """Accept historical acquisition_query_v1 range identities without offset/limit reinterpretation."""
    if query.query_contract_version != QUERY_CONTRACT_VERSION:
        raise AcquisitionPlaneError(
            "historical OCDS range requires acquisition_query_v1"
        )
    if query.source_kind != SOURCE_KIND_OCDS:
        raise AcquisitionPlaneError("OCDS range source_kind mismatch")
    if query.endpoint_kind != ENDPOINT_OCDS_MONTHLY_RANGE:
        raise AcquisitionPlaneError("OCDS range endpoint_kind mismatch")
    for page in pages:
        if page.source_kind != SOURCE_KIND_OCDS:
            raise AcquisitionPlaneError("page source_kind mismatch vs OCDS query")
        if page.endpoint_kind != ENDPOINT_OCDS_MONTHLY_RANGE:
            raise AcquisitionPlaneError("page endpoint_kind mismatch vs OCDS query")


def _validate_endpoint_contracts(
    query: AcquisitionQuery, pages: tuple[AcquisitionPage, ...]
) -> None:
    ek = query.endpoint_kind
    if ek in {ENDPOINT_TICKET_LICITACIONES_SUMMARY, ENDPOINT_TICKET_LICITACION_DETAIL}:
        _validate_ticket_query_pages(query, pages)
        return
    if ek == ENDPOINT_OCDS_MONTHLY_MONTH:
        _validate_ocds_month_query_pages(query, pages)
        return
    if ek == ENDPOINT_OCDS_MONTHLY_RANGE:
        if query.query_contract_version == OCDS_QUERY_CONTRACT_VERSION:
            _validate_ocds_lista_v2_query_pages(query, pages)
        elif query.query_contract_version == QUERY_CONTRACT_VERSION:
            _validate_ocds_lista_v1_historical(query, pages)
        else:
            raise AcquisitionPlaneError(
                f"unsupported OCDS range query_contract_version="
                f"{query.query_contract_version!r}"
            )
        return
    raise AcquisitionPlaneError(f"unsupported endpoint_kind={ek!r}")


def _page_links_to_query(
    query: AcquisitionQuery, page: AcquisitionPage
) -> None:
    if query.endpoint_kind == ENDPOINT_OCDS_MONTHLY_MONTH:
        return
    if page.acquisition_query_id != query.acquisition_query_id:
        raise AcquisitionPlaneError("page acquisition_query_id mismatch")
    if page.source_kind != query.source_kind or page.endpoint_kind != query.endpoint_kind:
        raise AcquisitionPlaneError("page source_kind/endpoint_kind mismatch vs query")


def _recompute_source_observation_id(obs: ProcurementSourceObservation) -> str:
    recomputed = procurement_source_observation_id(
        source_kind=obs.source_kind,
        endpoint_kind=obs.endpoint_kind,
        source_native_key=obs.source_native_key,
        package_id=obs.package_id,
        release_id=obs.release_id,
        release_kind=obs.release_kind,
        raw_payload_digest=obs.raw_payload_digest,
    )
    if recomputed == obs.observation_id:
        return recomputed
    if (
        "lista_index_row" in obs.provenance_reason_codes
        and obs.release_kind is None
    ):
        alt = procurement_source_observation_id(
            source_kind=obs.source_kind,
            endpoint_kind=obs.endpoint_kind,
            source_native_key=obs.source_native_key,
            package_id=obs.package_id,
            release_id=obs.release_id,
            release_kind="lista_index",
            raw_payload_digest=obs.raw_payload_digest,
        )
        if alt == obs.observation_id:
            return alt
    raise AcquisitionPlaneError("source observation_id recompute mismatch")


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
    _validate_versions(
        query,
        parser_version=str(payload["parser_version"]),
        contract_version=str(payload["contract_version"]),
    )
    recomputed_qid = acquisition_query_id(query.identity_payload())
    if recomputed_qid != query.acquisition_query_id:
        raise AcquisitionPlaneError("acquisition query identity mismatch")

    pages = tuple(_page_from_dict(dict(p)) for p in payload["pages"])
    page_ids = [p.page_id for p in pages]
    if len(page_ids) != len(set(page_ids)):
        raise AcquisitionPlaneError("duplicate page_id")

    _validate_endpoint_contracts(query, pages)

    for page in pages:
        _page_links_to_query(query, page)
        recomputed_page = acquisition_page_id(
            acquisition_query_id_value=page.acquisition_query_id,
            range_position=page.range_position,
            raw_canonical_json_digest=page.raw_canonical_json_digest,
        )
        if recomputed_page != page.page_id:
            raise AcquisitionPlaneError("page_id recompute mismatch")

    source_reported_total = _optional_nonneg_int(
        payload.get("source_reported_total"), field="source_reported_total"
    )
    for page in pages:
        if (
            source_reported_total is not None
            and page.source_reported_total is not None
            and page.source_reported_total != source_reported_total
        ):
            raise AcquisitionPlaneError(
                "page/snapshot source_reported_total mismatch"
            )

    sources = tuple(_source_from_dict(dict(o)) for o in payload["source_observations"])
    source_ids = [o.observation_id for o in sources]
    if len(source_ids) != len(set(source_ids)):
        raise AcquisitionPlaneError("duplicate source observation_id")
    page_id_set = set(page_ids)
    for obs in sources:
        if obs.snapshot_id != str(payload["snapshot_id"]):
            raise AcquisitionPlaneError("observation.snapshot_id mismatch")
        if obs.page_id not in page_id_set:
            raise AcquisitionPlaneError("observation page_id missing")
        page = next(p for p in pages if p.page_id == obs.page_id)
        if obs.source_kind != page.source_kind or obs.endpoint_kind != page.endpoint_kind:
            raise AcquisitionPlaneError(
                "observation source_kind/endpoint_kind mismatch vs page"
            )
        if obs.parser_version not in SUPPORTED_PARSER_VERSIONS:
            raise AcquisitionPlaneError(
                f"unsupported observation parser_version={obs.parser_version!r}"
            )
        _recompute_source_observation_id(obs)

    tenders = tuple(_tender_from_dict(dict(t)) for t in payload["tender_observations"])
    tender_ids = [t.tender_observation_id for t in tenders]
    if len(tender_ids) != len(set(tender_ids)):
        raise AcquisitionPlaneError("duplicate tender_observation_id")
    source_id_set = set(source_ids)
    source_by_id = {o.observation_id: o for o in sources}
    for tender in tenders:
        if tender.source_observation_id not in source_id_set:
            raise AcquisitionPlaneError("tender source_observation_id missing")
        src = source_by_id[tender.source_observation_id]
        if tender.source_kind != src.source_kind:
            raise AcquisitionPlaneError("tender source_kind mismatch vs source observation")
        if (
            tender.canonical_tender_key_candidate
            and src.canonical_tender_key_candidate
            and tender.canonical_tender_key_candidate != src.canonical_tender_key_candidate
        ):
            raise AcquisitionPlaneError(
                "tender canonical candidate contradicts source observation"
            )
        recomputed_tender = procurement_tender_observation_id(
            source_observation_id=tender.source_observation_id,
            normalized_tender_key=tender.normalized_tender_key,
        )
        if recomputed_tender != tender.tender_observation_id:
            raise AcquisitionPlaneError("tender_observation_id recompute mismatch")

    tender_by_source: dict[str, ProcurementTenderObservation] = {}
    for tender in tenders:
        prior = tender_by_source.get(tender.source_observation_id)
        if prior is not None and prior.tender_observation_id != tender.tender_observation_id:
            raise AcquisitionPlaneError(
                "duplicate tender observations for one source observation"
            )
        tender_by_source[tender.source_observation_id] = tender

    lines = tuple(_line_from_dict(dict(line)) for line in payload["line_observations"])
    line_ids = [ln.line_observation_id for ln in lines]
    if len(line_ids) != len(set(line_ids)):
        raise AcquisitionPlaneError("duplicate line_observation_id")
    tender_id_set = set(tender_ids)
    for line in lines:
        if line.tender_observation_id not in tender_id_set:
            raise AcquisitionPlaneError("line tender_observation_id missing")
        recomputed_line = procurement_line_observation_id(
            tender_observation_id=line.tender_observation_id,
            source_native_line_id=line.source_native_line_id,
            ordinal=line.ordinal,
            description=line.description,
        )
        if recomputed_line != line.line_observation_id:
            raise AcquisitionPlaneError("line_observation_id recompute mismatch")

    completeness = str(payload["completeness_status"])
    page_statuses = {p.completeness_status for p in pages}
    if completeness == "complete" and any(
        s in {"malformed_response", "failed"} for s in page_statuses
    ):
        raise AcquisitionPlaneError("snapshot completeness disagrees with page statuses")

    recomputed_fp = acquisition_source_fingerprint(
        source_kind=query.source_kind,
        query_identity=query.identity_payload(),
        pages=pages,
        completeness_status=completeness,
        source_reported_total=source_reported_total,
    )
    if recomputed_fp != str(payload["source_fingerprint"]):
        raise AcquisitionPlaneError("acquisition source_fingerprint mismatch")

    recomputed_sem = acquisition_normalized_semantic_digest(
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        parser_version=str(payload["parser_version"]),
        contract_version=str(payload["contract_version"]),
    )
    if recomputed_sem != str(payload["normalized_semantic_digest"]):
        raise AcquisitionPlaneError("acquisition normalized_semantic_digest mismatch")

    recomputed_snap_id = procurement_snapshot_id(
        acquisition_query_id_value=query.acquisition_query_id,
        source_fingerprint=recomputed_fp,
    )
    if recomputed_snap_id != str(payload["snapshot_id"]):
        raise AcquisitionPlaneError("snapshot_id recompute mismatch")

    return AcquisitionSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        query=query,
        pages=pages,
        source_observations=sources,
        tender_observations=tenders,
        line_observations=lines,
        completeness_status=completeness,
        parser_version=str(payload["parser_version"]),
        contract_version=str(payload["contract_version"]),
        fixture_origin=str(payload.get("fixture_origin") or "unknown"),
        diagnostics=dict(payload.get("diagnostics") or {}),
        source_fingerprint=str(payload["source_fingerprint"]),
        normalized_semantic_digest=str(payload["normalized_semantic_digest"]),
        source_reported_total=source_reported_total,
        materialized_at_utc=payload.get("materialized_at_utc"),
    )


def load_acquisition_snapshot_json(path: Path) -> AcquisitionSnapshot:
    text = Path(path).read_text(encoding="utf-8")
    payload = json.loads(text)
    return materialize_acquisition_snapshot(payload)


def dedupe_acquisition_instances(
    snapshots: list[AcquisitionSnapshot],
) -> list[tuple[str, AcquisitionSnapshot]]:
    """Dedupe by acquisition_instance_id; keep distinct capture events separately.

    Same snapshot_id with different acquired_at yields different instance IDs and
    both are retained. Exact same instance payload is kept once. Same instance_id
    with divergent event metadata is rejected.
    """
    by_id: dict[str, AcquisitionSnapshot] = {}
    for snap in snapshots:
        instance_id = candidate_acquisition_instance_id(snap)
        prior = by_id.get(instance_id)
        if prior is None:
            by_id[instance_id] = snap
            continue
        if acquisition_instance_event_payload(
            prior
        ) != acquisition_instance_event_payload(snap):
            raise AcquisitionPlaneError(
                f"duplicate acquisition_instance_id with divergent event metadata: "
                f"{instance_id}"
            )
    return [(iid, by_id[iid]) for iid in sorted(by_id.keys())]


def _page_by_id(snap: AcquisitionSnapshot) -> dict[str, AcquisitionPage]:
    return {p.page_id: p for p in snap.pages}


def _tender_by_source_obs(
    snap: AcquisitionSnapshot,
) -> dict[str, ProcurementTenderObservation]:
    out: dict[str, ProcurementTenderObservation] = {}
    for t in snap.tender_observations:
        if t.source_observation_id in out:
            raise AcquisitionPlaneError(
                "duplicate tender observations for one source observation"
            )
        out[t.source_observation_id] = t
    return out


def _unresolved(
    *,
    snap: AcquisitionSnapshot,
    obs: ProcurementSourceObservation,
    acquisition_instance_id: str,
    reason: str,
    acquired: str | None,
    extra_codes: tuple[str, ...] = (),
) -> UnresolvedProcurementEvidence:
    return UnresolvedProcurementEvidence(
        unresolved_id=stable_content_id(
            "unresolved",
            {
                "acquisition_instance_id": acquisition_instance_id,
                "observation_id": obs.observation_id,
                "reason": reason,
            },
        ),
        evidence_plane=EVIDENCE_PLANE_ACQUISITION,
        source_kind=obs.source_kind,
        endpoint_kind=obs.endpoint_kind,
        source_record_id=obs.source_native_key,
        snapshot_id=snap.snapshot_id,
        acquisition_instance_id=acquisition_instance_id,
        observation_id=obs.observation_id,
        unresolved_reason=reason,
        canonical_candidate_kind=obs.canonical_candidate_kind,
        canonical_tender_key_candidate=obs.canonical_tender_key_candidate,
        source_native_tender_key=obs.source_native_tender_key,
        tender_key_kind=None,
        identity_namespace=None,
        pr4_procurement_id=None,
        reason_codes=(reason,) + extra_codes,
        source_payload_digest=obs.raw_payload_digest,
        acquired_at_utc=acquired,
    )


def snapshot_to_evidence(
    snap: AcquisitionSnapshot,
    *,
    acquisition_instance_id: str,
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
        page_id = page.page_id if page else obs.page_id
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
                _unresolved(
                    snap=snap,
                    obs=obs,
                    acquisition_instance_id=acquisition_instance_id,
                    reason="incomplete_or_failed_page",
                    acquired=acquired,
                    extra_codes=(page_status,),
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
                _unresolved(
                    snap=snap,
                    obs=obs,
                    acquisition_instance_id=acquisition_instance_id,
                    reason=reason,
                    acquired=acquired,
                    extra_codes=(obs.canonical_candidate_reason or "",),
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
        pub_nt = normalize_tender_timestamp(pub)
        close_nt = normalize_tender_timestamp(close)
        ts_reasons = tuple(r for r in (pub_nt.reason, close_nt.reason) if r)

        ref_id = stable_content_id(
            "evidence_ref",
            {
                "plane": EVIDENCE_PLANE_ACQUISITION,
                "acquisition_instance_id": acquisition_instance_id,
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
                tender_key_kind=CANONICAL_KIND_MERCADO_PUBLICO,
                identity_namespace=IDENTITY_NS_MERCADO_PUBLICO,
                cross_source_join_eligible=True,
                snapshot_id=snap.snapshot_id,
                acquisition_instance_id=acquisition_instance_id,
                page_id=page_id,
                observation_id=obs.observation_id,
                acquired_at_utc=acquired,
                source_status_code=status_code,
                source_status_name=status_name,
                source_status_value=obs.source_status_value or status_name,
                source_status_system=obs.source_status_system or None,
                normalized_status_meaning=normalized_status_meaning(
                    status_code, status_name
                ),
                publication_timestamp_raw=pub,
                close_timestamp_raw=close,
                publication_timestamp_utc=pub_nt.utc_iso,
                close_timestamp_utc=close_nt.utc_iso,
                publication_santiago_date=(
                    pub_nt.santiago_date.isoformat() if pub_nt.santiago_date else None
                ),
                close_santiago_date=(
                    close_nt.santiago_date.isoformat() if close_nt.santiago_date else None
                ),
                publication_precision=pub_nt.precision if pub else None,
                close_precision=close_nt.precision if close else None,
                timestamp_parse_reasons=ts_reasons,
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
