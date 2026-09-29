"""Read-only response timing headers (observation only) — never on sign-in routes."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

SERVER_TIMING_HEADER = "Server-Timing"
PROCESS_TIME_MS_HEADER = "X-Process-Time-Ms"

#: No response under these paths carries a timing header. How long a PIN check, a profile
#: selection, a session verification, the Google callback, a profile clear or a logout took is
#: not something to publish: it is the side channel `v2/profile_auth.py` documents (a lock, or
#: whether a profile id exists, can be told apart by the database work). Wall-clock time stays
#: observable to a caller; this only stops the server from measuring it for them.
UNTIMED_PATH_PREFIXES = ("/auth/",)


def is_untimed_path(path: str) -> bool:
    return path == "/auth" or path.startswith(UNTIMED_PATH_PREFIXES)


class ResponseTimingMiddleware(BaseHTTPMiddleware):
    """Attach Server-Timing + X-Process-Time-Ms without logging request content.

    Sign-in routes (`/auth/*`) get neither header, on success and on refusal alike.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        if is_untimed_path(request.url.path):
            for name in (PROCESS_TIME_MS_HEADER, SERVER_TIMING_HEADER):
                if name in response.headers:
                    del response.headers[name]
            return response
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        response.headers[PROCESS_TIME_MS_HEADER] = f"{elapsed_ms:.2f}"
        response.headers[SERVER_TIMING_HEADER] = f"app;dur={elapsed_ms:.2f}"
        return response
