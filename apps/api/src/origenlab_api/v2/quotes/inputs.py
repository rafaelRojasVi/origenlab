"""From a quote draft to the engine's input: catalog costs, exchange rates, parameters, expense lines.

Reads only (the caller's transaction): `catalog.cost_parameter`, `catalog.product`,
`catalog.supplier_product`, `catalog.supplier_terms`, `crm.organization`. Exchange rates come from
the injected `FxResolver` (the catalog's `PricingFx.rate`) unless the draft overrides them.

What a line costs, when the operator did not type it: the product's newest observation (its own and
those of the twins merged into it), non-stale first, newest day first, per-deal kinds before list
kinds on the same day (10-05 spec §4.3) — from the line's supplier when it names one, or exactly
the observation it names. The whole draft resolves in three statements whatever its length
(products, observations, suppliers), so a preview stays fast over a remote database. A line is imported unless its supplier's terms say
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
from origenlab_api.v2.quotes.draft import (
    DraftExpenseLine,
    DraftFxOverride,
    DraftItemLine,
    DraftTerm,
    DraftTerms,
    QuoteDraft,
)

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

# Every product the draft names, each with the product it survives as (one level of merge).
_PRODUCTS_SQL = """
select p.id::text as requested, t.id::text as id, t.product_kind, t.weight_kg, t.length_cm, t.width_cm,
       t.height_cm, t.origin_country, t.is_dangerous_goods
  from catalog.product p
  join catalog.product t on t.id = coalesce(p.merged_into_product_id, p.id)
 where p.id = any(%s::uuid[])
"""

# One observation per line that needs a cost, in one statement: the observation the line names; else
# the newest of the product (its own and its merged twins'), from the line's supplier when it names
# one (through organization merges, as the catalog reads). Non-stale first, newest Santiago day first,
# per-deal kinds before list kinds on the same day.
_OBSERVATIONS_SQL = """
select r.line_no, o.id, o.supplier_id, o.price, o.currency
  from unnest(%(lines)s::int[], %(products)s::uuid[], %(suppliers)s::uuid[], %(observations)s::uuid[])
       as r(line_no, product_id, supplier_id, observation_id)
  cross join lateral (
    select sp.id::text as id, coalesce(so.merged_into_organization_id, so.id)::text as supplier_id,
           sp.price, sp.currency
      from catalog.supplier_product sp
      join crm.organization so on so.id = sp.supplier_organization_id
     where case when r.observation_id is not null then sp.id = r.observation_id
                else (sp.product_id = r.product_id
                      or sp.product_id in (select id from catalog.product where merged_into_product_id = r.product_id))
                     and (r.supplier_id is null or r.supplier_id in (so.id, so.merged_into_organization_id))
           end
     order by sp.is_stale, (sp.as_of at time zone 'America/Santiago')::date desc,
              array_position(%(kinds)s::text[], sp.price_kind) nulls last, sp.as_of desc, sp.created_at desc, sp.id
     limit 1
  ) o
"""

_SUPPLIERS_SQL = """
select o.id::text as id, o.name, t.route, t.origin_country
  from crm.organization o
  left join catalog.supplier_terms t on t.supplier_organization_id = o.id
 where o.id = any(%s::uuid[])
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


def _rows(cur: Any) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def _products(cur: Any, lines: list[DraftItemLine]) -> dict[str, dict[str, Any]]:
    wanted = sorted({str(line.product_id) for line in lines if line.product_id})
    if not wanted:
        return {}
    cur.execute(_PRODUCTS_SQL, (wanted,))
    found = {row["requested"]: row for row in _rows(cur)}
    if set(wanted) - set(found):
        raise CommandRefused(404, "product_not_found", "no such product")
    return found


def _observations(cur: Any, lines: list[DraftItemLine],
                  products: dict[str, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """The cost observation for every line that has no typed cost, by line number."""
    asks = [(n, products[str(line.product_id)]["id"] if line.product_id else None,
             str(line.supplier_organization_id) if line.supplier_organization_id else None,
             str(line.cost_observation_id) if line.cost_observation_id else None)
            for n, line in enumerate(lines, start=1)
            if line.unit_cost is None and (line.product_id or line.cost_observation_id)]
    if not asks:
        return {}
    cur.execute(_OBSERVATIONS_SQL, {"lines": [a[0] for a in asks], "products": [a[1] for a in asks],
                                    "suppliers": [a[2] for a in asks], "observations": [a[3] for a in asks],
                                    "kinds": list(PRICE_KIND_PRIORITY)})
    found = {row["line_no"]: row for row in _rows(cur)}
    for n, _, _, observation in asks:
        if observation is not None and n not in found:
            raise CommandRefused(422, "cost_observation_not_found",
                                 f"line {n} names a cost observation that does not exist")
    return found


def _suppliers(cur: Any, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    cur.execute(_SUPPLIERS_SQL, (sorted(ids),))
    return {row["id"]: row for row in _rows(cur)}


def _resolve_items(cur: Any, lines: list[DraftItemLine], params: dict[str, Decimal | str]) -> list[ResolvedItem]:
    """Every line's cost, supplier and product facts in three statements, whatever the line count."""
    products = _products(cur, lines)
    observations = _observations(cur, lines, products)
    costs: list[tuple[Decimal | None, str | None, str | None, str | None]] = []
    for n, line in enumerate(lines, start=1):
        supplier = str(line.supplier_organization_id) if line.supplier_organization_id else None
        observation = str(line.cost_observation_id) if line.cost_observation_id else None
        found = observations.get(n)
        if found is not None:
            costs.append((Decimal(found["price"]), found["currency"], found["supplier_id"], found["id"]))
        else:
            costs.append((line.unit_cost, line.cost_currency, supplier, observation))
    suppliers = _suppliers(cur, {c[2] for c in costs if c[2]})

    items = []
    for n, (line, (unit_cost, currency, supplier, observation)) in enumerate(zip(lines, costs, strict=True), start=1):
        product = products[str(line.product_id)] if line.product_id else {}
        terms = suppliers.get(supplier) if supplier else None
        if terms is not None and terms["route"] is not None:
            imported = terms["route"] != "domestic"
        else:
            imported = currency is not None and currency != "CLP"
        item_type = line.item_type or product.get("product_kind") or "other"
        margin_pct = line.margin_pct
        if margin_pct is None and line.margin_mode == "markup":
            margin_pct = default_markup(item_type, params)
        origin = product.get("origin_country") or (terms or {}).get("origin_country")
        items.append(ResolvedItem(
            line_no=n, draft=line, product_id=product.get("id"), item_type=item_type,
            unit_cost=unit_cost, cost_currency=currency, supplier_organization_id=supplier,
            cost_observation_id=observation, margin_pct=margin_pct, imported=imported,
            weight_kg=product.get("weight_kg"), length_cm=product.get("length_cm"),
            width_cm=product.get("width_cm"), height_cm=product.get("height_cm"),
            origin_country=origin.strip() if origin else None,
            dangerous=bool(product.get("is_dangerous_goods")),
            supplier_key=(terms or {}).get("name") or "sin proveedor",
        ))
    return items


def fx_override_reasons(draft: QuoteDraft) -> dict[str, dict[str, str]]:
    """What `crm.quote_revision.fx_override_reasons` stores: per currency, the operator's rate and why.

    The rate is kept here, not only in line snapshots: an override for a currency no line costs in
    (a USD rate that only drives the DHL calculation) would otherwise be lost on reload.
    """
    return {o.currency: {"reason": o.reason, "clp_per_unit": str(o.clp_per_unit)} for o in draft.fx_overrides}


def fx_overrides_from_row(value: dict[str, Any] | None) -> list[DraftFxOverride]:
    """The draft's overrides back from the stored column (the inverse of `fx_override_reasons`)."""
    out = []
    for currency, entry in sorted((value or {}).items()):
        if isinstance(entry, dict) and entry.get("clp_per_unit") is not None:
            out.append(DraftFxOverride(currency=currency, clp_per_unit=Decimal(str(entry["clp_per_unit"])),
                                       reason=entry["reason"]))
    return out


def draft_from_rows(revision: dict[str, Any], lines: list[dict[str, Any]]) -> QuoteDraft:
    """The draft a stored revision was saved from (the inverse of `save-quote-draft`).

    Item lines keep exactly the cost they were priced with — unit cost, currency, supplier and
    observation as stored — so re-resolving the draft reproduces the save: a stored cost is a
    typed cost, never looked up again. A `none` line's price is its stored unit price; any other
    line carries its stored `margin_pct` (the default markup was made explicit at save). Only the
    manual expense lines come back as expenses: the profile and DHL lines are derived, and
    `resolve_draft` regenerates them from `expense_method` exactly as the save did. The principal
    flag returns only under the manual rule, the one rule where the operator chose it.
    """
    manual = revision["principal_rule"] == "manual"
    items, expenses = [], []
    for row in sorted(lines, key=lambda r: r["line_no"]):
        if row["line_kind"] == "item":
            none = row["margin_mode"] == "none"
            items.append(DraftItemLine(
                product_id=row["product_id"], description=row["description"], quantity=row["quantity"],
                item_type=row["item_type"], unit_cost=row["unit_cost"], cost_currency=row["cost_currency"],
                supplier_organization_id=row["supplier_organization_id"],
                cost_observation_id=row["cost_observation_id"], margin_mode=row["margin_mode"],
                margin_pct=None if none else row["margin_pct"],
                typed_unit_price=row["unit_price_qc"] if none else None,
                is_principal=bool(row["is_principal"]) and manual, line_note_es=row["line_note_es"]))
        elif row["expense_method"] == "manual":
            expenses.append(DraftExpenseLine(label=row["description"], amount=row["unit_cost"],
                                             currency=row["cost_currency"]))
    terms = DraftTerms(
        delivery_terms=revision["delivery_terms"], validity_days=revision["validity_days"],
        payment_terms=revision["payment_terms"], warranty_terms_es=revision["warranty_terms_es"],
        delivery_place_es=revision["delivery_place_es"], installation_terms_es=revision["installation_terms_es"],
        client_notes_es=revision["client_notes_es"],
        terms_extra=[DraftTerm(**t) for t in revision["terms_extra"] or []])
    return QuoteDraft(items=items, manual_expenses=expenses, expense_method=revision["expense_method"],
                      allocation_mode=revision["expense_allocation_mode"], principal_rule=revision["principal_rule"],
                      fx_overrides=fx_overrides_from_row(revision["fx_override_reasons"]), terms=terms,
                      recipient_participant_id=revision["recipient_participant_id"])


def engine_input_from_rows(revision: dict[str, Any], lines: list[dict[str, Any]],
                           params: dict[str, Decimal | str]) -> EngineInput:
    """The engine's input exactly as the revision was last saved — what approval prices.

    Nothing is looked up again: no catalog cost (a line saved without one stays without one), no
    exchange rate (each currency's rate is the one its lines carry — the day's rate or the
    operator's override as it was saved), no regenerated expense (the stored expense lines are the
    expenses), no principal rule (the stored principal is the principal; `imported` only feeds the
    rules, so it is not needed). The frozen totals therefore equal the last save's.
    """
    fx: dict[str, FxRate] = {}
    for row in lines:
        currency = row["cost_currency"]
        if currency and currency != "CLP" and row["fx_rate"] is not None:
            fx.setdefault(currency, FxRate(currency, Decimal(row["fx_rate"]), _as_date(row["fx_as_of"]),
                                           row["fx_source"]))
    ordered = sorted(lines, key=lambda r: r["line_no"])
    items = tuple(
        ItemLine(r["line_no"], Decimal(r["quantity"]), r["item_type"] or "other", r["unit_cost"], r["cost_currency"],
                 False, r["margin_mode"], None if r["margin_mode"] == "none" else r["margin_pct"],
                 r["unit_price_qc"] if r["margin_mode"] == "none" else None, bool(r["is_principal"]))
        for r in ordered if r["line_kind"] == "item")
    expenses = tuple(
        ExpenseLine(r["line_no"], r["description"], r["unit_cost"] or Decimal(0), r["cost_currency"] or "CLP",
                    r["expense_method"] or "manual", r["calc_inputs"])
        for r in ordered if r["line_kind"] == "logistics")
    return EngineInput(
        fx=fx, items=items, expenses=expenses, principal_rule="manual",
        allocation_mode=revision["expense_allocation_mode"], iva_rate=Decimal(revision["tax_rate"]),
        principal_threshold_eur=_decimal_param(params, "principal_threshold_eur"),
        margin_target_min=_decimal_param(params, "margin_target_min"),
        margin_target_max=_decimal_param(params, "margin_target_max"),
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
    items = _resolve_items(cur, list(draft.items), params)
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
           "default_markup", "draft_from_rows", "engine_input_from_rows", "expense_target", "fx_override_reasons", "fx_overrides_from_row",
           "load_parameters", "resolve_draft", "result_json"]
