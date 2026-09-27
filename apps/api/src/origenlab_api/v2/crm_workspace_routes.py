"""CRM workspace read routes — ``/v2/workspace/*``.

GET only, same operator identity (``viewer`` or above) as ``/v2/cockpit``. The Drive links come
from the archive ledgers named by ``ORIGENLAB_V2_DRIVE_ARCHIVE_LEDGERS``, loaded once at startup;
without it every route still answers from the CRM and says ``drive_configured: false``.

**Not in the dashboard-proxy allowlist.** Local review through the Vite dev proxy only; adding
these paths to ``apps/dashboard-proxy`` is a separate, deliberate decision.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

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
def get_marketing(_: Operator, repo: Repo) -> Any:
    return repo.marketing()


@workspace_router.get("/drive")
def get_drive(_: Operator, repo: Repo) -> Any:
    return repo.drive_archive()


@workspace_router.get("/review")
def get_review(_: Operator, repo: Repo) -> Any:
    return repo.review()
