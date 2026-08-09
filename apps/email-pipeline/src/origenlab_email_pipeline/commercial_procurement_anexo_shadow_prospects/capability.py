"""Catalog capability for shadow annex claims — seed API only, never tender text."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_capability import (
    MATCH_CATEGORY_PLACEHOLDER,
    MATCH_EXACT_PRODUCT,
    MATCH_NEEDS_CONFIRMATION,
    MATCH_OUTSIDE_CATALOG,
    MATCH_VERIFIED_CLASS,
    catalog_digest,
    match_catalog_status,
)

from .constants import (
    CAPABILITY_IN_SCOPE,
    CAPABILITY_OUT_OF_SCOPE,
    CAPABILITY_UNCLEAR,
)
from .models import ClaimCapability

_IN_SCOPE_STATUSES = frozenset(
    {
        MATCH_EXACT_PRODUCT,
        MATCH_VERIFIED_CLASS,
        MATCH_NEEDS_CONFIRMATION,
        MATCH_CATEGORY_PLACEHOLDER,
    }
)


def classify_equipment_capability(equipment_class: str) -> ClaimCapability:
    """Map one taxonomy class to in_scope / out_of_scope / unclear via catalog seed."""
    status = match_catalog_status(equipment_class, is_purchase_signal=True)
    if status in _IN_SCOPE_STATUSES:
        capability = CAPABILITY_IN_SCOPE
    elif status == MATCH_OUTSIDE_CATALOG:
        capability = CAPABILITY_OUT_OF_SCOPE
    else:
        capability = CAPABILITY_UNCLEAR
    return ClaimCapability(
        equipment_class=equipment_class,
        capability=capability,
        catalog_match_status=status,
    )


def classify_classes(classes: tuple[str, ...] | list[str]) -> tuple[ClaimCapability, ...]:
    return tuple(
        classify_equipment_capability(c)
        for c in sorted({str(c) for c in classes if c and not str(c).startswith("relevance:")})
    )


def capability_digest() -> str:
    return catalog_digest()
