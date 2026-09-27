"""Campaign draft commands — `POST /v2/commands/{create,save}-campaign-draft`.

Mounted only with a V2 database **and** `ORIGENLAB_V2_CAMPAIGN_DRAFTS_ENABLED=true`: a separate
switch from `ORIGENLAB_V2_COMMANDS_ENABLED`, because letting an operator write email copy and
letting them record commercial decisions are different permissions to grant a deployment.

Same requirements as every command: an active `sales` or `admin` operator (from the verified
identity, never the body) and an `Idempotency-Key`. `apps/dashboard-proxy` allows no POST under
`/v2`, so behind the production Worker these paths are unreachable; locally the Vite dev proxy
reaches them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.campaign_drafts import (
    CREATE_CAMPAIGN_DRAFT,
    SAVE_CAMPAIGN_DRAFT,
    CreateCampaignDraftBody,
    SaveCampaignDraftBody,
    V2CampaignDraftRepository,
    validated_draft,
)
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key

campaign_draft_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])


def get_campaign_draft_repository(request: Request) -> V2CampaignDraftRepository:
    repo = getattr(request.app.state, "campaign_draft_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="campaign drafts are not configured")
    return repo


def _run(command_name: str, body: Any, repo: V2CampaignDraftRepository, operator: Any, key: str | None) -> dict[str, Any]:
    try:
        idempotency_key = require_idempotency_key(key)
        fields = validated_draft(command_name, body)
        return repo.execute(
            command_name=command_name,
            operator=operator,
            fields=fields,
            idempotency_key=idempotency_key,
            digest=request_digest(command_name, body),
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@campaign_draft_router.post("/create-campaign-draft")
def create_campaign_draft(
    body: CreateCampaignDraftBody,
    operator: Deciding,
    repo: V2CampaignDraftRepository = Depends(get_campaign_draft_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """A new campaign in `draft`, with whatever content the operator has written so far."""
    return _run(CREATE_CAMPAIGN_DRAFT, body, repo, operator, idempotency_key)


@campaign_draft_router.post("/save-campaign-draft")
def save_campaign_draft(
    body: SaveCampaignDraftBody,
    operator: Deciding,
    repo: V2CampaignDraftRepository = Depends(get_campaign_draft_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Rewrite a draft's name, content and limits, if it is still the version the operator saw."""
    return _run(SAVE_CAMPAIGN_DRAFT, body, repo, operator, idempotency_key)
