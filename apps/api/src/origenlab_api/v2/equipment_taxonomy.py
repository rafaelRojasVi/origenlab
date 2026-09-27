"""The website's equipment catalogue, used as the CRM's equipment-interest taxonomy.

`equipment_taxonomy.json` is generated from `apps/web/src/data/*.ts` by
`apps/api/scripts/export_equipment_taxonomy.mjs` and is checked for drift in the test suite, so
the CRM never knows a brand, family, model or product image the public site does not publish.

`match()` reads free text — a quotation's subject line, an attachment's file name, a case
title, a model string on a recorded interest — and says which catalogue entries it names, and
**by which exact term**. The term travels with every match so an operator can see why a record
was classified the way it was; a match is a reading of the text, never an interest by itself.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

TAXONOMY_PATH = Path(__file__).with_name("equipment_taxonomy.json")


def fold(text: str) -> str:
    """Lower-case and strip accents, so "Löser" and "Loser" read the same."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """A word-bounded pattern that tolerates spaces or hyphens between an alias's parts.

    "T 25 digital" also matches "T25 digital" and "T-25 digital"; "UP200St" matches "up200st".
    Boundaries are alphanumeric, so "IKA" does not match inside "mecánica".
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", fold(alias).strip()) if p]
    body = r"[\s\-]*".join(parts)
    return re.compile(rf"(?<![0-9a-z]){body}(?![0-9a-z])")


@dataclass(frozen=True)
class TaxonomyMatch:
    brand_id: str
    family_id: str
    model_id: str | None
    #: The catalogue alias that matched, and the text it matched (folded: lower-case, no accents).
    term: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "brand_id": self.brand_id,
            "family_id": self.family_id,
            "model_id": self.model_id,
            "matched_term": self.term,
            "matched_text": self.text,
        }


class EquipmentTaxonomy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.families = {f["id"]: f for f in data["families"]}
        self.brands = {b["id"]: b for b in data["brands"]}
        self.models = {m["id"]: m for m in data["models"]}
        # Longest alias first, so "Biocen 22 R" wins over "Biocen 22" on the same text.
        self._model_patterns = sorted(
            (
                (len(alias), _alias_pattern(alias), alias, m)
                for m in data["models"]
                for alias in m["aliases"]
            ),
            key=lambda t: -t[0],
        )
        self._brand_patterns = [
            (_alias_pattern(alias), alias, b) for b in data["brands"] for alias in b["aliases"]
        ]

    def match(self, text: str | None) -> list[TaxonomyMatch]:
        """Every catalogue entry the text names. A model match implies its brand."""
        if not text:
            return []
        folded = fold(text)
        found: dict[tuple[str, str | None], TaxonomyMatch] = {}
        consumed: list[tuple[int, int]] = []
        for _, pattern, alias, model in self._model_patterns:
            for hit in pattern.finditer(folded):
                span = hit.span()
                if any(s <= span[0] and span[1] <= e for s, e in consumed):
                    continue  # already inside a longer alias of some model
                consumed.append(span)
                key = (model["brand_id"], model["id"])
                found.setdefault(
                    key,
                    TaxonomyMatch(model["brand_id"], model["family_id"], model["id"], alias, hit.group(0)),
                )
        brands_with_model = {b for b, m in found if m is not None}
        for pattern, alias, brand in self._brand_patterns:
            if brand["id"] in brands_with_model or (brand["id"], None) in found:
                continue
            hit = pattern.search(folded)
            if hit:
                found[(brand["id"], None)] = TaxonomyMatch(
                    brand["id"], brand["family_id"], None, alias, hit.group(0)
                )
        return list(found.values())

    def public(self) -> dict[str, Any]:
        """The catalogue as the dashboard needs it: filters, labels and template images."""
        return self.data


@lru_cache(maxsize=1)
def load_taxonomy() -> EquipmentTaxonomy:
    return EquipmentTaxonomy(json.loads(TAXONOMY_PATH.read_text(encoding="utf-8")))
