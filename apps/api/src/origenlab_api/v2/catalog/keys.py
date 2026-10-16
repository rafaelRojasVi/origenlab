"""Pure catalog helpers: model keys, closed vocabularies, and the Labdelivery refusal (spec S5) — of
any string (`is_labdelivery`) and of a document Labdelivery issued (`labdelivery_issued`)."""
from __future__ import annotations

import re
import unicodedata

_MODEL_STRIP = re.compile(r"[\s\-_./]")
_NON_ALNUM = re.compile(r"[\W_]+")
_LABDELIVERY = re.compile(
    r"(?<![a-z0-9])(?:lab ?delivery|juan andres tejeda|tejeda arellano)(?![a-z0-9])"
)

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
    "quote_number_seed", "quote_default_delivery_terms", "quote_default_validity_days",
    "quote_default_payment_terms", "quote_default_warranty", "quote_default_delivery_place",
    "quote_default_installation",
)
#: The only `quote_default_*` key that is a number; the other quote defaults are printed text.
QUOTE_DEFAULT_NUMERIC_KEY = "quote_default_validity_days"
#: The parameters that are whole numbers, with their inclusive range: the series' next number has
#: five digits (`numbering.py`), and a validity is printed in days, at most a year.
INTEGER_PARAMETER_RANGES = {
    "quote_number_seed": (1, 99999),
    QUOTE_DEFAULT_NUMERIC_KEY: (1, 365),
}


def parameter_is_text(key: str) -> bool:
    return key.startswith("quote_default_") and key != QUOTE_DEFAULT_NUMERIC_KEY


class LabdeliveryRefused(ValueError):
    """Raised when an input is of Labdelivery origin (owner decision 2026-10-02)."""


def _upper_simple(ch: str) -> str:
    # Postgres upper() uses simple per-character mapping; Python's full mapping
    # would expand e.g. the German sharp s to "SS". Keep the character if it expands.
    up = ch.upper()
    return up if len(up) == 1 else ch


def model_key(model: str | None) -> str | None:
    """Same normalisation as catalog.product.model_key (generated column).

    Python `\\s` is Unicode-aware while Postgres `\\s` follows the server locale;
    the database index (Task 6) is the authority for duplicates.
    """
    if model is None:
        return None
    key = "".join(_upper_simple(c) for c in _MODEL_STRIP.sub("", model))
    return key or None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", stripped.casefold()).strip()


def is_labdelivery(text: str | None) -> bool:
    return bool(text) and bool(_LABDELIVERY.search(_fold(text)))


def refuse_labdelivery(*parts: str | None) -> None:
    for part in parts:
        if is_labdelivery(part):
            raise LabdeliveryRefused("input of Labdelivery origin is refused")


_H = r"[^\S\n]"  # horizontal whitespace: a footer pattern never spans more than the one line break it names
#: The old OrigenLab template's page footer, as `pdftotext -layout` prints it: an address line that
#: ends `…, Santiago, Chile. Teléfono (56-2) <number>`, then (on the next line, or the same one) the
#: line `Celular: (09) <number> www.labdelivery.cl` and nothing else. Only the web address of that
#: second line is exempt; the address, the numbers and every other line are still checked.
_OLD_TEMPLATE_FOOTER = re.compile(
    rf"^{_H}*[^\n]*,{_H}*Santiago,{_H}*Chile\.{_H}*Tel[eé]fono{_H}*\(56-2\){_H}*\d[\d ]{{5,10}}\d{_H}*"
    rf"(?:\n{_H}*|{_H}+)Celular:{_H}*\(09\){_H}*\d[\d ]{{5,9}}\d{_H}+(?P<url>www\.labdelivery\.cl){_H}*$",
    re.MULTILINE,
)


def labdelivery_issued(text: str | None) -> bool:
    """True when Labdelivery appears in a document anywhere other than the old OrigenLab template's
    footer line (`_OLD_TEMPLATE_FOOTER`): its letterhead, its issuer, a title, a line, a signature.

    Owner decision 2026-10-16: OrigenLab's old quote template printed that footer under an
    OrigenLab header, so a footer-only mention is not a Labdelivery document; a Labdelivery
    letterhead or issuer always is, footer or not. Nothing of the footer is ever stored — the
    callers write only what they read elsewhere, each string still refused by `is_labdelivery`.
    """
    if not text:
        return False
    normal = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    cut = [(m.start("url"), m.end("url")) for m in _OLD_TEMPLATE_FOOTER.finditer(normal)]
    for start, end in reversed(cut):
        normal = normal[:start] + normal[end:]
    return is_labdelivery(normal)
