"""The discovery sweep behind «En Mercado Público» (`origenlab_api.v2.tenders.discovery`).

Every run, on the tender child's queue:

1. **The list.** One call for every open tender (`licitaciones.json?estado=activas`, ~4,600 rows:
   code, title, estado, closing). **Every** tender not seen before is inserted into
   `procurement.notice` with the list's row as its head — not only the ones whose title matches: a
   tender called «Adquisición de equipamiento» can hold a centrifuge, and only its items say so. A
   known one is refreshed (title, estado, closing, `last_seen_at`) unless the worker already read it
   and found nothing of ours and no person said otherwise: those are left alone.
2. **The details.** Up to `detail_limit` notices still to read are read one by one — matching titles
   first, then the newest — (buyer, items, estimate); the detail becomes the head (the old one goes to
   `head_history`) and the match is stored in `relevance` (`relevance_of`: tier, families, catalog
   words, matched items, the catalog's fingerprint). The rest wait for the next run.
3. **Re-matching.** Up to `rematch_limit` open notices whose stored match is of an older version or
   an older catalog are matched again from their stored head — no API call.
4. **Withdrawn.** A notice seen in the last two days, still due to close, that is no longer in the
   list (desierta, revocada, closed early) gets `disappeared_at` — only when the list came back whole
   (`MIN_LIST_ROWS`), never for a notice a case was opened from. Older notices are left as they are.

Machine evidence only: no case is opened and no event is written; a person's verdict lives in
`crm.notice_review` (the API's `review-notice`). The ticket is read from the environment; it never
reaches a log line (`redact_ticket`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Final

from origenlab_api.v2.tenders.discovery import (
    RELEVANCE_VERSION,
    CatalogVocabulary,
    chile_time,
    load_vocabulary,
    relevance_of,
    title_candidate,
)
from origenlab_email_pipeline.chilecompra_api import _codigo_externo, _extract_close_date, _extract_listado

#: Details read per run (each is one API call, spaced by `DETAIL_SPACING_S`). The first runs after
#: every open tender became a candidate read the backlog (~4,600) at this pace: about eight hours.
DETAIL_LIMIT: Final = 150
DETAIL_SPACING_S: Final = 0.3
#: Stored matches re-made per run when the catalog or the match changed (no API call).
REMATCH_LIMIT: Final = 500
#: A list shorter than this is treated as a partial answer: nothing is marked withdrawn.
MIN_LIST_ROWS: Final = 500

DISCOVERY_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("procurement.notice", "SELECT"), ("procurement.notice", "INSERT"), ("procurement.notice", "UPDATE"),
    ("catalog.product", "SELECT"), ("crm.organization", "SELECT"), ("crm.notice_review", "SELECT"),
)
DISCOVERY_REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset({
    ("catalog", "product", "origenlab_worker_select"),
    ("crm", "organization", "origenlab_worker_select"),
    ("crm", "notice_review", "origenlab_worker_select"),
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


_UPSERT_SUMMARY_SQL = """
        insert into procurement.notice (codigo_externo, title, source_status, closes_at, head)
        values (%(code)s, %(title)s, %(status)s, %(closes_at)s, %(head)s::jsonb)
        on conflict (codigo_externo) do update
           set title = coalesce(excluded.title, notice.title),
               source_status = coalesce(excluded.source_status, notice.source_status),
               closes_at = coalesce(excluded.closes_at, notice.closes_at),
               last_seen_at = greatest(now(), notice.first_seen_at),
               disappeared_at = null,
               updated_at = now()
"""


def _summary_params(row: dict[str, Any], code: str) -> dict[str, Any]:
    return {"code": code, "title": _str(row.get("Nombre")), "status": _str(row.get("Estado") or row.get("CodigoEstado")),
            "closes_at": _closes_at(row), "head": json.dumps(row, default=str)}


def _store_detail(cur: Any, code: str, head: dict[str, Any], relevance: dict[str, Any]) -> None:
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
               relevance = %(relevance)s::jsonb,
               updated_at = now()
         where codigo_externo = %(code)s
        """,
        {"code": code, "head": json.dumps(head, default=str), "title": _str(head.get("Nombre")),
         "buyer_name": _str(buyer.get("NombreOrganismo")), "buyer_code": _str(buyer.get("CodigoOrganismo")),
         "status": _str(head.get("Estado")), "closes_at": _closes_at(head), "published_at": _published_at(head),
         "relevance": json.dumps(relevance, default=str)},
    )


#: What the sweep knows of the listed codes: `skip` when the worker read it, found nothing of ours
#: and no person reviewed it — such a notice is not refreshed.
_KNOWN_SQL = """
select n.codigo_externo,
       (n.relevance is not null and n.relevance->>'tier' is null
        and not exists (select 1 from crm.notice_review r where r.notice_id = n.id)) as skip
  from procurement.notice n
 where n.codigo_externo = any(%(codes)s)
"""

_PENDING_SQL = """
select codigo_externo, title, first_seen_at
  from procurement.notice
 where codigo_externo = any(%(codes)s) and disappeared_at is null
   and relevance is null and not (head ? 'Comprador')
"""

_STALE_SQL = """
select id::text, title, head
  from procurement.notice
 where disappeared_at is null and (closes_at is null or closes_at > %(now)s)
   and head ? 'Comprador'
   and (relevance is null or coalesce((relevance->>'version')::int, 0) <> %(version)s
        or relevance->>'vocabulary' is distinct from %(vocabulary)s)
 order by closes_at asc nulls last
 limit %(limit)s
"""


def _rematch(conn: Any, vocabulary: CatalogVocabulary, at: datetime, limit: int) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_STALE_SQL, {"now": at, "version": RELEVANCE_VERSION, "vocabulary": vocabulary.fingerprint,
                                 "limit": limit})
        stale = cur.fetchall()
        for notice_id, title, head in stale:
            cur.execute("update procurement.notice set relevance = %s::jsonb, updated_at = now() where id = %s::uuid",
                        (json.dumps(relevance_of(title, head, vocabulary, at), default=str), notice_id))
    return len(stale)


def run_discovery(conn: Any, fetch_list: FetchList, fetch_detail: FetchDetail, *, now: Callable[[], datetime],
                  detail_limit: int = DETAIL_LIMIT, spacing_s: float = DETAIL_SPACING_S,
                  rematch_limit: int = REMATCH_LIMIT, min_list_rows: int = MIN_LIST_ROWS,
                  sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One discovery run. Returns the counts the task logs."""
    started = now()
    with conn.cursor() as cur:
        vocabulary = load_vocabulary(cur)
    listado = _extract_listado(fetch_list())
    rows: dict[str, dict[str, Any]] = {}
    for row in listado:
        code = _codigo_externo(row)
        if code:
            rows[code] = row
    with conn.cursor() as cur:
        cur.execute(_KNOWN_SQL, {"codes": list(rows)})
        known = dict(cur.fetchall())
    fresh = [code for code in rows if code not in known]
    refreshed = [code for code in rows if code in known and not known[code]]
    with conn.transaction(), conn.cursor() as cur:
        batch = [_summary_params(rows[code], code) for code in fresh + refreshed]
        if batch:
            cur.executemany(_UPSERT_SUMMARY_SQL, batch)
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
                {"now": started, "codes": list(rows)},
            )
            withdrawn = cur.rowcount
        cur.execute(_PENDING_SQL, {"codes": list(rows)})
        pending_rows = cur.fetchall()
    # Matching titles first, then the newest: a backlog reads what matters most first.
    pending_rows.sort(key=lambda r: (not title_candidate(r[1], vocabulary), -(r[2].timestamp() if r[2] else 0), r[0]))
    pending = [code for code, _, _ in pending_rows]
    detailed = failed = relevant = 0
    for n, code in enumerate(pending[:detail_limit]):
        if n:
            sleep(spacing_s)
        head = fetch_detail(code)
        if head is None:
            failed += 1
            continue
        relevance = relevance_of(_str(head.get("Nombre")) or _str(rows[code].get("Nombre")), head, vocabulary, now())
        with conn.transaction(), conn.cursor() as cur:
            _store_detail(cur, code, head, relevance)
        detailed += 1
        relevant += relevance["tier"] is not None
    rematched = _rematch(conn, vocabulary, now(), rematch_limit) if rematch_limit else 0
    return {"listed": len(listado), "new": len(fresh), "refreshed": len(refreshed), "detailed": detailed,
            "relevant": relevant, "detail_failed": failed, "detail_waiting": max(len(pending) - detail_limit, 0),
            "rematched": rematched, "withdrawn": withdrawn}


__all__ = ["DETAIL_LIMIT", "DISCOVERY_REQUIRED_POLICIES", "DISCOVERY_REQUIRED_PRIVILEGES", "MIN_LIST_ROWS",
           "REMATCH_LIMIT", "run_discovery"]
