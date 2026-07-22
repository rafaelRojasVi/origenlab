"""HTTP / smoke adversarial cases using local fake servers only."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverFailureCategory,
    make_loopback_json_getter,
)


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    body = b'{"status":"ok","sqlite_query_only":true}'
    status = 200

    def log_message(self, *_a: Any) -> None:  # noqa: D401
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()
            return
        if self.mode == "redirect_external":
            self.send_response(302)
            self.send_header("Location", "http://example.com/steal")
            self.end_headers()
            return
        if self.mode == "oversized":
            payload = b"{" + b"x" * 2_000_000 + b"}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.mode == "empty":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return
        if self.mode == "array":
            payload = b"[1,2,3]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.mode == "malformed":
            payload = b"{not-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.mode == "http_error":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"err")
            return
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)


def _serve(mode: str) -> tuple[HTTPServer, str, threading.Thread]:
    _Handler.mode = mode
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}", thread


@pytest.fixture
def stop_server():
    servers: list[HTTPServer] = []

    def _add(server: HTTPServer) -> HTTPServer:
        servers.append(server)
        return server

    yield _add
    for s in servers:
        s.shutdown()


@pytest.mark.parametrize(
    "mode",
    ["redirect", "redirect_external", "oversized", "empty", "array", "malformed", "http_error"],
)
def test_loopback_getter_rejects_hostile_responses(mode: str, stop_server) -> None:
    server, base, _ = _serve(mode)
    stop_server(server)
    getter = make_loopback_json_getter(base, token_provider=lambda: None, max_bytes=1000)
    with pytest.raises(CutoverError) as ei:
        getter(base + "/health")
    assert ei.value.category in {
        CutoverFailureCategory.VERIFY,
        CutoverFailureCategory.SAFETY,
        CutoverFailureCategory.APPLY,
    }
    assert "origenlab_test_token" not in str(ei.value)


def test_loopback_getter_accepts_ok(stop_server) -> None:
    server, base, _ = _serve("ok")
    stop_server(server)
    getter = make_loopback_json_getter(base, token_provider=lambda: None)
    payload = getter(base + "/health")
    assert isinstance(payload, dict)
    assert payload.get("status") == "ok"


@pytest.mark.parametrize(
    "bad_base",
    [
        "https://127.0.0.1:8001",
        "http://8.8.8.8:8001",
        "http://example.com:8001",
    ],
)
def test_loopback_origin_rejects_non_approved_base(bad_base: str) -> None:
    with pytest.raises(CutoverError):
        make_loopback_json_getter(bad_base, token_provider=lambda: None)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@127.0.0.1:8001/health",
        "http://127.0.0.1:8001/health#frag",
        "https://127.0.0.1:8001/health",
        "http://8.8.8.8:8001/health",
    ],
)
def test_loopback_getter_rejects_hostile_request_urls(url: str) -> None:
    getter = make_loopback_json_getter(
        "http://127.0.0.1:8001", token_provider=lambda: None
    )
    with pytest.raises(CutoverError):
        getter(url)


def test_token_sent_but_never_in_error_text(stop_server) -> None:
    server, base, _ = _serve("http_error")
    stop_server(server)
    secret = "origenlab_test_token_ABCDEF123456"

    def provider() -> str:
        return secret

    getter = make_loopback_json_getter(base, token_provider=provider)
    with pytest.raises(CutoverError) as ei:
        getter(base + "/health")
    assert secret not in str(ei.value)
    assert secret not in json.dumps(ei.value.evidence or {})
