"""«Licitaciones» — the tender routes (spec §6.10).

| Route | Records / reads |
|---|---|
| `POST /v2/commands/open-tender-case` | a tender case from a Mercado Público code or link |
| `POST /v2/commands/set-tender-status` | a tender's estado (`adjudicada` is refused until phase 3) |
| `GET /v2/tenders?status=` | the list and the counts per estado; any signed-in operator (no bidder data) |
| `GET /v2/tenders/{id}` | one tender's page; a `viewer` is 403 `role_may_not_view_bidder` |

`tender_command_router` holds the tender commands, each behind `Deciding` (an active `sales` or
`admin` operator — a `viewer` is 403), an `Idempotency-Key` and one `CommandTransaction`.
`tender_read_router` (prefix `/v2/tenders`) holds the reads.
Both mount behind `ORIGENLAB_V2_COMMANDS_ENABLED`; the proxy lists the commands in
`TENDER_COMMAND_POST_PATHS`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.tenders.mercado_publico import fetch_tender_detail
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.status import (
    SET_TENDER_STATUS,
    SetTenderStatusBody,
    prepare_set_tender_status,
)
from origenlab_api.v2.tenders.open_case import (
    OPEN_TENDER_CASE,
    OpenTenderCaseBody,
    V2TenderCommandRepository,
    prepare_open_tender_case,
)

tender_command_router = APIRouter(prefix="/v2/commands", tags=["v2-tenders"])
tender_read_router = APIRouter(prefix="/v2/tenders", tags=["v2-tenders"])


def get_tender_command_repository(request: Request) -> V2TenderCommandRepository:
    repo = getattr(request.app.state, "v2_tender_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo


def get_tender_fetch(request: Request) -> Any:
    """`fetch_tender_detail`, replaceable in tests through `app.state.tender_fetch`."""
    return getattr(request.app.state, "tender_fetch", None) or fetch_tender_detail


@tender_command_router.post("/open-tender-case")
def open_tender_case(
    body: OpenTenderCaseBody,
    operator: Deciding,
    request: Request,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    fetch: Any = Depends(get_tender_fetch),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Open a tender case at `preparando` from a code or a portal link.

    The detail is read from Mercado Público before the transaction (a sync route: FastAPI runs it
    in its thread pool, so the API's 429 backoff blocks no event loop). Without it — no ticket, or
    a rejected one — the operator's typed `buyer` names the institution; with neither the answer
    is 422 `buyer_required`.
    """
    try:
        key = require_idempotency_key(idempotency_key)
        fields = prepare_open_tender_case(body, ticket=getattr(request.app.state, "chilecompra_ticket", None),
                                          fetch=fetch)
        return repo.execute(command_name=OPEN_TENDER_CASE, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(OPEN_TENDER_CASE, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@tender_command_router.post("/set-tender-status")
def set_tender_status_route(
    body: SetTenderStatusBody,
    operator: Deciding,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Record a tender's estado. `adjudicada` is 422 `award_is_phase_3`; `perdida` needs a `reason`."""
    try:
        key = require_idempotency_key(idempotency_key)
        fields = prepare_set_tender_status(body)
        return repo.execute(command_name=SET_TENDER_STATUS, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(SET_TENDER_STATUS, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


def get_tender_read_repository(request: Request) -> TenderReadRepository:
    repo = getattr(request.app.state, "v2_tender_read_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 tender reads are not configured")
    return repo


ReadRepo = Annotated[TenderReadRepository, Depends(get_tender_read_repository)]


@tender_read_router.get("")
def list_tenders(
    _: Operator,
    repo: ReadRepo,
    status: Annotated[Literal["preparando", "postulada", "adjudicada", "perdida"] | None, Query()] = None,
) -> dict[str, Any]:
    """The tenders, open ones first by closing time, with the count per estado.

    No bidder data is in a row, so any signed-in operator (a `viewer` too) reads it.
    """
    return repo.list_tenders(status)


@tender_read_router.get("/{tender_id}")
def get_tender(tender_id: str, operator: Operator, repo: ReadRepo) -> dict[str, Any]:
    """One tender's page: anexos, readings, offer, spend and whether the bidder's data is ready.

    A `viewer` is refused before anything is read (the page leads to bidder data, spec §6.3).
    """
    if operator.role == "viewer":
        raise HTTPException(status_code=403, detail={
            "code": "role_may_not_view_bidder",
            "message": "Esta página muestra datos del oferente: solo la ven ventas y administración."})
    try:
        tender_uuid = str(uuid.UUID(tender_id))
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "tender_not_found",
                                                     "message": "No existe esa licitación."}) from None
    page = repo.page(tender_uuid, datetime.now(UTC))
    if page is None:
        raise HTTPException(status_code=404, detail={"code": "tender_not_found",
                                                     "message": "No existe esa licitación."})
    return page
