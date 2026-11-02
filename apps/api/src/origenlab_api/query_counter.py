"""Per-request query counter — lightweight observability, no external dependencies.

Tracks query count and total database time for the current request via a
ContextVar.  Any code path that executes a database query calls ``record_query``
to add to the running totals; the tracing middleware reads the final tally after
the route handler finishes.

Counters must never raise into a request.  Every function here is fail-safe and
silently no-ops when called outside a traced request context.

Psycopg integration
-------------------
``CountingCursor`` is a ``psycopg.Cursor`` subclass that calls ``record_query``
on every ``execute`` / ``executemany``.  It is ``None`` when psycopg is not
installed (V1-only deployments).  Callers must guard ``if CountingCursor is not
None`` before passing it as a ``cursor_factory``.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class RequestQueryStats:
    """Mutable per-request query statistics.

    Shared via ContextVar and mutated in-place so both ``record_query`` and the
    middleware that reads the final totals see the same object.
    """

    query_count: int = 0
    total_db_ms: float = 0.0


# Active statistics object for the current request, or None outside a traced request.
_current_stats: ContextVar[RequestQueryStats | None] = ContextVar(
    "origenlab_request_query_stats", default=None
)


def reset_request_stats() -> RequestQueryStats:
    """Create a fresh stats object and install it as the current request's context."""
    stats = RequestQueryStats()
    _current_stats.set(stats)
    return stats


def get_request_stats() -> RequestQueryStats | None:
    """Return the current request's stats, or None if not inside a traced request."""
    return _current_stats.get()


def record_query(duration_ms: float = 0.0) -> None:
    """Increment the query counter and accumulate ``duration_ms``.

    Silently does nothing when called outside a traced request context.
    Never raises — counting code must not interrupt a request.
    """
    try:
        stats = _current_stats.get()
        if stats is None:
            return
        stats.query_count += 1
        stats.total_db_ms += max(0.0, float(duration_ms))
    except Exception:  # noqa: BLE001
        pass  # counting must not raise


# ---------------------------------------------------------------------------
# Psycopg 3 integration (optional — None when psycopg is not installed)
# ---------------------------------------------------------------------------

def _build_counting_cursor():  # type: ignore[return]
    """Return CountingCursor subclass if psycopg is importable, else None."""
    try:
        import psycopg  # noqa: PLC0415
    except ImportError:
        return None

    class CountingCursor(psycopg.Cursor):  # type: ignore[type-arg]
        """psycopg Cursor subclass that records each execute in the request counter."""

        def execute(
            self,
            query: "psycopg.abc.Query",
            params: "psycopg.abc.Params | None" = None,
            *,
            prepare: "bool | None" = None,
            binary: "bool | None" = None,
        ) -> "CountingCursor":
            started = time.perf_counter()
            try:
                return super().execute(query, params, prepare=prepare, binary=binary)
            finally:
                record_query((time.perf_counter() - started) * 1_000)

        def executemany(
            self,
            query: "psycopg.abc.Query",
            params_seq: "object",
            *,
            returning: bool = False,
        ) -> None:
            started = time.perf_counter()
            try:
                return super().executemany(query, params_seq, returning=returning)
            finally:
                # One executemany call = one logical query (one network round-trip
                # to the server, regardless of batch size).
                record_query((time.perf_counter() - started) * 1_000)

    return CountingCursor


#: ``psycopg.Cursor`` subclass that records query timing, or ``None``.
#: Check ``if CountingCursor is not None`` before using as ``cursor_factory``.
CountingCursor = _build_counting_cursor()
