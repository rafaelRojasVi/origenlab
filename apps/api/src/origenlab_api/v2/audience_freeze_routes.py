"""Audience freeze command — `POST /v2/commands/freeze-campaign-audience`.

Mounted only with a V2 database **and** `ORIGENLAB_V2_AUDIENCE_FREEZE_ENABLED=true`, a switch of
its own. Same requirements as every command: an active `sales` or `admin` operator (from the
verified identity, never the body), an `Idempotency-Key`, and here also the final confirmation
(`confirmed: true`) and the `preview_sha256` of the audience the operator was shown.

`apps/dashboard-proxy` allows no POST under `/v2`, so behind the production Worker this path is
unreachable; locally the Vite dev proxy reaches it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.audience_freeze import (
    FREEZE_CAMPAIGN_AUDIENCE,
    FreezeAudienceBody,
    V2AudienceFreezeRepository,
    freeze_fields,
)
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key

audience_freeze_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])


def get_audience_freeze_repository(request: Request) -> V2AudienceFreezeRepository:
    repo = getattr(request.app.state, "audience_freeze_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="audience freeze is not configured")
    return repo


@audience_freeze_router.post("/freeze-campaign-audience")
def freeze_campaign_audience(
    body: FreezeAudienceBody,
    operator: Deciding,
    repo: V2AudienceFreezeRepository = Depends(get_audience_freeze_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Freeze a draft's content and audience into an immutable recipient snapshot. Sends nothing."""
    try:
        return repo.execute(
            command_name=FREEZE_CAMPAIGN_AUDIENCE,
            operator=operator,
            fields=freeze_fields(body),
            idempotency_key=require_idempotency_key(idempotency_key),
            digest=request_digest(FREEZE_CAMPAIGN_AUDIENCE, body),
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
