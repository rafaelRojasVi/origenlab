"""Catalog capability derived from the recorded catalog seed, not a hand list.

"Verified catalog capability" means OrigenLab has a recorded equipment product or
category for that equipment class. Everything else is either a placeholder that
needs specifications, a class we only suspect, or plainly outside the catalog.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.catalog.catalog_seed import (
    CatalogSeedValidationError,
    default_seed_path,
    load_seed_json,
    validate_seed,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)

MATCH_EXACT_PRODUCT = "exact_catalog_product"
MATCH_VERIFIED_CLASS = "verified_catalog_equipment_class"
MATCH_NEEDS_CONFIRMATION = "catalog_equipment_class_needs_confirmation"
MATCH_CATEGORY_PLACEHOLDER = "category_placeholder_needs_specs"
MATCH_OUTSIDE_CATALOG = "outside_recorded_catalog"
MATCH_NO_CATALOG_MATCH = "no_catalog_match"
MATCH_NOT_APPLICABLE = "not_applicable"

CATALOG_MATCH_STATUSES = (
    MATCH_EXACT_PRODUCT,
    MATCH_VERIFIED_CLASS,
    MATCH_NEEDS_CONFIRMATION,
    MATCH_CATEGORY_PLACEHOLDER,
    MATCH_OUTSIDE_CATALOG,
    MATCH_NO_CATALOG_MATCH,
    MATCH_NOT_APPLICABLE,
)

# Catalog vocabulary ↔ procurement taxonomy vocabulary. Only explicit, reviewed
# synonyms belong here; nothing is inferred from string similarity.
CATALOG_TO_TAXONOMY_CLASS_ALIASES: dict[str, tuple[str, ...]] = {
    "sonicator": ("ultrasonic_processor",),
}
TAXONOMY_TO_CATALOG_CLASS_ALIASES: dict[str, str] = {
    taxonomy: catalog
    for catalog, taxonomy_classes in CATALOG_TO_TAXONOMY_CLASS_ALIASES.items()
    for taxonomy in taxonomy_classes
}

# Product confidence levels that record an intent to sell but not a confirmed
# specification we can quote against a tender.
UNCONFIRMED_PRODUCT_CONFIDENCE = frozenset(
    {"extracted_needs_review", "website_editorial"}
)

# An alias short enough to appear inside unrelated wording (a bare volume such as
# "5L") cannot identify a product on its own.
MIN_PRODUCT_ALIAS_LENGTH = 4

_EQUIPMENT_PRODUCT_KIND = "equipment"


def _norm(text: str | None) -> str:
    if not text:
        return ""
    nk = unicodedata.normalize("NFKD", str(text))
    raw = "".join(c for c in nk if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", raw).strip()


@lru_cache(maxsize=4)
def load_catalog_capability(seed_path: str | None = None) -> "CatalogCapability":
    """Load and validate the catalog seed once per process."""
    path = Path(seed_path) if seed_path else default_seed_path()
    data = load_seed_json(path)
    try:
        validate_seed(data)
        validated = True
    except CatalogSeedValidationError:
        # A seed that fails validation must never widen claimed capability, but it
        # must not take the recognition layer down either.
        validated = False
    return CatalogCapability.from_seed(data, validated=validated)


class CatalogCapability:
    """Equipment capability recorded in the catalog seed."""

    def __init__(
        self,
        *,
        category_classes: frozenset[str],
        confirmed_product_classes: frozenset[str],
        unconfirmed_product_classes: frozenset[str],
        placeholder_category_keys: frozenset[str],
        product_names: dict[str, str],
        seed_version: str | None,
        validated: bool,
        products: tuple[dict[str, Any], ...] = (),
        confirmed_product_names: dict[str, str] | None = None,
    ) -> None:
        self.category_classes = category_classes
        self.confirmed_product_classes = confirmed_product_classes
        self.unconfirmed_product_classes = unconfirmed_product_classes
        self.placeholder_category_keys = placeholder_category_keys
        self.product_names = product_names
        self.confirmed_product_names = confirmed_product_names or {}
        self.products = products
        self.seed_version = seed_version
        self.validated = validated

    @classmethod
    def from_seed(
        cls, data: dict[str, Any], *, validated: bool = True
    ) -> "CatalogCapability":
        if not validated:
            # A seed that failed validation proves nothing about what OrigenLab
            # sells, so it must expose zero capability rather than a partial one.
            return cls(
                category_classes=frozenset(),
                confirmed_product_classes=frozenset(),
                unconfirmed_product_classes=frozenset(),
                placeholder_category_keys=frozenset(),
                product_names={},
                confirmed_product_names={},
                products=(),
                seed_version=data.get("seed_version"),
                validated=False,
            )

        category_classes: set[str] = set()
        placeholders: set[str] = set()
        for category in data.get("categories") or []:
            equipment_class = category.get("equipment_class")
            if equipment_class:
                category_classes.add(str(equipment_class))
            else:
                key = category.get("category_key")
                if key:
                    placeholders.add(str(key))

        confirmed: set[str] = set()
        unconfirmed: set[str] = set()
        names: dict[str, str] = {}
        confirmed_names: dict[str, str] = {}
        products: list[dict[str, Any]] = []
        for product in data.get("products") or []:
            if str(product.get("product_kind") or "") != _EQUIPMENT_PRODUCT_KIND:
                continue
            equipment_class = product.get("equipment_class")
            if not equipment_class:
                continue
            confidence = str(product.get("confidence") or "")
            is_confirmed = confidence not in UNCONFIRMED_PRODUCT_CONFIDENCE
            (confirmed if is_confirmed else unconfirmed).add(str(equipment_class))
            product_key = str(product.get("product_key"))
            aliases = _product_lookup_names(product)
            for alias in aliases:
                names[alias] = product_key
                if is_confirmed:
                    confirmed_names[alias] = product_key
            products.append(
                {
                    "product_key": product_key,
                    "product_kind": str(product.get("product_kind") or ""),
                    "equipment_class": str(equipment_class),
                    "display_name": product.get("display_name"),
                    "model_number": product.get("model_number"),
                    "aliases": sorted(aliases),
                    "confidence": confidence,
                    "confirmed": is_confirmed,
                    "category_key": product.get("category_key"),
                }
            )

        return cls(
            category_classes=frozenset(category_classes),
            confirmed_product_classes=frozenset(confirmed),
            unconfirmed_product_classes=frozenset(unconfirmed),
            placeholder_category_keys=frozenset(placeholders),
            product_names=names,
            confirmed_product_names=confirmed_names,
            products=tuple(sorted(products, key=lambda p: p["product_key"])),
            seed_version=data.get("seed_version"),
            validated=validated,
        )

    def _expand(self, classes: frozenset[str]) -> frozenset[str]:
        expanded = set(classes)
        for catalog_class in classes:
            expanded.update(CATALOG_TO_TAXONOMY_CLASS_ALIASES.get(catalog_class, ()))
        return frozenset(expanded)

    @property
    def verified_equipment_classes(self) -> frozenset[str]:
        """Classes with a recorded catalog category or a confirmed product."""
        return self._expand(self.category_classes | self.confirmed_product_classes)

    @property
    def product_confirmed_equipment_classes(self) -> frozenset[str]:
        """Classes backed by at least one product with confirmed specifications."""
        return self._expand(self.confirmed_product_classes)

    @property
    def needs_confirmation_equipment_classes(self) -> frozenset[str]:
        """Classes seen only on products whose specifications are unreviewed."""
        return self._expand(self.unconfirmed_product_classes) - (
            self.product_confirmed_equipment_classes
        )

    def digest(self) -> str:
        return canonical_json_digest(
            {
                "seed_version": self.seed_version,
                "validated": self.validated,
                "category_classes": sorted(self.category_classes),
                "confirmed_product_classes": sorted(self.confirmed_product_classes),
                "unconfirmed_product_classes": sorted(
                    self.unconfirmed_product_classes
                ),
                "placeholder_category_keys": sorted(self.placeholder_category_keys),
                # Every product field that can move a match: product_key, kind,
                # equipment_class, display_name, model_number, aliases, confidence
                # and the category link.
                "products": [dict(p) for p in self.products],
                "class_aliases": {
                    k: list(v) for k, v in sorted(CATALOG_TO_TAXONOMY_CLASS_ALIASES.items())
                },
            }
        )


def _product_lookup_names(product: dict[str, Any]) -> list[str]:
    names = [product.get("display_name"), product.get("model_number")]
    for alias in product.get("aliases") or []:
        if isinstance(alias, dict):
            names.append(alias.get("alias_code") or alias.get("alias_text"))
        else:
            names.append(alias)
    normalized = [_norm(n) for n in names if n and _norm(n)]
    return [n for n in normalized if len(n) >= MIN_PRODUCT_ALIAS_LENGTH]


def verified_catalog_equipment_classes() -> frozenset[str]:
    """Equipment classes OrigenLab demonstrably sells, per the recorded catalog."""
    return load_catalog_capability().verified_equipment_classes


def catalog_equipment_classes_needing_confirmation() -> frozenset[str]:
    """Classes present in the catalog but without confirmed specifications."""
    return load_catalog_capability().needs_confirmation_equipment_classes


def catalog_digest() -> str:
    """Stable digest of the capability derived from the seed."""
    return load_catalog_capability().digest()


def matched_catalog_product_key(text: str | None) -> str | None:
    """Product key whose confirmed alias appears in ``text``, if any.

    Only operator-confirmed products can identify an exact match: an alias taken
    from an unreviewed extraction records an intent to sell, not a specification
    OrigenLab can quote against a tender.
    """
    normalized_text = _norm(text)
    if not normalized_text:
        return None
    capability = load_catalog_capability()
    for alias, product_key in sorted(capability.confirmed_product_names.items()):
        if alias and alias in normalized_text:
            return product_key
    return None


def match_catalog_status(
    category: str | None,
    *,
    text: str | None = None,
    is_purchase_signal: bool = True,
    outside_catalog_context: bool = False,
    specs_required: bool = False,
) -> str:
    """Classify one equipment category against recorded catalog capability."""
    if not is_purchase_signal:
        return MATCH_NOT_APPLICABLE
    capability = load_catalog_capability()
    if matched_catalog_product_key(text) is not None:
        return MATCH_EXACT_PRODUCT
    if outside_catalog_context:
        return MATCH_OUTSIDE_CATALOG
    if not category:
        return MATCH_CATEGORY_PLACEHOLDER if specs_required else MATCH_NO_CATALOG_MATCH
    name = str(category)
    known = (
        name in capability.verified_equipment_classes
        or name in capability.needs_confirmation_equipment_classes
    )
    if specs_required and known:
        return MATCH_CATEGORY_PLACEHOLDER
    if name in capability.product_confirmed_equipment_classes:
        return MATCH_VERIFIED_CLASS
    if name in capability.verified_equipment_classes:
        # A recorded category with no confirmed product behind it is capability we
        # intend to have, not capability we can prove against a specification.
        return MATCH_NEEDS_CONFIRMATION
    if name in capability.needs_confirmation_equipment_classes:
        return MATCH_NEEDS_CONFIRMATION
    if specs_required:
        return MATCH_CATEGORY_PLACEHOLDER
    return MATCH_OUTSIDE_CATALOG


__all__ = [
    "CATALOG_MATCH_STATUSES",
    "CATALOG_TO_TAXONOMY_CLASS_ALIASES",
    "MIN_PRODUCT_ALIAS_LENGTH",
    "CatalogCapability",
    "MATCH_CATEGORY_PLACEHOLDER",
    "MATCH_EXACT_PRODUCT",
    "MATCH_NEEDS_CONFIRMATION",
    "MATCH_NOT_APPLICABLE",
    "MATCH_NO_CATALOG_MATCH",
    "MATCH_OUTSIDE_CATALOG",
    "MATCH_VERIFIED_CLASS",
    "TAXONOMY_TO_CATALOG_CLASS_ALIASES",
    "catalog_digest",
    "catalog_equipment_classes_needing_confirmation",
    "load_catalog_capability",
    "match_catalog_status",
    "matched_catalog_product_key",
    "verified_catalog_equipment_classes",
]
