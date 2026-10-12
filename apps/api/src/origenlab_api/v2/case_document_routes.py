"""A case's documents over HTTP: attach the purchase order (upload or from an email), remove one,
and a short-lived link to open it. The rules and SQL live in `case_documents.py`.

| Route | What it does |
|---|---|
| `POST /v2/commands/attach-case-document` | multipart: one PDF/JPEG/PNG from a computer, stored, then recorded |
| `POST /v2/commands/attach-case-document-from-email` | an attachment of a captured Gmail message, copied, then recorded |
| `POST /v2/commands/remove-case-document` | one document removed, with its reason |
| `GET /v2/workspace/opportunities/{id}/documents/{document_id}/url` | a signed URL valid 10 minutes |

Every command needs an active `sales` or `admin` operator, an `Idempotency-Key` and a non-blank
`note`, like every other case command. The upload checks the operator and the key before it reads
a byte of the body, and the body is capped while it streams. Storage unconfigured is a 503
`storage_unavailable` before anything is read or written. Mounted with the case commands, behind
`ORIGENLAB_V2_COMMANDS_ENABLED`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from origenlab_api.v2.case_documents import (
    ATTACH_CASE_DOCUMENT,
    MAX_DOCUMENT_BYTES,
    MAX_EML_BYTES,
    REMOVE_CASE_DOCUMENT,
    DocumentRefused,
    attachment_from_eml,
    check_document,
    clean_filename,
    document_number_from,
    document_path,
    mail_storage_key,
)
from origenlab_api.v2.catalog.storage import SIGNED_URL_SECONDS, StorageUnavailable
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, DecisionBody, as_uuid, require_idempotency_key

case_document_command_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])
case_document_read_router = APIRouter(prefix="/v2/workspace", tags=["v2-workspace"])

_STORAGE_UNAVAILABLE = {"code": "storage_unavailable", "message": "document storage is unavailable"}
#: One document of at most 20 MiB plus its multipart envelope.
MAX_UPLOAD_BODY_BYTES = MAX_DOCUMENT_BYTES + 64 * 1024

DocumentNumber = Annotated[str, Field(min_length=1, max_length=64)]


class _AttachFields(DecisionBody):
    opportunity_id: str
    kind: Literal["purchase_order"] = "purchase_order"
    quote_revision_id: str | None = None
    document_number: DocumentNumber | None = None

    @field_validator("quote_revision_id", "document_number", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


class AttachCaseDocumentForm(_AttachFields):
    """The text fields of the `attach-case-document` multipart form."""


class AttachCaseDocumentFromEmailBody(_AttachFields):
    """An attachment of a captured Gmail message: the message's evidence record and the SHA-256
    the capture recorded for the attachment."""

    source_record_id: str
    document_sha256: Annotated[str, Field(min_length=64, max_length=64)]


class RemoveCaseDocumentBody(DecisionBody):
    """Remove one document; `note` is the reason."""

    document_id: str


def _storage(request: Request, name: str) -> Any:
    storage = getattr(request.app.state, name, None)
    if storage is None:
        raise HTTPException(503, detail=_STORAGE_UNAVAILABLE)
    return storage


def _repo(request: Request) -> Any:
    repo = getattr(request.app.state, "v2_case_command_repository", None)
    if repo is None:  # pragma: no cover - mounted only with the case commands
        raise HTTPException(503, detail="the V2 command boundary is not configured")
    return repo


def _digest(command_name: str, fields: dict[str, Any]) -> str:
    payload = {"command": command_name, "body": fields}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _execute(request: Request, operator: Any, key: str, command_name: str, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        return _repo(request).execute(
            command_name=command_name, operator=operator, fields=fields,
            idempotency_key=key, digest=_digest(command_name, fields),
        )
    except CommandRefused as exc:
        raise HTTPException(exc.status_code, detail=_detail(exc)) from exc


def _refused(exc: CommandRefused) -> HTTPException:
    return HTTPException(exc.status_code, detail=_detail(exc))


def _common_fields(body: _AttachFields) -> dict[str, Any]:
    try:
        return {
            "opportunity_id": as_uuid(body.opportunity_id, "opportunity_id"),
            "kind": body.kind,
            "quote_revision_id": as_uuid(body.quote_revision_id, "quote_revision_id") if body.quote_revision_id else None,
            "document_number": body.document_number.strip() if body.document_number else None,
            "note": body.note.strip(),
        }
    except CommandRefused as exc:
        raise _refused(exc) from None


def _store(request: Request, opportunity_id: str, data: bytes) -> dict[str, Any]:
    """Check the bytes, hash them and put the object; in the threadpool, off the event loop."""
    try:
        content_type = check_document(data)
    except DocumentRefused as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message}) from None
    sha256 = hashlib.sha256(data).hexdigest()
    path = document_path(opportunity_id, sha256, content_type)
    try:
        _storage(request, "case_document_storage").put_if_absent(path, data, content_type)
    except StorageUnavailable:
        raise HTTPException(503, detail=_STORAGE_UNAVAILABLE) from None
    return {"storage_path": path, "sha256": sha256, "content_type": content_type, "byte_size": len(data)}


# ──────────────────────────────────────────────────────────── upload ──

def _too_large() -> HTTPException:
    return HTTPException(413, detail={"code": "document_too_large", "message": "the document is larger than 20 MiB"})


async def _read_capped_body(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise _too_large()
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_content_length",
                                             "message": "the Content-Length header is not a number"}) from None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


async def _parse_form(request: Request, body: bytes) -> FormData:
    async def replay() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        return await Request(request.scope, replay).form(max_files=1, max_fields=8)
    except (ValueError, StarletteHTTPException):
        raise HTTPException(400, detail={"code": "invalid_multipart",
                                         "message": "the body is not a valid multipart form with one document"}) from None


def _form_invalid(errors: list[dict[str, Any]]) -> RequestValidationError:
    return RequestValidationError([{k: e[k] for k in ("type", "loc", "msg") if k in e} for e in errors])


_ATTACH_OPENAPI = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object",
    "required": ["opportunity_id", "note", "file"],
    "properties": {
        "opportunity_id": {"type": "string", "format": "uuid"},
        "quote_revision_id": {"type": "string", "format": "uuid"},
        "document_number": {"type": "string", "maxLength": 64},
        "kind": {"type": "string", "enum": ["purchase_order"], "default": "purchase_order"},
        "note": {"type": "string", "maxLength": 2000},
        "file": {"type": "string", "format": "binary", "description": "PDF, JPEG or PNG, at most 20 MiB"},
    },
}}}}}


@case_document_command_router.post("/attach-case-document", openapi_extra=_ATTACH_OPENAPI)
async def attach_case_document(request: Request, operator: Deciding,
                               idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Store one document from the operator's computer, then record it on the case.

    The form is parsed here rather than declared, because FastAPI reads a declared form body
    before it resolves any dependency: an unauthorized caller's upload would be spooled whole.
    """
    try:
        key = require_idempotency_key(idempotency_key)
    except CommandRefused as exc:
        raise _refused(exc) from exc
    _storage(request, "case_document_storage")
    form = await _parse_form(request, await _read_capped_body(request, MAX_UPLOAD_BODY_BYTES))
    try:
        items = form.multi_items()
        repeated = sorted(k for k, n in Counter(k for k, _ in items).items() if n > 1)
        if repeated:
            raise _form_invalid([{"type": "duplicate_field", "loc": ("body", name), "msg": "field sent more than once"}
                                 for name in repeated])
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise _form_invalid([{"type": "missing", "loc": ("body", "file"), "msg": "a document file is required"}])
        try:
            parsed = AttachCaseDocumentForm(**{k: v for k, v in items if k != "file"})
        except ValidationError as exc:
            raise _form_invalid(exc.errors()) from None
        data = await upload.read(MAX_DOCUMENT_BYTES + 1)
        filename = clean_filename(upload.filename)
    finally:
        await form.close()
    fields = _common_fields(parsed)
    stored = await run_in_threadpool(_store, request, fields["opportunity_id"], data)
    fields.update(stored, original_filename=filename, source="upload", source_record_id=None)
    if fields["document_number"] is None:
        fields["document_number"] = document_number_from(filename)
    return await run_in_threadpool(_execute, request, operator, key, ATTACH_CASE_DOCUMENT, fields)


# ──────────────────────────────────────────────────────────── from an email ──

_MESSAGE_DOCUMENT = """
select m.eml_storage_path, d ->> 'filename' as filename, sr.payload ->> 'subject_raw' as subject
  from evidence.source_record sr
  join comms.message m on 'gmail_message:' || m.provider_message_id = sr.dedupe_key
 cross join lateral jsonb_array_elements(
   case when jsonb_typeof(sr.payload -> 'documents') = 'array' then sr.payload -> 'documents' else '[]'::jsonb end) d
 where sr.id = %s and sr.kind = 'gmail_message' and lower(d ->> 'sha256') = %s
 limit 1
"""


class CopyRefused(Exception):
    """An email attachment could not be copied; `status`, `code` and `message` are the answer."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def copy_email_attachment(repo: Any, documents: Any, mail: Any, opportunity_id: str,
                          source_record_id: str, sha256: str) -> dict[str, Any]:
    """Find the attachment in its message's stored `.eml`, by the SHA-256 the capture recorded, and
    put its bytes in `crm-documents`. Returns the stored fields, the file name and a number hint.
    The route and the backfill tool (`tools/attach_case_documents.py`) both call this."""
    with repo._write() as cur:  # a read: the command that records the document is its own transaction
        cur.execute(_MESSAGE_DOCUMENT, (source_record_id, sha256))
        row = repo._row(cur)
    if row is None:
        raise CopyRefused(404, "document_not_in_message", "that message does not carry this document")
    not_stored = CopyRefused(409, "message_not_stored", "this email's original is not stored; upload the file instead")
    if not row.get("eml_storage_path"):
        raise not_stored
    try:
        raw = mail.get(mail_storage_key(row["eml_storage_path"]), MAX_EML_BYTES)
    except (KeyError, ValueError):
        raise not_stored from None
    except StorageUnavailable:
        raise CopyRefused(503, "storage_unavailable", "document storage is unavailable") from None
    found = attachment_from_eml(raw, sha256)
    if found is None:
        raise CopyRefused(409, "attachment_not_in_eml",
                          "the stored email does not hold this attachment; upload the file instead")
    try:
        content_type = check_document(found.data)
    except DocumentRefused as exc:
        raise CopyRefused(exc.status_code, exc.code, exc.message) from None
    digest = hashlib.sha256(found.data).hexdigest()
    path = document_path(opportunity_id, digest, content_type)
    try:
        documents.put_if_absent(path, found.data, content_type)
    except StorageUnavailable:
        raise CopyRefused(503, "storage_unavailable", "document storage is unavailable") from None
    filename = clean_filename(row.get("filename") or found.filename)
    return {"storage_path": path, "sha256": digest, "content_type": content_type, "byte_size": len(found.data),
            "original_filename": filename, "number_hint": document_number_from(filename, row.get("subject"))}


def _copy_from_email(request: Request, opportunity_id: str, source_record_id: str, sha256: str) -> dict[str, Any]:
    try:
        return copy_email_attachment(_repo(request), _storage(request, "case_document_storage"),
                                     _storage(request, "mail_storage"), opportunity_id, source_record_id, sha256)
    except CopyRefused as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message}) from None


@case_document_command_router.post("/attach-case-document-from-email")
def attach_case_document_from_email(body: AttachCaseDocumentFromEmailBody, request: Request, operator: Deciding,
                                    idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Copy one attachment of a captured Gmail message into the case's documents."""
    try:
        key = require_idempotency_key(idempotency_key)
        sha256 = body.document_sha256.strip().lower()
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise CommandRefused(422, "invalid_document_sha256", "document_sha256 must be a SHA-256 hex digest")
        source_record_id = as_uuid(body.source_record_id, "source_record_id")
    except CommandRefused as exc:
        raise _refused(exc) from exc
    _storage(request, "case_document_storage")
    _storage(request, "mail_storage")
    fields = _common_fields(body)
    copied = _copy_from_email(request, fields["opportunity_id"], source_record_id, sha256)
    hint = copied.pop("number_hint")
    fields.update(copied, source="email", source_record_id=source_record_id)
    if fields["document_number"] is None:
        fields["document_number"] = hint
    return _execute(request, operator, key, ATTACH_CASE_DOCUMENT, fields)


@case_document_command_router.post("/remove-case-document")
def remove_case_document(body: RemoveCaseDocumentBody, request: Request, operator: Deciding,
                         idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = {"document_id": as_uuid(body.document_id, "document_id"), "note": body.note.strip()}
    except CommandRefused as exc:
        raise _refused(exc) from exc
    return _execute(request, operator, key, REMOVE_CASE_DOCUMENT, fields)


# ──────────────────────────────────────────────────────────── the link ──

@case_document_read_router.get("/opportunities/{opportunity_id}/documents/{document_id}/url")
def case_document_url(opportunity_id: UUID, document_id: UUID, request: Request, response: Response,
                      operator: Operator) -> dict[str, Any]:
    """A signed URL for one live document of this case, valid `SIGNED_URL_SECONDS` (10 minutes).

    Every active role may open a case's documents, as they may read the case. A removed document,
    or one of another case, is the same 404 as one that does not exist.
    """
    storage = _storage(request, "case_document_storage")
    repo = _repo(request)
    with repo._write() as cur:
        cur.execute(
            """select storage_path, original_filename from crm.opportunity_document
                where id = %s and opportunity_id = %s and removed_at is null""",
            (str(document_id), str(opportunity_id)),
        )
        row = repo._row(cur)
    if row is None:
        raise HTTPException(404, detail={"code": "document_not_found", "message": "no such document"})
    try:
        url = storage.signed_url(row["storage_path"], SIGNED_URL_SECONDS)
    except StorageUnavailable:
        raise HTTPException(503, detail=_STORAGE_UNAVAILABLE) from None
    response.headers["Cache-Control"] = "no-store"
    return {"url": url, "expires_in": SIGNED_URL_SECONDS, "filename": row["original_filename"]}
