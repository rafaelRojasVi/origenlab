"""Read one tender's detail from the official Mercado Público API, by code."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError

from origenlab_email_pipeline.chilecompra_api import (
    ChileCompraApiError,
    build_licitaciones_url,
    normalize_licitacion_detail_items,
    redact_ticket,
    _extract_listado,
    _fetch_json,
)

from origenlab_api.v2.org_web_suggestions import valid_rut
from origenlab_api.v2.quote_document_identity import normalize_rut

log = logging.getLogger(__name__)

TICKET_ENV_FALLBACK = "CHILECOMPRA_API_TICKET"
_RETRIES_ON_429 = 2
_BACKOFF_SECONDS = 1.0


@dataclass(frozen=True)
class TenderDetail:
    code: str
    title: str
    buyer_name: str | None
    buyer_code: str | None
    buyer_rut: str | None
    closes_at: str | None
    published_at: str | None
    source_status: str | None
    items: list[dict] = field(default_factory=list)
    head: dict = field(default_factory=dict)


def resolve_ticket(configured: str | None) -> str | None:
    """The configured ticket, else the V1 environment variable; blank counts as unset."""
    return (configured or "").strip() or (os.environ.get(TICKET_ENV_FALLBACK) or "").strip() or None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _status_code(exc: BaseException) -> int | None:
    cause = exc.__cause__
    return cause.code if isinstance(cause, HTTPError) else None


def _fetch_with_backoff(url: str, *, ticket: str, timeout: float, urlopen_fn, sleep=time.sleep) -> dict:
    for attempt in range(_RETRIES_ON_429 + 1):
        try:
            return _fetch_json(url, ticket=ticket, timeout=timeout, urlopen_fn=urlopen_fn)
        except ChileCompraApiError as exc:
            if _status_code(exc) == 429 and attempt < _RETRIES_ON_429:
                sleep(_BACKOFF_SECONDS * (2**attempt))
                continue
            raise
    raise AssertionError("unreachable")


def fetch_tender_detail(
    code: str, *, ticket: str | None, urlopen_fn=None, timeout: float = 10.0, sleep=time.sleep
) -> TenderDetail | None:
    if not ticket:
        return None
    url = build_licitaciones_url(ticket=ticket, codigo=code)
    try:
        payload = _fetch_with_backoff(url, ticket=ticket, timeout=timeout, urlopen_fn=urlopen_fn, sleep=sleep)
        listado = _extract_listado(payload)
    except Exception as exc:  # network, HTTP, JSON: the caller only learns "no detail"
        log.warning("tender detail unavailable for %s: %s", code, redact_ticket(str(exc), ticket))
        return None
    if not listado:
        return None
    head = listado[0]
    buyer = head.get("Comprador") if isinstance(head.get("Comprador"), dict) else {}
    rut = normalize_rut(_str(buyer.get("RutUnidad")) or "")
    items = normalize_licitacion_detail_items(head)
    return TenderDetail(
        code=code,
        title=_str(head.get("Nombre")) or code,
        buyer_name=_str(buyer.get("NombreOrganismo")),
        buyer_code=_str(buyer.get("CodigoOrganismo")),
        buyer_rut=rut if rut and valid_rut(rut) else None,
        closes_at=_str(head.get("FechaCierre")),
        published_at=_str((head.get("Fechas") or {}).get("FechaPublicacion") if isinstance(head.get("Fechas"), dict) else None),
        source_status=_str(head.get("Estado")),
        items=items,
        head=head,
    )
