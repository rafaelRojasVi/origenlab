"""«Enviar prueba» routes — mounted only behind `ORIGENLAB_V2_CAMPAIGN_TEST_SEND_ENABLED` with a valid
Gmail send token. The only routes in V2 that send email, and only one test to one address."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from origenlab_api.v2.campaign_test_send import (
    SEND_CAMPAIGN_TEST,
    SendCampaignTestBody,
    TestSendLimitRefused,
    V2CampaignTestSendRepository,
)
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.contact_redaction import ContactRedactingRoute

campaign_test_send_router = APIRouter(tags=["v2-commands"], route_class=ContactRedactingRoute)


def _repo(request: Request) -> V2CampaignTestSendRepository:
    repo = getattr(request.app.state, "campaign_test_send_repository", None)
    if repo is None:  # pragma: no cover - not mounted without one
        raise HTTPException(status_code=503, detail="test sends are not configured")
    return repo


@campaign_test_send_router.post("/v2/commands/send-campaign-test")
def send_campaign_test(
    body: SendCampaignTestBody,
    operator: Deciding,
    repo: V2CampaignTestSendRepository = Depends(_repo),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Send the campaign's stored email to one address, from contacto@, subject `[PRUEBA] …`."""
    try:
        key = require_idempotency_key(idempotency_key)
        return repo.send_test(
            operator=operator, body=body, idempotency_key=key,
            digest=request_digest(SEND_CAMPAIGN_TEST, body),
        )
    except TestSendLimitRefused as exc:
        detail = {**_detail(exc), "next_allowed_at": exc.next_allowed_at}
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@campaign_test_send_router.get("/v2/workspace/marketing/test-send-history")
def test_send_history(
    _: Operator,
    repo: V2CampaignTestSendRepository = Depends(_repo),
    campaign_id: UUID | None = None,
    v1_lane_key: Annotated[str | None, Query(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")] = None,
) -> Any:
    if (campaign_id is None) == (v1_lane_key is None):
        raise HTTPException(status_code=422, detail="name exactly one of campaign_id or v1_lane_key")
    return repo.history(campaign_id=str(campaign_id) if campaign_id else None, v1_lane_key=v1_lane_key)
