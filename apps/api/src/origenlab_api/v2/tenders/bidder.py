"""Bidder data for tenders (spec §6.2–§6.3): who bids in Mercado Público, and their standard documents.

Today the bidder is a natural person; a company comes later (`bidder_kind`, `legal_rep_*`). The
profile is append-only (`crm.bidder_profile`: a save is a new row, the newest is current); a
document is added once and retired once (`crm.bidder_document`), its bytes under
`crm-documents/bidder/<sha256>.<ext>`.

Everything here is private: only `sales` and `admin` read or write it. The API role reads these
tables under `using (true)` row security, so the routes are the gate — each answers a `viewer` with
403 `role_may_not_view_bidder` before anything is read (`routes.py`). The events
`bidder.profile_saved`, `bidder.document_added` and `bidder.document_retired` name what changed and
never carry a RUT, a bank number or an address.

| Status | Code | When |
|---|---|---|
| 422 | `rut_invalid` | the bidder's or the representative's RUT fails its check digit |
| 422 | `legal_rep_only_for_company` | a natural person with a representative |
| 409 | `bidder_profile_changed` | `expected_current_id` is not the current profile |
| 415 / 413 | `unsupported_document` / `document_too_large` | the bytes are no PDF/JPEG/PNG/DOCX/XLSX, or over 20 MiB |
| 409 | `bidder_document_already_added` | the same bytes are already a live document |
| 404 | `document_not_found` | no such document |
| 409 | `document_already_retired` | the document was retired before |
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.case_documents import MAX_DOCUMENT_BYTES, DocumentRefused, sniff_document_type
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.org_web_suggestions import valid_rut
from origenlab_api.v2.quote_document_identity import normalize_rut

SAVE_BIDDER_PROFILE = "save-bidder-profile"
ADD_BIDDER_DOCUMENT = "add-bidder-document"
RETIRE_BIDDER_DOCUMENT = "retire-bidder-document"

DOCUMENT_KINDS = ("cedula", "certificado_inscripcion", "declaracion_jurada", "experiencia", "otro")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXTENSIONS = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", DOCX: "docx", XLSX: "xlsx"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
#: The table's CHECK: valid_rut also accepts 1-9 digit bodies, which the column refuses.
_STORED_RUT = re.compile(r"^[0-9]{7,8}-[0-9K]$")

#: The profile columns a save sets, in the order the page shows them.
PROFILE_FIELDS = (
    "bidder_kind", "legal_name", "rut", "giro", "address", "comuna", "region", "phone", "email",
    "legal_rep_name", "legal_rep_rut", "bank_name", "bank_account_type", "bank_account_number",
    "chileproveedores_status", "chileproveedores_checked_on", "is_emt", "is_local_supplier",
)
_PROFILE_SELECT = """
    p.id::text as id, p.bidder_kind, p.legal_name, p.rut, p.giro, p.address, p.comuna, p.region,
    p.phone, p.email, p.legal_rep_name, p.legal_rep_rut, p.bank_name, p.bank_account_type,
    p.bank_account_number, p.chileproveedores_status, p.chileproveedores_checked_on, p.is_emt,
    p.is_local_supplier, p.set_by_operator_id::text as set_by_operator_id, p.reason, p.created_at
"""

_Text = Annotated[str, Field(max_length=200)]


class BidderProfileBody(BaseModel):
    """The `crm.bidder_profile` columns, plus why and which profile the operator was looking at."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bidder_kind: Literal["natural_person", "company"]
    legal_name: Annotated[str, Field(min_length=1, max_length=200)]
    rut: Annotated[str, Field(min_length=1, max_length=20)]
    giro: _Text | None = None
    address: _Text | None = None
    comuna: _Text | None = None
    region: _Text | None = None
    phone: Annotated[str, Field(max_length=40)] | None = None
    email: Annotated[str, Field(max_length=200)] | None = None
    legal_rep_name: _Text | None = None
    legal_rep_rut: Annotated[str, Field(max_length=20)] | None = None
    bank_name: _Text | None = None
    bank_account_type: Literal["corriente", "vista", "ahorro", "rut"] | None = None
    bank_account_number: Annotated[str, Field(max_length=40)] | None = None
    chileproveedores_status: Literal["habil", "no_habil", "no_inscrito"] | None = None
    chileproveedores_checked_on: date | None = None
    is_emt: bool = False
    is_local_supplier: bool = False
    reason: Annotated[str, Field(min_length=3, max_length=2000)]
    expected_current_id: str | None = None

    @field_validator(
        "giro", "address", "comuna", "region", "phone", "email", "legal_rep_name", "legal_rep_rut", "bank_name",
        "bank_account_number", "expected_current_id", mode="before",
    )
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value


class RetireBidderDocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_id: str
    reason: Annotated[str, Field(min_length=3, max_length=2000)]


class AddBidderDocumentForm(BaseModel):
    """The text fields of the `add-bidder-document` multipart form."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["cedula", "certificado_inscripcion", "declaracion_jurada", "experiencia", "otro"]
    title: Annotated[str, Field(min_length=1, max_length=200)]


def format_rut(rut: str) -> str:
    """`11111111-1` as `11.111.111-1`, the form a printed document uses (ruling R15)."""
    body, _, check = rut.partition("-")
    return f"{int(body):,}".replace(",", ".") + f"-{check}"


# ------------------------------------------------------------------ the profile


def _row(cur: Any) -> dict[str, Any] | None:
    row = cur.fetchone()
    return None if row is None else dict(zip([d[0] for d in cur.description], row, strict=True))


def current_bidder_profile(cur: Any) -> dict[str, Any] | None:
    """The current bidder profile — the newest `crm.bidder_profile` row — or None."""
    cur.execute(f"select {_PROFILE_SELECT} from crm.bidder_profile p order by p.created_at desc, p.id desc limit 1")
    return _row(cur)


def prepare_save_bidder_profile(body: BidderProfileBody) -> dict[str, Any]:
    """The checks that need no database: RUTs normalised and verified, a representative only for a company."""
    fields = body.model_dump()
    fields["rut"] = normalize_rut(body.rut)
    if not (valid_rut(fields["rut"]) and _STORED_RUT.match(fields["rut"])):
        raise CommandRefused(422, "rut_invalid", "El RUT del oferente no es válido.")
    if body.legal_rep_rut is not None:
        fields["legal_rep_rut"] = normalize_rut(body.legal_rep_rut)
        if not (valid_rut(fields["legal_rep_rut"]) and _STORED_RUT.match(fields["legal_rep_rut"])):
            raise CommandRefused(422, "rut_invalid", "El RUT del representante legal no es válido.")
    if body.bidder_kind != "company" and (body.legal_rep_name is not None or body.legal_rep_rut is not None):
        raise CommandRefused(
            422, "legal_rep_only_for_company", "Solo una empresa tiene representante legal.")
    return fields


def save_bidder_profile(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Append a profile row. The event names the fields that differ from the previous profile."""
    cur.execute("select pg_advisory_xact_lock(hashtextextended('bidder:profile', 0))")
    current = current_bidder_profile(cur)
    if (current["id"] if current else None) != fields["expected_current_id"]:
        raise CommandRefused(
            409, "bidder_profile_changed",
            "Los datos del oferente cambiaron desde que los viste; vuelve a leerlos.")
    values = {name: fields[name] for name in PROFILE_FIELDS}
    columns = ", ".join(PROFILE_FIELDS)
    marks = ", ".join(f"%({name})s" for name in PROFILE_FIELDS)
    cur.execute(
        f"insert into crm.bidder_profile ({columns}, set_by_operator_id, reason) "
        f"values ({marks}, %(operator)s::uuid, %(reason)s) returning id::text",
        {**values, "operator": operator.operator_id, "reason": fields["reason"]},
    )
    profile_id = cur.fetchone()[0]
    changed = [name for name in PROFILE_FIELDS if current is None and values[name] not in (None, False)
               or current is not None and current[name] != values[name]]
    event_id = repo._append_event(
        cur, aggregate_kind="bidder", aggregate_id=profile_id, event_type="bidder.profile_saved",
        payload={"changed_fields": changed, "previous_profile_id": current["id"] if current else None},
        operator=operator, receipt_id=receipt_id)
    return {"command": SAVE_BIDDER_PROFILE, "profile_id": profile_id, "event_ids": [event_id]}


# ------------------------------------------------------------------ the documents


def sniff_bidder_document_type(data: bytes) -> str | None:
    """PDF, JPEG, PNG, or an Office zip told apart by its main part (`word/document.xml`,
    `xl/workbook.xml`). The bytes decide, never the browser's declaration."""
    plain = sniff_document_type(data)
    if plain is not None:
        return plain
    if not data.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, ValueError, NotImplementedError):
        return None
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        return None  # macro-enabled: not a document this boundary keeps
    if "word/document.xml" in names and "[Content_Types].xml" in names:
        return DOCX
    if "xl/workbook.xml" in names and "[Content_Types].xml" in names:
        return XLSX
    return None


def check_bidder_document(data: bytes) -> str:
    """The content type of an acceptable bidder document, or `DocumentRefused`."""
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentRefused(413, "document_too_large", "the document is larger than 20 MiB")
    content_type = sniff_bidder_document_type(data)
    if content_type is None:
        raise DocumentRefused(415, "unsupported_document", "only a PDF, JPEG, PNG, DOCX or XLSX document")
    return content_type


def bidder_document_path(sha256: str, content_type: str) -> str:
    """`bidder/<sha256>.<pdf|jpg|png|docx|xlsx>` — the bytes name their own object."""
    if not _SHA256.match(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return f"bidder/{sha256}.{EXTENSIONS[content_type]}"


_DOCUMENT_SELECT = """
    d.id::text as id, d.kind, d.title, d.storage_bucket, d.storage_path, d.sha256, d.content_type,
    d.byte_size, d.original_filename, d.added_at
"""


def live_bidder_documents(cur: Any) -> list[dict[str, Any]]:
    """The documents not retired, oldest first (with their storage path and hash, for the callers
    that read the bytes; the page read strips them)."""
    cur.execute(
        f"select {_DOCUMENT_SELECT} from crm.bidder_document d where d.retired_at is null "
        "order by d.added_at, d.id")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def add_bidder_document(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Record a document whose object the route has already stored."""
    cur.execute("select pg_advisory_xact_lock(hashtextextended('bidder:documents', 0))")
    cur.execute(
        "select id::text from crm.bidder_document where sha256 = %s and kind = %s and retired_at is null",
        (fields["sha256"], fields["kind"]))
    if cur.fetchone() is not None:
        raise CommandRefused(409, "bidder_document_already_added", "Ese documento ya está en los datos del oferente.")
    cur.execute(
        """
        insert into crm.bidder_document
            (kind, title, storage_path, sha256, content_type, byte_size, original_filename, added_by_operator_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s::uuid)
        returning id::text
        """,
        (fields["kind"], fields["title"], fields["storage_path"], fields["sha256"], fields["content_type"],
         fields["byte_size"], fields["original_filename"], operator.operator_id))
    document_id = cur.fetchone()[0]
    event_id = repo._append_event(
        cur, aggregate_kind="bidder", aggregate_id=document_id, event_type="bidder.document_added",
        payload={"document_id": document_id, "kind": fields["kind"], "sha256": fields["sha256"],
                 "content_type": fields["content_type"], "byte_size": fields["byte_size"]},
        operator=operator, receipt_id=receipt_id)
    return {"command": ADD_BIDDER_DOCUMENT, "document_id": document_id, "event_ids": [event_id]}


def retire_bidder_document(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    cur.execute("select retired_at from crm.bidder_document where id = %s::uuid for update", (fields["document_id"],))
    row = cur.fetchone()
    if row is None:
        raise CommandRefused(404, "document_not_found", "No existe ese documento.")
    if row[0] is not None:
        raise CommandRefused(409, "document_already_retired", "Ese documento ya estaba retirado.")
    cur.execute(
        "update crm.bidder_document set retired_at = now(), retired_by_operator_id = %s::uuid, retire_reason = %s "
        "where id = %s::uuid",
        (operator.operator_id, fields["reason"], fields["document_id"]))
    event_id = repo._append_event(
        cur, aggregate_kind="bidder", aggregate_id=fields["document_id"], event_type="bidder.document_retired",
        payload={"document_id": fields["document_id"]},
        operator=operator, receipt_id=receipt_id)
    return {"command": RETIRE_BIDDER_DOCUMENT, "document_id": fields["document_id"], "event_ids": [event_id]}


# ------------------------------------------------------------------ the read


def bidder_view(cur: Any) -> dict[str, Any]:
    """`GET /v2/bidder`: the current profile (or null), every save newest first, the live documents."""
    profile = current_bidder_profile(cur)
    cur.execute(
        """
        select p.id::text as id, o.display_name as set_by, p.reason, p.created_at
          from crm.bidder_profile p join platform.operator o on o.id = p.set_by_operator_id
         order by p.created_at desc, p.id desc
        """)
    names = [d[0] for d in cur.description]
    history = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    set_by = history[0]["set_by"] if history else None
    if profile is not None:
        profile.pop("set_by_operator_id", None)
        profile["set_by"] = set_by
    documents = [{k: v for k, v in d.items() if k not in ("storage_bucket", "storage_path")}
                 for d in live_bidder_documents(cur)]
    return {"profile": profile, "history": history, "documents": documents}
