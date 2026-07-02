"""Warm commercial cases (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from origenlab_api.schemas.cases import WARM_CASE_CATEGORIES, WarmCasesResponse
from origenlab_api.services.warm_case_service import build_warm_cases_response
from origenlab_api.settings import Settings, get_settings

router = APIRouter(tags=["cases"])


@router.get("/cases/warm", response_model=WarmCasesResponse)
def cases_warm(
    settings: Settings = Depends(get_settings),
    days: int = Query(14, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
    category: str | None = Query(
        None,
        description=(
            "Filter by canonical category. Legacy aliases are accepted and normalized; "
            "see meta.canonical_categories and meta.legacy_category_aliases."
        ),
    ),
    positive_signal_only: bool = Query(
        False,
        description="When true, omit auto-reply/security noise after normalization.",
    ),
    include_noise: bool = Query(False),
) -> WarmCasesResponse:
    if category is not None:
        cat = category.strip().lower()
        if cat not in WARM_CASE_CATEGORIES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid category {category!r}; allowed: {sorted(WARM_CASE_CATEGORIES)}",
            )
        category = cat
    return build_warm_cases_response(
        settings,
        days=days,
        limit=limit,
        category=category,
        positive_signal_only=positive_signal_only,
        include_noise=include_noise,
    )
