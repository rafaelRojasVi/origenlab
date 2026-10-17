"""«Rentabilidad»: where each authored quote's money goes (owner, 2026-10-17).

Pure: an engine input and its result in, the quote's economics out — the sale (neto), the product
cost, the import costs («gastos»: the quote's own expense lines, split freight / agent / customs /
transport / other) and the gross margin. No new formula: every figure is the engine's own
(`quoting.engine.compute`), read off its result —

* `sale_net_clp` — the engine's neto (K3);
* `product_cost_clp` — Σ quantity × the line's cost in pesos (J15), over the lines that are sold
  (priced), as the engine's cost base counts them;
* `import_costs_clp` — Σ of the expense lines in pesos (`engine.expense_clp`, E7:E12), which is the
  engine's `expenses_clp`;
* `gross_margin_clp` — sale − product cost − import costs; `gross_margin_pct` that over the sale.
  Whenever the engine allocates every expense (a principal exists, as approval requires) this is
  the engine's own margin (V15/K3), to the peso.

**Coverage.** A line without a cost makes the quote's margin unknowable: `cost_coverage` is `full`
(every item line has a cost), `partial` (some lack one) or `none` (all do, or there are no item
lines), with how many lack one. Only `full` carries a margin — a partial margin is never shown as
if it were complete.

Company overhead is not here (owner decision 2026-10-17): only each quote's own import costs.

**Which numbers.** An issued revision (approved, sent) is priced from its stored lines exactly as
approval froze them (`inputs.engine_input_from_rows`: nothing looked up again), so its economics
are its frozen values. A draft in the period report is priced the same way from its last save; in
its own workspace it is the live computation the rail shows.

Only quotes made in the Cotizador (`origin = authored`) are reported: an imported quote never had
its import costs recorded, and the owner wants no estimate (2026-10-17).

Money travels as strings (whole pesos); ratios as 4-decimal strings. Sales and admin only: the
route refuses a viewer before anything is read.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from origenlab_api.quoting.engine import EngineError, EngineInput, EngineResult, compute, expense_clp, margin_state, peso
from origenlab_api.quoting.expenses import EXPENSE_CATEGORIES, expense_category
from origenlab_api.v2.catalog.reads import _plain
from origenlab_api.v2.quotes.inputs import engine_input_from_rows

ZERO = Decimal(0)
_PCT = Decimal("0.0001")
#: The longest period the report reads at once, in months.
MAX_MONTHS = 36


def _pct(part: Decimal, whole: Decimal) -> Decimal | None:
    return None if whole == 0 else (part / whole).quantize(_PCT, rounding=ROUND_HALF_UP)


def _split() -> dict[str, Decimal]:
    return {c: ZERO for c in EXPENSE_CATEGORIES}


def economics(inp: EngineInput, result: EngineResult) -> dict[str, Any]:
    """One quote's economics from the engine's input and result, in Decimals (the reads print them)."""
    by_no = {line.line_no: line for line in inp.items}
    sold = [r for r in result.lines if r.line_total is not None]
    product_cost = peso(sum((by_no[r.line_no].quantity * r.cost_clp_unit for r in sold
                             if r.cost_clp_unit is not None), ZERO))
    split = _split()
    for e in inp.expenses:
        split[expense_category(e.label, e.calc_inputs)] += expense_clp(inp, e)
    imports = sum(split.values(), ZERO)
    missing = sum(1 for r in result.lines if r.cost_clp_unit is None)
    lines = len(result.lines)
    coverage = "none" if lines == 0 or missing == lines else ("partial" if missing else "full")
    sale = result.net
    margin = pct = None
    if coverage == "full":
        margin = sale - product_cost - imports
        pct = _pct(margin, sale)
    return {
        "sale_net_clp": sale, "product_cost_clp": product_cost, "import_costs_clp": imports,
        "import_costs_split": split, "gross_margin_clp": margin, "gross_margin_pct": pct,
        "margin_state": margin_state(pct, inp),
        "cost_coverage": {"state": coverage, "item_lines": lines, "lines_without_cost": missing},
    }


def economics_json(inp: EngineInput, result: EngineResult) -> dict[str, Any]:
    """`economics` with every amount as a string: what a response carries."""
    return _plain(economics(inp, result))


def frozen_economics(revision: dict[str, Any], lines: list[dict[str, Any]],
                     params: dict[str, Decimal | str]) -> dict[str, Any] | None:
    """A stored revision's economics, priced as approval prices it; None when it cannot be priced."""
    try:
        inp = engine_input_from_rows(revision, lines, params)
        return economics(inp, compute(inp))
    except EngineError:
        return None


# -- the period report (`GET /v2/quotes/economics`)

def month_range(from_month: str | None, to_month: str | None, today: dt.date) -> tuple[dt.date, dt.date]:
    """The first day of `from` and the first day after `to` (`YYYY-MM`). Neither given: the current
    year; only one: the rest of its year (from) or its year so far (to). Raises ValueError for a bad
    month, `to` before `from`, or more than `MAX_MONTHS` months."""
    def first(text: str) -> dt.date:
        if len(text) != 7 or text[4] != "-" or not (text[:4] + text[5:]).isdigit():
            raise ValueError("month must be YYYY-MM")
        return dt.date(int(text[:4]), int(text[5:]), 1)

    start = first(from_month) if from_month else None
    last = first(to_month) if to_month else None
    year = (start or last or today).year
    start = start or dt.date((last or today).year, 1, 1)
    last = last or dt.date(year, 12, 1)
    if last < start:
        raise ValueError("to is before from")
    months = (last.year - start.year) * 12 + last.month - start.month + 1
    if months > MAX_MONTHS:
        raise ValueError(f"at most {MAX_MONTHS} months")
    end = dt.date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    return start, end


class _Sum:
    def __init__(self) -> None:
        self.quotes = 0
        self.sale = ZERO
        self.product = ZERO
        self.imports = ZERO
        self.split = _split()
        self.full = {"quotes": 0, "sale": ZERO, "margin": ZERO}
        self.partial = {"quotes": 0, "sale": ZERO}
        self.none = {"quotes": 0, "sale": ZERO}

    def add(self, e: dict[str, Any]) -> None:
        self.quotes += 1
        self.sale += e["sale_net_clp"]
        self.product += e["product_cost_clp"]
        self.imports += e["import_costs_clp"]
        for k, v in e["import_costs_split"].items():
            self.split[k] += v
        state = e["cost_coverage"]["state"]
        bucket = getattr(self, state)
        bucket["quotes"] += 1
        bucket["sale"] += e["sale_net_clp"]
        if state == "full":
            bucket["margin"] += e["gross_margin_clp"]

    def json(self) -> dict[str, Any]:
        """The margin is the full-coverage quotes' only, over their own sale; the coverage says how
        much of the sale that is, so a total margin is never overstated."""
        return {
            "quotes": self.quotes, "sale_net_clp": self.sale, "product_cost_clp": self.product,
            "import_costs_clp": self.imports, "import_costs_split": dict(self.split),
            "gross_margin_clp": self.full["margin"] if self.full["quotes"] else None,
            "gross_margin_pct": _pct(self.full["margin"], self.full["sale"]),
            "coverage": {
                "full": {"quotes": self.full["quotes"], "sale_net_clp": self.full["sale"]},
                "partial": {"quotes": self.partial["quotes"], "sale_net_clp": self.partial["sale"]},
                "none": {"quotes": self.none["quotes"], "sale_net_clp": self.none["sale"]},
                "full_share_of_sale": _pct(self.full["sale"], self.sale),
            },
        }


def report(revisions: list[dict[str, Any]], lines: list[dict[str, Any]], params: dict[str, Decimal | str],
           start: dt.date, end: dt.date) -> dict[str, Any]:
    """The rows (newest first) and the totals by month and for the whole range.

    Approved and sent quotes count in the totals; a draft is listed, flagged `draft`, and only
    counted apart (`drafts`) — it is not a sale yet. A revision the engine cannot price (a stored
    line it refuses) is listed with `economics` null and counted nowhere.
    """
    by_revision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        by_revision[str(line["quote_revision_id"])].append(line)
    rows: list[dict[str, Any]] = []
    months: dict[str, _Sum] = {}
    total = _Sum()
    drafts = {"quotes": 0, "sale_net_clp": ZERO}
    for r in revisions:
        e = frozen_economics(r, by_revision.get(str(r["revision_id"]), []), params)
        draft = r["bucket"] == "draft"
        rows.append({
            "quote_id": str(r["quote_id"]), "revision_id": str(r["revision_id"]), "quote_number": r["quote_number"],
            "label": r["label"], "client": r["institution_name"], "status": r["bucket"], "draft": draft,
            "date": r["day"].isoformat(), "month": r["month"], "currency": r["quote_currency"],
            "source": "authored", "economics": e,
        })
        if e is None:
            continue
        if draft:
            drafts["quotes"] += 1
            drafts["sale_net_clp"] += e["sale_net_clp"]
            continue
        months.setdefault(r["month"], _Sum()).add(e)
        total.add(e)
    return _plain({
        "from": start.strftime("%Y-%m"), "to": (end - dt.timedelta(days=1)).strftime("%Y-%m"),
        "rows": rows,
        "months": [{"month": m, **months[m].json()} for m in sorted(months)],
        "totals": {**total.json(), "drafts": drafts},
    })
