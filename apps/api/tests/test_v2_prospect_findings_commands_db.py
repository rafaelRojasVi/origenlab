"""`review-prospect` and `contact-prospect` («Prospectos») against a disposable database, as `origenlab_api`.

`contact-prospect` is the promotion of DOMAIN.md §5: the person, their email, the institution and its
`prospect` role, in one transaction, re-checking Bajas at the moment of the click. Every value is
invented (public repository).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_v2_prospect_findings_reads_db import (
    SANTIAGO,
    _Lookup,
    _owner,
    blocked,
    finding,
    known_contact,
    organization,
    verdict,
)
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity
from origenlab_api.v2.prospect_findings.commands import V2ProspectCommandRepository
from origenlab_api.v2.prospect_findings.reads import ProspectFindingReadRepository
from origenlab_api.v2.prospect_findings.routes import prospect_command_router, prospect_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def operators(db):
    out = {}
    with _owner(db) as conn:
        for role in ("sales", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-contactar@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-contactar@example.test", display_name=f"Operador {role}", role=role,
                status="active")
    return out


@pytest.fixture
def client(db, operators) -> TestClient:
    app = FastAPI()
    app.include_router(prospect_read_router)
    app.include_router(prospect_command_router)
    app.state.v2_prospect_read_repository = ProspectFindingReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_prospect_command_repository = V2ProspectCommandRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def _post(client, operators, path, body, *, role="sales", key=None):
    return client.post(f"/v2/commands/{path}", json=body,
                       headers={OPERATOR_EMAIL_HEADER: operators[role].email_norm,
                                "Idempotency-Key": key or str(uuid.uuid4())})


def _one(db, sql, params=()):
    with _owner(db) as conn:
        return conn.execute(sql, params).fetchone()


def _count(db, sql, params=()):
    return _one(db, sql, params)[0]


def _crm_rows(db):
    return _count(db, "select (select count(*) from crm.person) + (select count(*) from crm.contact_point)"
                      " + (select count(*) from crm.organization) + (select count(*) from crm.affiliation)"
                      " + (select count(*) from crm.organization_relationship)")


# ── review-prospect ──────────────────────────────────────────────────────────────────────────

def test_review_descartar_requires_reason(client, db, operators):
    rid = finding(db, email="r1@example.test")
    r = _post(client, operators, "review-prospect", {"finding_id": rid, "verdict": "descartar"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "reason_required"
    ok = _post(client, operators, "review-prospect",
               {"finding_id": rid, "verdict": "descartar", "reason": "No es nuestro rubro"})
    assert ok.status_code == 200, ok.text
    assert _one(db, "select verdict, reason from crm.prospect_review where source_record_id = %s", (rid,)) == (
        "descartar", "No es nuestro rubro")
    assert _count(db, "select count(*) from crm.domain_event where aggregate_id = %s and "
                      "event_type = 'source_record.prospect_reviewed'", (rid,)) == 1


def test_review_mas_tarde_requires_future_date(client, db, operators):
    rid = finding(db, email="r2@example.test")
    today = datetime.now(SANTIAGO).date()
    for remind in (None, today.isoformat()):
        r = _post(client, operators, "review-prospect", {"finding_id": rid, "verdict": "mas_tarde", "remind_on": remind})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "remind_on_must_be_future"
    ok = _post(client, operators, "review-prospect",
               {"finding_id": rid, "verdict": "mas_tarde", "remind_on": (today + timedelta(days=7)).isoformat()})
    assert ok.status_code == 200


def test_review_reabrir_after_descartar_and_not_twice(client, db, operators):
    rid = finding(db, email="r3@example.test")
    _post(client, operators, "review-prospect", {"finding_id": rid, "verdict": "descartar", "reason": "Otro"})
    assert _post(client, operators, "review-prospect", {"finding_id": rid, "verdict": "reabrir"}).status_code == 200
    again = _post(client, operators, "review-prospect", {"finding_id": rid, "verdict": "reabrir"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "prospect_already_open"


def test_review_viewer_refused_403(client, db, operators):
    rid = finding(db, email="r4@example.test")
    r = _post(client, operators, "review-prospect", {"finding_id": rid, "verdict": "descartar", "reason": "x"},
              role="viewer")
    assert r.status_code == 403


def test_review_unknown_finding_404(client, operators):
    r = _post(client, operators, "review-prospect",
              {"finding_id": str(uuid.uuid4()), "verdict": "descartar", "reason": "x"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "prospect_not_found"


# ── contact-prospect ─────────────────────────────────────────────────────────────────────────

def test_contact_creates_person_email_org_and_prospect_role(client, db, operators):
    rid = finding(db, email="nueva.persona@nuevo.example.test", org="Instituto Ficticio Nuevo",
                  person="Nueva Persona")
    r = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert r.status_code == 200, r.text
    body = r.json()
    person = _one(db, "select display_name from crm.person where id = %s", (body["person_id"],))
    assert person == ("Nueva Persona",)
    assert _one(db, "select person_id::text from crm.contact_point where kind = 'email' and value_norm = %s",
                ("nueva.persona@nuevo.example.test",)) == (body["person_id"],)
    assert _one(db, "select name from crm.organization where id = %s", (body["organization_id"],)) == (
        "Instituto Ficticio Nuevo",)
    assert _count(db, "select count(*) from crm.organization_relationship where organization_id = %s "
                      "and role = 'prospect' and valid_to is null", (body["organization_id"],)) == 1
    assert _count(db, "select count(*) from crm.affiliation where person_id = %s and organization_id = %s",
                  (body["person_id"], body["organization_id"])) == 1
    assert _one(db, "select review_status from evidence.source_record where id = %s", (rid,)) == ("promoted",)
    assert _count(db, "select count(*) from evidence.assertion where source_record_id = %s "
                      "and resolution = 'unresolved'", (rid,)) == 0
    review = _one(db, "select verdict, reason from crm.prospect_review where source_record_id = %s", (rid,))
    assert review == ("contactar", "fuente: https://example.test/paper")


def test_contact_links_org_by_domain_when_name_differs(client, db, operators):
    oid = organization(db, "Universidad Ficticia de Prueba", "ufp.example.test")
    rid = finding(db, email="investigadora@ufp.example.test", org="U. Ficticia de Prueba")
    before = _count(db, "select count(*) from crm.organization")
    r = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == oid
    assert _count(db, "select count(*) from crm.organization") == before


def test_contact_refuses_ambiguous_org_with_candidates(client, db, operators):
    a = organization(db, "Instituto Ficticio Gemelo", "gemelo.example.test")
    b = organization(db, "Instituto Ficticio Gemelo")
    rid = finding(db, email="p@gemelo.example.test", org="Instituto Ficticio Gemelo")
    before = _crm_rows(db)
    r = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "organization_ambiguous"
    assert {c["id"] for c in detail["candidates"]} == {a, b}
    assert _crm_rows(db) == before


def test_contact_with_chosen_org_id_links_it(client, db, operators):
    organization(db, "Instituto Ficticio Elegido", "elegido.example.test")
    chosen = organization(db, "Instituto Ficticio Elegido")
    rid = finding(db, email="q@elegido.example.test", org="Instituto Ficticio Elegido")
    r = _post(client, operators, "contact-prospect", {"finding_id": rid, "organization_id": chosen})
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == chosen


def test_contact_create_new_organization_when_asked(client, db, operators):
    organization(db, "Instituto Ficticio Otro", "otro.example.test")
    organization(db, "Instituto Ficticio Otro")
    rid = finding(db, email="z@otro.example.test", org="Instituto Ficticio Otro")
    r = _post(client, operators, "contact-prospect", {"finding_id": rid, "create_new_organization": True})
    assert r.status_code == 200, r.text
    assert _count(db, "select count(*) from crm.organization where name = 'Instituto Ficticio Otro'") == 3


def test_contact_does_not_add_prospect_role_to_customer(client, db, operators):
    oid = organization(db, "Cliente Ficticio Existente", "cliente.example.test")
    with _owner(db) as conn:
        conn.execute("insert into crm.organization_relationship (organization_id, role, valid_from) "
                     "values (%s, 'customer', current_date - 30)", (oid,))
    rid = finding(db, email="nuevo@cliente.example.test", org="Cliente Ficticio Existente")
    assert _post(client, operators, "contact-prospect", {"finding_id": rid}).status_code == 200
    assert _count(db, "select count(*) from crm.organization_relationship where organization_id = %s "
                      "and role = 'prospect'", (oid,)) == 0


def test_contact_refused_when_blocked_since_listing(client, db, operators):
    rid = finding(db, email="se.dio.de.baja@example.test")
    listed = client.get("/v2/prospect-findings", params={"limit": 200},
                        headers={OPERATOR_EMAIL_HEADER: operators["sales"].email_norm}).json()
    assert rid in [i["id"] for i in listed["items"]]
    blocked(db, "se.dio.de.baja@example.test")
    before = _crm_rows(db)
    r = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "prospect_blocked"
    assert "Bajas" in r.json()["detail"]["message"]
    assert _crm_rows(db) == before
    assert _count(db, "select count(*) from crm.prospect_review where source_record_id = %s", (rid,)) == 0


def test_contact_is_idempotent_on_replay(client, db, operators):
    rid = finding(db, email="doble.clic@example.test")
    key = str(uuid.uuid4())
    first = _post(client, operators, "contact-prospect", {"finding_id": rid}, key=key)
    second = _post(client, operators, "contact-prospect", {"finding_id": rid}, key=key)
    assert first.status_code == second.status_code == 200
    assert second.json()["replayed"] is True
    assert {k: v for k, v in first.json().items() if k != "replayed"} == {
        k: v for k, v in second.json().items() if k != "replayed"}
    assert _count(db, "select count(*) from crm.contact_point where value_norm = 'doble.clic@example.test'") == 1
    assert _count(db, "select count(*) from crm.prospect_review where source_record_id = %s", (rid,)) == 1


def test_contact_twice_with_new_key_is_refused(client, db, operators):
    rid = finding(db, email="ya.contactado@example.test")
    assert _post(client, operators, "contact-prospect", {"finding_id": rid}).status_code == 200
    again = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "prospect_already_contacted"


def test_contact_known_email_links_existing_person(client, db, operators):
    with _owner(db) as conn:
        pid = conn.execute("insert into crm.person (display_name, confirmation, status) values "
                           "('Persona Ya Conocida', 'machine_proposed', 'active') returning id::text").fetchone()[0]
        conn.execute("insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
                     "values ('email', 'ya.conocida@example.test', 'ya.conocida@example.test', %s, 'personal', "
                     "'machine_proposed')", (pid,))
    rid = finding(db, email="ya.conocida@example.test", org="Instituto Ficticio Conocida")
    before = _count(db, "select count(*) from crm.person")
    r = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert r.status_code == 200, r.text
    assert r.json()["person_id"] == pid
    assert _count(db, "select count(*) from crm.person") == before
    assert _count(db, "select count(*) from crm.affiliation where person_id = %s", (pid,)) == 1


def test_contact_unattributed_email_is_claimed_by_new_person(client, db, operators):
    known_contact(db, "sin.duenio@example.test")
    rid = finding(db, email="sin.duenio@example.test")
    r = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert r.status_code == 200, r.text
    assert _count(db, "select count(*) from crm.contact_point where value_norm = 'sin.duenio@example.test'") == 1
    assert _one(db, "select person_id::text from crm.contact_point where value_norm = 'sin.duenio@example.test'") == (
        r.json()["person_id"],)


def test_contact_without_email_creates_person_and_org_only(client, db, operators):
    rid = finding(db, email=None, org="Laboratorio Ficticio Sin Correo")
    r = _post(client, operators, "contact-prospect", {"finding_id": rid})
    assert r.status_code == 200, r.text
    assert _count(db, "select count(*) from crm.contact_point where person_id = %s", (r.json()["person_id"],)) == 0


def test_contact_viewer_refused_403(client, db, operators):
    rid = finding(db, email="viewer.no@example.test")
    assert _post(client, operators, "contact-prospect", {"finding_id": rid}, role="viewer").status_code == 403


def test_contacted_finding_moves_to_contactados(client, db, operators):
    rid = finding(db, email="movido@example.test")
    _post(client, operators, "contact-prospect", {"finding_id": rid})
    body = client.get("/v2/prospect-findings", params={"view": "contactados", "limit": 200},
                      headers={OPERATOR_EMAIL_HEADER: operators["sales"].email_norm}).json()
    assert rid in [i["id"] for i in body["items"]]
    assert date.fromisoformat(body["items"][0]["latest_verdict"]["reviewed_at"][:10])
