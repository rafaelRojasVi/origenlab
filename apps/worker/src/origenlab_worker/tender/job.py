"""The tender bid preparation job: claim a queued `evidence.bid_preparation` row, run its phase, and
write the result back with what the model calls cost (spec 2026-10-19 §6.4, plan Task 9).

Single flight: one sweep at a time holds the session lock `LOCK_NAME` and runs preparations one by
one, in the tender worker's own child process (ruling R4). That is what keeps the monthly cap: the
cap is checked in a short transaction before each call (`model_spend.ensure_within_cap`, against the
worst case of every attempt the call may bill), the call runs with no transaction open, and its
usage is recorded right after — no second spender can run in between. A row found `running` when
the lock is taken was left by a process that died; it is failed `interrupted`, so the case can ask
again.

Only the `read` phase exists yet (`SUPPORTED_PHASES`); a `fill` row stays queued until the fill
phase is built. Failure codes are the row's `failure_code`, never a model's or an exception's text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Final
from uuid import UUID

from origenlab_api.v2.model_spend import (
    FEATURE_LICITACIONES,
    BudgetReached,
    UnpricedModel,
    dearest_model,
    ensure_within_cap,
    record_usage,
    with_cap_lock,
    worst_case_usd,
)
from origenlab_worker.storage import StorageError
from origenlab_worker.tender.extract import AnnexSetTooLarge, AnnexText, extract_annex, render_for_model
from origenlab_worker.tender.read_model import (
    FALLBACK_WORST_MODELS,
    MAX_TOKENS,
    ReadFailed,
    build_read_request,
    input_tokens_estimate,
    parse_read,
    validate_read,
)
from origenlab_worker.triage_model import ModelReader

LOCK_NAME: Final = "origenlab-worker:bid-preparation"
DOCUMENTS_BUCKET: Final = "crm-documents"
SUPPORTED_PHASES: Final = ("read",)
#: Preparations run per sweep; the next minute's sweep takes the rest.
SWEEP_LIMIT: Final = 5
STATUS_READY, STATUS_FAILED, STATUS_BUDGET = "ready", "failed", "budget_stopped"

#: What the tender worker's connection must hold (`database.verify_worker_connection`): read the case,
#: its anexos and (for the fill phase) the product data sheets; update the preparation; record spend.
#: UPDATE on evidence.bid_preparation is a column grant (status, result, …), which the table-level
#: probe cannot see; its RLS policy is required below and a missing grant fails the first write.
BID_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("evidence.bid_preparation", "SELECT"),
    ("crm.opportunity", "SELECT"), ("crm.opportunity_document", "SELECT"),
    ("platform.model_usage", "SELECT"), ("platform.model_usage", "INSERT"),
    ("catalog.product_document", "SELECT"),
)
BID_REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset({
    ("evidence", "bid_preparation", "origenlab_worker_select"),
    ("evidence", "bid_preparation", "origenlab_worker_update"),
    ("crm", "opportunity", "origenlab_worker_select"),
    ("crm", "opportunity_document", "origenlab_worker_select"),
    ("platform", "model_usage", "origenlab_worker_select"),
    ("platform", "model_usage", "origenlab_worker_insert"),
    ("catalog", "product_document", "origenlab_worker_select"),
})

_CLAIM = """
update evidence.bid_preparation set status = 'running', started_at = now(), updated_at = now()
 where id = (select id from evidence.bid_preparation
              where status = 'queued' and phase = any(%s)
              order by requested_at, id
              for update skip locked
              limit 1)
returning id
"""
_START = """
update evidence.bid_preparation
   set status = 'running', started_at = coalesce(started_at, now()), updated_at = now()
 where id = %s and status in ('queued', 'running') and phase = any(%s)
returning phase, opportunity_id::text, annex_document_ids::text[]
"""
_FINISH = """
update evidence.bid_preparation
   set status = %s, failure_code = %s, result = %s, models = %s, finished_at = now(), updated_at = now()
 where id = %s and status = 'running'
"""
_INTERRUPTED = """
update evidence.bid_preparation
   set status = 'failed', failure_code = 'interrupted', finished_at = now(), updated_at = now()
 where status = 'running'
"""
_ANNEXES = """
select id::text, original_filename, content_type, storage_path, sha256
  from crm.opportunity_document
 where opportunity_id = %s and kind = 'tender_annex' and id = any(%s::uuid[])
"""


@dataclass(frozen=True)
class BidModels:
    read: str
    fill: str
    match: str


class PreparationFailed(Exception):
    def __init__(self, code: str, status: str = STATUS_FAILED, result: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.result = result or {}


class ConnectionLost(Exception):
    """The worker's session died after a model call. Its usage was recorded and its row failed on a
    fresh connection (when one could be opened); the single-flight lock went with the session, so the
    sweep must stop."""


def call_worst_case(request: dict[str, Any], model: str) -> Decimal:
    """What one call may bill: the requested model's worst case plus, when the request carries
    server-side fallbacks, the fallback attempt's — each attempt is billed (claude-api skill)."""
    fallbacks = FALLBACK_WORST_MODELS if request.get("fallbacks") else ()
    return worst_case_usd(model, input_tokens_estimate=input_tokens_estimate(request), max_tokens=MAX_TOKENS,
                          fallback_models=fallbacks)


def _sent_and_maybe_billed(exc: BaseException) -> bool:
    """False only when the API answered with an error status before generating anything (nothing is
    billed); a stream cut after it started, a timeout or a lost connection may have been billed."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the SDK is a worker dependency
        return True
    return not isinstance(exc, anthropic.APIStatusError)


_CONNECTION_ERRORS: tuple[type[BaseException], ...]
try:
    import psycopg as _psycopg

    _CONNECTION_ERRORS = (_psycopg.OperationalError, _psycopg.InterfaceError)
except ImportError:  # pragma: no cover
    _CONNECTION_ERRORS = ()


class BidPreparer:
    """Runs preparations on one verified autocommit worker connection.

    The remote connection has TCP keepalives on (`database.KEEPALIVES`), so it survives a model call
    of several minutes. If it is gone anyway when the usage must be recorded, `reconnect` (a context
    manager factory for a fresh verified connection) records it there and fails the row
    `connection_lost`, and :class:`ConnectionLost` stops the sweep (fail closed).
    """

    def __init__(self, conn: Any, store: Any, reader: ModelReader, models: BidModels, *,
                 now: Callable[[], datetime],
                 reconnect: Callable[[], AbstractContextManager[Any]] | None = None) -> None:
        self._conn = conn
        self._store = store
        self._reader = reader
        self._models = models
        self._now = now
        self._reconnect = reconnect

    # ── the queue ───────────────────────────────────────────────────────────────────────────

    def claim(self) -> UUID | None:
        """The oldest queued preparation of a supported phase, now `running`; None if there is none
        (a row another session holds locked is skipped, not waited for)."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_CLAIM, (list(SUPPORTED_PHASES),))
            row = cur.fetchone()
        return row[0] if row else None

    def recover_interrupted(self) -> int:
        """Fail every `running` row. Call only while holding `LOCK_NAME`: then none is really running."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_INTERRUPTED)
            return cur.rowcount

    def run(self, prep_id: UUID) -> str:
        """Run one preparation to its final status (`ready`, `failed` or `budget_stopped`)."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_START, (prep_id, list(SUPPORTED_PHASES)))
            row = cur.fetchone()
        if row is None:
            with self._conn.cursor() as cur:
                cur.execute("select status from evidence.bid_preparation where id = %s", (prep_id,))
                found = cur.fetchone()
            return found[0] if found else "missing"
        _phase, opportunity_id, annex_ids = row
        models: dict[str, str] = {}
        try:
            result = self._read(prep_id, opportunity_id, annex_ids, models)
        except PreparationFailed as exc:
            self._finish(prep_id, exc.status, exc.code, exc.result, models)
            return exc.status
        except ConnectionLost:
            raise
        except Exception:  # noqa: BLE001 — the row must never stay `running`; the class is not stored
            self._finish(prep_id, STATUS_FAILED, "internal_error", {}, models)
            raise
        self._finish(prep_id, STATUS_READY, None, result, models)
        return STATUS_READY

    def _finish(self, prep_id: UUID, status: str, code: str | None, result: dict[str, Any],
                models: dict[str, str], *, conn: Any = None) -> None:
        from psycopg.types.json import Jsonb

        conn = conn or self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_FINISH, (status, code, Jsonb(result), Jsonb(models), prep_id))

    def _record(self, prep_id: UUID, models: dict[str, str], **usage: Any) -> None:
        """Record one call's usage; on a dead session, on a fresh one, then stop (`ConnectionLost`)."""
        def write(conn: Any) -> None:
            with conn.transaction(), conn.cursor() as cur:
                record_usage(cur, feature=FEATURE_LICITACIONES, step="read", bid_preparation_id=prep_id, **usage)

        try:
            write(self._conn)
            return
        except _CONNECTION_ERRORS:
            if self._reconnect is None:
                raise ConnectionLost("usage_not_recorded") from None
        with self._reconnect() as fresh:
            write(fresh)
            self._finish(prep_id, STATUS_FAILED, "connection_lost", {}, models, conn=fresh)
        raise ConnectionLost("connection_lost")

    # ── the read phase ──────────────────────────────────────────────────────────────────────

    def _annexes(self, opportunity_id: str, annex_ids: list[str]) -> list[AnnexText]:
        with self._conn.cursor() as cur:
            cur.execute(_ANNEXES, (opportunity_id, annex_ids))
            rows = {r[0]: r for r in cur.fetchall()}
        annexes: list[AnnexText] = []
        for doc_id in annex_ids:  # the order the preparation recorded
            row = rows.get(doc_id)
            if row is None:
                raise PreparationFailed("annex_missing")
            _, filename, content_type, path, sha256 = row
            try:
                data = self._store.get(path)
            except StorageError:
                raise PreparationFailed("annex_unavailable") from None
            if hashlib.sha256(data).hexdigest() != sha256:
                raise PreparationFailed("annex_hash_mismatch")
            annexes.append(extract_annex(doc_id, filename, content_type, data))
        return annexes

    def _case(self, opportunity_id: str) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute("select title from crm.opportunity where id = %s", (opportunity_id,))
            row = cur.fetchone()
        return {"titulo": row[0] if row else None}

    def _read(self, prep_id: UUID, opportunity_id: str, annex_ids: list[str],
              models: dict[str, str]) -> dict[str, Any]:
        annexes = self._annexes(opportunity_id, annex_ids)
        readable = [a for a in annexes if a.pages]
        if not readable:
            raise PreparationFailed("no_readable_annex")
        try:
            rendered = render_for_model(readable)
        except AnnexSetTooLarge:
            raise PreparationFailed("annex_set_too_large") from None
        model = self._models.read
        request = build_read_request(rendered, self._case(opportunity_id), model=model)
        attempts = (model, *FALLBACK_WORST_MODELS) if request.get("fallbacks") else (model,)
        try:
            worst = call_worst_case(request, model)
        except UnpricedModel:
            raise PreparationFailed("model_unpriced") from None
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                with_cap_lock(cur, FEATURE_LICITACIONES)
                ensure_within_cap(cur, FEATURE_LICITACIONES, self._now(), worst)
        except BudgetReached:
            raise PreparationFailed("budget_reached", STATUS_BUDGET) from None

        try:
            message = self._reader(request)  # no transaction is open during the call
        except Exception as exc:  # noqa: BLE001 — network, 4xx/5xx, timeout: the code, never the text
            if _sent_and_maybe_billed(exc):
                # What it really cost is unknown: one attempt's worst case at the dearest model.
                conservative = SimpleNamespace(input_tokens=input_tokens_estimate(request), output_tokens=MAX_TOKENS)
                self._record(prep_id, models, model=dearest_model(attempts), usage=conservative)
            raise PreparationFailed("request_failed") from None
        served = getattr(message, "model", None)
        served = served if isinstance(served, str) and served else model
        models["read"] = served
        # Recorded before the answer is judged: a refused or truncated call may still be billed.
        self._record(prep_id, models, model=model, usage=getattr(message, "usage", None), served_model=served)
        try:
            answer = parse_read(message)
        except ReadFailed as exc:
            extra = {"refusal_category": exc.category} if exc.code == "model_refused" else None
            raise PreparationFailed(exc.code, result=extra) from None
        result = validate_read(answer, annexes)
        result["annexes"] = [{"document_id": a.document_id, "filename": a.filename, "outcome": a.outcome}
                             for a in annexes]
        return result


def run_bid_sweep(conn: Any, store: Any, reader: ModelReader, models: BidModels, *,
                  now: Callable[[], datetime], limit: int = SWEEP_LIMIT,
                  reconnect: Callable[[], AbstractContextManager[Any]] | None = None) -> dict[str, Any]:
    """One sweep: take the single-flight lock, fail rows a dead process left `running`, then claim
    and run queued preparations one at a time. Counts only — never a case, a file or a text: an
    unexpected error is reported by its class (a database error's text may quote row values), and
    the sweep stops; a lost connection stops it too (the lock went with the session)."""
    with conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))", (LOCK_NAME,))
        if not cur.fetchone()[0]:
            return {"outcome": "locked"}
    counts: dict[str, Any] = {"outcome": "ran", "interrupted": 0, "claimed": 0, "statuses": {}}
    try:
        preparer = BidPreparer(conn, store, reader, models, now=now, reconnect=reconnect)
        counts["interrupted"] = preparer.recover_interrupted()
        for _ in range(max(1, limit)):
            prep = preparer.claim()
            if prep is None:
                break
            counts["claimed"] += 1
            try:
                status = preparer.run(prep)
            except ConnectionLost:
                counts["outcome"] = "connection_lost"
                return counts
            except Exception:  # noqa: BLE001 — the row is already failed; the next one still runs
                status = "error"
            counts["statuses"][status] = counts["statuses"].get(status, 0) + 1
        return counts
    except Exception as exc:  # noqa: BLE001 — the class only; never the text
        counts.update(outcome="error", error=type(exc).__name__)
        return counts
    finally:
        with suppress(Exception), conn.cursor() as cur:
            cur.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK_NAME,))


__all__ = ["BID_REQUIRED_POLICIES", "BID_REQUIRED_PRIVILEGES", "DOCUMENTS_BUCKET", "LOCK_NAME", "SUPPORTED_PHASES",
           "BidModels", "BidPreparer", "ConnectionLost", "PreparationFailed", "call_worst_case", "run_bid_sweep"]
