"""«Subir anexos» — several files, or a zip of them, onto a tender case (spec §6.4, plan Task 6).

`POST /v2/commands/attach-tender-annexes` takes a multipart form with `opportunity_id` and 1–20
`files` (each at most 20 MiB; the whole body at most 60 MiB + 64 KiB). A file is accepted by its
bytes — PDF, JPEG, PNG, DOCX, XLSX, or a legacy DOC/XLS — never by its name or the browser's
declaration. A `.zip` is unpacked in memory with V1's archive limits (`ArchiveLimits` defaults,
ruling R15) and each safe member is judged like an uploaded file. Nothing aborts silently: every
file that is not kept is listed by name with its reason.

| Reason | When |
|---|---|
| `unsupported_type` | not a type above (a `.rar`, a macro-enabled file, a nested zip, an empty file) |
| `too_large` | over 20 MiB (a file, or a zip member) |
| `unsafe_path` | a zip member whose path climbs out (`../`), is absolute or drive-qualified |
| `encrypted` | a password-protected zip member or Office file |
| `archive_rejected` | the zip is unreadable or breaks a limit (ratio over 200, over 512 members, ...): every member is skipped; an Office file that expands unsafely |
| `already_attached` | the same bytes are already a live anexo of the case |

Each kept file becomes a `crm.opportunity_document` (`kind = 'tender_annex'`, `source = 'upload'`)
named by its original or zip-member file name, with one `opportunity.document_attached` event.
A case that is not a tender is 422 `not_a_tender_case`. The route stores the objects first
(content-addressed, `opportunities/<case>/<sha256>.<ext>`) and then runs this command in one
transaction, as every document upload here does.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Any

from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import (
    ArchiveLimits,
    ArchiveSafetyError,
    iter_archive_members,
    preflight_archive,
    verify_actual_expansion,
)

from origenlab_api.v2.case_documents import (
    MAX_DOCUMENT_BYTES,
    DocumentRefused,
    check_document,
    clean_filename,
    document_path,
)
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

ATTACH_TENDER_ANNEXES = "attach-tender-annexes"
MAX_FILES = 20
MAX_TOTAL_BYTES = 60 * 1024 * 1024
#: Largest list of member names one rejected archive adds to the answer.
MAX_LISTED_MEMBERS = 100

_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")


def _skip(filename: str, reason: str) -> dict[str, str]:
    return {"filename": filename, "reason": reason}


def classify(data: bytes) -> tuple[str | None, str | None]:
    """`(content_type, None)` for a document this boundary keeps, else `(None, reason)`."""
    if len(data) > MAX_DOCUMENT_BYTES:
        return None, "too_large"
    if data.startswith(_OLE_SIGNATURE) and _ENCRYPTED_PACKAGE in data:
        return None, "encrypted"  # a password-protected .docx/.xlsx is an OLE wrapper
    try:
        return check_document(data, annex=True), None
    except DocumentRefused as exc:
        return None, "archive_rejected" if exc.code == "unsafe_document" else "unsupported_type"


def _member_names(data: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return [i.filename for i in archive.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, ValueError, NotImplementedError):
        return []


def _rejected(zip_name: str, data: bytes) -> list[dict[str, str]]:
    names = _member_names(data)
    listed = [_skip(name, "archive_rejected") for name in names[:MAX_LISTED_MEMBERS]]
    if not names:
        return [_skip(zip_name, "archive_rejected")]
    if len(names) > MAX_LISTED_MEMBERS:
        listed.append(_skip(f"{zip_name} (y {len(names) - MAX_LISTED_MEMBERS} archivos más)", "archive_rejected"))
    return listed


def unpack_zip(zip_name: str, data: bytes) -> tuple[list[tuple[str, bytes]], list[dict[str, str]]]:
    """The safe members `(path, bytes)` of a zip and the skipped ones. Never raises on a hostile zip."""
    limits = ArchiveLimits()
    try:
        preflight = preflight_archive(data, limits=limits)
        if preflight.rejected or verify_actual_expansion(data, limits=limits).rejected:
            return [], _rejected(zip_name, data)
        members = iter_archive_members(data, limits=limits)
    except (ArchiveSafetyError, zipfile.BadZipFile, ValueError, NotImplementedError, RuntimeError):
        return [], _rejected(zip_name, data)
    kept: list[tuple[str, bytes]] = []
    skipped: list[dict[str, str]] = []
    for member in members:
        if "unsafe_member_path" in member.warnings:
            skipped.append(_skip(member.member_path, "unsafe_path"))
        elif member.encrypted:
            skipped.append(_skip(member.member_path, "encrypted"))
        elif member.truncated:
            skipped.append(_skip(member.member_path, "too_large"))
        elif any(w.startswith("member_unreadable") for w in member.warnings):
            skipped.append(_skip(member.member_path, "archive_rejected"))
        else:
            kept.append((member.member_path, member.payload))
    return kept, skipped


def expand_uploads(uploads: list[tuple[str, bytes]]) -> tuple[list[tuple[str, str, bytes]], list[dict[str, str]]]:
    """Judge each uploaded file (unpacking a `.zip`): `[(shown name, content type, bytes)]` kept and
    `[{filename, reason}]` skipped, in upload order."""
    kept: list[tuple[str, str, bytes]] = []
    skipped: list[dict[str, str]] = []
    for name, data in uploads:
        content_type, reason = classify(data)
        if content_type is None and reason == "unsupported_type" and name.lower().endswith(".zip") \
                and data.startswith(b"PK"):
            members, member_skips = unpack_zip(name, data)
            skipped.extend(member_skips)
            for path, payload in members:
                member_type, member_reason = classify(payload)
                if member_type is None:
                    skipped.append(_skip(path, member_reason or "unsupported_type"))
                else:
                    kept.append((clean_filename(path), member_type, payload))
            continue
        if content_type is None:
            skipped.append(_skip(clean_filename(name), reason or "unsupported_type"))
        else:
            kept.append((clean_filename(name), content_type, data))
    return kept, skipped


def prepare_objects(opportunity_id: str, kept: list[tuple[str, str, bytes]], storage: Any) -> list[dict[str, Any]]:
    """Put each kept file into the private bucket (content-addressed) and describe it for the command."""
    files = []
    for filename, content_type, data in kept:
        sha256 = hashlib.sha256(data).hexdigest()
        path = document_path(opportunity_id, sha256, content_type)
        storage.put_if_absent(path, data, content_type)
        files.append({"filename": filename, "sha256": sha256, "content_type": content_type,
                      "byte_size": len(data), "storage_path": path})
    return files


def attach_tender_annexes(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Record the stored files as the tender's anexos, skipping bytes already live on the case.

    Refusals: no such case (404 `case_not_found`); the case is not a tender (422 `not_a_tender_case`).
    """
    opportunity_id = fields["opportunity_id"]
    cur.execute("select kind from crm.opportunity where id = %s::uuid for update", (opportunity_id,))
    case = cur.fetchone()
    if case is None:
        raise CommandRefused(404, "case_not_found", "no such case")
    if case[0] != "tender":
        raise CommandRefused(422, "not_a_tender_case", "Los anexos se suben a una licitación.")
    attached: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = list(fields["skipped"])
    event_ids: list[Any] = []
    for file in fields["files"]:
        cur.execute(
            "select 1 from crm.opportunity_document where opportunity_id = %s::uuid and kind = 'tender_annex' "
            "and sha256 = %s and removed_at is null", (opportunity_id, file["sha256"]))
        if cur.fetchone() is not None:
            skipped.append(_skip(file["filename"], "already_attached"))
            continue
        cur.execute(
            """insert into crm.opportunity_document
                   (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename,
                    source, attached_by_operator_id)
               values (%s::uuid, 'tender_annex', %s, %s, %s, %s, %s, 'upload', %s::uuid)
               returning id::text""",
            (opportunity_id, file["storage_path"], file["sha256"], file["content_type"], file["byte_size"],
             file["filename"], operator.operator_id))
        document_id = cur.fetchone()[0]
        event_ids.append(repo._append_event(
            cur, aggregate_kind="opportunity", aggregate_id=opportunity_id,
            event_type="opportunity.document_attached",
            payload={"document_id": document_id, "kind": "tender_annex", "sha256": file["sha256"],
                     "document_number": None, "quote_revision_id": None, "source": "upload",
                     "source_record_id": None, "note": None},
            operator=operator, receipt_id=receipt_id))
        attached.append({"document_id": document_id, "filename": file["filename"]})
    return {"command": ATTACH_TENDER_ANNEXES, "opportunity_id": opportunity_id, "attached": attached,
            "skipped": skipped, "event_ids": event_ids}
