"""Spanish catalog content proposed by Claude: the request it is sent, and what is kept of its answer.

Pure — no database, no network, no SDK import. `scripts/catalog/enrich_products.py` reads the
products, calls the model and writes what `validate_response` keeps.

**What Claude is sent.** Only the product's own data — brand, model number, English name, kind
and category — and the text of its datasheet or manufacturer page, with every line that looks
commercial (a currency, a price, a cost, a discount, a quote) removed first
(`scrub_commercial_lines`). `build_request` reads those five keys from the product by name and
nothing else, so a price, a supplier's terms, a client, a quote line or an operator's note that a
caller left in the dict cannot reach the request.

**What is kept of the answer: only what the source says.**

* Every number in a Spanish text or a spec must appear in the source text sent (the product
  data plus the scrubbed datasheet and page). A decimal comma for a decimal point is the same
  number. A rejected spec is dropped alone.
* A weight is kept only if the source writes that number directly followed by kilograms
  (`2,5 kg`); «2.5 L» or «2.5 lb» does not state a weight in kg. A dimension is kept only if
  the source writes it with `cm` (or as `A x B x C cm`), or ten times it with `mm` — the one
  conversion allowed, because the columns are in cm. One rejected dimension drops all three.
* No origin country is asked for or kept (review ruling I2): the source cannot prove which
  country it names, and the column's check would accept any two letters.
* An image URL is kept only if it is https on the manufacturer's domain (or a subdomain), was in
  the source text verbatim, and names no credentials, port or IP literal.
* Every string of the answer passes the Labdelivery refusal (spec S5); one hit refuses the item.

Every rejection is recorded (`EnrichmentResult.rejected`) with the field and a reason, never with
the rejected value.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from origenlab_api.v2.catalog.keys import PRODUCT_KINDS, refuse_labdelivery

#: Claude Haiku 4.5 (dated id) — the cost-sensitive default for ~7,000 products (owner ruling);
#: `--model` overrides it. Haiku 4.5 supports structured outputs, but not adaptive thinking,
#: `effort` or the server-side refusal fallback, so those are sent only to models that take them.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL = DEFAULT_MODEL
MAX_TOKENS = 16000
EFFORT = "medium"
#: Server-side refusal fallback ("default" routes by refusal category); rejected by the Batches API.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: Models that take `thinking: {type: "adaptive"}` with `output_config.effort` (claude-api skill).
_ADAPTIVE_MODELS = ("claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
                    "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6")
#: Models the skill documents `fallbacks: "default"` for on the Claude API.
_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_MODEL_ID = re.compile(r"^claude-[a-z0-9-]{1,80}$")


def check_model_id(model: str) -> str:
    """A plausible Claude model id (`claude-…`), or ValueError."""
    if not _MODEL_ID.match(model or ""):
        raise ValueError("--model must be a Claude model id such as claude-haiku-4-5-20251001")
    return model


def supports_adaptive_thinking(model: str) -> bool:
    return model in _ADAPTIVE_MODELS


def supports_fallbacks(model: str) -> bool:
    return model in _FALLBACK_MODELS

MAX_NAME = 160
MAX_DESCRIPTION = 1200
MAX_CATEGORY = 120
MAX_SPECS = 30
MAX_SPEC_LABEL = 120
MAX_SPEC_VALUE = 400
MAX_SPEC_UNIT = 20
MAX_IMAGES = 6
#: A source longer than this is refused for the item, never silently truncated.
MAX_SOURCE_CHARS = 200_000

#: The product keys sent to Claude, and the only ones read from the product dict.
PRODUCT_KEYS = ("brand", "model_number", "name", "product_kind", "category_es")

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NUM = r"(?<![\d.,])(\d+(?:[.,]\d+)*)"
_KG = re.compile(_NUM + r"\s*(?:kgs?|kilo(?:gram(?:me)?s?|gramos?)?)(?!\w)", re.IGNORECASE)
_SINGLE_LENGTH = re.compile(_NUM + r"\s*(cm|mm)(?!\w)", re.IGNORECASE)
_TRIPLE_LENGTH = re.compile(_NUM + r"\s*(?:cm|mm)?\s*[x×*]\s*" + _NUM + r"\s*(?:cm|mm)?\s*[x×*]\s*" + _NUM
                            + r"\s*(cm|mm)(?!\w)", re.IGNORECASE)
_COMMERCIAL = re.compile(
    r"[$€£¥]|\b(?:usd|eur|clp|gbp|chf|prices?|pricing|precios?|costs?|costos?|costes?|discounts?|descuentos?|"
    r"dcto|tarifas?|quotes?|quotation|cotizaci[oó]n(?:es)?|presupuestos?|neto|net price|iva|vat)\b",
    re.IGNORECASE,
)


class EnrichmentRefused(ValueError):
    """The model's answer cannot be used for this item (a refusal, a truncation, not JSON)."""


@dataclass
class EnrichmentResult:
    name_es: str | None = None
    description_es: str | None = None
    category_es: str | None = None
    product_kind: str | None = None
    specs: list[dict[str, Any]] = field(default_factory=list)
    weight_kg: Decimal | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None
    #: (url, source) with source `manufacturer_site` or `datasheet`.
    images: list[tuple[str, str]] = field(default_factory=list)
    #: {field, reason} — never the rejected value.
    rejected: list[dict[str, str]] = field(default_factory=list)

    def product_fields(self) -> dict[str, Any]:
        """The catalog.product columns this result proposes (None = no proposal)."""
        return {"name_es": self.name_es, "description_es": self.description_es, "category_es": self.category_es,
                "product_kind": self.product_kind, "specs": self.specs or None, "weight_kg": self.weight_kg,
                "length_cm": self.length_cm, "width_cm": self.width_cm, "height_cm": self.height_cm}


# ------------------------------------------------------------------ the request

def scrub_commercial_lines(text: str | None) -> tuple[str, int]:
    """The text without any line naming a currency, price, cost, discount or quote; and how many went."""
    if not text:
        return "", 0
    kept, removed = [], 0
    for line in text.splitlines():
        if _COMMERCIAL.search(line):
            removed += 1
        else:
            kept.append(line)
    return "\n".join(kept).strip(), removed


def product_payload(product: dict[str, Any]) -> dict[str, Any]:
    """The five product keys Claude may see, by name; everything else in the dict is ignored."""
    payload = {}
    for key in PRODUCT_KEYS:
        value = product.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()
    return payload


def source_text(product: dict[str, Any], datasheet_text: str | None, page_text: str | None) -> str:
    """Everything the model is shown, as one text: what its answer is checked against."""
    datasheet, _ = scrub_commercial_lines(datasheet_text)
    page, _ = scrub_commercial_lines(page_text)
    parts = [json.dumps(product_payload(product), ensure_ascii=False)]
    if datasheet:
        parts.append(datasheet)
    if page:
        parts.append(page)
    return "\n\n".join(parts)


SYSTEM_PROMPT = """You write Spanish (Chile) catalog content for laboratory equipment sold in Chile.

You receive one product's identity (brand, model number, English name, kind, category) and, when \
available, text from its datasheet and its manufacturer's web page. Write the product's Spanish \
name, a short Spanish description, its Spanish category, its kind, and its technical \
specifications in Spanish.

Rules — the answer is checked automatically, and anything that breaks a rule is discarded:
- Use only facts stated in the text you are given. Do not add facts from memory.
- Copy every number exactly as the source writes it (a decimal comma instead of a decimal point is \
fine). Never convert units, round, add or derive numbers. If a value is not stated, leave it out.
- weight_kg only when the source writes the weight in kilograms (copy that number); otherwise null.
- dims only when the source states all three dimensions in centimetres (copy them) or millimetres \
(divide by 10 — the only conversion allowed); otherwise null.
- images: only URLs of product images that appear verbatim in the given text, with found_in saying \
whether they came from the datasheet or the page. Otherwise an empty list.
- product_kind is one of: equipment, accessory, consumable, spare_part, service — or null.
- name_es at most 160 characters; description_es at most 1200 characters, factual, no marketing \
superlatives; at most 30 specs.
- Commercial terms are out of scope: never write about prices, availability, distributors or resellers."""


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name_es", "description_es", "category_es", "product_kind", "specs", "weight_kg", "dims", "images"],
    "properties": {
        "name_es": _nullable({"type": "string"}),
        "description_es": _nullable({"type": "string"}),
        "category_es": _nullable({"type": "string"}),
        "product_kind": _nullable({"type": "string", "enum": list(PRODUCT_KINDS)}),
        "specs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["label_es", "value", "unit"],
            "properties": {"label_es": {"type": "string"}, "value": {"type": "string"},
                           "unit": _nullable({"type": "string"})}}},
        "weight_kg": _nullable({"type": "number"}),
        "dims": _nullable({
            "type": "object", "additionalProperties": False, "required": ["length_cm", "width_cm", "height_cm"],
            "properties": {"length_cm": {"type": "number"}, "width_cm": {"type": "number"},
                           "height_cm": {"type": "number"}}}),
        "images": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["url", "found_in"],
            "properties": {"url": {"type": "string"}, "found_in": {"type": "string", "enum": ["datasheet", "page"]}}}},
    },
}


def build_request(product: dict[str, Any], datasheet_text: str | None, page_text: str | None, *,
                  model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """The keyword arguments of `client.beta.messages.create` for one product.

    One synchronous Messages request per product with structured output (`output_config.format`).
    Adaptive thinking at medium effort and the server-side refusal fallback are added only for
    models that accept them (not Haiku 4.5, the default).
    """
    datasheet, _ = scrub_commercial_lines(datasheet_text)
    page, _ = scrub_commercial_lines(page_text)
    content = "PRODUCT\n" + json.dumps(product_payload(product), ensure_ascii=False)
    if datasheet:
        content += "\n\nDATASHEET TEXT\n" + datasheet
    if page:
        content += "\n\nMANUFACTURER PAGE TEXT\n" + page
    model = check_model_id(model)
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }
    if supports_adaptive_thinking(model):
        request["thinking"] = {"type": "adaptive"}
        request["output_config"]["effort"] = EFFORT
    if supports_fallbacks(model):
        request["betas"] = [FALLBACK_BETA]
        request["fallbacks"] = "default"
    return request


def parse_message(message: Any) -> dict[str, Any]:
    """The JSON object of a Messages response, or `EnrichmentRefused`. Checks stop_reason first."""
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        raise EnrichmentRefused("model_refused")
    if stop == "max_tokens":
        raise EnrichmentRefused("truncated")
    text = next((b.text for b in getattr(message, "content", None) or [] if getattr(b, "type", None) == "text"), None)
    if text is None:
        raise EnrichmentRefused("no_text")
    try:
        raw = json.loads(text)
    except ValueError:
        raise EnrichmentRefused("not_json") from None
    if not isinstance(raw, dict):
        raise EnrichmentRefused("not_an_object")
    return raw


# ------------------------------------------------------------------ the validator

def _swap(token: str) -> str:
    return token.translate(str.maketrans({".": ",", ",": "."}))


def _decimals(token: str) -> set[Decimal]:
    """The values a number as written may mean.

    One separator followed by exactly three digits is ambiguous (`1,500` is 1.5 or 1500); any
    other single separator is a decimal one (`2,5` is only 2.5). With several separators, the
    last is the decimal one if it differs from the others, else they are all thousands.
    """
    seps = re.findall(r"[.,]", token)
    digits = re.split(r"[.,]", token)
    try:
        if not seps:
            return {Decimal(token)}
        if len(seps) == 1:
            whole, frac = digits
            out = {Decimal(f"{whole}.{frac}")}
            if len(frac) == 3:
                out.add(Decimal(whole + frac))
            return out
        if seps[-1] != seps[0] and len(set(seps[:-1])) == 1:
            return {Decimal("".join(digits[:-1]) + "." + digits[-1])}
        if len(set(seps)) == 1:
            return {Decimal("".join(digits))}
    except InvalidOperation:
        pass
    return set()


def _value_set(pattern: re.Pattern[str], text: str, groups: tuple[int, ...]) -> dict[str, set[Decimal]]:
    """Unit (lower case) → the values the source writes with it."""
    found: dict[str, set[Decimal]] = {}
    for m in pattern.finditer(text):
        unit = m.group(m.lastindex).lower() if pattern is not _KG else "kg"
        for g in groups:
            found.setdefault(unit, set()).update(_decimals(m.group(g)))
    return found


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


class _Source:
    def __init__(self, text: str) -> None:
        self.text = text or ""
        self.tokens = set(_NUMBER.findall(self.text))
        self.kg = _value_set(_KG, self.text, (1,)).get("kg", set())
        lengths = _value_set(_SINGLE_LENGTH, self.text, (1,))
        for unit, values in _value_set(_TRIPLE_LENGTH, self.text, (1, 2, 3)).items():
            lengths.setdefault(unit, set()).update(values)
        self.cm, self.mm = lengths.get("cm", set()), lengths.get("mm", set())

    def states_numbers_in(self, text: str) -> bool:
        return all(t in self.tokens or _swap(t) in self.tokens for t in _NUMBER.findall(text))

    def states_kg(self, value: Any) -> Decimal | None:
        number = _as_decimal(value)
        return number if number is not None and number in self.kg else None

    def states_cm(self, value: Any) -> Decimal | None:
        number = _as_decimal(value)
        if number is None:
            return None
        return number if number in self.cm or number * 10 in self.mm else None


def host_allowed(host: str | None, domains: tuple[str, ...] | list[str]) -> bool:
    """The host equals one of the manufacturer domains or is a subdomain of one; never an IP literal."""
    if not host:
        return False
    host = host.rstrip(".").lower()
    try:
        ip_address(host)
        return False
    except ValueError:
        pass
    return any(host == d or host.endswith("." + d) for d in (x.lower() for x in domains))


def image_url_problem(url: str, domains: tuple[str, ...] | list[str]) -> str | None:
    """Why this URL may not be fetched as a manufacturer image, or None."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "malformed"
    if parts.scheme != "https":
        return "not_https"
    if parts.username is not None or parts.password is not None:
        return "credentials"
    if port not in (None, 443):
        return "port"
    if not host_allowed(parts.hostname, domains):
        return "not_manufacturer_domain"
    return None


def _all_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _all_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _all_strings(v)


def validate_response(raw: dict[str, Any], *, source_text: str = "",
                      manufacturer_domains: tuple[str, ...] | list[str] = ()) -> EnrichmentResult:
    """What may be written of the model's answer. `LabdeliveryRefused` refuses the whole item.

    The defaults are the safe ones: with no source text no number survives, and with no
    manufacturer domain no image does.
    """
    if not isinstance(raw, dict):
        raise EnrichmentRefused("not_an_object")
    refuse_labdelivery(*_all_strings(raw))
    src = _Source(source_text)
    result = EnrichmentResult()

    def reject(name: str, reason: str) -> None:
        result.rejected.append({"field": name, "reason": reason})

    def text(name: str, limit: int) -> str | None:
        value = raw.get(name)
        if value is None:
            return None
        if not isinstance(value, str):
            reject(name, "not_a_string")
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > limit:
            reject(name, "too_long")
            return None
        if not src.states_numbers_in(value):
            reject(name, "number_not_in_source")
            return None
        return value

    result.name_es = text("name_es", MAX_NAME)
    result.description_es = text("description_es", MAX_DESCRIPTION)
    result.category_es = text("category_es", MAX_CATEGORY)

    kind = raw.get("product_kind")
    if kind is not None:
        if kind in PRODUCT_KINDS:
            result.product_kind = kind
        else:
            reject("product_kind", "not_a_kind")

    specs = raw.get("specs") or []
    if not isinstance(specs, list):
        reject("specs", "not_a_list")
        specs = []
    for i, spec in enumerate(specs):
        name = f"specs[{i}]"
        if len(result.specs) >= MAX_SPECS:
            reject(name, "too_many")
            continue
        if not isinstance(spec, dict):
            reject(name, "not_an_object")
            continue
        label, value, unit = spec.get("label_es"), spec.get("value"), spec.get("unit")
        if not isinstance(label, str) or not isinstance(value, str) or not (unit is None or isinstance(unit, str)):
            reject(name, "not_strings")
            continue
        label, value, unit = label.strip(), value.strip(), (unit or "").strip() or None
        if not label or not value:
            reject(name, "blank")
            continue
        if len(label) > MAX_SPEC_LABEL or len(value) > MAX_SPEC_VALUE or (unit and len(unit) > MAX_SPEC_UNIT):
            reject(name, "too_long")
            continue
        if not all(src.states_numbers_in(part) for part in (label, value, unit or "")):
            reject(name, "number_not_in_source")
            continue
        result.specs.append({"label_es": label, "value": value, "unit": unit, "source": "machine"})

    if raw.get("weight_kg") is not None:
        result.weight_kg = src.states_kg(raw["weight_kg"])
        if result.weight_kg is None:
            reject("weight_kg", "not_stated_in_kg")

    dims = raw.get("dims")
    if dims is not None:
        values = [src.states_cm(dims.get(k)) if isinstance(dims, dict) else None
                  for k in ("length_cm", "width_cm", "height_cm")]
        if all(v is not None for v in values):
            result.length_cm, result.width_cm, result.height_cm = values
        else:
            reject("dims", "not_stated_in_cm_or_mm")

    images = raw.get("images") or []
    if not isinstance(images, list):
        reject("images", "not_a_list")
        images = []
    seen: set[str] = set()
    for i, image in enumerate(images):
        name = f"images[{i}]"
        url = image.get("url") if isinstance(image, dict) else None
        found_in = image.get("found_in") if isinstance(image, dict) else None
        if not isinstance(url, str) or found_in not in ("datasheet", "page"):
            reject(name, "malformed")
            continue
        url = url.strip()
        problem = image_url_problem(url, tuple(manufacturer_domains))
        if problem is None and url not in src.text:
            problem = "not_in_source"
        if problem is None and len(result.images) >= MAX_IMAGES:
            problem = "too_many"
        if problem is not None:
            reject(name, problem)
            continue
        if url in seen:
            continue
        seen.add(url)
        result.images.append((url, "datasheet" if found_in == "datasheet" else "manufacturer_site"))
    return result
