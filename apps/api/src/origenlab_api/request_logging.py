"""Request logging middleware — one line per request for Render log tailing.

Emits a single structured log line per request:

    INFO  <METHOD> <path> <status> <duration_ms>ms

* ``<path>`` is the URL path only — no query string, no headers, no cookies,
  no request body, no operator identity.  Keeping latency data and operator
  identity in separate log lines is a deliberate privacy boundary.
* ``<duration_ms>`` is wall-clock time from the start of the Starlette
  dispatch to the response being ready, rounded to the nearest millisecond.
* Auth routes (``/auth/*``) are still logged: method, path and status are not
  secret.  Duration is emitted too — this middleware measures total latency,
  not the side-channel-sensitive database-work time that ResponseTimingMiddleware
  strips from headers for auth routes.
"""
from __future__ import annotations

import logging
import sys
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)
# Uvicorn configures only its own loggers, and an unconfigured one inherits the root's WARNING:
# without this, every line was dropped in production while tests (which pin the level) passed.
# Its own stderr handler at INFO, formatted like uvicorn's lines; still propagates, so pytest's
# caplog sees it too.
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log ``METHOD path status duration_ms`` for every request.

    No cookies, headers, bodies, query strings or identities are logged.
    The line is INFO on its own stderr handler, so it appears in Render's log view whatever the
    root logger is set to.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "%s %s %d %dms",
            request.method,
            request.url.path,   # path only — no query string
            response.status_code,
            duration_ms,
        )
        return response
