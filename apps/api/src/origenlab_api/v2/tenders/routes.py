"""«Licitaciones» — the tender routes (spec §6.10).

| Route | Records / reads |
|---|---|
| `POST /v2/commands/open-tender-case` | a tender case from a Mercado Público code or link |

`tender_command_router` holds the tender commands, each behind `Deciding` (an active `sales` or
`admin` operator — a `viewer` is 403), an `Idempotency-Key` and one `CommandTransaction`.
`tender_read_router` (prefix `/v2/tenders`) holds the reads; the list and page come with Task 4b.
Both mount behind `ORIGENLAB_V2_COMMANDS_ENABLED`; the proxy lists the commands in
`TENDER_COMMAND_POST_PATHS`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.tenders.mercado_publico import fetch_tender_detail
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
