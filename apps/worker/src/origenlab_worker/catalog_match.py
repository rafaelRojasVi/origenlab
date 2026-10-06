"""Cheap product candidates for a message, before any model: model numbers matched exactly against
`catalog.product.model_key`, then the catalog's Spanish full-text index for the words of the
subject and the first lines of the body.

`model_tokens` and `search_words` are pure. `match_products` runs two read-only queries as
`origenlab_worker`, which holds SELECT on catalog.product (Slice 0). It never writes the catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from origenlab_api.v2.mail_rules import fold

#: A model-number-shaped token: letters and digits together, joined by - _ . / at most.
_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9](?:[A-Za-z0-9\-_./]{1,28})[A-Za-z0-9])(?![A-Za-z0-9])")
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_DIGIT = re.compile(r"\d")
#: The normalisation catalog.product.model_key is generated with (20261005134832).
_KEY_STRIP = re.compile(r"[\s\-_./]")
#: Tokens that look like model numbers and never are: our quote and order numbers, units, ids.
_NOT_A_MODEL = re.compile(r"^(?:CN\d+|OC\d+|N\d+|\d+(?:ML|L|KG|G|MG|MM|CM|M|V|W|HZ|RPM|C|H|MIN|S|X)|RUT\d+K?)$")
_WORD = re.compile(r"[a-z0-9]{4,}")
_STOP = frozenset({
    "para", "favor", "solicito", "solicitamos", "solicitud", "cotizacion", "cotizar", "precio",
    "precios", "presupuesto", "saludos", "estimado", "estimados", "estimada", "gracias", "quisiera",
    "necesito", "necesitamos", "podrian", "puede", "pueden", "enviar", "envio", "adjunto", "adjunta",
    "quedo", "atento", "atenta", "buenos", "buenas", "tardes", "dias", "hola", "cordial", "nuestro",
    "nuestra", "unidad", "unidades", "equipo", "equipos", "laboratorio", "universidad", "desde",
    "sobre", "este", "esta", "estos", "estas", "tiene", "tienen", "como", "cual", "cuales", "donde",
    "with", "from", "this", "that", "please", "quote", "quotation", "price", "regards",
})
MAX_WORDS = 24
MAX_CANDIDATES = 8


@dataclass(frozen=True)
class ProductCandidate:
    product_id: str
    model_number: str | None
    model_key: str | None
    name: str | None
    how: str  # 'model_key' | 'text'
    #: The token in the message it matched (model_key), or the full-text rank × 1000 (text).
    evidence: str


def model_key(token: str) -> str:
    return _KEY_STRIP.sub("", token).upper()


def model_tokens(*texts: str) -> list[str]:
    """Distinct normalised model keys named in `texts`, in first-seen order."""
    seen: dict[str, None] = {}
    for text in texts:
        for match in _TOKEN.finditer(text or ""):
            raw = match.group(1)
            if not (_HAS_LETTER.search(raw) and _HAS_DIGIT.search(raw)):
                continue
            key = model_key(raw)
            if len(key) < 3 or _NOT_A_MODEL.match(key):
                continue
            seen.setdefault(key, None)
    return list(seen)


def search_words(subject: str, body: str, *, body_chars: int = 600) -> list[str]:
    """The words worth a full-text lookup: folded, ≥ 4 characters, no greeting or filler."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(fold(f"{subject} {body[:body_chars]}")):
        if word not in _STOP and not word.isdigit():
            seen.setdefault(word, None)
        if len(seen) >= MAX_WORDS:
            break
    return list(seen)


_BY_KEY = """
select id::text, model_number, model_key, coalesce(name_es, name)
  from catalog.product
 where model_key = any(%s)
 order by model_key, id
 limit %s
"""
# An OR query over the words (each is [a-z0-9]{4,}, so nothing in it is tsquery syntax).
_BY_TEXT = """
select id::text, model_number, model_key, coalesce(name_es, name),
       ts_rank(search_tsv, q) as rank
  from catalog.product, to_tsquery('spanish', %s) q
 where search_tsv @@ q
 order by rank desc, id
 limit %s
"""


def match_products(cur: Any, subject: str, body: str) -> list[ProductCandidate]:
    keys = model_tokens(subject, body)
    found: list[ProductCandidate] = []
    if keys:
        cur.execute(_BY_KEY, (keys, MAX_CANDIDATES))
        for pid, number, key, name in cur.fetchall():
            found.append(ProductCandidate(pid, number, key, name, "model_key", key))
    if found:
        return found
    words = search_words(subject, body)
    if not words:
        return []
    cur.execute(_BY_TEXT, (" | ".join(words), 5))
    for pid, number, key, name, rank in cur.fetchall():
        found.append(ProductCandidate(pid, number, key, name, "text", str(round(float(rank) * 1000))))
    return found


__all__ = ["MAX_CANDIDATES", "ProductCandidate", "match_products", "model_key", "model_tokens", "search_words"]
