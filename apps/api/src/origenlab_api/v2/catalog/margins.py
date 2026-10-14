"""Gross margin of each quoted price against what the model cost us (owner, 2026-10-14).

Pure: the price history (`reads.price_history`, with the model's cost observations under `costs`)
and the exchange rates of each quote's day in, the same history out with, per single-unit line:

* `cost` — the supplier cost in force that day (the newest observation on or before it; a stale one
  yields to a current one; before the first observation, the earliest, flagged `after_quote`), the
  rate of that day and the cost in pesos;
* `margin` — (price − cost in pesos) / price, the **gross** margin: before import freight, customs
  and transport, which this history does not hold.

No cost or no rate → no `margin` (never a guess). Both keys are cost data: the route redacts them
for the viewer role (`redaction.COST_FIELDS` holds `cost` and `margin`).
"""

from __future__ import annotations

import datetime as dt
import statistics
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_PCT = Decimal("0.0001")
_PESO = Decimal("1")


def _day(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def cost_on(costs: list[dict[str, Any]], day: dt.date) -> dict[str, Any] | None:
    """The observation in force on `day`: newest on or before it, current before stale."""
    dated = [(d, c) for c in costs if (d := _day(c.get("as_of"))) is not None and c.get("price") is not None]
    if not dated:
        return None
    before = [(d, c) for d, c in dated if d <= day]
    if before:
        d, c = max(before, key=lambda dc: (not dc[1].get("is_stale"), dc[0]))
        return {**c, "after_quote": False}
    d, c = min(dated, key=lambda dc: dc[0])
    return {**c, "after_quote": True}


def _unit_price(line: dict[str, Any]) -> Decimal | None:
    qty = line.get("qty")
    if qty not in (None, "", "1", 1) and Decimal(str(qty)) != 1:
        return None
    raw = line.get("unit_price") or line.get("line_total")
    try:
        value = Decimal(str(raw))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return value if value > 0 else None


def attach_margins(history: dict[str, Any], rates: dict[str, dict[dt.date, dict[str, Any]]]) -> dict[str, Any]:
    costs = history.pop("costs", None) or []
    margins: list[Decimal] = []
    for line in history.get("items", []):
        day = _day(line.get("date"))
        price = _unit_price(line)
        if day is None or price is None or (line.get("currency") or "CLP") != "CLP":
            continue
        cost = cost_on(costs, day)
        if cost is None:
            continue
        currency = cost.get("currency") or "CLP"
        if currency == "CLP":
            fx, fx_as_of = None, None
            clp = Decimal(str(cost["price"]))
        else:
            rate = rates.get(currency, {}).get(day)
            if rate is None:
                continue
            fx, fx_as_of = Decimal(str(rate["clp_per_unit"])), rate["as_of"]
            clp = Decimal(str(cost["price"])) * fx
        clp = clp.quantize(_PESO, rounding=ROUND_HALF_UP)
        margin = ((price - clp) / price).quantize(_PCT, rounding=ROUND_HALF_UP)
        line["cost"] = {
            "price": _plain_number(cost["price"]), "currency": currency, "price_kind": cost.get("price_kind"),
            "as_of": str(_day(cost.get("as_of"))), "after_quote": cost["after_quote"],
            "fx": _plain_number(fx) if fx is not None else None,
            "fx_as_of": str(fx_as_of) if fx_as_of is not None else None, "clp": str(clp),
        }
        line["margin"] = str(margin)
        margins.append(margin)
    summary = history.setdefault("summary", {})
    if margins:
        summary["margin"] = {
            "last": str(margins[0]), "min": str(min(margins)), "max": str(max(margins)), "count": len(margins),
            "median": str(Decimal(statistics.median(margins)).quantize(_PCT, rounding=ROUND_HALF_UP)),
        }
    else:
        summary["margin"] = None
    return history


def _plain_number(value: Any) -> str:
    """«2500.000000» → «2500», «1100.00» → «1100», «1095.5» stays."""
    d = Decimal(str(value))
    text = format(d.normalize(), "f")
    return text
