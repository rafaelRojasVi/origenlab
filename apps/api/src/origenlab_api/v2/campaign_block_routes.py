"""Campaign safety block commands — `POST /v2/commands/block-campaign`, `/unblock-campaign`.

Mounted only with a V2 database **and** `ORIGENLAB_V2_CAMPAIGN_BLOCKS_ENABLED=true` (default
off): its own switch, because stopping every send is a different permission from writing copy.
The read of the current holds (`GET /v2/workspace/marketing/campaign-blocks`) and their
enforcement in the database do not depend on it.

**Admin only.** An active `admin` from the verified identity (never the body); a `sales` or
`viewer` operator is 403 before anything is read. Both need an `Idempotency-Key`, a non-blank
reason and a compare-and-set token (`expected_block_version` / `expected_version`).
`apps/dashboard-proxy` lists these two exact paths as POST behind the same Origin / JSON /
`Idempotency-Key` guard as the other marketing commands; the role check is here, upstream, and
again in `outbound.campaign_block_guard`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.campaign_blocks import (
    BLOCK_CAMPAIGN,
    UNBLOCK_CAMPAIGN,
    BlockCampaignBody,
    UnblockCampaignBody,
    V2CampaignBlockRepository,
    block_fields,
    unblock_fields,
)
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.identity import OperatorIdentity

campaign_block_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])


def get_campaign_block_repository(request: Request) -> V2CampaignBlockRepository:
    repo = getattr(request.app.state, "campaign_block_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="campaign blocks are not configured")
    return repo


def block_admin(operator: Deciding) -> OperatorIdentity:
    """A deciding operator who is also an admin: sales can see a block, never place or lift one."""
    if operator.role != "admin":
        raise HTTPException(status_code=403, detail={
            "code": "role_may_not_block",
            "message": "only an admin places or lifts a campaign block",
        })
    return operator


BlockAdmin = Annotated[OperatorIdentity, Depends(block_admin)]


@campaign_block_router.post("/block-campaign")
def block_campaign(
    body: BlockCampaignBody,
    operator: BlockAdmin,
    repo: V2CampaignBlockRepository = Depends(get_campaign_block_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Block one campaign, or every campaign. Refuses; never enqueues, sends or rewrites anything."""
    try:
        key = require_idempotency_key(idempotency_key)
        return repo.execute(
            command_name=BLOCK_CAMPAIGN,
            operator=operator,
            fields=block_fields(body),
            idempotency_key=key,
            digest=request_digest(BLOCK_CAMPAIGN, body),
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@campaign_block_router.post("/unblock-campaign")
def unblock_campaign(
    body: UnblockCampaignBody,
    operator: BlockAdmin,
    repo: V2CampaignBlockRepository = Depends(get_campaign_block_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Lift one active block, with its own reason. Starts nothing: a lifted block only stops refusing."""
    try:
        key = require_idempotency_key(idempotency_key)
        return repo.execute(
            command_name=UNBLOCK_CAMPAIGN,
            operator=operator,
            fields=unblock_fields(body),
            idempotency_key=key,
            digest=request_digest(UNBLOCK_CAMPAIGN, body),
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
