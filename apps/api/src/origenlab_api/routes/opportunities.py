"""Equipment-first opportunities (read-only read model)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from origenlab_api.schemas.opportunities import EquipmentOpportunitiesResponse
from origenlab_api.services.equipment_opportunity_service import build_equipment_opportunities_response
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
