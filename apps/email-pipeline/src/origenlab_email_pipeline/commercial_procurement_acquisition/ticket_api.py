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
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
    original_bytes_digest,
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
    procurement_line_observation_id,
    procurement_source_observation_id,
    procurement_tender_observation_id,
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
    """Malformed ticket API envelope (message must never include ticket)."""

    def __init__(self, message: str) -> None:
        super().__init__(sanitize_error_message(message))


def _as_optional_str(value: Any) -> str | None:
    text = _as_str(value)
    return text or None


def build_ticket_summary_query(
    *,
    estado: str | None = "activas",
    fecha_ddmmaaaa: str | None = None,
) -> AcquisitionQuery:
    identity = {
        "source_kind": SOURCE_KIND_TICKET_API,
        "endpoint_kind": ENDPOINT_TICKET_LICITACIONES_SUMMARY,
        "query_contract_version": QUERY_CONTRACT_VERSION,
        "estado": estado,
        "fecha_ddmmaaaa": fecha_ddmmaaaa,
        "tender_code": None,
        "year": None,
        "month": None,
        "range_start": None,
        "range_end": None,
        "endpoint_path": ENDPOINT_PATH_TICKET,
    }
    return AcquisitionQuery(
        acquisition_query_id=acquisition_query_id(identity),
        **identity,  # type: ignore[arg-type]
    )


def build_ticket_detail_query(*, tender_code: str) -> AcquisitionQuery:
    code = (tender_code or "").strip()
    if not code:
        raise TicketApiParseError("tender_code is required for detail query")
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
        **identity,  # type: ignore[arg-type]
    )


def _envelope_meta(payload: dict[str, Any]) -> dict[str, Any]:
    meta = {
        "Cantidad": payload.get("Cantidad"),
        "FechaCreacion": payload.get("FechaCreacion"),
        "Version": payload.get("Version"),
    }
    return sanitize_mapping(meta)


def _canonical_tender_key(codigo: str) -> str:
    return f"ticket:{codigo.strip().casefold()}"


def _line_native_id(item: dict[str, Any], ordinal: int) -> str | None:
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


def _validate_envelope(payload: Any) -> dict[str, Any]:
    if payload is None:
        raise TicketApiParseError("payload is null")
    if not isinstance(payload, dict):
        raise TicketApiParseError("payload must be a JSON object")
    # Reject credential-bearing keys early.
    if any(str(k).lower() == "ticket" for k in payload.keys()):
        raise TicketApiParseError("payload must not include ticket")
    return payload


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
    status_code, status_name = _extract_status_fields(licitacion)
    buyer = _buyer_name(licitacion) or None
    buyer_id = None
    comprador = licitacion.get("Comprador")
    if isinstance(comprador, dict):
        buyer_id = _as_optional_str(
            comprador.get("CodigoOrganismo") or comprador.get("RutUnidad")
        )
    pub = _as_optional_str(
        licitacion.get("FechaPublicacion") or licitacion.get("FechaCreacion")
    )
    close = _extract_close_date(licitacion) or None
    raw_digest = canonical_json_digest(licitacion)
    source_native = codigo
    obs_id = procurement_source_observation_id(
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=endpoint_kind,
        source_native_key=source_native,
        package_id=None,
        release_id=None,
        raw_payload_digest=raw_digest,
    )
    source = ProcurementSourceObservation(
        observation_id=obs_id,
        snapshot_id=snapshot_id,
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=endpoint_kind,
        source_native_key=source_native,
        canonical_tender_key_candidate=_canonical_tender_key(codigo),
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
        raw_payload_digest=raw_digest,
        parser_version=PARSER_VERSION,
        provenance_reason_codes=("ticket_api_listado_item",),
        page_id=page_id,
    )
    tender_key = _canonical_tender_key(codigo)
    tender_id = procurement_tender_observation_id(
        source_observation_id=obs_id, normalized_tender_key=tender_key
    )
    tender = ProcurementTenderObservation(
        tender_observation_id=tender_id,
        source_observation_id=obs_id,
        source_kind=SOURCE_KIND_TICKET_API,
        normalized_tender_key=tender_key,
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
        field_provenance={
            "normalized_tender_key": "CodigoExterno",
            "title": "Nombre|Titulo",
            "close_timestamp_raw": "FechaCierre|Fechas.FechaCierre",
            "buyer_display": "Comprador.NombreOrganismo",
        },
    )
    lines: list[ProcurementLineObservation] = []
    if include_lines:
        items = _sort_items(_extract_items(licitacion))
        # Occurrence counter for duplicate native IDs.
        seen: dict[str, int] = {}
        for ordinal, item in enumerate(items):
            fields = _normalize_item_fields(item)
            native = _line_native_id(item, ordinal)
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
    """Parse a ticket API summary (estado=activas) response. Captures all tenders."""
    query = query or build_ticket_summary_query()
    try:
        data = _validate_envelope(payload)
        listado = _extract_listado(data)
    except TicketApiParseError as exc:
        digest = canonical_json_digest({"error": str(exc)})
        page = AcquisitionPage(
            page_id=acquisition_page_id(
                acquisition_query_id_value=query.acquisition_query_id,
                range_position={"page": 0},
                raw_canonical_json_digest=digest,
            ),
            source_kind=SOURCE_KIND_TICKET_API,
            endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
            acquisition_query_id=query.acquisition_query_id,
            range_position={"page": 0},
            acquired_at_utc=acquired_at_utc,
            raw_canonical_json_digest=digest,
            original_bytes_digest=original_bytes_digest(original_bytes),
            parser_input_digest=digest,
            response_item_count=0,
            source_reported_total=None,
            http_status=http_status,
            parser_status="malformed",
            error_classification="malformed_response",
            completeness_status="malformed_response",
            envelope_meta={},
        )
        return query, page, [], [], [], {"error": str(exc)}

    parser_digest = canonical_json_digest(data)
    reported = data.get("Cantidad")
    reported_int = int(reported) if isinstance(reported, int) else None
    completeness = "complete"
    if reported_int is not None and reported_int != len(listado):
        completeness = "source_total_mismatch"

    page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position={"page": 0},
            raw_canonical_json_digest=parser_digest,
        ),
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=ENDPOINT_TICKET_LICITACIONES_SUMMARY,
        acquisition_query_id=query.acquisition_query_id,
        range_position={"page": 0},
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=parser_digest,
        original_bytes_digest=original_bytes_digest(original_bytes),
        parser_input_digest=parser_digest,
        response_item_count=len(listado),
        source_reported_total=reported_int,
        http_status=http_status,
        parser_status="ok",
        error_classification=None,
        completeness_status=completeness,
        envelope_meta=_envelope_meta(data),
    )

    sources: list[ProcurementSourceObservation] = []
    tenders: list[ProcurementTenderObservation] = []
    # Summary snapshots do not expand lines.
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
    """Parse a ticket API code-detail response into tender + line observations."""
    if query is None:
        if not tender_code:
            # Attempt to discover from payload after validation.
            query = None  # set below
        else:
            query = build_ticket_detail_query(tender_code=tender_code)

    try:
        data = _validate_envelope(payload)
        listado = _extract_listado(data)
        if not listado:
            raise TicketApiParseError("detail Listado empty")
        licitacion = listado[0]
        codigo = _codigo_externo(licitacion)
        if query is None:
            query = build_ticket_detail_query(tender_code=codigo)
    except TicketApiParseError as exc:
        q = query or build_ticket_detail_query(tender_code=tender_code or "UNKNOWN")
        digest = canonical_json_digest({"error": str(exc)})
        page = AcquisitionPage(
            page_id=acquisition_page_id(
                acquisition_query_id_value=q.acquisition_query_id,
                range_position={"page": 0, "codigo": q.tender_code},
                raw_canonical_json_digest=digest,
            ),
            source_kind=SOURCE_KIND_TICKET_API,
            endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
            acquisition_query_id=q.acquisition_query_id,
            range_position={"page": 0, "codigo": q.tender_code},
            acquired_at_utc=acquired_at_utc,
            raw_canonical_json_digest=digest,
            original_bytes_digest=original_bytes_digest(original_bytes),
            parser_input_digest=digest,
            response_item_count=0,
            source_reported_total=None,
            http_status=http_status,
            parser_status="malformed",
            error_classification="malformed_response",
            completeness_status="malformed_response",
            envelope_meta={},
        )
        return q, page, [], [], [], {"error": str(exc)}

    assert query is not None
    parser_digest = canonical_json_digest(data)
    page = AcquisitionPage(
        page_id=acquisition_page_id(
            acquisition_query_id_value=query.acquisition_query_id,
            range_position={"page": 0, "codigo": query.tender_code},
            raw_canonical_json_digest=parser_digest,
        ),
        source_kind=SOURCE_KIND_TICKET_API,
        endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
        acquisition_query_id=query.acquisition_query_id,
        range_position={"page": 0, "codigo": query.tender_code},
        acquired_at_utc=acquired_at_utc,
        raw_canonical_json_digest=parser_digest,
        original_bytes_digest=original_bytes_digest(original_bytes),
        parser_input_digest=parser_digest,
        response_item_count=1,
        source_reported_total=data.get("Cantidad")
        if isinstance(data.get("Cantidad"), int)
        else 1,
        http_status=http_status,
        parser_status="ok",
        error_classification=None,
        completeness_status="complete",
        envelope_meta=_envelope_meta(data),
    )
    src, tender, lines = _build_observations_from_licitacion(
        listado[0],
        snapshot_id=snapshot_id,
        page_id=page.page_id,
        endpoint_kind=ENDPOINT_TICKET_LICITACION_DETAIL,
        include_lines=True,
    )
    diagnostics = {
        "detail_snapshot_complete": True,
        "line_count": len(lines),
        "summary_vs_detail": "detail endpoint_kind distinct from summary",
    }
    return query, page, [src], [tender], lines, diagnostics
