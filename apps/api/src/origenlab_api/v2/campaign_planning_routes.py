"""Campaign planning command — `POST /v2/commands/set-campaign-planning`.

Mounted only with a V2 database **and** `ORIGENLAB_V2_CAMPAIGN_PLANNING_ENABLED=true`: its own
switch, because noting an intended send day is a different permission from writing copy or
freezing an audience. It schedules nothing either way.

Same requirements as every command: an active `sales` or `admin` operator (from the verified
identity, never the body), an `Idempotency-Key`, and a compare-and-set
`expected_planning_version`. `apps/dashboard-proxy` lists this one exact path as POST and refuses
it before forwarding without an allowed `Origin`, a JSON body within its size limit and a
well-formed `Idempotency-Key`; the role check is here, upstream.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.campaign_planning import (
    SET_CAMPAIGN_PLANNING,
    SetCampaignPlanningBody,
    V2CampaignPlanningRepository,
    validated_planning,
)
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key

campaign_planning_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])


def get_campaign_planning_repository(request: Request) -> V2CampaignPlanningRepository:
    repo = getattr(request.app.state, "campaign_planning_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="campaign planning is not configured")
    return repo


@campaign_planning_router.post("/set-campaign-planning")
def set_campaign_planning(
    body: SetCampaignPlanningBody,
    operator: Deciding,
    repo: V2CampaignPlanningRepository = Depends(get_campaign_planning_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Set, change or clear the internal planned day of an unsent campaign. Sends nothing."""
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated_planning(body)
        return repo.execute(
            command_name=SET_CAMPAIGN_PLANNING,
            operator=operator,
            fields=fields,
            idempotency_key=key,
            digest=request_digest(SET_CAMPAIGN_PLANNING, body),
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
