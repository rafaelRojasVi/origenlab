"""Read-only client for Mercado Público licitaciones JSON API and portal anexos."""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

from origenlab_email_pipeline.equipment_first_licitacion_queue import CSV_COLUMNS, parse_close_date

LICITACIONES_API_BASE = (
    "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json"
)
TICKET_ENV_VAR = "CHILECOMPRA_API_TICKET"
DEFAULT_TIMEOUT_SECONDS = 30.0
FECHA_PATTERN = re.compile(r"^\d{8}$")
_TICKET_IN_URL_RE = re.compile(r"([?&]ticket=)[^&]*", re.IGNORECASE)
_UNSAFE_ATTACHMENT_URL_RE = re.compile(r"ticket|api\.chilecompra|api\.mercadopublico\.cl", re.IGNORECASE)

# --- Public portal (anexos) -------------------------------------------------
# The JSON API exposes anexo *metadata* only; the files themselves live behind
# the ASP.NET portal, where each listing row downloads via a WebForms postback.
PORTAL_BASE_URL = "https://www.mercadopublico.cl/Procurement/Modules/"
PORTAL_DETAIL_PATH = "RFB/DetailsAcquisition.aspx"
PORTAL_LISTING_PATH = "Attachment/VerAntecedentes.aspx"
PORTAL_ALLOWED_HOSTS = frozenset({"www.mercadopublico.cl", "mercadopublico.cl"})
PORTAL_USER_AGENT = "origenlab-chilecompra-api/1.0"
DEFAULT_ATTACHMENT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_PORTAL_HTML_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_ATTACHMENT_COUNT = 50
DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES = 128 * 1024 * 1024
_ATTACHMENT_FALLBACK_FILENAME = "anexo.bin"
_MAX_ATTACHMENT_FILENAME_LENGTH = 180
_MAX_PORTAL_REDIRECTS = 5
_PORTAL_DETAIL_PATH_SUFFIX = "/procurement/modules/rfb/detailsacquisition.aspx"
_PORTAL_LISTING_PATH_SUFFIX = "/procurement/modules/attachment/verantecedentes.aspx"
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_SENSITIVE_PORTAL_QUERY_KEYS = frozenset({"qs", "enc", "ticket"})
# A ficha is addressable by the opaque token or by the public tender code.
_PORTAL_DETAIL_QUERY_KEYS = ("qs", "idlicitacion")
# Budget breaches are named so callers can record why a bundle is incomplete.
ATTACHMENT_COUNT_BUDGET_EXCEEDED = "attachment_count_budget_exceeded"
TOTAL_BYTES_BUDGET_EXCEEDED = "total_bytes_budget_exceeded"

_LISTING_HREF_RE = re.compile(
    r"""href=["']((?:\.\./)*Attachment/VerAntecedentes\.aspx\?enc=[^"'#]+)["']""",
    re.IGNORECASE,
)
_HIDDEN_INPUT_RE = re.compile(r"""<input\b[^>]*type=["']hidden["'][^>]*>""", re.IGNORECASE)
_INPUT_NAME_RE = re.compile(r"""\bname=["']([^"']+)["']""", re.IGNORECASE)
_INPUT_VALUE_RE = re.compile(r"""\bvalue=["']([^"']*)["']""", re.IGNORECASE)
_GRID_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.IGNORECASE | re.DOTALL)
_VIEW_BUTTON_RE = re.compile(
    r"""\bname=["'](grdAttachment\$ctl\d+\$grdIbtnView)["']""", re.IGNORECASE
)
_TABLE_CELL_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"""filename\*?=(?:UTF-8''|["'])?([^"';]+)""", re.IGNORECASE
)
_UNSAFE_FILENAME_RE = re.compile(r"""[\\/:*?"<>|\x00-\x1f]""")
_ROW_LABEL_IDS = {
    "nombre": "grdLblSourceFileName",
    "descripcion": "grdLblFileDescription",
    "fecha_adjunto": "grdLblFileDate",
}

_ANEXOS_CONTAINER_KEYS = (
    "Anexos",
    "Adjuntos",
    "Documentos",
    "Archivos",
    "ListadoAnexos",
    "ListadoAdjuntos",
)
_ANEXO_ITEM_KEYS = ("Anexo", "Adjunto", "Documento", "Archivo")
_ANEXO_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "nombre": ("Nombre", "nombre", "NombreArchivo", "NombreDocumento", "Titulo"),
    "tipo": ("Tipo", "tipo", "TipoDocumento", "TipoArchivo"),
    "descripcion": ("Descripcion", "descripcion", "Detalle", "Glosa"),
    "tamano": ("Tamano", "Tamanio", "tamano", "Size", "Peso"),
    "fecha_adjunto": ("Fecha", "FechaAdjunto", "fecha_adjunto", "FechaPublicacion"),
    "url": ("Url", "URL", "url", "Link", "Enlace", "Uri"),
}

CHILECOMPRA_EXTRA_FIELDS = (
    "chilecompra_status_code",
    "chilecompra_status",
)
CHILECOMPRA_NORMALIZED_FIELDS = (*CSV_COLUMNS, *CHILECOMPRA_EXTRA_FIELDS)

ACTIVE_CHILECOMPRA_STATUS_CODE = "5"
INACTIVE_CHILECOMPRA_STATUS_CODES = frozenset({"6", "7", "8", "18", "19"})
INACTIVE_CHILECOMPRA_STATUS_NAMES = frozenset(
    {
        "cerrada",
        "desierta",
        "adjudicada",
        "revocada",
        "suspendida",
    }
)

VALIDITY_STATUS_OPEN = "open"
VALIDITY_STATUS_CLOSES_TODAY = "closes_today"
VALIDITY_STATUS_EXPIRED = "expired"
VALIDITY_STATUS_NOT_PUBLICADA = "status_not_publicada"
VALIDITY_STATUS_MISSING_CLOSE_DATE = "missing_close_date"

UrlopenFn = Callable[..., Any]
PortalOpenFn = Callable[..., Any]


class ChileCompraApiError(Exception):
    """Base error for Mercado Público licitaciones API client."""


class ChileCompraTicketMissingError(ChileCompraApiError):
    """Raised when CHILECOMPRA_API_TICKET is not configured."""


class ChileCompraHttpError(ChileCompraApiError):
    """Raised when the API returns a non-success HTTP status."""


class ChileCompraJsonError(ChileCompraApiError):
    """Raised when the API response is not valid JSON or has an unexpected shape."""


class ChileCompraPortalError(ChileCompraApiError):
    """Raised when the public portal returns markup we cannot turn into a download."""


def ticket_from_env(environ: dict[str, str] | None = None) -> str:
    """Return the API ticket from ``CHILECOMPRA_API_TICKET``."""
    env = environ if environ is not None else os.environ
    ticket = (env.get(TICKET_ENV_VAR) or "").strip()
    if not ticket:
        raise ChileCompraTicketMissingError(
            f"{TICKET_ENV_VAR} is not set. Request a Mercado Público API ticket and "
            "export it in the environment before calling the live API."
        )
    return ticket


def redact_ticket(text: str, ticket: str) -> str:
    """Remove a ticket value from free-form text (errors, logs)."""
    if not ticket:
        return text
    redacted = text.replace(ticket, "<redacted>")
    return _TICKET_IN_URL_RE.sub(r"\1<redacted>", redacted)


def redact_ticket_in_url(url: str) -> str:
    """Return a URL safe for logs/tests without exposing the ticket query param."""
    return _TICKET_IN_URL_RE.sub(r"\1<redacted>", url)


def validate_fecha(fecha: str) -> str:
    """Validate Mercado Público ``fecha`` query format ``ddmmaaaa``."""
    value = (fecha or "").strip()
    if not FECHA_PATTERN.fullmatch(value):
        raise ValueError("fecha must use ddmmaaaa format (8 digits, e.g. 14062026)")
    return value


def build_licitaciones_url(
    *,
    ticket: str,
    fecha: str | None = None,
    estado: str | None = None,
    codigo: str | None = None,
) -> str:
    """Build the licitaciones JSON endpoint URL."""
    if not (ticket or "").strip():
        raise ValueError("ticket is required")
    params: dict[str, str] = {"ticket": ticket.strip()}
    if fecha is not None:
        params["fecha"] = validate_fecha(fecha)
    if estado is not None and estado.strip():
        params["estado"] = estado.strip()
    if codigo is not None and codigo.strip():
        params["codigo"] = codigo.strip()
    query = urlencode(params)
    parsed = urlparse(LICITACIONES_API_BASE)
    return urlunparse(parsed._replace(query=query))


def _empty_row() -> dict[str, str]:
    return {column: "" for column in CHILECOMPRA_NORMALIZED_FIELDS}


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _nested_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("NombreOrganismo", "Nombre", "RazonSocial", "NombreUnidad"):
            text = _as_str(value.get(key))
            if text:
                return text
    return _as_str(value)


def _buyer_name(licitacion: dict[str, Any]) -> str:
    for key in ("Comprador", "OrganismoComprador", "Organismo"):
        name = _nested_name(licitacion.get(key))
        if name:
            return name
    for key in ("NombreOrganismo", "NombreUnidad", "Comprador"):
        text = _as_str(licitacion.get(key))
        if text:
            return text
    return ""


def _region_name(licitacion: dict[str, Any]) -> str:
    for key in ("Region", "RegionUnidad", "NombreRegion"):
        text = _as_str(licitacion.get(key))
        if text:
            return text
    for container_key in ("Comprador", "OrganismoComprador", "Organismo"):
        container = licitacion.get(container_key)
        if isinstance(container, dict):
            text = _as_str(container.get("Region") or container.get("RegionUnidad"))
            if text:
                return text
    return ""


def _codigo_externo(licitacion: dict[str, Any]) -> str:
    for key in ("CodigoExterno", "Codigo", "codigo"):
        text = _as_str(licitacion.get(key))
        if text:
            return text
    return ""


def _normalize_status_name(value: str) -> str:
    return (value or "").strip().casefold()


def _extract_status_fields(licitacion: dict[str, Any]) -> tuple[str, str]:
    return (
        _as_str(licitacion.get("CodigoEstado")),
        _as_str(licitacion.get("Estado")),
    )


def _extract_close_date(licitacion: dict[str, Any]) -> str:
    for key in ("FechaCierre", "FechaFinal", "FechaCierre1"):
        text = _as_str(licitacion.get(key))
        if text:
            return text
    fechas = licitacion.get("Fechas")
    if isinstance(fechas, dict):
        for key in ("FechaCierre", "FechaFinal"):
            text = _as_str(fechas.get(key))
            if text:
                return text
    return ""


def is_active_chilecompra_licitacion(
    *,
    chilecompra_status_code: str,
    chilecompra_status: str,
) -> bool:
    code = (chilecompra_status_code or "").strip()
    status = _normalize_status_name(chilecompra_status)
    if code in INACTIVE_CHILECOMPRA_STATUS_CODES:
        return False
    if status in INACTIVE_CHILECOMPRA_STATUS_NAMES:
        return False
    if code and code != ACTIVE_CHILECOMPRA_STATUS_CODE:
        return False
    return True


def classify_chilecompra_validity_status(
    *,
    chilecompra_status_code: str,
    chilecompra_status: str,
    close_date: str,
    now: datetime,
) -> str:
    if not is_active_chilecompra_licitacion(
        chilecompra_status_code=chilecompra_status_code,
        chilecompra_status=chilecompra_status,
    ):
        return VALIDITY_STATUS_NOT_PUBLICADA
    raw_close_date = (close_date or "").strip()
    if not raw_close_date:
        return VALIDITY_STATUS_MISSING_CLOSE_DATE
    close_dt = parse_close_date(raw_close_date)
    if close_dt is None:
        return VALIDITY_STATUS_MISSING_CLOSE_DATE
    days = (close_dt.date() - now.date()).days
    if days < 0:
        return VALIDITY_STATUS_EXPIRED
    if days == 0:
        return VALIDITY_STATUS_CLOSES_TODAY
    return VALIDITY_STATUS_OPEN


def normalize_licitacion_summary(licitacion: dict[str, Any]) -> dict[str, str]:
    """Normalize a basic licitación payload to equipment-first CSV row shape."""
    row = _empty_row()
    row["codigo"] = _codigo_externo(licitacion)
    row["tipo_licitacion"] = _as_str(
        licitacion.get("TipoLicitacion") or licitacion.get("Tipo") or licitacion.get("CodigoTipo")
    )
    row["title"] = _as_str(licitacion.get("Nombre") or licitacion.get("Titulo"))
    row["descripcion"] = _as_str(licitacion.get("Descripcion"))
    row["buyer"] = _buyer_name(licitacion)
    row["region"] = _region_name(licitacion)
    row["fecha_publicacion"] = _as_str(
        licitacion.get("FechaPublicacion") or licitacion.get("FechaCreacion")
    )
    row["close_date"] = _extract_close_date(licitacion)
    status_code, status_name = _extract_status_fields(licitacion)
    row["chilecompra_status_code"] = status_code
    row["chilecompra_status"] = status_name
    return row


def _extract_listado(payload: dict[str, Any]) -> list[dict[str, Any]]:
    listado = payload.get("Listado")
    if listado is None:
        return []
    if isinstance(listado, list):
        return [item for item in listado if isinstance(item, dict)]
    if isinstance(listado, dict):
        nested = listado.get("Licitacion")
        if nested is None:
            return [listado]
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            return [nested]
    return []


def is_safe_public_attachment_url(url: str) -> bool:
    text = (url or "").strip()
    if not text:
        return False
    if not text.lower().startswith(("http://", "https://")):
        return False
    return _UNSAFE_ATTACHMENT_URL_RE.search(text) is None


def _pick_anexo_field(item: dict[str, Any], field: str) -> str:
    for key in _ANEXO_FIELD_ALIASES.get(field, ()):
        value = _as_str(item.get(key))
        if value:
            return value
    return ""


def _extract_anexo_items(container: Any) -> list[dict[str, Any]]:
    if container is None:
        return []
    if isinstance(container, list):
        return [item for item in container if isinstance(item, dict)]
    if isinstance(container, dict):
        for key in ("Listado", "listado", *_ANEXO_ITEM_KEYS):
            nested = container.get(key)
            if nested is None:
                continue
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
            if isinstance(nested, dict):
                return [nested]
        return [container]
    return []


def _normalize_anexo_item(item: dict[str, Any]) -> dict[str, str]:
    url = _pick_anexo_field(item, "url")
    if url and not is_safe_public_attachment_url(url):
        url = ""
    return {
        "nombre": _pick_anexo_field(item, "nombre"),
        "tipo": _pick_anexo_field(item, "tipo"),
        "descripcion": _pick_anexo_field(item, "descripcion"),
        "tamano": _pick_anexo_field(item, "tamano"),
        "fecha_adjunto": _pick_anexo_field(item, "fecha_adjunto"),
        "url": url,
    }


def extract_licitacion_anexos(licitacion: dict[str, Any]) -> list[dict[str, str]]:
    """Extract safe read-only attachment metadata from a licitación detail payload."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, str]] = []
    for key in _ANEXOS_CONTAINER_KEYS:
        for item in _extract_anexo_items(licitacion.get(key)):
            normalized = _normalize_anexo_item(item)
            if not any(normalized.values()):
                continue
            dedupe_key = (
                normalized["nombre"],
                normalized["tipo"],
                normalized["url"],
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(normalized)
    return out


def _extract_items(licitacion: dict[str, Any]) -> list[dict[str, Any]]:
    items = licitacion.get("Items")
    if items is None:
        return []
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if isinstance(items, dict):
        listado = items.get("Listado")
        if listado is None:
            if any(
                key in items
                for key in (
                    "Descripcion",
                    "NombreProducto",
                    "CodigoProducto",
                    "Categoria",
                    "Cantidad",
                )
            ):
                return [items]
            return []
        if isinstance(listado, list):
            return [item for item in listado if isinstance(item, dict)]
        if isinstance(listado, dict):
            return [listado]
    return []


def _normalize_item_fields(item: dict[str, Any]) -> dict[str, str]:
    return {
        "line_description": _as_str(
            item.get("Descripcion")
            or item.get("NombreProducto")
            or item.get("DescripcionProducto")
            or item.get("rbiDescription")
        ),
        "unspsc_code": _as_str(
            item.get("CodigoProducto")
            or item.get("CodigoCategoria")
            or item.get("CodigoUNSPSC")
            or item.get("unspsc_code")
        ),
        "unidad": _as_str(item.get("UnidadMedida") or item.get("Unidad") or item.get("Medida")),
        "cantidad": _as_str(item.get("Cantidad")),
        "producto": _as_str(
            item.get("Producto") or item.get("NombreProducto") or item.get("Generico")
        ),
        "nivel_1": _as_str(item.get("Nivel1") or item.get("nivel_1") or item.get("Categoria")),
        "nivel_2": _as_str(item.get("Nivel2") or item.get("nivel_2")),
        "nivel_3": _as_str(item.get("Nivel3") or item.get("nivel_3")),
    }


def normalize_licitacion_detail_items(
    licitacion: dict[str, Any],
    *,
    summary_fallback: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Normalize a detailed licitación plus its line items to CSV-shaped rows."""
    summary = normalize_licitacion_summary(licitacion)
    if summary_fallback:
        for field in ("close_date", "chilecompra_status_code", "chilecompra_status"):
            if not (summary.get(field) or "").strip():
                fallback_value = (summary_fallback.get(field) or "").strip()
                if fallback_value:
                    summary[field] = fallback_value
    items = _extract_items(licitacion)
    if not items:
        return [summary]
    rows: list[dict[str, str]] = []
    for item in items:
        row = dict(summary)
        row.update(_normalize_item_fields(item))
        rows.append(row)
    return rows


def normalize_licitaciones_response(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize a list lookup response to summary rows."""
    return [normalize_licitacion_summary(item) for item in _extract_listado(payload)]


def _decode_json_response(raw: bytes, *, ticket: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ChileCompraJsonError("API response is not valid UTF-8") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        message = redact_ticket(str(exc), ticket)
        raise ChileCompraJsonError(f"API response is not valid JSON: {message}") from exc
    if not isinstance(payload, dict):
        raise ChileCompraJsonError("API response must be a JSON object")
    return payload


def _fetch_json(url: str, *, ticket: str, timeout: float, urlopen_fn: UrlopenFn | None) -> dict[str, Any]:
    opener = urlopen_fn or urlopen
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "origenlab-chilecompra-api/1.0"},
        method="GET",
    )
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read()
    except HTTPError as exc:
        message = redact_ticket(
            f"HTTP {exc.code} while fetching {redact_ticket_in_url(url)}",
            ticket,
        )
        raise ChileCompraHttpError(message) from exc
    except URLError as exc:
        message = redact_ticket(
            f"Network error while fetching {redact_ticket_in_url(url)}: {exc.reason}",
            ticket,
        )
        raise ChileCompraHttpError(message) from exc

    if status is not None and int(status) >= 400:
        message = redact_ticket(
            f"HTTP {status} while fetching {redact_ticket_in_url(url)}",
            ticket,
        )
        raise ChileCompraHttpError(message)

    return _decode_json_response(body, ticket=ticket)


def fetch_licitaciones(
    *,
    ticket: str | None = None,
    fecha: str | None = None,
    estado: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    urlopen_fn: UrlopenFn | None = None,
) -> dict[str, Any]:
    """Fetch licitaciones list JSON from Mercado Público."""
    resolved_ticket = ticket or ticket_from_env()
    url = build_licitaciones_url(ticket=resolved_ticket, fecha=fecha, estado=estado)
    return _fetch_json(url, ticket=resolved_ticket, timeout=timeout, urlopen_fn=urlopen_fn)


def fetch_licitacion_by_codigo(
    codigo: str,
    *,
    ticket: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    urlopen_fn: UrlopenFn | None = None,
) -> dict[str, Any]:
    """Fetch one licitación by ``codigo`` (detailed payload when available)."""
    resolved_ticket = ticket or ticket_from_env()
    url = build_licitaciones_url(ticket=resolved_ticket, codigo=codigo)
    return _fetch_json(url, ticket=resolved_ticket, timeout=timeout, urlopen_fn=urlopen_fn)


# --- Public portal anexo downloads ------------------------------------------


@dataclass(frozen=True)
class PortalAttachment:
    """One anexo row on a ``VerAntecedentes.aspx`` listing page."""

    nombre: str
    tipo: str
    descripcion: str
    fecha_adjunto: str
    listing_url: str
    control_name: str


@dataclass(frozen=True)
class DownloadedAttachment:
    """An anexo whose bytes were fetched from the portal."""

    attachment: PortalAttachment
    filename: str
    content_type: str
    content: bytes

    @property
    def is_pdf(self) -> bool:
        return self.content.startswith(b"%PDF")


def build_licitacion_detail_url(qs: str) -> str:
    """Build the portal ficha URL from the ``qs`` token in a Mercado Público link."""
    token = (qs or "").strip()
    if not token:
        raise ValueError("qs is required")
    return f"{PORTAL_BASE_URL}{PORTAL_DETAIL_PATH}?{urlencode({'qs': token})}"


def is_portal_url(url: str) -> bool:
    """Return True when ``url`` is an https Mercado Público portal URL."""
    text = (url or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = parsed.hostname
    return host is not None and host.lower() in PORTAL_ALLOWED_HOSTS


def sanitize_portal_url_for_error(url: str) -> str:
    """Redact opaque portal query tokens (``qs`` / ``enc`` / ``ticket``) from error text."""
    text = (url or "").strip()
    if not text:
        return text
    parsed = urlparse(text)
    if not parsed.query:
        return text
    pairs: list[tuple[str, str]] = []
    for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
        if key.lower() in _SENSITIVE_PORTAL_QUERY_KEYS:
            pairs.append((key, "<redacted>"))
        else:
            for value in values:
                pairs.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(pairs)))


def _require_portal_url(url: str) -> str:
    if not is_portal_url(url):
        raise ChileCompraPortalError(
            f"Refusing to fetch non-portal URL: {sanitize_portal_url_for_error(url)!r} "
            f"(expected https on one of {sorted(PORTAL_ALLOWED_HOSTS)})"
        )
    return url


def _normalized_portal_path(url: str) -> str:
    path = (urlparse(url).path or "").lower()
    while "//" in path:
        path = path.replace("//", "/")
    return path.rstrip("/")


def build_licitacion_detail_url_for_codigo(codigo: str) -> str:
    """Build the ficha URL from a public ``CodigoExterno``.

    The portal answers this form with a redirect to the opaque ``qs`` variant on
    the same endpoint, so callers holding only a tender code can still reach a
    ficha without an authenticated ticket.
    """
    token = (codigo or "").strip()
    if not token:
        raise ValueError("codigo is required")
    return f"{PORTAL_BASE_URL}{PORTAL_DETAIL_PATH}?{urlencode({'idlicitacion': token})}"


def is_portal_detail_url(url: str) -> bool:
    """True for a licitación ficha endpoint keyed by ``qs`` or ``idlicitacion``."""
    if not is_portal_url(url):
        return False
    path = _normalized_portal_path(url)
    if not path.endswith(_PORTAL_DETAIL_PATH_SUFFIX):
        return False
    query = parse_qs(urlparse(url).query)
    for key in _PORTAL_DETAIL_QUERY_KEYS:
        values = query.get(key) or []
        if values and str(values[0]).strip():
            return True
    return False


def is_portal_listing_url(url: str) -> bool:
    """True when ``url`` is a Mercado Público anexo listing endpoint with ``enc``."""
    if not is_portal_url(url):
        return False
    path = _normalized_portal_path(url)
    if not path.endswith(_PORTAL_LISTING_PATH_SUFFIX):
        return False
    enc_values = parse_qs(urlparse(url).query).get("enc") or []
    return bool(enc_values and str(enc_values[0]).strip())


def _require_portal_detail_url(url: str) -> str:
    if not is_portal_detail_url(url):
        raise ChileCompraPortalError(
            "Refusing non-detail portal URL: "
            f"{sanitize_portal_url_for_error(url)!r} "
            "(expected https DetailsAcquisition.aspx with qs=... or idlicitacion=...)"
        )
    return url


def _require_portal_listing_url(url: str) -> str:
    if not is_portal_listing_url(url):
        raise ChileCompraPortalError(
            "Refusing non-listing portal URL: "
            f"{sanitize_portal_url_for_error(url)!r} "
            "(expected https VerAntecedentes.aspx with enc=...)"
        )
    return url


class _ValidatedPortalRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only after each Location target passes portal host checks.

    Default urllib redirect following would otherwise issue the next request before
    any post-response URL validation, enabling SSRF via open redirects.
    """

    max_repeats = _MAX_PORTAL_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        absolute = urljoin(req.full_url, newurl)
        _require_portal_url(absolute)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def _cell_text(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", fragment)).strip()


def _row_label_text(row_html: str, label_id: str) -> str:
    match = re.search(
        rf"""id=["'][^"']*_{re.escape(label_id)}["'][^>]*>(.*?)</span>""",
        row_html,
        re.IGNORECASE | re.DOTALL,
    )
    return _cell_text(match.group(1)) if match else ""


def extract_attachment_listing_urls(page_html: str, *, base_url: str = PORTAL_BASE_URL) -> list[str]:
    """Extract the ``VerAntecedentes.aspx`` listing URLs linked from a ficha page."""
    urls: list[str] = []
    for match in _LISTING_HREF_RE.finditer(page_html or ""):
        href = html.unescape(match.group(1))
        # Hrefs are relative to .../Modules/RFB/; normalize onto the Modules root.
        absolute = urljoin(base_url, href.replace("../", "", 1) if href.startswith("../") else href)
        if is_portal_listing_url(absolute) and absolute not in urls:
            urls.append(absolute)
    return urls


def parse_attachment_listing(listing_html: str, *, listing_url: str) -> list[PortalAttachment]:
    """Parse the anexo rows (name, tipo, postback control) off a listing page."""
    rows: list[PortalAttachment] = []
    for row_html in _GRID_ROW_RE.findall(listing_html or ""):
        button = _VIEW_BUTTON_RE.search(row_html)
        if not button:
            continue
        cells = [_cell_text(cell) for cell in _TABLE_CELL_RE.findall(row_html)]
        rows.append(
            PortalAttachment(
                nombre=_row_label_text(row_html, _ROW_LABEL_IDS["nombre"]),
                # "Tipo" is the only column rendered as a bare cell, without a label id.
                tipo=cells[1] if len(cells) > 1 else "",
                descripcion=_row_label_text(row_html, _ROW_LABEL_IDS["descripcion"]),
                fecha_adjunto=_row_label_text(row_html, _ROW_LABEL_IDS["fecha_adjunto"]),
                listing_url=listing_url,
                control_name=html.unescape(button.group(1)),
            )
        )
    return rows


def extract_aspnet_form_fields(page_html: str) -> dict[str, str]:
    """Collect the hidden WebForms fields (``__VIEWSTATE`` and friends) from a page."""
    fields: dict[str, str] = {}
    for tag in _HIDDEN_INPUT_RE.findall(page_html or ""):
        name = _INPUT_NAME_RE.search(tag)
        if not name:
            continue
        value = _INPUT_VALUE_RE.search(tag)
        fields[html.unescape(name.group(1))] = html.unescape(value.group(1)) if value else ""
    return fields


def build_attachment_postback_body(fields: dict[str, str], control_name: str) -> bytes:
    """Build the urlencoded postback body that makes the portal serve one anexo."""
    if not (control_name or "").strip():
        raise ValueError("control_name is required")
    payload = dict(fields)
    # The download trigger is an <input type="image">, so it posts x/y instead of a value.
    payload[f"{control_name}.x"] = "10"
    payload[f"{control_name}.y"] = "10"
    return urlencode(payload, encoding="utf-8").encode("utf-8")


def safe_attachment_filename(name: str, *, fallback: str = _ATTACHMENT_FALLBACK_FILENAME) -> str:
    """Sanitize a portal-supplied filename so it is safe to write to disk."""
    candidate = unquote((name or "").strip()).replace("\\", "/").split("/")[-1]
    candidate = _UNSAFE_FILENAME_RE.sub("_", candidate).strip().strip(".").strip()
    if not candidate:
        return fallback
    stem, dot, suffix = candidate.rpartition(".")
    reserved_stem = (stem if dot else candidate).upper()
    if reserved_stem in _WINDOWS_RESERVED_FILENAMES:
        candidate = f"_{candidate}"
    if len(candidate) > _MAX_ATTACHMENT_FILENAME_LENGTH:
        stem, dot, suffix = candidate.rpartition(".")
        if dot and len(suffix) <= 8:
            keep = _MAX_ATTACHMENT_FILENAME_LENGTH - len(suffix) - 1
            candidate = f"{stem[:keep]}.{suffix}"
        else:
            candidate = candidate[:_MAX_ATTACHMENT_FILENAME_LENGTH]
    return candidate or fallback


def _repair_latin1_mojibake(text: str) -> str:
    """Undo the latin-1 decode urllib applies to UTF-8 bytes in response headers."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _filename_from_headers(headers: dict[str, str]) -> str:
    disposition = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    match = _CONTENT_DISPOSITION_FILENAME_RE.search(disposition)
    if not match:
        return ""
    return _repair_latin1_mojibake(match.group(1).strip().strip('"'))


def _looks_like_html_payload(body: bytes, content_type: str = "") -> bool:
    """Best-effort detection that the portal returned an HTML page instead of a file."""
    ct = (content_type or "").lower()
    if "text/html" in ct or "application/xhtml" in ct:
        return True
    sample = body[:2048].lstrip()
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:].lstrip()
    if sample.startswith(b"<?xml"):
        end = sample.find(b"?>")
        if end != -1:
            sample = sample[end + 2 :].lstrip()
    while sample.startswith(b"<!--"):
        end = sample.find(b"-->")
        if end == -1:
            break
        sample = sample[end + 3 :].lstrip()
    lower = sample[:64].lower()
    return lower.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _write_bytes_exclusive(path: Path, content: bytes) -> None:
    """Create ``path`` exclusively; do not overwrite or follow an existing filesystem object."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(os.fspath(path), flags, 0o644)
    except FileExistsError:
        raise
    except OSError as exc:
        raise ChileCompraPortalError(
            f"Refusing to write attachment through unsafe path {path.name!r}: {exc.strerror or exc}"
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def new_portal_opener() -> PortalOpenFn:
    """Build a cookie-aware opener; the portal ties ``__VIEWSTATE`` to a session cookie."""
    return build_opener(
        HTTPCookieProcessor(CookieJar()),
        _ValidatedPortalRedirectHandler(),
    ).open


def _portal_request(
    url: str,
    *,
    opener: PortalOpenFn,
    timeout: float,
    data: bytes | None = None,
    referer: str | None = None,
    max_bytes: int | None = None,
    url_kind: str = "any",
) -> tuple[bytes, dict[str, str]]:
    if url_kind == "detail":
        _require_portal_detail_url(url)
    elif url_kind == "listing":
        _require_portal_listing_url(url)
    else:
        _require_portal_url(url)
    safe_url = sanitize_portal_url_for_error(url)
    headers = {"User-Agent": PORTAL_USER_AGENT}
    if referer:
        headers["Referer"] = sanitize_portal_url_for_error(referer)
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            final_url = getattr(response, "url", None) or url
            body = response.read() if max_bytes is None else response.read(max_bytes + 1)
            response_headers = dict(getattr(response, "headers", {}) or {})
    except ChileCompraPortalError:
        raise
    except HTTPError as exc:
        raise ChileCompraHttpError(f"HTTP {exc.code} while fetching {safe_url}") from exc
    except URLError as exc:
        raise ChileCompraHttpError(
            f"Network error while fetching {safe_url}: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise ChileCompraHttpError(f"Timed out while fetching {safe_url}") from exc

    if status is not None and int(status) >= 400:
        raise ChileCompraHttpError(f"HTTP {status} while fetching {safe_url}")
    # Redirect targets are validated by `_ValidatedPortalRedirectHandler` before follow;
    # still fail closed if a custom opener returns an off-host final URL.
    if url_kind == "detail":
        _require_portal_detail_url(str(final_url))
    elif url_kind == "listing":
        # Download postbacks stay on the listing endpoint; final URL must remain portal.
        _require_portal_url(str(final_url))
    else:
        _require_portal_url(str(final_url))
    if max_bytes is not None and len(body) > max_bytes:
        raise ChileCompraPortalError(
            f"Portal response at {safe_url} exceeds max_bytes={max_bytes}"
        )
    return body, response_headers


def _decode_portal_html(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def download_portal_attachment(
    attachment: PortalAttachment,
    form_fields: dict[str, str],
    *,
    opener: PortalOpenFn,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_ATTACHMENT_MAX_BYTES,
) -> DownloadedAttachment:
    """Post back one listing row and return the file the portal serves."""
    body, headers = _portal_request(
        attachment.listing_url,
        opener=opener,
        timeout=timeout,
        data=build_attachment_postback_body(form_fields, attachment.control_name),
        referer=attachment.listing_url,
        max_bytes=max_bytes,
        url_kind="listing",
    )
    content_type = (headers.get("Content-Type") or headers.get("content-type") or "").strip()
    if _looks_like_html_payload(body, content_type):
        raise ChileCompraPortalError(
            f"Portal returned HTML instead of a file for {attachment.nombre or attachment.control_name!r}"
        )
    # The listing name is decoded from the page's UTF-8 markup, so it keeps accents and
    # spaces the portal mangles in Content-Disposition; fall back to the header otherwise.
    return DownloadedAttachment(
        attachment=attachment,
        filename=safe_attachment_filename(attachment.nombre or _filename_from_headers(headers)),
        content_type=content_type,
        content=body,
    )


def fetch_licitacion_attachments(
    detail_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_ATTACHMENT_MAX_BYTES,
    html_max_bytes: int = DEFAULT_PORTAL_HTML_MAX_BYTES,
    max_attachment_count: int = DEFAULT_MAX_ATTACHMENT_COUNT,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
    opener: PortalOpenFn | None = None,
) -> list[DownloadedAttachment]:
    """Download every anexo published on a licitación ficha page.

    ``detail_url`` is the ``DetailsAcquisition.aspx?qs=...`` link; use
    :func:`build_licitacion_detail_url` when you only have the ``qs`` token.

    Raises :class:`ChileCompraPortalError` if attachment count or aggregate byte
    budgets would be exceeded (fail-closed; no partial success return).
    """
    session = opener or new_portal_opener()
    detail_raw, _ = _portal_request(
        detail_url,
        opener=session,
        timeout=timeout,
        max_bytes=html_max_bytes,
        url_kind="detail",
    )
    listing_urls = extract_attachment_listing_urls(_decode_portal_html(detail_raw))

    downloads: list[DownloadedAttachment] = []
    total_bytes = 0
    for listing_url in listing_urls:
        listing_raw, _ = _portal_request(
            listing_url,
            opener=session,
            timeout=timeout,
            referer=detail_url,
            max_bytes=html_max_bytes,
            url_kind="listing",
        )
        listing_html = _decode_portal_html(listing_raw)
        form_fields = extract_aspnet_form_fields(listing_html)
        for attachment in parse_attachment_listing(listing_html, listing_url=listing_url):
            if len(downloads) >= max_attachment_count:
                raise ChileCompraPortalError(
                    f"Attachment count exceeds max_attachment_count={max_attachment_count}"
                )
            download = download_portal_attachment(
                attachment,
                form_fields,
                opener=session,
                timeout=timeout,
                max_bytes=max_bytes,
            )
            next_total = total_bytes + len(download.content)
            if next_total > max_total_bytes:
                raise ChileCompraPortalError(
                    f"Attachment total bytes exceed max_total_bytes={max_total_bytes}"
                )
            downloads.append(download)
            total_bytes = next_total
    return downloads


@dataclass(frozen=True)
class PortalAttachmentInventory:
    """Every anexo row a ficha exposes, discovered without downloading any body.

    ``__VIEWSTATE`` and the anexo postbacks are bound to the cookie session that
    produced them, so the inventory carries the opener it was built with. That
    binding is runtime-only: it is excluded from ``repr``/equality and is never
    serialized into a shareable artifact.
    """

    detail_url: str
    listing_urls: tuple[str, ...]
    attachments: tuple[PortalAttachment, ...]
    listing_form_fields: dict[str, dict[str, str]]
    session: PortalOpenFn | None = field(default=None, repr=False, compare=False)

    @property
    def attachments_discovered(self) -> int:
        return len(self.attachments)

    def listing_ordinal_of(self, attachment: PortalAttachment) -> int:
        try:
            return self.listing_urls.index(attachment.listing_url)
        except ValueError:
            return -1


def list_licitacion_attachments(
    detail_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    html_max_bytes: int = DEFAULT_PORTAL_HTML_MAX_BYTES,
    opener: PortalOpenFn | None = None,
) -> PortalAttachmentInventory:
    """Enumerate all anexo rows with GETs only; issues zero attachment postbacks.

    Knowing the true discovered count before downloading is what lets callers
    fail a budget check up front instead of silently keeping the first N rows.
    """
    session = opener or new_portal_opener()
    detail_raw, _ = _portal_request(
        detail_url,
        opener=session,
        timeout=timeout,
        max_bytes=html_max_bytes,
        url_kind="detail",
    )
    listing_urls = tuple(extract_attachment_listing_urls(_decode_portal_html(detail_raw)))

    attachments: list[PortalAttachment] = []
    form_fields: dict[str, dict[str, str]] = {}
    for listing_url in listing_urls:
        listing_raw, _ = _portal_request(
            listing_url,
            opener=session,
            timeout=timeout,
            referer=detail_url,
            max_bytes=html_max_bytes,
            url_kind="listing",
        )
        listing_html = _decode_portal_html(listing_raw)
        form_fields[listing_url] = extract_aspnet_form_fields(listing_html)
        # Rows with identical names are distinct documents; never dedupe here.
        attachments.extend(parse_attachment_listing(listing_html, listing_url=listing_url))

    return PortalAttachmentInventory(
        detail_url=detail_url,
        listing_urls=listing_urls,
        attachments=tuple(attachments),
        listing_form_fields=form_fields,
        session=session,
    )


def iter_licitacion_attachments(
    detail_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_ATTACHMENT_MAX_BYTES,
    html_max_bytes: int = DEFAULT_PORTAL_HTML_MAX_BYTES,
    max_attachment_count: int = DEFAULT_MAX_ATTACHMENT_COUNT,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
    opener: PortalOpenFn | None = None,
    inventory: PortalAttachmentInventory | None = None,
) -> Iterator[DownloadedAttachment]:
    """Yield anexos one at a time so callers never hold every body at once.

    The count budget is checked against the discovered inventory *before* any
    body is fetched, so exceeding it raises instead of truncating the corpus.

    A supplied ``inventory`` is always consumed through the session that built
    it; the portal's ``__VIEWSTATE`` would otherwise be replayed against a fresh
    cookie jar and every postback would fail or serve the wrong file.
    """
    if inventory is not None:
        if inventory.session is None:
            raise ChileCompraPortalError(
                "Refusing to consume an inventory with no bound portal session; "
                "build it with list_licitacion_attachments()"
            )
        if opener is not None and opener is not inventory.session:
            raise ChileCompraPortalError(
                "Refusing to consume an inventory through a different portal session "
                "than the one that produced its __VIEWSTATE"
            )
        session = inventory.session
        resolved = inventory
    else:
        session = opener or new_portal_opener()
        resolved = list_licitacion_attachments(
            detail_url,
            timeout=timeout,
            html_max_bytes=html_max_bytes,
            opener=session,
        )
    discovered = resolved.attachments_discovered
    if discovered > max_attachment_count:
        raise ChileCompraPortalError(
            f"{ATTACHMENT_COUNT_BUDGET_EXCEEDED}: discovered {discovered} attachments "
            f"but max_attachment_count={max_attachment_count}"
        )

    total_bytes = 0
    for attachment in resolved.attachments:
        fields = resolved.listing_form_fields.get(attachment.listing_url, {})
        download = download_portal_attachment(
            attachment,
            fields,
            opener=session,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        total_bytes += len(download.content)
        if total_bytes > max_total_bytes:
            raise ChileCompraPortalError(
                f"{TOTAL_BYTES_BUDGET_EXCEEDED}: attachment bytes exceed "
                f"max_total_bytes={max_total_bytes}"
            )
        yield download


def save_licitacion_attachments(
    detail_url: str,
    dest_dir: str | Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_ATTACHMENT_MAX_BYTES,
    html_max_bytes: int = DEFAULT_PORTAL_HTML_MAX_BYTES,
    max_attachment_count: int = DEFAULT_MAX_ATTACHMENT_COUNT,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
    opener: PortalOpenFn | None = None,
) -> list[Path]:
    """Download a ficha's anexos and write them into ``dest_dir``."""
    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for download in fetch_licitacion_attachments(
        detail_url,
        timeout=timeout,
        max_bytes=max_bytes,
        html_max_bytes=html_max_bytes,
        max_attachment_count=max_attachment_count,
        max_total_bytes=max_total_bytes,
        opener=opener,
    ):
        path = destination / download.filename
        counter = 2
        while True:
            try:
                _write_bytes_exclusive(path, download.content)
                break
            except FileExistsError:
                path = destination / f"{Path(download.filename).stem} ({counter}){Path(download.filename).suffix}"
                counter += 1
        if path.resolve().parent != destination.resolve():
            raise ChileCompraPortalError(
                f"Refusing to write attachment outside destination: {path.name!r}"
            )
        written.append(path)
    return written
