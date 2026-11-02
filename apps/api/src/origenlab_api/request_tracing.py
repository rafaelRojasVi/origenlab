"""Per-request tracing middleware — slow request detection and N+1 query visibility.

Emits ONE structured log line per request:

    DEBUG GET /v2/cases/{opportunity_id} 200 45ms 3q 12ms

* Route template, not the raw path (``/v2/cases/{opportunity_id}`` not
  ``/v2/cases/abc-123``).  Raw paths can carry UUIDs, e-mail addresses and
  sequential IDs; the repo is public and logs must not carry personal data.
  Query strings and request bodies are never logged.
* Level is INFO when the request exceeds the slow threshold
  (``ORIGENLAB_REQUEST_SLOW_THRESHOLD_MS``, default 500 ms) or when the query
  count exceeds the N+1 threshold (``ORIGENLAB_REQUEST_HIGH_QUERY_COUNT``,
  default 25).  Otherwise DEBUG.  Health check paths always use DEBUG.
* Extends the existing ``Server-Timing`` header with a ``db`` entry so the
  owner can read DB cost in browser DevTools:
  ``app;dur=12.5, db;dur=8.3;desc="3 queries"``.
  Auth paths (``/auth/*``) are excluded from both the DB entry and the log line
  (same exclusion policy as ``ResponseTimingMiddleware``).

Pure ASGI — not BaseHTTPMiddleware — so it does not buffer response bodies and
does not interact with ``GZipMiddleware``.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Any

from origenlab_api.query_counter import get_request_stats, reset_request_stats
from origenlab_api.response_timing import is_untimed_path

logger = logging.getLogger(__name__)
# Match the pattern in request_logging.py: add a stderr handler at INFO so the
# line appears in Render logs even when uvicorn has not configured this logger.
# DEBUG lines reach pytest's caplog but not production stderr (filtered by level).
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    _handler.setLevel(logging.INFO)
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)  # emit DEBUG too; the handler filters at INFO

#: Paths whose timing is never logged at INFO (health check probes every 30 s).
_HEALTH_PATH = "/health"

_DEFAULT_SLOW_MS: int = 500
_DEFAULT_HIGH_QUERY_COUNT: int = 25


def _route_template(scope: dict) -> str:  # type: ignore[type-arg]
    """Return the matched route path template, or the raw path as a fallback.

    The route template (e.g. ``/v2/cases/{opportunity_id}``) is written to
    ``scope["route"]`` by Starlette's router after the endpoint is dispatched.
    Using the template keeps personal data — UUIDs, IDs, e-mail addresses that
    appear as path segments — out of every log line.
    """
    route = scope.get("route")
    if route is not None:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            return path
    # No route matched (e.g. 404/405) or route not set yet.  Raw path only —
    # no query string.  For unmatched paths the raw value is typically a probe
    # or a typo, not a real operator request with personal-data identifiers.
    return scope.get("path", "?")


class RequestTracingMiddleware:
    """Pure-ASGI per-request tracing: slow logs, query counts, Server-Timing."""

    __slots__ = ("_app", "_slow_ms", "_high_query")

    def __init__(
        self,
        app: Any,
        *,
        slow_request_threshold_ms: int = _DEFAULT_SLOW_MS,
        high_query_count_threshold: int = _DEFAULT_HIGH_QUERY_COUNT,
    ) -> None:
        self._app = app
        self._slow_ms = int(slow_request_threshold_ms)
        self._high_query = int(high_query_count_threshold)

    async def __call__(
        self, scope: dict, receive: Any, send: Any  # type: ignore[type-arg]
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Reset the query counter for this request's context.
        reset_request_stats()
        started = time.perf_counter()

        # status_code is captured from the http.response.start message so we
        # can log it after the app finishes (the response object is gone by then).
        status_holder: list[int] = [0]

        async def _send(message: dict) -> None:  # type: ignore[type-arg]
            if message["type"] == "http.response.start":
                status_holder[0] = message.get("status", 0)
                # Append DB timing to Server-Timing header (except auth routes).
                path = scope.get("path", "")
                if not is_untimed_path(path):
                    message = _append_db_server_timing(message)
            await send(message)

        try:
            await self._app(scope, receive, _send)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1_000
            self._log(scope, status_holder[0], elapsed_ms)

    def _log(self, scope: dict, status: int, elapsed_ms: float) -> None:  # type: ignore[type-arg]
        try:
            stats = get_request_stats()
            query_count = stats.query_count if stats is not None else 0
            total_db_ms = stats.total_db_ms if stats is not None else 0.0

            method = scope.get("method", "?")
            template = _route_template(scope)
            path = scope.get("path", "")

            is_slow = elapsed_ms >= self._slow_ms
            is_high_query = query_count >= self._high_query
            is_health = path == _HEALTH_PATH

            level = logging.DEBUG
            if (is_slow or is_high_query) and not is_health:
                level = logging.INFO

            if logger.isEnabledFor(level):
                logger.log(
                    level,
                    "%s %s %d %.0fms %dq %.0fms",
                    method,
                    template,
                    status,
                    elapsed_ms,
                    query_count,
                    total_db_ms,
                )
        except Exception:  # noqa: BLE001
            pass  # logging must never raise into a request


def _append_db_server_timing(message: dict) -> dict:  # type: ignore[type-arg]
    """Add ``db;dur=X;desc="N queries"`` to the ``Server-Timing`` header.

    Reads current query stats from the ContextVar.  Returns the message
    unchanged (same dict, no copy) when stats are unavailable or on any error.
    """
    try:
        stats = get_request_stats()
        db_count = stats.query_count if stats is not None else 0
        db_ms = stats.total_db_ms if stats is not None else 0.0

        db_entry = f'db;dur={db_ms:.2f};desc="{db_count} queries"'
        db_bytes = db_entry.encode()

        raw_headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
        new_headers: list[tuple[bytes, bytes]] = []
        found = False
        for name, value in raw_headers:
            if name.lower() == b"server-timing":
                value = value + b", " + db_bytes
                found = True
            new_headers.append((name, value))
        if not found:
            new_headers.append((b"server-timing", db_bytes))
        return {**message, "headers": new_headers}
    except Exception:  # noqa: BLE001
        return message  # never raise from header manipulation
