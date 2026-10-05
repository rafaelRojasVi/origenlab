#!/usr/bin/env python3
"""Load supplier documents and costing sheets into the catalog as per-deal cost observations.

The inputs are private JSON files outside this public repository, produced by the cost-extraction
study:

    <document>.json   {"doc_type", "issuer", "recipient", "doc_number", "date", "currency", "incoterm",
                       "lines": [{"model", "description", "qty", "unit_price", "discount_pct",
                                  "net_unit_price", "line_total"}], "packing", "freight", "total"}
    <costing>.json    [{"fx_date", "lines": [{"brand", "model", "supplier_unit_cost", "cost_currency", ...}], ...}]
                      (a list of sheets; one sheet or {"sheets": [...]} also work). Only `fx_date` and
                      `lines` are read; quote_number, source, expenses and totals never are.

    uv run python scripts/catalog/import_supplier_documents.py plan --out ~/data/…/catalog-import-<ts> \\
        --documents <dir or file.json> [--documents …] --costing-sheets <sheets.json> \\
        [--costing-as-of YYYY-MM-DD] [--domestic-supplier NAME] [--route import_courier]
    … apply / verify / rollback as every catalog importer (`_common.py`).

What each document becomes (price = `net_unit_price`, else `unit_price`; `as_of` = the document date):

* `supplier_quote` -> `supplier_offer`, `supplier_proforma` -> `order_confirmation`, both for the issuer;
  `purchase_order` -> `purchase_order` for the recipient (the supplier), only if OrigenLab issued it and
  the currency is not CLP or the supplier is one of `--domestic-supplier`. `source_document` is the
  document number. When a discount and both prices are given, the list price and discount (a percent in
  the file, a fraction in the column) are kept too.
* costing sheet lines -> `costing_sheet` for the brand, `as_of` = the sheet's `fx_date`; `source_document` is the literal "costing sheet".
  The quote number, exchange rates and anything else about the client's quote never leave the file.
* `sales_invoice`, `company_paperwork` and a purchase order that fails the rule are skipped and counted
  by reason, with the sha256 of their path.
* Ortoalresa documents also give `supplier_terms.packing_pct` = packing / subtotal (4 decimals), written
  only if the supplier has no terms yet.

A malformed file (a missing field, a non-numeric price, an unknown `doc_type`) refuses the whole plan,
naming the file's basename and the field. Every input path, name and string passes the Labdelivery
refusal; a Labdelivery issuer or recipient is refused.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from origenlab_api.v2.catalog import importing  # noqa: E402
from origenlab_api.v2.catalog.keys import model_key, refuse_labdelivery  # noqa: E402

import _common  # noqa: E402

IMPORTER = "supplier_documents"
KINDS = ("document", "costing_sheet")
ROUTES = ("import_courier", "import_freight", "domestic")
TERMS_CURRENCIES = ("EUR", "USD", "CLP")
KIND_OF = {"supplier_quote": "supplier_offer", "supplier_proforma": "order_confirmation",
           "purchase_order": "purchase_order"}
SKIPPED_TYPES = ("sales_invoice", "company_paperwork")
SKIP_REASONS = ("sales_invoice", "company_paperwork", "purchase_order_rule")
COSTING_SOURCE_DOCUMENT = "costing sheet"
PACKING_SUPPLIERS = ("ortoalresa",)
OWN_NAME = "origenlab"
_SIX = Decimal("0.000001")
_FOUR = Decimal("0.0001")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class _Bad(ValueError):
    """A malformed input; the message starts with the file's basename and the field."""


# ------------------------------------------------------------------ values

def _fail(name: str, field: str, why: str) -> _Bad:
    return _Bad(f"{name}: {field}: {why}")


def _need(name: str, mapping: dict[str, Any], field: str, label: str | None = None) -> Any:
    label = label or field
    if field not in mapping or mapping[field] is None or (isinstance(mapping[field], str) and not mapping[field].strip()):
        raise _fail(name, label, "missing")
    return mapping[field]


def _number(name: str, field: str, value: Any) -> Decimal:
    try:
        number = importing.to_decimal(value)
    except ValueError:
        number = None
    if number is None or not number.is_finite():
        raise _fail(name, field, "not a number")
    return number


def _price(name: str, field: str, value: Any) -> Decimal:
    number = _number(name, field, value)
    if number < 0:
        raise _fail(name, field, "negative")
    return number.quantize(_SIX)


def _currency(name: str, field: str, value: Any) -> str:
    if not isinstance(value, str) or not _CURRENCY.match(value):
        raise _fail(name, field, "not a three-letter upper-case currency code")
    return value


def _day(name: str, field: str, value: Any) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise _fail(name, field, "not an ISO date (YYYY-MM-DD)") from None


def _text(name: str, field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(name, field, "not text")
    return value.strip()


def _is_own(name: str) -> bool:
    return importing.norm_name(name).startswith(OWN_NAME)


# ------------------------------------------------------------------ documents

class _Obs(dict):  # a parsed observation, kept as a plain dict with a fixed set of keys
    pass


def _lines(name: str, doc: dict[str, Any]) -> list[dict[str, Any]]:
    lines = doc.get("lines")
    if not isinstance(lines, list):
        raise _fail(name, "lines", "not a list")
    for n, line in enumerate(lines):
        if not isinstance(line, dict):
            raise _fail(name, f"lines[{n}]", "not an object")
    return lines


def _document_observations(name: str, doc: dict[str, Any], *, domestic: set[str], skipped: Counter[str],
                           skip_log: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """(observations, supplier terms or None) of one supplier document; a skipped type gives ([], None)."""
    doc_type = _need(name, doc, "doc_type")
    if doc_type in SKIPPED_TYPES:
        skipped[f"skipped_{doc_type}"] += 1
        skip_log.append(doc_type)
        return [], None
    if doc_type not in KIND_OF:
        raise _fail(name, "doc_type", "unknown document type")
    issuer = _text(name, "issuer", _need(name, doc, "issuer"))
    recipient = _text(name, "recipient", _need(name, doc, "recipient"))
    number = _text(name, "doc_number", _need(name, doc, "doc_number"))
    as_of = _day(name, "date", _need(name, doc, "date"))
    currency = _currency(name, "currency", _need(name, doc, "currency"))
    lines = _lines(name, doc)
    if doc_type == "purchase_order":
        if not _is_own(issuer):
            skipped["skipped_purchase_order_rule"] += 1
            skip_log.append("purchase_order_rule")
            return [], None
        supplier = recipient
        if currency == "CLP" and importing.norm_name(supplier) not in domestic:
            skipped["skipped_purchase_order_rule"] += 1
            skip_log.append("purchase_order_rule")
            return [], None
    else:
        if _is_own(issuer):
            raise _fail(name, "issuer", "a supplier document issued by OrigenLab")
        supplier = issuer
    if _is_own(supplier):
        raise _fail(name, "recipient", "the supplier is OrigenLab")

    observations: list[dict[str, Any]] = []
    subtotal = Decimal(0)
    for n, line in enumerate(lines):
        at = f"lines[{n}]"
        unit = line.get("unit_price")
        net = line.get("net_unit_price")
        unit_price = None if unit is None or unit == "" else _price(name, f"{at}.unit_price", unit)
        net_price = None if net is None or net == "" else _price(name, f"{at}.net_unit_price", net)
        total = line.get("line_total")
        if total is not None and total != "":
            subtotal += _price(name, f"{at}.line_total", total)
        elif line.get("qty") not in (None, "") and (net_price or unit_price) is not None:
            subtotal += _number(name, f"{at}.qty", line["qty"]) * (net_price if net_price is not None else unit_price)
        model = _text(name, f"{at}.model", _need(name, line, "model", f"{at}.model"))
        price = net_price if net_price is not None else unit_price
        discount = None
        if line.get("discount_pct") not in (None, ""):
            percent = _number(name, f"{at}.discount_pct", line["discount_pct"])
            if not 0 <= percent < 100:
                raise _fail(name, f"{at}.discount_pct", "not a percent in [0, 100)")
            if net_price is not None and unit_price is not None:
                discount = (percent / 100).quantize(_SIX)
        observations.append({
            "manufacturer": supplier, "supplier": supplier, "model_number": model,
            "name": (line.get("description") or model) if isinstance(line.get("description") or model, str) else model,
            "price": price, "list_price": unit_price if discount is not None else None, "discount_pct": discount,
            "currency": currency, "as_of": as_of, "price_kind": KIND_OF[doc_type], "source_document": number,
            "ref": f"{number}#{n}"})

    terms = None
    packing = doc.get("packing")
    amount = None if packing in (None, "") else _price(name, "packing", packing)
    if amount is not None and importing.norm_name(supplier) in PACKING_SUPPLIERS and doc_type != "purchase_order":
        if amount > 0:
            if subtotal <= 0:
                raise _fail(name, "packing", "no line total to take it over")
            pct = (amount / subtotal).quantize(_FOUR)
            if pct > Decimal("0.5"):
                raise _fail(name, "packing", "more than half of the subtotal")
            if currency not in TERMS_CURRENCIES:
                raise _fail(name, "currency", "not a currency supplier terms can hold")
            terms = {"supplier": supplier, "currency": currency, "packing_pct": pct}
    return observations, terms


def _costing_observations(name: str, data: Any, sheet_as_of: str | None) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("sheets"), list):
        sheets = data["sheets"]
    elif isinstance(data, dict):
        sheets = [data]
    elif isinstance(data, list):
        sheets = data
    else:
        raise _fail(name, "sheets", "not an object or a list")
    out: list[dict[str, Any]] = []
    for s, sheet in enumerate(sheets):
        if not isinstance(sheet, dict):
            raise _fail(name, f"sheets[{s}]", "not an object")
        # The sheet's quote number, rates and client are deliberately never read.
        # Only `fx_date` and `lines` are read; quote_number, source, expenses and totals never are.
        as_of = None
        if sheet.get("fx_date") not in (None, ""):
            try:
                as_of = _day(name, "fx_date", sheet["fx_date"])
            except _Bad:
                if not sheet_as_of:
                    raise
        if as_of is None:
            if not sheet_as_of:
                raise _fail(name, "fx_date", "missing; give --costing-as-of YYYY-MM-DD")
            as_of = sheet_as_of
        for n, line in enumerate(_lines(name, sheet)):
            at = f"lines[{n}]"
            brand = _text(name, f"{at}.brand", _need(name, line, "brand", f"{at}.brand"))
            model = _text(name, f"{at}.model", _need(name, line, "model", f"{at}.model"))
            if "supplier_unit_cost" not in line:
                raise _fail(name, f"{at}.supplier_unit_cost", "missing")
            cost = line["supplier_unit_cost"]
            price = None if cost is None or cost == "" else _price(name, f"{at}.supplier_unit_cost", cost)
            currency = None
            if price is not None:
                currency = _currency(name, f"{at}.cost_currency", _need(name, line, "cost_currency", f"{at}.cost_currency"))
            out.append({"manufacturer": brand, "supplier": brand, "model_number": model, "name": model,
                        "price": price, "list_price": None, "discount_pct": None, "currency": currency,
                        "as_of": as_of, "price_kind": "costing_sheet", "source_document": COSTING_SOURCE_DOCUMENT,
                        "ref": f"sheet {s} line {n}"})
    return out


# ------------------------------------------------------------------ the plan

def _org_key(name: str) -> str:
    return "org:" + importing.norm_name(name)


def plan_items(observations: list[dict[str, Any]], terms: list[dict[str, Any]], *, route: str,
               skipped: Counter[str], input_sha256: str) -> list[importing.PlanItem]:
    items: list[importing.PlanItem] = []
    orgs: set[str] = set()

    def org(name: str) -> str:
        key = _org_key(name)
        if key not in orgs:
            orgs.add(key)
            items.append({"action": "create_org", "key": key, "fields": {"name": name, "kind": "supplier"}})
        return key

    for o in observations:
        key = model_key(o["model_number"])
        if o["price"] is None:
            skipped["lines_without_price"] += 1
            continue
        if not key:
            skipped["lines_without_model"] += 1
            continue
        maker, supplier = org(o["manufacturer"]), org(o["supplier"])
        source = {"input": input_sha256, "ref": o["ref"]}
        product = f"product:{importing.norm_name(o['manufacturer'])}|{key}"
        items.append({"action": "create_product", "key": product, "source": source,
                      "fields": {"manufacturer": maker, "model_number": o["model_number"], "name": o["name"],
                                 "description": None}})
        items.append({"action": "add_observation",
                      "key": f"obs:{importing.norm_name(o['supplier'])}|{key}|{o['as_of']}|{o['price_kind']}",
                      "source": source,
                      "fields": {"supplier": supplier, "product": product, "as_of": o["as_of"],
                                 "price_kind": o["price_kind"], "price": o["price"], "currency": o["currency"],
                                 "list_price": o["list_price"], "discount_pct": o["discount_pct"],
                                 "is_stale": False, "source_document": o["source_document"]}})
    for t in terms:
        items.append({"action": "set_terms", "key": f"terms:{importing.norm_name(t['supplier'])}",
                      "fields": {"supplier": org(t["supplier"]), "currency": t["currency"], "route": route,
                                 "map_enforced": False, "packing_pct": t["packing_pct"]}})
    return items


def build_supplier_document_plan(inputs: list[tuple[str, Path, str | None]], *,
                                 domestic_suppliers: list[str] | tuple[str, ...] = (),
                                 route: str = "import_courier") -> importing.Plan:
    """`inputs` is (kind, path, as_of) per file: kind `document`, or `costing_sheet` with its fallback date."""
    if route not in ROUTES:
        raise ValueError(f"route must be one of {', '.join(ROUTES)}")
    domestic = {importing.norm_name(n) for n in domestic_suppliers}
    hashed = []
    for kind, path, as_of in inputs:
        if kind not in KINDS:
            raise ValueError(f"unknown input kind {kind!r}; kinds are {', '.join(KINDS)}")
        hashed.append((importing.input_record(Path(path), kind), kind, Path(path), as_of))
    hashed.sort(key=lambda h: (h[0]["file_sha256"], h[1]))
    records, items = [], []
    skipped: Counter[str] = Counter({f"skipped_{r}": 0 for r in SKIP_REASONS})
    skip_list: list[dict[str, str]] = []
    for record, kind, path, as_of in hashed:
        raw = path.read_text(encoding="utf-8")
        refuse_labdelivery(str(path.resolve()), path.name, raw)
        try:
            data = json.loads(raw)
        except ValueError:
            raise ValueError(f"{path.name}: not JSON") from None
        if as_of is not None:
            as_of = _day(path.name, "--costing-as-of", as_of)
        observations: list[dict[str, Any]] = []
        terms: list[dict[str, Any]] = []
        if kind == "costing_sheet":
            observations = _costing_observations(path.name, data, as_of)
        else:
            for doc in data if isinstance(data, list) else [data]:
                if not isinstance(doc, dict):
                    raise _fail(path.name, "document", "not an object")
                reasons: list[str] = []
                obs, term = _document_observations(path.name, doc, domestic=domestic, skipped=skipped,
                                                   skip_log=reasons)
                observations += obs
                terms += [term] if term else []
                skip_list += [{"path_sha256": record["path_sha256"], "reason": r} for r in reasons]
        records.append(record)
        items += plan_items(observations, terms, route=route, skipped=skipped, input_sha256=record["file_sha256"])
    skipped.setdefault("lines_without_price", 0)
    skipped.setdefault("lines_without_model", 0)
    return importing.build_plan(IMPORTER, records, items, extra_counts=skipped, skipped=skip_list)


# ------------------------------------------------------------------ the CLI

def _expand(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        out += sorted(p.glob("*.json")) if p.is_dir() else [p]
    return out


def _add_plan_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--documents", type=Path, action="append", default=[],
                   help="extracted supplier document JSON, or a directory of them")
    p.add_argument("--costing-sheets", type=Path, action="append", default=[], help="costing sheet JSON")
    p.add_argument("--costing-as-of", metavar="YYYY-MM-DD", help="fallback date for sheets whose fx_date is missing or invalid")
    p.add_argument("--domestic-supplier", action="append", default=[], metavar="NAME",
                   help="a supplier whose CLP purchase orders count (default: only non-CLP ones do)")
    p.add_argument("--route", choices=ROUTES, default="import_courier",
                   help="import route of the supplier terms written for packing, if the supplier has none")


def _make_plan(args: argparse.Namespace) -> importing.Plan:
    inputs: list[tuple[str, Path, str | None]] = [("document", p, None) for p in _expand(args.documents)]
    inputs += [("costing_sheet", p, args.costing_as_of) for p in _expand(args.costing_sheets)]
    if not inputs:
        raise ValueError("no supplier document or costing sheet given")
    return build_supplier_document_plan(inputs, domestic_suppliers=args.domestic_supplier, route=args.route)


def main(argv: list[str] | None = None) -> int:
    return _common.main(argv, importer=IMPORTER, description=__doc__ or "", add_plan_arguments=_add_plan_arguments,
                        make_plan=_make_plan)


if __name__ == "__main__":
    raise SystemExit(main())
