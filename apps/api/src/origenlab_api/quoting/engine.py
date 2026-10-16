"""The Cotizador's pricing engine: the Plantilla Maestra's _CALCULOS sheet, in Decimal.

Pure: no database, no clock, no I/O. Every rule cites the spreadsheet cell it reproduces
(10-05 spec §5). CLP amounts are rounded to the peso half-up; the unit cost in CLP is rounded
before expenses are added (J15), the unit base is not (R15), prices are (S15/T15).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

ONE = Decimal(1)
ZERO = Decimal(0)


def peso(x: Decimal) -> Decimal:
    """Excel ROUND(x, 0) for money: half away from zero, to the peso."""
    return x.quantize(ONE, rounding=ROUND_HALF_UP)


class EngineError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FxRate:
    currency: str
    clp_per_unit: Decimal
    as_of: date
    source: str


@dataclass(frozen=True)
class ItemLine:
    line_no: int
    quantity: Decimal
    item_type: str
    unit_cost: Decimal | None
    cost_currency: str | None
    imported: bool
    margin_mode: str            # markup | margin | none
    margin_pct: Decimal | None
    typed_unit_price: Decimal | None
    is_principal: bool = False  # only read when principal_rule == "manual"


@dataclass(frozen=True)
class ExpenseLine:
    line_no: int
    label: str
    amount: Decimal
    currency: str
    method: str                 # profile | dhl_calc | manual
    calc_inputs: dict | None = None


@dataclass(frozen=True)
class EngineInput:
    fx: Mapping[str, FxRate]
    items: tuple[ItemLine, ...]
    expenses: tuple[ExpenseLine, ...]
    principal_rule: str
    allocation_mode: str
    iva_rate: Decimal
    principal_threshold_eur: Decimal
    margin_target_min: Decimal
    margin_target_max: Decimal


@dataclass(frozen=True)
class LineResult:
    line_no: int
    cost_clp_unit: Decimal | None
    expense_to_line: Decimal
    unit_base: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    real_markup: Decimal | None
    gross_margin: Decimal | None
    margin_state: str
    is_principal: bool


@dataclass(frozen=True)
class EngineResult:
    lines: tuple[LineResult, ...]
    expenses_clp: Decimal
    principal_line_no: int | None
    cost_base: Decimal
    net: Decimal
    iva: Decimal
    total: Decimal
    markup: Decimal | None
    margin: Decimal | None
    margin_state: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _rate(inp: EngineInput, currency: str) -> Decimal:
    if currency == "CLP":
        return ONE
    fx = inp.fx.get(currency)
    if fx is None:
        raise EngineError(f"fx_missing:{currency}")
    return fx.clp_per_unit


def _cost_clp(inp: EngineInput, line: ItemLine) -> Decimal | None:
    if line.unit_cost is None or line.cost_currency is None:
        return None
    return peso(line.unit_cost * _rate(inp, line.cost_currency))                       # J15


def _principal(inp: EngineInput, costs: dict[int, Decimal | None]) -> int | None:
    if inp.principal_rule == "manual":
        return next((l.line_no for l in inp.items if l.is_principal), None)
    if inp.principal_rule == "first_eur_equipment_over_threshold":                       # N15, G3
        return next((l.line_no for l in inp.items
                     if l.item_type == "equipment" and l.cost_currency == "EUR"
                     and l.unit_cost is not None and l.unit_cost > inp.principal_threshold_eur), None)
    candidates = [(costs[l.line_no], -l.line_no) for l in inp.items                      # 10-05 D3
                  if l.item_type == "equipment" and l.imported and costs[l.line_no] is not None]
    return -max(candidates)[1] if candidates else None


def _allocate(inp: EngineInput, costs: dict[int, Decimal | None], principal: int | None,
              expenses: Decimal) -> dict[int, Decimal]:
    alloc = {l.line_no: ZERO for l in inp.items}
    if expenses == 0 or principal is None:
        return alloc
    if inp.allocation_mode != "proportional_cost":
        alloc[principal] = expenses                                                      # P15
        return alloc
    weights = {l.line_no: l.quantity * costs[l.line_no] for l in inp.items if costs[l.line_no]}
    total = sum(weights.values(), ZERO)
    if total == 0:
        alloc[principal] = expenses
        return alloc
    for n, w in weights.items():
        alloc[n] = (expenses * w / total).quantize(ONE, rounding=ROUND_FLOOR)
    alloc[principal] += expenses - sum(alloc.values(), ZERO)
    return alloc


def _state(margin: Decimal | None, inp: EngineInput) -> str:
    if margin is None:
        return "no_cost"
    if margin < 0:
        return "loss"
    if margin < inp.margin_target_min:
        return "below_target"
    if margin > inp.margin_target_max:
        return "above_target"
    return "on_target"


def _guard(inp: EngineInput) -> None:
    for l in inp.items:
        if l.quantity <= 0:
            raise EngineError(f"quantity_invalid:{l.line_no}")
        if l.margin_mode == "margin" and l.margin_pct is not None and l.margin_pct >= ONE:
            raise EngineError(f"margin_pct_invalid:{l.line_no}")


def compute(inp: EngineInput) -> EngineResult:
    _guard(inp)
    warnings: list[str] = []
    expenses_clp = sum((peso(e.amount * _rate(inp, e.currency)) for e in inp.expenses), ZERO)   # E7:E12
    costs = {l.line_no: _cost_clp(inp, l) for l in inp.items}
    principal = _principal(inp, costs)
    if expenses_clp and principal is None:
        warnings.append("principal_missing")
    alloc = _allocate(inp, costs, principal, expenses_clp)
    out: list[LineResult] = []
    cost_base, net, any_missing = ZERO, ZERO, False
    for l in inp.items:
        cost = costs[l.line_no]
        exp = alloc[l.line_no]
        base = None if cost is None else cost + (exp / l.quantity)                        # Q15, R15
        if l.margin_mode == "none":
            price = None if l.typed_unit_price is None else peso(l.typed_unit_price)     # T15
        elif base is None or l.margin_pct is None:
            price = None
        elif l.margin_mode == "margin":
            price = peso(base / (ONE - l.margin_pct))
        else:
            price = peso(base * (ONE + l.margin_pct))                                     # S15
        total = None if price is None else peso(l.quantity * price)                       # W15
        if cost is None:
            any_missing = True
            warnings.append(f"no_cost:{l.line_no}")
        real_markup = None if (price is None or not base) else price / base - ONE         # U15
        gross = None if (price is None or base is None or price == 0) else (price - base) / price   # V15
        out.append(LineResult(l.line_no, cost, exp, base, price, total, real_markup, gross,
                              _state(gross, inp), l.line_no == principal))
        if total is not None:
            net += total                                                                  # K3
            if base is not None:
                cost_base += l.quantity * base                                            # J3
    iva = peso(net * inp.iva_rate)                                                        # L3
    markup = None if (cost_base == 0 or any_missing) else net / cost_base - ONE
    margin = None if (net == 0 or any_missing) else (net - cost_base) / net
    return EngineResult(tuple(out), expenses_clp, principal, cost_base, net, iva, net + iva,
                        markup, margin, _state(margin, inp), tuple(warnings))


__all__ = ["EngineError", "EngineInput", "EngineResult", "ExpenseLine", "FxRate", "ItemLine",
           "LineResult", "compute", "peso"]
