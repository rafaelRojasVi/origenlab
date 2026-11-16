"""«Borrador en Gmail» (owner 2026-11-16, Seguimiento build 1): which open cases already have a reply
draft waiting in contacto@'s Gmail, so Hoy does not ask for a follow-up somebody has already written.

Gmail is read for draft ids and thread ids only — never a subject or a body — with the same token as
«Enviar prueba» (gmail.compose). A draft belongs to a case when it answers one of the case's threads.
The list is cached for `CACHE_SECONDS`; when Gmail fails, the last list is kept and the answer says
`available: false` only when there has never been one. Nothing is written anywhere.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: How long a read of the mailbox's drafts stands before Gmail is asked again.
CACHE_SECONDS = 60.0
#: Opens the case's thread in Gmail, where the draft shows under the last message.
GMAIL_THREAD_URL = "https://mail.google.com/mail/u/0/#all/{}"

#: The open cases' threads among these Gmail thread ids (`= any`, one bounded query).
CASE_THREADS_SQL = """
select distinct o.id::text as opportunity_id, sr.payload->>'gmail_thread_id' as thread_id
  from crm.opportunity_evidence oe
  join evidence.source_record sr on sr.id = oe.source_record_id
  join crm.opportunity o on o.id = oe.opportunity_id
 where oe.unlinked_at is null and sr.kind = 'gmail_message'
   and sr.payload->>'gmail_thread_id' = any(%s)
   and o.stage not in ('won', 'lost', 'abandoned')
"""


class DraftLister(Protocol):
    def list_draft_threads(self) -> dict[str, list[str]]: ...


class DraftThreads:
    """The mailbox's draft threads, read at most once per `CACHE_SECONDS` (thread-safe)."""

    def __init__(self, lister: DraftLister, clock: Callable[[], float] = time.monotonic,
                 ttl: float = CACHE_SECONDS) -> None:
        self._lister = lister
        self._clock = clock
        self._ttl = ttl
        self._lock = threading.Lock()
        self._value: dict[str, list[str]] | None = None
        self._read_at = float("-inf")

    def get(self) -> dict[str, list[str]] | None:
        with self._lock:
            if self._value is not None and self._clock() - self._read_at < self._ttl:
                return self._value
            try:
                self._value = self._lister.list_draft_threads()
            except Exception as exc:  # noqa: BLE001 - Gmail down never breaks Hoy; the last list stands
                logger.warning("gmail drafts not read: %s", getattr(exc, "kind", type(exc).__name__))
            self._read_at = self._clock()  # a failure waits a full period too: no retry storm
            return self._value


def case_drafts(threads: Mapping[str, list[str]], rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per case: how many drafts answer its threads, and a link to the thread of one of them. Pure."""
    out: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: (r["opportunity_id"], r["thread_id"])):
        drafts = threads.get(row["thread_id"]) or []
        if not drafts:
            continue
        card = out.setdefault(row["opportunity_id"], {"drafts": 0, "url": GMAIL_THREAD_URL.format(row["thread_id"])})
        card["drafts"] += len(drafts)
    return out


def read_case_drafts(drafts: DraftThreads | None, connect: Callable[..., Any], dsn: str,
                     statement_timeout_ms: int) -> dict[str, Any]:
    """The route's answer: `{available, items: {case_id: {drafts, url}}}`."""
    threads = drafts.get() if drafts is not None else None
    if threads is None:
        return {"available": False, "items": {}}
    if not threads:
        return {"available": True, "items": {}}
    with connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(f"set local statement_timeout = {int(statement_timeout_ms)}")
        cur.execute(CASE_THREADS_SQL, (sorted(threads),))
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        conn.rollback()
    return {"available": True, "items": case_drafts(threads, rows)}


__all__ = ["CACHE_SECONDS", "DraftThreads", "case_drafts", "read_case_drafts"]
