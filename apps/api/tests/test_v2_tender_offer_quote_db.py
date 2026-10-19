"""A tender's economic offer as a Cotizador draft on the case (spec 2026-10-19 §6.10, plan Task 10).

Runs as `origenlab_api` against a disposable database. Every value is invented (public repository).
"""

from __future__ import annotations

import itertools
import uuid

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.quotes.commands import V2QuoteRepository
from origenlab_api.v2.quotes.inputs import no_provider
from origenlab_api.v2.quotes.reads import V2QuoteReads
from origenlab_api.v2.quotes.routes import quote_command_router, quote_read_router
from origenlab_api.v2.tenders.mercado_publico import TenderDetail
from origenlab_api.v2.tenders.open_case import (
    OPEN_TENDER_CASE,
    OpenTenderCaseBody,
    V2TenderCommandRepository,
    prepare_open_tender_case,
)
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.routes import tender_command_router, tender_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_KEYS = itertools.count(1)
_SERIALS = itertools.count(1)
URL = "/v2/commands/create-tender-offer-quote"
BIDDER_RUT = "61111111-8"  # 61.111.111-8 (ruling R2)


def _owner_conn(dsn: str):
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


def _operators(dsn: str) -> dict[str, OperatorIdentity]:
    out = {}
    with _owner_conn(dsn) as conn:
        for role in ("sales", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-offer@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-offer@example.test", display_name=f"Operador {role}", role=role,
                status="active")
    return out


def _seed_numbering(dsn: str) -> None:
    with _owner_conn(dsn) as conn:
        conn.execute("insert into catalog.cost_parameter (key, value_numeric, valid_from, reason) "
                     "values ('quote_number_seed', 1300, now() - interval '1 day', 'prueba')")


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def operators(db):
    out = _operators(db)
    _seed_numbering(db)
    with _owner_conn(db) as conn:
        conn.execute(
            "insert into crm.bidder_profile (bidder_kind, legal_name, rut, set_by_operator_id, reason) "
            "values ('natural_person', 'Oferente Ejemplo', %s, %s, 'prueba')",
            (BIDDER_RUT, out["sales"].operator_id))
    return out


class _Lookup(OperatorLookup):
    def __init__(self, operators) -> None:
        self._by_email = {o.email_norm: o for o in operators.values()}

    def by_email(self, email_norm: str):
        return self._by_email.get(email_norm)


@pytest.fixture(scope="module")
def reads(db) -> V2QuoteReads:
    return V2QuoteReads(psycopg.connect, runtime_dsn(db), fx_resolver=lambda c, d: pytest.fail("no FX expected"))


@pytest.fixture
def client(db, operators, reads) -> TestClient:
    dsn = runtime_dsn(db)
    app = FastAPI()
    for router in (tender_command_router, tender_read_router, quote_read_router, quote_command_router):
        app.include_router(router)
    app.state.v2_tender_command_repository = V2TenderCommandRepository(psycopg.connect, dsn)
    app.state.v2_tender_read_repository = TenderReadRepository(psycopg.connect, dsn)
    app.state.quote_reads = reads
    app.state.quote_repository = V2QuoteRepository(psycopg.connect, dsn, fx_resolver=no_provider)
    app.state.case_document_storage = FakeStorage("crm-documents")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def headers(role: str = "sales", key: str | None = None) -> dict[str, str]:
    return {OPERATOR_EMAIL_HEADER: f"{role}-offer@example.test",
            "Idempotency-Key": key or f"pytest-offer-{next(_KEYS)}"}


def open_tender(dsn: str, operators) -> tuple[str, str]:
    """(case id, tender code)."""
    n = next(_SERIALS)
    tag = uuid.uuid4().hex[:6]
    code = f"{1235700 + n}-12-LE26"
    detail = TenderDetail(
        code=code, title=f"Adquisición de balanzas {tag}", buyer_name=f"Hospital Ejemplo {tag}",
        buyer_code=f"8{n:03d}{tag[:2]}", buyer_rut=None, closes_at=None, published_at=None,
        source_status="Publicada", head={"CodigoExterno": code})
    body = OpenTenderCaseBody(reference=code)
    fields = prepare_open_tender_case(body, ticket="ticket-ficticio", fetch=lambda c, ticket: detail)
    case = V2TenderCommandRepository(psycopg.connect, runtime_dsn(dsn)).execute(
        command_name=OPEN_TENDER_CASE, operator=operators["sales"], fields=fields,
        idempotency_key=f"pytest-open-{next(_KEYS)}", digest=request_digest(OPEN_TENDER_CASE, body))["opportunity_id"]
    return case, code


def sale_case(dsn: str, operators, name: str) -> str:
    """A sale case with its requesting institution (the deferred agreement check passes in one transaction)."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into crm.organization (kind, name, confirmation) values ('institution', %s, "
                    "'confirmed') returning id::text", (name,))
        org = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                    "values (%s, 'lead', %s, %s) returning id::text", (f"Venta {name}", operators["sales"].operator_id, org))
        sale = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                    "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                    "current_date, 'confirmed', %s)", (sale, org, operators["sales"].operator_id))
        conn.commit()
    return sale


def add_annex(dsn: str, case: str, operators) -> str:
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    with _owner_conn(dsn) as conn:
        return conn.execute(
            "insert into crm.opportunity_document (opportunity_id, kind, storage_path, sha256, content_type, "
            "byte_size, original_filename, source, attached_by_operator_id) "
            "values (%s, 'tender_annex', %s, %s, 'application/pdf', 10, 'bases.pdf', 'upload', %s) returning id::text",
            (case, f"opportunities/{case}/{sha}.pdf", sha, operators["sales"].operator_id)).fetchone()[0]


def add_read(dsn: str, case: str, operators, items, *, status: str = "ready", phase: str = "read") -> str:
    """A preparation row of the case, written as the worker leaves it (result.items)."""
    annex = add_annex(dsn, case, operators)
    with _owner_conn(dsn) as conn:
        based_on = None
        if phase == "fill":
            based_on = add_read(dsn, case, operators, items)
        return conn.execute(
            "insert into evidence.bid_preparation (opportunity_id, phase, status, annex_document_ids, "
            "based_on_preparation_id, requested_by_operator_id, result) "
            "values (%s, %s, %s, %s::uuid[], %s, %s, %s) returning id::text",
            (case, phase, status, [annex], based_on, operators["sales"].operator_id,
             Jsonb({"resumen": "r", "checklist": [], "items": items, "annexes": []}))).fetchone()[0]


def item(n: int, text: str, qty: str = "1", **extra) -> dict:
    return {"linea": n, "descripcion": text, "cantidad": qty, "unidad": "un", "requisitos": [], "citas": [], **extra}


def make_product(dsn: str) -> str:
    tag = uuid.uuid4().hex[:6]
    with _owner_conn(dsn) as conn:
        maker = conn.execute("insert into crm.organization (kind, name, confirmation) values ('company', %s, "
                             "'confirmed') returning id::text", (f"Fabricante Ejemplo {tag}",)).fetchone()[0]
        return conn.execute(
            "insert into catalog.product (manufacturer_organization_id, model_number, name_es, product_kind, "
            "content_origin) values (%s, %s, 'Balanza ficticia', 'equipment', 'operator') returning id::text",
            (maker, f"BE-{tag}")).fetchone()[0]


def ask(client, case: str, prep: str, role: str = "sales", key: str | None = None):
    return client.post(URL, json={"opportunity_id": case, "preparation_id": prep}, headers=headers(role, key))


def code(response) -> str:
    return response.json()["detail"]["code"]


def lines(dsn: str, revision_id: str) -> list[dict]:
    with _owner_conn(dsn) as conn, conn.cursor() as cur:
        cur.execute("select line_no, line_kind, description, quantity, product_id::text, line_note_es "
                    "from crm.quote_line where quote_revision_id = %s order by line_no", (revision_id,))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def test_two_items_become_two_lines_on_a_new_quote_of_the_case(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    product = make_product(db)
    prep = add_read(db, case, operators, [item(1, "Balanza analítica 220 g", "2", product_id=product),
                                          item(2, "Pesas patrón clase F1")])
    r = ask(client, case, prep)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["quote_number"] == "01300-26" and body["matched"] == 1 and body["free"] == 1
    assert body["existing"] is False
    with _owner_conn(db) as conn:
        row = conn.execute("select q.opportunity_id::text, q.quote_number, r.status, r.origin from crm.quote q "
                           "join crm.quote_revision r on r.quote_id = q.id where q.id = %s",
                           (body["quote_id"],)).fetchone()
    assert row == (case, "01300-26", "draft", "authored")
    got = lines(db, body["revision_id"])
    assert [(g["line_no"], g["description"], int(g["quantity"]), g["product_id"]) for g in got] == [
        (1, "Balanza analítica 220 g", 2, product), (2, "Pesas patrón clase F1", 1, None)]


def test_a_quantity_that_cannot_be_read_is_one_with_the_text_in_a_note(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Micropipeta", "2,5"), item(2, "Reactivo", "según demanda"),
                                          item(3, "Guantes", "0"), item(4, "Tubos", "")])
    body = ask(client, case, prep).json()
    got = lines(db, body["revision_id"])
    assert [float(g["quantity"]) for g in got] == [2.5, 1, 1, 1]
    assert got[0]["line_note_es"] is None
    assert got[1]["line_note_es"] == "Cantidad según bases: según demanda"
    assert got[2]["line_note_es"] == "Cantidad según bases: 0"
    assert got[3]["line_note_es"] is None or got[3]["line_note_es"].startswith("Cantidad según bases")


def test_a_second_call_returns_the_same_draft_and_mints_nothing(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    first = ask(client, case, prep).json()
    second = ask(client, case, prep)
    assert second.status_code == 200, second.text
    again = second.json()
    assert again["existing"] is True
    assert (again["quote_id"], again["revision_id"], again["quote_number"]) == (
        first["quote_id"], first["revision_id"], first["quote_number"])
    assert (again["matched"], again["free"]) == (0, 1)
    with _owner_conn(db) as conn:
        assert conn.execute("select count(*) from crm.quote where opportunity_id = %s", (case,)).fetchone()[0] == 1


def test_the_same_key_replays_the_answer(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    a = ask(client, case, prep, key="pytest-offer-replay-1").json()
    b = ask(client, case, prep, key="pytest-offer-replay-1").json()
    assert b.pop("replayed") is True and a.pop("replayed") is False
    assert a == b


@pytest.mark.parametrize("make, expected", [
    ("fill", "preparation_not_a_read"),
    ("queued", "read_not_ready"),
    ("failed", "read_not_ready"),
    ("empty", "no_items"),
])
def test_a_preparation_that_is_not_a_ready_read_with_items_is_refused(db, operators, client, make, expected) -> None:
    case, _ = open_tender(db, operators)
    items = [] if make == "empty" else [item(1, "Balanza")]
    status = make if make in ("queued", "failed") else "ready"
    prep = add_read(db, case, operators, items, status=status, phase="fill" if make == "fill" else "read")
    r = ask(client, case, prep)
    assert r.status_code == 422 and code(r) == expected, r.text
    with _owner_conn(db) as conn:
        assert conn.execute("select count(*) from crm.quote where opportunity_id = %s", (case,)).fetchone()[0] == 0


def test_a_preparation_of_another_case_or_none_is_not_found(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    other, _ = open_tender(db, operators)
    foreign = add_read(db, other, operators, [item(1, "Balanza")])
    for prep in (foreign, str(uuid.uuid4())):
        r = ask(client, case, prep)
        assert r.status_code == 404 and code(r) == "preparation_not_found", r.text
    assert ask(client, str(uuid.uuid4()), foreign).status_code == 404


def test_a_sale_case_is_refused(db, operators, client) -> None:
    sale = sale_case(db, operators, "Clínica Ejemplo")
    r = ask(client, sale, str(uuid.uuid4()))
    assert r.status_code == 422 and code(r) == "not_a_tender_case"


def test_a_viewer_may_not_ask(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    assert ask(client, case, prep, role="viewer").status_code == 403


def test_a_voided_offer_is_gone_and_a_new_one_may_be_made(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    body = ask(client, case, prep).json()
    with _owner_conn(db) as conn:
        conn.execute("update crm.quote_revision set status = 'void' where id = %s", (body["revision_id"],))
    # a voided offer is gone: a new one may be made
    assert ask(client, case, prep).json()["existing"] is False


def test_numbering_not_configured_is_refused_like_create_quote() -> None:
    bare = build_disposable_database()
    dsn = next(bare)
    try:
        ops = _operators(dsn)
        case, _ = open_tender(dsn, ops)
        prep = add_read(dsn, case, ops, [item(1, "Balanza")])
        repo = V2TenderCommandRepository(psycopg.connect, runtime_dsn(dsn))
        with pytest.raises(CommandRefused) as exc:
            repo.execute(command_name="create-tender-offer-quote", operator=ops["sales"],
                         fields={"opportunity_id": case, "preparation_id": prep},
                         idempotency_key=f"pytest-nonum-{next(_KEYS)}", digest="d" * 64)
        assert exc.value.code == "quote_numbering_not_configured"
    finally:
        bare.close()


def test_the_workspace_of_the_offer_carries_the_tender_and_the_bidder(db, operators, client) -> None:
    case, tender_code = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    quote_id = ask(client, case, prep).json()["quote_id"]
    r = client.get(f"/v2/quotes/{quote_id}", headers=headers())
    assert r.status_code == 200, r.text
    tender = r.json()["tender"]
    assert tender["code"] == tender_code
    assert tender["bidder"]["legal_name"] == "Oferente Ejemplo"
    assert tender["bidder"]["rut"] == "61.111.111-8"
    assert [x["description"] for x in r.json()["current"]["draft"]["items"]] == ["Balanza"]


def test_a_viewer_gets_no_bidder_data(db, operators, client, reads) -> None:
    case, tender_code = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    quote_id = ask(client, case, prep).json()["quote_id"]
    assert client.get(f"/v2/quotes/{quote_id}", headers=headers("viewer")).status_code == 403
    for role in ("viewer", None):
        tender = reads.workspace(quote_id, role=role)["tender"]
        assert tender == {"code": tender_code, "bidder": None}
    assert reads.workspace(quote_id, role="admin")["tender"]["bidder"]["legal_name"] == "Oferente Ejemplo"


def test_a_sale_quote_has_no_tender_block(db, operators, client) -> None:
    sale = sale_case(db, operators, "Clínica Ejemplo Dos")
    made = client.post("/v2/commands/create-quote", json={"opportunity_id": sale}, headers=headers())
    assert made.status_code == 200, made.text
    r = client.get(f"/v2/quotes/{made.json()['quote_id']}", headers=headers())
    assert r.status_code == 200 and r.json()["tender"] is None


def test_the_offer_opens_by_id_but_is_not_in_the_list_or_rentabilidad(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    made = ask(client, case, prep).json()
    assert client.get(f"/v2/quotes/{made['quote_id']}", headers=headers()).status_code == 200
    listed = client.get("/v2/quotes?status=all&limit=100", headers=headers())
    assert listed.status_code == 200 and made["quote_number"] not in listed.text
    assert made["quote_id"] not in listed.text
    economics = client.get("/v2/quotes/economics", headers=headers())
    assert economics.status_code == 200 and made["quote_id"] not in economics.text


def test_an_approved_offer_is_not_replaced_by_a_second_quote(db, operators, client) -> None:
    case, _ = open_tender(db, operators)
    prep = add_read(db, case, operators, [item(1, "Balanza")])
    made = ask(client, case, prep).json()
    with _owner_conn(db) as conn:
        conn.execute(
            "update crm.quote_revision set status = 'approved', subtotal = 1000, discount_total = 0, tax_base = 1000, "
            "tax_total = 190, grand_total = 1190, totals_computed_at = now(), approved_at = now(), "
            "approved_by_operator_id = %s, party_snapshot = '{}'::jsonb, party_snapshot_version = 1 where id = %s",
            (operators["sales"].operator_id, made["revision_id"]))
    r = ask(client, case, prep)
    assert r.status_code == 409 and code(r) == "offer_quote_exists", r.text
    with _owner_conn(db) as conn:
        assert conn.execute("select count(*) from crm.quote where opportunity_id = %s", (case,)).fetchone()[0] == 1
