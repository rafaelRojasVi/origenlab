"""Institution-prospect procurement intelligence (read-only read model)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from origenlab_api.schemas.institution_prospects import (
    InstitutionProspectDetailResponse,
    InstitutionProspectsResponse,
    InstitutionProspectStatusResponse,
    OperatorQueueRowsResponse,
    QueueName,
)
from origenlab_api.schemas.tender_terms import TenderDetailResponse
from origenlab_api.services.institution_prospect_service import (
    build_institution_detail_response,
    build_institution_prospect_status_response,
    build_institutions_response,
    build_queue_response,
)
from origenlab_api.services.tender_terms_service import build_tender_detail_response
from origenlab_api.settings import Settings, get_settings

router = APIRouter(prefix="/operator/procurement", tags=["procurement"])

_MAX_PAGE_SIZE = 500


@router.get("/status", response_model=InstitutionProspectStatusResponse)
def procurement_status(
    settings: Settings = Depends(get_settings),
) -> InstitutionProspectStatusResponse:
    return build_institution_prospect_status_response(settings)


@router.get("/institutions", response_model=InstitutionProspectsResponse)
def procurement_institutions(
    settings: Settings = Depends(get_settings),
    limit: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    institution_id: str | None = Query(None, description="Exact institution_id filter"),
    q: str | None = Query(None, description="Search display_name / normalized_name / institution_id"),
) -> InstitutionProspectsResponse:
    return build_institutions_response(
        settings,
        limit=limit,
        offset=offset,
        institution_id=institution_id,
        search=q,
    )


@router.get("/institutions/{institution_id}", response_model=InstitutionProspectDetailResponse)
def procurement_institution_detail(
    institution_id: str,
    settings: Settings = Depends(get_settings),
) -> InstitutionProspectDetailResponse:
    result = build_institution_detail_response(settings, institution_id)
    # A healthy feed with no matching institution is a real 404. A degraded
    # feed (missing/malformed/unsupported contract) is not "not found" — it is
    # "unavailable", and the caller must see that distinction in meta rather
    # than a misleading 404, so it is returned as-is (200, item=None).
    if result.item is None and not result.meta.reduced_mode:
        raise HTTPException(status_code=404, detail="institution not found")
    return result


@router.get("/tenders/{tender_code}", response_model=TenderDetailResponse)
def procurement_tender_detail(
    tender_code: str,
    settings: Settings = Depends(get_settings),
) -> TenderDetailResponse:
    """Merged W1 (actionability) + T1 (ANEXO term intelligence) tender view.

    W1's current_opportunity_queue is the sole actionability authority: if
    the tender_code is not present there, this is a real 404 regardless of
    whether T1 has published terms for it (T1 alone can never make a tender
    actionable). A degraded W1 feed (missing/malformed) is not "not found";
    it is surfaced via queue_meta.reduced_mode with found_in_queue=False.
    """
    result = build_tender_detail_response(settings, tender_code)
    if not result.found_in_queue and not result.queue_meta.get("reduced_mode"):
        raise HTTPException(status_code=404, detail="tender not found in current opportunity queue")
    return result


@router.get("/queues/{queue_name}", response_model=OperatorQueueRowsResponse)
def procurement_queue(
    queue_name: QueueName,
    settings: Settings = Depends(get_settings),
    limit: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    institution_id: str | None = Query(None, description="Exact institution_id filter"),
    tender_code: str | None = Query(None, description="Exact tender_code filter"),
    equipment_category: str | None = Query(None, description="Exact equipment_category filter"),
    commercial_signal_type: str | None = Query(
        None, description="Exact commercial_signal_type filter"
    ),
    q: str | None = Query(None, description="Search display_name / institution_id / tender_code"),
) -> OperatorQueueRowsResponse:
    return build_queue_response(
        settings,
        queue_name,
        limit=limit,
        offset=offset,
        institution_id=institution_id,
        tender_code=tender_code,
        equipment_category=equipment_category,
        commercial_signal_type=commercial_signal_type,
        search=q,
    )
