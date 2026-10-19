"""`open-tender-case` against a real, disposable database, as `origenlab_api`.

Every value is invented (public repository): buyer codes 7xxx, «Hospital Ejemplo …», the RUTs
61.111.111-8 (ruling R2: a valid check digit), 61.222.222-3 and 60.910.000-1, and codes of the
shape 1234567-12-LE26. One database serves the module, so each test names its own RUT.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.tenders.codes import portal_url
from origenlab_api.v2.tenders.mercado_publico import TenderDetail
from origenlab_api.v2.tenders.open_case import (
    OPEN_TENDER_CASE,
    OpenTenderCaseBody,
    V2TenderCommandRepository,
    prepare_open_tender_case,
)

pytestmark = needs_db
_SERIALS = itertools.count(1)
_KEYS = itertools.count(1)
RUT = "61.111.111-8"


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    import psycopg

    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


def scalar(db, sql, *args):
    with _owner(db) as conn:
        row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def row(db, sql, *args):
    with _owner(db) as conn:
        return conn.execute(sql, args).fetchone()


@pytest.fixture(scope="module")
def operator_id(db):
    with _owner(db) as conn:
        return conn.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) values "
            "(gen_random_uuid(), 'ventas-licitaciones@example.test', 'Vendedora Ficticia', 'sales', 'active') "
            "returning id::text").fetchone()[0]


@pytest.fixture
def world():
    """One fresh tender code, buyer code and buyer name per test (the database is module-wide)."""
    n = next(_SERIALS)
    tag = uuid.uuid4().hex[:6]
    return {"code": f"{1234500 + n}-12-LE26", "buyer_code": f"7{n:03d}{tag[:2]}",
            "buyer_name": f"Hospital Ejemplo {tag}"}


def _detail(world, **over) -> TenderDetail:
    values = {"code": world["code"], "title": "Adquisición de balanzas analíticas", "buyer_name": world["buyer_name"],
              "buyer_code": world["buyer_code"], "buyer_rut": None, "closes_at": "2026-11-03T15:00:00",
              "published_at": "2026-10-15T09:00:00", "source_status": "Publicada",
              "head": {"CodigoExterno": world["code"], "Nombre": "Adquisición de balanzas analíticas",
                       "Comprador": {"CodigoOrganismo": world["buyer_code"], "NombreOrganismo": world["buyer_name"]}}}
    values.update(over)
    return TenderDetail(**values)


def open_tender_case(db, operator_id, *, reference, detail=None, buyer=None, key=None):
    """The route's two halves: prepare (parse + the stubbed lookup), then the transaction."""
    import psycopg

    body = OpenTenderCaseBody(reference=reference, buyer=buyer)
    operator = OperatorIdentity(operator_id=operator_id, email_norm="ventas-licitaciones@example.test",
                                display_name="Vendedora Ficticia", role="sales", status="active")
    try:
        fields = prepare_open_tender_case(body, ticket="ticket-ficticio", fetch=lambda code, ticket: detail)
        return V2TenderCommandRepository(psycopg.connect, runtime_dsn(db)).execute(
            command_name=OPEN_TENDER_CASE, operator=operator, fields=fields,
            idempotency_key=key or f"pytest-tender-{next(_KEYS)}", digest=request_digest(OPEN_TENDER_CASE, body))
    except CommandRefused as exc:
        return exc


def refusal(result) -> tuple[str, int]:
    assert isinstance(result, CommandRefused), result
    return result.code, result.status_code


def _org(db, name, code=None, rut=None):
    with _owner(db) as conn:
        org_id = conn.execute("insert into crm.organization (kind, name, confirmation) values "
                              "('institution', %s, 'confirmed') returning id::text", (name,)).fetchone()[0]
        for scheme, value in (("chilecompra_buyer_code", code), ("rut", rut)):
            if value:
                conn.execute("insert into crm.external_identifier (scheme, value_norm, organization_id) "
                             "values (%s, %s, %s)", (scheme, value, org_id))
    return org_id


def test_opens_case_with_machine_proposed_buyer(db, operator_id, world) -> None:
    r = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world, buyer_rut="61222222-3"))
    assert r["organization_confirmation"] == "machine_proposed" and r["fetched"] is True
    assert scalar(db, "select relation from crm.opportunity_evidence where opportunity_id=%s and notice_id=%s",
                  r["opportunity_id"], r["notice_id"]) == "origin"
    assert scalar(db, "select count(*) from crm.external_identifier where scheme='chilecompra_buyer_code' "
                      "and value_norm=%s and organization_id=%s", world["buyer_code"], r["organization_id"]) == 1
    assert scalar(db, "select count(*) from crm.external_identifier where scheme='rut' and value_norm='61222222-3' "
                      "and organization_id=%s", r["organization_id"]) == 1
    assert row(db, "select name, confirmation from crm.organization where id=%s", r["organization_id"]) == (
        world["buyer_name"], "machine_proposed")
    assert row(db, "select organization_id::text, title from crm.opportunity where id=%s", r["opportunity_id"]) == (
        r["organization_id"], f"Licitación {world['code']} — Adquisición de balanzas analíticas")
    assert row(db, "select codigo_externo, promoted_opportunity_id::text, buyer_code, head->>'Nombre', "
                   "to_char(closes_at at time zone 'America/Santiago', 'YYYY-MM-DD HH24:MI') "
                   "from procurement.notice where id=%s", r["notice_id"]) == (
        world["code"], r["opportunity_id"], world["buyer_code"], "Adquisición de balanzas analíticas",
        "2026-11-03 15:00")
    assert r["portal_url"] == portal_url(world["code"])
    events = {e for (e,) in _all(db, "select event_type from crm.domain_event where command_receipt_id=%s",
                                 r["command_receipt_id"])}
    assert {"opportunity.created", "notice.promoted", "organization.created", "case_evidence.linked"} <= events


def _all(db, sql, *args):
    with _owner(db) as conn:
        return conn.execute(sql, args).fetchall()


def test_tender_row_shape(db, operator_id, world) -> None:
    r = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world))
    assert row(db, "select kind, tender_status, stage, owner_operator_id::text from crm.opportunity where id=%s",
               r["opportunity_id"]) == ("tender", "preparando", "lead", operator_id)


def test_without_ticket_requires_typed_buyer(db, operator_id, world) -> None:
    before = scalar(db, "select count(*) from crm.opportunity")
    result = open_tender_case(db, operator_id, reference=world["code"])
    assert refusal(result) == ("buyer_required", 422)
    assert result.details == {"fetched": False}
    assert scalar(db, "select count(*) from crm.opportunity") == before
    assert scalar(db, "select count(*) from procurement.notice where codigo_externo=%s", world["code"]) == 0


def test_typed_buyer_used_when_not_fetched(db, operator_id, world) -> None:
    name = f"Hospital Ejemplo Tipeado {world['buyer_code']}"
    r = open_tender_case(db, operator_id, reference=world["code"], buyer={"name": name, "rut": RUT})
    assert r["fetched"] is False and r["organization_confirmation"] == "machine_proposed"
    assert scalar(db, "select name from crm.organization where id=%s", r["organization_id"]) == name
    assert scalar(db, "select organization_id::text from crm.external_identifier where scheme='rut' "
                      "and value_norm='61111111-8'") == r["organization_id"]
    assert row(db, "select head, title from procurement.notice where id=%s", r["notice_id"]) == (
        {"source": "operator", "code": world["code"]}, None)
    assert scalar(db, "select title from crm.opportunity where id=%s", r["opportunity_id"]) == (
        f"Licitación {world['code']} — {name}")


def test_second_open_refused_with_existing_case(db, operator_id, world) -> None:
    first = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world))
    again = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world))
    assert refusal(again) == ("tender_case_exists", 409)
    assert again.details["opportunity_id"] == first["opportunity_id"]
    assert scalar(db, "select count(*) from crm.opportunity_evidence e join procurement.notice n on n.id = e.notice_id "
                      "where n.codigo_externo=%s", world["code"]) == 1


def test_same_key_replays_the_first_answer(db, operator_id, world) -> None:
    key = f"pytest-tender-replay-{world['code']}"
    first = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world), key=key)
    again = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world), key=key)
    assert again["replayed"] is True and again["opportunity_id"] == first["opportunity_id"]


def test_existing_org_name_reused_not_refused(db, operator_id, world) -> None:
    org_id = _org(db, world["buyer_name"])
    r = open_tender_case(db, operator_id, reference=world["code"],
                         detail=_detail(world, buyer_name=world["buyer_name"].upper()))
    assert r["organization_id"] == org_id and r["organization_confirmation"] == "confirmed"
    assert scalar(db, "select count(*) from crm.organization where lower(name)=lower(%s)", world["buyer_name"]) == 1


def test_known_buyer_code_reuses_org(db, operator_id, world) -> None:
    org_id = _org(db, f"Servicio de Salud Ficticio {world['buyer_code']}", code=world["buyer_code"])
    r = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world))
    assert r["organization_id"] == org_id
    assert scalar(db, "select count(*) from crm.organization where name=%s", world["buyer_name"]) == 0
    assert scalar(db, "select organization_id::text from crm.opportunity_organization where opportunity_id=%s "
                      "and role='requesting_institution' and valid_to is null", r["opportunity_id"]) == org_id


def test_known_rut_reuses_org(db, operator_id, world) -> None:
    rut = "60910000-1"
    org_id = _org(db, f"Universidad Ficticia {world['buyer_code']}", rut=rut)
    r = open_tender_case(db, operator_id, reference=world["code"], buyer={"name": "Otro Nombre", "rut": "60.910.000-1"})
    assert r["organization_id"] == org_id


def test_reopen_after_the_case_closed_opens_a_new_case(db, operator_id, world) -> None:
    first = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world))
    with _owner(db) as conn:
        conn.execute("update crm.opportunity set stage='lost', closed_at=now(), close_reason='perdida ficticia', "
                     "version=version+1 where id=%s", (first["opportunity_id"],))
    second = open_tender_case(db, operator_id, reference=world["code"], detail=_detail(world))
    assert second["opportunity_id"] != first["opportunity_id"] and second["notice_id"] == first["notice_id"]
    assert scalar(db, "select promoted_opportunity_id::text from procurement.notice where id=%s",
                  first["notice_id"]) == second["opportunity_id"]


def test_current_bidder_profile_is_the_newest_row(db, operator_id) -> None:
    """The Task 5 stub (ruling R8): None before any save, then the newest profile."""
    import psycopg

    from origenlab_api.v2.tenders.bidder import current_bidder_profile

    def current():
        with psycopg.connect(runtime_dsn(db)) as conn, conn.cursor() as cur:
            return current_bidder_profile(cur)

    assert current() is None
    with _owner(db) as conn:
        for name, at in (("Oferente Ejemplo", "2026-10-01"), ("Oferente Ejemplo Dos", "2026-10-02")):
            conn.execute("insert into crm.bidder_profile (bidder_kind, legal_name, rut, set_by_operator_id, reason, "
                         "created_at) values ('natural_person', %s, '11111111-1', %s, 'datos ficticios', %s)",
                         (name, operator_id, at))
    profile = current()
    assert (profile["legal_name"], profile["rut"], profile["bidder_kind"]) == (
        "Oferente Ejemplo Dos", "11111111-1", "natural_person")
