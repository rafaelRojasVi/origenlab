"""«Subir anexos» — several files, or a zip of them, onto a tender case (spec §6.4, plan Task 6).

`POST /v2/commands/attach-tender-annexes` takes a multipart form with `opportunity_id` and 1–20
`files` (each at most 20 MiB; the whole body at most 60 MiB + 64 KiB). A file is accepted by its
bytes — PDF, JPEG, PNG, DOCX, XLSX, or a legacy DOC/XLS — never by its name or the browser's
declaration. A `.zip` is unpacked in memory with V1's archive checks (ruling R15; totals are this request's 60 MiB, a member is read to 20 MiB) and each safe member is judged like an uploaded file. Nothing aborts silently: every
file that is not kept is listed by name with its reason.

| Reason | When |
|---|---|
| `unsupported_type` | not a type above (a `.rar`, a macro-enabled file, a nested zip, an empty file) |
| `too_large` | over 20 MiB (a file, or a zip member) |
| `unsafe_path` | a zip member whose path climbs out (`../`), is absolute or drive-qualified |
| `encrypted` | a password-protected zip member or Office file |
| `archive_rejected` | the zip is unreadable or breaks a limit (ratio over 200, over 512 members, ...): every member is skipped; an Office file that expands unsafely |
| `already_attached` | the same bytes are already a live anexo of the case |
| `request_too_large` | the files kept so far already fill the request's 60 MiB (loose files and zip members share it) |
| `not_listed` | one summary line: the list was cut at 100 names (`MAX_LISTED_MEMBERS`) |

Each kept file becomes a `crm.opportunity_document` (`kind = 'tender_annex'`, `source = 'upload'`)
named by its original or zip-member file name, with one `opportunity.document_attached` event.
A case that is not a tender is 422 `not_a_tender_case`. The route stores the objects first
(content-addressed, `opportunities/<case>/<sha256>.<ext>`) and then runs this command in one
transaction, as every document upload here does.

«Quitar anexo» — `POST /v2/commands/remove-tender-annex {opportunity_id, document_id, reason}` — removes
one wrong anexo once, as a case document is removed: the row stays with `removed_at`, who and the
reason (3 characters or more), and one `opportunity.document_removed` event. A removed anexo is no
longer on the page and no new preparation takes it. Refusals: 404 `case_not_found`, 422
`not_a_tender_case`, 404 `annex_not_found` (not a `tender_annex` of this case), 409
`document_already_removed`.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

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
REMOVE_TENDER_ANNEX = "remove-tender-annex"
MAX_FILES = 20
MAX_TOTAL_BYTES = 60 * 1024 * 1024
#: Largest list of member names one rejected archive adds to the answer.
MAX_LISTED_MEMBERS = 100

_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")


#: Longest name a skipped entry repeats; a hostile zip member name may be far longer.
MAX_SKIPPED_NAME = 200


def _skip(filename: str, reason: str) -> dict[str, str]:
    shown = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    return {"filename": shown[:MAX_SKIPPED_NAME], "reason": reason}


def cap_skipped(skipped: list[dict[str, str]]) -> list[dict[str, str]]:
    """At most `MAX_LISTED_MEMBERS` skipped entries, then one summary entry for the rest."""
    if len(skipped) <= MAX_LISTED_MEMBERS:
        return skipped
    return [*skipped[:MAX_LISTED_MEMBERS],
            _skip(f"(y {len(skipped) - MAX_LISTED_MEMBERS} archivos más)", "not_listed")]


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
    return [_skip(name, "archive_rejected") for name in names] or [_skip(zip_name, "archive_rejected")]


def unpack_zip(
    zip_name: str, data: bytes, budget: int = MAX_TOTAL_BYTES
) -> tuple[list[tuple[str, bytes]], list[dict[str, str]]]:
    """The safe members `(path, bytes)` of a zip and the skipped ones. Never raises on a hostile zip.

    `budget` is what is left of the request's bytes: no more than that is ever read into memory,
    and a member cut short by it (not by the 20 MiB limit) is skipped `request_too_large`.
    """
    # The totals are the request's, not V1's 256 MiB. The preflight judges a member at V1's 64 MiB;
    # the read stops at 20 MiB, so a big member is `too_large` rather than a rejected archive.
    check = ArchiveLimits(max_total_uncompressed_bytes=MAX_TOTAL_BYTES)
    read = ArchiveLimits(max_total_uncompressed_bytes=MAX_TOTAL_BYTES,
                         max_member_uncompressed_bytes=MAX_DOCUMENT_BYTES)
    try:
        preflight = preflight_archive(data, limits=check)
        if preflight.rejected or verify_actual_expansion(data, limits=check).rejected:
            return [], _rejected(zip_name, data)
        members = iter_archive_members(data, limits=read, remaining_budget_bytes=budget)
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
            # Cut by the 20 MiB member limit, or by what the request had left.
            by_budget = len(member.payload) < MAX_DOCUMENT_BYTES
            skipped.append(_skip(member.member_path, "request_too_large" if by_budget else "too_large"))
        elif any(w.startswith("member_unreadable") for w in member.warnings):
            skipped.append(_skip(member.member_path, "archive_rejected"))
        else:
            kept.append((member.member_path, member.payload))
    return kept, skipped


def expand_uploads(uploads: list[tuple[str, bytes]]) -> tuple[list[tuple[str, str, bytes]], list[dict[str, str]]]:
    """Judge each uploaded file (unpacking a `.zip`): `[(shown name, content type, bytes)]` kept and
    `[{filename, reason}]` skipped, in upload order. Everything kept, loose or from a zip, shares one
    budget of `MAX_TOTAL_BYTES`; what does not fit is skipped `request_too_large`."""
    kept: list[tuple[str, str, bytes]] = []
    skipped: list[dict[str, str]] = []
    budget = MAX_TOTAL_BYTES

    def keep(name: str, content_type: str, data: bytes) -> None:
        nonlocal budget
        if len(data) > budget:
            skipped.append(_skip(name, "request_too_large"))
            return
        budget -= len(data)
        kept.append((clean_filename(name), content_type, data))

    for name, data in uploads:
        content_type, reason = classify(data)
        if content_type is None and reason == "unsupported_type" and name.lower().endswith(".zip") \
                and data.startswith(b"PK"):
            if budget <= 0:
                skipped.extend(_skip(n, "request_too_large") for n in (_member_names(data) or [name]))
                continue
            members, member_skips = unpack_zip(name, data, budget)
            skipped.extend(member_skips)
            for path, payload in members:
                member_type, member_reason = classify(payload)
                if member_type is None:
                    skipped.append(_skip(path, member_reason or "unsupported_type"))
                else:
                    keep(path, member_type, payload)
            continue
        if content_type is None:
            skipped.append(_skip(clean_filename(name), reason or "unsupported_type"))
        else:
            keep(name, content_type, data)
    return kept, cap_skipped(skipped)


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
    skipped: list[dict[str, str]] = list(fields["skipped"])  # already capped
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
            "skipped": cap_skipped(skipped), "event_ids": event_ids}


# ------------------------------------------------------------------ «Quitar anexo»

class RemoveTenderAnnexBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opportunity_id: str
    document_id: str
    reason: Annotated[str, Field(min_length=3, max_length=2000)]


def remove_tender_annex(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Remove one anexo of a tender, once, with its reason (the case-document way: nothing deleted)."""
    opportunity_id, document_id = fields["opportunity_id"], fields["document_id"]
    cur.execute("select kind from crm.opportunity where id = %s::uuid for update", (opportunity_id,))
    case = cur.fetchone()
    if case is None:
        raise CommandRefused(404, "case_not_found", "no such case")
    if case[0] != "tender":
        raise CommandRefused(422, "not_a_tender_case", "Los anexos son de una licitación.")
    cur.execute(
        "select removed_at from crm.opportunity_document where id = %s::uuid and opportunity_id = %s::uuid "
        "and kind = 'tender_annex' for update", (document_id, opportunity_id))
    row = cur.fetchone()
    if row is None:
        raise CommandRefused(404, "annex_not_found", "No existe ese anexo en esta licitación.")
    if row[0] is not None:
        raise CommandRefused(409, "document_already_removed", "Ese anexo ya se quitó.")
    cur.execute(
        "update crm.opportunity_document set removed_at = now(), removed_by_operator_id = %s::uuid, "
        "remove_reason = %s where id = %s::uuid",
        (operator.operator_id, fields["reason"], document_id))
    event_id = repo._append_event(
        cur, aggregate_kind="opportunity", aggregate_id=opportunity_id,
        event_type="opportunity.document_removed",
        payload={"document_id": document_id, "kind": "tender_annex", "reason": fields["reason"]},
        operator=operator, receipt_id=receipt_id)
    return {"command": REMOVE_TENDER_ANNEX, "opportunity_id": opportunity_id, "document_id": document_id,
            "event_ids": [event_id]}
