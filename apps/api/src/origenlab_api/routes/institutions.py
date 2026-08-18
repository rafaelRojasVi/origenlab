"""Institution-prospect procurement intelligence (read-only read model)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from origenlab_api.schemas.institution_prospects import (
    InstitutionProspectDetailResponse,
    InstitutionProspectsResponse,
    InstitutionProspectStatusResponse,
    OperatorQueueRowsResponse,
    QueueName,
)
from origenlab_api.schemas.tender_annex_preview import (
    TenderAnnexBundleImportResponse,
    TenderAnnexBundlePreviewResponse,
)
from origenlab_api.schemas.tender_attachment_navigation import (
    TenderAttachmentNavigationResponse,
)
from origenlab_api.schemas.tender_terms import TenderDetailResponse
from origenlab_api.services.institution_prospect_service import (
    build_institution_detail_response,
    build_institution_prospect_status_response,
    build_institutions_response,
    build_queue_response,
)
from origenlab_api.services.tender_annex_import_service import (
    build_tender_annex_bundle_import,
)
from origenlab_api.services.tender_annex_preview_service import (
    build_tender_annex_bundle_preview,
)
from origenlab_api.services.tender_attachment_navigation_service import (
    build_tender_attachment_navigation,
)
from origenlab_api.services.tender_terms_service import build_tender_detail_response
from origenlab_api.settings import Settings, get_settings

router = APIRouter(prefix="/operator/procurement", tags=["procurement"])

_MAX_PAGE_SIZE = 500

# ZIPs of tender attachment bundles are typically a few MB; 64 MiB gives
# generous headroom over any real Mercado Público download while staying
# well inside chilecompra_anexo_evidence.constants.ArchiveLimits' own
# 256 MiB total-uncompressed-bytes container bound (a raw compressed upload
# is bounded here before ArchiveLimits ever gets to inspect its contents).
_MAX_ANNEX_BUNDLE_UPLOAD_BYTES = 64 * 1024 * 1024
_ALLOWED_ANNEX_BUNDLE_CONTENT_TYPES = frozenset({"application/zip"})


async def _read_bounded_zip_body(request: Request) -> bytes:
    """Read the request body streamed and capped -- never trusts Content-Length alone.

    A declared Content-Length over the limit is rejected before reading
    anything; the stream itself is also capped as it arrives so a missing or
    understated Content-Length (chunked transfer, a lying client) cannot
    force this into buffering an unbounded body.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid Content-Length header"
            ) from exc
        if declared_bytes > _MAX_ANNEX_BUNDLE_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail="Upload exceeds maximum allowed size"
            )

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_ANNEX_BUNDLE_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail="Upload exceeds maximum allowed size"
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
    q: str | None = Query(
        None, description="Search display_name / normalized_name / institution_id"
    ),
) -> InstitutionProspectsResponse:
    return build_institutions_response(
        settings,
        limit=limit,
        offset=offset,
        institution_id=institution_id,
        search=q,
    )


@router.get(
    "/institutions/{institution_id}", response_model=InstitutionProspectDetailResponse
)
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
        raise HTTPException(
            status_code=404, detail="tender not found in current opportunity queue"
        )
    return result


@router.get(
    "/tenders/{tender_code}/attachment-navigation",
    response_model=TenderAttachmentNavigationResponse,
)
def procurement_tender_attachment_navigation(
    tender_code: str,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> TenderAttachmentNavigationResponse:
    """Resolve an ephemeral Mercado Público navigation destination on demand.

    The response may contain an opaque ``enc`` token because this HTTP response
    exists solely for immediate human navigation. It is never threaded into W1,
    T1, published artifacts, reports, or persistence.

    W1 ``current_opportunity_queue`` remains the sole actionability authority.
    """
    outcome = build_tender_attachment_navigation(settings, tender_code)

    if not outcome.w1_healthy:
        raise HTTPException(
            status_code=503,
            detail="W1 current_opportunity_queue lookup is unavailable; try again shortly",
        )

    if not outcome.found_in_queue:
        raise HTTPException(
            status_code=404,
            detail="tender not found in current opportunity queue",
        )

    assert outcome.response is not None

    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"

    return outcome.response


@router.post(
    "/tenders/{tender_code}/annex-bundle/preview",
    response_model=TenderAnnexBundlePreviewResponse,
)
async def procurement_tender_annex_bundle_preview(
    tender_code: str,
    request: Request,
    declare_complete: bool = Query(
        False,
        description=(
            "Explicit operator assertion that the ZIP is the tender's complete "
            "attachment inventory. Never inferred; default false."
        ),
    ),
    settings: Settings = Depends(get_settings),
) -> TenderAnnexBundlePreviewResponse:
    """Preview-only: validate + extract an operator-uploaded ChileCompra annex ZIP.

    Never publishes, never persists, never authorizes contact/outreach, and
    never writes to disk or a database -- see
    ``services/tender_annex_preview_service.py``. Gated on the same W1
    ``current_opportunity_queue`` authority as ``GET /tenders/{tender_code}``:
    a healthy W1 feed with no matching tender is a real 404; a degraded W1
    feed fails closed with 503 rather than processing the ZIP against an
    unconfirmed tender_code.
    """
    content_type = (
        (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    )
    if content_type not in _ALLOWED_ANNEX_BUNDLE_CONTENT_TYPES:
        raise HTTPException(
            status_code=415, detail="Content-Type must be application/zip"
        )

    zip_bytes = await _read_bounded_zip_body(request)
    if not zip_bytes:
        raise HTTPException(status_code=422, detail="Empty request body")

    outcome = build_tender_annex_bundle_preview(
        settings,
        tender_code,
        zip_bytes,
        declare_complete=declare_complete,
    )
    if not outcome.w1_healthy:
        raise HTTPException(
            status_code=503,
            detail="W1 current_opportunity_queue lookup is unavailable; try again shortly",
        )
    if not outcome.found_in_queue:
        raise HTTPException(
            status_code=404, detail="tender not found in current opportunity queue"
        )
    if outcome.rejected:
        raise HTTPException(status_code=422, detail=outcome.error)
    assert outcome.response is not None
    return outcome.response


@router.post(
    "/tenders/{tender_code}/annex-bundle/import",
    response_model=TenderAnnexBundleImportResponse,
)
async def procurement_tender_annex_bundle_import(
    tender_code: str,
    request: Request,
    response: Response,
    declare_complete: bool = Query(
        False,
        description=(
            "Explicit operator assertion that the ZIP is the tender's complete "
            "attachment inventory. Never inferred; default false."
        ),
    ),
    settings: Settings = Depends(get_settings),
) -> TenderAnnexBundleImportResponse:
    """Explicitly save a validated operator-uploaded annex ZIP as T1 evidence.

    This is the only mutation in the procurement tender document workflow.
    W1 remains the sole actionability authority. Raw ZIP bytes are processed
    in memory and are not persisted; only validated structured T1/provenance
    is atomically saved to the operator-import overlay.
    """
    content_type = (
        (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    )
    if content_type not in _ALLOWED_ANNEX_BUNDLE_CONTENT_TYPES:
        raise HTTPException(
            status_code=415, detail="Content-Type must be application/zip"
        )

    zip_bytes = await _read_bounded_zip_body(request)
    if not zip_bytes:
        raise HTTPException(status_code=422, detail="Empty request body")

    outcome = build_tender_annex_bundle_import(
        settings,
        tender_code,
        zip_bytes,
        declare_complete=declare_complete,
    )
    if not outcome.w1_healthy:
        raise HTTPException(
            status_code=503,
            detail="W1 current_opportunity_queue lookup is unavailable; try again shortly",
        )
    if not outcome.found_in_queue:
        raise HTTPException(
            status_code=404,
            detail="tender not found in current opportunity queue",
        )
    if outcome.rejected:
        raise HTTPException(status_code=422, detail=outcome.error)
    if outcome.persistence_failed:
        raise HTTPException(
            status_code=500,
            detail="Validated annex import could not be persisted",
        )

    assert outcome.response is not None
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return outcome.response


@router.get("/queues/{queue_name}", response_model=OperatorQueueRowsResponse)
def procurement_queue(
    queue_name: QueueName,
    settings: Settings = Depends(get_settings),
    limit: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    institution_id: str | None = Query(None, description="Exact institution_id filter"),
    tender_code: str | None = Query(None, description="Exact tender_code filter"),
    equipment_category: str | None = Query(
        None, description="Exact equipment_category filter"
    ),
    commercial_signal_type: str | None = Query(
        None, description="Exact commercial_signal_type filter"
    ),
    q: str | None = Query(
        None, description="Search display_name / institution_id / tender_code"
    ),
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
