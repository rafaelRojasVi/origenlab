"""Institution-prospect service layer (repository -> response schema)."""

from __future__ import annotations

from origenlab_api.path_redaction import enrich_institution_prospect_meta_paths
from origenlab_api.repositories.institution_prospects import (
    DEFAULT_STALE_AFTER_HOURS,
    get_institution_detail,
    get_institution_prospect_status,
    list_institutions,
    list_queue_rows,
)
from origenlab_api.schemas.institution_prospects import (
    InstitutionProfileItem,
    InstitutionProspectDetailResponse,
    InstitutionProspectMeta,
    InstitutionProspectsResponse,
    InstitutionProspectStatusResponse,
    OperatorQueueRowsResponse,
)
from origenlab_api.settings import Settings


def _redacted_meta(meta: InstitutionProspectMeta) -> InstitutionProspectMeta:
    return InstitutionProspectMeta.model_validate(
        enrich_institution_prospect_meta_paths(meta.model_dump())
    )


def build_institution_prospect_status_response(
    settings: Settings,
    *,
    max_stale_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> InstitutionProspectStatusResponse:
    dest_dir = settings.resolved_institution_prospect_dir()
    result = get_institution_prospect_status(dest_dir, max_stale_hours=max_stale_hours)
    return InstitutionProspectStatusResponse(
        meta=_redacted_meta(result["meta"]),
        counts=result["counts"],
        operator_queue_sizes=result["operator_queue_sizes"],
        summary_ok=result["summary_ok"],
    )


def build_institutions_response(
    settings: Settings,
    *,
    limit: int = 50,
    offset: int = 0,
    institution_id: str | None = None,
    search: str | None = None,
) -> InstitutionProspectsResponse:
    dest_dir = settings.resolved_institution_prospect_dir()
    rows, meta, total = list_institutions(
        dest_dir,
        limit=limit,
        offset=offset,
        institution_id=institution_id,
        search=search,
    )
    return InstitutionProspectsResponse(
        meta=_redacted_meta(meta),
        limit=limit,
        offset=offset,
        total=total,
        count=len(rows),
        items=[InstitutionProfileItem.model_validate(r) for r in rows],
    )


def build_institution_detail_response(
    settings: Settings,
    institution_id: str,
) -> InstitutionProspectDetailResponse:
    dest_dir = settings.resolved_institution_prospect_dir()
    profile, meta = get_institution_detail(dest_dir, institution_id)
    return InstitutionProspectDetailResponse(
        meta=_redacted_meta(meta),
        item=InstitutionProfileItem.model_validate(profile) if profile is not None else None,
    )


def build_queue_response(
    settings: Settings,
    queue_route_name: str,
    *,
    limit: int = 50,
    offset: int = 0,
    institution_id: str | None = None,
    tender_code: str | None = None,
    equipment_category: str | None = None,
    commercial_signal_type: str | None = None,
    search: str | None = None,
) -> OperatorQueueRowsResponse:
    dest_dir = settings.resolved_institution_prospect_dir()
    rows, meta, total = list_queue_rows(
        dest_dir,
        queue_route_name,
        limit=limit,
        offset=offset,
        institution_id=institution_id,
        tender_code=tender_code,
        equipment_category=equipment_category,
        commercial_signal_type=commercial_signal_type,
        search=search,
    )
    return OperatorQueueRowsResponse(
        meta=_redacted_meta(meta),
        queue=queue_route_name,
        limit=limit,
        offset=offset,
        total=total,
        count=len(rows),
        items=rows,
    )


__all__ = [
    "build_institution_detail_response",
    "build_institution_prospect_status_response",
    "build_institutions_response",
    "build_queue_response",
]
