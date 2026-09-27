"""Case workspace — ``GET /v2/cockpit/case-archive``.

Mounted only when ``ORIGENLAB_V2_CASE_ARCHIVE_DIR`` names a case migration dry-run directory
whose inputs still hash as recorded; loaded once at startup, so a changed input fails the process
instead of silently changing the page. GET only, same operator identity as ``/v2/cockpit``.
Reads files only — no database, no Drive, no Gmail call. Not in the dashboard-proxy allowlist:
local review through the Vite dev proxy.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.quote_case_workspace import build_case_workspace

case_archive_router = APIRouter(prefix="/v2/cockpit/case-archive", tags=["cockpit"], route_class=ContactRedactingRoute)


@case_archive_router.get("")
def get_case_archive(_: Operator, request: Request) -> Any:
    inputs = getattr(request.app.state, "case_archive", None)
    if inputs is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="case archive is not configured")
    return build_case_workspace(inputs)
