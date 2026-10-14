"""Gross margin of each quoted price against what the product cost us, at that day's rate. Invented numbers."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from origenlab_api.v2.catalog.margins import attach_margins, cost_on


def obs(as_of, price, currency="EUR", kind="dealer_net", stale=False):
    return {"as_of": as_of, "price": price, "currency": currency, "price_kind": kind, "is_stale": stale}


def item(day, price, qty="1"):
    return {"date": day, "line_total": price, "unit_price": price, "qty": qty, "currency": "CLP", "document_number": "X"}


def test_the_cost_in_force_on_the_quote_day_is_the_newest_before_it() -> None:
    costs = [obs("2026-01-01", "3000"), obs("2026-09-07", "3100", kind="costing_sheet")]
    assert cost_on(costs, date(2026, 5, 1))["price"] == "3000"
    assert cost_on(costs, date(2026, 9, 10))["price"] == "3100"
    # Before the first observation: the earliest one, flagged as recorded after the quote.
    early = cost_on(costs, date(2025, 12, 1))
    assert early["price"] == "3000" and early["after_quote"] is True


def test_a_stale_observation_yields_to_a_current_one_of_the_same_period() -> None:
    costs = [obs("2026-03-01", "2500", stale=True), obs("2026-02-01", "3000")]
    assert cost_on(costs, date(2026, 4, 1))["price"] == "3000"


def test_each_quote_gets_its_cost_in_pesos_at_its_own_rate_and_its_margin() -> None:
    history = {"items": [item("2026-09-17", "7000000"), item("2026-04-23", "6800000")],
               "costs": [obs("2026-01-01", "2500")], "summary": {"times_quoted": 2}}
    rates = {"EUR": {date(2026, 9, 17): {"clp_per_unit": D("1100"), "as_of": date(2026, 9, 16)},
                     date(2026, 4, 23): {"clp_per_unit": D("1060"), "as_of": date(2026, 4, 23)}}}
    attach_margins(history, rates)
    new, old = history["items"]
    assert new["cost"] == {"price": "2500", "currency": "EUR", "price_kind": "dealer_net", "as_of": "2026-01-01",
                           "after_quote": False, "fx": "1100", "fx_as_of": "2026-09-16", "clp": "2750000"}
    assert new["margin"] == "0.6071"                       # (7000000 − 2750000) / 7000000
    assert old["cost"]["clp"] == "2650000" and old["margin"] == "0.6103"
    m = history["summary"]["margin"]
    assert (m["last"], m["min"], m["max"], m["count"]) == ("0.6071", "0.6071", "0.6103", 2)
    assert m["median"] == "0.6087"
    assert "costs" not in history                           # observations are not sent twice


def test_no_cost_or_no_rate_means_no_margin_never_a_guess() -> None:
    history = {"items": [item("2026-09-17", "7000000")], "costs": [], "summary": {}}
    attach_margins(history, {})
    assert "margin" not in history["items"][0] and history["summary"]["margin"] is None
    history = {"items": [item("2026-09-17", "7000000")], "costs": [obs("2026-01-01", "2500")], "summary": {}}
    attach_margins(history, {"EUR": {}})
    assert "margin" not in history["items"][0]


def test_a_peso_cost_needs_no_rate_and_a_multi_unit_line_is_not_compared() -> None:
    history = {"items": [item("2026-09-01", "100000"), item("2026-09-01", "300000", qty="3")],
               "costs": [obs("2026-08-01", "60000", currency="CLP")], "summary": {}}
    attach_margins(history, {})
    single, triple = history["items"]
    assert single["margin"] == "0.4000" and single["cost"]["fx"] is None
    assert "margin" not in triple
