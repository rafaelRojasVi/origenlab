"""Legacy Drive reconciliation and case-first migration plan — dry run only.

Input: a read-only inventory of ``Cotizaciones`` (Pendientes, Enviadas, Casos) and the local
facts about each document (ledger decision, printed number, CRM import plan). Output: one
row per legacy item saying what a case-first migration *would* do with it, the cases it
would create or reuse, every duplicate and collision found, and a rehearsal against an
in-memory copy of Drive. Nothing here reaches Drive, the CRM, Gmail or SQLite.

Rules (conservative, reversible):

- **Legacy is never touched.** Every Pendientes/Enviadas item gets ``legacy_action =
  untouched``. A migration only *adds* copies under ``Casos``; retiring the legacy folders is a
  separate, later approval.
- **Only a ledger-confirmed document joins a case.** Its case is the ledger's planned
  opportunity id; its quote number and revision come from the import plan (print wins).
  Owner-blocked documents, unreviewed PDFs, a Drive PDF whose bytes differ from the sent
  one, brochures, spreadsheets and files without a Drive checksum are reported, not copied.
- **CRM status never decides placement.** Ready, waiting-for-organization and held cases are
  all archived the same way; the status is reported beside the row, never used in a path.
- **Bytes are identity.** The same SHA-256 in several legacy folders is copied once; a SHA
  already in ``Casos`` is linked, not copied again.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from origenlab_api.v2 import quote_case_archive as qa

MIGRATION_VERSION = "qcase-legacy-migration/2026-09-26.1"
LEGACY_ROOTS = ("Pendientes", "Enviadas")

# Classifications of one legacy item.
C_FOLDER = "folder"
C_CONFIRMED = "confirmed_quotation"
C_PENDING_DOC = "pending_document"
C_BLOCKED = "owner_blocked_document"
C_REJECTED = "rejected_non_quotation"
C_OTHER_VERSION = "unledgered_version_of_known_quote"
C_EMAIL_ONLY = "email_confirmed_not_in_document_ledger"
C_UNREVIEWED = "unreviewed_quotation"
C_ATTACHMENT = "supporting_attachment"
C_WORKING = "working_file"
C_NO_CHECKSUM = "no_drive_checksum"
C_UNKNOWN = "unknown_document"

# Actions on the *case copy* (the legacy item itself is always untouched).
A_COPY = "copy_into_case"
A_LINK = "link_existing_case_file"
A_DUPLICATE = "covered_by_duplicate"
A_NONE = "not_archived"
A_BLOCKED = "blocked"
# A confirmed plan document that is in no Drive folder: uploaded from verified local (Gmail) bytes.
# Not a legacy migration step — it needs its own authorization (reported apart, rehearsed apart).
SOURCE_LEGACY = "legacy_drive"
SOURCE_GMAIL = "gmail_bytes"

_NUMBER_RE = re.compile(r"^0*(?P<serial>\d+)(?P<suffix>[A-Za-z]*)(?:-(?P<year>\d{2}))?$")
_FILENAME_CN_RE = re.compile(r"(?i)\bCN\s*0*(?P<serial>\d{1,7})")
_PDF = "application/pdf"


def number_key(quote_number: str | None) -> str | None:
    """'01005-26' and '1005-26' are one printed number; the letter suffix is kept."""
    if not quote_number:
        return None
    m = _NUMBER_RE.match(quote_number.strip())
    if not m:
        return quote_number.strip().upper()
    return f"{int(m['serial'])}{m['suffix'].upper()}" + (f"-{m['year']}" if m["year"] else "")


def number_serial(quote_number: str | None) -> int | None:
    m = _NUMBER_RE.match((quote_number or "").strip())
    return int(m["serial"]) if m else None


def filename_serial(name: str) -> int | None:
    m = _FILENAME_CN_RE.search(name)
    return int(m["serial"]) if m else None


@dataclass(frozen=True)
class KnownDocument:
    """What the local evidence says about one SHA-256. Every field is optional."""

    sha256: str
    ledger_decision: str | None = None  # confirm_customer_quotation | reject_non_quotation | leave_pending
    planned_opportunity_id: str | None = None
    ledger_quote_number: str | None = None
    gmail_message_ids: tuple[str, ...] = ()
    email_ids: tuple[int, ...] = ()
    original_filename: str | None = None
    identity_class: str | None = None  # quotation | brochure_or_generic_document | insufficient_evidence
    printed_quote_number: str | None = None
    printed_addressee: str | None = None
    blocked_reasons: tuple[str, ...] = ()
    local_bytes: bool = False
    email_decision: str | None = None  # decisions.jsonl (historical, email-level)


@dataclass(frozen=True)
class PlanCase:
    """One opportunity of the CRM import plan, reduced to what placement needs."""

    case_key: str
    status: str
    printed_addressee: str | None
    revisions: tuple[tuple[str, str, str], ...]  # (quote_number, document_sha256, sent_at) in plan order
    reasons: tuple[str, ...] = ()

    def ordered(self) -> list[tuple[str, int, str, str]]:
        """(quote_number, revision_no, sha, sent_at): revisions of one number ordered by sent time."""
        by_quote: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for q, s, t in self.revisions:
            by_quote[q].append((t, s))
        out = []
        for q, revs in by_quote.items():
            for i, (t, s) in enumerate(sorted(revs), start=1):
                out.append((q, i, s, t))
        return sorted(out, key=lambda r: (r[3], r[0], r[1]))

    @property
    def opening_quote_number(self) -> str:
        return self.ordered()[0][0]


def plan_cases(plan: Mapping[str, Any]) -> dict[str, PlanCase]:
    cases = {}
    for o in plan["opportunities"]:
        revs = tuple((q["quote_number"], r["document_sha256"], r["sent_at"]) for q in o["quotes"] for r in q["revisions"])
        if revs:
            cases[o["planned_opportunity_id"]] = PlanCase(o["planned_opportunity_id"], o["status"], o.get("printed_addressee"),
                                                          revs, tuple(o.get("reasons") or ()))
    return cases


@dataclass
class LegacyRow:
    root: str
    path: str
    drive_id: str
    name: str
    mime_type: str
    legacy_folder_id: str | None
    legacy_folder_name: str | None
    sha256: str | None
    size: int | None
    classification: str
    action: str
    reason: str
    legacy_action: str = "untouched"
    case_key: str | None = None
    plan_status: str | None = None
    crm_status: str | None = None
    lifecycle: str | None = None
    quote_number: str | None = None
    revision: int | None = None
    printed_quote_number: str | None = None
    filename_serial: int | None = None
    target_folder: str | None = None
    target_file: str | None = None
    renamed_in_case_copy: bool | None = None
    gmail_message_ids: tuple[str, ...] = ()
    email_ids: tuple[int, ...] = ()
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def _legacy_items(inventory: Mapping[str, Any]) -> list[tuple[str, dict]]:
    return [(root, f) for root in LEGACY_ROOTS for f in inventory["trees"].get(root, [])]


def _casos_index(inventory: Mapping[str, Any]) -> tuple[dict[str, dict], dict[str, dict]]:
    folders, files = {}, {}
    for f in inventory["trees"].get("Casos", []):
        props = f.get("appProperties") or {}
        if props.get(qa.PROP_KIND) == qa.KIND_CASE:
            folders[props[qa.PROP_CASE_KEY]] = f
        elif props.get(qa.PROP_KIND) == qa.KIND_REVISION:
            files[props[qa.PROP_DOC_SHA]] = f
    return folders, files


def _classify(f: dict, doc: KnownDocument | None, ledger_numbers: Mapping[str, set[str]]) -> tuple[str, str]:
    if f["mimeType"] == qa.FOLDER_MIME:
        return C_FOLDER, "legacy folder"
    if f["mimeType"] != _PDF:
        return C_WORKING, f"not a PDF ({f['mimeType']}): working file, not a sent document"
    if not f.get("sha256Checksum"):
        return C_NO_CHECKSUM, "Drive reports no sha256Checksum; identity cannot be proven without a download"
    if doc is None:
        return C_UNKNOWN, "no local evidence for these bytes"
    if doc.blocked_reasons:
        return C_BLOCKED, "owner-blocked: " + ", ".join(doc.blocked_reasons)
    if doc.ledger_decision == "reject_non_quotation":
        return C_REJECTED, "ledger: not a quotation"
    if doc.ledger_decision == "leave_pending":
        return C_PENDING_DOC, "ledger: document left pending"
    if doc.ledger_decision == "confirm_customer_quotation":
        return C_CONFIRMED, "ledger-confirmed quotation"
    if doc.identity_class == "brochure_or_generic_document":  # before the email decision, which covers every attachment
        return C_ATTACHMENT, "brochure or generic attachment"
    if doc.email_decision == "confirm_customer_quotation":
        return C_EMAIL_ONLY, ("confirmed only by an email-level decision; no document-level row exists, "
                              "so the CRM import plan omits it")
    if doc.email_decision == "reject_non_quotation":
        return C_REJECTED, "email-level ledger: not a quotation"
    key = number_key(doc.printed_quote_number)
    if key and key in ledger_numbers:
        return C_OTHER_VERSION, (f"prints {doc.printed_quote_number}, confirmed in the ledger with other bytes "
                                 f"({', '.join(sorted(s[:12] for s in ledger_numbers[key]))}) — draft or unsent version?")
    if doc.printed_quote_number or doc.identity_class == "quotation":
        return C_UNREVIEWED, "quotation bytes never reviewed in the ledger"
    return C_UNKNOWN, "no printed quote number and no ledger decision"


@dataclass
class MigrationPlan:
    rows: list[LegacyRow]
    cases: list[dict[str, Any]]
    collisions: list[dict[str, Any]]
    duplicates: list[dict[str, Any]]
    folders: list[dict[str, Any]]
    summary: dict[str, Any]
    gmail_only: list[dict[str, Any]] = field(default_factory=list)

    def archive_cases(self, bytes_for: Callable[[str], bytes | None]) -> tuple[list[qa.ArchiveCase], list[str]]:
        """The ArchiveCase inputs a real run would use; shas with no local bytes are listed."""
        out, missing = [], []
        for c in self.cases:
            if not c["documents"]:
                continue
            docs = []
            for d in c["documents"]:
                data = bytes_for(d["document_sha256"])
                if data is None:
                    missing.append(d["document_sha256"])
                    continue
                docs.append(qa.ArchiveDocument(d["document_sha256"], d["quote_number"], d["revision"], d["original_filename"],
                                               data, d.get("gmail_message_id"), d.get("legacy_file_id")))
            if docs:
                out.append(qa.ArchiveCase(c["case_key"], c["opening_quote_number"], c["printed_addressee"], tuple(docs)))
        return out, missing


def build_plan(inventory: Mapping[str, Any], known: Mapping[str, KnownDocument], cases: Mapping[str, PlanCase],
               *, include_gmail_only: bool = True) -> MigrationPlan:
    casos_folders, casos_files = _casos_index(inventory)
    ledger_numbers: dict[str, set[str]] = defaultdict(set)
    for d in known.values():
        if d.ledger_decision == "confirm_customer_quotation" and d.ledger_quote_number:
            ledger_numbers[number_key(d.ledger_quote_number)].add(d.sha256)
    placement: dict[str, tuple[str, str, int]] = {}  # sha → (case_key, quote_number, revision)
    for c in cases.values():
        for q, rev, s, _t in c.ordered():
            placement[s] = (c.case_key, q, rev)

    items = _legacy_items(inventory)
    by_id = {f["id"]: f for _, f in items}
    rows: list[LegacyRow] = []
    copied: dict[str, LegacyRow] = {}
    for root, f in items:
        s = f.get("sha256Checksum")
        doc = known.get(s) if s else None
        cls, reason = _classify(f, doc, ledger_numbers)
        parent = by_id.get((f.get("parents") or [None])[0])
        row = LegacyRow(
            root=root, path=f["path"], drive_id=f["id"], name=f["name"], mime_type=f["mimeType"],
            legacy_folder_id=parent["id"] if parent else None, legacy_folder_name=parent["name"] if parent else None,
            sha256=s, size=int(f["size"]) if f.get("size") else None, classification=cls, action=A_NONE, reason=reason,
            printed_quote_number=doc.printed_quote_number if doc else None, filename_serial=filename_serial(f["name"]),
            gmail_message_ids=doc.gmail_message_ids if doc else (), email_ids=doc.email_ids if doc else (),
        )
        if cls == C_FOLDER:
            row.action = "folder_untouched"
        elif cls in (C_CONFIRMED, C_PENDING_DOC):
            placed = placement.get(s) if doc and doc.planned_opportunity_id else None
            if placed is None or placed[0] != doc.planned_opportunity_id:
                row.action, row.reason = A_BLOCKED, "ledger case not in the import plan (or disagrees with it)"
            else:
                case_key, quote, rev = placed
                plan_case = cases[case_key]
                row.case_key, row.quote_number, row.revision = case_key, quote, rev
                row.plan_status = plan_case.status
                crm = qa.crm_status_from_plan(plan_case.status)
                role = qa.DocumentRole.PENDING_DOCUMENT if cls == C_PENDING_DOC else qa.DocumentRole.QUOTATION
                row.crm_status, row.lifecycle = crm.value, qa.document_lifecycle(role, crm, revision=rev).value
                row.target_folder = qa.case_folder_name(plan_case.opening_quote_number, plan_case.printed_addressee)
                row.target_file = qa.revision_file_name(quote, rev, doc.original_filename or f["name"])
                row.renamed_in_case_copy = row.target_file != f["name"]
                if s in casos_files:
                    row.action, row.reason = A_LINK, f"already archived in Casos as {casos_files[s]['id']}"
                elif s in copied:
                    row.action, row.reason = A_DUPLICATE, f"same bytes as {copied[s].path}; copied once"
                    row.flags.append("duplicate_pdf")
                    copied[s].flags.append("duplicate_pdf")
                else:
                    row.action = A_COPY
                    copied[s] = row
                if row.filename_serial is not None and row.filename_serial != number_serial(quote):
                    row.flags.append("filename_number_mismatch")
        elif cls in (C_BLOCKED, C_OTHER_VERSION, C_EMAIL_ONLY, C_UNREVIEWED, C_UNKNOWN, C_NO_CHECKSUM):
            row.action = A_BLOCKED
            row.lifecycle = qa.Lifecycle.HELD.value if cls == C_BLOCKED else qa.Lifecycle.UNKNOWN_NEEDS_REVIEW.value
        elif cls == C_REJECTED:
            row.lifecycle = qa.Lifecycle.REJECTED_NON_QUOTATION.value
        rows.append(row)

    # Duplicates: same bytes in several legacy places, or already in Casos.
    by_sha: dict[str, list[LegacyRow]] = defaultdict(list)
    for r in rows:
        if r.sha256:
            by_sha[r.sha256].append(r)
    duplicates = [{"sha256": s, "paths": [r.path for r in rs], "classification": rs[0].classification,
                   "in_casos": s in casos_files}
                  for s, rs in sorted(by_sha.items()) if len(rs) > 1 or s in casos_files]
    for d in duplicates:
        for r in by_sha[d["sha256"]]:
            if len(by_sha[d["sha256"]]) > 1 and "duplicate_pdf" not in r.flags:
                r.flags.append("duplicate_pdf")

    # Number collisions: one printed number, several cases or unplaced documents.
    # A number is a collision when it spans several cases, or when several PDFs carry it and at
    # least one of them belongs to no case. Several revisions inside one case are not a collision.
    by_number: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cases": set(), "shas": set(), "unplaced": set(), "addressees": set(), "paths": []})
    for d in known.values():
        n = number_key(d.ledger_quote_number or d.printed_quote_number)
        if n and d.identity_class != "brochure_or_generic_document" and d.ledger_decision != "reject_non_quotation":
            by_number[n]["shas"].add(d.sha256)
            if d.printed_addressee:
                by_number[n]["addressees"].add(d.printed_addressee)
            if d.planned_opportunity_id and d.ledger_decision == "confirm_customer_quotation":
                by_number[n]["cases"].add(d.planned_opportunity_id)
            else:
                by_number[n]["unplaced"].add(d.sha256)
    for r in rows:
        n = number_key(r.quote_number or r.printed_quote_number)
        if n in by_number:
            by_number[n]["paths"].append(r.path)
    collisions = []
    for n, v in sorted(by_number.items()):
        if len(v["cases"]) > 1 or (len(v["shas"]) > 1 and v["unplaced"]):
            collisions.append({"number": n, "cases": sorted(v["cases"]), "documents": sorted(v["shas"]),
                               "unplaced_documents": sorted(v["unplaced"]), "printed_addressees": sorted(v["addressees"]),
                               "legacy_paths": sorted(set(v["paths"])),
                               "kind": "several_cases" if len(v["cases"]) > 1 else "unplaced_versions"})
    collided = {c["number"] for c in collisions}
    for r in rows:
        if number_key(r.quote_number or r.printed_quote_number) in collided:
            r.flags.append("number_collision")

    # Cases the copy would create or reuse.
    case_docs: dict[str, dict[str, dict]] = defaultdict(dict)
    case_folders_legacy: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r.case_key and r.action in (A_COPY, A_LINK, A_DUPLICATE):
            case_folders_legacy[r.case_key].add(r.legacy_folder_name or "")
        if r.action == A_COPY:
            doc = known[r.sha256]
            case_docs[r.case_key][r.sha256] = {
                "document_sha256": r.sha256, "quote_number": r.quote_number, "revision": r.revision,
                "original_filename": doc.original_filename or r.name, "legacy_name": r.name, "legacy_file_id": r.drive_id,
                "gmail_message_id": doc.gmail_message_ids[0] if doc.gmail_message_ids else None,
                "target_file": r.target_file, "source": SOURCE_LEGACY,
            }
    legacy_shas = {r.sha256 for r in rows if r.sha256}
    gmail_only: list[dict[str, Any]] = []
    for c in cases.values():
        for q, rev, s, _t in c.ordered():
            if s in legacy_shas or s in casos_files:
                continue
            doc = known.get(s)
            entry = {"case_key": c.case_key, "document_sha256": s, "quote_number": q, "revision": rev,
                     "plan_status": c.status, "gmail_message_id": (doc.gmail_message_ids[0] if doc and doc.gmail_message_ids else None)}
            if doc is None or doc.ledger_decision != "confirm_customer_quotation" or doc.planned_opportunity_id != c.case_key:
                entry["action"], entry["reason"] = A_BLOCKED, "plan document without a matching ledger confirmation"
            else:
                name = qa.revision_file_name(q, rev, doc.original_filename or f"{q}.pdf")
                entry |= {"action": "upload_from_gmail_bytes", "target_file": name,
                          "target_folder": qa.case_folder_name(c.opening_quote_number, c.printed_addressee)}
                if include_gmail_only:
                    case_docs[c.case_key][s] = {
                        "document_sha256": s, "quote_number": q, "revision": rev,
                        "original_filename": doc.original_filename or f"{q}.pdf", "legacy_name": None, "legacy_file_id": None,
                        "gmail_message_id": entry["gmail_message_id"], "target_file": name, "source": SOURCE_GMAIL,
                    }
            gmail_only.append(entry)
    case_rows = []
    for key in sorted(set(case_docs) | set(case_folders_legacy)):
        c = cases[key]
        placed = {s for _q, _r, s, _t in c.ordered()}
        in_legacy = {r.sha256 for r in rows if r.case_key == key}
        case_rows.append({
            "case_key": key, "plan_status": c.status, "crm_status": qa.crm_status_from_plan(c.status).value,
            "plan_reasons": list(c.reasons),
            "opening_quote_number": c.opening_quote_number, "printed_addressee": c.printed_addressee,
            "folder_name": qa.case_folder_name(c.opening_quote_number, c.printed_addressee),
            "folder_action": "reuse_case_folder" if key in casos_folders else "create_case_folder",
            "existing_folder_id": casos_folders[key]["id"] if key in casos_folders else None,
            "documents": sorted(case_docs[key].values(), key=lambda d: (d["quote_number"], d["revision"])),
            "documents_from_legacy": sum(d["source"] == SOURCE_LEGACY for d in case_docs[key].values()),
            "documents_from_gmail": sum(d["source"] == SOURCE_GMAIL for d in case_docs[key].values()),
            "legacy_folders": sorted(case_folders_legacy[key]),
            "plan_documents_not_in_legacy": sorted(placed - in_legacy),
            "flags": (["case_spans_legacy_folders"] if len(case_folders_legacy[key]) > 1 else [])
                     + (["case_has_several_quotes"] if len({q for q, _r, _s, _t in c.ordered()}) > 1 else [])
                     + (["case_has_revisions"] if any(r > 1 for _q, r, _s, _t in c.ordered()) else []),
        })

    # Legacy folders: what happens to each (always untouched), and which cases it feeds.
    folder_rows = []
    for r in rows:
        if r.classification != C_FOLDER:
            continue
        kids = [k for k in rows if k.legacy_folder_id == r.drive_id]
        keys = sorted({k.case_key for k in kids if k.case_key})
        outcome = ("fully_mapped" if kids and all(k.action in (A_COPY, A_LINK, A_DUPLICATE) for k in kids) else
                   "partially_mapped" if keys else "nothing_mapped")
        if len(keys) > 1:
            r.flags.append("legacy_folder_spans_cases")
        r.reason = f"{outcome}: {len(kids)} item(s), {len(keys)} case(s)"
        folder_rows.append({"root": r.root, "folder": r.name, "drive_id": r.drive_id, "legacy_action": "untouched",
                            "outcome": outcome, "cases": keys, "items": len(kids),
                            "actions": dict(sorted(_count(k.action for k in kids).items())),
                            "flags": r.flags})

    summary = {
        "migration_version": MIGRATION_VERSION,
        "legacy_items": {root: _count(r.classification for r in rows if r.root == root) for root in LEGACY_ROOTS},
        "legacy_item_totals": {root: sum(1 for r in rows if r.root == root) for root in LEGACY_ROOTS},
        "actions": _count(r.action for r in rows),
        "cases_to_create": sum(1 for c in case_rows if c["folder_action"] == "create_case_folder" and c["documents"]),
        "cases_to_reuse": sum(1 for c in case_rows if c["folder_action"] == "reuse_case_folder" and c["documents"]),
        "files_to_copy": sum(c["documents_from_legacy"] for c in case_rows),
        "files_to_upload_from_gmail_bytes": sum(c["documents_from_gmail"] for c in case_rows),
        "gmail_only_blocked": sum(1 for g in gmail_only if g["action"] == A_BLOCKED),
        "cases_only_from_gmail": sum(1 for c in case_rows if c["documents"] and not c["documents_from_legacy"]),
        "cases_by_crm_status": _count(c["crm_status"] for c in case_rows if c["documents"]),
        "legacy_folders": _count(f["outcome"] for f in folder_rows),
        "collisions": len(collisions), "duplicate_groups": len(duplicates),
        "legacy_writes": 0,
    }
    return MigrationPlan(rows, case_rows, collisions, duplicates, folder_rows, summary, gmail_only)


def _count(values: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[str(v)] += 1
    return dict(out)


# ── Rehearsal against an in-memory Drive seeded from the inventory ─────────────────────


class SimulatedDrive:
    """A DrivePort over the inventory's metadata. Writes land in memory only; trash is a flag."""

    def __init__(self, inventory: Mapping[str, Any], *, blobs: Mapping[str, bytes] | None = None) -> None:
        self.who = inventory["principal"]
        self.items: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.writes: list[str] = []
        root = inventory["cotizaciones"]
        self.items[root["id"]] = dict(root, appProperties=root.get("appProperties") or {})
        for tree in inventory["trees"].values():
            for f in tree:
                self.items[f["id"]] = {**f, "appProperties": dict(f.get("appProperties") or {}), "trashed": bool(f.get("trashed"))}
        for c in inventory["cotizaciones_children"]:
            self.items.setdefault(c["id"], {**c, "appProperties": dict(c.get("appProperties") or {}), "trashed": False})
        for fid, item in self.items.items():  # existing files: serve their bytes when the caller has them
            s = item.get("sha256Checksum")
            if s and blobs and s in blobs:
                self.blobs[fid] = blobs[s]
        self._n = 0

    def principal(self) -> str:
        return self.who

    def get(self, file_id: str) -> dict | None:
        return dict(self.items[file_id]) if file_id in self.items else None

    def find_by_property(self, key: str, value: str) -> list[dict]:
        return [dict(i) for i in self.items.values() if not i.get("trashed") and (i.get("appProperties") or {}).get(key) == value]

    def children(self, folder_id: str) -> list[dict]:
        return [dict(i) for i in self.items.values() if folder_id in (i.get("parents") or ()) and not i.get("trashed")]

    def _new(self, **item: Any) -> dict:
        self._n += 1
        fid = f"sim-{self._n:04d}"
        self.items[fid] = {"id": fid, "trashed": False, "webViewLink": None, **item}
        return self.items[fid]

    def create_folder(self, *, name: str, parent_id: str, app_properties: Mapping[str, str]) -> dict:
        self.writes.append(f"create_folder:{name}")
        return dict(self._new(name=name, mimeType=qa.FOLDER_MIME, parents=[parent_id], appProperties=dict(app_properties)))

    def upload_pdf(self, *, name: str, parent_id: str, app_properties: Mapping[str, str], data: bytes) -> dict:
        self.writes.append(f"upload:{name}")
        item = self._new(name=name, mimeType=_PDF, parents=[parent_id], appProperties=dict(app_properties),
                         size=str(len(data)), sha256Checksum=hashlib.sha256(data).hexdigest())
        self.blobs[item["id"]] = data
        return {"id": item["id"]}

    def download(self, file_id: str) -> bytes:
        return self.blobs[file_id]

    def trash_run(self, run_id: str) -> list[str]:
        """Rollback: trash (never delete) exactly the items one run created."""
        ids = [i["id"] for i in self.find_by_property(qa.PROP_RUN, run_id)]
        for fid in ids:
            self.items[fid]["trashed"] = True
            self.writes.append(f"trash:{fid}")
        return ids

    def fingerprint(self, root_id: str) -> str:
        """Recursive fingerprint of one folder's live subtree (the legacy-untouched proof)."""
        rows, frontier = [], [root_id]
        while frontier:
            parent = frontier.pop()
            for c in self.children(parent):
                rows.append({k: c.get(k) for k in ("id", "name", "parents", "sha256Checksum", "appProperties", "trashed")})
                if c.get("mimeType") == qa.FOLDER_MIME:
                    frontier.append(c["id"])
        rows.sort(key=lambda r: r["id"])
        return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rehearse(inventory: Mapping[str, Any], plan: MigrationPlan, bytes_for: Callable[[str], bytes | None],
             *, run_id: str) -> dict[str, Any]:
    """Run the real archiver against a SimulatedDrive: first run, rerun, rollback. Legacy fingerprints before/after."""
    archive_cases, missing = plan.archive_cases(bytes_for)
    blobs = {c.documents[i].document_sha256: c.documents[i].data for c in archive_cases for i in range(len(c.documents))}
    sim = SimulatedDrive(inventory, blobs=blobs)
    names = {c["name"]: c["id"] for c in inventory["cotizaciones_children"]}
    target = qa.ArchiveTarget(sim.who, names["Casos"], frozenset(names[n] for n in LEGACY_ROOTS if n in names))
    legacy_before = {n: sim.fingerprint(names[n]) for n in LEGACY_ROOTS if n in names}
    casos_before = sim.fingerprint(names["Casos"])

    def run(rid: str) -> tuple[list[dict], list[dict]]:
        ok, refused = [], []
        for c in archive_cases:
            try:
                r = qa.archive_case(sim, c, target, run_id=rid)
                ok.append({"case_key": c.case_key, "folder_outcome": r.folder_outcome, "writes": len(r.writes),
                           "files": [f.outcome for f in r.files], "verified": r.all_verified})
            except qa.ArchiveRefused as e:
                refused.append({"case_key": c.case_key, "code": e.code, "detail": e.detail})
        return ok, refused

    first_ok, first_refused = run(run_id)
    writes_first = len(sim.writes)
    second_ok, second_refused = run(run_id + "-rerun")
    writes_second = len(sim.writes) - writes_first
    legacy_mid = {n: sim.fingerprint(i) for n, i in ((n, names[n]) for n in LEGACY_ROOTS if n in names)}
    trashed = sim.trash_run(run_id)
    casos_after_rollback = sim.fingerprint(names["Casos"])
    legacy_after = {n: sim.fingerprint(names[n]) for n in LEGACY_ROOTS if n in names}
    return {
        "run_id": run_id,
        "cases_attempted": len(archive_cases), "documents_without_local_bytes": missing,
        "first_run": {"writes": writes_first, "cases_ok": len(first_ok), "refused": first_refused,
                      "all_verified": all(o["verified"] for o in first_ok),
                      "folders_created": sum(o["folder_outcome"] == "created" for o in first_ok),
                      "folders_reused": sum(o["folder_outcome"] == "reused" for o in first_ok),
                      "files_uploaded": sum(f == "uploaded" for o in first_ok for f in o["files"])},
        "rerun": {"writes": writes_second, "refused": second_refused},
        "rollback": {"trashed": len(trashed), "casos_restored": casos_after_rollback == casos_before,
                     "method": f"trash items whose appProperties.{qa.PROP_RUN} = run id; never delete"},
        "legacy_unchanged": legacy_before == legacy_mid == legacy_after,
        "legacy_fingerprints": legacy_before,
        "write_log_sample": sim.writes[:12],
    }
