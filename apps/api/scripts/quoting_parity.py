"""Prove the engine reproduces the owner's filled Plantilla costing sheets (private data, never committed).

    uv run --frozen --with openpyxl python scripts/quoting_parity.py <costing_sheets.json> [costs_dir]

The JSON is the one the reference plantilla_engine.py reads. It carries no principal choice, so
that is read from cell B18 of sheet "2 — INGRESAR DATOS" in <costs_dir>/<quote_number>-*.xlsx
(default: ../costs next to the JSON's folder); "AUTO"/empty maps to the spreadsheet rule, a number
to the manual choice. Prints `N/N values match` per sheet ordinal (never quote numbers, names or
prices) and exits 1 on any mismatch; a mismatch names the check, never the values.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from origenlab_api.quoting.engine import EngineInput, ExpenseLine, FxRate, ItemLine, compute

KIND = {"EQUIPO": "equipment", "ACCESORIO": "accessory", "SERVICIO": "service", "OTRO": "other"}
INPUT_SHEET = "2 — INGRESAR DATOS"


def _principal_choice(costs_dir: Path, quote_number: str):
    import openpyxl  # lazy: only this runner needs it

    xlsx = next(costs_dir.glob(f"{quote_number}-*.xlsx"))
    value = openpyxl.load_workbook(xlsx, data_only=True)[INPUT_SHEET]["B18"].value
    return "AUTO" if value in (None, "AUTO") else int(value)


def _d(x) -> D:
    return D(str(x))


def main(path: str, costs_dir: str | None = None) -> int:
    json_path = Path(path).expanduser()
    costs = Path(costs_dir).expanduser() if costs_dir else json_path.parent.parent / "costs"
    sheets = json.loads(json_path.read_text())
    ok = True
    for ordinal, s in enumerate(sheets, start=1):
        principal = _principal_choice(costs, s["quote_number"])
        manual = principal != "AUTO"
        items = tuple(
            ItemLine(
                int(l["item"]), _d(l["qty"]), KIND[l["type"]], _d(l["supplier_unit_cost"]),
                l["cost_currency"], l["cost_currency"] != "CLP",
                "none" if l["mode"] == "POR PRECIO" else "markup",
                None if l["mode"] == "POR PRECIO" else _d(l["markup_target"]),
                _d(l["price_used_clp"]) if l["mode"] == "POR PRECIO" else None,
                manual and principal == int(l["item"]),
            )
            for l in s["lines"]
        )
        expenses = tuple(
            ExpenseLine(i + 1, "gasto", _d(e["amount"]), e["currency"], "profile")
            for i, e in enumerate(s["expenses"])
        )
        fx = {
            "EUR": FxRate("EUR", _d(s["fx_eur"]), date(2026, 1, 1), "manual"),
            "USD": FxRate("USD", _d(s["fx_usd"]), date(2026, 1, 1), "manual"),
        }
        r = compute(EngineInput(
            fx, items, expenses, "manual" if manual else "first_eur_equipment_over_threshold",
            "principal", _d(s["iva"]), D("3000"), D("0.25"), D("0.45"),
        ))
        t = s["totals"]
        checks = [
            ("expenses", r.expenses_clp, _d(t["expenses_clp"])),
            ("cost_base", r.cost_base.quantize(D(1)), _d(round(t["cost_base_clp"]))),
            ("net", r.net, _d(t["net_sale_clp"])),
            ("total", r.total, _d(t["total_clp"])),
            ("margin", round(r.margin, 6), round(_d(t["gross_margin"]), 6)),
        ]
        for got, sl in zip(r.lines, s["lines"], strict=True):
            checks += [
                (f"L{got.line_no} price", got.unit_price, _d(sl["price_used_clp"])),
                (f"L{got.line_no} markup", round(got.real_markup, 6), round(_d(sl["real_markup"]), 6)),
                (f"L{got.line_no} margin", round(got.gross_margin, 6), round(_d(sl["gross_margin"]), 6)),
                (f"L{got.line_no} principal", got.is_principal, bool(sl["is_principal"])),
            ]
        bad = [c[0] for c in checks if c[1] != c[2]]
        ok &= not bad
        print(f"sheet {ordinal}: {len(checks) - len(bad)}/{len(checks)} values match"
              + (f"  MISMATCH {bad}" if bad else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
