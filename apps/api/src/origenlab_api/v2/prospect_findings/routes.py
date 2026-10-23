"""«Prospectos» — the prospect finding routes (spec 2026-10-23 §9).

| Route | Reads |
|---|---|
| `GET /v2/prospect-findings?view=&limit=&offset=` | one view of the classified findings and the counters |
| `GET /v2/prospect-findings/{id}` | one finding's full profile and its verdict history |
| `GET /v2/prospect-findings/{id}/logo` | the organization's logo, stored by the worker (never hot-linked) |
| `POST /v2/commands/review-prospect` | «Más tarde», «Descartar» or «Deshacer» (`reabrir`) |
| `POST /v2/commands/contact-prospect` | «Contactar»: the promotion into the CRM (`commands.py`) |

Any signed-in operator reads; the commands are behind `Deciding` (a `viewer` is 403), an
`Idempotency-Key` and one `CommandTransaction`. A blocked finding is never listed and its page is 404. Not to be
confused with `GET /v2/prospects`, the lead-stage opportunities.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.prospect_findings.commands import (
    CONTACT_PROSPECT,
    REVIEW_PROSPECT,
    ContactProspectBody,
    ReviewProspectBody,
    V2ProspectCommandRepository,
    prepare_contact_prospect,
    prepare_review_prospect,
)
from origenlab_api.v2.prospect_findings.reads import PAGE, ProspectFindingReadRepository

# Contact addresses are masked for an operator without the role (OPERATIONS §2), like every /v2 read.
prospect_read_router = APIRouter(prefix="/v2/prospect-findings", tags=["v2-prospects"], route_class=ContactRedactingRoute)
prospect_command_router = APIRouter(prefix="/v2/commands", tags=["v2-prospects"])

_NOT_FOUND = {"code": "prospect_not_found", "message": "Ese prospecto no existe."}


def get_prospect_read_repository(request: Request) -> ProspectFindingReadRepository:
    repo = getattr(request.app.state, "v2_prospect_read_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 read boundary is not configured")
    return repo


ReadRepo = Annotated[ProspectFindingReadRepository, Depends(get_prospect_read_repository)]


def _finding_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from None


@prospect_read_router.get("")
def list_prospect_findings(
    _: Operator, repo: ReadRepo,
    view: Literal["nuevos", "conocidos", "mas_tarde", "contactados", "descartados"] = "nuevos",
    limit: Annotated[int, Query(ge=1, le=200)] = PAGE, offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    return repo.list(view, limit=limit, offset=offset)


@prospect_read_router.get("/{finding_id}")
def get_prospect_finding(finding_id: str, _: Operator, repo: ReadRepo) -> dict[str, Any]:
    page = repo.one(_finding_id(finding_id))
    if page is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return page


@prospect_read_router.get("/{finding_id}/logo")
def get_prospect_logo(finding_id: str, _: Operator, repo: ReadRepo) -> Response:
    logo = repo.logo(_finding_id(finding_id))
    if logo is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return Response(content=logo[1], media_type=logo[0],
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


def get_prospect_command_repository(request: Request) -> V2ProspectCommandRepository:
    repo = getattr(request.app.state, "v2_prospect_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo


CommandRepo = Annotated[V2ProspectCommandRepository, Depends(get_prospect_command_repository)]


def _run(name: str, prepare: Any, body: Any, operator: Any, repo: V2ProspectCommandRepository,
         idempotency_key: str | None) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = prepare(body)
        return repo.execute(command_name=name, operator=operator, fields=fields, idempotency_key=key,
                            digest=request_digest(name, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@prospect_command_router.post("/review-prospect")
def review_prospect_route(body: ReviewProspectBody, operator: Deciding, repo: CommandRepo,
                          idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _run(REVIEW_PROSPECT, prepare_review_prospect, body, operator, repo, idempotency_key)


@prospect_command_router.post("/contact-prospect")
def contact_prospect_route(body: ContactProspectBody, operator: Deciding, repo: CommandRepo,
                           idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _run(CONTACT_PROSPECT, prepare_contact_prospect, body, operator, repo, idempotency_key)
