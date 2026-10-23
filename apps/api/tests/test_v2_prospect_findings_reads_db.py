"""`GET /v2/prospect-findings` («Prospectos») against a disposable database, as `origenlab_api`.

Findings are seeded as the worker's `prospects-load` leaves them (a `prospect_finding` source record
and its assertions). Whether a finding is new, known or blocked is decided at read time. Every value
is invented (public repository): «Instituto Ficticio …», addresses under example.test.
"""

from __future__ import annotations

import base64
import itertools
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.prospect_findings.reads import ProspectFindingReadRepository
from origenlab_api.v2.prospect_findings.routes import prospect_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_SERIALS = itertools.count(1)
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
SANTIAGO = ZoneInfo("America/Santiago")


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


@pytest.fixture(scope="module")
def operators(db):
    out = {}
    with _owner(db) as conn:
        for role in ("sales", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-prospectos@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-prospectos@example.test", display_name=f"Operador {role}", role=role,
                status="active")
    return out


class _Lookup(OperatorLookup):
    def __init__(self, operators) -> None:
        self._by_email = {o.email_norm: o for o in operators.values()}

    def by_email(self, email_norm: str):
        return self._by_email.get(email_norm)


@pytest.fixture
def client(db, operators) -> TestClient:
    app = FastAPI()
    app.include_router(prospect_read_router)
    app.state.v2_prospect_read_repository = ProspectFindingReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def _headers(operators, role="sales"):
    return {OPERATOR_EMAIL_HEADER: operators[role].email_norm}


def finding(db, *, email: str | None = None, org: str | None = None, person: str | None = None,
            website: str | None = None, score: int = 50, logo: bool = False, acquired_ago=timedelta(hours=1)) -> str:
    """One finding as the worker loader stores it. Returns its id."""
    n = next(_SERIALS)
    person = person or f"Persona Ficticia {n}"
    org = org or f"Instituto Ficticio {n}"
    payload = {"source": "manual", "external_id": f"t-{n}", "person_name": person, "role": "Investigadora",
               "organization_name": org, "unit": "Lab de Prueba", "email": email,
               "email_source_url": "https://example.test/paper" if email else None, "email_source_year": 2025,
               "phone": None, "website": website, "linkedin_person": None,
               "linkedin_org": "https://www.linkedin.com/company/ficticio", "orcid": None, "scholar": None,
               "fund_label": "Fondequip 2025", "project_title": "Proyecto ficticio", "amount_label": "$ 100 M",
               "awarded_on": "2025-09-01", "why_now": "Ganó un fondo", "equipment": ["Centrífuga"],
               "sources": [["fondo", "https://example.test/fondo"]], "confidence": "alta", "score": score,
               "logo": {"content_type": "image/png", "b64": base64.b64encode(PNG).decode()} if logo else None}
    with _owner(db) as conn:
        rid = conn.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri, review_status, acquired_at) "
            "values ('prospect_finding', %s, %s::jsonb, 'https://example.test/fondo', 'pending', %s) returning id::text",
            (f"prospect:manual:t-{n}:{person.lower()}", json.dumps(payload), datetime.now(UTC) - acquired_ago),
        ).fetchone()[0]
        rows = [("organization_name", org.lower(), {"organization_name": org}),
                ("affiliation", f"{person.lower()}|{org.lower()}", {"person_name": person, "organization_name": org})]
        if email:
            rows.append(("contact_address", email, {"address": email}))
        for kind, norm, value in rows:
            conn.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value) "
                         "values (%s, %s, %s, %s::jsonb)", (rid, kind, norm, json.dumps(value)))
    return rid


def known_contact(db, email: str) -> None:
    with _owner(db) as conn:
        conn.execute("insert into crm.contact_point (kind, value_norm, value_display, usage, confirmation) "
                     "values ('email', %s, %s, 'unattributed', 'machine_proposed')", (email, email))


def mailed(db, email: str, times: int = 1) -> None:
    with _owner(db) as conn:
        box = conn.execute("insert into comms.mailbox (address_norm, display_name) values (%s, 'Contacto') "
                           "on conflict do nothing returning id::text", (f"box{next(_SERIALS)}@example.test",)).fetchone()[0]
        for _ in range(times):
            mid = conn.execute("insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) "
                               "values (%s, %s, 'outbound', now()) returning id::text",
                               (box, f"m-{next(_SERIALS)}")).fetchone()[0]
            conn.execute("insert into comms.message_participant (message_id, role, address_norm) values (%s, 'to', %s)",
                         (mid, email))


def blocked(db, value: str, scope: str = "address") -> None:
    with _owner(db) as conn:
        conn.execute("insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) "
                     "values (%s, %s, 'block', 'all', 'Se dio de baja', 'operator_command')", (scope, value))


def organization(db, name: str, domain: str | None = None, scope: str = "exclusive") -> str:
    with _owner(db) as conn:
        oid = conn.execute("insert into crm.organization (kind, name, confirmation) values ('university', %s, "
                           "'machine_proposed') returning id::text", (name,)).fetchone()[0]
        if domain:
            conn.execute("insert into crm.organization_domain (organization_id, domain_norm, scope) values (%s, %s, %s)",
                         (oid, domain, scope))
    return oid


def verdict(db, operators, rid: str, value: str, *, reason: str | None = None, remind_on: date | None = None) -> None:
    with _owner(db) as conn:
        conn.execute("insert into crm.prospect_review (source_record_id, verdict, reason, remind_on, "
                     "reviewed_by_operator_id) values (%s, %s, %s, %s, %s)",
                     (rid, value, reason, remind_on, operators["sales"].operator_id))


def _items(client, operators, view="nuevos", **params):
    r = client.get("/v2/prospect-findings", params={"view": view, "limit": 200, **params}, headers=_headers(operators))
    assert r.status_code == 200, r.text
    return r.json()


def _ids(body):
    return [i["id"] for i in body["items"]]


def test_classify_matches_email_case_and_mailto(client, db, operators):
    known_contact(db, "ana.conocida@example.test")
    rid = finding(db, email="ana.conocida@example.test")
    assert rid not in _ids(_items(client, operators))
    item = next(i for i in _items(client, operators, "conocidos")["items"] if i["id"] == rid)
    assert item["state"] == "known"
    assert any("CRM" in r for r in item["reasons"])


def test_classify_known_from_mail_history(client, db, operators):
    mailed(db, "escribio@example.test", times=3)
    rid = finding(db, email="escribio@example.test")
    item = next(i for i in _items(client, operators, "conocidos")["items"] if i["id"] == rid)
    assert "Ya nos escribimos (3 correos)" in item["reasons"]


def test_classify_blocked_hidden_and_counted(client, db, operators):
    blocked(db, "baja@example.test")
    blocked(db, "bloqueado.invalid", scope="domain")
    a = finding(db, email="baja@example.test")
    b = finding(db, email="otra@bloqueado.invalid")
    before = _items(client, operators)
    for view in ("nuevos", "conocidos", "mas_tarde", "contactados", "descartados"):
        assert not {a, b} & set(_ids(_items(client, operators, view)))
    assert before["counts"]["omitidos_bajas"] >= 2
    r = client.get(f"/v2/prospect-findings/{a}", headers=_headers(operators))
    assert r.status_code == 404


def test_classify_shared_university_domain_does_not_make_person_known(client, db, operators):
    organization(db, "Universidad Ficticia Compartida", "ufc.example.test", scope="shared")
    known_contact(db, "otra.persona@ufc.example.test")
    rid = finding(db, email="nueva.persona@ufc.example.test", org="Universidad Ficticia Compartida")
    item = next(i for i in _items(client, operators)["items"] if i["id"] == rid)
    assert item["state"] == "new"


def test_known_institution_new_person_is_new_with_reason(client, db, operators):
    oid = organization(db, "Instituto Ficticio Conocido", "ific.example.test")
    rid = finding(db, email="nueva@ific.example.test", org="Instituto Ficticio Conocido")
    item = next(i for i in _items(client, operators)["items"] if i["id"] == rid)
    assert item["state"] == "new"
    assert "Institución conocida: Instituto Ficticio Conocido" in item["reasons"]
    assert item["organization_candidates"] == [{"id": oid, "name": "Instituto Ficticio Conocido"}]


def test_free_mail_domain_never_matches_an_organization(client, db, operators):
    organization(db, "Correo Gratis SpA", "gmail.com")
    rid = finding(db, email="alguien@gmail.com", org="Laboratorio Ficticio Independiente")
    item = next(i for i in _items(client, operators)["items"] if i["id"] == rid)
    assert item["organization_candidates"] == []


def test_two_candidate_organizations_flag_posible_duplicado(client, db, operators):
    organization(db, "Instituto Ficticio Doble", "doble.example.test")
    organization(db, "Instituto Ficticio Doble")
    rid = finding(db, email="x@doble.example.test", org="Instituto Ficticio Doble")
    item = next(i for i in _items(client, operators)["items"] if i["id"] == rid)
    assert len(item["organization_candidates"]) == 2
    assert "Posible duplicado de institución" in item["reasons"]


def test_descartado_never_in_nuevos(client, db, operators):
    rid = finding(db, email="descartar@example.test")
    verdict(db, operators, rid, "descartar", reason="No es nuestro rubro")
    assert rid not in _ids(_items(client, operators))
    assert rid in _ids(_items(client, operators, "descartados"))


def test_reabrir_returns_to_nuevos(client, db, operators):
    rid = finding(db, email="reabrir@example.test")
    verdict(db, operators, rid, "descartar", reason="Datos incorrectos")
    verdict(db, operators, rid, "reabrir")
    assert rid in _ids(_items(client, operators))


def test_mas_tarde_returns_today_on_top(client, db, operators):
    today = datetime.now(SANTIAGO).date()
    due = finding(db, email="vuelve@example.test", score=1)
    later = finding(db, email="despues@example.test", score=99)
    verdict(db, operators, due, "mas_tarde", remind_on=today)
    verdict(db, operators, later, "mas_tarde", remind_on=today + timedelta(days=7))
    body = _items(client, operators)
    assert body["items"][0]["id"] == due and body["items"][0]["returns_today"] is True
    assert later not in _ids(body)
    assert later in _ids(_items(client, operators, "mas_tarde"))
    assert body["counts"]["vuelven_hoy"] >= 1


def test_list_orders_by_score_then_newest(client, db, operators):
    low = finding(db, email="bajo@example.test", score=2)
    high = finding(db, email="alto@example.test", score=98)
    ids = _ids(_items(client, operators))
    assert ids.index(high) < ids.index(low)


def test_list_paginates_25_with_has_more(client, db, operators):
    for _ in range(27):
        finding(db, email=None)
    r = client.get("/v2/prospect-findings", params={"view": "nuevos"}, headers=_headers(operators))
    body = r.json()
    assert len(body["items"]) == 25 and body["has_more"] is True
    r2 = client.get("/v2/prospect-findings", params={"view": "nuevos", "offset": 25}, headers=_headers(operators))
    assert not set(_ids(body)) & set(_ids(r2.json()))


def test_counts_nuevos_hoy_and_contactados_mes(client, db, operators):
    fresh = finding(db, email="fresco@example.test")
    old = finding(db, email="viejo@example.test", acquired_ago=timedelta(days=3))
    contacted = finding(db, email="contactado@example.test")
    verdict(db, operators, contacted, "contactar", reason="fuente: https://example.test/paper")
    counts = _items(client, operators)["counts"]
    assert counts["nuevos"] >= 1 and counts["contactados_mes"] >= 1
    assert counts["por_revisar"] >= counts["nuevos"]
    assert contacted in _ids(_items(client, operators, "contactados"))
    assert fresh and old


def test_detail_carries_sources_history_and_links(client, db, operators):
    rid = finding(db, email="detalle@example.test", website="https://example.test", logo=True)
    verdict(db, operators, rid, "mas_tarde", remind_on=datetime.now(SANTIAGO).date() + timedelta(days=2))
    r = client.get(f"/v2/prospect-findings/{rid}", headers=_headers(operators))
    assert r.status_code == 200
    body = r.json()
    assert body["project_title"] == "Proyecto ficticio"
    assert body["sources"] == [["fondo", "https://example.test/fondo"]]
    assert body["links"]["linkedin_org"] == "https://www.linkedin.com/company/ficticio"
    assert body["links"]["website"] == "https://example.test"
    assert body["has_logo"] is True
    assert [h["verdict"] for h in body["history"]] == ["mas_tarde"]
    assert "logo" not in body


def test_detail_unknown_or_malformed_id_is_404(client, operators):
    assert client.get("/v2/prospect-findings/00000000-0000-4000-8000-000000000000",
                      headers=_headers(operators)).status_code == 404
    assert client.get("/v2/prospect-findings/no-es-uuid", headers=_headers(operators)).status_code == 404


def test_logo_served_with_nosniff(client, db, operators):
    rid = finding(db, email="logo@example.test", logo=True)
    r = client.get(f"/v2/prospect-findings/{rid}/logo", headers=_headers(operators))
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=86400"


def test_logo_404_when_absent(client, db, operators):
    rid = finding(db, email="sinlogo@example.test")
    assert client.get(f"/v2/prospect-findings/{rid}/logo", headers=_headers(operators)).status_code == 404


def test_viewer_may_read(client, db, operators):
    r = client.get("/v2/prospect-findings", headers=_headers(operators, "viewer"))
    assert r.status_code == 200


def test_unknown_view_is_422(client, operators):
    r = client.get("/v2/prospect-findings", params={"view": "todo"}, headers=_headers(operators))
    assert r.status_code == 422


def test_viewer_sees_masked_contact_addresses(client, db, operators):
    from origenlab_api.v2.contact_redaction import EMAIL_MASK, REDACTION_HEADER

    rid = finding(db, email="enmascarar@example.test")
    r = client.get("/v2/prospect-findings", params={"limit": 200}, headers=_headers(operators, "viewer"))
    assert r.status_code == 200
    assert REDACTION_HEADER.lower() in {k.lower() for k in r.headers}
    item = next(i for i in r.json()["items"] if i["id"] == rid)
    assert item["email"] == f"{EMAIL_MASK}@example.test"
    detail = client.get(f"/v2/prospect-findings/{rid}", headers=_headers(operators, "viewer")).json()
    assert detail["email"] == f"{EMAIL_MASK}@example.test"
    sales = client.get(f"/v2/prospect-findings/{rid}", headers=_headers(operators)).json()
    assert sales["email"] == "enmascarar@example.test"


def test_logo_still_served_through_the_redacting_route(client, db, operators):
    rid = finding(db, email="logo.visor@example.test", logo=True)
    r = client.get(f"/v2/prospect-findings/{rid}/logo", headers=_headers(operators, "viewer"))
    assert r.status_code == 200 and r.content == PNG


# ── the same person from two sources (final review #5) ───────────────────────────────────────

def test_same_person_from_two_sources_shows_once(client, db, operators):
    a = finding(db, email="dos.fuentes@example.test", score=70)
    b = finding(db, email="dos.fuentes@example.test", score=90)
    ids = _ids(_items(client, operators))
    assert b in ids and a not in ids
    item = next(i for i in _items(client, operators)["items"] if i["id"] == b)
    assert "También en 1 fuente más" in item["reasons"]


def test_discarding_one_source_discards_the_other(client, db, operators):
    a = finding(db, email="descartada.una@example.test")
    b = finding(db, email="descartada.una@example.test")
    verdict(db, operators, a, "descartar", reason="No es nuestro rubro")
    assert b not in _ids(_items(client, operators))
    assert b in _ids(_items(client, operators, "descartados"))


def test_same_person_without_email_matches_by_name_and_institution(client, db, operators):
    a = finding(db, email=None, person="Persona Repetida Ficticia", org="Instituto Ficticio Repetido", score=10)
    b = finding(db, email=None, person="persona  repetida ficticia", org="Instituto Ficticio Repetido", score=20)
    ids = _ids(_items(client, operators))
    assert b in ids and a not in ids
