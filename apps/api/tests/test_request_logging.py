"""Request logging middleware — no PII in log lines, correct format.

Checked properties:
* One INFO line per request: METHOD path status duration_ms.
* Path only — no query string, no cookies, no headers, no body, no identity.
* Log level is INFO so lines appear at the default Render log level.
* Duration is a non-negative integer (milliseconds).
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.request_logging import RequestLoggingMiddleware


def _app_with_routes() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/v2/workspace/overview")
    def overview():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/v2/workspace/search")
    def search(q: str = ""):
        return {"q": q}

    return app


def test_logging_middleware_emits_one_info_line_per_request(caplog) -> None:
    """Each request produces exactly one INFO log line."""
    app = _app_with_routes()
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="origenlab_api.request_logging"):
        client.get("/health")
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO


def test_log_line_contains_method_path_status_duration(caplog) -> None:
    """The log line must contain method, path, status code, and duration_ms."""
    app = _app_with_routes()
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="origenlab_api.request_logging"):
        client.get("/v2/workspace/overview")
    assert caplog.records, "no log record emitted"
    msg = caplog.records[0].getMessage()
    assert "GET" in msg
    assert "/v2/workspace/overview" in msg
    assert "200" in msg
    # Duration: last token ends with "ms"
    assert msg.strip().endswith("ms")
    duration_token = msg.strip().split()[-1]
    assert duration_token.endswith("ms")
    assert int(duration_token[:-2]) >= 0


def test_log_line_omits_query_string(caplog) -> None:
    """Query parameters must not appear in the logged path."""
    app = _app_with_routes()
    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="origenlab_api.request_logging"):
        client.get("/v2/workspace/search?q=secret@example.com")
    assert caplog.records, "no log record emitted"
    msg = caplog.records[0].getMessage()
    assert "secret" not in msg, "query string must not appear in log line"
    assert "/v2/workspace/search" in msg


def test_log_line_is_logged_for_every_status_code(caplog) -> None:
    """Auth routes (which return 401 when unauthenticated) are also logged."""
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/auth/session")
    def session_route():
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="no credential")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO, logger="origenlab_api.request_logging"):
        client.get("/auth/session")
    assert caplog.records, "no log record emitted for 401 response"
    msg = caplog.records[0].getMessage()
    assert "401" in msg


def test_the_line_reaches_the_server_log_as_uvicorn_configures_logging() -> None:
    """Production, reproduced: a fresh process, uvicorn's own logging config, one request.

    The tests above pin the logger's level themselves (`caplog.at_level(..., logger=...)`), so
    they passed while production printed nothing: uvicorn configures only its own loggers, and an
    unconfigured logger inherits the root's WARNING. This one reads what reaches stderr.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import logging.config
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from uvicorn.config import LOGGING_CONFIG
        logging.config.dictConfig(LOGGING_CONFIG)  # what uvicorn does at startup
        from origenlab_api.request_logging import RequestLoggingMiddleware
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware)
        @app.get("/ping")
        def ping():
            return {"ok": True}
        TestClient(app).get("/ping?secret=1")
        """
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = [line for line in out.stderr.splitlines() if "/ping" in line]
    assert len(lines) == 1, out.stderr
    assert "GET /ping 200 " in lines[0] and lines[0].rstrip().endswith("ms")
    assert "secret" not in out.stderr
