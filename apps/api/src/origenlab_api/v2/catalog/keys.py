"""Pure catalog helpers: model keys, closed vocabularies, and the Labdelivery refusal (spec S5) — of
any string (`is_labdelivery`) and of a document Labdelivery issued (`labdelivery_issued`)."""
from __future__ import annotations

import hashlib
import re
import unicodedata

_MODEL_STRIP = re.compile(r"[\s\-_./]")
_NON_ALNUM = re.compile(r"[\W_]+")


def _spaced(term: str) -> str:
    """A folded term whose letters may be separated by one space: «l a b d e l i v e r y» too."""
    return " ?".join(term.replace(" ", ""))


_LABDELIVERY = re.compile(
    rf"(?<![a-z0-9])(?:{_spaced('labdelivery')}|{_spaced('juan andres tejeda')}|{_spaced('tejeda arellano')})"
    r"(?![a-z0-9])"
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


_H = r"[^\S\n]"  # horizontal whitespace
#: The old OrigenLab template's page footer, as `pdftotext -layout` prints it: an address line that
#: ends `…, Santiago, Chile. Teléfono (56-2) <number>`, then — on the next line, or the same one —
#: `Celular: (09) <number> www.labdelivery.cl` and nothing else. The shape only finds a candidate;
#: it is exempt only when the SHA-256 of its folded text is pinned in `OLD_TEMPLATE_FOOTER_SHA256`.
_ADDRESS = (rf"(?P<address>(?P<street>\S[^\n]*?),{_H}*Santiago,{_H}*Chile\.{_H}*Tel[eé]fono{_H}*\(56-2\)"
            rf"{_H}*\d[\d ]{{5,10}}\d)")
_CELULAR = rf"(?P<celular>(?P<phone>Celular:{_H}*\(09\){_H}*\d[\d ]{{5,9}}\d){_H}+www\.labdelivery\.cl)"
_FOOTER_ADDRESS_LINE = re.compile(rf"{_H}*{_ADDRESS}{_H}*")
_FOOTER_CELULAR_LINE = re.compile(rf"{_H}*{_CELULAR}{_H}*")
_FOOTER_ONE_LINE = re.compile(rf"{_H}*{_ADDRESS}{_H}+{_CELULAR}{_H}*")
#: SHA-256 of `_fold(address line) + "\n" + _fold(celular line)` of the one real old-template footer
#: (read from the private history texts, 2026-10-16). Only its hash is here: the repository is
#: public. Tests pin their invented footer by adding its hash.
OLD_TEMPLATE_FOOTER_SHA256: frozenset[str] = frozenset({
    "4b3e94ccfe549a2d2186ab36d524543f9e64ab3a0d8a570e8f23db6c78196122",
})
_ORIGENLAB = re.compile(r"(?<![a-z0-9])origen ?lab(?![a-z0-9])")
_DIGIT_GROUPS = re.compile(r"\d+(?: \d+)*")
_BETWEEN_DIGITS = re.compile(r"(?<=\d)[\s.\-()/+]+(?=\d)")
#: A footer number shorter than this is not an identity (`(09)`, `56-2`, a street number).
_MIN_FOOTER_DIGITS = 6


def footer_sha256(address: str, celular: str) -> str:
    """The pin of one footer: its address line and its celular line (with the web address), folded."""
    return hashlib.sha256(f"{_fold(address)}\n{_fold(celular)}".encode()).hexdigest()


def _lines(text: str) -> list[str]:
    normal = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    return normal.split("\n")


def _old_footers(lines: list[str]) -> list[tuple[tuple[int, ...], re.Match[str], re.Match[str]]]:
    """Every pinned footer: (its line numbers, the address match, the celular match)."""
    found = []
    for n, line in enumerate(lines):
        both = _FOOTER_ONE_LINE.fullmatch(line)
        if both is not None:
            if footer_sha256(both["address"], both["celular"]) in OLD_TEMPLATE_FOOTER_SHA256:
                found.append(((n,), both, both))
            continue
        address = _FOOTER_ADDRESS_LINE.fullmatch(line)
        celular = _FOOTER_CELULAR_LINE.fullmatch(lines[n + 1]) if address and n + 1 < len(lines) else None
        if address and celular and footer_sha256(address["address"], celular["celular"]) in OLD_TEMPLATE_FOOTER_SHA256:
            found.append(((n, n + 1), address, celular))
    return found


def labdelivery_issued(text: str | None) -> bool:
    """True when the document is Labdelivery's: its name anywhere but the old OrigenLab template's
    footer (`OLD_TEMPLATE_FOOTER_SHA256`) — a letterhead, an issuer, a title, a line, a signature
    — or that footer on a page that does not name OrigenLab anywhere else.

    Owner decision 2026-10-16: OrigenLab's old quote template printed that footer under an
    OrigenLab header, so a footer-only mention under OrigenLab's name is not a Labdelivery
    document; a Labdelivery letterhead or issuer always is, footer or not. Nothing of the footer
    may be stored: callers refuse a document whose written strings copy it (`copies_old_footer`).
    """
    if not text:
        return False
    lines = _lines(text)
    footers = _old_footers(lines)
    if not footers:
        return is_labdelivery(text)
    skip = {n for numbers, _, _ in footers for n in numbers}
    rest = "\n".join(line for n, line in enumerate(lines) if n not in skip)
    if not _ORIGENLAB.search(_fold(rest)):
        return True  # the footer names Labdelivery, and nothing else names OrigenLab
    return is_labdelivery(rest)


def old_footer_identities(text: str | None) -> frozenset[str]:
    """What a pinned old-template footer in `text` identifies, normalised, for `copies_old_footer`:
    its folded address line, street and phone label, and every number of 6 or more digits on it."""
    if not text:
        return frozenset()
    found: set[str] = set()
    for _, address, celular in _old_footers(_lines(text)):
        found |= {_fold(address["address"]), _fold(address["street"]), _fold(celular["phone"])}
        for part in (address["address"], celular["celular"]):
            for group in _DIGIT_GROUPS.findall(part):
                digits = group.replace(" ", "")
                if len(digits) >= _MIN_FOOTER_DIGITS:
                    found.add(digits)
    return frozenset(i for i in found if i)


def copies_old_footer(value: str | None, identities: frozenset[str]) -> bool:
    """True when a string to be written repeats the footer: its address, street or phone label
    (folded, as whole words), or one of its numbers (separators between digits ignored)."""
    if not value or not identities:
        return False
    folded = f" {_fold(value)} "
    digits = _BETWEEN_DIGITS.sub("", value)
    return any(i in digits if i.isdigit() else f" {i} " in folded for i in identities)
