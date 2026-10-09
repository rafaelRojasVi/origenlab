"""File every recorded quote PDF in its case's Drive folder, and record where it went.

`origenlab-worker drive-file` runs after `gmail-sync` in the same cron. It closes the loop the
case archive (STATUS §2.7.24) left open: a quote revision recorded in the CRM from a captured
email — by a person («Registrar cotización», «Nueva revisión») or by the email rules (R3/R4) —
is put in `Cotizaciones/Casos/<case folder>` without anyone downloading or uploading anything,
and the case card links to it.

**What it files.** A `crm.quote_revision` that is not void, carries a `pdf_sha256`, was recorded
from a live `gmail_message` capture whose `.eml` is in Storage, and has no `drive_file` record
yet. Historical revisions recorded from staged records have no `.eml` here; the laptop archive
(`apps/api/scripts/quote_drive_case_archive.py`) filed those, and `drive-ledger-import` records
its links.

**Where.** The case folder is found, in this order, by: the folder an earlier `drive_file` record
of the same case names; the case key of a Drive file holding another revision of the same case;
the case folder whose opening quote number is one of the case's quote numbers; else a new folder
keyed by the CRM case id. Names, verification, idempotency and the refusal to write under a
legacy folder are `origenlab_api.v2.quote_case_archive`'s — the archiver the owner already ran.

**What it records.** One `evidence.source_record` of kind `drive_file` per document, keyed
`drive_file:<sha256>`, `review_status = 'reviewed'` (a machine fact about where a file is, not a
claim for anyone to review), with the Drive file and folder, the revision, the case and the
archive version. The worker writes no `crm.*` row; the API reads these records beside the ledgers.

**Order.** Newest first, at most `DEFAULT_LIMIT` per run: a document that keeps being refused
(its refusal code is in every run's log line) is retried, but never holds up a newer one.

**What it never does.** Rename, move, trash or delete in Drive; write a CRM row; read a message
body (only the attachment whose SHA-256 the CRM already holds); log a name, address or id.
"""

from __future__ import annotations

import email
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from typing import Any

from origenlab_api.v2 import quote_case_archive as qa
from origenlab_worker.drive_client import DriveAuthError, DriveError
from origenlab_worker.storage import StorageError

KIND = "drive_file"
DEDUPE_PREFIX = "drive_file:"
DEFAULT_LIMIT = 20
LOCK_NAME = "origenlab-worker:drive-file"

_CANDIDATES_SQL = """
select r.id::text as revision_id, r.revision_no, r.pdf_sha256, q.id::text as quote_id, q.quote_number,
       o.id::text as opportunity_id, org.name as organization_name,
       (select q2.quote_number from crm.quote q2 where q2.opportunity_id = o.id
         order by q2.created_at, q2.quote_number limit 1) as opening_quote_number,
       sr.payload ->> 'gmail_message_id' as gmail_message_id, sr.payload -> 'documents' as documents,
       m.eml_storage_path, m.eml_sha256
  from crm.quote_revision r
  join crm.quote q on q.id = r.quote_id
  join crm.opportunity o on o.id = q.opportunity_id
  left join crm.organization org on org.id = o.organization_id
  join evidence.source_record sr on sr.id = r.origin_source_record_id and sr.kind = 'gmail_message'
  join lateral (select cm.eml_storage_path, cm.eml_sha256 from comms.message cm
                 where cm.provider_message_id = sr.payload ->> 'gmail_message_id'
                   and cm.eml_storage_path is not null
                 order by cm.created_at limit 1) m on true
 where r.status <> 'void' and r.pdf_sha256 is not null
   and not exists (select 1 from evidence.source_record d where d.dedupe_key = 'drive_file:' || r.pdf_sha256)
 order by r.created_at desc, r.id
 limit %s
"""
_CASE_FOLDER_SQL = """
select payload ->> 'case_key' as case_key from evidence.source_record
 where kind = 'drive_file' and payload ->> 'opportunity_id' = %s and payload ->> 'case_key' is not null
 order by created_at limit 1
"""
_SIBLINGS_SQL = """
select r.pdf_sha256, q.quote_number from crm.quote_revision r join crm.quote q on q.id = r.quote_id
 where q.opportunity_id = %s and r.pdf_sha256 is not null and r.pdf_sha256 <> %s
 order by r.created_at desc limit 5
"""
_CASE_NUMBERS_SQL = "select quote_number from crm.quote where opportunity_id = %s order by created_at"
_INSERT_SQL = """
insert into evidence.source_record (kind, dedupe_key, payload, source_uri, review_status)
values ('drive_file', %s, %s, %s, 'reviewed')
on conflict (dedupe_key) do nothing
returning id
"""


@dataclass(frozen=True)
class DriveTarget:
    principal: str
    casos_folder_id: str
    protected_folder_ids: frozenset[str] = frozenset()

    def archive_target(self) -> qa.ArchiveTarget:
        return qa.ArchiveTarget(principal=self.principal, casos_folder_id=self.casos_folder_id,
                                protected_folder_ids=self.protected_folder_ids)


@dataclass
class FilingCounts:
    candidates: int = 0
    filed: int = 0
    reused: int = 0
    skipped: int = 0
    refused: int = 0
    #: Stable codes only — never a name, an id or an address.
    refusals: dict[str, int] = field(default_factory=dict)

    def refuse(self, code: str) -> None:
        self.refused += 1
        self.refusals[code] = self.refusals.get(code, 0) + 1

    def as_log(self) -> dict[str, Any]:
        return {"candidates": self.candidates, "filed": self.filed, "reused": self.reused,
                "skipped": self.skipped, "refused": self.refused, "refusals": dict(sorted(self.refusals.items()))}


def _rows(cur: Any) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def attachment_bytes(raw_eml: bytes, sha256: str) -> bytes | None:
    """The decoded MIME part whose SHA-256 is `sha256` — the bytes the capture hashed — or None."""
    msg = email.message_from_bytes(raw_eml, policy=policy.default)
    for part in msg.walk():
        if part.is_multipart():
            continue
        data = part.get_payload(decode=True)
        if isinstance(data, bytes) and hashlib.sha256(data).hexdigest() == sha256:
            return data
    return None


def _filename(documents: Any, sha256: str) -> str:
    for d in documents or []:
        if isinstance(d, Mapping) and d.get("sha256") == sha256 and d.get("filename"):
            return str(d["filename"])
    return f"{sha256[:12]}.pdf"


def storage_key(eml_storage_path: str) -> str:
    """`comms.message.eml_storage_path` is `mail/<key>`; the S3 key is `<key>` in bucket `mail`."""
    prefix = "mail/"
    return eml_storage_path[len(prefix):] if eml_storage_path.startswith(prefix) else eml_storage_path


class DriveFiler:
    def __init__(self, conn: Any, drive: qa.DrivePort, store: Any, target: DriveTarget, *,
                 now: Any = None) -> None:
        self._conn = conn
        self._drive = drive
        self._store = store
        self._target = target
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _query(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql, args)
            return _rows(cur)

    def candidates(self, limit: int) -> list[dict[str, Any]]:
        return self._query(_CANDIDATES_SQL, (limit,))

    def case_key(self, c: Mapping[str, Any]) -> str:
        """The key of the folder this case's documents already live in, else the CRM case id."""
        prior = self._query(_CASE_FOLDER_SQL, (c["opportunity_id"],))
        if prior:
            return str(prior[0]["case_key"])
        for sibling in self._query(_SIBLINGS_SQL, (c["opportunity_id"], c["pdf_sha256"])):
            for hit in self._drive.find_by_property(qa.PROP_DOC_SHA, sibling["pdf_sha256"]):
                key = (hit.get("appProperties") or {}).get(qa.PROP_CASE_KEY)
                if key:
                    return str(key)
        for row in self._query(_CASE_NUMBERS_SQL, (c["opportunity_id"],)):
            folders = [h for h in self._drive.find_by_property(qa.PROP_OPENING_QUOTE, row["quote_number"])
                       if (h.get("appProperties") or {}).get(qa.PROP_KIND) == qa.KIND_CASE]
            if len(folders) == 1:
                return str(folders[0]["appProperties"][qa.PROP_CASE_KEY])
        return str(c["opportunity_id"])

    def file_one(self, c: Mapping[str, Any], *, dry_run: bool = False) -> str:
        """`filed`, `reused` (already in Drive, now recorded), `skipped` (dry run), or raises
        `FilingRefused` with a stable code."""
        sha = str(c["pdf_sha256"])
        raw = self._store.get(storage_key(str(c["eml_storage_path"])))
        if c.get("eml_sha256") and hashlib.sha256(raw).hexdigest() != c["eml_sha256"]:
            raise FilingRefused("eml_hash_mismatch")
        data = attachment_bytes(raw, sha)
        if data is None:
            raise FilingRefused("attachment_not_in_eml")
        case_key = self.case_key(c)
        doc = qa.ArchiveDocument(document_sha256=sha, quote_number=str(c["quote_number"]),
                                 revision=int(c["revision_no"]),
                                 original_filename=_filename(c.get("documents"), sha), data=data,
                                 gmail_message_id=c.get("gmail_message_id"))
        case = qa.ArchiveCase(case_key=case_key,
                              opening_quote_number=str(c.get("opening_quote_number") or c["quote_number"]),
                              printed_addressee=c.get("organization_name"), documents=(doc,))
        if dry_run:
            return "skipped"
        try:
            result = qa.archive_case(self._drive, case, self._target.archive_target(),
                                     run_id=f"worker-{self._now():%Y%m%dT%H%M%SZ}")
        except qa.ArchiveRefused as exc:
            raise FilingRefused(exc.code) from None
        except qa.NameRefused:
            raise FilingRefused("name_refused") from None
        filed = next((f for f in result.files if f.document_sha256 == sha), None)
        if filed is None or not filed.verified:
            raise FilingRefused("not_verified")
        self._record(c, case_key, result.folder_id, filed)
        return "filed" if filed.outcome == "uploaded" else "reused"

    def _record(self, c: Mapping[str, Any], case_key: str, folder_id: str | None, filed: Any) -> None:
        from psycopg.types.json import Jsonb

        payload = {
            "drive_file_id": filed.file_id,
            "drive_web_view_link": filed.web_view_link,
            "drive_folder_id": folder_id,
            "case_key": case_key,
            "document_sha256": filed.document_sha256,
            "file_name": filed.name,
            "quote_revision_id": c["revision_id"],
            "quote_id": c["quote_id"],
            "quote_number": c["quote_number"],
            "revision": int(c["revision_no"]),
            "opportunity_id": c["opportunity_id"],
            "gmail_message_id": c.get("gmail_message_id"),
            "outcome": filed.outcome,
            "archive_version": qa.ARCHIVE_VERSION,
            "filed_at": self._now().isoformat(),
            "filed_by": "worker",
        }
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_INSERT_SQL, (DEDUPE_PREFIX + filed.document_sha256, Jsonb(payload),
                                      f"gdrive://file/{filed.file_id}"))

    def run(self, *, limit: int = DEFAULT_LIMIT, dry_run: bool = False) -> FilingCounts:
        counts = FilingCounts()
        rows = self.candidates(limit)
        counts.candidates = len(rows)
        for c in rows:
            try:
                outcome = self.file_one(c, dry_run=dry_run)
            except FilingRefused as exc:
                counts.refuse(exc.code)
                continue
            except DriveAuthError:
                raise  # the consent is gone: every other candidate would fail the same way
            except DriveError as exc:
                counts.refuse(f"drive_{exc.kind}")
                continue
            except StorageError as exc:
                counts.refuse(exc.code)
                continue
            setattr(counts, outcome, getattr(counts, outcome) + 1)
        return counts


class FilingRefused(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ─────────────────────────────────────────────────────────── the laptop ledgers ──


def ledger_records(rows: list[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any], str]]:
    """`archive_links.jsonl` rows → `(dedupe_key, payload, source_uri)` for every verified,
    archived document. The ledger's own `case_key` is kept, so later revisions of the same case
    find its folder."""
    out: list[tuple[str, dict[str, Any], str]] = []
    seen: dict[str, str] = {}
    for row in rows:
        sha = str(row.get("document_sha256") or "").lower()
        file_id = str(row.get("drive_file_id") or "")
        if len(sha) != 64 or not file_id:
            continue
        if row.get("archive_status") not in (None, "archived_verified"):
            continue
        if seen.get(sha, file_id) != file_id:
            raise ValueError(f"document {sha[:12]} is archived as two different Drive files")
        if sha in seen:
            continue
        seen[sha] = file_id
        payload = {
            "drive_file_id": file_id,
            "drive_web_view_link": row.get("drive_web_view_link"),
            "drive_folder_id": row.get("drive_folder_id"),
            "case_key": row.get("case_key"),
            "document_sha256": sha,
            "quote_number": row.get("quote_number"),
            "revision": row.get("revision"),
            "original_filename": row.get("original_filename"),
            "gmail_message_id": row.get("gmail_message_id"),
            "outcome": "ledger_import",
            "archive_version": qa.ARCHIVE_VERSION,
            "filed_by": "archive_ledger",
        }
        out.append((DEDUPE_PREFIX + sha, payload, f"gdrive://file/{file_id}"))
    return out


def import_ledger(conn: Any, rows: list[Mapping[str, Any]], *, dry_run: bool = False) -> dict[str, int]:
    """Record the laptop archive's links (one transaction). Existing records are left alone."""
    from psycopg.types.json import Jsonb

    records = ledger_records(rows)
    inserted = 0
    if dry_run:
        return {"rows": len(rows), "documents": len(records), "inserted": 0}
    with conn.transaction(), conn.cursor() as cur:
        for key, payload, uri in records:
            cur.execute(_INSERT_SQL, (key, Jsonb(payload), uri))
            inserted += cur.fetchone() is not None
    return {"rows": len(rows), "documents": len(records), "inserted": inserted}
