"""`GET /v2/workspace/mail-sync` — whether the Gmail capture is running. No address, no message."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace import MAIL_SYNC_LATE_MINUTES, CrmWorkspaceRepository, mail_sync_state
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

AT = datetime(2026, 10, 12, 13, 5, tzinfo=timezone.utc)
VIEWER = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="operator@example.invalid",
                          display_name="Operator", role="viewer", status="active")


@pytest.mark.parametrize("state,last,minutes,expected", [
    ("authorized", AT, 5, "ok"),
    ("authorized", AT, MAIL_SYNC_LATE_MINUTES + 1, "late"),
    ("authorized", None, None, "late"),
    ("revoked", AT, 90, "stopped"),
    ("unauthorized", AT, 90, "stopped"),
    ("unauthorized", None, None, "not_started"),
    (None, None, None, "not_configured"),
])
def test_the_state_follows_authorization_and_age(state, last, minutes, expected) -> None:
    body = mail_sync_state(state, last, minutes)
    assert (body["state"], body["late_after_minutes"]) == (expected, 30)
    assert body["last_synced_at"] == (last.isoformat() if last else None)


class _Port(IdentityPort):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._operator is None:
            raise IdentityRefused("no identity")
        return self._operator


class _Repo:
    def mail_sync(self):
        return mail_sync_state("revoked", AT, 90)


def _client(operator: OperatorIdentity | None) -> TestClient:
    app = FastAPI()
    app.state.v2_identity = _Port(operator)
    app.state.crm_workspace = _Repo()
    app.include_router(workspace_router)
    return TestClient(app)


def test_any_signed_in_operator_reads_it_and_nobody_else() -> None:
    assert _client(None).get("/v2/workspace/mail-sync").status_code == 401
    res = _client(VIEWER).get("/v2/workspace/mail-sync")
    assert res.status_code == 200 and res.json()["state"] == "stopped" and "@" not in res.text
    assert _client(VIEWER).post("/v2/workspace/mail-sync").status_code == 405


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql)


@needs_db
def test_the_read_runs_as_the_api_role_and_measures_age_in_the_database(disposable_database) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    assert repo.mail_sync()["state"] == "not_configured"
    _owner(disposable_database, "insert into comms.mailbox (address_norm, authorization_state, last_synced_at) "
                                "values ('contacto@origenlab.cl', 'authorized', now() - interval '45 minutes')")
    body = repo.mail_sync()
    assert (body["state"], body["minutes_since_sync"]) == ("late", 45)
    _owner(disposable_database, "update comms.mailbox set last_synced_at = now() - interval '3 minutes'")
    assert repo.mail_sync()["state"] == "ok"
