"""Read-only response timing headers (observation only)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

SERVER_TIMING_HEADER = "Server-Timing"
PROCESS_TIME_MS_HEADER = "X-Process-Time-Ms"


class ResponseTimingMiddleware(BaseHTTPMiddleware):
    """Attach Server-Timing + X-Process-Time-Ms without logging request content."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        response.headers[PROCESS_TIME_MS_HEADER] = f"{elapsed_ms:.2f}"
        response.headers[SERVER_TIMING_HEADER] = f"app;dur={elapsed_ms:.2f}"
        return response
