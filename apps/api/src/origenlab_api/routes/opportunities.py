"""Equipment-first opportunities (read-only read model)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query

from origenlab_api.schemas.commercial_opportunities import (
    CommercialOpportunitiesResponse,
    CommercialOpportunityDetailResponse,
)
from origenlab_api.schemas.opportunities import EquipmentOpportunitiesResponse
from origenlab_api.services.commercial_opportunity_service import (
    build_commercial_opportunities_response,
    build_commercial_opportunity_detail_response,
)
from origenlab_api.services.equipment_opportunity_service import (
    build_equipment_opportunities_response,
)
from origenlab_api.settings import Settings, get_settings

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("/equipment", response_model=EquipmentOpportunitiesResponse)
def equipment_opportunities(
    settings: Settings = Depends(get_settings),
    limit: int = Query(50, ge=1, le=200),
    priority: int | None = Query(None, ge=1, le=999),
    next_action: str | None = Query(None, description="Exact next_action filter"),
    safe_channel: str | None = Query(None, description="Exact safe_channel filter"),
    include_account_intelligence: bool = Query(
        True,
        description="When false, omit account_intelligence_only / skip_consumables rows",
    ),
) -> EquipmentOpportunitiesResponse:
    return build_equipment_opportunities_response(
        settings,
        limit=limit,
        priority=priority,
        next_action=next_action,
        safe_channel=safe_channel,
        include_account_intelligence=include_account_intelligence,
    )


@router.get("/commercial", response_model=CommercialOpportunitiesResponse)
def commercial_opportunities(
    settings: Settings = Depends(get_settings),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    canonical_stage: str | None = Query(None, min_length=1, max_length=128),
    record_kind: str | None = Query(None, min_length=1, max_length=128),
    review_status: str | None = Query(None, min_length=1, max_length=128),
    account_id: str | None = Query(None, min_length=1, max_length=128),
    primary_contact_id: str | None = Query(None, min_length=1, max_length=128),
) -> CommercialOpportunitiesResponse:
    return build_commercial_opportunities_response(
        settings,
        limit=limit,
        offset=offset,
        canonical_stage=canonical_stage,
        record_kind=record_kind,
        review_status=review_status,
        account_id=account_id,
        primary_contact_id=primary_contact_id,
    )


@router.get(
    "/commercial/{opportunity_id}",
    response_model=CommercialOpportunityDetailResponse,
)
def commercial_opportunity_detail(
    opportunity_id: str = PathParam(min_length=1, max_length=128),
    settings: Settings = Depends(get_settings),
) -> CommercialOpportunityDetailResponse:
    result = build_commercial_opportunity_detail_response(
        settings,
        opportunity_id,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Commercial opportunity not found",
        )
    return result
