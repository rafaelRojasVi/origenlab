"""Contract test: the email-pipeline readonly-smoke getter matches apps/api auth.

Proves the loopback smoke getter emits auth headers that the REAL
`ApiTokenAuthMiddleware`/`extract_api_token` in `origenlab_api.http_security`
accepts, without exposing any real secret (a dummy token is used and never
printed). apps/api depends on origenlab-email-pipeline, so importing the getter
here is the correct place to bind the two contracts together.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from origenlab_api.http_security import (
    _API_KEY_HEADER,
    api_token_is_valid,
    extract_api_token,
)
from origenlab_email_pipeline.qa.dashboard_api_readiness import API_AUTH_TOKEN_HEADER
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    make_loopback_json_getter,
)

_DUMMY_TOKEN = "contract-only-token-not-a-secret"


class _CaptureHandler(BaseHTTPRequestHandler):
    captured: list[Any] = []

    def log_message(self, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        type(self).captured.append(self.headers)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(b'{"status": "ok"}')
        except OSError:
            pass


def test_smoke_getter_header_name_matches_api_constant() -> None:
    # The header the getter/tooling uses is exactly the one the API validates.
    assert API_AUTH_TOKEN_HEADER == _API_KEY_HEADER


def test_smoke_getter_headers_accepted_by_real_api_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("starlette.requests")
    from starlette.requests import Request

    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", _DUMMY_TOKEN)
    handler = type("_H", (_CaptureHandler,), {"captured": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, port = server.server_address
        base_url = f"http://127.0.0.1:{port}"
        getter = make_loopback_json_getter(base_url)
        getter(base_url + "/health")
    finally:
        server.shutdown()

    assert handler.captured, "getter made no request"
    sent = handler.captured[0]
    bearer = sent.get("Authorization")
    api_key = sent.get(_API_KEY_HEADER)

    def _request_with(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "method": "GET", "path": "/operator/status", "headers": raw})

    # Both header shapes the getter sends must be accepted by the real API path.
    assert extract_api_token(_request_with({"Authorization": bearer})) == _DUMMY_TOKEN
    assert extract_api_token(_request_with({_API_KEY_HEADER: api_key})) == _DUMMY_TOKEN
    assert api_token_is_valid(
        extract_api_token(_request_with({"Authorization": bearer, _API_KEY_HEADER: api_key})),
        _DUMMY_TOKEN,
    )
