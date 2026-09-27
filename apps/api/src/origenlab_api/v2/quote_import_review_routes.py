"""Quotation import review — ``GET /v2/cockpit/import-review`` and its revision PDFs.

Mounted only when ``ORIGENLAB_V2_IMPORT_REVIEW_PLAN_DIR`` names a dry-run directory whose
``intent.json`` matches its ``intent.sha256``; the plan is loaded once, at startup, so a changed
plan fails the process instead of silently changing what the page compares against. GET only,
same operator identity as the rest of ``/v2/cockpit``.

Not in the dashboard-proxy allowlist: it exists for local review of a disposable rehearsal
database, reached through the Vite dev proxy.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.contact_redaction import ContactRedactingRoute, sees_contact_addresses
from origenlab_api.v2.quote_import_review import (
    LoadedPlan,
    QuoteImportReviewRepository,
    build_review,
    revision_document_path,
)

import_review_router = APIRouter(prefix="/v2/cockpit/import-review", tags=["cockpit"], route_class=ContactRedactingRoute)


def _state(request: Request) -> tuple[LoadedPlan, QuoteImportReviewRepository, str | None]:
    state = getattr(request.app.state, "import_review", None)
    if state is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="import review is not configured")
    return state


@import_review_router.get("")
def get_import_review(_: Operator, request: Request) -> Any:
    plan, repo, _root = _state(request)
    return build_review(plan, repo.snapshot(plan))


@import_review_router.get("/documents/{sha256}")
def get_revision_document(sha256: str, operator: Operator, request: Request) -> FileResponse:
    # A quotation PDF prints its addressee and contact details, and a file cannot be masked
    # the way a JSON answer is: an operator who may not see contact addresses gets 403.
    if not sees_contact_addresses(operator.role):
        raise HTTPException(status_code=403, detail="operator role may not open quotation documents")
    plan, repo, root = _state(request)
    if root is None:
        raise HTTPException(status_code=404, detail="no document root configured")
    review = build_review(plan, repo.snapshot(plan))
    path = revision_document_path(review, plan, root, sha256.lower())
    if path is None:
        raise HTTPException(status_code=404, detail="no such imported revision document")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{sha256.lower()}.pdf"'},
    )
