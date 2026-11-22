"""«Finanzas» commands and reads against PostgreSQL, as `origenlab_api` (opt-in: `ORIGENLAB_V2_TEST_DSN`).

The four commands run end to end through `FinanceCommandRepository` — receipt, row and event in one
transaction — in a disposable database migrated to head (`tests/v2_command_harness.py`): until
migration 20261122100000 every one of them failed the `crm.domain_event` checks. An update writes
only at the version it was read at. The reads take a fixed Santiago day (`clock`), never the real
one. Every name, RUT and amount is fictitious.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.finance.commands import (
    SET_MONTHLY_TARGET,
    SET_PARTNER_RULE,
    UPSERT_EXPENSE,
    UPSERT_SUPPLIER_ORDER,
    SetMonthlyTargetBody,
    SetPartnerRuleBody,
    UpsertExpenseBody,
    UpsertSupplierOrderBody,
    validated_finance,
)
from origenlab_api.v2.identity import OperatorIdentity

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402

TODAY = date(2026, 11, 20)  # the Santiago day every read here is asked about
_FOLIOS = iter(range(50_000, 1_000_000))
_KEYS = iter(range(1, 1_000_000))
SANTIAGO_NOON = timezone(timedelta(hours=-3))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(dsn, sql, *params):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def world(db):
    """An admin operator, and empty finance and invoice tables (each test counts its own rows)."""
    for table, trigger in (("crm.invoice_payment", "invoice_payment_never_deleted"),
                           ("crm.invoice", "invoice_never_deleted")):
        _owner(db, f"alter table {table} disable trigger {trigger}")
        _owner(db, f"delete from {table}")
        _owner(db, f"alter table {table} enable trigger {trigger}")
    _owner(db, "delete from crm.supplier_order")
    _owner(db, "delete from crm.expense")
    tag = uuid.uuid4().hex[:10]
    [(operator_id,)] = _owner(db, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                                  "values (gen_random_uuid(), %s, 'Administradora Ficticia', 'admin', 'active') "
                                  "returning id::text", f"admin-{tag}@example.test")
    return {"db": db, "operator_id": operator_id, "tag": tag}


def _operator(world) -> OperatorIdentity:
    return OperatorIdentity(operator_id=world["operator_id"], email_norm="admin@example.test",
                            display_name="Administradora Ficticia", role="admin", status="active")


def _run(world, name, body):
    import psycopg

    from origenlab_api.v2.finance.repository import FinanceCommandRepository

    repo = FinanceCommandRepository(psycopg.connect, runtime_dsn(world["db"]))
    return repo.execute(command_name=name, operator=_operator(world), fields=validated_finance(name, body),
                        idempotency_key=f"pytest-finance-{next(_KEYS)}", digest=request_digest(name, body))


def _reads(world):
    import psycopg

    from origenlab_api.v2.finance.reads import FinanceReadRepository

    return FinanceReadRepository(psycopg.connect, runtime_dsn(world["db"]), clock=lambda: TODAY)


def _events(world, kind, aggregate_id):
    return _owner(world["db"], "select event_type, payload from crm.domain_event "
                               "where aggregate_kind = %s and aggregate_id = %s order by seq", kind, aggregate_id)


# ------------------------------------------------------------------ the four commands

@needs_db
def test_an_expense_is_recorded_and_updated_only_at_the_version_read(world) -> None:
    created = _run(world, UPSERT_EXPENSE, UpsertExpenseBody(
        month=date(2026, 11, 1), category="servidores", amount_clp=45000, paid_by="empresa", note="hosting"))
    assert (created["event_type"], created["version"]) == ("expense.created", 1)
    [(event, payload)] = _events(world, "expense", created["expense_id"])
    assert (event, payload["amount_clp"]) == ("expense.created", 45000)

    updated = _run(world, UPSERT_EXPENSE, UpsertExpenseBody(
        expense_id=created["expense_id"], expected_version=1, month=date(2026, 11, 1), category="servidores",
        amount_clp=50000, note="subió"))
    assert (updated["event_type"], updated["version"]) == ("expense.updated", 2)

    # A form opened before that save is stale: it writes nothing.
    with pytest.raises(CommandRefused) as stale:
        _run(world, UPSERT_EXPENSE, UpsertExpenseBody(
            expense_id=created["expense_id"], expected_version=1, month=date(2026, 11, 1), category="otros",
            amount_clp=1000, note="formulario viejo"))
    assert (stale.value.status_code, stale.value.code) == (409, "stale_version")
    [(amount, category, version)] = _owner(world["db"], "select amount_clp, category, version from crm.expense "
                                                        "where id = %s", created["expense_id"])
    assert (amount, category, version) == (50000, "servidores", 2)

    with pytest.raises(CommandRefused) as unversioned:
        validated_finance(UPSERT_EXPENSE, UpsertExpenseBody(
            expense_id=created["expense_id"], month=date(2026, 11, 1), category="otros", amount_clp=1, note="x"))
    assert (unversioned.value.status_code, unversioned.value.code) == (422, "expected_version_required")


@needs_db
def test_a_partner_payer_is_stored_generically_and_read_back_in_the_dashboards_name(world) -> None:
    out = _run(world, UPSERT_EXPENSE, UpsertExpenseBody(
        month=date(2026, 11, 1), category="arriendo", amount_clp=300000, paid_by="tatiana", note="arriendo"))
    [(stored,)] = _owner(world["db"], "select paid_by from crm.expense where id = %s", out["expense_id"])
    assert stored == "partner_a"
    _run(world, UPSERT_EXPENSE, UpsertExpenseBody(
        month=date(2026, 11, 1), category="otros", amount_clp=1000, paid_by="partner_b", note="genérico"))
    listing = _reads(world).expenses()
    assert listing["month"] == "2026-11-01"
    assert sorted(i["paid_by"] for i in listing["items"]) == ["sebastian", "tatiana"]
    assert {i["version"] for i in listing["items"]} == {1}


@needs_db
def test_a_supplier_order_is_recorded_and_updated_only_at_the_version_read(world) -> None:
    created = _run(world, UPSERT_SUPPLIER_ORDER, UpsertSupplierOrderBody(
        supplier_name="Proveedor Ficticio Ltda", ordered_on=date(2026, 11, 2), amount_original=500000,
        currency="CLP", note="pedido"))
    assert (created["event_type"], created["version"]) == ("supplier_order.created", 1)
    [(amount_clp,)] = _owner(world["db"], "select amount_clp from crm.supplier_order where id = %s",
                             created["order_id"])
    assert amount_clp == 500000  # a CLP order states its pesos
    updated = _run(world, UPSERT_SUPPLIER_ORDER, UpsertSupplierOrderBody(
        order_id=created["order_id"], expected_version=1, supplier_name="Proveedor Ficticio Ltda",
        ordered_on=date(2026, 11, 2), amount_original=500000, currency="CLP", received_on=date(2026, 11, 10),
        note="llegó"))
    assert (updated["event_type"], updated["version"]) == ("supplier_order.updated", 2)
    with pytest.raises(CommandRefused) as stale:
        _run(world, UPSERT_SUPPLIER_ORDER, UpsertSupplierOrderBody(
            order_id=created["order_id"], expected_version=1, supplier_name="Proveedor Ficticio Ltda",
            ordered_on=date(2026, 11, 2), amount_original=1, currency="CLP", note="formulario viejo"))
    assert (stale.value.status_code, stale.value.code) == (409, "stale_version")
    [(received, version)] = _owner(world["db"], "select received_on, version from crm.supplier_order where id = %s",
                                   created["order_id"])
    assert (received, version) == (date(2026, 11, 10), 2)
    assert [e for e, _ in _events(world, "supplier_order", created["order_id"])] == [
        "supplier_order.created", "supplier_order.updated"]


@needs_db
def test_a_monthly_target_is_set_under_its_uuid_aggregate(world) -> None:
    month = date(2026, 12, 1)
    first = _run(world, SET_MONTHLY_TARGET, SetMonthlyTargetBody(month=month, sales_target_net=7_000_000, note="meta"))
    _run(world, SET_MONTHLY_TARGET, SetMonthlyTargetBody(month=month, sales_target_net=8_000_000, note="sube"))
    assert first["month"] == "2026-12-01"
    [(aggregate_id, target)] = _owner(world["db"], "select aggregate_id::text, sales_target_net from crm.monthly_target "
                                                   "where month = %s", month)
    assert target == 8_000_000
    assert [p["sales_target_net"] for _, p in _events(world, "monthly_target", aggregate_id)] == [7_000_000, 8_000_000]


@needs_db
def test_a_partner_rule_takes_the_dashboards_names_and_reads_back_both(world) -> None:
    body = SetPartnerRuleBody.model_validate({
        "valid_from": "2027-01-01", "rule_name": "opcion_d", "arriendo_clp": 300000, "sueldo_tatiana": 100000,
        "tasa_sebastian": 0.015, "split_pct": 0.5, "note": "nueva regla"})
    out = _run(world, SET_PARTNER_RULE, body)
    [(aggregate_id, salary, rate)] = _owner(world["db"], "select aggregate_id::text, partner_a_salary, partner_b_rate "
                                                         "from crm.partner_rule where id = %s", out["rule_id"])
    assert (salary, float(rate)) == (100000, 0.015)
    assert [e for e, _ in _events(world, "partner_rule", aggregate_id)] == ["partner_rule.set"]
    rule = _reads(world).settings()["partner_rule"]
    assert (rule["partner_a_salary"], rule["partner_b_rate"]) == (100000, 0.015)
    assert (rule["sueldo_tatiana"], rule["tasa_sebastian"]) == (100000, 0.015)  # the dashboard's names
    with pytest.raises(ValueError):  # the rent is an input: no default amount
        SetPartnerRuleBody.model_validate({"valid_from": "2027-01-01", "rule_name": "actual_50_50", "note": "x"})


# ------------------------------------------------------------------ the reads

def _invoice(world, *, doc_type=33, issued_on, total, ref=None, paid_on=None):
    folio = next(_FOLIOS)
    net = round(total / 1.19) if doc_type != 34 else total
    [(invoice_id,)] = _owner(
        world["db"],
        "insert into crm.invoice (doc_type, folio, issued_on, client_rut, client_name, net_amount, vat_amount, "
        "total_amount, reference_doc_type, reference_folio, paid_on) values (%s, %s, %s, '76000000-1', "
        "'CLIENTE FICTICIO SPA', %s, %s, %s, %s, %s, %s) returning id::text",
        doc_type, folio, issued_on, net, total - net, total, ref[0] if ref else None, ref[1] if ref else None, paid_on)
    return invoice_id, folio


@needs_db
def test_the_sales_chart_keeps_the_first_of_the_month_in_its_month(world) -> None:
    _invoice(world, issued_on=date(2026, 10, 1), total=119000)
    _invoice(world, issued_on=date(2026, 11, 1), total=238000)
    history = {h["month"]: h["neto"] for h in _reads(world).mes()["monthly_history"]}
    assert history == {"2026-10-01": 100000, "2026-11-01": 200000}


@needs_db
def test_a_debit_note_after_pagada_is_owed_again_everywhere(world) -> None:
    import psycopg

    from origenlab_api.v2.invoices.commands import IMPORT_INVOICES, MARK_INVOICE_PAID, MarkInvoicePaidBody, validated_invoice
    from origenlab_api.v2.invoices.reads import InvoiceReadRepository
    from origenlab_api.v2.invoices.repository import V2InvoiceCommandRepository

    repo = V2InvoiceCommandRepository(psycopg.connect, runtime_dsn(world["db"]))
    invoice_id, folio = _invoice(world, issued_on=date(2026, 11, 3), total=119000)
    body = MarkInvoicePaidBody(invoice_id=invoice_id, invoice_version=1, paid_on=date(2026, 11, 10), note="pagó")
    repo.execute(command_name=MARK_INVOICE_PAID, operator=_operator(world),
                 fields=validated_invoice(MARK_INVOICE_PAID, body, today=TODAY),
                 idempotency_key=f"pytest-finance-{next(_KEYS)}", digest=request_digest(MARK_INVOICE_PAID, body))
    assert _reads(world).mes()["por_cobrar"]["total"] == 0

    nota = {"doc_type": 56, "folio": next(_FOLIOS), "issued_on": "2026-11-12", "client_rut": "76000000-1",
            "client_name": "CLIENTE FICTICIO SPA", "exempt_amount": 0, "net_amount": 10000, "vat_amount": 1900,
            "total_amount": 11900, "reference_doc_type": 33, "reference_folio": folio}
    repo.execute(command_name=IMPORT_INVOICES, operator=_operator(world),
                 fields={"documents": [nota], "skipped_types": {}, "sha256": "c" * 64, "filename": "RCV.csv",
                         "note": "nota de débito"},
                 idempotency_key=f"pytest-finance-{next(_KEYS)}", digest="c" * 64)
    [(paid_on, version)] = _owner(world["db"], "select paid_on, version from crm.invoice where id = %s", invoice_id)
    assert paid_on is None  # «Pagada» no more: 11.900 are owed
    assert _events(world, "invoice", invoice_id)[-1][0] == "invoice.unpaid"

    mes = _reads(world).mes()
    assert (mes["por_cobrar"]["total"], mes["por_cobrar"]["count"]) == (11900, 1)
    assert mes["por_cobrar"]["by_age"]["0-30"] == 11900
    assert [c["balance_iva"] for c in _reads(world).resumen()["cobrar_hoy"]] == [11900]
    item = next(i for i in InvoiceReadRepository(psycopg.connect, runtime_dsn(world["db"])).list()["items"]
                if i["invoice_id"] == invoice_id)
    assert (item["status"], item["billed"], item["paid_amount"], item["balance"]) == ("unpaid", 130900, 119000, 11900)

    # The rest is paid like any other balance.
    rest = MarkInvoicePaidBody(invoice_id=invoice_id, invoice_version=version, paid_on=date(2026, 11, 15), note="resto")
    out = repo.execute(command_name=MARK_INVOICE_PAID, operator=_operator(world),
                       fields=validated_invoice(MARK_INVOICE_PAID, rest, today=TODAY),
                       idempotency_key=f"pytest-finance-{next(_KEYS)}", digest=request_digest(MARK_INVOICE_PAID, rest))
    assert out["left"] == 0
    assert _reads(world).mes()["por_cobrar"]["total"] == 0


@needs_db
def test_the_cash_forecast_does_not_count_a_prepaid_order_as_outflow(world) -> None:
    _run(world, UPSERT_SUPPLIER_ORDER, UpsertSupplierOrderBody(
        supplier_name="Proveedor Pagado SpA", ordered_on=date(2026, 11, 2), paid_on=date(2026, 11, 2),
        amount_original=400000, currency="CLP", note="pagado por adelantado"))
    _run(world, UPSERT_SUPPLIER_ORDER, UpsertSupplierOrderBody(
        supplier_name="Proveedor Por Pagar SpA", ordered_on=date(2026, 11, 2), amount_original=150000,
        currency="CLP", note="por pagar"))
    caja = _reads(world).resumen()["caja"]
    assert {c["sale_confirmado"] for c in caja} == {150000}
    # Capital atado still counts both: the money is tied up until the goods arrive.
    assert _reads(world).mes()["capital_atado"]["total"] == 550000


@needs_db
def test_the_month_margin_is_the_engines_and_counts_logistics_or_refuses(world) -> None:
    db = world["db"]

    def won_case(lines, *, closed_at):
        import psycopg

        [(org,)] = _owner(db, "insert into crm.organization (kind, name, confirmation) values ('company', %s, "
                              "'confirmed') returning id::text", f"Instituto Margen {uuid.uuid4().hex[:8]}")
        with psycopg.connect(db) as conn, conn.cursor() as cur:  # one transaction: the institution check is deferred
            cur.execute("set role origenlab_owner")
            cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) values "
                        "('Caso margen ficticio', 'lead', %s, %s) returning id::text", (world["operator_id"], org))
            case = cur.fetchone()[0]
            cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                        "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                        "current_date, 'confirmed', %s)", (case, org, world["operator_id"]))
        [(quote,)] = _owner(db, "insert into crm.quote (opportunity_id, quote_number) values (%s, %s) returning id::text",
                            case, f"Q-M{uuid.uuid4().hex[:8]}")
        [(rev,)] = _owner(db, "insert into crm.quote_revision (quote_id, revision_no, status, quote_currency, "
                              "price_decimals, tax_rate, expense_allocation_mode) values (%s, 1, 'draft', 'CLP', 0, "
                              "0.19, 'principal') returning id::text", quote)
        for no, (kind, cost, price) in enumerate(lines, start=1):
            # A cost is stored with its rate snapshot (all five columns or none); a peso is a peso.
            snapshot = ("CLP", cost, 1, date(2026, 11, 1), "manual") if cost is not None else (None,) * 5
            if kind == "item":
                _owner(db, "insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, "
                           "cost_currency, unit_cost, fx_rate, fx_as_of, fx_source, margin_mode, unit_price_qc, "
                           "line_total_qc, item_type, is_principal) values (%s, %s, 'item', 'Equipo ficticio', 1, "
                           "%s, %s, %s, %s, %s, 'none', %s, %s, 'equipment', %s)",
                       rev, no, *snapshot, price, price, no == 1)
            else:
                _owner(db, "insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity, "
                           "cost_currency, unit_cost, fx_rate, fx_as_of, fx_source, expense_method) values (%s, %s, "
                           "'logistics', 'Flete', 1, %s, %s, %s, %s, %s, 'manual')", rev, no, *snapshot)
        _owner(db, "update crm.quote_revision set status = 'approved', approved_at = now(), approved_by_operator_id = %s, "
                   "party_snapshot = '{\"sold_to\": {}}', party_snapshot_version = 1, subtotal = 0, discount_total = 0, "
                   "tax_base = 0, tax_total = 0, grand_total = 0, totals_computed_at = now() where id = %s",
               world["operator_id"], rev)
        for step in ("qualifying", "qualified", "quoting", "negotiating"):
            _owner(db, "update crm.opportunity set stage = %s where id = %s", step, case)
        _owner(db, "update crm.opportunity set stage = 'won', closed_at = %s, won_quote_id = %s, won_revision_no = 1 "
                   "where id = %s", closed_at, quote, case)
        return case

    in_month = datetime(2026, 11, 12, 12, 0, tzinfo=SANTIAGO_NOON)
    # Sale 1.000.000 with a typed price; item cost 500.000 and freight 100.000: margin 40 %, not 50 %.
    won_case([("item", 500000, 1000000), ("logistics", 100000, None)], closed_at=in_month)
    ind = _reads(world).resumen()["indicadores"]
    assert (ind["margen_mes_known"], ind["margen_mes_subtotal"], ind["margen_mes_costo"], ind["margen_mes_pct"]) == (
        True, 1000000, 600000, 40.0)

    # A won quote with a costless line makes the month's margin unknown, not a partial figure.
    won_case([("item", None, 200000)], closed_at=in_month)
    ind = _reads(world).resumen()["indicadores"]
    assert (ind["margen_mes_known"], ind["margen_mes_subtotal"], ind["margen_mes_pct"]) == (False, 0, 0.0)
    assert (ind["margen_mes_revisions"], ind["margen_mes_incomplete"]) == (2, 1)
