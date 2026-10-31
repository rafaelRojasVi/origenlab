"""The «Pagos» invoice commands and read against PostgreSQL (opt-in: `ORIGENLAB_V2_TEST_DSN`).

Runs in a disposable database (`tests/v2_command_harness.py`) as `origenlab_api`: an import adds new
folios only, finds the client by RUT (or an earlier document, or the name) and attaches the one case
whose quote has the document's neto (authored or read off the PDF), else the client's only won case,
and guesses again on a later import unless a person set or cleared the case;
a stale version writes nothing, «Pagada» and its undo, and the read's balance and age buckets.
Every name, RUT and amount is fictitious.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.invoices.commands import (
    IMPORT_INVOICES,
    MARK_INVOICE_PAID,
    MARK_INVOICE_UNPAID,
    SET_INVOICE_CASE,
    MarkInvoicePaidBody,
    MarkInvoiceUnpaidBody,
    SetInvoiceCaseBody,
    validated_invoice,
)

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402

_KEYS = iter(range(1, 1_000_000))
_FOLIOS = iter(range(10_000, 1_000_000))
_RUTS = iter(range(70_000_001, 79_999_999))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(world_or_db, sql, *params):
    import psycopg

    dsn = world_or_db["db"] if isinstance(world_or_db, dict) else world_or_db
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _case_with_quote(db, operator_id, organization_id, total, tag, stage="lead", pdf_net=None):  # noqa: PLR0913
    """A case of the organization (its confirmed requesting institution) with one approved CLP quote of
    `total`, or — `total` None — one historical revision whose only amount is `pdf_net`, the neto read off
    its PDF (none at all when that is None too). `stage='won'` closes it on that quote."""
    import psycopg

    with psycopg.connect(db) as conn, conn.cursor() as cur:  # one transaction: the institution check is deferred
        cur.execute("set role origenlab_owner")
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) values "
                    "(%s, %s, %s, %s) returning id::text",
                    (f"Caso ficticio {tag}", "lead", operator_id, organization_id))
        case_id = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                    "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', current_date, "
                    "'confirmed', %s)", (case_id, organization_id, operator_id))
        cur.execute("insert into crm.quote (opportunity_id, quote_number, created_by_operator_id) "
                    "values (%s, %s, %s) returning id", (case_id, f"CN9{uuid.uuid4().hex[:8]}", operator_id))
        quote_id = cur.fetchone()[0]
        if total is None:
            sha = uuid.uuid4().hex * 2
            cur.execute("insert into evidence.source_record (kind, dedupe_key, payload, review_status) values "
                        "('gmail_message', %s, '{}'::jsonb, 'pending') returning id", (f"gmail_message:{sha}",))
            mail = cur.fetchone()[0]
            if pdf_net is not None:
                cur.execute("insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', "
                            "%s, jsonb_build_object('currency', 'CLP', 'net_total', %s::text))",
                            (f"quote_document:{sha}", pdf_net))
            cur.execute("insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at, "
                        "origin_source_record_id) values (%s, 1, 'sent', 'historical_import', %s, now(), %s)",
                        (quote_id, sha, mail))
        else:
            net = round(total / 1.19)
            cur.execute(
                "insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals, "
                "subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at, "
                "approved_at, approved_by_operator_id, party_snapshot, party_snapshot_version) "
                "values (%s, 1, 'approved', 'CLP', 0, %s, 0, %s, %s, %s, now(), now(), %s, '{}'::jsonb, 1)",
                (quote_id, net, net, total - net, total, operator_id))
        if stage == "won":  # a case opens at lead and walks the transition table
            for step in ("qualifying", "qualified", "quoting", "negotiating"):
                cur.execute("update crm.opportunity set stage = %s where id = %s", (step, case_id))
            cur.execute("update crm.opportunity set stage = 'won', closed_at = now(), won_quote_id = %s, "
                        "won_revision_no = 1 where id = %s", (quote_id, case_id))
    return case_id


@pytest.fixture
def world(db):
    """A sales operator, an organization with a RUT, and two of its cases (quotes of 119.000 and 238.000)."""
    tag = uuid.uuid4().hex[:12]
    _owner(db, "alter table crm.invoice_payment disable trigger invoice_payment_never_deleted")
    _owner(db, "delete from crm.invoice_payment")
    _owner(db, "alter table crm.invoice_payment enable trigger invoice_payment_never_deleted")
    _owner(db, "alter table crm.invoice disable trigger invoice_never_deleted")
    _owner(db, "delete from crm.invoice")
    _owner(db, "alter table crm.invoice enable trigger invoice_never_deleted")
    rut = f"{next(_RUTS)}-K"  # identifiers are never deleted: a fresh fictitious RUT per test
    [(operator_id,)] = _owner(db, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                                  "values (gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') "
                                  "returning id::text", f"ventas-{tag}@example.test")
    [(org_id,)] = _owner(db, "insert into crm.organization (kind, name, confirmation) "
                             "values ('company', %s, 'confirmed') returning id::text", f"Instituto Ficticio {tag}")
    _owner(db, "insert into crm.external_identifier (scheme, value_norm, organization_id) values ('rut', %s, %s)",
           rut, org_id)
    case_a = _case_with_quote(db, operator_id, org_id, 119000, tag + "a")
    case_b = _case_with_quote(db, operator_id, org_id, 238000, tag + "b")
    return {"db": db, "operator_id": operator_id, "org_id": org_id, "case_a": case_a, "case_b": case_b, "rut": rut}


def _operator(world):
    return OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                            display_name="Vendedora Ficticia", role="sales", status="active")


def _repo(world):
    import psycopg

    from origenlab_api.v2.invoices.repository import V2InvoiceCommandRepository

    return V2InvoiceCommandRepository(psycopg.connect, runtime_dsn(world["db"]))


def _reads(world):
    import psycopg

    from origenlab_api.v2.invoices.reads import InvoiceReadRepository

    return InvoiceReadRepository(psycopg.connect, runtime_dsn(world["db"]))


def _doc(world, doc_type=33, folio=None, total=119000, rut=None, issued_on=None, ref=None, name="INSTITUTO FICTICIO"):
    return {"doc_type": doc_type, "folio": folio or next(_FOLIOS),
            "issued_on": (issued_on or date.today()).isoformat(), "client_rut": rut or world["rut"],
            "client_name": name, "exempt_amount": 0, "net_amount": round(total / 1.19),
            "vat_amount": total - round(total / 1.19), "total_amount": total,
            "reference_doc_type": ref[0] if ref else None, "reference_folio": ref[1] if ref else None}


def _import(world, *docs, sha="a" * 64, key=None):
    fields = {"documents": list(docs), "skipped_types": {}, "sha256": sha, "filename": "RCV_VENTA.csv",
              "note": "prueba"}
    return _repo(world).execute(command_name=IMPORT_INVOICES, operator=_operator(world), fields=fields,
                                idempotency_key=key or f"pytest-invoice-{next(_KEYS)}", digest=sha)


def _run(world, name, body):
    return _repo(world).execute(command_name=name, operator=_operator(world),
                                fields=validated_invoice(name, body, today=date.today() + timedelta(days=1)),
                                idempotency_key=f"pytest-invoice-{next(_KEYS)}", digest=request_digest(name, body))


def _invoice(world, folio, doc_type=33):
    [row] = _owner(world, "select id::text, version, opportunity_id::text, case_link, organization_id::text, paid_on "
                          "from crm.invoice where doc_type = %s and folio = %s", doc_type, folio)
    return dict(zip(("id", "version", "opportunity_id", "case_link", "organization_id", "paid_on"), row, strict=True))


@needs_db
def test_import_finds_the_client_by_rut_and_attaches_the_one_case_with_that_total(world) -> None:
    one, other, stranger = _doc(world, total=119000), _doc(world, total=777000), _doc(world, total=119000, rut="11111111-1")
    result = _import(world, one, other, stranger)
    assert (result["imported"], result["already_on_record"], result["attached_to_case"],
            result["without_organization"]) == (3, 0, 1, 1)
    first = _invoice(world, one["folio"])
    assert (first["organization_id"], first["opportunity_id"], first["case_link"]) == (
        world["org_id"], world["case_a"], "machine")
    assert _invoice(world, other["folio"])["opportunity_id"] is None  # no quote of that total
    assert _invoice(world, stranger["folio"])["organization_id"] is None  # unknown RUT
    [(events,)] = _owner(world, "select count(*) from crm.domain_event where event_type = 'invoice.imported'")
    assert events >= 3


@needs_db
def test_reimporting_a_month_adds_nothing_and_keeps_what_people_set(world) -> None:
    doc = _doc(world, total=777000)
    _import(world, doc)
    inv = _invoice(world, doc["folio"])
    _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(invoice_id=inv["id"], invoice_version=inv["version"],
                                                        paid_on=date.today(), note="transferencia"))
    again = _import(world, doc, _doc(world, total=1000), sha="b" * 64)
    assert (again["imported"], again["already_on_record"]) == (1, 1)
    assert _invoice(world, doc["folio"])["paid_on"] == date.today()


@needs_db
def test_two_cases_with_the_same_total_attach_none(world) -> None:
    _case_with_quote(world["db"], world["operator_id"], world["org_id"], 238000, uuid.uuid4().hex[:12])
    doc = _doc(world, total=238000)
    _import(world, doc)
    assert _invoice(world, doc["folio"])["opportunity_id"] is None


@needs_db
def test_a_person_attaches_and_detaches_a_case_at_the_version_shown(world) -> None:
    doc = _doc(world, total=777000)
    _import(world, doc)
    inv = _invoice(world, doc["folio"])
    out = _run(world, SET_INVOICE_CASE, SetInvoiceCaseBody(invoice_id=inv["id"], invoice_version=inv["version"],
                                                          opportunity_id=world["case_b"], note="es este caso"))
    assert out["invoice_version"] == inv["version"] + 1
    now = _invoice(world, doc["folio"])
    assert (now["opportunity_id"], now["case_link"]) == (world["case_b"], "operator")
    with pytest.raises(CommandRefused) as stale:
        _run(world, SET_INVOICE_CASE, SetInvoiceCaseBody(invoice_id=inv["id"], invoice_version=inv["version"],
                                                        opportunity_id=None, note="viejo"))
    assert stale.value.code == "invoice_version_conflict"
    _run(world, SET_INVOICE_CASE, SetInvoiceCaseBody(invoice_id=inv["id"], invoice_version=now["version"],
                                                    opportunity_id=None, note="no era"))
    assert _invoice(world, doc["folio"])["case_link"] is None


@needs_db
def test_paid_then_unpaid_and_a_nota_cannot_be_paid(world) -> None:
    factura = _doc(world, total=119000)
    nota = _doc(world, doc_type=61, total=19000, ref=(33, factura["folio"]))
    _import(world, factura, nota)
    inv = _invoice(world, factura["folio"])
    _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(invoice_id=inv["id"], invoice_version=inv["version"],
                                                        paid_on=date.today(), note="pagó"))
    with pytest.raises(CommandRefused) as twice:
        _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(invoice_id=inv["id"], invoice_version=inv["version"] + 1,
                                                            paid_on=date.today(), note="otra vez"))
    assert twice.value.code == "invoice_already_paid"
    _run(world, MARK_INVOICE_UNPAID, MarkInvoiceUnpaidBody(invoice_id=inv["id"], invoice_version=inv["version"] + 1,
                                                            note="error"))
    assert _invoice(world, factura["folio"])["paid_on"] is None
    n = _invoice(world, nota["folio"], doc_type=61)
    with pytest.raises(CommandRefused) as not_collectable:
        _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(invoice_id=n["id"], invoice_version=n["version"],
                                                            paid_on=date.today(), note="no"))
    assert not_collectable.value.code == "invoice_not_collectable"


@needs_db
def test_the_read_nets_notas_into_the_balance_and_buckets_by_age(world) -> None:
    today = datetime.now(UTC).date()
    old = _doc(world, total=119000, issued_on=today - timedelta(days=45))
    nota = _doc(world, doc_type=61, total=19000, ref=(33, old["folio"]))
    fresh = _doc(world, total=50000)
    _import(world, old, nota, fresh)
    listing = _reads(world).list()
    by_folio = {i["folio"]: i for i in listing["items"]}
    assert nota["folio"] not in by_folio  # listed under its factura
    assert by_folio[old["folio"]]["balance"] == 100000
    assert [n["folio"] for n in by_folio[old["folio"]]["notes"]] == [nota["folio"]]
    assert by_folio[old["folio"]]["opportunity"]["id"] == world["case_a"]
    summary = listing["summary"]
    assert summary["por_cobrar"] == 150000
    assert summary["by_age"]["31-60"] == 100000 and summary["by_age"]["0-30"] == 50000
    one_case = _reads(world).list(world["case_a"])
    assert {i["folio"] for i in one_case["items"]} == {old["folio"]}


def _client_without_rut(world, name):
    [(org_id,)] = _owner(world, "insert into crm.organization (kind, name, confirmation) "
                                "values ('company', %s, 'confirmed') returning id::text", name)
    return org_id


def test_name_key_drops_the_legal_form_and_the_domain_ending() -> None:
    from origenlab_api.v2.invoices.repository import name_key

    assert name_key("LABORATORIO FICTICIO S.A.") == name_key("laboratorioficticio.cl") == name_key("Laboratorio Ficticio SA") == "laboratorioficticio"
    assert name_key("AGRICOLA Y GANADERA FICTICIA LIMITADA") == name_key("Agrícola y Ganadera Ficticia")
    assert name_key("S.A.") == ""  # nothing left to tell clients apart


@needs_db
def test_a_client_without_rut_is_found_by_name_and_its_case_by_the_neto_read_off_the_pdf(world) -> None:
    tag = uuid.uuid4().hex[:8]
    org = _client_without_rut(world, f"lab{tag}ficticio.cl")
    case = _case_with_quote(world["db"], world["operator_id"], org, None, tag, stage="won", pdf_net=397000)
    _case_with_quote(world["db"], world["operator_id"], org, None, tag + "x", stage="won")  # a second won case
    doc = _doc(world, total=472430, rut=f"{next(_RUTS)}-1", name=f"LAB{tag.upper()} FICTICIO S.A.")
    doc["net_amount"], doc["vat_amount"] = 397000, 75430
    assert _import(world, doc)["attached_to_case"] == 1
    inv = _invoice(world, doc["folio"])
    assert (inv["organization_id"], inv["opportunity_id"], inv["case_link"]) == (org, case, "machine")


@needs_db
def test_no_matching_quote_falls_back_to_the_clients_only_won_case(world) -> None:
    tag = uuid.uuid4().hex[:8]
    org = _client_without_rut(world, f"Agrícola Ficticia {tag}")
    won = _case_with_quote(world["db"], world["operator_id"], org, None, tag, stage="won")
    _case_with_quote(world["db"], world["operator_id"], org, 555000, tag + "q")  # still quoting, another amount
    doc = _doc(world, total=1500000, rut=f"{next(_RUTS)}-2", name=f"AGRICOLA FICTICIA {tag.upper()} LIMITADA")
    _import(world, doc)
    assert _invoice(world, doc["folio"])["opportunity_id"] == won


@needs_db
def test_a_later_import_guesses_again_unless_a_person_set_or_cleared_the_case(world) -> None:
    tag = uuid.uuid4().hex[:8]
    org = _client_without_rut(world, f"Planta Ficticia {tag}")
    name = f"PLANTA FICTICIA {tag.upper()} SPA"
    early, cleared = _doc(world, total=900000, rut=f"{next(_RUTS)}-3", name=name), _doc(world, total=800000,
                                                                                       rut=f"{next(_RUTS)}-4", name=name)
    _import(world, early, cleared)  # no won case yet
    assert _invoice(world, early["folio"])["opportunity_id"] is None
    inv = _invoice(world, cleared["folio"])
    _run(world, SET_INVOICE_CASE, SetInvoiceCaseBody(invoice_id=inv["id"], invoice_version=inv["version"],
                                                    opportunity_id=None, note="no tiene caso"))
    won = _case_with_quote(world["db"], world["operator_id"], org, None, tag, stage="won")
    again = _import(world, early, cleared, sha="c" * 64)
    assert (again["imported"], again["already_on_record"], again["attached_to_case"]) == (0, 2, 1)
    assert (_invoice(world, early["folio"])["opportunity_id"], _invoice(world, early["folio"])["case_link"]) == (won, "machine")
    assert _invoice(world, cleared["folio"])["opportunity_id"] is None  # a person's «Quitar» stands


@needs_db
def test_a_case_set_by_hand_teaches_the_rut_to_the_next_document(world) -> None:
    tag = uuid.uuid4().hex[:8]
    org = _client_without_rut(world, f"Nombre Distinto {tag}")
    case = _case_with_quote(world["db"], world["operator_id"], org, 333000, tag)
    rut = f"{next(_RUTS)}-5"
    first = _doc(world, total=111000, rut=rut, name="RAZON SOCIAL QUE NO CALZA")
    _import(world, first)
    inv = _invoice(world, first["folio"])
    assert inv["organization_id"] is None
    _run(world, SET_INVOICE_CASE, SetInvoiceCaseBody(invoice_id=inv["id"], invoice_version=inv["version"],
                                                    opportunity_id=case, note="es este"))
    assert _invoice(world, first["folio"])["organization_id"] == org
    second = _doc(world, total=333000, rut=rut, name="RAZON SOCIAL QUE NO CALZA")
    _import(world, second)
    assert _invoice(world, second["folio"])["opportunity_id"] == case


@needs_db
def test_a_factura_paid_in_two_parts_is_pagada_only_when_nothing_is_left(world) -> None:
    doc = _doc(world, total=238000)
    _import(world, doc)
    inv = _invoice(world, doc["folio"])
    half = _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(
        invoice_id=inv["id"], invoice_version=inv["version"], paid_on=date.today(), amount=119000, note="abono 50 %"))
    assert (half["paid_amount"], half["left"]) == (119000, 119000)
    assert _invoice(world, doc["folio"])["paid_on"] is None
    item = next(i for i in _reads(world).list()["items"] if i["folio"] == doc["folio"])
    assert (item["status"], item["billed"], item["paid_amount"], item["balance"]) == ("unpaid", 238000, 119000, 119000)
    assert [p["amount"] for p in item["payments"]] == [119000]
    with pytest.raises(CommandRefused) as too_much:
        _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(
            invoice_id=inv["id"], invoice_version=inv["version"] + 1, paid_on=date.today(), amount=200000, note="no"))
    assert too_much.value.code == "payment_exceeds_balance"
    rest = _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(
        invoice_id=inv["id"], invoice_version=inv["version"] + 1, paid_on=date.today(), note="el resto"))
    assert rest["left"] == 0
    assert _invoice(world, doc["folio"])["paid_on"] == date.today()
    summary = _reads(world).list()["summary"]
    assert summary["por_cobrar"] == 0


@needs_db
def test_undo_removes_the_latest_payment_and_the_factura_is_owed_again(world) -> None:
    doc = _doc(world, total=238000)
    _import(world, doc)
    inv = _invoice(world, doc["folio"])
    _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(
        invoice_id=inv["id"], invoice_version=inv["version"], paid_on=date.today(), amount=100000, note="abono"))
    _run(world, MARK_INVOICE_PAID, MarkInvoicePaidBody(
        invoice_id=inv["id"], invoice_version=inv["version"] + 1, paid_on=date.today(), note="resto"))
    _run(world, MARK_INVOICE_UNPAID, MarkInvoiceUnpaidBody(
        invoice_id=inv["id"], invoice_version=inv["version"] + 2, note="el resto no llegó"))
    now = _invoice(world, doc["folio"])
    assert now["paid_on"] is None
    item = next(i for i in _reads(world).list()["items"] if i["folio"] == doc["folio"])
    assert (item["paid_amount"], item["balance"]) == (100000, 138000)
    [(removed,)] = _owner(world, "select count(*) from crm.invoice_payment p join crm.invoice i on i.id = p.invoice_id "
                                 "where i.folio = %s and p.removed_at is not null", doc["folio"])
    assert removed == 1
