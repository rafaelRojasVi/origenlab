"""Email → cases routes: admin only, the client never sends actions, mounting follows the switches."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}
PREVIEW = "/v2/workspace/mail-rules/preview"
APPLY = "/v2/commands/apply-mail-rules"
UNDO = "/v2/commands/undo-mail-rule-action"
RECEIPT = "00000000-0000-4000-8000-0000000000aa"


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


def _operator(role):
    return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001",
                            email_norm="op@example.test", display_name="Op", role=role, status="active")


class _FakeRepo:
    def __init__(self):
        self.calls = []

    def preview(self):
        self.calls.append(("preview",))
        return {"actions": [], "applied": []}

    def apply(self, operator, pairs):
        self.calls.append(("apply", operator.operator_id, pairs))
        return {"applied": [], "refused": []}

    def undo(self, operator, receipt_id, note):
        self.calls.append(("undo", receipt_id, note))
        return {"undoes_receipt_id": receipt_id}


def _client(repo, role="admin"):
    from origenlab_api.v2.mail_rules_routes import mail_rules_command_router, mail_rules_preview_router

    app = FastAPI()
    app.include_router(mail_rules_preview_router)
    app.include_router(mail_rules_command_router)
    app.state.mail_rules_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


KEY = {**HEADERS, "Idempotency-Key": "k-12345678"}
PAIR = {"evidence_id": "e-1", "rule_id": "R3"}


@pytest.mark.parametrize("role", ["sales", "viewer"])
def test_only_an_admin_previews_applies_or_undoes(role) -> None:
    repo = _FakeRepo()
    client = _client(repo, role)
    assert client.get(PREVIEW, headers=HEADERS).status_code == 403
    assert client.post(APPLY, json={"actions": [PAIR]}, headers=KEY).status_code == 403
    assert client.post(UNDO, json={"receipt_id": RECEIPT, "note": "x"}, headers=KEY).status_code == 403
    assert repo.calls == []


def test_an_admin_previews_applies_and_undoes() -> None:
    repo = _FakeRepo()
    client = _client(repo)
    assert client.get(PREVIEW, headers=HEADERS).status_code == 200
    assert client.post(APPLY, json={"actions": [PAIR]}, headers=KEY).status_code == 200
    assert client.post(UNDO, json={"receipt_id": RECEIPT, "note": "equivocada"}, headers=KEY).status_code == 200
    assert repo.calls == [("preview",), ("apply", "00000000-0000-4000-8000-000000000001", [PAIR]),
                          ("undo", RECEIPT, "equivocada")]


def test_the_client_cannot_send_actions_and_commands_need_a_key() -> None:
    repo = _FakeRepo()
    client = _client(repo)
    assert client.post(APPLY, json={"actions": [{"rule_id": "R3"}]}, headers=KEY).status_code == 422
    assert client.post(APPLY, json={"actions": [PAIR]}, headers=HEADERS).status_code == 400
    assert client.post(APPLY, json={"actions": [PAIR] * 11}, headers=KEY).status_code == 422
    assert client.post(APPLY, json={"actions": [{"evidence_id": "e", "rule_id": "R9"}]}, headers=KEY).status_code == 422
    assert client.post(UNDO, json={"receipt_id": RECEIPT, "note": " "}, headers=KEY).status_code == 422
    assert [c[0] for c in repo.calls] == []


def test_preview_mounts_with_v2_and_apply_only_with_commands(monkeypatch) -> None:
    from origenlab_api.main import create_app
    from origenlab_api.settings import get_settings

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORIGENLAB_V2_COMMANDS_ENABLED", "false")
    paths = set(create_app().openapi()["paths"])
    assert PREVIEW in paths and APPLY not in paths and UNDO not in paths
    monkeypatch.setenv("ORIGENLAB_V2_COMMANDS_ENABLED", "true")
    get_settings.cache_clear()
    paths = set(create_app().openapi()["paths"])
    assert {PREVIEW, APPLY, UNDO} <= paths
