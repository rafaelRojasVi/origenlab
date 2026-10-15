"""From a quote draft to the engine's input: catalog costs, exchange rates, parameters, expense lines.

Reads only (the caller's transaction): `catalog.cost_parameter`, `catalog.product`,
`catalog.supplier_product`, `catalog.supplier_terms`, `crm.organization`. Exchange rates come from
the injected `FxResolver` (the catalog's `PricingFx.rate`) unless the draft overrides them.

What a line costs, when the operator did not type it: the product's newest observation (its own and
those of the twins merged into it), non-stale first, newest day first, per-deal kinds before list
kinds on the same day (10-05 spec §4.3). A line is imported unless its supplier's terms say
`domestic`; with no terms, a CLP cost is domestic and any other currency imported. Only imported
lines ship, and a draft with nothing to ship gets no import expenses — only the manual lines the
operator typed (10-05 spec §5.3: "domestic suppliers produce no import expense lines").
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from functools import lru_cache
from typing import Any

from origenlab_api.quoting.dhl_rates import RateCard
from origenlab_api.quoting.engine import EngineInput, EngineResult, ExpenseLine, FxRate, ItemLine
from origenlab_api.quoting.expenses import ShipmentItem, expenses_for
from origenlab_api.v2.catalog.fx import FxUnavailable
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.quotes.draft import DraftItemLine, QuoteDraft

#: (currency, day) -> {"clp_per_unit", "as_of", "source", ...}; raises FxUnavailable.
FxResolver = Callable[[str, dt.date], dict]

DEFAULT_MARKUP_KEYS = {
    "equipment": "default_markup_equipment",
    "accessory": "default_markup_accessory",
    "consumable": "default_markup_consumable",
    "spare_part": "default_markup_spare_part",
    "service": "default_markup_service",
    "other": "default_markup_accessory",
}
#: When the parameter is unset (10-05 Q2): 40% equipment, 45% everything else.
_MARKUP_FALLBACK_EQUIPMENT = Decimal("0.40")
_MARKUP_FALLBACK_OTHER = Decimal("0.45")

_DEFAULTS = {
    "iva_rate": Decimal("0.19"),
    "principal_threshold_eur": Decimal("3000"),
    "margin_target_min": Decimal("0.25"),
    "margin_target_max": Decimal("0.45"),
}

#: Same-day tie-break between observations: per-deal kinds first, list kinds last.
PRICE_KIND_PRIORITY = ("negotiated", "supplier_offer", "order_confirmation", "purchase_order", "costing_sheet",
                       "dealer_net", "list")
_FX_SOURCES = frozenset({"bcentral", "manual", "supplier_quote"})

_PARAMETERS_SQL = """
select distinct on (key) key, value_numeric, value_json
  from catalog.cost_parameter
 where valid_from <= clock_timestamp()
 order by key, valid_from desc, created_at desc
"""

_PRODUCT_SQL = """
select id::text as id, merged_into_product_id::text as merged_into_product_id, product_kind, weight_kg,
       length_cm, width_cm, height_cm, origin_country, is_dangerous_goods
  from catalog.product
 where id = %s::uuid
"""

# The product's newest observation, through product merges and organization merges (as the catalog reads).
_OBSERVATION_SQL = """
select sp.id::text as id, coalesce(so.merged_into_organization_id, so.id)::text as supplier_id,
       sp.price, sp.currency
  from catalog.supplier_product sp
  join crm.organization so on so.id = sp.supplier_organization_id
 where (sp.product_id = %(pid)s::uuid
        or sp.product_id in (select id from catalog.product where merged_into_product_id = %(pid)s::uuid))
 order by sp.is_stale, (sp.as_of at time zone 'America/Santiago')::date desc,
          array_position(%(kinds)s::text[], sp.price_kind) nulls last, sp.as_of desc, sp.created_at desc, sp.id
 limit 1
"""

_SUPPLIER_SQL = """
select o.name, t.route, t.origin_country
  from crm.organization o
  left join catalog.supplier_terms t on t.supplier_organization_id = o.id
 where o.id = %s::uuid
"""


@dataclass(frozen=True)
class ResolvedItem:
    line_no: int
    draft: DraftItemLine
    product_id: str | None          # the surviving product when the draft named a merged twin
    item_type: str
    unit_cost: Decimal | None
    cost_currency: str | None
    supplier_organization_id: str | None
    cost_observation_id: str | None
    margin_pct: Decimal | None
    imported: bool
    weight_kg: Decimal | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None
    origin_country: str | None = None
    dangerous: bool = False
    supplier_key: str = "sin proveedor"
    fob_usd: Decimal | None = None  # per unit; set once the rates are known


@dataclass(frozen=True)
class Resolved:
    items: list[ResolvedItem]
    expenses: list[ExpenseLine]
    fx: dict[str, FxRate]
    engine_input: EngineInput
    warnings: list[str] = field(default_factory=list)


def _one(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))


def load_parameters(cur: Any) -> dict[str, Decimal | str]:
    """The current value of every parameter: numbers as Decimal, printed texts as str."""
    cur.execute(_PARAMETERS_SQL)
    out: dict[str, Decimal | str] = {}
    for key, numeric, json_value in cur.fetchall():
        if numeric is not None:
            out[key] = Decimal(numeric)
        elif isinstance(json_value, str):
            out[key] = json_value
    return out


def _decimal_param(params: dict[str, Decimal | str], key: str) -> Decimal:
    value = params.get(key)
    return value if isinstance(value, Decimal) else _DEFAULTS[key]


def default_markup(item_type: str, params: dict[str, Decimal | str]) -> Decimal:
    value = params.get(DEFAULT_MARKUP_KEYS.get(item_type, "default_markup_accessory"))
    if isinstance(value, Decimal):
        return value
    return _MARKUP_FALLBACK_EQUIPMENT if item_type == "equipment" else _MARKUP_FALLBACK_OTHER


@lru_cache(maxsize=1)
def _rate_card() -> RateCard:
    return RateCard.load()


def _product(cur: Any, product_id: str) -> dict[str, Any]:
    cur.execute(_PRODUCT_SQL, (product_id,))
    product = _one(cur)
    if product is not None and product["merged_into_product_id"] is not None:
        cur.execute(_PRODUCT_SQL, (product["merged_into_product_id"],))
        product = _one(cur)
    if product is None:
        raise CommandRefused(404, "product_not_found", "no such product")
    return product


def _resolve_item(cur: Any, line_no: int, line: DraftItemLine, params: dict[str, Decimal | str]) -> ResolvedItem:
    product = _product(cur, str(line.product_id)) if line.product_id else None
    unit_cost, currency = line.unit_cost, line.cost_currency
    supplier = str(line.supplier_organization_id) if line.supplier_organization_id else None
    observation = str(line.cost_observation_id) if line.cost_observation_id else None
    if unit_cost is None and product is not None:
        cur.execute(_OBSERVATION_SQL, {"pid": product["id"], "kinds": list(PRICE_KIND_PRIORITY)})
        found = _one(cur)
        if found is not None:
            unit_cost, currency = Decimal(found["price"]), found["currency"]
            supplier, observation = found["supplier_id"], found["id"]

    terms: dict[str, Any] | None = None
    if supplier is not None:
        cur.execute(_SUPPLIER_SQL, (supplier,))
        terms = _one(cur)
    if terms is not None and terms["route"] is not None:
        imported = terms["route"] != "domestic"
    else:
        imported = currency is not None and currency != "CLP"

    item_type = line.item_type or (product or {}).get("product_kind") or "other"
    margin_pct = line.margin_pct
    if margin_pct is None and line.margin_mode == "markup":
        margin_pct = default_markup(item_type, params)
    origin = (product or {}).get("origin_country") or (terms or {}).get("origin_country")
    return ResolvedItem(
        line_no=line_no, draft=line, product_id=product["id"] if product else None, item_type=item_type,
        unit_cost=unit_cost, cost_currency=currency, supplier_organization_id=supplier,
        cost_observation_id=observation, margin_pct=margin_pct, imported=imported,
        weight_kg=(product or {}).get("weight_kg"), length_cm=(product or {}).get("length_cm"),
        width_cm=(product or {}).get("width_cm"), height_cm=(product or {}).get("height_cm"),
        origin_country=origin.strip() if origin else None,
        dangerous=bool((product or {}).get("is_dangerous_goods")),
        supplier_key=(terms or {}).get("name") or "sin proveedor",
    )


def _as_date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _resolve_fx(draft: QuoteDraft, currencies: set[str], fx_resolver: FxResolver,
                today: dt.date) -> dict[str, FxRate]:
    out = {o.currency: FxRate(o.currency, o.clp_per_unit, today, "manual") for o in draft.fx_overrides}
    for currency in sorted(currencies - set(out) - {"CLP"}):
        try:
            rate = fx_resolver(currency, today)
        except FxUnavailable as exc:
            raise CommandRefused(409, "fx_unavailable",
                                 f"No hay tipo de cambio {currency} para hoy; ingrésalo con «Cambiar».") from exc
        source = rate.get("source")
        out[currency] = FxRate(currency, Decimal(str(rate["clp_per_unit"])), _as_date(rate["as_of"]),
                               source if source in _FX_SOURCES else "bcentral")
    return out


def _clp(amount: Decimal, currency: str, fx: dict[str, FxRate]) -> Decimal:
    return amount if currency == "CLP" else amount * fx[currency].clp_per_unit


def resolve_draft(cur: Any, draft: QuoteDraft, params: dict[str, Decimal | str], fx_resolver: FxResolver,
                  today: dt.date) -> Resolved:
    items = [_resolve_item(cur, n, line, params) for n, line in enumerate(draft.items, start=1)]
    shipping = [i for i in items if i.imported]

    currencies = {i.cost_currency for i in items if i.cost_currency} | {e.currency for e in draft.manual_expenses}
    if draft.expense_method == "dhl_calc" and shipping:
        currencies.add("USD")  # the DHL card and the FOB threshold are in dollars
    fx = _resolve_fx(draft, currencies, fx_resolver, today)
    usd = fx["USD"].clp_per_unit if "USD" in fx else None

    def fob(item: ResolvedItem) -> Decimal:
        if item.unit_cost is None or item.cost_currency is None or usd is None:
            return Decimal(0)
        return item.unit_cost if item.cost_currency == "USD" else _clp(item.unit_cost, item.cost_currency, fx) / usd

    items = [replace(i, fob_usd=fob(i)) if i.imported else i for i in items]
    shipments = [ShipmentItem(i.line_no, i.draft.quantity, i.weight_kg, i.length_cm, i.width_cm, i.height_cm,
                              i.origin_country, i.fob_usd or Decimal(0), i.dangerous, i.supplier_key)
                 for i in items if i.imported]

    warnings: list[str] = []
    computed: list[ExpenseLine] = []
    if shipments:
        computed, warnings = expenses_for(draft.expense_method, shipments, params, usd or Decimal(0), _rate_card())
    manual = [ExpenseLine(0, e.label, e.amount, e.currency, "manual", None) for e in draft.manual_expenses]
    expenses = [replace(e, line_no=len(items) + n) for n, e in enumerate([*computed, *manual], start=1)]

    engine_input = EngineInput(
        fx=fx,
        items=tuple(ItemLine(i.line_no, i.draft.quantity, i.item_type, i.unit_cost, i.cost_currency, i.imported,
                             i.draft.margin_mode, i.margin_pct, i.draft.typed_unit_price, i.draft.is_principal)
                    for i in items),
        expenses=tuple(expenses),
        principal_rule=draft.principal_rule,
        allocation_mode=draft.allocation_mode,
        iva_rate=_decimal_param(params, "iva_rate"),
        principal_threshold_eur=_decimal_param(params, "principal_threshold_eur"),
        margin_target_min=_decimal_param(params, "margin_target_min"),
        margin_target_max=_decimal_param(params, "margin_target_max"),
    )
    return Resolved(items, expenses, fx, engine_input, warnings)


def _s(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def expense_target(resolved: Resolved, result: EngineResult) -> int | None:
    """The item line an expense line is allocated to: the principal, unless costs are spread."""
    return result.principal_line_no if resolved.engine_input.allocation_mode == "principal" else None


def result_json(resolved: Resolved, result: EngineResult) -> dict[str, Any]:
    """The computed draft for the response: every Decimal as a string."""
    by_no = {i.line_no: i for i in resolved.items}
    lines = []
    for r in result.lines:
        item = by_no[r.line_no]
        lines.append({
            "line_no": r.line_no, "item_type": item.item_type, "product_id": item.product_id,
            "unit_cost": _s(item.unit_cost), "cost_currency": item.cost_currency,
            "supplier_organization_id": item.supplier_organization_id,
            "cost_observation_id": item.cost_observation_id, "imported": item.imported,
            "margin_mode": item.draft.margin_mode, "margin_pct": _s(item.margin_pct),
            "cost_clp_unit": _s(r.cost_clp_unit), "expense_to_line": _s(r.expense_to_line),
            "unit_base": _s(r.unit_base), "unit_price": _s(r.unit_price), "line_total": _s(r.line_total),
            "real_markup": _s(r.real_markup), "gross_margin": _s(r.gross_margin),
            "margin_state": r.margin_state, "is_principal": r.is_principal,
        })
    target = expense_target(resolved, result)
    return {
        "lines": lines,
        "expenses": [{"line_no": e.line_no, "label": e.label, "amount": _s(e.amount), "currency": e.currency,
                      "method": e.method, "calc_inputs": e.calc_inputs, "allocated_to_line_no": target}
                     for e in resolved.expenses],
        "totals": {"net": _s(result.net), "iva": _s(result.iva), "total": _s(result.total),
                   "cost_base": _s(result.cost_base), "expenses_clp": _s(result.expenses_clp),
                   "markup": _s(result.markup), "margin": _s(result.margin), "margin_state": result.margin_state},
        "principal_line_no": result.principal_line_no,
        "fx": [{"currency": f.currency, "clp_per_unit": _s(f.clp_per_unit), "as_of": f.as_of.isoformat(),
                "source": f.source} for _, f in sorted(resolved.fx.items())],
        "warnings": [*resolved.warnings, *result.warnings],
    }


__all__ = ["DEFAULT_MARKUP_KEYS", "FxResolver", "PRICE_KIND_PRIORITY", "Resolved", "ResolvedItem",
           "default_markup", "expense_target", "load_parameters", "resolve_draft", "result_json"]
