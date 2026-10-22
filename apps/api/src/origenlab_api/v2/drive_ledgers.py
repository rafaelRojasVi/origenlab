"""Drive archive-ledger types and loader.

A Drive *archive ledger* is an ``archive_links.jsonl`` file written by the owner-approved
case-archive runs, or a verified ``drive_case_upload_report.json`` from an earlier upload run.
Both are indexed by document SHA-256 so every quotation PDF can be located in Drive without an
API call at request time.

These types and helpers were extracted from :mod:`origenlab_api.v2.crm_workspace` so that
:mod:`origenlab_api.main` and :mod:`origenlab_api.v2.audience_freeze` can import them without
pulling in the full 3 k-line workspace module.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/{}"
DRIVE_FILE_URL = "https://drive.google.com/file/d/{}/view"


@dataclass(frozen=True)
class DriveLink:
    document_sha256: str
    file_id: str
    file_url: str
    folder_id: str | None
    case_key: str | None
    quote_number: str | None
    revision: int | None
    original_filename: str | None
    archive_status: str | None
    ledger_crm_status: str | None
    lifecycle: str | None
    gmail_message_id: str | None
    ledger: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": "archive_ledger",
            "ledger": self.ledger,
            "document_sha256": self.document_sha256,
            "file_id": self.file_id,
            "file_url": self.file_url,
            "folder_id": self.folder_id,
            "folder_url": DRIVE_FOLDER_URL.format(self.folder_id) if self.folder_id else None,
            "case_key": self.case_key,
            "quote_number": self.quote_number,
            "revision": self.revision,
            "original_filename": self.original_filename,
            "archive_status": self.archive_status,
        }


class DriveLedgerError(ValueError):
    """An archive ledger is missing or malformed. Raised at startup, never per request."""


def _upload_report_links(p: Path) -> list[DriveLink]:
    """The first case upload (``drive_case_upload_report.json``) predates ``archive_links.jsonl``.
    Only a report whose every upload was verified is accepted, and only verified uploads."""
    try:
        report = json.loads(p.read_text(encoding="utf-8"))
        uploads = report["uploads"]
    except (ValueError, KeyError) as exc:
        raise DriveLedgerError(f"{p}: not a case upload report") from exc
    if report.get("mode") != "upload" or report.get("all_verified") is not True:
        raise DriveLedgerError(f"{p}: upload report is not a verified upload run")
    links = []
    for u in uploads:
        f, folder = u.get("file") or {}, u.get("folder") or {}
        props = f.get("appProperties") or {}
        sha = str(f.get("sha256Checksum") or props.get("origenlab_document_sha256") or "").lower()
        if not (sha and f.get("id") and u.get("downloaded_sha256_matches") is True):
            continue
        rev = props.get("origenlab_revision")
        links.append(
            DriveLink(
                document_sha256=sha,
                file_id=f["id"],
                file_url=DRIVE_FILE_URL.format(f["id"]),
                folder_id=folder.get("id"),
                case_key=u.get("case_key"),
                quote_number=u.get("quote_number"),
                revision=int(rev) if rev and str(rev).isdigit() else None,
                original_filename=f.get("name"),
                archive_status="archived_verified",
                ledger_crm_status=None,
                lifecycle=None,
                gmail_message_id=props.get("origenlab_gmail_message_id"),
                ledger=p.parent.name,
            )
        )
    return links


def _add(index: dict[str, DriveLink], link: DriveLink, where: str) -> None:
    prior = index.get(link.document_sha256)
    if prior is not None and prior.file_id != link.file_id:
        raise DriveLedgerError(
            f"{where}: document {link.document_sha256[:12]} is archived as two different Drive files"
        )
    index.setdefault(link.document_sha256, link)


def load_drive_ledgers(paths: Iterable[str | Path]) -> dict[str, DriveLink]:
    """Index every ledger row by document SHA-256. A later ledger never silently overrides an
    earlier one with a different Drive file: two ledgers disagreeing about one document refuses.

    Accepts ``archive_links.jsonl`` ledgers and verified ``drive_case_upload_report.json`` runs."""
    index: dict[str, DriveLink] = {}
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            raise DriveLedgerError(f"archive ledger not found: {p}")
        ledger = p.parent.name
        if p.suffix == ".json":
            for link in _upload_report_links(p):
                _add(index, link, str(p))
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                sha = str(row["document_sha256"]).lower()
                file_id = str(row["drive_file_id"])
            except (ValueError, KeyError) as exc:
                raise DriveLedgerError(f"{p}:{n}: malformed ledger row") from exc
            link = DriveLink(
                document_sha256=sha,
                file_id=file_id,
                file_url=row.get("drive_web_view_link") or DRIVE_FILE_URL.format(file_id),
                folder_id=row.get("drive_folder_id"),
                case_key=row.get("case_key"),
                quote_number=row.get("quote_number"),
                revision=row.get("revision"),
                original_filename=row.get("original_filename"),
                archive_status=row.get("archive_status"),
                ledger_crm_status=row.get("crm_status"),
                lifecycle=row.get("lifecycle"),
                gmail_message_id=row.get("gmail_message_id"),
                ledger=ledger,
            )
            _add(index, link, f"{p}:{n}")
    return index
