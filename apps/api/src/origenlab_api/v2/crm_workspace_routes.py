"""CRM workspace read routes — ``/v2/workspace/*``.

GET only, same operator identity (``viewer`` or above) as ``/v2/cockpit``. The Drive links come
from the archive ledgers named by ``ORIGENLAB_V2_DRIVE_ARCHIVE_LEDGERS``, loaded once at startup;
without it every route still answers from the CRM and says ``drive_configured: false``.

**Not in the dashboard-proxy allowlist.** Local review through the Vite dev proxy only; adding
these paths to ``apps/dashboard-proxy`` is a separate, deliberate decision.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from origenlab_api.v2.audience_freeze import SEND_BLOCKERS, FreezeCriteria
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.equipment_taxonomy import load_taxonomy
from origenlab_api.v2.marketing_audience import BASES, AudienceFilter, apply_filter, compose

workspace_router = APIRouter(prefix="/v2/workspace", tags=["workspace"], route_class=ContactRedactingRoute)


def _repo(request: Request) -> CrmWorkspaceRepository:
    repo = getattr(request.app.state, "crm_workspace", None)
    if repo is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="the CRM workspace is not configured")
    return repo


Repo = Annotated[CrmWorkspaceRepository, Depends(_repo)]


@workspace_router.get("/overview")
def get_overview(_: Operator, repo: Repo) -> Any:
    """Entity counts with their provenance: imported, partial, not imported, or no write path."""
    return repo.overview()


@workspace_router.get("/pipeline")
def get_pipeline(_: Operator, repo: Repo) -> Any:
    """One card per opportunity: institution, contact evidence, quotes, revisions, Drive, Gmail."""
    return repo.pipeline()


@workspace_router.get("/providers")
def get_providers(_: Operator, repo: Repo) -> Any:
    return repo.providers()


@workspace_router.get("/marketing")
def get_marketing(_: Operator, repo: Repo, request: Request) -> Any:
    body = repo.marketing()
    # Whether this API records drafts at all: the dashboard must never imply a save it cannot do.
    body["authoring"] = {
        "drafts_enabled": bool(getattr(request.app.state, "campaign_drafts_enabled", False)),
        "freeze_enabled": bool(getattr(request.app.state, "audience_freeze_enabled", False)),
        "recontact_review_enabled": bool(getattr(request.app.state, "recontact_review_enabled", False)),
    }
    return body


@workspace_router.get("/marketing/taxonomy")
def get_marketing_taxonomy(_: Operator) -> Any:
    """Families, brands, models and verified product images, from the public website catalogue."""
    return load_taxonomy().public()


@workspace_router.get("/marketing/campaigns/{campaign_id}")
def get_marketing_campaign(campaign_id: UUID, _: Operator, repo: Repo) -> Any:
    row = repo.campaign(str(campaign_id))
    if row is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    return row


@workspace_router.get("/marketing/campaigns/{campaign_id}/freeze-preview")
def get_marketing_freeze_preview(
    campaign_id: UUID,
    _: Operator,
    repo: Repo,
    request: Request,
    family_id: str | None = None,
    brand_id: str | None = None,
    model_id: str | None = None,
    organization_id: UUID | None = None,
    basis: Annotated[list[str] | None, Query()] = None,
    recorded: Literal["crm", "evidence"] | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    scope: Literal["persons", "institutions", "both"] = "both",
) -> Any:
    """The snapshot `freeze-campaign-audience` would write now, and what would stop it.

    A read: nothing is written. `preview_sha256` is what the confirmation screen sends back.
    """
    try:
        criteria = FreezeCriteria(
            family_id=family_id, brand_id=brand_id, model_id=model_id, organization_id=organization_id,
            bases=tuple(basis or ()), recorded=recorded, q=q, scope=scope,
        )
        preview = repo.freeze_preview(
            str(campaign_id), criteria,
            recontact_review=bool(getattr(request.app.state, "recontact_review_enabled", False)),
        )
    except (ValidationError, CommandRefused) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if preview is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    preview.pop("body_text", None)
    preview["freeze_enabled"] = bool(getattr(request.app.state, "audience_freeze_enabled", False))
    return preview


@workspace_router.get("/marketing/campaigns/{campaign_id}/recipients")
def get_marketing_frozen_recipients(campaign_id: UUID, _: Operator, repo: Repo) -> Any:
    """A frozen campaign's recipient snapshot. Empty for a draft."""
    body = repo.frozen_recipients(str(campaign_id))
    if body is None:
        raise HTTPException(status_code=404, detail="no such campaign")
    body["send_blockers"] = list(SEND_BLOCKERS)
    return body


@workspace_router.get("/marketing/audience")
def get_marketing_audience(
    _: Operator,
    repo: Repo,
    family_id: str | None = None,
    brand_id: str | None = None,
    model_id: str | None = None,
    organization_id: UUID | None = None,
    basis: Annotated[list[str] | None, Query()] = None,
    recorded: Literal["crm", "evidence"] | None = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> Any:
    """People and institutions with evidenced equipment interest, and who may be written to.

    Relevance and sending eligibility are separate fields; no score is computed.
    """
    taxonomy = load_taxonomy()
    for value, known, name in (
        (family_id, taxonomy.families, "family_id"),
        (brand_id, taxonomy.brands, "brand_id"),
        (model_id, taxonomy.models, "model_id"),
    ):
        if value is not None and value not in known:
            raise HTTPException(status_code=422, detail=f"unknown {name}")
    bases = tuple(basis or ())
    if any(b not in BASES for b in bases):
        raise HTTPException(status_code=422, detail="unknown basis")
    composed = compose(taxonomy, repo.marketing_audience_inputs())
    flt = AudienceFilter(
        family_id=family_id, brand_id=brand_id, model_id=model_id,
        organization_id=str(organization_id) if organization_id else None,
        bases=bases, recorded=recorded, q=q,
    )
    return {**apply_filter(composed, flt), "coverage": composed["coverage"]}


@workspace_router.get("/drive")
def get_drive(_: Operator, repo: Repo) -> Any:
    return repo.drive_archive()


@workspace_router.get("/review")
def get_review(_: Operator, repo: Repo) -> Any:
    return repo.review()
