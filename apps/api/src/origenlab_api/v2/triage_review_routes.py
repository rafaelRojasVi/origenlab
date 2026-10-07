"""Mail triage review routes (`triage_review.py`).

| Route | Who | What |
|---|---|---|
| `GET /v2/workspace/triage-readings?status=pending\\|reviewed\\|all&limit=N` | any operator | the suggestions, with their email, products, cases and latest verdict |
| `POST /v2/commands/review-triage` | `sales` or `admin` | one verdict; `Idempotency-Key` required |

The read mounts with the other V2 reads; the command only where `ORIGENLAB_V2_COMMANDS_ENABLED`
mounts the commands. The read uses the contact-redacting route class every V2 GET uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.triage_review import REVIEW_TRIAGE, ReviewTriageBody, TriageReviewRepository, validated_review

triage_review_read_router = APIRouter(prefix="/v2/workspace", tags=["v2-triage"], route_class=ContactRedactingRoute)
triage_review_command_router = APIRouter(prefix="/v2/commands", tags=["v2-triage"])


def get_triage_review_repository(request: Request) -> TriageReviewRepository:
    repo = getattr(request.app.state, "triage_review_repository", None)
    if repo is None:  # pragma: no cover - the routers are not mounted without one
        raise HTTPException(status_code=503, detail="the triage review is not configured")
    return repo


@triage_review_read_router.get("/triage-readings")
def triage_readings(
    operator: Operator,
    status: str = "pending",
    limit: int = 100,
    repo: TriageReviewRepository = Depends(get_triage_review_repository),
) -> dict[str, Any]:
    """The triage's suggestions for the mail a person wrote, newest first."""
    try:
        return repo.readings(status=status, limit=limit)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@triage_review_command_router.post("/review-triage")
def review_triage(
    body: ReviewTriageBody,
    operator: Deciding,
    repo: TriageReviewRepository = Depends(get_triage_review_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Approve, correct or reject one suggestion. Moves nothing: the case's stage is a separate command."""
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated_review(body)
        digest = request_digest(REVIEW_TRIAGE, body)
        return repo.execute(command_name=REVIEW_TRIAGE, operator=operator, fields=fields,
                            idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
