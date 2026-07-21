"""Focused tests for PR-A: readonly-smoke bounded loopback getter + exit-143 gates.

These tests never touch production SQLite, services, or the real :8001 API. The
only HTTP traffic is against an ephemeral local ``http.server`` bound to
127.0.0.1 (loopback, test-server scoped), which the requirements explicitly
allow.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from origenlab_email_pipeline.qa.dashboard_api_readiness import API_AUTH_TOKEN_HEADER
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    API_ACTIVITY_RUNNING,
    API_ACTIVITY_STOPPED,
    CutoverError,
    FilesystemAdapters,
    classify_api_activity,
    make_loopback_json_getter,
)


class _Handler(BaseHTTPRequestHandler):
    # Populated per-server via the factory below.
    routes: dict[str, dict[str, Any]] = {}
    received_headers: list[Any] = []

    def log_message(self, *args: Any) -> None:  # silence test noise
        return

    def _safe_write(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
        except OSError:
            # Client bailed (e.g. timeout test closed the socket) — ignore.
            pass

    def do_GET(self) -> None:  # noqa: N802
        # Keep the case-insensitive header object (urllib normalizes header case).
        type(self).received_headers.append(self.headers)
        route = type(self).routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            self._safe_write(b"{}")
            return
        if route.get("sleep"):
            time.sleep(route["sleep"])
        if route.get("redirect"):
            self.send_response(302)
            self.send_header("Location", route["redirect"])
            self.end_headers()
            return
        body = route["body"]
        self.send_response(route.get("status", 200))
        self.send_header("Content-Type", route.get("content_type", "application/json"))
        self.end_headers()
        self._safe_write(body)


def _make_server(routes: dict[str, dict[str, Any]]) -> tuple[ThreadingHTTPServer, str, type]:
    handler = type("_ScopedHandler", (_Handler,), {"routes": dict(routes), "received_headers": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://127.0.0.1:{port}"
    return server, base_url, handler


def _json(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _ok_routes() -> dict[str, dict[str, Any]]:
    return {
        "/health": {"body": _json({"status": "ok", "sqlite_ok": True})},
        "/operator/status": {"body": _json({"status": "ok", "sqlite": {"ready": True}})},
        "/operator/automation-status": {
            "body": _json({"status": "ok", "mail": {"paused": False}})
        },
    }


# --- Test 1: real FilesystemAdapters smoke with no injected getter ---
def test_real_adapters_smoke_without_injected_getter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIGENLAB_API_AUTH_TOKEN", raising=False)
    server, base_url, _ = _make_server(_ok_routes())
    try:
        adapters = FilesystemAdapters(http_get=None)
        result = adapters.http_smoke(base_url, expected_fingerprint="1:2:3:abc")
        assert result["/health"]["ok"] is True
        assert result["/operator/status"]["ok"] is True
        assert result["/operator/automation-status"]["ok"] is True
        assert result["expected_fingerprint"] == "1:2:3:abc"
    finally:
        server.shutdown()


# --- Test 2: loopback-origin enforcement (+ userinfo/fragment) ---
def test_loopback_origin_enforced() -> None:
    # Non-loopback base URL is refused at build time.
    with pytest.raises(CutoverError):
        make_loopback_json_getter("http://example.com:8001")
    # HTTPS (non-http) base URL is refused at build time.
    with pytest.raises(CutoverError):
        make_loopback_json_getter("https://127.0.0.1:8001")
    # A getter bound to one loopback port refuses a different port.
    getter = make_loopback_json_getter("http://127.0.0.1:8001")
    with pytest.raises(CutoverError) as exc:
        getter("http://127.0.0.1:9002/health")
    assert "loopback origin" in str(exc.value).lower()
    # And refuses a non-loopback host outright.
    with pytest.raises(CutoverError):
        getter("http://10.0.0.5:8001/health")


def test_userinfo_and_fragment_rejected() -> None:
    getter = make_loopback_json_getter("http://127.0.0.1:8001")
    with pytest.raises(CutoverError) as exc1:
        getter("http://user:pass@127.0.0.1:8001/health")
    assert "userinfo" in str(exc1.value).lower()
    with pytest.raises(CutoverError) as exc2:
        getter("http://127.0.0.1:8001/health#fragment")
    assert "fragment" in str(exc2.value).lower()


# --- Test 3: redirect rejection ---
def test_redirect_rejected() -> None:
    server, base_url, _ = _make_server(
        {"/health": {"redirect": "http://127.0.0.1:1/evil"}}
    )
    try:
        getter = make_loopback_json_getter(base_url)
        with pytest.raises(CutoverError) as exc:
            getter(base_url + "/health")
        assert "redirect" in str(exc.value).lower()
    finally:
        server.shutdown()


# --- Test 4: response-size and timeout bounds ---
def test_response_size_bound() -> None:
    server, base_url, _ = _make_server({"/big": {"body": b"x" * 5000}})
    try:
        getter = make_loopback_json_getter(base_url, max_bytes=100)
        with pytest.raises(CutoverError) as exc:
            getter(base_url + "/big")
        assert "byte bound" in str(exc.value).lower()
    finally:
        server.shutdown()


def test_timeout_bound() -> None:
    server, base_url, _ = _make_server(
        {"/slow": {"body": _json({"status": "ok"}), "sleep": 1.0}}
    )
    try:
        getter = make_loopback_json_getter(base_url, timeout=0.2)
        with pytest.raises(CutoverError) as exc:
            getter(base_url + "/slow")
        assert "timed out" in str(exc.value).lower() or "failed" in str(exc.value).lower()
    finally:
        server.shutdown()


def test_per_endpoint_timeout_status_gets_longer_allowance() -> None:
    # /operator/status tolerates a slow-but-healthy response; /health does not.
    server, base_url, _ = _make_server(
        {
            "/operator/status": {"body": _json({"status": "ok"}), "sleep": 0.5},
            "/health": {"body": _json({"status": "ok"}), "sleep": 0.5},
        }
    )
    try:
        getter = make_loopback_json_getter(base_url, timeout=0.2, status_timeout=3.0)
        # Longer bound applies to /operator/status -> succeeds despite 0.5s sleep.
        assert getter(base_url + "/operator/status")["status"] == "ok"
        # Short bound applies to /health -> times out at 0.2s.
        with pytest.raises(CutoverError):
            getter(base_url + "/health")
    finally:
        server.shutdown()


# --- Test 5: invalid status / malformed JSON / non-object JSON ---
def test_non_200_status_rejected() -> None:
    server, base_url, _ = _make_server(
        {"/err": {"status": 500, "body": _json({"status": "error"})}}
    )
    try:
        getter = make_loopback_json_getter(base_url)
        with pytest.raises(CutoverError) as exc:
            getter(base_url + "/err")
        assert "non-success" in str(exc.value).lower() or "200" in str(exc.value)
    finally:
        server.shutdown()


def test_malformed_json_rejected() -> None:
    server, base_url, _ = _make_server({"/bad": {"body": b"not-json{{"}})
    try:
        getter = make_loopback_json_getter(base_url)
        with pytest.raises(CutoverError) as exc:
            getter(base_url + "/bad")
        assert "valid json" in str(exc.value).lower()
    finally:
        server.shutdown()


def test_non_object_json_rejected() -> None:
    server, base_url, _ = _make_server({"/arr": {"body": _json([1, 2, 3])}})
    try:
        getter = make_loopback_json_getter(base_url)
        with pytest.raises(CutoverError) as exc:
            getter(base_url + "/arr")
        assert "object" in str(exc.value).lower()
    finally:
        server.shutdown()


# --- Test 6: authentication header present but never leaked ---
def test_auth_header_present_but_not_leaked(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "s3cr3t-smoke-token-DO-NOT-LEAK"
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", secret)
    # A route that succeeds (to capture header) and one that fails (to check leaks).
    server, base_url, handler = _make_server(
        {
            "/health": {"body": _json({"status": "ok"})},
            "/arr": {"body": _json([1, 2, 3])},
        }
    )
    try:
        getter = make_loopback_json_getter(base_url)
        getter(base_url + "/health")
        # Matches the real apps/api contract: both Bearer and X-OriginLab-API-Key.
        assert any(
            h.get(API_AUTH_TOKEN_HEADER) == secret for h in handler.received_headers
        ), "X-OriginLab-API-Key header was not sent"
        assert any(
            (h.get("Authorization") or "") == f"Bearer {secret}"
            for h in handler.received_headers
        ), "Authorization: Bearer header was not sent"
        with caplog.at_level("DEBUG"):
            with pytest.raises(CutoverError) as exc:
                getter(base_url + "/arr")
        assert secret not in str(exc.value)
        assert secret not in str(getattr(exc.value, "evidence", "") or "")
        assert secret not in caplog.text
    finally:
        server.shutdown()


def test_no_secret_or_body_leak_in_any_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "leak-check-token-XYZ"
    body_sentinel = "SENSITIVE_BODY_MARKER_9999"
    monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", secret)
    server, base_url, _ = _make_server(
        {
            "/e500": {"status": 500, "body": json.dumps({"detail": body_sentinel}).encode()},
            "/bad": {"body": (body_sentinel + "{{").encode()},
            "/arr": {"body": json.dumps([body_sentinel]).encode()},
            "/big": {"body": (body_sentinel + "x" * 5000).encode()},
        }
    )
    try:
        getter = make_loopback_json_getter(base_url, max_bytes=50)
        for path in ("/e500", "/bad", "/arr", "/big"):
            with caplog.at_level("DEBUG"):
                with pytest.raises(CutoverError) as exc:
                    getter(base_url + path)
            msg = str(exc.value) + str(getattr(exc.value, "evidence", "") or "")
            assert secret not in msg, f"token leaked for {path}"
            assert body_sentinel not in msg, f"body leaked for {path}"
        assert secret not in caplog.text
        assert body_sentinel not in caplog.text
    finally:
        server.shutdown()


# --- Test 10: exit-143 failed-but-not-running classification ---
def test_exit_143_failed_classified_as_stopped() -> None:
    # Intentional systemctl stop leaves 'failed' + SIGTERM/143, no PID, no listener.
    result = classify_api_activity(
        is_active_text="failed",
        main_pid=0,
        listener_present=False,
        sub_state="failed",
    )
    assert result["state"] == API_ACTIVITY_STOPPED
    assert result["running"] is False
    assert result["stopped"] is True
    assert result["ambiguous"] is False  # 'failed' is a KNOWN stopped state
    # inactive/dead are likewise known-stopped, not ambiguous.
    for text in ("inactive", "dead"):
        r = classify_api_activity(
            is_active_text=text, main_pid=0, listener_present=False
        )
        assert r["stopped"] is True
        assert r["ambiguous"] is False


# --- Test 11: genuine running / unknown-ambiguous states fail closed ---
def test_running_and_unknown_states_fail_closed() -> None:
    assert classify_api_activity(
        is_active_text="active", main_pid=1234, listener_present=True
    )["state"] == API_ACTIVITY_RUNNING
    # PID + listener even when text is transitional/unknown => running.
    assert classify_api_activity(
        is_active_text="activating", main_pid=777, listener_present=True
    )["running"] is True
    assert classify_api_activity(
        is_active_text="unknown", main_pid=999, listener_present=True
    )["running"] is True
    # Unknown/unreachable text with no PID/listener => AMBIGUOUS, fail closed.
    for text in ("unknown", "", None):
        amb = classify_api_activity(
            is_active_text=text, main_pid=0, listener_present=False
        )
        assert amb["ambiguous"] is True
        assert amb["running"] is True
    # A lingering listener alone (no PID) is still running.
    assert classify_api_activity(
        is_active_text="failed", main_pid=0, listener_present=True
    )["running"] is True
    # Genuine failure signals are surfaced, never masked.
    crash_loop = classify_api_activity(
        is_active_text="activating",
        main_pid=0,
        listener_present=False,
        sub_state="auto-restart",
    )
    assert crash_loop["genuine_failure_signal"] is True
    active_oom = classify_api_activity(
        is_active_text="active",
        main_pid=42,
        listener_present=True,
        sub_state="oom-kill",
    )
    assert active_oom["running"] is True
    assert active_oom["genuine_failure_signal"] is True
