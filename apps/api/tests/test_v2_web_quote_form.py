"""Tests for build_web_quote_message, web_quote_form route, caps, and idempotency."""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from email import message_from_bytes
import email.policy
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Message builder tests
# ---------------------------------------------------------------------------


def test_build_web_quote_message_round_trip() -> None:
    from origenlab_api.v2.gmail_send import build_web_quote_message

    raw = build_web_quote_message(
        reply_to="ana@uchile.cl",
        institution="U. de Chile",
        need="Autoclave 50 L",
        extra={},
        submitted_at="2026-10-18T15:00:00-03:00",
    )
    msg = message_from_bytes(raw, policy=email.policy.default)
    assert msg["Reply-To"].addresses[0].addr_spec == "ana@uchile.cl"
    assert msg["Subject"] == "Solicitud de cotización — sitio web — U. de Chile"
    assert msg["X-OrigenLab-Origin"] == "web-quote-form/v1"
    assert msg["From"].addresses[0].addr_spec == "contacto@origenlab.cl"
    assert msg["To"].addresses[0].addr_spec == "contacto@origenlab.cl"
    assert "List-Unsubscribe" not in msg


def test_header_injection_in_institution_is_refused() -> None:
    from origenlab_api.v2.gmail_send import build_web_quote_message

    with pytest.raises(ValueError):
        build_web_quote_message(
            reply_to="a@b.invalid",
            institution="Hack\r\nBcc: bad@evil.invalid",
            need="x",
            extra={},
            submitted_at="2026-10-18T15:00:00-03:00",
        )


def test_invalid_reply_to_is_refused() -> None:
    from origenlab_api.v2.gmail_send import build_web_quote_message

    with pytest.raises(ValueError):
        build_web_quote_message(
            reply_to="not-an-email",
            institution="U. de Chile",
            need="Autoclave",
            extra={},
            submitted_at="2026-10-18T15:00:00-03:00",
        )


def test_institution_truncated_to_120_in_subject() -> None:
    from origenlab_api.v2.gmail_send import build_web_quote_message

    long_name = "A" * 200
    raw = build_web_quote_message(
        reply_to="user@example.invalid",
        institution=long_name,
        need="something",
        extra={},
        submitted_at="2026-10-18T15:00:00-03:00",
    )
    msg = message_from_bytes(raw, policy=email.policy.default)
    subject = msg["Subject"]
    prefix = "Solicitud de cotización — sitio web — "
    assert subject.startswith(prefix)
    assert len(subject) - len(prefix) == 120


# ---------------------------------------------------------------------------
# Mounting tests — require create_app()
# ---------------------------------------------------------------------------


def _token_file(tmp_path: Path, address: str = "contacto@origenlab.cl") -> str:
    p = tmp_path / "t.json"
    p.write_text(
        json.dumps(
            {
                "client_id": "c",
                "client_secret": "s",
                "refresh_token": "r",
                "address": address,
            }
        )
    )
    return str(p)


def _app(monkeypatch, **env):
    from origenlab_api.main import create_app
    from origenlab_api.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return create_app()


def test_route_off_when_flag_off(monkeypatch) -> None:
    assert "POST" not in {
        m for m in _app(monkeypatch).openapi()["paths"].get("/v2/web/quote-request", {})
    }


def test_route_on_with_flag_and_valid_token(monkeypatch, tmp_path: Path) -> None:
    app = _app(
        monkeypatch,
        ORIGENLAB_V2_WEB_QUOTE_FORM_ENABLED="true",
        ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE=_token_file(tmp_path),
    )
    assert "/v2/web/quote-request" in app.openapi()["paths"]


def test_route_off_with_flag_but_wrong_token(monkeypatch, tmp_path: Path) -> None:
    app = _app(
        monkeypatch,
        ORIGENLAB_V2_WEB_QUOTE_FORM_ENABLED="true",
        ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE=_token_file(tmp_path, "otra@example.invalid"),
    )
    assert "/v2/web/quote-request" not in app.openapi()["paths"]
    assert app.state.web_quote_form_enabled is False


# ---------------------------------------------------------------------------
# Route-level tests — minimal test app with mocked sender
# ---------------------------------------------------------------------------


class _MockSender:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def send(self, raw: bytes) -> str:
        self.calls.append(raw)
        return "mock_msg_id"


def _route_app(sender=None, caps=None):
    from origenlab_api.v2.web_quote_form import WebQuoteFormCaps, web_quote_form_router

    app = FastAPI()
    app.state.web_quote_form_sender = sender or _MockSender()
    app.state.web_quote_form_caps = caps or WebQuoteFormCaps()
    app.include_router(web_quote_form_router)
    return app


def _body(**overrides) -> dict:
    base = {
        "submission_id": str(uuid.uuid4()),
        "need": "Autoclave 50 L",
        "email": "test@example.invalid",
        "org": "U. de Chile",
        "render_at": time.time() - 10,  # 10 s ago — passes the < 3 s bot guard
    }
    base.update(overrides)
    return base


def test_successful_submission_returns_sent() -> None:
    sender = _MockSender()
    client = TestClient(_route_app(sender=sender))
    r = client.post("/v2/web/quote-request", json=_body())
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    assert len(sender.calls) == 1


def test_idempotency_duplicate_answers_success_without_sending() -> None:
    sender = _MockSender()
    app = _route_app(sender=sender)
    client = TestClient(app)
    sid = str(uuid.uuid4())
    b = _body(submission_id=sid)

    r1 = client.post("/v2/web/quote-request", json=b)
    r2 = client.post("/v2/web/quote-request", json=b)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(sender.calls) == 1  # sender called only once


def test_global_cap_refuses_after_40_sends() -> None:
    from origenlab_api.v2.web_quote_form import WebQuoteFormCaps

    caps = WebQuoteFormCaps()
    now = datetime.now(timezone.utc)
    for _ in range(40):
        caps.global_deque.append(now)

    sender = _MockSender()
    client = TestClient(_route_app(sender=sender, caps=caps))
    r = client.post("/v2/web/quote-request", json=_body())

    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert sender.calls == []


def test_per_address_cap_refuses_after_3_sends() -> None:
    from origenlab_api.v2.web_quote_form import WebQuoteFormCaps

    caps = WebQuoteFormCaps()
    now = datetime.now(timezone.utc)
    target = "ana@test.invalid"
    caps.per_address[target] = deque()
    for _ in range(3):
        caps.per_address[target].append(now)

    sender = _MockSender()
    client = TestClient(_route_app(sender=sender, caps=caps))
    r = client.post("/v2/web/quote-request", json=_body(email=target))

    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert sender.calls == []


def test_timing_guard_silently_succeeds_without_sending() -> None:
    """Submission faster than 3 s after render silently succeeds without calling the sender."""
    sender = _MockSender()
    client = TestClient(_route_app(sender=sender))
    # render_at = right now → elapsed < 3 s → bot guard fires
    r = client.post("/v2/web/quote-request", json=_body(render_at=time.time()))
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    assert sender.calls == []


def test_honeypot_non_empty_silently_succeeds_without_sending() -> None:
    """Non-empty honeypot field silently succeeds without calling the sender."""
    sender = _MockSender()
    client = TestClient(_route_app(sender=sender))
    r = client.post("/v2/web/quote-request", json=_body(hp="i-am-a-bot"))
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    assert sender.calls == []


def test_crlf_in_need_field_is_refused() -> None:
    """CRLF in a body field is rejected with HTTP 400, not silently normalised."""
    sender = _MockSender()
    client = TestClient(_route_app(sender=sender))
    r = client.post(
        "/v2/web/quote-request",
        json=_body(need="Autoclave 50 L\r\nBcc: bad@evil.invalid"),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_input"
    assert sender.calls == []


# ---------------------------------------------------------------------------
# load_send_token scope validation tests
# ---------------------------------------------------------------------------


def test_load_send_token_accepts_compose_scope(tmp_path: Path) -> None:
    """Token file with gmail.compose scope (plus gmail.settings.basic) is accepted."""
    from origenlab_api.v2.gmail_send import load_send_token

    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "client_id": "client.id",
        "client_secret": "secret",
        "refresh_token": "rt",
        "address": "contacto@origenlab.cl",
        "scope": (
            "https://www.googleapis.com/auth/gmail.compose "
            "https://www.googleapis.com/auth/gmail.settings.basic"
        ),
    }), encoding="utf-8")
    # Should not raise
    load_send_token(str(token_file))


def test_load_send_token_rejects_unknown_scope(tmp_path: Path) -> None:
    """Token file with an unrelated scope (gmail.readonly) is rejected with ValueError."""
    from origenlab_api.v2.gmail_send import load_send_token

    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "client_id": "client.id",
        "client_secret": "secret",
        "refresh_token": "rt",
        "address": "contacto@origenlab.cl",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="gmail.send or gmail.compose"):
        load_send_token(str(token_file))


def test_load_send_token_accepts_send_scope(tmp_path: Path) -> None:
    """Token file with gmail.send scope continues to be accepted (backward compat)."""
    from origenlab_api.v2.gmail_send import load_send_token

    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({
        "client_id": "client.id",
        "client_secret": "secret",
        "refresh_token": "rt",
        "address": "contacto@origenlab.cl",
        "scope": "https://www.googleapis.com/auth/gmail.send",
    }), encoding="utf-8")
    # Should not raise
    load_send_token(str(token_file))
