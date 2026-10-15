from datetime import date
from decimal import Decimal as D

import pytest

from origenlab_api.quoting.engine import (EngineError, EngineInput, ExpenseLine, FxRate, ItemLine, compute, peso)

EUR = FxRate("EUR", D("1000"), date(2026, 1, 2), "bcentral")
USD = FxRate("USD", D("900"), date(2026, 1, 2), "bcentral")


def item(n, qty="1", kind="equipment", cost="1000", cur="EUR", mode="markup", pct="0.40", typed=None,
         imported=True, principal=False):
    return ItemLine(n, D(qty), kind, None if cost is None else D(cost), None if cost is None else cur, imported,
                    mode, None if pct is None else D(pct), None if typed is None else D(typed), principal)


def inp(items, expenses=(), rule="first_eur_equipment_over_threshold", mode="principal", fx=None):
    return EngineInput(fx=fx or {"EUR": EUR, "USD": USD}, items=tuple(items), expenses=tuple(expenses),
                       principal_rule=rule, allocation_mode=mode, iva_rate=D("0.19"),
                       principal_threshold_eur=D("3000"), margin_target_min=D("0.25"), margin_target_max=D("0.45"))


def test_peso_rounds_half_up():
    assert peso(D("2.5")) == D(3) and peso(D("2.4999")) == D(2) and peso(D("0")) == D(0)


def test_markup_line_without_expenses():
    r = compute(inp([item(1, cost="1234.56")]))
    line = r.lines[0]
    assert line.cost_clp_unit == D(1234560)            # 1234.56 × 1000
    assert line.unit_price == D(1728384)               # round(1234560 × 1.40)
    assert r.net == D(1728384) and r.iva == D(328393) and r.total == D(2056777)


def test_expenses_go_to_the_first_eur_equipment_over_threshold():
    items = [item(1, kind="accessory", cost="500", pct="0.45"), item(2, cost="4000"), item(3, cost="5000")]
    exp = [ExpenseLine(1, "Flete", D("300000"), "CLP", "profile"), ExpenseLine(2, "Agente", D("100"), "USD", "profile")]
    r = compute(inp(items, exp))
    assert r.expenses_clp == D(390000)                 # 300000 + round(100 × 900)
    assert r.principal_line_no == 2
    p = r.lines[1]
    assert p.is_principal and p.expense_to_line == D(390000)
    assert p.unit_base == D(4390000) and p.unit_price == D(6146000)


def test_highest_cost_imported_equipment_rule_any_currency():
    items = [item(1, cost="2000"), item(2, cost="3000", cur="USD")]   # 2,000,000 vs 2,700,000 CLP
    r = compute(inp(items, [ExpenseLine(1, "Flete", D("1000"), "CLP", "profile")], rule="highest_cost_imported_equipment"))
    assert r.principal_line_no == 2


def test_manual_principal():
    items = [item(1, cost="2000"), item(2, cost="3000", principal=True)]
    r = compute(inp(items, [ExpenseLine(1, "Flete", D("1000"), "CLP", "manual")], rule="manual"))
    assert r.principal_line_no == 2


def test_por_precio_uses_the_typed_price():
    r = compute(inp([item(1, cost="1000", mode="none", pct=None, typed="1500000.4")]))
    assert r.lines[0].unit_price == D(1500000)
    assert r.lines[0].real_markup == D("0.5")


def test_margin_mode():
    r = compute(inp([item(1, cost="1000", mode="margin", pct="0.20")]))
    assert r.lines[0].unit_price == D(1250000)          # 1,000,000 / 0.8


def test_fractional_quantity_rounds_the_line_total():
    r = compute(inp([item(1, qty="2.5", kind="consumable", cost="333", pct="0.45")]))
    line = r.lines[0]
    assert line.unit_price == D(482850) and line.line_total == D(1207125)
    assert r.net == sum(l.line_total for l in r.lines)


def test_proportional_allocation_splits_by_cost_and_gives_the_remainder_to_the_principal():
    items = [item(1, cost="1000"), item(2, kind="accessory", cost="1000", pct="0.45")]
    r = compute(inp(items, [ExpenseLine(1, "Flete", D("1001"), "CLP", "profile")],
                    rule="highest_cost_imported_equipment", mode="proportional_cost"))
    assert sorted(l.expense_to_line for l in r.lines) == [D(500), D(501)]
    assert next(l for l in r.lines if l.is_principal).expense_to_line == D(501)


def test_expenses_without_a_principal_warn():
    r = compute(inp([item(1, kind="accessory", cost="500", pct="0.45")], [ExpenseLine(1, "Flete", D("1000"), "CLP", "profile")]))
    assert r.principal_line_no is None and "principal_missing" in r.warnings
    assert r.lines[0].expense_to_line == D(0)


def test_line_without_cost_is_no_cost_and_needs_a_typed_price():
    r = compute(inp([item(1, cost=None, mode="none", pct=None, typed="10000")]))
    assert r.lines[0].margin_state == "no_cost" and r.lines[0].unit_price == D(10000)
    assert "no_cost:1" in r.warnings
    assert r.margin_state == "no_cost"


def test_markup_line_without_cost_has_no_price():
    r = compute(inp([item(1, cost=None)]))
    assert r.lines[0].unit_price is None and r.lines[0].line_total is None and r.net == D(0)


def test_margin_states():
    assert compute(inp([item(1, pct="0.10")])).lines[0].margin_state == "below_target"
    assert compute(inp([item(1, pct="0.40")])).lines[0].margin_state == "on_target"
    assert compute(inp([item(1, pct="1.00")])).lines[0].margin_state == "above_target"
    assert compute(inp([item(1, mode="none", pct=None, typed="900000")])).lines[0].margin_state == "loss"


def test_missing_fx_is_refused():
    with pytest.raises(EngineError) as exc:
        compute(inp([item(1, cur="USD")], fx={"EUR": EUR}))
    assert exc.value.code == "fx_missing:USD"


def test_clp_cost_needs_no_rate():
    r = compute(inp([item(1, cost="100000", cur="CLP", imported=False)], fx={}))
    assert r.lines[0].cost_clp_unit == D(100000)
