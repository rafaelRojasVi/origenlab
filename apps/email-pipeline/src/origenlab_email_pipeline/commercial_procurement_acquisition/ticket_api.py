"""Ticket API pure parsers — no network, no ticket access."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.chilecompra_api import (
    _as_str,
    _buyer_name,
    _codigo_externo,
    _extract_close_date,
    _extract_items,
    _extract_listado,
    _extract_status_fields,
    _normalize_item_fields,
    _region_name,
    validate_fecha,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ENDPOINT_PATH_TICKET,
    ENDPOINT_TICKET_LICITACION_DETAIL,
    ENDPOINT_TICKET_LICITACIONES_SUMMARY,
    PARSER_VERSION,
    QUERY_CONTRACT_VERSION,
    SOURCE_KIND_TICKET_API,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_page_id,
    acquisition_query_id,
    payload_digests,
    procurement_line_observation_id,
    procurement_source_observation_id,
    procurement_tender_observation_id,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.identity import (
    normalize_mercado_publico_codigo,
    ticket_canonical_candidate,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionPage,
    AcquisitionQuery,
    ProcurementLineObservation,
    ProcurementSourceObservation,
    ProcurementTenderObservation,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.redaction import (
    sanitize_error_message,
    sanitize_mapping,
)


class TicketApiParseError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(sanitize_error_message(message))


def _as_optional_str(value: Any) -> str | None:
    text = _as_str(value)
    return text or None


def _extract_publication_date(licitacion: dict[str, Any]) -> str:
    """Mirror close-date extraction: top-level first, then Fechas nested object.

    Live Ticket detail envelopes place FechaPublicacion under Fechas only.
    """
    for key in ("FechaPublicacion", "FechaCreacion"):
        text = _as_str(licitacion.get(key))
        if text:
            return text
    fechas = licitacion.get("Fechas")
    if isinstance(fechas, dict):
        for key in ("FechaPublicacion", "FechaCreacion"):
            text = _as_str(fechas.get(key))
            if text:
                return text
    return ""


def _normalize_estado(estado: str | None) -> str | None:
    if estado is None:
        return None
    text = str(estado).strip().casefold()
    return text or None


def build_ticket_summary_query(
    *,
    estado: str | None = "activas",
    fecha_ddmmaaaa: str | None = None,
) -> AcquisitionQuery:
    estado_n = _normalize_estado(estado) or "activas"
    fecha_n = validate_fecha(fecha_ddmmaaaa) if fecha_ddmmaaaa else None
    identity = {
        "source_kind": SOURCE_KIND_TICKET_API,
        "endpoint_kind": ENDPOINT_TICKET_LICITACIONES_SUMMARY,
        "query_contract_version": QUERY_CONTRACT_VERSION,
        "estado": estado_n,
        "fecha_ddmmaaaa": fecha_n,
        "tender_code": None,
        "year": None,
        "month": None,
        "range_start": None,
        "range_end": None,
        "endpoint_path": ENDPOINT_PATH_TICKET,
    }
    return AcquisitionQuery(
        acquisition_query_id=acquisition_query_id(identity),
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
        query_contract_version=QUERY_CONTRACT_VERSION,
        estado=estado_n,
        fecha_ddmmaaaa=fecha_n,
        tender_code=None,
        year=None,
        month=None,
        range_start=None,
        range_end=None,
        endpoint_path=ENDPOINT_PATH_TICKET,
    )


def build_ticket_detail_query(*, tender_code: str) -> AcquisitionQuery:
    code = normalize_mercado_publico_codigo(tender_code)
    if not code:
        raise TicketApiParseError("tender_code is required for detail query")
    # Preserve original casing for query identity via normalized form only.
    identity = {
        "source_kind": SOURCE_KIND_TICKET_API,
        "endpoint_kind": ENDPOINT_TICKET_LICITACION_DETAIL,
        "query_contract_version": QUERY_CONTRACT_VERSION,
        "estado": None,
        "fecha_ddmmaaaa": None,
        "tender_code": code,
        "year": None,
        "month": None,
        "range_start": None,
        "range_end": None,
        "endpoint_path": ENDPOINT_PATH_TICKET,
    }
    return AcquisitionQuery(
        acquisition_query_id=acquisition_query_id(identity),
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
        query_contract_version=QUERY_CONTRACT_VERSION,
        estado=None,
        fecha_ddmmaaaa=None,
        tender_code=code,
        year=None,
        month=None,
        range_start=None,
        range_end=None,
        endpoint_path=ENDPOINT_PATH_TICKET,
    )


def _envelope_meta(payload: dict[str, Any]) -> dict[str, Any]:
    return sanitize_mapping(
        {
            "Cantidad": payload.get("Cantidad"),
            "FechaCreacion": payload.get("FechaCreacion"),
            "Version": payload.get("Version"),
        }
    )


def _line_native_id(item: dict[str, Any]) -> str | None:
    for key in ("Correlativo", "CodigoProducto", "CodigoCategoria", "CodigoUNSPSC"):
        text = _as_str(item.get(key))
        if text:
            return f"{key}:{text}"
    return None


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple:
        return (
            _as_str(item.get("Correlativo")),
            _as_str(item.get("CodigoProducto")),
            _as_str(item.get("Descripcion") or item.get("NombreProducto")).casefold(),
            canonical_json_digest(item),
        )

    return sorted(items, key=key)


def _malformed_page(
    *,
    query: AcquisitionQuery,
    endpoint_kind: str,
    payload: Any,
    original_bytes: bytes | str | None,
    http_status: int | None,
    classification: str,
    message: str,
    range_position: dict[str, Any],
    acquired_at_utc: str | None = None,
) -> AcquisitionPage:
    raw_digest, parser_digest, bytes_digest = payload_digests(
        payload, original_bytes=original_bytes
    )
    return AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position=range_position,
            raw_canonical_json_digest=raw_digest,
        ),
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=endpoint_kind,
        acquisition_query_id=query.acquisition_query_id,
        range_position=range_position,
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=raw_digest,
        original_bytes_digest=bytes_digest,
        parser_input_digest=parser_digest,
        response_item_count=0,
        source_reported_total=None,
        http_status=http_status,
        parser_status="malformed" if classification == "malformed_response" else "error",
        error_classification=classification,
        completeness_status=classification,
        envelope_meta={},
        error_message=sanitize_error_message(message),
    )


def _strict_summary_listado(
    payload: dict[str, Any],
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, str]]]:
    """Validate Listado before legacy extraction.

    Returns (malformed_reason, valid_entries, rejected_entries).
    """
    if "Listado" not in payload:
        return "missing_listado", [], []
    listado = payload.get("Listado")
    if listado is None:
        return None, [], []
    if isinstance(listado, list):
        raw_entries = listado
    elif isinstance(listado, dict):
        nested = listado.get("Licitacion")
        if nested is None:
            raw_entries = [listado]
        elif isinstance(nested, list):
            raw_entries = nested
        elif isinstance(nested, dict):
            raw_entries = [nested]
        else:
            return "invalid_nested_licitacion", [], []
    else:
        return "invalid_listado_scalar", [], []

    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for ordinal, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            rejected.append(
                {
                    "ordinal": str(ordinal),
                    "reason_code": "entry_not_object",
                    "entry_digest": canonical_json_digest(
                        {"ordinal": ordinal, "type": type(entry).__name__}
                    ),
                }
            )
            continue
        if not _codigo_externo(entry):
            rejected.append(
                {
                    "ordinal": str(ordinal),
                    "reason_code": "missing_codigo_externo",
                    "entry_digest": canonical_json_digest(
                        {
                            "ordinal": ordinal,
                            "keys": sorted(str(k) for k in entry.keys()),
                        }
                    ),
                }
            )
            continue
        valid.append(entry)
    return None, valid, rejected


def _build_observations_from_licitacion(
    licitacion: dict[str, Any],
    *,
    snapshot_id: str,
    page_id: str,
    endpoint_kind: str,
    include_lines: bool,
) -> tuple[
    ProcurementSourceObservation,
    ProcurementTenderObservation,
    list[ProcurementLineObservation],
]:
    codigo = _codigo_externo(licitacion)
    if not codigo:
        raise TicketApiParseError("licitacion missing CodigoExterno")
    cand = ticket_canonical_candidate(codigo)
    status_code, status_name = _extract_status_fields(licitacion)
    buyer = _buyer_name(licitacion) or None
    buyer_id = None
    comprador = licitacion.get("Comprador")
    if isinstance(comprador, dict):
        buyer_id = _as_optional_str(
            comprador.get("CodigoOrganismo") or comprador.get("RutUnidad")
        )
    pub = _extract_publication_date(licitacion) or None
    close = _extract_close_date(licitacion) or None
    raw_digest = canonical_json_digest(licitacion)
    obs_id = procurement_source_observation_id(
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=endpoint_kind,
        source_native_key=cand.source_native_tender_key,
        package_id=None,
        release_id=None,
        release_kind=None,
        raw_payload_digest=raw_digest,
    )
    # Tender observation key uses source-native identity (not a false shared prefix).
    tender_key = cand.source_native_tender_key
    source = ProcurementSourceObservation(
        observation_id=obs_id,
        snapshot_id=snapshot_id,
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=endpoint_kind,
        source_native_key=cand.source_native_tender_key,
        source_native_tender_key=cand.source_native_tender_key,
        canonical_tender_key_candidate=cand.canonical_tender_key_candidate,
        canonical_candidate_kind=cand.canonical_candidate_kind,
        canonical_candidate_reason=cand.canonical_candidate_reason,
        source_status_code=status_code or None,
        source_status_name=status_name or None,
        source_status_system="mercado_publico_codigo_estado",
        source_status_value=status_code or status_name or None,
        publication_timestamp_raw=pub,
        close_timestamp_raw=close,
        buyer_display_raw=buyer,
        buyer_source_id=buyer_id,
        package_id=None,
        release_id=None,
        ocid=None,
        record_id=None,
        release_kind=None,
        release_tags=(),
        raw_payload_digest=raw_digest,
        parser_version=PARSER_VERSION,
        provenance_reason_codes=("ticket_api_listado_item",),
        page_id=page_id,
    )
    tender_id = procurement_tender_observation_id(
        source_observation_id=obs_id, normalized_tender_key=tender_key
    )
    tender = ProcurementTenderObservation(
        tender_observation_id=tender_id,
        source_observation_id=obs_id,
        source_kind=SOURCE_KIND_TICKET_API,
        normalized_tender_key=tender_key,
        source_native_tender_key=cand.source_native_tender_key,
        canonical_tender_key_candidate=cand.canonical_tender_key_candidate,
        canonical_candidate_kind=cand.canonical_candidate_kind,
        canonical_candidate_reason=cand.canonical_candidate_reason,
        title=_as_optional_str(licitacion.get("Nombre") or licitacion.get("Titulo")),
        description=_as_optional_str(licitacion.get("Descripcion")),
        buyer_display=buyer,
        buyer_source_id=buyer_id,
        publication_timestamp_raw=pub,
        close_timestamp_raw=close,
        source_status_code=status_code or None,
        source_status_name=status_name or None,
        source_process_stage=_as_optional_str(licitacion.get("EstadoEtapas")),
        region=_region_name(licitacion) or None,
        currency=_as_optional_str(licitacion.get("Moneda")),
        estimated_value=_as_optional_str(licitacion.get("MontoEstimado")),
        procurement_method=None,
        procurement_method_details=None,
        related_processes=(),
        field_provenance={
            "canonical_tender_key_candidate": "CodigoExterno",
            "title": "Nombre|Titulo",
            "publication_timestamp_raw": (
                "FechaPublicacion|FechaCreacion|Fechas.FechaPublicacion|Fechas.FechaCreacion"
            ),
            "close_timestamp_raw": "FechaCierre|Fechas.FechaCierre",
            "buyer_display": "Comprador.NombreOrganismo",
        },
    )
    lines: list[ProcurementLineObservation] = []
    if include_lines:
        items = _sort_items(_extract_items(licitacion))
        seen: dict[str, int] = {}
        for ordinal, item in enumerate(items):
            fields = _normalize_item_fields(item)
            native = _line_native_id(item)
            if native:
                seen[native] = seen.get(native, 0) + 1
                if seen[native] > 1:
                    native = f"{native}#occ{seen[native]}"
            line_id = procurement_line_observation_id(
                tender_observation_id=tender_id,
                source_native_line_id=native,
                ordinal=ordinal,
                description=fields.get("line_description"),
            )
            lines.append(
                ProcurementLineObservation(
                    line_observation_id=line_id,
                    tender_observation_id=tender_id,
                    source_native_line_id=native,
                    description=fields.get("line_description") or None,
                    product=fields.get("producto") or None,
                    category=fields.get("nivel_1") or None,
                    unspsc_or_classification=fields.get("unspsc_code") or None,
                    additional_classifications=(),
                    quantity=fields.get("cantidad") or None,
                    unit=fields.get("unidad") or None,
                    ordinal=ordinal,
                    field_provenance={
                        "description": "Descripcion|NombreProducto",
                        "unspsc_or_classification": "CodigoProducto|CodigoCategoria",
                    },
                )
            )
    return source, tender, lines


def parse_ticket_summary_payload(
    payload: Any,
    *,
    query: AcquisitionQuery | None = None,
    snapshot_id: str = "pending",
    acquired_at_utc: str | None = None,
    original_bytes: bytes | str | None = None,
    http_status: int | None = 200,
) -> tuple[
    AcquisitionQuery,
    AcquisitionPage,
    list[ProcurementSourceObservation],
    list[ProcurementTenderObservation],
    list[ProcurementLineObservation],
    dict[str, Any],
]:
    query = query or build_ticket_summary_query()
    range_pos = {"page": 0}
    if not isinstance(payload, dict):
        page = _malformed_page(
            query=query,
            endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
            payload=payload,
            original_bytes=original_bytes,
            http_status=http_status,
            classification="malformed_response",
            message="payload must be a JSON object",
            range_position=range_pos,
            acquired_at_utc=acquired_at_utc,
        )
        return query, page, [], [], [], {"error": page.error_message}
    if any(str(k).lower() == "ticket" for k in payload.keys()):
        page = _malformed_page(
            query=query,
            endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
            payload=payload,
            original_bytes=original_bytes,
            http_status=http_status,
            classification="malformed_response",
            message="payload must not include ticket",
            range_position=range_pos,
            acquired_at_utc=acquired_at_utc,
        )
        return query, page, [], [], [], {"error": page.error_message}

    malformed_reason, listado, rejected = _strict_summary_listado(payload)
    raw_digest, parser_digest, bytes_digest = payload_digests(
        payload, original_bytes=original_bytes
    )
    if malformed_reason is not None:
        page = AcquisitionPage(
            page_id=acquisition_page_id(
                acquisition_query_id_value=query.acquisition_query_id,
                range_position=range_pos,
                raw_canonical_json_digest=raw_digest,
            ),
            source_kind=SOURCE_KIND_TICKET_API,
            endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
            acquisition_query_id=query.acquisition_query_id,
            range_position=range_pos,
            acquired_at_utc=acquired_at_utc,
            raw_canonical_json_digest=raw_digest,
            original_bytes_digest=bytes_digest,
            parser_input_digest=parser_digest,
            response_item_count=0,
            source_reported_total=None,
            http_status=http_status,
            parser_status="malformed",
            error_classification="malformed_response",
            completeness_status="malformed_response",
            envelope_meta=_envelope_meta(payload),
            error_message=sanitize_error_message(malformed_reason),
        )
        return query, page, [], [], [], {
            "error": page.error_message,
            "rejected_entry_count": 0,
        }

    reported = payload.get("Cantidad")
    reported_int = int(reported) if isinstance(reported, int) else None
    if rejected and listado:
        completeness = "partial_page_failure"
        parser_status = "partial"
        error_classification = "partial_page_failure"
    elif rejected and not listado:
        completeness = "partial_page_failure"
        parser_status = "partial"
        error_classification = "partial_page_failure"
    elif reported_int is not None and reported_int != len(listado):
        completeness = "source_total_mismatch"
        parser_status = "ok"
        error_classification = None
    else:
        completeness = "complete"
        parser_status = "ok"
        error_classification = None

    page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position=range_pos,
            raw_canonical_json_digest=raw_digest,
        ),
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
        acquisition_query_id=query.acquisition_query_id,
        range_position=range_pos,
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=raw_digest,
        original_bytes_digest=bytes_digest,
        parser_input_digest=parser_digest,
        response_item_count=len(listado),
        source_reported_total=reported_int,
        http_status=http_status,
        parser_status=parser_status,
        error_classification=error_classification,
        completeness_status=completeness,
        envelope_meta=_envelope_meta(payload),
        error_message=None,
    )
    sources: list[ProcurementSourceObservation] = []
    tenders: list[ProcurementTenderObservation] = []
    for lic in listado:
        src, tender, _ = _build_observations_from_licitacion(
            lic,
            snapshot_id=snapshot_id,
            page_id=page.page_id,
            endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
            include_lines=False,
        )
        sources.append(src)
        tenders.append(tender)
    diagnostics = {
        "summary_snapshot_complete": completeness == "complete",
        "listado_count": len(listado),
        "rejected_entry_count": len(rejected),
        "rejected_entries": rejected,
        "lines_emitted": 0,
        "note": "summary snapshot does not require detail expansion",
    }
    return query, page, sources, tenders, [], diagnostics


def parse_ticket_detail_payload(
    payload: Any,
    *,
    query: AcquisitionQuery | None = None,
    snapshot_id: str = "pending",
    acquired_at_utc: str | None = None,
    original_bytes: bytes | str | None = None,
    http_status: int | None = 200,
    tender_code: str | None = None,
) -> tuple[
    AcquisitionQuery,
    AcquisitionPage,
    list[ProcurementSourceObservation],
    list[ProcurementTenderObservation],
    list[ProcurementLineObservation],
    dict[str, Any],
]:
    if query is None:
        if not tender_code:
            raise TicketApiParseError("tender_code or query required for detail parse")
        query = build_ticket_detail_query(tender_code=tender_code)

    range_pos = {"page": 0, "codigo": query.tender_code}
    if not isinstance(payload, dict):
        page = _malformed_page(
            query=query,
            endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
            payload=payload,
            original_bytes=original_bytes,
            http_status=http_status,
            classification="malformed_response",
            message="payload must be a JSON object",
            range_position=range_pos,
            acquired_at_utc=acquired_at_utc,
        )
        return query, page, [], [], [], {"error": page.error_message}
    if any(str(k).lower() == "ticket" for k in payload.keys()):
        page = _malformed_page(
            query=query,
            endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
            payload=payload,
            original_bytes=original_bytes,
            http_status=http_status,
            classification="malformed_response",
            message="payload must not include ticket",
            range_position=range_pos,
            acquired_at_utc=acquired_at_utc,
        )
        return query, page, [], [], [], {"error": page.error_message}

    listado = _extract_listado(payload)
    raw_digest, parser_digest, bytes_digest = payload_digests(
        payload, original_bytes=original_bytes
    )
    reported = payload.get("Cantidad")
    reported_int = int(reported) if isinstance(reported, int) else None

    def _fail(classification: str, message: str) -> tuple:
        page = AcquisitionPage(
            page_id=acquisition_page_id(
                acquisition_query_id_value=query.acquisition_query_id,
                range_position=range_pos,
                raw_canonical_json_digest=raw_digest,
            ),
            source_kind=SOURCE_KIND_TICKET_API,
            endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
            acquisition_query_id=query.acquisition_query_id,
            range_position=range_pos,
            acquired_at_utc=acquired_at_utc,
            raw_canonical_json_digest=raw_digest,
            original_bytes_digest=bytes_digest,
            parser_input_digest=parser_digest,
            response_item_count=len(listado),
            source_reported_total=reported_int,
            http_status=http_status,
            parser_status="error",
            error_classification=classification,
            completeness_status=classification,
            envelope_meta=_envelope_meta(payload),
            error_message=sanitize_error_message(message),
        )
        return query, page, [], [], [], {"error": page.error_message}

    if not listado:
        return _fail("detail_empty", "detail Listado empty")
    if len(listado) > 1:
        return _fail(
            "detail_multiple_results",
            f"expected exactly one detail tender, got {len(listado)}",
        )
    licitacion = listado[0]
    codigo = _codigo_externo(licitacion)
    if not codigo:
        return _fail("malformed_response", "detail missing CodigoExterno")
    returned = normalize_mercado_publico_codigo(codigo)
    requested = query.tender_code
    if returned != requested:
        return _fail(
            "detail_code_mismatch",
            "returned CodigoExterno does not match requested query code",
        )
    if reported_int is not None and reported_int != 1:
        return _fail(
            "source_total_mismatch",
            f"Cantidad={reported_int} incoherent with single detail result",
        )

    page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position=range_pos,
            raw_canonical_json_digest=raw_digest,
        ),
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
        acquisition_query_id=query.acquisition_query_id,
        range_position=range_pos,
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=raw_digest,
        original_bytes_digest=bytes_digest,
        parser_input_digest=parser_digest,
        response_item_count=1,
        source_reported_total=reported_int if reported_int is not None else 1,
        http_status=http_status,
        parser_status="ok",
        error_classification=None,
        completeness_status="complete",
        envelope_meta=_envelope_meta(payload),
        error_message=None,
    )
    src, tender, lines = _build_observations_from_licitacion(
        licitacion,
        snapshot_id=snapshot_id,
        page_id=page.page_id,
        endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
        include_lines=True,
    )
    diagnostics = {
        "detail_snapshot_complete": True,
        "line_count": len(lines),
        "requested_code": requested,
        "returned_code": returned,
        "summary_vs_detail": "detail endpoint_kind distinct from summary",
    }
    return query, page, [src], [tender], lines, diagnostics
