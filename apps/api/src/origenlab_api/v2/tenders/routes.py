"""«Licitaciones» — the tender routes (spec §6.10).

| Route | Records / reads |
|---|---|
| `POST /v2/commands/open-tender-case` | a tender case from a Mercado Público code or link |
| `POST /v2/commands/set-tender-status` | a tender's estado (`adjudicada` is refused until phase 3) |
| `GET /v2/tenders?status=` | the list and the counts per estado; any signed-in operator (no bidder data) |
| `GET /v2/tenders/{id}` | one tender's page; a `viewer` is 403 `role_may_not_view_bidder` |
| `POST /v2/commands/save-bidder-profile` | a new bidder profile row (append-only) |
| `POST /v2/commands/add-bidder-document` | multipart: one standard document, stored then recorded |
| `POST /v2/commands/retire-bidder-document` | one document retired, with its reason |
| `GET /v2/bidder` | the profile, its history and the live documents |
| `GET /v2/bidder/documents/{id}/url` | a signed URL valid 10 minutes |

Every bidder route refuses a `viewer` with 403 `role_may_not_view_bidder` (spec §6.3): row security
lets the API role read the bidder tables, so this check is the gate.

`tender_command_router` holds the tender commands, each behind `Deciding` (an active `sales` or
`admin` operator — a `viewer` is 403), an `Idempotency-Key` and one `CommandTransaction`.
`tender_read_router` (prefix `/v2/tenders`) holds the reads.
Both mount behind `ORIGENLAB_V2_COMMANDS_ENABLED`; the proxy lists the commands in
`TENDER_COMMAND_POST_PATHS`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from origenlab_api.v2.case_document_routes import (
    document_storage,
    read_document_form,
)
from origenlab_api.v2.case_documents import DocumentRefused
from origenlab_api.v2.catalog.storage import SIGNED_URL_SECONDS, StorageUnavailable
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, as_uuid, request_digest, require_idempotency_key
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.tenders.bidder import (
    ADD_BIDDER_DOCUMENT,
    RETIRE_BIDDER_DOCUMENT,
    SAVE_BIDDER_PROFILE,
    AddBidderDocumentForm,
    BidderProfileBody,
    RetireBidderDocumentBody,
    bidder_document_path,
    bidder_view,
    check_bidder_document,
    prepare_save_bidder_profile,
)
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
bidder_read_router = APIRouter(prefix="/v2/bidder", tags=["v2-tenders"])

_NOT_FOR_VIEWER = {
    "code": "role_may_not_view_bidder",
    "message": "Esta página muestra datos del oferente: solo la ven ventas y administración.",
}


def bidder_operator(operator: Operator) -> OperatorIdentity:
    """An active operator who may see the bidder: `sales` or `admin`, never `viewer` (spec §6.3)."""
    if operator.role == "viewer":
        raise HTTPException(status_code=403, detail=_NOT_FOR_VIEWER)
    return operator


BidderOperator = Annotated[OperatorIdentity, Depends(bidder_operator)]


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
        raise HTTPException(status_code=403, detail=_NOT_FOR_VIEWER)
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


# ------------------------------------------------------------------ the bidder


@tender_command_router.post("/save-bidder-profile")
def save_bidder_profile_route(
    body: BidderProfileBody,
    operator: BidderOperator,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Save the bidder's data as a new profile row; `expected_current_id` is the profile the
    operator was shown (none for the first save), else 409 `bidder_profile_changed`."""
    try:
        key = require_idempotency_key(idempotency_key)
        if body.expected_current_id is not None:
            body.expected_current_id = as_uuid(body.expected_current_id, "expected_current_id")
        fields = prepare_save_bidder_profile(body)
        return repo.execute(command_name=SAVE_BIDDER_PROFILE, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(SAVE_BIDDER_PROFILE, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@tender_command_router.post("/retire-bidder-document")
def retire_bidder_document_route(
    body: RetireBidderDocumentBody,
    operator: BidderOperator,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = {"document_id": as_uuid(body.document_id, "document_id"), "reason": body.reason}
        return repo.execute(command_name=RETIRE_BIDDER_DOCUMENT, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(RETIRE_BIDDER_DOCUMENT, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


_ADD_DOCUMENT_OPENAPI = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object",
    "required": ["kind", "title", "file"],
    "properties": {
        "kind": {"type": "string", "enum": ["cedula", "certificado_inscripcion", "declaracion_jurada",
                                            "experiencia", "otro"]},
        "title": {"type": "string", "maxLength": 200},
        "file": {"type": "string", "format": "binary",
                 "description": "PDF, JPEG, PNG, DOCX or XLSX, at most 20 MiB"},
    },
}}}}}


def _store_bidder_document(request: Request, data: bytes) -> dict[str, Any]:
    """Check the bytes, hash them and put the object; in the threadpool, off the event loop."""
    try:
        content_type = check_bidder_document(data)
    except DocumentRefused as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from None
    sha256 = hashlib.sha256(data).hexdigest()
    path = bidder_document_path(sha256, content_type)
    try:
        document_storage(request, "case_document_storage").put_if_absent(path, data, content_type)
    except StorageUnavailable:
        raise HTTPException(503, detail={"code": "storage_unavailable",
                                         "message": "document storage is unavailable"}) from None
    return {"storage_path": path, "sha256": sha256, "content_type": content_type, "byte_size": len(data)}


def _execute_add_document(request: Request, operator: OperatorIdentity, key: str, fields: dict[str, Any]) -> dict[str, Any]:
    digest_fields = {k: fields[k] for k in ("kind", "title", "sha256", "original_filename")}
    digest = hashlib.sha256(json.dumps({"command": ADD_BIDDER_DOCUMENT, "body": digest_fields}, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    try:
        return get_tender_command_repository(request).execute(
            command_name=ADD_BIDDER_DOCUMENT, operator=operator, fields=fields, idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@tender_command_router.post("/add-bidder-document", openapi_extra=_ADD_DOCUMENT_OPENAPI)
async def add_bidder_document_route(
    request: Request, operator: BidderOperator, idempotency_key: IdempotencyKey = None
) -> dict[str, Any]:
    """Store one standard document of the bidder, then record it.

    The form is parsed here rather than declared (as `attach-case-document` does) so an
    unauthorized caller's upload is never spooled; the operator and the key are checked first.
    """
    try:
        key = require_idempotency_key(idempotency_key)
    except CommandRefused as exc:
        raise HTTPException(exc.status_code, detail=_detail(exc)) from exc
    document_storage(request, "case_document_storage")
    parsed, data, filename = await read_document_form(request, AddBidderDocumentForm)
    stored = await run_in_threadpool(_store_bidder_document, request, data)
    fields = {"kind": parsed.kind, "title": parsed.title, "original_filename": filename, **stored}
    return await run_in_threadpool(_execute_add_document, request, operator, key, fields)


@bidder_read_router.get("")
def get_bidder(_: BidderOperator, repo: ReadRepo, response: Response) -> dict[str, Any]:
    """The current profile (null before the first save), every save, and the live documents."""
    response.headers["Cache-Control"] = "no-store"
    with repo._read() as cur:
        return bidder_view(cur)


@bidder_read_router.get("/documents/{document_id}/url")
def bidder_document_url(document_id: str, _: BidderOperator, repo: ReadRepo, request: Request,
                        response: Response) -> dict[str, Any]:
    """A signed URL for one live document, valid `SIGNED_URL_SECONDS` (10 minutes)."""
    storage = document_storage(request, "case_document_storage")
    not_found = HTTPException(404, detail={"code": "document_not_found", "message": "No existe ese documento."})
    try:
        document_uuid = str(uuid.UUID(document_id))
    except ValueError:
        raise not_found from None
    with repo._read() as cur:
        cur.execute("select storage_path, original_filename from crm.bidder_document "
                    "where id = %s::uuid and retired_at is null", (document_uuid,))
        row = cur.fetchone()
    if row is None:
        raise not_found
    try:
        url = storage.signed_url(row[0], SIGNED_URL_SECONDS)
    except StorageUnavailable:
        raise HTTPException(503, detail={"code": "storage_unavailable",
                                         "message": "document storage is unavailable"}) from None
    response.headers["Cache-Control"] = "no-store"
    return {"url": url, "expires_in": SIGNED_URL_SECONDS, "filename": row[1]}
