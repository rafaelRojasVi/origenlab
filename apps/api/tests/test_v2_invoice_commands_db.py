"""The «Pagos» invoice commands and read against PostgreSQL (opt-in: `ORIGENLAB_V2_TEST_DSN`).

Runs in a disposable database (`tests/v2_command_harness.py`) as `origenlab_api`: an import adds new
folios only, finds the client by RUT and attaches the one case whose quote has the document's total,
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


def _case_with_quote(db, operator_id, organization_id, total, tag):
    """A case of the organization (its confirmed requesting institution) with one approved CLP quote."""
    import psycopg

    net = round(total / 1.19)
    with psycopg.connect(db) as conn, conn.cursor() as cur:  # one transaction: the institution check is deferred
        cur.execute("set role origenlab_owner")
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                    "values (%s, 'lead', %s, %s) returning id::text", (f"Caso ficticio {tag}", operator_id, organization_id))
        case_id = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                    "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', current_date, "
                    "'confirmed', %s)", (case_id, organization_id, operator_id))
        cur.execute("insert into crm.quote (opportunity_id, quote_number, created_by_operator_id) "
                    "values (%s, %s, %s) returning id", (case_id, f"CN9{uuid.uuid4().hex[:8]}", operator_id))
        quote_id = cur.fetchone()[0]
        cur.execute(
            "insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, price_decimals, "
            "subtotal, discount_total, tax_base, tax_total, grand_total, totals_computed_at, "
            "approved_at, approved_by_operator_id, party_snapshot, party_snapshot_version) "
            "values (%s, 1, 'approved', 'CLP', 0, %s, 0, %s, %s, %s, now(), now(), %s, '{}'::jsonb, 1)",
            (quote_id, net, net, total - net, total, operator_id))
    return case_id


@pytest.fixture
def world(db):
    """A sales operator, an organization with a RUT, and two of its cases (quotes of 119.000 and 238.000)."""
    tag = uuid.uuid4().hex[:12]
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


def _doc(world, doc_type=33, folio=None, total=119000, rut=None, issued_on=None, ref=None):
    return {"doc_type": doc_type, "folio": folio or next(_FOLIOS),
            "issued_on": (issued_on or date.today()).isoformat(), "client_rut": rut or world["rut"],
            "client_name": "INSTITUTO FICTICIO", "exempt_amount": 0, "net_amount": round(total / 1.19),
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
