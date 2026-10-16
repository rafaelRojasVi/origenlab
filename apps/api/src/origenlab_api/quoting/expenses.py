"""Import-expense lines for a quote: the Plantilla's flat profile and the DHL calculation.

Pure functions, no I/O. Both return ``(lines, warnings)``; ``expenses_for`` picks
the method and falls back to the profile when the DHL calculation cannot run.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .dhl_rates import RateCard, chargeable_weight_kg
from .engine import EngineError, ExpenseLine, peso

# Origins for which the FTA duty rate applies (EU member states + US).
FTA_ORIGINS = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT",
    "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "US",
})

# Chilean import IVA, reported for information only (recoverable crédito fiscal, never a cost).
IMPORT_IVA_RATE = Decimal("0.19")

# The card has three dangerous-goods tiers; an item only says "dangerous", so the
# most conservative (fully regulated) tier is charged.
DANGEROUS_GOODS_SURCHARGE = "dg_fully_regulated"

# Per-piece surcharge thresholds. The rate-card file prices the surcharges but does not state
# the thresholds; these are DHL Express's published rule (piece over 70 kg actual weight, or
# any side over 120 cm).
OVERWEIGHT_PIECE_KG = Decimal(70)
OVERSIZE_PIECE_CM = Decimal(120)

_PROFILE_LINES = (
    ("Flete", "profile_freight_clp"),
    ("Agente de aduana", "profile_agent_clp"),
    ("Derechos y aduana", "profile_customs_clp"),
    ("Transporte local", "profile_transport_clp"),
)

_DHL_PARAMS = (
    "dhl_import_discount_pct", "dhl_fuel_surcharge_pct", "dhl_customs_clearance_fee_usd",
    "courier_threshold_usd_fob", "customs_agent_fee_clp", "duty_rate_general", "duty_rate_fta",
    "insurance_pct_of_fob", "domestic_transport_clp",
)


@dataclass(frozen=True)
class ShipmentItem:
    line_no: int
    quantity: Decimal
    weight_kg: Decimal | None
    length_cm: Decimal | None
    width_cm: Decimal | None
    height_cm: Decimal | None
    origin_country: str | None
    fob_usd: Decimal            # per unit
    dangerous: bool
    supplier_key: str


def _s(value: Decimal) -> str:
    """JSON-safe, trailing-zero-free decimal string."""
    return format(value.normalize(), "f")


def profile_expenses(params: Mapping[str, Decimal]) -> tuple[list[ExpenseLine], list[str]]:
    lines: list[ExpenseLine] = []
    warnings: list[str] = []
    for label, key in _PROFILE_LINES:
        if key not in params:
            warnings.append(f"profile_parameter_missing:{key}")
            continue
        lines.append(ExpenseLine(len(lines) + 1, label, params[key], "CLP", "profile", {"parameter": key}))
    return lines, warnings


def dhl_expenses(
    items: list[ShipmentItem], params: Mapping[str, Decimal], usd_rate: Decimal, card: RateCard
) -> tuple[list[ExpenseLine], list[str]]:
    if any(i.weight_kg is None for i in items):
        raise EngineError("dhl_weight_missing")
    for key in _DHL_PARAMS:
        if key not in params:
            raise EngineError(f"dhl_parameter_missing:{key}")

    groups: dict[str, list[ShipmentItem]] = {}
    for item in items:
        groups.setdefault(item.supplier_key, []).append(item)

    lines: list[ExpenseLine] = []

    def add(label: str, amount: Decimal, inputs: dict) -> None:
        lines.append(ExpenseLine(len(lines) + 1, label, amount, "CLP", "dhl_calc", inputs))

    for supplier, group in groups.items():
        origins = {i.origin_country.upper() for i in group if i.origin_country}
        if len(origins) != 1 or any(not i.origin_country for i in group):
            raise EngineError("dhl_origin_missing" if not origins else "dhl_origin_mixed")
        origin = next(iter(origins))
        try:
            zone = card.zone_for(origin)
        except KeyError:
            raise EngineError("dhl_zone_unknown") from None

        kg = sum(
            (i.quantity * chargeable_weight_kg(i.weight_kg, i.length_cm, i.width_cm, i.height_cm) for i in group),
            Decimal(0),
        )
        base_usd = card.published_import_usd(zone, kg)
        freight_usd = base_usd * (1 - params["dhl_import_discount_pct"]) * (1 + params["dhl_fuel_surcharge_pct"])
        dangerous = any(i.dangerous for i in group)
        if dangerous:
            freight_usd += card.surcharge_usd(DANGEROUS_GOODS_SURCHARGE)
        overweight = sum((i.quantity for i in group if i.weight_kg > OVERWEIGHT_PIECE_KG), Decimal(0))
        oversize = sum(
            (i.quantity for i in group
             if any(d is not None and d > OVERSIZE_PIECE_CM for d in (i.length_cm, i.width_cm, i.height_cm))),
            Decimal(0),
        )
        freight_usd += overweight * card.surcharge_usd("overweight_piece")
        freight_usd += oversize * card.surcharge_usd("oversize_piece")
        freight_clp = peso(freight_usd * usd_rate)

        fob_usd = sum((i.fob_usd * i.quantity for i in group), Decimal(0))
        cif_usd = fob_usd + freight_usd + fob_usd * params["insurance_pct_of_fob"]
        duty_rate = params["duty_rate_fta"] if origin in FTA_ORIGINS else params["duty_rate_general"]
        duty_clp = peso(duty_rate * cif_usd * usd_rate)
        iva_clp = peso(IMPORT_IVA_RATE * (cif_usd * usd_rate + duty_clp))

        add(f"Flete DHL ({supplier})", freight_clp, {
            "supplier_key": supplier, "origin_country": origin, "zone": zone, "chargeable_kg": _s(kg),
            "published_usd": _s(base_usd), "discount_pct": _s(params["dhl_import_discount_pct"]),
            "fuel_pct": _s(params["dhl_fuel_surcharge_pct"]), "dangerous": dangerous,
            "overweight_pieces": _s(overweight), "oversize_pieces": _s(oversize),
            "freight_usd": _s(freight_usd), "fob_usd": _s(fob_usd), "cif_usd": _s(cif_usd),
            "usd_rate": _s(usd_rate), "rate_card": card.version,
            "cash_advance_iva_clp": _s(iva_clp),     # information only, never an amount
        })
        if fob_usd <= params["courier_threshold_usd_fob"]:
            fee = params["dhl_customs_clearance_fee_usd"]
            add("Despacho DHL", peso(fee * usd_rate), {
                "supplier_key": supplier, "fob_usd": _s(fob_usd), "clearance_fee_usd": _s(fee),
                "threshold_usd": _s(params["courier_threshold_usd_fob"]), "usd_rate": _s(usd_rate)})
        else:
            add("Agente de aduana", params["customs_agent_fee_clp"], {
                "supplier_key": supplier, "fob_usd": _s(fob_usd),
                "threshold_usd": _s(params["courier_threshold_usd_fob"])})
        if duty_clp > 0:
            add("Derechos", duty_clp, {
                "supplier_key": supplier, "origin_country": origin, "duty_rate": _s(duty_rate),
                "cif_usd": _s(cif_usd), "insurance_pct": _s(params["insurance_pct_of_fob"]),
                "usd_rate": _s(usd_rate)})

    if groups:
        add("Transporte local", params["domestic_transport_clp"], {"parameter": "domestic_transport_clp"})
    return lines, []


def expenses_for(
    method: str, items: list[ShipmentItem], params: Mapping[str, Decimal], usd_rate: Decimal, card: RateCard
) -> tuple[list[ExpenseLine], list[str]]:
    if method == "profile":
        return profile_expenses(params)
    if method == "dhl_calc":
        try:
            return dhl_expenses(items, params, usd_rate, card)
        except EngineError as exc:
            lines, warnings = profile_expenses(params)
            return lines, [f"dhl_fallback:{exc.code}", *warnings]
    if method == "manual":
        return [], []
    raise EngineError(f"expense_method_unknown:{method}")
