"""A case's documents: the purchase order (OC) that closes it, stored privately (DOMAIN.md §7 #56).

Owner request 2026-10-12: «en ganada attach the oc too, upload it to the database so it's there
with the cotización». A document is a file a person attaches to a case — today only
`purchase_order` — with the quote revision it closes when known. Its bytes live in the private
Storage bucket `crm-documents` under `opportunities/<case>/<sha256>.<ext>` (ARCHITECTURE §7); the
row in `crm.opportunity_document` says what it is, who attached it, from where and when.

Two sources, one row shape:

* **upload** — a file from the operator's computer (`POST /v2/commands/attach-case-document`,
  multipart). The bytes decide the type (PDF, JPEG or PNG), never the browser's declaration.
* **email** — an attachment of a captured Gmail message (`attach-case-document-from-email`). The
  API reads the message's `.eml` from the worker's `mail` bucket, finds the MIME part whose
  SHA-256 is the one the capture recorded, and copies those bytes into `crm-documents`. The copy
  is what is shown, so a later change to the mailbox changes nothing here.

The route stores the object first and then runs the command, as product images do: a failed
transaction leaves an object that the next attach of the same bytes reuses (the path is the
content). Attaching changes no `crm.opportunity` column, so it takes no case version: «Marcar
ganada» attaches the OC and then records the win against the version it was shown.

Nothing is deleted or rewritten. A wrong document is removed once (`remove-case-document`, with
its reason) and may be attached again.
"""

from __future__ import annotations

import email
import hashlib
import re
from dataclasses import dataclass
from email import policy
from typing import Any
from uuid import UUID

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

ATTACH_CASE_DOCUMENT = "attach_case_document"
REMOVE_CASE_DOCUMENT = "remove_case_document"

BUCKET = "crm-documents"
MAIL_BUCKET = "mail"
#: 20 MiB — the CHECK on byte_size holds the same line; the proxy holds it for the upload path.
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
#: The largest `.eml` read to find an attachment (Gmail's own message limit is 25 MB, base64 inflates).
MAX_EML_BYTES = 40 * 1024 * 1024
EXTENSIONS = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
KINDS = ("purchase_order",)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PO_NUMBER = re.compile(
    r"(?:\bo[.]?\s?c\b|orden\s+de\s+compra|\bord[-_\s]?co\b)\s*[-_#:]?\s*(?:n\s*[°º.]?\s*)?[-_#:]?\s*(\d{2,})",
    re.IGNORECASE,
)


class DocumentRefused(ValueError):
    """The bytes are not a document this boundary keeps."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# ──────────────────────────────────────────────────────────── pure rules ──

def sniff_document_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def check_document(data: bytes) -> str:
    """The content type of an acceptable document, decided by its bytes, or `DocumentRefused`."""
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentRefused(413, "document_too_large", "the document is larger than 20 MiB")
    kind = sniff_document_type(data)
    if kind is None:
        raise DocumentRefused(415, "unsupported_document", "only a PDF, JPEG or PNG document")
    return kind


def document_path(opportunity_id: str, sha256: str, content_type: str) -> str:
    """`opportunities/<case>/<sha256>.<pdf|jpg|png>` — the bytes name their own object."""
    if not _SHA256.match(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    if content_type not in EXTENSIONS:
        raise ValueError("unsupported document content type")
    return f"opportunities/{UUID(str(opportunity_id))}/{sha256}.{EXTENSIONS[content_type]}"


def clean_filename(name: str | None) -> str:
    """The base name a person would recognise; never a path. At most 255 characters."""
    base = re.split(r"[\\/]", (name or "").strip())[-1].strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    if not base or base in (".", ".."):
        return "documento"
    if len(base) > 255:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 250 - len(ext)] + dot + ext) if dot and len(ext) <= 8 else base[:255]
    return base


def document_number_from(*texts: str | None) -> str | None:
    """The purchase-order number a file name or subject states («OC 5500012345», «Orden de compra
    4321», «ORD-CO-17»), or None. A bare number is not read as one: it may be anything."""
    for text in texts:
        found = _PO_NUMBER.search(text or "")
        if found:
            return found.group(1)
    return None


def mail_storage_key(eml_storage_path: str) -> str:
    """`comms.message.eml_storage_path` is `mail/<key>`; the object is `<key>` in bucket `mail`."""
    prefix = f"{MAIL_BUCKET}/"
    if not eml_storage_path.startswith(prefix) or ".." in eml_storage_path:
        raise ValueError("not a path in the mail bucket")
    return eml_storage_path[len(prefix):]


@dataclass(frozen=True)
class Attachment:
    data: bytes
    filename: str | None


def attachment_from_eml(raw_eml: bytes, sha256: str) -> Attachment | None:
    """The decoded MIME part whose SHA-256 is `sha256` — the bytes the capture hashed — or None."""
    msg = email.message_from_bytes(raw_eml, policy=policy.default)
    for part in msg.walk():
        if part.is_multipart():
            continue
        data = part.get_payload(decode=True)
        if isinstance(data, bytes) and hashlib.sha256(data).hexdigest() == sha256:
            return Attachment(data=data, filename=part.get_filename())
    return None


# ──────────────────────────────────────────────────────────── the commands ──

_DOCUMENT_COLUMNS = """
    d.id::text as document_id, d.opportunity_id::text as opportunity_id,
    d.quote_revision_id::text as quote_revision_id, d.kind, d.document_number,
    d.original_filename, d.content_type, d.byte_size, d.sha256, d.source,
    d.source_record_id::text as source_record_id, d.attached_at,
    d.removed_at, d.remove_reason
"""


def _case(cur: Any, repo: Any, opportunity_id: str) -> dict[str, Any]:
    cur.execute(
        "select id::text as id, stage from crm.opportunity where id = %s for update",
        (opportunity_id,),
    )
    case = repo._row(cur)
    if case is None:
        raise CommandRefused(404, "case_not_found", "no such case")
    return case


def attach_case_document(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Record a document whose object the route has already stored, and say so on the case.

    Refusals: no such case (404 `case_not_found`); the named revision is not on this case (404
    `quote_revision_not_on_case`); an email document whose message is not a captured Gmail
    message carrying a document with that SHA-256 (404 `document_not_in_message`); the same bytes
    already attached to this case and not removed (409 `document_already_attached`).
    """
    opportunity_id = fields["opportunity_id"]
    _case(cur, repo, opportunity_id)
    revision_id = fields.get("quote_revision_id")
    if revision_id:
        cur.execute(
            """select 1 from crm.quote_revision r join crm.quote q on q.id = r.quote_id
                where r.id = %s and q.opportunity_id = %s""",
            (revision_id, opportunity_id),
        )
        if cur.fetchone() is None:
            raise CommandRefused(404, "quote_revision_not_on_case", "that quote revision is not on this case")
    if fields["source"] == "email":
        cur.execute(
            """select 1 from evidence.source_record sr
                 cross join lateral jsonb_array_elements(
                   case when jsonb_typeof(sr.payload -> 'documents') = 'array'
                        then sr.payload -> 'documents' else '[]'::jsonb end) d
                where sr.id = %s and sr.kind = 'gmail_message'
                  and lower(d ->> 'sha256') = %s""",
            (fields["source_record_id"], fields["sha256"]),
        )
        if cur.fetchone() is None:
            raise CommandRefused(404, "document_not_in_message", "that message does not carry this document")
    cur.execute(
        """select id::text as id from crm.opportunity_document
            where opportunity_id = %s and kind = %s and sha256 = %s and removed_at is null""",
        (opportunity_id, fields["kind"], fields["sha256"]),
    )
    existing = repo._row(cur)
    if existing is not None:
        raise CommandRefused(
            409, "document_already_attached",
            f"this document is already attached to the case ({existing['id']})",
        )
    cur.execute(
        f"""insert into crm.opportunity_document
                (opportunity_id, quote_revision_id, kind, document_number, storage_path, sha256,
                 content_type, byte_size, original_filename, source, source_record_id,
                 attached_by_operator_id)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning {_DOCUMENT_COLUMNS.replace('d.', '')}""",
        (
            opportunity_id, revision_id, fields["kind"], fields.get("document_number"),
            fields["storage_path"], fields["sha256"], fields["content_type"], fields["byte_size"],
            fields["original_filename"], fields["source"], fields.get("source_record_id"),
            operator.operator_id,
        ),
    )
    document = repo._row(cur)
    assert document is not None  # noqa: S101 - `returning` on a successful insert
    repo._append_event(
        cur,
        aggregate_kind="opportunity",
        aggregate_id=opportunity_id,
        event_type="opportunity.document_attached",
        payload={
            "document_id": document["document_id"],
            "kind": fields["kind"],
            "sha256": fields["sha256"],
            "document_number": fields.get("document_number"),
            "quote_revision_id": revision_id,
            "source": fields["source"],
            "source_record_id": fields.get("source_record_id"),
            "note": fields["note"],
        },
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"document": _jsonable(document)}


def remove_case_document(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Remove one attached document, once, with its reason (the request's `note`)."""
    cur.execute(
        f"select {_DOCUMENT_COLUMNS} from crm.opportunity_document d where d.id = %s for update",
        (fields["document_id"],),
    )
    document = repo._row(cur)
    if document is None:
        raise CommandRefused(404, "document_not_found", "no such document")
    if document["removed_at"] is not None:
        raise CommandRefused(409, "document_already_removed", "this document was already removed")
    cur.execute(
        f"""update crm.opportunity_document d
               set removed_at = now(), removed_by_operator_id = %s, remove_reason = %s
             where d.id = %s
         returning {_DOCUMENT_COLUMNS}""",
        (operator.operator_id, fields["note"], fields["document_id"]),
    )
    document = repo._row(cur)
    assert document is not None  # noqa: S101
    repo._append_event(
        cur,
        aggregate_kind="opportunity",
        aggregate_id=document["opportunity_id"],
        event_type="opportunity.document_removed",
        payload={"document_id": document["document_id"], "kind": document["kind"],
                 "reason": fields["note"]},
        operator=operator,
        receipt_id=receipt_id,
    )
    return {"document": _jsonable(document)}


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        out[key] = value.isoformat() if hasattr(value, "isoformat") else value
    return out
