"""The discovery sweep behind «En Mercado Público» (`origenlab_api.v2.tenders.discovery`).

Every run, on the tender child's queue:

1. **The list.** One call for every open tender (`licitaciones.json?estado=activas`, ~4,600 rows:
   code, title, estado, closing). A title that passes `title_candidate` — the curated families, or
   anything the catalog sells (its types, makers and models, read fresh every run) — is upserted into
   `procurement.notice`: a new one with the list's row as its head, a known one with its title,
   estado, closing and `last_seen_at` refreshed — its head is kept (it may be a detail).
2. **The details.** Up to `detail_limit` candidates whose head is still the list's are read one by one
   (buyer, items, estimated amount); the detail becomes the head and the old head goes to
   `head_history`. The rest wait for the next run, newest first.
3. **Withdrawn.** A notice seen in the last two days, still due to close, that is no longer in the
   list (desierta, revocada, closed early) gets `disappeared_at` — only when the list came back whole
   (`MIN_LIST_ROWS`), never for a notice a case was opened from. Older notices are left as they are.

Machine evidence only: no case is opened and no event is written; «Preparar» on the page opens the
case through the API's `open-tender-case`. The ticket is read from the environment; it never reaches a
log line (`redact_ticket`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from origenlab_api.v2.tenders.discovery import chile_time, has_detail, load_vocabulary, title_candidate
from origenlab_email_pipeline.chilecompra_api import _codigo_externo, _extract_close_date, _extract_listado

#: Details read per run (each is one API call, spaced by `DETAIL_SPACING_S`).
DETAIL_LIMIT: Final = 60
DETAIL_SPACING_S: Final = 0.5
#: A list shorter than this is treated as a partial answer: nothing is marked withdrawn.
MIN_LIST_ROWS: Final = 500

DISCOVERY_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("procurement.notice", "SELECT"), ("procurement.notice", "INSERT"), ("procurement.notice", "UPDATE"),
    ("catalog.product", "SELECT"), ("crm.organization", "SELECT"),
)
DISCOVERY_REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset({
    ("catalog", "product", "origenlab_worker_select"),
    ("crm", "organization", "origenlab_worker_select"),
    ("procurement", "notice", "origenlab_worker_select"),
    ("procurement", "notice", "origenlab_worker_insert"),
    ("procurement", "notice", "origenlab_worker_update"),
})

FetchList = Callable[[], dict[str, Any]]
FetchDetail = Callable[[str], dict[str, Any] | None]


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _closes_at(row: dict[str, Any]) -> datetime | None:
    return chile_time(_extract_close_date(row))


def _published_at(head: dict[str, Any]) -> datetime | None:
    fechas = head.get("Fechas") if isinstance(head.get("Fechas"), dict) else {}
    return chile_time(fechas.get("FechaPublicacion"))


def _upsert_summary(cur: Any, row: dict[str, Any], code: str) -> None:
    cur.execute(
        """
        insert into procurement.notice (codigo_externo, title, source_status, closes_at, head)
        values (%(code)s, %(title)s, %(status)s, %(closes_at)s, %(head)s::jsonb)
        on conflict (codigo_externo) do update
           set title = coalesce(excluded.title, notice.title),
               source_status = coalesce(excluded.source_status, notice.source_status),
               closes_at = coalesce(excluded.closes_at, notice.closes_at),
               last_seen_at = greatest(now(), notice.first_seen_at),
               disappeared_at = null,
               updated_at = now()
        """,
        {"code": code, "title": _str(row.get("Nombre")), "status": _str(row.get("Estado") or row.get("CodigoEstado")),
         "closes_at": _closes_at(row), "head": json.dumps(row, default=str)},
    )


def _store_detail(cur: Any, code: str, head: dict[str, Any]) -> None:
    buyer = head.get("Comprador") if isinstance(head.get("Comprador"), dict) else {}
    cur.execute(
        """
        update procurement.notice
           set head_history = head_history || jsonb_build_array(head),
               head = %(head)s::jsonb,
               title = coalesce(%(title)s, title),
               buyer_name = coalesce(%(buyer_name)s, buyer_name),
               buyer_code = coalesce(%(buyer_code)s, buyer_code),
               source_status = coalesce(%(status)s, source_status),
               closes_at = coalesce(%(closes_at)s, closes_at),
               published_at = coalesce(%(published_at)s, published_at),
               updated_at = now()
         where codigo_externo = %(code)s
        """,
        {"code": code, "head": json.dumps(head, default=str), "title": _str(head.get("Nombre")),
         "buyer_name": _str(buyer.get("NombreOrganismo")), "buyer_code": _str(buyer.get("CodigoOrganismo")),
         "status": _str(head.get("Estado")), "closes_at": _closes_at(head), "published_at": _published_at(head)},
    )


def run_discovery(conn: Any, fetch_list: FetchList, fetch_detail: FetchDetail, *, now: Callable[[], datetime],
                  detail_limit: int = DETAIL_LIMIT, spacing_s: float = DETAIL_SPACING_S,
                  min_list_rows: int = MIN_LIST_ROWS, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One discovery run. Returns the counts the task logs."""
    started = now()
    with conn.cursor() as cur:
        vocabulary = load_vocabulary(cur)
    listado = _extract_listado(fetch_list())
    candidates: dict[str, dict[str, Any]] = {}
    for row in listado:
        code = _codigo_externo(row)
        if code and title_candidate(_str(row.get("Nombre")), vocabulary):
            candidates[code] = row
    with conn.transaction(), conn.cursor() as cur:
        for code, row in candidates.items():
            _upsert_summary(cur, row, code)
        withdrawn = 0
        if len(listado) >= min_list_rows:
            cur.execute(
                """
                update procurement.notice
                   set disappeared_at = greatest(%(now)s, last_seen_at), updated_at = now()
                 where disappeared_at is null and promoted_opportunity_id is null
                   and last_seen_at > %(now)s - interval '2 days'
                   and (closes_at is null or closes_at > %(now)s)
                   and not (codigo_externo = any(%(codes)s))
                """,
                {"now": started, "codes": [_codigo_externo(r) for r in listado if _codigo_externo(r)]},
            )
            withdrawn = cur.rowcount
        cur.execute(
            """
            select codigo_externo, head from procurement.notice
             where codigo_externo = any(%(codes)s) and disappeared_at is null
             order by first_seen_at desc, codigo_externo
            """,
            {"codes": list(candidates)},
        )
        pending = [code for code, head in cur.fetchall() if not has_detail(head)]
    detailed = failed = 0
    for n, code in enumerate(pending[:detail_limit]):
        if n:
            sleep(spacing_s)
        head = fetch_detail(code)
        if head is None:
            failed += 1
            continue
        with conn.transaction(), conn.cursor() as cur:
            _store_detail(cur, code, head)
        detailed += 1
    return {"listed": len(listado), "candidates": len(candidates), "detailed": detailed,
            "detail_failed": failed, "detail_waiting": max(len(pending) - detail_limit, 0), "withdrawn": withdrawn}


__all__ = ["DETAIL_LIMIT", "DISCOVERY_REQUIRED_POLICIES", "DISCOVERY_REQUIRED_PRIVILEGES", "MIN_LIST_ROWS",
           "run_discovery"]
