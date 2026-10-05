"""`GET /v2/workspace/mail-quote-numbers` — the quote numbers the captured Gmail already shows.

The Resumen's quote-number box suggests the next number. Without this read it knew only the CRM
and the Drive archive, so numbers already sent by email were suggested again. Numbers, dates and
counts only: no address, no subject, no institution. Every number below is invented.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace import MAIL_QUOTE_NUMBERS_LIMIT, CrmWorkspaceRepository
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

VIEWER = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="operator@example.invalid",
                          display_name="Operator", role="viewer", status="active")
BODY = {"items": [{"quote_number": "CN09901", "first_seen_at": "2026-10-01T12:00:00+00:00", "messages": 2}]}


class _Port(IdentityPort):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._operator is None:
            raise IdentityRefused("no identity")
        return self._operator


class _Repo:
    def mail_quote_numbers(self):
        return BODY


def _client(operator: OperatorIdentity | None) -> TestClient:
    app = FastAPI()
    app.state.v2_identity = _Port(operator)
    app.state.crm_workspace = _Repo()
    app.include_router(workspace_router)
    return TestClient(app)


def test_any_signed_in_operator_reads_it_and_nobody_else() -> None:
    assert _client(None).get("/v2/workspace/mail-quote-numbers").status_code == 401
    res = _client(VIEWER).get("/v2/workspace/mail-quote-numbers")
    assert res.status_code == 200 and res.json() == BODY
    assert _client(VIEWER).post("/v2/workspace/mail-quote-numbers").status_code == 405


def test_the_cap_is_five_hundred() -> None:
    assert MAIL_QUOTE_NUMBERS_LIMIT == 500


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)


def _mail(dsn: str, key: str, numbers, sent_at: str | None, *, status: str = "pending", kind: str = "gmail_message") -> None:
    payload: dict = {"subject_raw": "Cotización ficticia", "sender": "ventas@ejemplo.invalid"}
    if numbers is not None:
        payload["proposed_quote_numbers"] = numbers
    if sent_at is not None:
        payload["sent_at"] = sent_at
    _owner(
        dsn,
        "insert into evidence.source_record (kind, dedupe_key, payload, review_status, acquired_at) "
        "values (%s, %s, %s::jsonb, %s, '2026-10-05T10:00:00+00')",
        (kind, key, json.dumps(payload), status),
    )


@needs_db
def test_distinct_numbers_with_first_sighting_and_message_count(disposable_database) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    assert repo.mail_quote_numbers() == {"items": []}

    _mail(disposable_database, "gmail:t1", ["CN09901"], "2026-09-30T15:00:00-03:00")
    _mail(disposable_database, "gmail:t2", ["CN09901", "CN09902"], "2026-10-02T09:00:00+00:00")
    _mail(disposable_database, "gmail:t3", ["CN09903"], "not a date")  # falls back to acquired_at
    _mail(disposable_database, "gmail:t4", ["CN09904"], "2026-10-03T09:00:00+00:00", status="rejected")
    _mail(disposable_database, "gmail:t5", None, "2026-10-03T09:00:00+00:00")  # an unsubscribe reply shape
    _mail(disposable_database, "gmail:t6", "CN09905", "2026-10-03T09:00:00+00:00")  # not an array
    _mail(disposable_database, "gmail:t7", ["CN09906"], "2026-10-03T09:00:00+00:00", status="promoted")
    _mail(disposable_database, "drive:t8", ["CN09907"], "2026-10-03T09:00:00+00:00", kind="drive_file")

    items = repo.mail_quote_numbers()["items"]
    by_number = {i["quote_number"]: i for i in items}
    assert set(by_number) == {"CN09901", "CN09902", "CN09903", "CN09906"}
    assert by_number["CN09901"] == {"quote_number": "CN09901", "first_seen_at": "2026-09-30T18:00:00+00:00", "messages": 2}
    assert by_number["CN09902"]["messages"] == 1
    assert by_number["CN09903"]["first_seen_at"] == "2026-10-05T10:00:00+00:00"
    # Newest first.
    assert [i["quote_number"] for i in items] == ["CN09903", "CN09906", "CN09902", "CN09901"]
