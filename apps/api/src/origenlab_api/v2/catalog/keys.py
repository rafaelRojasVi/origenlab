"""Pure catalog helpers: model keys, closed vocabularies, and the Labdelivery refusal (spec S5)."""
from __future__ import annotations

import re

_MODEL_STRIP = re.compile(r"[\s\-_./]")
_LABDELIVERY = re.compile(r"lab[\s\-_.]*delivery|juan\s+andr[eé]s\s+tejeda", re.IGNORECASE)

PRODUCT_KINDS = ("equipment", "accessory", "consumable", "spare_part", "service")
PRICE_KINDS = ("dealer_net", "list", "map", "supplier_offer", "order_confirmation", "purchase_order",
               "costing_sheet", "negotiated")
ROUTES = ("import_courier", "import_freight", "domestic")
CONTENT_ORIGINS = ("import", "machine", "operator")
IMAGE_SOURCES = ("upload", "datasheet", "manufacturer_site")
IMAGE_STATUSES = ("proposed", "confirmed", "hidden")
PARAMETER_KEYS = (
    "dhl_import_discount_pct", "dhl_fuel_surcharge_pct", "dhl_customs_clearance_fee_usd",
    "courier_threshold_usd_fob", "customs_agent_fee_clp", "duty_rate_general", "duty_rate_fta",
    "insurance_pct_of_fob", "domestic_transport_clp",
    "profile_freight_clp", "profile_agent_clp", "profile_customs_clp", "profile_transport_clp",
    "principal_threshold_eur",
    "default_markup_equipment", "default_markup_accessory", "default_markup_consumable",
    "default_markup_spare_part", "default_markup_service",
    "margin_target_min", "margin_target_max", "price_deviation_warn_pct", "iva_rate",
)


class LabdeliveryRefused(ValueError):
    """Raised when an input is of Labdelivery origin (owner decision 2026-10-02)."""


def _upper_simple(ch: str) -> str:
    # Postgres upper() uses simple per-character mapping; Python's full mapping
    # would expand e.g. the German sharp s to "SS". Keep the character if it expands.
    up = ch.upper()
    return up if len(up) == 1 else ch


def model_key(model: str | None) -> str | None:
    """Same normalisation as catalog.product.model_key (generated column)."""
    if model is None:
        return None
    key = "".join(_upper_simple(c) for c in _MODEL_STRIP.sub("", model))
    return key or None


def is_labdelivery(text: str | None) -> bool:
    return bool(text) and bool(_LABDELIVERY.search(text))


def refuse_labdelivery(*parts: str | None) -> None:
    for part in parts:
        if is_labdelivery(part):
            raise LabdeliveryRefused("input of Labdelivery origin is refused")
