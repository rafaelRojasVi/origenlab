"""CLI-oriented API contract polish tests."""

from __future__ import annotations

from origenlab_api.schemas.cases import (
    CANONICAL_WARM_CASE_CATEGORIES,
    LEGACY_WARM_CASE_CATEGORY_ALIASES,
    WarmCaseItem,
    WarmCasesMeta,
)
from origenlab_api.services.warm_case_output_normalize import normalize_warm_case_items


THIS_IS_A_SUPPLIER_DOMAIN = "southamerican@hinotek.com"


def test_warm_cases_meta_exposes_category_taxonomy_for_cli_clients() -> None:
    meta = WarmCasesMeta()

    assert meta.canonical_categories == list(CANONICAL_WARM_CASE_CATEGORIES)
    assert meta.legacy_category_aliases == LEGACY_WARM_CASE_CATEGORY_ALIASES
    assert "supplier_followup" in meta.canonical_categories
    assert meta.legacy_category_aliases["supplier_reply"] == "supplier_followup"
    assert meta.legacy_category_aliases["client_reply"] == "client_response"
    assert meta.legacy_category_aliases["vendor_logistics"] == "logistics_admin"


def test_legacy_warm_category_filter_matches_normalized_category() -> None:
    item = WarmCaseItem(
        case_id="case-1",
        last_email_id=1,
        contact_email=THIS_IS_A_SUPPLIER_DOMAIN,
        subject="Re: cotizacion",
        category="supplier_reply",
        status="new",
        snippet="Oferta recibida",
    )

    filtered = normalize_warm_case_items(
        [item],
        include_noise=False,
        category_filter="supplier_reply",
    )

    assert len(filtered) == 1
    assert filtered[0].category == "supplier_followup"
