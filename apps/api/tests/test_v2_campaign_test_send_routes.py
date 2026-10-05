"""Routes and mounting of «Enviar prueba». No database, no Gmail: the repository is a fake."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.campaign_test_send_routes import campaign_test_send_router
from origenlab_api.v2.campaign_test_send import TestSendLimitRefused
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}
POST = "/v2/commands/send-campaign-test"
CYBER = "cyber-2026-10"
BODY = {"v1_lane_key": CYBER, "to": "ana@example.invalid"}


class _Lookup(OperatorLookup):
    def __init__(self, role: str) -> None:
        self.role = role

    def by_email(self, email_norm: str) -> OperatorIdentity:
        return OperatorIdentity(operator_id=str(uuid.uuid4()), email_norm=email_norm,
                                display_name="Op", role=self.role, status="active")


LIMITED = "limit@example.invalid"


class _Repo:
    def __init__(self) -> None:
        self.calls = []

    def send_test(self, *, operator, body, idempotency_key, digest):
        if body.to == LIMITED:
            raise TestSendLimitRefused("2026-10-05T01:00:00+00:00")
        if operator.role != "admin":
            raise CommandRefused(403, "admin_only", "x")
        self.calls.append(body)
        return {"status": "sent", "to": body.to}

    def history(self, *, campaign_id, v1_lane_key):
        return {"tests": [{"to": "ana@example.invalid", "status": "sent"}], "remaining": {"hour": 9, "day": 29}}


def _client(role="admin"):
    app = FastAPI()
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(role))
    app.state.campaign_test_send_repository = _Repo()
    app.include_router(campaign_test_send_router)
    return TestClient(app), app.state.campaign_test_send_repository


def test_an_admin_sends_with_an_idempotency_key() -> None:
    client, repo = _client("admin")
    res = client.post(POST, json=BODY, headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())})
    assert res.status_code == 200 and len(repo.calls) == 1


def test_no_idempotency_key_no_send() -> None:
    client, repo = _client("admin")
    assert client.post(POST, json=BODY, headers=HEADERS).status_code in (400, 422)
    assert repo.calls == []


@pytest.mark.parametrize("role,status", [("sales", 403), ("viewer", 403)])
def test_only_admin_sends(role: str, status: int) -> None:
    client, _ = _client(role)
    assert client.post(POST, json=BODY, headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())}).status_code == status


def test_html_in_the_body_is_refused() -> None:
    client, repo = _client("admin")
    res = client.post(POST, json={**BODY, "body_html": "<p>x</p>"}, headers={**HEADERS, "Idempotency-Key": str(uuid.uuid4())})
    assert res.status_code == 422 and repo.calls == []


def test_history_masks_the_address_for_a_viewer() -> None:
    client, _ = _client("viewer")
    res = client.get("/v2/workspace/marketing/test-send-history?v1_lane_key=" + CYBER, headers=HEADERS)
    assert res.status_code == 200 and "ana@example.invalid" not in res.text


def _token_file(tmp_path: Path, address="contacto@origenlab.cl") -> str:
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "r", "address": address}))
    return str(p)


def _app(monkeypatch, **env):
    from origenlab_api.main import create_app
    from origenlab_api.settings import get_settings

    get_settings.cache_clear()  # settings are cached per process; each app here reads its own env
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


def _v2_paths(app) -> set[str]:
    return {p for p in app.openapi()["paths"] if p.startswith("/v2")}


def test_mounted_only_with_its_switch_and_a_valid_token(monkeypatch, tmp_path: Path) -> None:
    assert POST not in _v2_paths(_app(monkeypatch))
    assert POST not in _v2_paths(_app(monkeypatch, ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED="true"))
    bad = _app(monkeypatch, ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED="true",
               ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE=_token_file(tmp_path, "otra@example.invalid"))
    assert POST not in _v2_paths(bad) and bad.state.campaign_test_send_enabled is False
    good = _app(monkeypatch, ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED="true",
                ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE=_token_file(tmp_path))
    assert POST in _v2_paths(good) and good.state.campaign_test_send_enabled is True
    assert "/v2/workspace/marketing/test-send-history" in _v2_paths(good)


def test_with_the_switch_on_the_only_sending_route_is_the_test_send(monkeypatch, tmp_path: Path) -> None:
    import re

    env = {f"ORIGENLAB_V2_{s}_ENABLED": "true" for s in
           ("COMMANDS", "CAMPAIGN_DRAFTS", "AUDIENCE_FREEZE", "RECONTACT_REVIEW", "CAMPAIGN_PLANNING", "CAMPAIGN_TEST_SEND")}
    app = _app(monkeypatch, **env, ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE=_token_file(tmp_path))
    forbidden = {"send", "sends", "approve", "activate", "enqueue", "dispatch", "schedule", "launch"}
    offenders = {p for p in _v2_paths(app) if forbidden & set(re.split(r"[/_{}-]+", p.lower()))}
    assert offenders == {POST, "/v2/workspace/marketing/test-send-history"}


def test_the_limit_answer_carries_next_allowed_at_and_others_keep_their_shape() -> None:
    client, _ = _client("admin")
    key = {**HEADERS, "Idempotency-Key": str(uuid.uuid4())}
    res = client.post(POST, json={**BODY, "to": LIMITED}, headers=key)
    assert res.status_code == 429
    d = res.json()["detail"]
    assert d["code"] == "test_send_limit" and d["next_allowed_at"] == "2026-10-05T01:00:00+00:00" and d["message"]
    other, _ = _client("sales")
    d2 = other.post(POST, json=BODY, headers=key).json()["detail"]
    assert set(d2) == {"code", "message"}


def test_the_token_file_variable_is_read_through_its_alias(monkeypatch) -> None:
    from origenlab_api.settings import Settings

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE", "/run/secrets/t.json")
    monkeypatch.setenv("ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED", "true")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    st = Settings(_env_file=None)
    assert st.v2_test_send_token_file == "/run/secrets/t.json" and st.v2_campaign_test_send_configured()


@pytest.mark.parametrize("content", ["not json {", None])
def test_an_unreadable_or_non_json_token_leaves_it_off_and_the_api_boots(monkeypatch, tmp_path: Path, caplog, content) -> None:
    path = tmp_path / "bad.json"
    if content is not None:
        path.write_text(content)  # None: the path does not exist
    with caplog.at_level("WARNING"):
        app = _app(monkeypatch, ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED="true",
                   ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE=str(path))
    assert app.state.campaign_test_send_enabled is False and POST not in _v2_paths(app)
    assert "campaign test send disabled" in caplog.text
