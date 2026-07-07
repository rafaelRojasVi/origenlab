"""Mirror lead research prospects (read-only Postgres lead_intel.*)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from origenlab_api.mirror.deps import MirrorDbConn
from origenlab_email_pipeline.lead_research.prospect_action_queue_export import EXPORT_QUEUES
from origenlab_email_pipeline.postgres_dashboard_api.lead_intel import (
    export_lead_prospects_csv,
    get_lead_prospect,
    get_lead_research_summary,
    list_lead_prospects,
)
from origenlab_email_pipeline.postgres_dashboard_api.schemas import (
    LeadProspectDetailResponse,
    LeadProspectsListResponse,
    LeadResearchSummaryResponse,
)

router = APIRouter(tags=["postgres-mirror"])


@router.get("/prospects", response_model=LeadProspectsListResponse)
def mirror_list_lead_prospects(
    conn: MirrorDbConn,
    q: Annotated[str | None, Query(max_length=200)] = None,
    classification: Annotated[str | None, Query(max_length=80)] = None,
    source_type: Annotated[str | None, Query(max_length=80)] = None,
    blocked_only: Annotated[bool, Query()] = False,
    sector: Annotated[str | None, Query(max_length=120)] = None,
    region: Annotated[str | None, Query(max_length=120)] = None,
    buyer_type: Annotated[str | None, Query(max_length=80)] = None,
    campaign_bucket: Annotated[str | None, Query(max_length=80)] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    include_blocked: Annotated[bool, Query()] = False,
    contact_scope: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> LeadProspectsListResponse:
    return list_lead_prospects(
        conn,
        q=q,
        classification=classification,
        source_type=source_type,
        blocked_only=blocked_only,
        sector=sector,
        region=region,
        buyer_type=buyer_type,
        campaign_bucket=campaign_bucket,
        min_score=min_score,
        include_blocked=include_blocked,
        contact_scope=contact_scope,
        limit=limit,
    )


@router.get("/prospects/export.csv")
def mirror_export_lead_prospects_csv(
    conn: MirrorDbConn,
    q: Annotated[str | None, Query(max_length=200)] = None,
    classification: Annotated[str | None, Query(max_length=80)] = None,
    source_type: Annotated[str | None, Query(max_length=80)] = None,
    blocked_only: Annotated[bool, Query()] = False,
    sector: Annotated[str | None, Query(max_length=120)] = None,
    region: Annotated[str | None, Query(max_length=120)] = None,
    buyer_type: Annotated[str | None, Query(max_length=80)] = None,
    campaign_bucket: Annotated[str | None, Query(max_length=80)] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    include_blocked: Annotated[bool, Query()] = False,
    contact_scope: Annotated[str | None, Query(max_length=40)] = None,
    commercial_action_bucket: Annotated[str | None, Query(max_length=80)] = None,
    export_queue: Annotated[str, Query(max_length=80)] = "all_visible",
) -> Response:
    if export_queue not in EXPORT_QUEUES:
        raise HTTPException(status_code=422, detail="invalid export_queue")
    try:
        csv_text, filename = export_lead_prospects_csv(
            conn,
            q=q,
            classification=classification,
            source_type=source_type,
            blocked_only=blocked_only,
            sector=sector,
            region=region,
            buyer_type=buyer_type,
            campaign_bucket=campaign_bucket,
            min_score=min_score,
            include_blocked=include_blocked,
            contact_scope=contact_scope,
            commercial_action_bucket=commercial_action_bucket,
            export_queue=export_queue,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/prospects/{prospect_key}", response_model=LeadProspectDetailResponse)
def mirror_get_lead_prospect(
    conn: MirrorDbConn,
    prospect_key: str,
) -> LeadProspectDetailResponse:
    detail = get_lead_prospect(conn, prospect_key=prospect_key)
    if detail.prospect is None and detail.table_available:
        raise HTTPException(status_code=404, detail="prospect not found")
    return detail


@router.get("/summary", response_model=LeadResearchSummaryResponse)
def mirror_lead_research_summary(conn: MirrorDbConn) -> LeadResearchSummaryResponse:
    return get_lead_research_summary(conn)
