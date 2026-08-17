"""Tests for Mercado Público licitaciones API client (mocked HTTP only)."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

from origenlab_email_pipeline.chilecompra_api import (
    CHILECOMPRA_NORMALIZED_FIELDS,
    ChileCompraHttpError,
    ChileCompraJsonError,
    ChileCompraPortalError,
    ChileCompraTicketMissingError,
    VALIDITY_STATUS_CLOSES_TODAY,
    VALIDITY_STATUS_EXPIRED,
    VALIDITY_STATUS_MISSING_CLOSE_DATE,
    VALIDITY_STATUS_NOT_PUBLICADA,
    VALIDITY_STATUS_OPEN,
    _ValidatedPortalRedirectHandler,
    build_attachment_postback_body,
    build_licitacion_detail_url,
    build_licitaciones_url,
    classify_chilecompra_validity_status,
    download_portal_attachment,
    extract_aspnet_form_fields,
    extract_attachment_listing_urls,
    extract_licitacion_anexos,
    fetch_licitacion_attachments,
    fetch_licitacion_by_codigo,
    fetch_licitaciones,
    has_gated_attachment_listing_hint,
    is_active_chilecompra_licitacion,
    is_portal_detail_url,
    is_portal_listing_url,
    is_portal_url,
    is_safe_public_attachment_url,
    list_licitacion_attachments,
    new_portal_opener,
    normalize_licitacion_detail_items,
    normalize_licitacion_summary,
    normalize_licitaciones_response,
    parse_attachment_listing,
    redact_ticket,
    redact_ticket_in_url,
    safe_attachment_filename,
    sanitize_portal_url_for_error,
    save_licitacion_attachments,
    ticket_from_env,
    validate_fecha,
)

_SECRET_TICKET = "00000000-0000-0000-0000-000000000099"


def _mock_response(payload: dict[str, Any] | str, *, status: int = 200) -> MagicMock:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    if isinstance(payload, str):
        body = payload.encode("utf-8")
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status = status
    response.getcode.return_value = status
    response.read.return_value = body
    return response


def test_build_licitaciones_url_includes_ticket_but_log_helper_redacts() -> None:
    url = build_licitaciones_url(ticket=_SECRET_TICKET, fecha="14062026", estado="activas")
    assert f"ticket={_SECRET_TICKET}" in url
    assert "fecha=14062026" in url
    assert "estado=activas" in url
    safe = redact_ticket_in_url(url)
    assert _SECRET_TICKET not in safe
    assert "ticket=<redacted>" in safe


def test_ticket_from_env_missing_raises_clear_error() -> None:
    with pytest.raises(ChileCompraTicketMissingError, match="CHILECOMPRA_API_TICKET"):
        ticket_from_env({})


def test_ticket_from_env_reads_configured_value() -> None:
    assert ticket_from_env({"CHILECOMPRA_API_TICKET": _SECRET_TICKET}) == _SECRET_TICKET


def test_validate_fecha_requires_ddmmaaaa() -> None:
    assert validate_fecha("14062026") == "14062026"
    with pytest.raises(ValueError, match="ddmmaaaa"):
        validate_fecha("2026-06-14")


def test_normalize_summary_from_listado_payload() -> None:
    payload = {
        "Listado": [
            {
                "CodigoExterno": "1051-1-LP26",
                "Tipo": "LP",
                "Nombre": "Adquisición centrifuga laboratorio",
                "Descripcion": "Equipo para laboratorio clínico",
                "Comprador": {"NombreOrganismo": "Hospital Demo", "Region": "Región Metropolitana"},
                "FechaPublicacion": "01/06/2026 10:00:00",
                "FechaCierre": "15/06/2026 15:00:00",
            }
        ]
    }
    rows = normalize_licitaciones_response(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row["codigo"] == "1051-1-LP26"
    assert row["tipo_licitacion"] == "LP"
    assert row["title"] == "Adquisición centrifuga laboratorio"
    assert row["descripcion"] == "Equipo para laboratorio clínico"
    assert row["buyer"] == "Hospital Demo"
    assert row["region"] == "Región Metropolitana"
    assert row["fecha_publicacion"] == "01/06/2026 10:00:00"
    assert row["close_date"] == "15/06/2026 15:00:00"
    assert row["chilecompra_status_code"] == ""
    assert row["chilecompra_status"] == ""
    assert set(row) == set(CHILECOMPRA_NORMALIZED_FIELDS)


def test_missing_listado_returns_empty_list() -> None:
    assert normalize_licitaciones_response({}) == []
    assert normalize_licitaciones_response({"Listado": None}) == []


def test_normalize_detail_items_from_items_listado() -> None:
    licitacion = {
        "CodigoExterno": "2277-2-LR25",
        "Nombre": "Reactivos y equipos",
        "Descripcion": "Compra anual laboratorio",
        "OrganismoComprador": {"NombreOrganismo": "Universidad Demo", "Region": "Biobío"},
        "Items": {
            "Listado": [
                {
                    "Descripcion": "Centrifuga refrigerada 4000 rpm",
                    "CodigoProducto": "41105301",
                    "UnidadMedida": "Unidad",
                    "Cantidad": "2",
                    "NombreProducto": "Centrifuga",
                    "Categoria": "Equipos de laboratorio",
                },
                {
                    "Descripcion": "Balanza analítica 0.1 mg",
                    "CodigoProducto": "41111503",
                    "UnidadMedida": "Unidad",
                    "Cantidad": "1",
                    "NombreProducto": "Balanza",
                    "Nivel2": "Instrumentos",
                    "Nivel3": "Balanza",
                },
            ]
        },
    }
    rows = normalize_licitacion_detail_items(licitacion)
    assert len(rows) == 2
    assert rows[0]["codigo"] == "2277-2-LR25"
    assert rows[0]["buyer"] == "Universidad Demo"
    assert rows[0]["line_description"] == "Centrifuga refrigerada 4000 rpm"
    assert rows[0]["unspsc_code"] == "41105301"
    assert rows[0]["cantidad"] == "2"
    assert rows[0]["producto"] == "Centrifuga"
    assert rows[0]["nivel_1"] == "Equipos de laboratorio"
    assert rows[1]["line_description"] == "Balanza analítica 0.1 mg"
    assert rows[1]["nivel_2"] == "Instrumentos"
    assert rows[1]["nivel_3"] == "Balanza"


def test_normalize_detail_items_single_item_object() -> None:
    licitacion = {
        "CodigoExterno": "1000-1-LP26",
        "Nombre": "Sonicador",
        "Items": {
            "Descripcion": "Procesador ultrasónico",
            "CodigoProducto": "41105312",
            "Cantidad": "1",
            "UnidadMedida": "Unidad",
        },
    }
    rows = normalize_licitacion_detail_items(licitacion)
    assert len(rows) == 1
    assert rows[0]["line_description"] == "Procesador ultrasónico"
    assert rows[0]["unspsc_code"] == "41105312"


def test_normalize_detail_without_items_returns_summary_only() -> None:
    summary = normalize_licitacion_summary(
        {"CodigoExterno": "1000-2-LP26", "Nombre": "Solo encabezado"}
    )
    rows = normalize_licitacion_detail_items(
        {"CodigoExterno": "1000-2-LP26", "Nombre": "Solo encabezado"}
    )
    assert rows == [summary]
    assert rows[0]["line_description"] == ""


def test_fetch_licitaciones_uses_mocked_http() -> None:
    payload = {"Listado": [{"CodigoExterno": "1-1-LP26", "Nombre": "Demo"}]}
    mock_urlopen = MagicMock(return_value=_mock_response(payload))

    result = fetch_licitaciones(
        ticket=_SECRET_TICKET,
        estado="activas",
        urlopen_fn=mock_urlopen,
    )

    assert result == payload
    called_url = mock_urlopen.call_args[0][0].full_url
    assert "estado=activas" in called_url
    assert f"ticket={_SECRET_TICKET}" in called_url


def test_fetch_licitacion_by_codigo_uses_codigo_param() -> None:
    payload = {"Listado": [{"CodigoExterno": "1051-1-LP26", "Nombre": "Detalle"}]}
    mock_urlopen = MagicMock(return_value=_mock_response(payload))

    result = fetch_licitacion_by_codigo(
        "1051-1-LP26",
        ticket=_SECRET_TICKET,
        urlopen_fn=mock_urlopen,
    )

    assert result == payload
    called_url = mock_urlopen.call_args[0][0].full_url
    assert "codigo=1051-1-LP26" in called_url


def test_fetch_http_error_redacts_ticket_from_message() -> None:
    def _raise_http_error(*_args: object, **_kwargs: object) -> None:
        url = build_licitaciones_url(ticket=_SECRET_TICKET, estado="activas")
        raise HTTPError(url, 403, "Forbidden", hdrs=None, fp=io.BytesIO(b"denied"))

    with pytest.raises(ChileCompraHttpError) as exc:
        fetch_licitaciones(ticket=_SECRET_TICKET, urlopen_fn=_raise_http_error)

    message = str(exc.value)
    assert _SECRET_TICKET not in message
    assert "HTTP 403" in message
    assert "<redacted>" in message


def test_fetch_invalid_json_raises_clear_error_without_ticket() -> None:
    mock_urlopen = MagicMock(return_value=_mock_response("not-json"))

    with pytest.raises(ChileCompraJsonError) as exc:
        fetch_licitaciones(ticket=_SECRET_TICKET, urlopen_fn=mock_urlopen)

    assert _SECRET_TICKET not in str(exc.value)


def test_redact_ticket_removes_secret_from_free_form_text() -> None:
    text = f"failed for ticket {_SECRET_TICKET} on retry"
    redacted = redact_ticket(text, _SECRET_TICKET)
    assert _SECRET_TICKET not in redacted
    assert "<redacted>" in redacted


def test_normalize_summary_preserves_top_level_fecha_cierre_and_status() -> None:
    row = normalize_licitacion_summary(
        {
            "CodigoExterno": "1051-1-LP26",
            "Nombre": "Centrifuga",
            "FechaCierre": "20/06/2026 17:00:00",
            "CodigoEstado": "5",
            "Estado": "Publicada",
        }
    )
    assert row["close_date"] == "20/06/2026 17:00:00"
    assert row["chilecompra_status_code"] == "5"
    assert row["chilecompra_status"] == "Publicada"


def test_normalize_summary_reads_nested_fechas_fecha_cierre() -> None:
    row = normalize_licitacion_summary(
        {
            "CodigoExterno": "2000-1-LP26",
            "Nombre": "Balanza",
            "Fechas": {"FechaCierre": "21/06/2026 18:00:00"},
            "CodigoEstado": "5",
            "Estado": "Publicada",
        }
    )
    assert row["close_date"] == "21/06/2026 18:00:00"


def test_normalize_detail_items_keep_summary_close_date_when_detail_missing() -> None:
    summary_fallback = {
        "close_date": "20/06/2026 17:00:00",
        "chilecompra_status_code": "5",
        "chilecompra_status": "Publicada",
    }
    rows = normalize_licitacion_detail_items(
        {
            "CodigoExterno": "1051-1-LP26",
            "Nombre": "Centrifuga",
            "Items": {
                "Descripcion": "Centrifuga refrigerada",
                "CodigoProducto": "41105301",
            },
        },
        summary_fallback=summary_fallback,
    )
    assert rows[0]["close_date"] == "20/06/2026 17:00:00"
    assert rows[0]["chilecompra_status_code"] == "5"


def test_normalize_detail_items_reads_nested_fechas_fecha_cierre() -> None:
    rows = normalize_licitacion_detail_items(
        {
            "CodigoExterno": "3000-1-LP26",
            "Nombre": "Sonicador",
            "Fechas": {"FechaCierre": "22/06/2026 12:00:00"},
            "CodigoEstado": "5",
            "Estado": "Publicada",
            "Items": {"Descripcion": "Procesador ultrasónico"},
        }
    )
    assert rows[0]["close_date"] == "22/06/2026 12:00:00"


def test_codigo_estado_5_maps_to_open_validity() -> None:
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
        close_date="20/06/2026 17:00:00",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_OPEN
    assert is_active_chilecompra_licitacion(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
    )


@pytest.mark.parametrize(
    ("status_code", "status_name"),
    [
        ("6", "Cerrada"),
        ("7", "Desierta"),
        ("8", "Adjudicada"),
        ("18", "Revocada"),
        ("19", "Suspendida"),
    ],
)
def test_inactive_status_codes_are_not_active(status_code: str, status_name: str) -> None:
    assert is_active_chilecompra_licitacion(
        chilecompra_status_code=status_code,
        chilecompra_status=status_name,
    ) is False
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code=status_code,
        chilecompra_status=status_name,
        close_date="20/06/2026 17:00:00",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_NOT_PUBLICADA


def test_expired_close_date_maps_to_expired_validity() -> None:
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
        close_date="10/06/2026 17:00:00",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_EXPIRED


def test_missing_close_date_maps_to_missing_validity() -> None:
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
        close_date="",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_MISSING_CLOSE_DATE


def test_iso_future_close_date_maps_to_open_validity() -> None:
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
        close_date="2026-06-17T19:00:00",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_OPEN


def test_iso_same_day_close_date_maps_to_closes_today_validity() -> None:
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
        close_date="2026-06-14T19:00:00",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_CLOSES_TODAY


def test_iso_past_close_date_maps_to_expired_validity() -> None:
    validity = classify_chilecompra_validity_status(
        chilecompra_status_code="5",
        chilecompra_status="Publicada",
        close_date="2026-06-10T19:00:00",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )
    assert validity == VALIDITY_STATUS_EXPIRED


def test_is_safe_public_attachment_url_rejects_ticket_and_api_hosts() -> None:
    assert is_safe_public_attachment_url(
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/Details.aspx"
    )
    assert not is_safe_public_attachment_url(
        "https://api.mercadopublico.cl/servicios/v1/publico/ordencompra.json?ticket=SECRET"
    )
    assert not is_safe_public_attachment_url("https://api.chilecompra.cl/archivo.pdf")
    assert not is_safe_public_attachment_url("ftp://files.example/base.pdf")


def test_extract_licitacion_anexos_normalizes_safe_fields_only() -> None:
    licitacion = {
        "Anexos": {
            "Listado": [
                {
                    "Nombre": "Bases técnicas.pdf",
                    "Tipo": "Bases",
                    "Descripcion": "Especificaciones del equipo",
                    "Tamano": "1.2 MB",
                    "Fecha": "01/06/2026",
                    "Url": "https://www.mercadopublico.cl/archivos/bases.pdf",
                },
                {
                    "Nombre": "Anexo ticket",
                    "Url": "https://api.mercadopublico.cl/v1/doc?ticket=SECRET",
                },
            ]
        }
    }
    anexos = extract_licitacion_anexos(licitacion)
    assert len(anexos) == 2
    assert anexos[0]["nombre"] == "Bases técnicas.pdf"
    assert anexos[0]["tipo"] == "Bases"
    assert anexos[0]["descripcion"] == "Especificaciones del equipo"
    assert anexos[0]["tamano"] == "1.2 MB"
    assert anexos[0]["fecha_adjunto"] == "01/06/2026"
    assert "mercadopublico.cl" in anexos[0]["url"]
    assert anexos[1]["nombre"] == "Anexo ticket"
    assert anexos[1]["url"] == ""


def test_extract_licitacion_anexos_empty_when_payload_has_no_attachment_keys() -> None:
    assert extract_licitacion_anexos({"CodigoExterno": "1702-20-L126", "Items": {"Listado": []}}) == []


# --- Public portal anexo downloads ------------------------------------------

_DETAIL_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
    "DetailsAcquisition.aspx?qs=6kaRy83zbKUYD9NMoSijTg=="
)
_LISTING_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/Attachment/VerAntecedentes.aspx?enc=AAA%2fBBB"
)
_DETAIL_HTML = """
<html><body>
  <a href="../Attachment/VerAntecedentes.aspx?enc=AAA%2fBBB"><img src="../../Includes/images/Ficha/adjunto.gif" /></a>
  <a href="../Attachment/VerAntecedentes.aspx?enc=AAA%2fBBB">Ver anexos</a>
  <a href="https://evil.example.com/Attachment/VerAntecedentes.aspx?enc=CCC">off-host</a>
</body></html>
"""
_LISTING_HTML = """
<html><body><form>
<input type="hidden" name="__VIEWSTATE" value="/wEPDwUxMjM=" />
<input type="hidden" name="__EVENTVALIDATION" value="/wEdAAQ=" />
<input type="hidden" name="__EVENTTARGET" value="" />
<table id="grdAttachment">
  <tr class="cssFwkHeaderTableRow">
    <th scope="col">Anexo</th><th scope="col">Tipo</th><th scope="col">Descripci&oacute;n</th>
    <th scope="col">Fecha</th><th scope="col">Acciones</th>
  </tr>
  <tr class="cssFwkItemStyle ajustar">
    <td><span id="grdAttachment_ctl02_grdLblSourceFileName">BASES ADMINISTRATIVAS.pdf</span></td>
    <td>Anexos Administrativos de Adquisici&oacute;n</td>
    <td><span id="grdAttachment_ctl02_grdLblFileDescription">Anexo Administrativo</span></td>
    <td><span id="grdAttachment_ctl02_grdLblFileDate">02-06-2026 15:36:39</span></td>
    <td><input type="image" name="grdAttachment$ctl02$grdIbtnView" id="grdAttachment_ctl02_grdIbtnView" /></td>
  </tr>
  <tr class="cssFwkItemStyle ajustar">
    <td><span id="grdAttachment_ctl03_grdLblSourceFileName">ANEXO N&deg;2 REQUERIMIENTOS.pdf</span></td>
    <td>Anexos T&eacute;cnicos</td>
    <td><span id="grdAttachment_ctl03_grdLblFileDescription">Anexo T&eacute;cnico</span></td>
    <td><span id="grdAttachment_ctl03_grdLblFileDate">02-06-2026 15:40:00</span></td>
    <td><input type="image" name="grdAttachment$ctl03$grdIbtnView" id="grdAttachment_ctl03_grdIbtnView" /></td>
  </tr>
</table>
</form></body></html>
"""
_PDF_BYTES = b"%PDF-1.7\nfake attachment body\n%%EOF"

# --- Real-regression-shaped fixtures: a ficha can advertise a normal
# VerAntecedentes.aspx href listing *and* a separate, CAPTCHA-gated popup
# listing (the ``imgAdjuntos`` onclick below), independently of one another.
# This mirrors sanitized markup captured from a real ficha (tender
# 4291-46-LE26): the popup's ``open(...)`` call quotes its JS string
# arguments with HTML entities (``&#39;``), not literal apostrophes.
_GATED_ATTACHMENT_ONCLICK_SNIPPET = (
    '<input type="image" name="imgAdjuntos" id="imgAdjuntos" '
    'src="../../Includes/images/FichaLight/iconos_licitacion/ic-21.png" '
    "onclick=\"open(&#39;../Attachment/ViewAttachment.aspx?enc=GATEDTOKEN123&#39;,"
    "&#39;MercadoPublico&#39;, &#39;width=850, height=700, status=yes, "
    "scrollbars=yes, left=0, top=0, resizable=yes&#39;);"
    'window.event.returnValue=false;" border="0" style="width: 43; height: 60;" />'
)

_GATED_DETAIL_HTML = f"""
<html><body>
  <a href="../Attachment/VerAntecedentes.aspx?enc=AAA%2fBBB">Ver anexos</a>
  {_GATED_ATTACHMENT_ONCLICK_SNIPPET}
</body></html>
"""

_GATED_LISTING_HTML = """
<html><body><form>
<input type="hidden" name="__VIEWSTATE" value="/wEPDwUxMjM=" />
<input type="hidden" name="__EVENTVALIDATION" value="/wEdAAQ=" />
<table id="grdAttachment">
  <tr class="cssFwkHeaderTableRow">
    <th scope="col">Anexo</th><th scope="col">Tipo</th><th scope="col">Descripci&oacute;n</th>
    <th scope="col">Fecha</th><th scope="col">Acciones</th>
  </tr>
  <tr class="cssFwkItemStyle ajustar">
    <td><span id="grdAttachment_ctl02_grdLblSourceFileName">FORMULARIO_OBLIGATORIO_1234.docx</span></td>
    <td>Anexos Administrativos de Adquisici&oacute;n</td>
    <td><span id="grdAttachment_ctl02_grdLblFileDescription">Formulario</span></td>
    <td><span id="grdAttachment_ctl02_grdLblFileDate">02-06-2026 15:36:39</span></td>
    <td><input type="image" name="grdAttachment$ctl02$grdIbtnView" id="grdAttachment_ctl02_grdIbtnView" /></td>
  </tr>
</table>
</form></body></html>
"""


def _portal_response(body: bytes, *, headers: dict[str, str] | None = None, url: str | None = None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status = 200
    response.getcode.return_value = 200
    response.read.side_effect = lambda *args: body
    response.headers = headers or {}
    response.url = url
    return response


def _fake_portal_opener(*, pdf_headers: dict[str, str] | None = None):
    """Serve the ficha page, the listing page, and PDF bytes for each postback."""
    calls: list[tuple[str, str]] = []

    def opener(request, timeout=None):
        url = request.full_url
        calls.append((request.get_method(), url))
        if request.data is not None:
            headers = {"Content-Type": "application/pdf"}
            headers.update(pdf_headers or {})
            return _portal_response(_PDF_BYTES, headers=headers, url=url)
        if "DetailsAcquisition" in url:
            return _portal_response(_DETAIL_HTML.encode("utf-8"), url=url)
        return _portal_response(_LISTING_HTML.encode("utf-8"), url=url)

    opener.calls = calls
    return opener


def test_build_licitacion_detail_url_encodes_qs_token() -> None:
    url = build_licitacion_detail_url("6kaRy83zbKUYD9NMoSijTg==")
    assert url.startswith("https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?")
    assert "qs=6kaRy83zbKUYD9NMoSijTg%3D%3D" in url
    with pytest.raises(ValueError, match="qs is required"):
        build_licitacion_detail_url("  ")


def test_is_portal_url_rejects_other_hosts_and_schemes() -> None:
    assert is_portal_url(_LISTING_URL)
    assert not is_portal_url("http://www.mercadopublico.cl/Procurement/Modules/x.aspx")
    assert not is_portal_url("https://evil.example.com/Procurement/Modules/x.aspx")
    assert not is_portal_url("https://mercadopublico.cl.evil.example.com/x.aspx")
    assert not is_portal_url("https://user:pass@www.mercadopublico.cl/Procurement/Modules/x.aspx")
    assert not is_portal_url("")


def test_portal_endpoint_helpers_require_expected_paths_and_query() -> None:
    assert is_portal_detail_url(_DETAIL_URL)
    assert is_portal_listing_url(_LISTING_URL)
    assert not is_portal_detail_url(
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx"
    )
    assert not is_portal_listing_url(
        "https://www.mercadopublico.cl/Procurement/Modules/Attachment/VerAntecedentes.aspx"
    )
    assert not is_portal_detail_url(_LISTING_URL)
    assert not is_portal_listing_url(_DETAIL_URL)
    assert not is_portal_detail_url(
        "https://www.mercadopublico.cl/Procurement/Modules/Other/Page.aspx?qs=abc"
    )


def test_sanitize_portal_url_for_error_redacts_opaque_tokens() -> None:
    redacted = sanitize_portal_url_for_error(_DETAIL_URL)
    assert "6kaRy83zbKUYD9NMoSijTg" not in redacted
    assert "qs=%3Credacted%3E" in redacted or "qs=<redacted>" in redacted
    listing = sanitize_portal_url_for_error(_LISTING_URL)
    assert "AAA" not in listing
    assert "enc=" in listing


def test_extract_attachment_listing_urls_dedupes_and_drops_off_host_links() -> None:
    urls = extract_attachment_listing_urls(_DETAIL_HTML)
    assert urls == [_LISTING_URL]


# --- Gated (CAPTCHA-popup) attachment-listing hint ---------------------------


def test_has_gated_attachment_listing_hint_is_false_on_a_normal_ficha() -> None:
    # Fixture A: no onclick popup hint anywhere on the page.
    assert has_gated_attachment_listing_hint(_DETAIL_HTML) is False


def test_has_gated_attachment_listing_hint_detects_entity_quoted_onclick() -> None:
    # Fixture B (Antofagasta-shaped): real markup HTML-entity-encodes the JS
    # string quotes inside the onclick handler (&#39; not a literal ').
    assert has_gated_attachment_listing_hint(_GATED_DETAIL_HTML) is True


def test_has_gated_attachment_listing_hint_also_detects_literal_quotes() -> None:
    literal = (
        '<input onclick="open(\'../Attachment/ViewAttachment.aspx?enc=abc\', '
        "'MercadoPublico', 'width=850');\" />"
    )
    assert has_gated_attachment_listing_hint(literal) is True


def test_gated_hint_and_normal_href_listing_are_independent_signals() -> None:
    """The onclick hint must never be conflated with, or crowd out, the
    ordinary href-based VerAntecedentes.aspx listing (requirement: normal
    listing behavior is unaffected by the presence of the gated hint)."""
    assert has_gated_attachment_listing_hint(_GATED_DETAIL_HTML) is True
    urls = extract_attachment_listing_urls(_GATED_DETAIL_HTML)
    assert urls == [_LISTING_URL]
    # And the inverse: a ficha with only the normal listing never reports a
    # gated hint that isn't actually there.
    assert has_gated_attachment_listing_hint(_DETAIL_HTML) is False
    assert extract_attachment_listing_urls(_DETAIL_HTML) == [_LISTING_URL]


def test_list_licitacion_attachments_surfaces_gated_listing_hint() -> None:
    """Antofagasta-shaped ficha: the one reachable DOCX row is still fully
    enumerated, and the inventory additionally reports the gated hint."""

    def opener(request, timeout=None):
        url = request.full_url
        if "DetailsAcquisition" in url:
            return _portal_response(_GATED_DETAIL_HTML.encode("utf-8"), url=url)
        return _portal_response(_GATED_LISTING_HTML.encode("utf-8"), url=url)

    inventory = list_licitacion_attachments(_DETAIL_URL, opener=opener)
    assert inventory.attachments_discovered == 1
    assert inventory.attachments[0].nombre == "FORMULARIO_OBLIGATORIO_1234.docx"
    assert inventory.gated_listing_hint is True


def test_list_licitacion_attachments_no_gated_hint_on_normal_ficha() -> None:
    opener = _fake_portal_opener()
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=opener)
    assert inventory.attachments_discovered == 2
    assert inventory.gated_listing_hint is False


def test_parse_attachment_listing_reads_every_row() -> None:
    rows = parse_attachment_listing(_LISTING_HTML, listing_url=_LISTING_URL)
    assert [row.nombre for row in rows] == [
        "BASES ADMINISTRATIVAS.pdf",
        "ANEXO N°2 REQUERIMIENTOS.pdf",
    ]
    assert rows[0].tipo == "Anexos Administrativos de Adquisición"
    assert rows[0].descripcion == "Anexo Administrativo"
    assert rows[0].fecha_adjunto == "02-06-2026 15:36:39"
    assert rows[0].control_name == "grdAttachment$ctl02$grdIbtnView"
    assert rows[1].control_name == "grdAttachment$ctl03$grdIbtnView"
    assert rows[0].listing_url == _LISTING_URL


def test_extract_aspnet_form_fields_keeps_viewstate_and_empty_values() -> None:
    fields = extract_aspnet_form_fields(_LISTING_HTML)
    assert fields["__VIEWSTATE"] == "/wEPDwUxMjM="
    assert fields["__EVENTVALIDATION"] == "/wEdAAQ="
    assert fields["__EVENTTARGET"] == ""


def test_build_attachment_postback_body_adds_image_button_coordinates() -> None:
    body = build_attachment_postback_body(
        {"__VIEWSTATE": "abc"}, "grdAttachment$ctl02$grdIbtnView"
    ).decode("utf-8")
    assert "__VIEWSTATE=abc" in body
    assert "grdAttachment%24ctl02%24grdIbtnView.x=10" in body
    assert "grdAttachment%24ctl02%24grdIbtnView.y=10" in body
    with pytest.raises(ValueError, match="control_name is required"):
        build_attachment_postback_body({}, "")


def test_safe_attachment_filename_strips_paths_and_control_characters() -> None:
    assert safe_attachment_filename("BASES ADMINISTRATIVAS.pdf") == "BASES ADMINISTRATIVAS.pdf"
    assert safe_attachment_filename("../../etc/passwd") == "passwd"
    assert safe_attachment_filename("..\\..\\windows\\system32\\evil.dll") == "evil.dll"
    assert safe_attachment_filename("ANEXO%20N%C2%B01.pdf") == "ANEXO N°1.pdf"
    assert safe_attachment_filename("bad\x00name\n.pdf") == "bad_name_.pdf"
    assert safe_attachment_filename("   ") == "anexo.bin"
    assert safe_attachment_filename("CON.pdf") == "_CON.pdf"
    assert safe_attachment_filename("nul") == "_nul"
    long_name = safe_attachment_filename("a" * 400 + ".pdf")
    assert len(long_name) <= 180
    assert long_name.endswith(".pdf")


def test_fetch_licitacion_attachments_downloads_every_row_over_one_session() -> None:
    opener = _fake_portal_opener()
    downloads = fetch_licitacion_attachments(_DETAIL_URL, opener=opener)

    assert [d.filename for d in downloads] == [
        "BASES ADMINISTRATIVAS.pdf",
        "ANEXO N°2 REQUERIMIENTOS.pdf",
    ]
    assert all(d.is_pdf and d.content == _PDF_BYTES for d in downloads)
    assert downloads[0].content_type == "application/pdf"
    assert downloads[0].attachment.tipo == "Anexos Administrativos de Adquisición"
    # One GET for the ficha, one GET for the listing, one POST per anexo row.
    assert [method for method, _ in opener.calls] == ["GET", "GET", "POST", "POST"]


def test_fetch_licitacion_attachments_prefers_listing_name_over_header() -> None:
    """The portal mangles accents/spaces in Content-Disposition; the page markup does not."""
    opener = _fake_portal_opener(
        pdf_headers={"Content-Disposition": "attachment; filename=BASES_ADMINISTRATIVAS.pdf"}
    )
    downloads = fetch_licitacion_attachments(_DETAIL_URL, opener=opener)
    assert downloads[0].filename == "BASES ADMINISTRATIVAS.pdf"


def test_download_portal_attachment_falls_back_to_sanitized_header_filename() -> None:
    row = parse_attachment_listing(_LISTING_HTML, listing_url=_LISTING_URL)[0]
    nameless = replace(row, nombre="")

    def opener(request, timeout=None):
        # urllib exposes UTF-8 header bytes latin-1 decoded; the path traversal must not survive.
        mojibake = "ANEXO N°2.pdf".encode("utf-8").decode("latin-1")
        return _portal_response(
            _PDF_BYTES, headers={"Content-Disposition": f'attachment; filename="../../{mojibake}"'}
        )

    download = download_portal_attachment(nameless, {"__VIEWSTATE": "abc"}, opener=opener)
    assert download.filename == "ANEXO N°2.pdf"


def test_fetch_licitacion_attachments_rejects_non_portal_detail_url() -> None:
    opener = _fake_portal_opener()
    with pytest.raises(ChileCompraPortalError, match="non-detail portal URL"):
        fetch_licitacion_attachments("https://evil.example.com/DetailsAcquisition.aspx", opener=opener)
    assert opener.calls == []


def test_fetch_licitacion_attachments_rejects_portal_path_without_detail_contract() -> None:
    opener = _fake_portal_opener()
    with pytest.raises(ChileCompraPortalError, match="non-detail portal URL"):
        fetch_licitacion_attachments(
            "https://www.mercadopublico.cl/Procurement/Modules/Other/Page.aspx?qs=abc",
            opener=opener,
        )
    assert opener.calls == []


def test_fetch_licitacion_attachments_rejects_off_host_redirect() -> None:
    def opener(request, timeout=None):
        return _portal_response(
            _DETAIL_HTML.encode("utf-8"), url="https://evil.example.com/landing"
        )

    with pytest.raises(ChileCompraPortalError, match="non-detail portal URL"):
        fetch_licitacion_attachments(_DETAIL_URL, opener=opener)


def test_download_portal_attachment_raises_when_portal_serves_html() -> None:
    attachment = parse_attachment_listing(_LISTING_HTML, listing_url=_LISTING_URL)[0]

    def opener(request, timeout=None):
        return _portal_response(b"<html><body>Sesion expirada</body></html>")

    with pytest.raises(ChileCompraPortalError, match="HTML instead of a file"):
        download_portal_attachment(attachment, {"__VIEWSTATE": "abc"}, opener=opener)


def test_download_portal_attachment_detects_html_via_content_type_and_bom() -> None:
    attachment = parse_attachment_listing(_LISTING_HTML, listing_url=_LISTING_URL)[0]

    def opener_ct(request, timeout=None):
        return _portal_response(
            b"%PDF-not-really",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    with pytest.raises(ChileCompraPortalError, match="HTML instead of a file"):
        download_portal_attachment(attachment, {"__VIEWSTATE": "abc"}, opener=opener_ct)

    def opener_bom(request, timeout=None):
        return _portal_response(b"\xef\xbb\xbf<!-- x -->\n<html><body>expired</body></html>")

    with pytest.raises(ChileCompraPortalError, match="HTML instead of a file"):
        download_portal_attachment(attachment, {"__VIEWSTATE": "abc"}, opener=opener_bom)

    def opener_xml(request, timeout=None):
        return _portal_response(b'<?xml version="1.0"?><html><body>x</body></html>')

    with pytest.raises(ChileCompraPortalError, match="HTML instead of a file"):
        download_portal_attachment(attachment, {"__VIEWSTATE": "abc"}, opener=opener_xml)


def test_download_portal_attachment_allows_non_pdf_binary() -> None:
    attachment = parse_attachment_listing(_LISTING_HTML, listing_url=_LISTING_URL)[0]
    zip_bytes = b"PK\x03\x04" + b"\x00" * 20

    def opener(request, timeout=None):
        return _portal_response(zip_bytes, headers={"Content-Type": "application/zip"})

    download = download_portal_attachment(attachment, {"__VIEWSTATE": "abc"}, opener=opener)
    assert download.content == zip_bytes
    assert download.content_type == "application/zip"


def test_download_portal_attachment_enforces_max_bytes() -> None:
    attachment = parse_attachment_listing(_LISTING_HTML, listing_url=_LISTING_URL)[0]

    def opener(request, timeout=None):
        return _portal_response(b"%PDF-" + b"x" * 5000)

    with pytest.raises(ChileCompraPortalError, match="max_bytes=1024"):
        download_portal_attachment(attachment, {"__VIEWSTATE": "abc"}, opener=opener, max_bytes=1024)


def test_download_portal_attachment_surfaces_http_errors_without_enc_token() -> None:
    attachment = parse_attachment_listing(_LISTING_HTML, listing_url=_LISTING_URL)[0]

    def opener(request, timeout=None):
        raise HTTPError(_LISTING_URL, 500, "Server Error", {}, io.BytesIO(b""))

    with pytest.raises(ChileCompraHttpError, match="HTTP 500") as exc_info:
        download_portal_attachment(attachment, {"__VIEWSTATE": "abc"}, opener=opener)
    assert "AAA" not in str(exc_info.value)
    assert "enc=" in str(exc_info.value)


def test_fetch_licitacion_attachments_rejects_oversized_detail_html() -> None:
    def opener(request, timeout=None):
        return _portal_response(b"x" * 200, url=_DETAIL_URL)

    with pytest.raises(ChileCompraPortalError, match="max_bytes=100"):
        fetch_licitacion_attachments(_DETAIL_URL, opener=opener, html_max_bytes=100)


def test_fetch_licitacion_attachments_rejects_oversized_listing_html() -> None:
    detail_body = _DETAIL_HTML.encode("utf-8")

    def opener(request, timeout=None):
        if "DetailsAcquisition" in request.full_url:
            return _portal_response(detail_body, url=_DETAIL_URL)
        return _portal_response(b"y" * (len(detail_body) + 500), url=_LISTING_URL)

    with pytest.raises(ChileCompraPortalError, match="max_bytes="):
        fetch_licitacion_attachments(
            _DETAIL_URL,
            opener=opener,
            html_max_bytes=len(detail_body) + 50,
        )


def test_fetch_licitacion_attachments_accepts_html_under_limit() -> None:
    opener = _fake_portal_opener()
    downloads = fetch_licitacion_attachments(
        _DETAIL_URL, opener=opener, html_max_bytes=50_000
    )
    assert len(downloads) == 2


def test_fetch_licitacion_attachments_enforces_count_cap_fail_closed() -> None:
    opener = _fake_portal_opener()
    with pytest.raises(ChileCompraPortalError, match="max_attachment_count=1"):
        fetch_licitacion_attachments(_DETAIL_URL, opener=opener, max_attachment_count=1)


def test_fetch_licitacion_attachments_enforces_total_byte_cap_fail_closed() -> None:
    opener = _fake_portal_opener()
    with pytest.raises(ChileCompraPortalError, match="max_total_bytes="):
        fetch_licitacion_attachments(
            _DETAIL_URL,
            opener=opener,
            max_total_bytes=len(_PDF_BYTES),
        )


def test_save_licitacion_attachments_writes_files_to_destination(tmp_path) -> None:
    written = save_licitacion_attachments(
        _DETAIL_URL, tmp_path / "anexos", opener=_fake_portal_opener()
    )
    assert [p.name for p in written] == [
        "BASES ADMINISTRATIVAS.pdf",
        "ANEXO N°2 REQUERIMIENTOS.pdf",
    ]
    assert all(p.read_bytes() == _PDF_BYTES for p in written)
    assert all(p.resolve().parent == (tmp_path / "anexos").resolve() for p in written)


def test_save_licitacion_attachments_suffixes_duplicate_names(tmp_path) -> None:
    duplicated = _LISTING_HTML.replace("ANEXO N&deg;2 REQUERIMIENTOS.pdf", "BASES ADMINISTRATIVAS.pdf")

    def opener(request, timeout=None):
        if request.data is not None:
            return _portal_response(_PDF_BYTES, headers={"Content-Type": "application/pdf"})
        if "DetailsAcquisition" in request.full_url:
            return _portal_response(_DETAIL_HTML.encode("utf-8"))
        return _portal_response(duplicated.encode("utf-8"))

    written = save_licitacion_attachments(_DETAIL_URL, tmp_path / "anexos", opener=opener)
    assert [p.name for p in written] == [
        "BASES ADMINISTRATIVAS.pdf",
        "BASES ADMINISTRATIVAS (2).pdf",
    ]


def test_save_licitacion_attachments_does_not_overwrite_existing_file(tmp_path) -> None:
    dest = tmp_path / "anexos"
    dest.mkdir()
    existing = dest / "BASES ADMINISTRATIVAS.pdf"
    existing.write_bytes(b"original-content")

    written = save_licitacion_attachments(_DETAIL_URL, dest, opener=_fake_portal_opener())
    assert existing.read_bytes() == b"original-content"
    assert (dest / "BASES ADMINISTRATIVAS (2).pdf") in written
    assert (dest / "ANEXO N°2 REQUERIMIENTOS.pdf") in written


@pytest.mark.skipif(not hasattr(__import__("os"), "O_NOFOLLOW"), reason="O_NOFOLLOW unavailable")
def test_save_licitacion_attachments_does_not_follow_symlink(tmp_path) -> None:
    import os

    dest = tmp_path / "anexos"
    dest.mkdir()
    target = tmp_path / "outside-target.bin"
    target.write_bytes(b"do-not-touch")
    link = dest / "BASES ADMINISTRATIVAS.pdf"
    os.symlink(target, link)

    written = save_licitacion_attachments(_DETAIL_URL, dest, opener=_fake_portal_opener())

    assert target.read_bytes() == b"do-not-touch"
    assert link.is_symlink()
    assert any(p.name == "BASES ADMINISTRATIVAS (2).pdf" for p in written)
    assert all(not p.is_symlink() for p in written)


def test_validated_redirect_handler_allows_same_host_https() -> None:
    from urllib.request import Request

    handler = _ValidatedPortalRedirectHandler()
    req = Request(_DETAIL_URL)
    next_url = (
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?qs=other-token"
    )
    redirected = handler.redirect_request(
        req, fp=None, code=302, msg="Found", headers={"Location": next_url}, newurl=next_url
    )
    assert redirected is not None
    assert redirected.full_url == next_url


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.com/steal",
        "http://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?qs=x",
        "https://127.0.0.1/internal",
        "https://localhost/internal",
        "https://[::1]/internal",
        "https://169.254.169.254/latest/meta-data/",
        "https://mercadopublico.cl.evil.example/x",
        "https://user:pass@www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?qs=x",
    ],
)
def test_validated_redirect_handler_rejects_unsafe_targets(location: str) -> None:
    from urllib.request import Request

    handler = _ValidatedPortalRedirectHandler()
    req = Request(_DETAIL_URL)
    with pytest.raises(ChileCompraPortalError):
        handler.redirect_request(
            req, fp=None, code=302, msg="Found", headers={"Location": location}, newurl=location
        )


def test_portal_opener_rejects_redirect_before_second_request() -> None:
    """SSRF guard: blocked Location must not trigger a follow-up HTTPS open."""
    from email.message import EmailMessage
    from io import BytesIO
    from urllib.request import HTTPSHandler, build_opener
    from urllib.response import addinfourl

    from origenlab_email_pipeline.chilecompra_api import _ValidatedPortalRedirectHandler

    calls: list[str] = []
    start = "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?qs=start"
    evil = "https://evil.example.com/internal"

    class ScriptedHTTPSHandler(HTTPSHandler):
        def https_open(self, req):  # noqa: ANN001
            calls.append(req.full_url)
            headers = EmailMessage()
            if req.full_url == start:
                headers["Location"] = evil
                resp = addinfourl(BytesIO(b""), headers, req.full_url, code=302)
                resp.msg = "Found"
                return resp
            resp = addinfourl(BytesIO(b"should-not-fetch"), headers, req.full_url, code=200)
            resp.msg = "OK"
            return resp

    opener = build_opener(_ValidatedPortalRedirectHandler(), ScriptedHTTPSHandler()).open
    from urllib.request import Request

    with pytest.raises(ChileCompraPortalError):
        opener(Request(start), timeout=5)
    assert calls == [start]
    assert evil not in calls


def test_new_portal_opener_includes_redirect_validation() -> None:
    opener = new_portal_opener()
    assert callable(opener)
