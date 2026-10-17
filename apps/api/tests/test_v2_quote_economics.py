"""«Rentabilidad» (`economics_report`, the workspace's `current.economics`) against PostgreSQL as `origenlab_api`.

Every name, number, cost and rate is invented. The quotes are built through the commands exactly as
the routes call them (the helpers of test_v2_quote_commands). This module has a database of its own,
so the totals below hold only its quotes.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from origenlab_api.quoting.engine import EngineInput, ExpenseLine, ItemLine, compute
from origenlab_api.quoting.expenses import expense_category
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.quotes.economics import economics, month_range
from origenlab_api.v2.quotes.reads import V2QuoteReads
from test_v2_quote_reads import _Counting
from test_v2_quote_commands import PROFILE, WARRANTY, _case, _make_operator, _org, _owner, _param, _run, fake_fx
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


@pytest.fixture(scope="module")
def world():
    for dsn in build_disposable_database():
        operator = _make_operator(dsn)
        institution = _org(dsn, "institution", "Universidad Ficticia")
        supplier = _org(dsn, "supplier", "Proveedor Ficticio")
        maker = _org(dsn, "manufacturer", "Fabricante Ficticio")
        _owner(dsn, "insert into catalog.supplier_terms (supplier_organization_id, currency, origin_country, route) "
                    "values (%s, 'EUR', 'DE', 'import_courier')", (supplier,))
        product = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name_es, "
                              "product_kind, weight_kg, origin_country, content_origin) values (%s, 'FX-100', "
                              "'Equipo ficticio', 'equipment', 10, 'DE', 'operator') returning id::text",
                         (maker,))[0][0]
        _owner(dsn, "insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, "
                    "currency, price_kind, provenance_note) values (%s, %s, now() - interval '3 days', 1000, 'EUR', "
                    "'dealer_net', 'prueba')", (supplier, product))
        _param(dsn, "quote_number_seed", 3100)
        for key, value in PROFILE.items():
            _param(dsn, key, value)
        _param(dsn, "quote_default_warranty", WARRANTY)
        yield {"dsn": dsn, "operator": operator, "institution": institution, "product": product}


def _reads(dsn: str) -> V2QuoteReads:
    # The real clock: the database stamps approvals with its own now(), and the default period is this year.
    return V2QuoteReads(psycopg.connect, runtime_dsn(dsn), fx_resolver=fake_fx, clock=lambda: datetime.now(UTC))


TERMS = {"warranty_terms_es": WARRANTY, "payment_terms": "30 días", "validity_days": 20}


def _full(world: dict) -> dict:
    """Imported equipment (1000 EUR at 1000) with the profile's four expense lines, and a CLP line."""
    return {"items": [{"product_id": world["product"], "description": "Equipo ficticio FX-100", "quantity": "1"},
                      {"description": "Línea libre", "quantity": "2", "unit_cost": "15000", "cost_currency": "CLP",
                       "margin_mode": "margin", "margin_pct": "0.25"}],
            "expense_method": "profile", "terms": TERMS}


def _partial(world: dict) -> dict:
    """The same equipment and a service with a typed price and no cost."""
    return {"items": [{"product_id": world["product"], "description": "Equipo ficticio FX-100", "quantity": "1"},
                      {"description": "Servicio sin costo", "quantity": "1", "margin_mode": "none",
                       "typed_unit_price": "50000"}],
            "expense_method": "profile", "terms": TERMS}


def _quote(world: dict, draft: dict, *, approve: bool) -> dict:
    case = _case(world["dsn"], world["operator"], world["institution"])
    created = _run(world, "create-quote", {"opportunity_id": case})
    saved = _run(world, "save-quote-draft", {"revision_id": created["revision_id"],
                                             "expected_version": created["version"], "draft": draft})
    if approve:
        _run(world, "approve-quote-revision", {"revision_id": created["revision_id"], "expected_version": saved["version"]})
    return {**created, "computed": saved["computed"]}


def _historical(world: dict) -> str:
    dsn = world["dsn"]
    case = _case(dsn, world["operator"], world["institution"])
    quote = _owner(dsn, "insert into crm.quote (opportunity_id, quote_number, number_origin) values (%s, %s, "
                        "'printed_historical') returning id::text", (case, f"H{uuid.uuid4().hex[:6]}"))[0][0]
    record = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', "
                         "%s, '{}'::jsonb) returning id", (f"hist-{uuid.uuid4().hex}",))[0][0]
    _owner(dsn, "insert into crm.quote_revision (quote_id, revision_no, status, origin, quote_currency, price_decimals, "
                "pdf_sha256, sent_at, origin_source_record_id, grand_total) values (%s, 1, 'sent', 'historical_import', "
                "'CLP', 0, %s, now(), %s, 119000)", (quote, "d" * 64, record))
    return quote


@pytest.fixture(scope="module")
def built(world):
    """One approved full quote, one approved partial one, a draft, a voided one and an imported one."""
    full = _quote(world, _full(world), approve=True)
    partial = _quote(world, _partial(world), approve=True)
    draft = _quote(world, _full(world), approve=False)
    voided = _quote(world, _full(world), approve=False)
    revision = _owner(world["dsn"], "select version from crm.quote_revision where id = %s", (voided["revision_id"],))[0][0]
    _run(world, "void-quote-revision", {"revision_id": voided["revision_id"], "expected_version": revision,
                                        "reason": "duplicada"})
    historical = _historical(world)
    return {"full": full, "partial": partial, "draft": draft, "voided": voided, "historical": historical}


def _report(world: dict, **kw) -> dict:
    return _reads(world["dsn"]).economics_report(from_month=kw.get("from_month"), to_month=kw.get("to_month"))


def _row(report: dict, quote: dict) -> dict:
    [row] = [r for r in report["rows"] if r["quote_id"] == quote["quote_id"]]
    return row


D = Decimal


@needs_db
def test_an_approved_quote_reports_its_frozen_sale_costs_import_split_and_margin(world, built) -> None:
    full = built["full"]
    row = _row(_report(world), full)
    totals = full["computed"]["totals"]
    e = row["economics"]
    assert (row["status"], row["draft"], row["source"], row["currency"]) == ("approved", False, "authored", "CLP")
    assert row["quote_number"] == full["quote_number"] and row["client"].startswith("Universidad Ficticia")
    assert row["date"][:7] == row["month"]
    assert D(e["sale_net_clp"]) == D(totals["net"])
    # 1000 EUR at 1000, plus 2 × 15 000 CLP.
    assert D(e["product_cost_clp"]) == 1_030_000
    assert {k: D(v) for k, v in e["import_costs_split"].items()} == {
        "freight": 500_000, "agent": 120_000, "customs": 180_000, "transport": 30_000, "other": 0}
    assert D(e["import_costs_clp"]) == D(totals["expenses_clp"]) == 830_000
    assert D(e["gross_margin_clp"]) == D(totals["net"]) - 1_030_000 - 830_000
    # The engine's own margin, to four decimals.
    assert D(e["gross_margin_pct"]) == D(totals["margin"]).quantize(D("0.0001"))
    assert e["cost_coverage"] == {"state": "full", "item_lines": 2, "lines_without_cost": 0}
    assert e["margin_state"] == totals["margin_state"]
    # The save's own `computed.economics` (the rail's box) is what approval froze.
    assert e == full["computed"]["economics"]


@needs_db
def test_a_line_without_cost_is_partial_coverage_and_carries_no_margin(world, built) -> None:
    e = _row(_report(world), built["partial"])["economics"]
    assert e["cost_coverage"] == {"state": "partial", "item_lines": 2, "lines_without_cost": 1}
    assert e["gross_margin_clp"] is None and e["gross_margin_pct"] is None and e["margin_state"] == "no_cost"
    # What is known is still said: the sale includes the service, the cost only the equipment.
    assert D(e["product_cost_clp"]) == 1_000_000 and D(e["import_costs_clp"]) == 830_000
    assert D(e["sale_net_clp"]) == D(built["partial"]["computed"]["totals"]["net"])


@needs_db
def test_drafts_are_listed_flagged_and_void_and_imported_quotes_are_not(world, built) -> None:
    report = _report(world)
    ids = {r["quote_id"] for r in report["rows"]}
    assert built["voided"]["quote_id"] not in ids and built["historical"] not in ids
    draft = _row(report, built["draft"])
    assert draft["status"] == "draft" and draft["draft"] is True
    # A draft is priced from its last save, as approval would price it.
    assert D(draft["economics"]["sale_net_clp"]) == D(built["draft"]["computed"]["totals"]["net"])
    assert report["totals"]["drafts"] == {"quotes": 1, "sale_net_clp": draft["economics"]["sale_net_clp"]}


@needs_db
def test_totals_count_issued_quotes_and_the_margin_only_over_full_coverage(world, built) -> None:
    report = _report(world)
    full, partial = (_row(report, built[k])["economics"] for k in ("full", "partial"))
    t = report["totals"]
    assert t["quotes"] == 2
    assert D(t["sale_net_clp"]) == D(full["sale_net_clp"]) + D(partial["sale_net_clp"])
    assert D(t["product_cost_clp"]) == D(full["product_cost_clp"]) + D(partial["product_cost_clp"])
    assert D(t["import_costs_clp"]) == 1_660_000 and D(t["import_costs_split"]["freight"]) == 1_000_000
    assert D(t["gross_margin_clp"]) == D(full["gross_margin_clp"])
    assert t["gross_margin_pct"] == full["gross_margin_pct"]
    cov = t["coverage"]
    assert cov["full"] == {"quotes": 1, "sale_net_clp": full["sale_net_clp"]}
    assert cov["partial"] == {"quotes": 1, "sale_net_clp": partial["sale_net_clp"]}
    assert cov["none"] == {"quotes": 0, "sale_net_clp": "0"}
    share = (D(full["sale_net_clp"]) / D(t["sale_net_clp"])).quantize(D("0.0001"))
    assert D(cov["full_share_of_sale"]) == share
    [month] = report["months"]
    assert month["month"] == _row(report, built["full"])["month"]
    assert {k: month[k] for k in ("quotes", "sale_net_clp", "gross_margin_pct")} == \
        {k: t[k] for k in ("quotes", "sale_net_clp", "gross_margin_pct")}
    assert report["params"]["margin_target_min"] == "0.25"


@needs_db
def test_the_period_filter_keeps_only_its_months(world, built) -> None:
    month = _row(_report(world), built["full"])["month"]
    same = _report(world, from_month=month, to_month=month)
    assert (same["from"], same["to"]) == (month, month)
    assert {r["quote_id"] for r in same["rows"]} >= {built["full"]["quote_id"], built["partial"]["quote_id"]}
    empty = _report(world, from_month="2001-01", to_month="2001-12")
    assert empty["rows"] == [] and empty["months"] == []
    assert empty["totals"]["quotes"] == 0 and empty["totals"]["gross_margin_pct"] is None
    assert empty["totals"]["coverage"]["full_share_of_sale"] is None
    for bad in ({"from_month": "2026-12", "to_month": "2026-01"}, {"from_month": "2026-13"},
                {"from_month": "2020-01", "to_month": "2026-12"}):
        with pytest.raises(CommandRefused) as exc:
            _report(world, **bad)
        assert exc.value.status_code == 422 and exc.value.code == "bad_period"


@needs_db
def test_the_workspace_carries_the_same_economics(world, built) -> None:
    reads = _reads(world["dsn"])
    report = _report(world)
    issued = reads.workspace(built["full"]["quote_id"])["current"]
    assert issued["status"] == "approved" and issued["economics"] == _row(report, built["full"])["economics"]
    # A draft: its live computation, which equals its save while nothing changed.
    live = reads.workspace(built["draft"]["quote_id"])["current"]
    assert live["needs_saving"] is False
    assert live["economics"] == _row(report, built["draft"])["economics"]
    assert reads.workspace(built["historical"])["current"]["economics"] is None


@needs_db
def test_the_report_reads_in_a_constant_number_of_statements(world, built) -> None:
    def statements(**period) -> int:
        counting = _Counting()
        reads = V2QuoteReads(counting.connect, runtime_dsn(world["dsn"]), fx_resolver=fake_fx,
                             clock=lambda: datetime.now(UTC))
        reads.economics_report(from_month=period.get("from_month"), to_month=period.get("to_month"))
        assert counting.connections == 1
        return counting.statements

    # The set-up, then three statements in one pipeline — never one per quote.
    assert statements() == statements(from_month="2001-01", to_month="2001-01") <= 4


# -- pure: the split and the coverage rules

def test_expense_categories_by_parameter_then_label() -> None:
    assert expense_category("Flete", {"parameter": "profile_freight_clp"}) == "freight"
    assert expense_category("Transporte local", {"parameter": "domestic_transport_clp"}) == "transport"
    assert expense_category("Flete DHL (Proveedor)", {"zone": "3"}) == "freight"
    assert expense_category("Despacho DHL", {}) == "agent"
    assert expense_category("Agente de aduana", None) == "agent"
    assert expense_category("Derechos", None) == "customs"
    assert expense_category("Derechos y aduana", None) == "customs"
    assert expense_category("Gastos de aduana", None) == "customs"
    assert expense_category("Embalaje especial", None) == "other"


def _inp(items, expenses=()) -> EngineInput:
    return EngineInput(fx={}, items=tuple(items), expenses=tuple(expenses), principal_rule="manual",
                       allocation_mode="principal", iva_rate=D("0.19"), principal_threshold_eur=D(3000),
                       margin_target_min=D("0.25"), margin_target_max=D("0.45"))


def test_no_item_line_or_no_cost_at_all_is_none_coverage() -> None:
    empty = _inp([])
    assert economics(empty, compute(empty))["cost_coverage"]["state"] == "none"
    typed = _inp([ItemLine(1, D(1), "service", None, None, False, "none", None, D(1000))],
                 [ExpenseLine(2, "Embalaje", D(500), "CLP", "manual")])
    e = economics(typed, compute(typed))
    assert e["cost_coverage"] == {"state": "none", "item_lines": 1, "lines_without_cost": 1}
    assert e["gross_margin_clp"] is None and e["import_costs_split"]["other"] == 500


def test_month_range() -> None:
    from datetime import date
    today = date(2026, 10, 17)
    assert month_range(None, None, today) == (date(2026, 1, 1), date(2027, 1, 1))
    assert month_range("2025-12", "2025-12", today) == (date(2025, 12, 1), date(2026, 1, 1))
    assert month_range(None, "2026-03", today) == (date(2026, 1, 1), date(2026, 4, 1))
    for bad in (("2026-00", None), ("2026-1", None), ("abcd-ef", None)):
        with pytest.raises(ValueError):
            month_range(*bad, today)
