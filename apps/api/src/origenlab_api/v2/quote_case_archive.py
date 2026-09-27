"""Case-first Drive archive for quotations — one Drive folder per commercial case.

The CRM owns what a quotation *is* (its opportunity, its status, its organization). Drive
holds the documents. This module keeps the two apart:

- **One stable folder per case.** A case folder lives directly under ``Cotizaciones/Casos`` and
  is keyed by ``origenlab_case_key`` in its ``appProperties`` — the planned opportunity id the
  ledger minted once from the opening document. Its name uses only immutable facts from the
  sent document (opening serial, printed addressee). Neither CRM status nor a confirmed
  organization name ever appears in a name, a path or a property, so a status change moves
  nothing in Drive, and a pending document is never filed apart from its case.
- **One file per document.** A revision file is keyed by ``origenlab_document_sha256``. The
  same bytes are never uploaded twice; a file already filed under another case is refused;
  nothing is ever overwritten, moved, renamed, trashed or deleted by this module.
- **Every upload is verified twice.** Drive's reported ``sha256Checksum`` and a fresh download
  of the file must both equal the local SHA-256. A mismatch stops the run and is reported with
  the offending file id; the file is left in place for a human (no delete path exists here).
- **Retries are safe.** A partial run (folder created, upload lost, verification interrupted)
  is resumed by looking the case folder and the document up by their keys — first through the
  caller's journal of ids already written (Drive's search index lags), then by property search.
- **Archive is not approval.** Uploading to Drive changes no CRM state. ``crm_link_intents``
  only *describes* the CRM rows that would record the link (a ``drive_folder`` external
  identifier, a ``drive_file`` evidence record, a ``document_reference`` assertion); an
  opportunity that is not in the CRM yet gets a deferred intent, never an inferred one.

Drive is reached only through the injected :class:`DrivePort`. This module makes no network
call of its own and imports no Google library; tests drive it with an in-memory fake.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

ARCHIVE_VERSION = "qcase-archive/2026-09-26.1"

FOLDER_MIME = "application/vnd.google-apps.folder"
PDF_MIME = "application/pdf"

# appProperties — the same keys the 2026-09-26 case upload wrote (manifest 1c9a00f7…).
PROP_KIND = "origenlab_kind"
PROP_CASE_KEY = "origenlab_case_key"
PROP_OPENING_QUOTE = "origenlab_opening_quote"
PROP_DOC_SHA = "origenlab_document_sha256"
PROP_QUOTE_NUMBER = "origenlab_quote_number"
PROP_REVISION = "origenlab_revision"
PROP_GMAIL_MESSAGE = "origenlab_gmail_message_id"
PROP_LEGACY_FILE = "origenlab_legacy_file_id"
# Stamped on items a run *creates* (never on reused ones): rollback finds exactly them by property.
PROP_RUN = "origenlab_archive_run"

KIND_CASE_ROOT = "case_root"
KIND_CASE = "case"
KIND_REVISION = "quote_revision"

# Drive limits one appProperty to 124 bytes of key + value (UTF-8).
_APP_PROPERTY_MAX_BYTES = 124
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_WS_RE = re.compile(r"\s+")
_NO_ADDRESSEE = "sin destinatario impreso"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Names ────────────────────────────────────────────────────────────────────────────────


class NameRefused(ValueError):
    """A Drive name that cannot be used as-is (a path separator, a control character, empty)."""


# A '/' in a printed addressee ("Unidad X / Universidad Y") is shown as U+2215 DIVISION SLASH in
# Drive; the printed text itself is kept verbatim in the manifest. Names are never identity.
_SLASH_STANDIN = "\u2215"


def _clean(text: str) -> str:
    out = _WS_RE.sub(" ", text).strip().replace("/", _SLASH_STANDIN)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in out):
        raise NameRefused(f"unsafe character in Drive name: {text!r}")
    return out


def opening_serial(quote_number: str) -> str:
    """'01244-26' → '01244'; '011453AI' stays whole. The number is never renormalised."""
    serial = quote_number.split("-")[0].strip()
    if not serial:
        raise NameRefused(f"no serial in quote number {quote_number!r}")
    return serial


def case_folder_name(opening_quote_number: str, printed_addressee: str | None) -> str:
    """'Caso <opening serial> — <addressee as printed>'. Never renamed after creation."""
    addressee = _clean(printed_addressee or "") or _NO_ADDRESSEE
    return _clean(f"Caso {opening_serial(opening_quote_number)} — {addressee}")


def revision_file_name(quote_number: str, revision: int, original_filename: str) -> str:
    """'<quote number> r<n> — <original filename>'. A new revision is a new file."""
    if revision < 1:
        raise NameRefused(f"revision must be ≥ 1, got {revision}")
    original = _clean(original_filename)
    if not original:
        raise NameRefused("empty original filename")
    return _clean(f"{quote_number} r{revision} — {original}")


def app_properties_fit(props: Mapping[str, str]) -> bool:
    return all(len(k.encode()) + len(str(v).encode()) <= _APP_PROPERTY_MAX_BYTES for k, v in props.items())


# ── Lifecycle: CRM status and archive status are two separate facts ─────────────────────


class CrmStatus(StrEnum):
    """Where the *opportunity* stands. Only the CRM (or the import plan standing in for it) sets this."""

    NOT_IN_PLAN = "not_in_plan"
    READY_TO_IMPORT = "ready_to_import"
    PENDING_ORGANIZATION = "pending_organization_confirmation"
    HELD = "held"
    OPEN_IN_CRM = "open_in_crm"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


class DocumentRole(StrEnum):
    """What the ledger says the *document* is."""

    QUOTATION = "quotation"
    REJECTED_NON_QUOTATION = "rejected_non_quotation"
    PENDING_DOCUMENT = "pending_document"  # document-level leave_pending (e.g. 01243 owner hold)
    BLOCKED_DOCUMENT = "blocked_document"  # owner-blocked (G1 collision etc.)
    UNREVIEWED = "unreviewed"  # known bytes, no ledger decision
    UNKNOWN = "unknown"  # no local evidence at all


class ArchiveStatus(StrEnum):
    """Where the *bytes* stand in Drive. Never feeds CrmStatus."""

    NOT_ARCHIVED = "not_archived"
    LEGACY_ONLY = "legacy_only"  # only in Pendientes/Enviadas
    ARCHIVED_VERIFIED = "archived_verified"  # in its case folder, both hashes checked
    ARCHIVED_UNVERIFIED = "archived_unverified"
    HASH_MISMATCH = "hash_mismatch"


class Lifecycle(StrEnum):
    """The single label an operator reads. Derived, never stored."""

    HISTORICAL_SENT = "historical_sent"
    REVISION = "revision"
    PENDING_ORGANIZATION = "pending_organization_confirmation"
    HELD = "held"
    CONFIRMED_OPPORTUNITY = "confirmed_opportunity"
    REJECTED_NON_QUOTATION = "rejected_non_quotation"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"
    UNKNOWN_NEEDS_REVIEW = "unknown_needs_review"


LIFECYCLE_LABEL_ES = {
    Lifecycle.HISTORICAL_SENT: "Cotización enviada (histórica)",
    Lifecycle.REVISION: "Revisión",
    Lifecycle.PENDING_ORGANIZATION: "Pendiente: confirmar institución",
    Lifecycle.HELD: "Retenida",
    Lifecycle.CONFIRMED_OPPORTUNITY: "Oportunidad confirmada",
    Lifecycle.REJECTED_NON_QUOTATION: "Rechazada: no es cotización",
    Lifecycle.CLOSED_WON: "Cerrada: ganada",
    Lifecycle.CLOSED_LOST: "Cerrada: perdida",
    Lifecycle.UNKNOWN_NEEDS_REVIEW: "Desconocida: requiere revisión",
}


def document_lifecycle(role: DocumentRole, crm: CrmStatus, *, revision: int = 1) -> Lifecycle:
    """One label, fixed precedence. ``ArchiveStatus`` is deliberately not a parameter: a file
    being in Drive can never make a document look confirmed."""
    if role is DocumentRole.REJECTED_NON_QUOTATION:
        return Lifecycle.REJECTED_NON_QUOTATION
    if role in (DocumentRole.UNKNOWN, DocumentRole.UNREVIEWED):
        return Lifecycle.UNKNOWN_NEEDS_REVIEW
    if role in (DocumentRole.PENDING_DOCUMENT, DocumentRole.BLOCKED_DOCUMENT) or crm is CrmStatus.HELD:
        return Lifecycle.HELD
    if crm is CrmStatus.CLOSED_WON:
        return Lifecycle.CLOSED_WON
    if crm is CrmStatus.CLOSED_LOST:
        return Lifecycle.CLOSED_LOST
    if crm is CrmStatus.PENDING_ORGANIZATION:
        return Lifecycle.PENDING_ORGANIZATION
    if crm is CrmStatus.NOT_IN_PLAN:
        return Lifecycle.UNKNOWN_NEEDS_REVIEW
    if crm is CrmStatus.OPEN_IN_CRM:
        return Lifecycle.REVISION if revision > 1 else Lifecycle.CONFIRMED_OPPORTUNITY
    # READY_TO_IMPORT: confirmed by the owner, not yet in the CRM — a sent historical document.
    return Lifecycle.REVISION if revision > 1 else Lifecycle.HISTORICAL_SENT


def crm_status_from_plan(plan_status: str | None) -> CrmStatus:
    return {
        "ready": CrmStatus.READY_TO_IMPORT,
        "needs_organization_confirmation": CrmStatus.PENDING_ORGANIZATION,
        "held": CrmStatus.HELD,
    }.get(plan_status or "", CrmStatus.NOT_IN_PLAN)


# ── The Drive port and the archiver ─────────────────────────────────────────────────────


class DrivePort(Protocol):
    """The seven Drive operations the archiver needs. Search results exclude trashed items."""

    def principal(self) -> str: ...

    def get(self, file_id: str) -> dict[str, Any] | None: ...

    def find_by_property(self, key: str, value: str) -> list[dict[str, Any]]: ...

    def children(self, folder_id: str) -> list[dict[str, Any]]: ...

    def create_folder(self, *, name: str, parent_id: str, app_properties: Mapping[str, str]) -> dict[str, Any]: ...

    def upload_pdf(self, *, name: str, parent_id: str, app_properties: Mapping[str, str], data: bytes) -> dict[str, Any]: ...

    def download(self, file_id: str) -> bytes: ...


class ArchiveRefused(RuntimeError):
    """The archiver stopped. ``code`` is stable; ``partial`` holds what was already written."""

    def __init__(self, code: str, detail: str, partial: ArchiveResult | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.partial = code, detail, partial


@dataclass(frozen=True)
class ArchiveDocument:
    document_sha256: str
    quote_number: str
    revision: int
    original_filename: str
    data: bytes
    gmail_message_id: str | None = None
    legacy_file_id: str | None = None

    def app_properties(self, case_key: str) -> dict[str, str]:
        props = {
            PROP_KIND: KIND_REVISION,
            PROP_CASE_KEY: case_key,
            PROP_DOC_SHA: self.document_sha256,
            PROP_QUOTE_NUMBER: self.quote_number,
            PROP_REVISION: str(self.revision),
        }
        if self.gmail_message_id:
            props[PROP_GMAIL_MESSAGE] = self.gmail_message_id
        if self.legacy_file_id:
            props[PROP_LEGACY_FILE] = self.legacy_file_id
        return props

    @property
    def file_name(self) -> str:
        return revision_file_name(self.quote_number, self.revision, self.original_filename)


@dataclass(frozen=True)
class ArchiveCase:
    case_key: str
    opening_quote_number: str
    printed_addressee: str | None
    documents: tuple[ArchiveDocument, ...]

    @property
    def folder_name(self) -> str:
        return case_folder_name(self.opening_quote_number, self.printed_addressee)

    def folder_properties(self) -> dict[str, str]:
        return {PROP_KIND: KIND_CASE, PROP_CASE_KEY: self.case_key, PROP_OPENING_QUOTE: self.opening_quote_number}


@dataclass(frozen=True)
class ArchiveTarget:
    principal: str
    casos_folder_id: str
    protected_folder_ids: frozenset[str]  # Pendientes, Enviadas: no write may land under them


@dataclass
class ArchivedFile:
    document_sha256: str
    file_id: str
    name: str
    web_view_link: str | None
    outcome: str  # "uploaded" | "reused"
    drive_sha256_matches: bool
    downloaded_sha256_matches: bool
    size_matches: bool
    properties_match: bool

    @property
    def verified(self) -> bool:
        return self.drive_sha256_matches and self.downloaded_sha256_matches and self.size_matches and self.properties_match


@dataclass
class ArchiveResult:
    case_key: str
    folder_id: str | None = None
    folder_outcome: str | None = None  # "created" | "reused"
    files: list[ArchivedFile] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)

    @property
    def all_verified(self) -> bool:
        return self.folder_id is not None and all(f.verified for f in self.files)

    def archive_status(self, sha: str) -> ArchiveStatus:
        for f in self.files:
            if f.document_sha256 == sha:
                if not (f.drive_sha256_matches and f.downloaded_sha256_matches):
                    return ArchiveStatus.HASH_MISMATCH
                return ArchiveStatus.ARCHIVED_VERIFIED if f.verified else ArchiveStatus.ARCHIVED_UNVERIFIED
        return ArchiveStatus.NOT_ARCHIVED


def _kind(item: Mapping[str, Any]) -> str | None:
    return (item.get("appProperties") or {}).get(PROP_KIND)


def _prop(item: Mapping[str, Any], key: str) -> str | None:
    return (item.get("appProperties") or {}).get(key)


def _check_local(case: ArchiveCase) -> None:
    seen: set[str] = set()
    slots: set[tuple[str, int]] = set()
    if not case.documents:
        raise ArchiveRefused("empty_case", f"case {case.case_key} has no document")
    for d in case.documents:
        if not _SHA_RE.match(d.document_sha256):
            raise ArchiveRefused("bad_sha256", d.document_sha256)
        if sha256_hex(d.data) != d.document_sha256:
            raise ArchiveRefused("local_hash_mismatch", f"{d.quote_number}: bytes are not {d.document_sha256[:12]}…")
        if not d.data.startswith(b"%PDF-"):
            raise ArchiveRefused("not_pdf", f"{d.quote_number}: bytes are not a PDF")
        if d.document_sha256 in seen:
            raise ArchiveRefused("duplicate_document_in_request", d.document_sha256)
        if (d.quote_number, d.revision) in slots:
            raise ArchiveRefused("duplicate_revision_slot", f"{d.quote_number} r{d.revision}")
        seen.add(d.document_sha256)
        slots.add((d.quote_number, d.revision))
        if not app_properties_fit(d.app_properties(case.case_key)):
            raise ArchiveRefused("app_property_too_long", d.document_sha256)
    case.folder_name  # raises NameRefused early
    for d in case.documents:
        d.file_name


class _GuardedDrive:
    """Every write goes through here: a write whose parent is protected is refused."""

    def __init__(self, port: DrivePort, target: ArchiveTarget, result: ArchiveResult) -> None:
        self.port, self.target, self.result = port, target, result

    def _guard(self, parent_id: str) -> None:
        if parent_id in self.target.protected_folder_ids:
            raise ArchiveRefused("protected_parent", f"refused write under legacy folder {parent_id}", self.result)
        parent = self.port.get(parent_id)
        if parent is None or parent.get("trashed"):
            raise ArchiveRefused("parent_missing", parent_id, self.result)
        if set(parent.get("parents") or ()) & self.target.protected_folder_ids:
            raise ArchiveRefused("protected_parent", f"refused write inside legacy folder {parent_id}", self.result)

    def create_folder(self, *, name: str, parent_id: str, app_properties: Mapping[str, str]) -> dict[str, Any]:
        self._guard(parent_id)
        self.result.writes.append(f"create_folder:{name}")
        return self.port.create_folder(name=name, parent_id=parent_id, app_properties=app_properties)

    def upload_pdf(self, *, name: str, parent_id: str, app_properties: Mapping[str, str], data: bytes) -> dict[str, Any]:
        self._guard(parent_id)
        self.result.writes.append(f"upload:{name}")
        return self.port.upload_pdf(name=name, parent_id=parent_id, app_properties=app_properties, data=data)


def _journal_hit(port: DrivePort, journal: Mapping[str, str] | None, key: str) -> dict[str, Any] | None:
    if not journal or key not in journal:
        return None
    item = port.get(journal[key])
    return item if item and not item.get("trashed") else None


def _resolve_case_folder(
    port: DrivePort, drive: _GuardedDrive, case: ArchiveCase, target: ArchiveTarget,
    result: ArchiveResult, journal: MutableMapping[str, str] | None, run_id: str | None,
) -> str:
    hits = [h for h in port.find_by_property(PROP_CASE_KEY, case.case_key) if _kind(h) == KIND_CASE]
    jhit = _journal_hit(port, journal, f"case:{case.case_key}")
    if jhit and all(h["id"] != jhit["id"] for h in hits):
        hits.append(jhit)
    if len(hits) > 1:
        raise ArchiveRefused("case_key_conflict", f"{len(hits)} folders carry case {case.case_key}", result)
    if hits:
        folder = hits[0]
        if target.casos_folder_id not in (folder.get("parents") or ()):
            raise ArchiveRefused("case_folder_elsewhere", f"case {case.case_key} is filed outside Casos ({folder['id']})", result)
        result.folder_outcome = "reused"
        return folder["id"]
    same_name = [c for c in port.children(target.casos_folder_id) if c.get("name") == case.folder_name]
    if same_name:
        raise ArchiveRefused("case_name_taken", f"'{case.folder_name}' exists under Casos with another key", result)
    props = case.folder_properties() | ({PROP_RUN: run_id} if run_id else {})
    folder = drive.create_folder(name=case.folder_name, parent_id=target.casos_folder_id, app_properties=props)
    if journal is not None:
        journal[f"case:{case.case_key}"] = folder["id"]
    result.folder_outcome = "created"
    return folder["id"]


def _without_run(props: Mapping[str, str] | None) -> dict[str, str]:
    return {k: v for k, v in (props or {}).items() if k != PROP_RUN}


def _verify(port: DrivePort, file_id: str, doc: ArchiveDocument, case_key: str, outcome: str) -> ArchivedFile:
    meta = port.get(file_id) or {}
    downloaded = port.download(file_id)
    return ArchivedFile(
        document_sha256=doc.document_sha256,
        file_id=file_id,
        name=meta.get("name", ""),
        web_view_link=meta.get("webViewLink"),
        outcome=outcome,
        drive_sha256_matches=meta.get("sha256Checksum") == doc.document_sha256,
        downloaded_sha256_matches=sha256_hex(downloaded) == doc.document_sha256,
        size_matches=str(meta.get("size")) == str(len(doc.data)),
        properties_match=_without_run(meta.get("appProperties")) == doc.app_properties(case_key),
    )


def archive_case(
    port: DrivePort,
    case: ArchiveCase,
    target: ArchiveTarget,
    *,
    journal: MutableMapping[str, str] | None = None,
    run_id: str | None = None,
) -> ArchiveResult:
    """Create or reuse the case folder and put each document in it exactly once, verified.

    Idempotent: a second run with the same input writes nothing and re-verifies. ``journal``
    (``case:<key>`` / ``doc:<sha>`` → Drive id) is filled as writes happen so a retry finds
    what Drive's search index may not show yet. ``run_id`` is stamped on created items only, so
    a rollback can trash exactly what one run made. Raises :class:`ArchiveRefused`, whose
    ``partial`` result lists every id already written.
    """
    result = ArchiveResult(case_key=case.case_key)
    _check_local(case)

    who = port.principal()
    if who != target.principal:
        raise ArchiveRefused("wrong_account", f"Drive principal is {who}, expected {target.principal}", result)
    if target.casos_folder_id in target.protected_folder_ids:
        raise ArchiveRefused("protected_parent", "Casos is configured as a legacy folder", result)
    casos = port.get(target.casos_folder_id)
    if not casos or casos.get("trashed") or casos.get("mimeType") != FOLDER_MIME or _kind(casos) != KIND_CASE_ROOT:
        raise ArchiveRefused("casos_invalid", f"{target.casos_folder_id} is not the tagged, live case root", result)

    drive = _GuardedDrive(port, target, result)
    result.folder_id = _resolve_case_folder(port, drive, case, target, result, journal, run_id)

    for doc in case.documents:
        hits = [h for h in port.find_by_property(PROP_DOC_SHA, doc.document_sha256) if _kind(h) == KIND_REVISION]
        jhit = _journal_hit(port, journal, f"doc:{doc.document_sha256}")
        if jhit and all(h["id"] != jhit["id"] for h in hits):
            hits.append(jhit)
        elsewhere = [h for h in hits if _prop(h, PROP_CASE_KEY) != case.case_key or result.folder_id not in (h.get("parents") or ())]
        if elsewhere:
            raise ArchiveRefused("document_filed_elsewhere",
                                 f"{doc.document_sha256[:12]}… already filed as {elsewhere[0]['id']}", result)
        if len(hits) > 1:
            raise ArchiveRefused("document_duplicate_in_case", f"{len(hits)} files carry {doc.document_sha256[:12]}…", result)
        if hits:
            file_id, outcome = hits[0]["id"], "reused"
        else:
            clash = [c for c in port.children(result.folder_id) if c.get("name") == doc.file_name]
            if clash:
                raise ArchiveRefused("file_name_taken", f"'{doc.file_name}' exists in the case folder with other bytes", result)
            props = doc.app_properties(case.case_key) | ({PROP_RUN: run_id} if run_id else {})
            up = drive.upload_pdf(name=doc.file_name, parent_id=result.folder_id, app_properties=props, data=doc.data)
            file_id, outcome = up["id"], "uploaded"
            if journal is not None:
                journal[f"doc:{doc.document_sha256}"] = file_id
        checked = _verify(port, file_id, doc, case.case_key, outcome)
        result.files.append(checked)
        if not (checked.drive_sha256_matches and checked.downloaded_sha256_matches):
            raise ArchiveRefused("hash_mismatch", f"{file_id}: Drive bytes are not {doc.document_sha256[:12]}… — left in place", result)
        if not checked.verified:
            raise ArchiveRefused("verification_failed", f"{file_id}: size or properties differ", result)
    return result


# ── Linkage: what the CRM would record, never recorded here ─────────────────────────────


def archive_link_rows(case: ArchiveCase, result: ArchiveResult, *, crm_status: CrmStatus,
                      roles: Mapping[str, DocumentRole] | None = None) -> list[dict[str, Any]]:
    """One row per document — the local archive ledger's shape until the CRM holds it."""
    by_sha = {f.document_sha256: f for f in result.files}
    rows = []
    for d in case.documents:
        f = by_sha.get(d.document_sha256)
        role = (roles or {}).get(d.document_sha256, DocumentRole.QUOTATION)
        rows.append({
            "archive_version": ARCHIVE_VERSION,
            "case_key": case.case_key,
            "quote_number": d.quote_number,
            "revision": d.revision,
            "document_sha256": d.document_sha256,
            "gmail_message_id": d.gmail_message_id,
            "original_filename": d.original_filename,
            "legacy_file_id": d.legacy_file_id,
            "drive_folder_id": result.folder_id,
            "drive_file_id": f.file_id if f else None,
            "drive_web_view_link": f.web_view_link if f else None,
            "archive_status": result.archive_status(d.document_sha256).value,
            "crm_status": crm_status.value,
            "lifecycle": document_lifecycle(role, crm_status, revision=d.revision).value,
        })
    return rows


def crm_link_intents(
    rows: Iterable[Mapping[str, Any]],
    *,
    crm_opportunity_id: str | None,
    quote_revision_ids: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Describe the CRM rows that would record an archive link. Writes nothing, approves nothing.

    - ``crm.external_identifier`` (scheme ``drive_folder``) → the opportunity, once it exists.
    - ``evidence.source_record`` (kind ``drive_file``, dedupe ``drive_file:<id>``) per file.
    - ``evidence.assertion`` (``document_reference``, ``sha256:<hex>``) linked to the
      ``quote_revision`` whose ``pdf_sha256`` is these bytes — or left unresolved.
    An opportunity not in the CRM yields one deferred intent and nothing else.
    """
    rows = list(rows)
    if not rows:
        return []
    case_key = rows[0]["case_key"]
    if crm_opportunity_id is None:
        return [{"intent": "deferred", "case_key": case_key, "reason": "opportunity_not_in_crm",
                 "idempotency_key": f"qdrive:deferred:{case_key}"}]
    folder_ids = {r["drive_folder_id"] for r in rows}
    if len(folder_ids) != 1 or None in folder_ids:
        raise ValueError(f"case {case_key}: rows disagree on the Drive folder {sorted(map(str, folder_ids))}")
    intents: list[dict[str, Any]] = [{
        "intent": "external_identifier", "idempotency_key": f"qdrive:folder:{case_key}",
        "scheme": "drive_folder", "value_norm": rows[0]["drive_folder_id"], "opportunity_id": crm_opportunity_id,
        "effect": "archive_link_only",
    }]
    for r in rows:
        if r["archive_status"] != ArchiveStatus.ARCHIVED_VERIFIED.value or not r["drive_file_id"]:
            intents.append({"intent": "skipped", "idempotency_key": f"qdrive:skip:{r['document_sha256']}",
                            "reason": f"archive_status={r['archive_status']}"})
            continue
        payload = {k: r[k] for k in ("case_key", "quote_number", "revision", "document_sha256", "gmail_message_id",
                                     "original_filename", "legacy_file_id", "drive_folder_id", "drive_file_id")}
        revision_id = (quote_revision_ids or {}).get(r["document_sha256"])
        intents.append({
            "intent": "drive_file_evidence", "idempotency_key": f"qdrive:file:{r['document_sha256']}",
            "source_record": {"kind": "drive_file", "dedupe_key": f"drive_file:{r['drive_file_id']}",
                              "source_uri": r["drive_web_view_link"], "payload": payload},
            "assertion": {"kind": "document_reference", "value_norm": f"sha256:{r['document_sha256']}",
                          "resolution": "linked" if revision_id else "unresolved",
                          "resolved_kind": "quote_revision" if revision_id else None, "resolved_id": revision_id},
            "effect": "archive_link_only",
        })
    return intents


# ── Check mode against the live Drive: reads go through, writes stay in memory ─────────


class OverlayDrive:
    """Wraps a real :class:`DrivePort`. Every read reaches the real Drive; every write is recorded
    here and never forwarded, and later reads see those in-memory items as if they existed. Running
    :func:`archive_case` through it shows exactly what a real run would create, with zero writes."""

    def __init__(self, real: DrivePort) -> None:
        self.real = real
        self.created: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.would_write: list[str] = []

    def principal(self) -> str:
        return self.real.principal()

    def get(self, file_id: str) -> dict[str, Any] | None:
        return dict(self.created[file_id]) if file_id in self.created else self.real.get(file_id)

    def find_by_property(self, key: str, value: str) -> list[dict[str, Any]]:
        mine = [dict(i) for i in self.created.values() if (i.get("appProperties") or {}).get(key) == value]
        return self.real.find_by_property(key, value) + mine

    def children(self, folder_id: str) -> list[dict[str, Any]]:
        mine = [dict(i) for i in self.created.values() if folder_id in i["parents"]]
        real = [] if folder_id in self.created else self.real.children(folder_id)
        return real + mine

    def _new(self, **item: Any) -> dict[str, Any]:
        fid = f"overlay-{len(self.created) + 1:04d}"
        self.created[fid] = {"id": fid, "trashed": False, "webViewLink": None, **item}
        return self.created[fid]

    def create_folder(self, *, name: str, parent_id: str, app_properties: Mapping[str, str]) -> dict[str, Any]:
        self.would_write.append(f"create_folder:{parent_id}/{name}")
        return dict(self._new(name=name, mimeType=FOLDER_MIME, parents=[parent_id], appProperties=dict(app_properties)))

    def upload_pdf(self, *, name: str, parent_id: str, app_properties: Mapping[str, str], data: bytes) -> dict[str, Any]:
        self.would_write.append(f"upload:{parent_id}/{name}")
        item = self._new(name=name, mimeType=PDF_MIME, parents=[parent_id], appProperties=dict(app_properties),
                         size=str(len(data)), sha256Checksum=sha256_hex(data))
        self.blobs[item["id"]] = data
        return {"id": item["id"]}

    def download(self, file_id: str) -> bytes:
        return self.blobs[file_id] if file_id in self.blobs else self.real.download(file_id)
