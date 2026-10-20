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
| `POST /v2/commands/attach-tender-annexes` | multipart: 1–20 files or a zip of anexos onto a tender (`annexes.py`) |
| `POST /v2/commands/remove-tender-annex` | «Quitar anexo»: one anexo removed, with its reason (`annexes.py`) |
| `POST /v2/commands/prepare-tender-bid` | queue a bid preparation, phase `read` or `fill` (`preparation.py`) |
| `POST /v2/commands/create-tender-offer-quote` | the economic offer as a Cotizador draft from a ready `read` (`preparation.py`) |
| `GET /v2/tenders/{id}/preparations/{prep}/files/{key}/url` | a signed URL for one output file, 10 minutes |
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

from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.datastructures import UploadFile

from origenlab_api.v2.case_document_routes import (
    MAX_UPLOAD_BODY_BYTES,
    document_storage,
    form_invalid,
    parse_form,
    read_capped_body,
    read_document_form,
)
from origenlab_api.v2.case_documents import MAX_DOCUMENT_BYTES, DocumentRefused
from origenlab_api.v2.catalog.storage import SIGNED_URL_SECONDS, StorageUnavailable
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, as_uuid, request_digest, require_idempotency_key
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.tenders.annexes import (
    ATTACH_TENDER_ANNEXES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    REMOVE_TENDER_ANNEX,
    RemoveTenderAnnexBody,
    expand_uploads,
    prepare_objects,
)
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
from origenlab_api.v2.tenders.preparation import (
    CREATE_TENDER_OFFER_QUOTE,
    FILE_KEY,
    PREPARE_TENDER_BID,
    CreateTenderOfferQuoteBody,
    PrepareTenderBidBody,
    output_storage_path,
)
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.review import REVIEW_NOTICE, ReviewNoticeBody, prepare_review_notice
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


@tender_command_router.post("/review-notice")
def review_notice_route(
    body: ReviewNoticeBody,
    operator: Deciding,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """«Nos interesa» / «No es nuestro» on a notice of «En Mercado Público» (sales and admin)."""
    try:
        key = require_idempotency_key(idempotency_key)
        fields = prepare_review_notice(body)
        return repo.execute(command_name=REVIEW_NOTICE, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(REVIEW_NOTICE, body))
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
    operator: Operator,
    repo: ReadRepo,
    status: Annotated[Literal["preparando", "postulada", "adjudicada", "perdida"] | None, Query()] = None,
) -> dict[str, Any]:
    """The tenders, open ones first by closing time, with the count per estado.

    No bidder data is in a row, so any signed-in operator (a `viewer` too) reads it; a `viewer` gets
    the notice's estimate as the amount, never the offer's price.
    """
    return repo.list_tenders(status, offer_prices=operator.role != "viewer")


@tender_read_router.get("/discovered")
def list_discovered_tenders(_: Operator, repo: ReadRepo) -> dict[str, Any]:
    """«En Mercado Público»: open tenders that fit what OrigenLab sells, catalog families first.

    Public notice data only (no bidder data, no prices of ours), so any signed-in operator reads it.
    """
    return repo.discovered(datetime.now(UTC))


@tender_read_router.get("/discovered/{notice_id}")
def get_discovered_tender(notice_id: str, _: Operator, repo: ReadRepo) -> dict[str, Any]:
    """One notice of «En Mercado Público»: every date, every item with what matched the catalog, and
    the head's facts. Public notice data only, so any signed-in operator reads it."""
    try:
        notice_uuid = str(uuid.UUID(notice_id))
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "notice_not_found",
                                                     "message": "No existe esa licitación."}) from None
    page = repo.discovered_one(notice_uuid, datetime.now(UTC))
    if page is None:
        raise HTTPException(status_code=404, detail={"code": "notice_not_found",
                                                     "message": "No existe esa licitación."})
    return page


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


@tender_command_router.post("/remove-tender-annex")
def remove_tender_annex_route(
    body: RemoveTenderAnnexBody,
    operator: Deciding,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """«Quitar anexo»: one anexo of the tender removed, once, with its reason (3 characters or more)."""
    try:
        key = require_idempotency_key(idempotency_key)
        fields = {"opportunity_id": as_uuid(body.opportunity_id, "opportunity_id"),
                  "document_id": as_uuid(body.document_id, "document_id"), "reason": body.reason}
        return repo.execute(command_name=REMOVE_TENDER_ANNEX, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(REMOVE_TENDER_ANNEX, body))
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


class AttachTenderAnnexesForm(BaseModel):
    """The text field of the `attach-tender-annexes` multipart form."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opportunity_id: uuid.UUID


_ANNEXES_OPENAPI = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object",
    "required": ["opportunity_id", "files"],
    "properties": {
        "opportunity_id": {"type": "string", "format": "uuid"},
        "files": {"type": "array", "minItems": 1, "maxItems": MAX_FILES,
                  "items": {"type": "string", "format": "binary"},
                  "description": "PDF, JPEG, PNG, DOCX, XLSX, DOC, XLS or a .zip of them; each at most 20 MiB, "
                                 "all at most 60 MiB"},
    },
}}}}}

ANNEXES_BODY_BYTES = MAX_TOTAL_BYTES + 64 * 1024


async def _read_annexes_form(request: Request) -> tuple[AttachTenderAnnexesForm, list[tuple[str, bytes]]]:
    form = await parse_form(request, await read_capped_body(request, ANNEXES_BODY_BYTES), max_files=MAX_FILES + 1)
    try:
        items = form.multi_items()
        fields = [(k, v) for k, v in items if not isinstance(v, UploadFile)]
        if any(isinstance(v, UploadFile) and k != "files" for k, v in items):
            raise form_invalid([{"type": "extra_forbidden", "loc": ("body", "file"), "msg": "send the files as `files`"}])
        if sum(1 for k, _ in fields if k == "opportunity_id") > 1:
            raise form_invalid([{"type": "duplicate_field", "loc": ("body", "opportunity_id"),
                                 "msg": "field sent more than once"}])
        try:
            parsed = AttachTenderAnnexesForm(**dict(fields))
        except ValidationError as exc:
            raise form_invalid(exc.errors()) from None
        uploads = [v for k, v in items if isinstance(v, UploadFile)]
        if not uploads:
            raise form_invalid([{"type": "missing", "loc": ("body", "files"), "msg": "at least one file is required"}])
        if len(uploads) > MAX_FILES:
            raise form_invalid([{"type": "too_many", "loc": ("body", "files"), "msg": f"at most {MAX_FILES} files"}])
        files = [(upload.filename or "", await upload.read(MAX_DOCUMENT_BYTES + 1)) for upload in uploads]
    finally:
        await form.close()
    return parsed, files


def _store_annexes(request: Request, operator: OperatorIdentity, key: str, opportunity_id: str,
                   uploads: list[tuple[str, bytes]]) -> dict[str, Any]:
    """Judge and unpack the uploads, store the kept ones, then run the command; in the threadpool."""
    repo = get_tender_command_repository(request)
    kind = repo.case_kind(opportunity_id)  # before any object is stored; the command re-checks locked
    if kind is None:
        raise HTTPException(404, detail={"code": "case_not_found", "message": "no such case"})
    if kind != "tender":
        raise HTTPException(422, detail={"code": "not_a_tender_case",
                                         "message": "Los anexos se suben a una licitación."})
    kept, skipped = expand_uploads(uploads)
    try:
        files = prepare_objects(opportunity_id, kept, document_storage(request, "case_document_storage"))
    except StorageUnavailable:
        raise HTTPException(503, detail={"code": "storage_unavailable",
                                         "message": "document storage is unavailable"}) from None
    fields = {"opportunity_id": opportunity_id, "files": files, "skipped": skipped}
    digest = hashlib.sha256(json.dumps({"command": ATTACH_TENDER_ANNEXES, "body": fields}, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    try:
        return repo.execute(
            command_name=ATTACH_TENDER_ANNEXES, operator=operator, fields=fields, idempotency_key=key,
            digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@tender_command_router.post("/attach-tender-annexes", openapi_extra=_ANNEXES_OPENAPI)
async def attach_tender_annexes_route(
    request: Request, operator: Deciding, idempotency_key: IdempotencyKey = None
) -> dict[str, Any]:
    """«Subir anexos»: 1–20 files, or a zip, onto a tender case.

    `Deciding` and the key are checked before the body is read, so a viewer's upload is never
    spooled. The answer is `{attached: [{document_id, filename}], skipped: [{filename, reason}]}`.
    """
    try:
        key = require_idempotency_key(idempotency_key)
    except CommandRefused as exc:
        raise HTTPException(exc.status_code, detail=_detail(exc)) from exc
    document_storage(request, "case_document_storage")
    parsed, uploads = await _read_annexes_form(request)
    return await run_in_threadpool(_store_annexes, request, operator, key, str(parsed.opportunity_id), uploads)


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


@tender_command_router.post("/prepare-tender-bid")
def prepare_tender_bid_route(
    body: PrepareTenderBidBody,
    operator: Deciding,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Queue a bid preparation (`read` of the anexos, or `fill` of the forms and offer).

    The answer carries `preparation_id`; the worker does the rest. Refusals are in `preparation.py`.
    """
    try:
        key = require_idempotency_key(idempotency_key)
        fields = {"opportunity_id": as_uuid(body.opportunity_id, "opportunity_id"), "phase": body.phase}
        return repo.execute(command_name=PREPARE_TENDER_BID, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(PREPARE_TENDER_BID, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@tender_command_router.post("/create-tender-offer-quote")
def create_tender_offer_quote_route(
    body: CreateTenderOfferQuoteBody,
    operator: Deciding,
    repo: V2TenderCommandRepository = Depends(get_tender_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """The economic offer as a Cotizador draft on the case, one line per item of a ready `read`.

    Returns `{quote_id, revision_id, quote_number, matched, free, existing}`; the case's authored
    draft comes back as it is (`existing`). Refusals are in `preparation.py`.
    """
    try:
        key = require_idempotency_key(idempotency_key)
        fields = {"opportunity_id": as_uuid(body.opportunity_id, "opportunity_id"),
                  "preparation_id": as_uuid(body.preparation_id, "preparation_id")}
        return repo.execute(command_name=CREATE_TENDER_OFFER_QUOTE, operator=operator, fields=fields,
                            idempotency_key=key, digest=request_digest(CREATE_TENDER_OFFER_QUOTE, body))
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@tender_read_router.get("/{tender_id}/preparations/{preparation_id}/files/{file_key}/url")
def preparation_file_url(tender_id: str, preparation_id: str, file_key: str, _: BidderOperator, repo: ReadRepo,
                         request: Request, response: Response) -> dict[str, Any]:
    """A signed URL for one output file of a preparation, valid `SIGNED_URL_SECONDS` (10 minutes)."""
    not_found = HTTPException(404, detail={"code": "file_not_found", "message": "No existe ese archivo."})
    if not FILE_KEY.match(file_key):
        raise not_found
    try:
        case_uuid, prep_uuid = str(uuid.UUID(tender_id)), str(uuid.UUID(preparation_id))
    except ValueError:
        raise not_found from None
    storage = document_storage(request, "case_document_storage")
    with repo._read() as cur:
        cur.execute("select outputs from evidence.bid_preparation where id = %s::uuid and opportunity_id = %s::uuid",
                    (prep_uuid, case_uuid))
        row = cur.fetchone()
    if row is None:
        raise not_found
    output = next((o for o in row[0] or [] if isinstance(o, dict) and o.get("file_key") == file_key), None)
    path = output_storage_path(case_uuid, prep_uuid, output) if output else None
    if path is None:
        raise not_found
    try:
        url = storage.signed_url(path, SIGNED_URL_SECONDS)
    except StorageUnavailable:
        raise HTTPException(503, detail={"code": "storage_unavailable",
                                         "message": "document storage is unavailable"}) from None
    response.headers["Cache-Control"] = "no-store"
    return {"url": url, "expires_in": SIGNED_URL_SECONDS, "filename": output.get("filename")}
