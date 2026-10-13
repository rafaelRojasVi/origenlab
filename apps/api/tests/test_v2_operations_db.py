"""«Operaciones» against a disposable database, read as the API role; and the route's audience."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

VIEWER = OperatorIdentity(operator_id="00000000-0000-0000-0000-0000000000a1", email_norm="viewer@origenlab.cl",
                          display_name="Lectora Ficticia", role="viewer", status="active", version=1)


class _Port(IdentityPort):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._operator is None:
            raise IdentityRefused("no identity")
        return self._operator


class _Repo:
    def operations(self):
        return {"generated_at": "2026-10-13T12:00:00+00:00", "cards": [], "actions": [],
                "counts": {"cases_without_institution": 0, "institutions_por_confirmar": 0}, "actions_today": 0}


def _client(operator: OperatorIdentity | None) -> TestClient:
    app = FastAPI()
    app.state.v2_identity = _Port(operator)
    app.state.crm_workspace = _Repo()
    app.include_router(workspace_router)
    return TestClient(app)


def test_any_signed_in_operator_reads_it_and_nobody_else() -> None:
    assert _client(None).get("/v2/workspace/operations").status_code == 401
    res = _client(VIEWER).get("/v2/workspace/operations")
    assert res.status_code == 200
    keys = [c["key"] for c in res.json()["cards"]]
    assert keys[-3:] == ["reglas", "version", "respaldo"]  # added by the route, not the repository
    assert _client(VIEWER).post("/v2/workspace/operations").status_code == 405


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, *args) -> list:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, args or None)
        return cur.fetchall() if cur.description else []


def _message(dsn: str, mailbox: str, *, minutes_ago: int, subject: str, triaged: bool) -> None:
    gid = uuid.uuid4().hex
    rec = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                      "values ('gmail_message', %s, %s::jsonb, %s) returning id::text",
                 f"gmail_message:{gid}", json.dumps({"gmail_message_id": gid, "subject": subject}), f"gmail://msg/{gid}")[0][0]
    _owner(dsn, "insert into comms.message (mailbox_id, provider_message_id, direction, internal_date, subject, "
                "parse_status, eml_storage_path, eml_sha256, created_at) values (%s, %s, 'inbound', now() - make_interval(mins => %s), "
                "%s, 'parsed', 'mail/x.eml', repeat('0', 64), now() - make_interval(mins => %s))",
           mailbox, gid, minutes_ago, subject, minutes_ago)
    if triaged:
        _owner(dsn, "insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                    "values (%s, 'message_triage', 'triage:v3', '{\"class\": \"business_other\"}'::jsonb)", rec)


@needs_db
def test_the_cards_read_the_database_as_the_api_role(disposable_database) -> None:
    import psycopg

    dsn = disposable_database
    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn))
    empty = {c["key"]: c for c in repo.operations()["cards"]}
    assert empty["correo"]["summary"] == "Gmail no configurado"
    assert empty["clasificacion"]["status"] == "ok" and empty["drive"]["status"] == "ok"

    mailbox = _owner(dsn, "insert into comms.mailbox (address_norm, authorization_state, last_synced_at) "
                          "values ('contacto@origenlab.cl', 'authorized', now() - interval '2 minutes') returning id")[0][0]
    _message(dsn, mailbox, minutes_ago=3, subject="Asunto ficticio uno", triaged=True)
    _message(dsn, mailbox, minutes_ago=20, subject="Asunto ficticio dos", triaged=False)
    admin = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                        "values (gen_random_uuid(), 'admin.ficticio@example.test', 'Admin Ficticio', 'admin', 'active') "
                        "returning id")[0][0]
    case = _owner(dsn, "insert into crm.opportunity (title, stage, owner_operator_id, origin_source_record_id) "
                       "select 'Caso ficticio', 'lead', %s, id from evidence.source_record limit 1 returning id", admin)[0][0]
    quote = _owner(dsn, "insert into crm.quote (opportunity_id, quote_number) values (%s, '09876-26') returning id", case)[0][0]
    _owner(dsn, "insert into crm.quote_revision (quote_id, revision_no, quote_currency, price_decimals, pdf_sha256, "
                "origin_source_record_id, created_at) select %s, 1, 'CLP', 0, %s, id, now() - interval '2 days' "
                "from evidence.source_record limit 1", quote, "ab" * 32)

    body = repo.operations()
    cards = {c["key"]: c for c in body["cards"]}
    assert cards["correo"]["status"] == "ok"
    assert {f["label"]: f["value"] for f in cards["correo"]["facts"]}["Correos hoy"] >= 1
    assert (cards["clasificacion"]["status"], cards["clasificacion"]["facts"][0]["value"]) == ("warn", 1)
    assert cards["drive"]["status"] == "warn" and cards["cotizaciones"]["status"] == "warn"
    assert body["counts"]["cases_without_institution"] == 1
    text = json.dumps(body)
    assert "@" not in text and "Asunto ficticio" not in text
