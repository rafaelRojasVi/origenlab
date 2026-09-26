"""Read-only Google Drive evidence for quotation document candidates.

Drive is a second place a quotation document may live. It is evidence about a document,
never a source of decisions, and it is read only:

- **Matched by exact Drive file id.** The id comes from the migration manifest (a staged
  document's `drive_file_id`). A Drive file is never matched to a document by filename,
  subject, client name or address domain — a fetched file whose id no staged document
  names is reported as unreferenced and attached to nothing.
- **Hashed locally.** The SHA-256 is computed here from the fetched bytes; Drive's own
  checksums are not trusted in its place. A matching hash says the Drive file is these exact
  bytes; a different hash is shown as a mismatch, never merged.
- **Failure is a status, not an error.** A document with no Drive id, an id that was not
  fetched, or a fetch that failed is shown as "Drive evidence unavailable" with the exact
  reason. Nothing else changes: no staging file, ledger or decision is touched.

This module performs no network call and writes nothing. Fetch results are recorded by a
separate read-only step (metadata + content by exact id) and loaded from a JSON file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from origenlab_api.v2.quote_document_review import DocumentReview
from origenlab_api.v2.quote_evidence_review import Staging, StagingInconsistent

DRIVE_HASH_MATCH = "hash_match"
DRIVE_HASH_MISMATCH = "hash_mismatch"
DRIVE_UNAVAILABLE = "unavailable"

REASON_NO_DRIVE_ID = "no Drive file ID for this document in the migration manifests"

# Drive file ids are URL-safe base64-ish tokens. Anything else is not an exact id.
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


def valid_drive_file_id(raw: str | None) -> bool:
    return bool(raw) and bool(_DRIVE_ID_RE.match(raw or ""))


@dataclass(frozen=True)
class DriveFetch:
    """One read-only fetch of one Drive file, by exact id."""

    drive_file_id: str
    fetched: bool
    name: str | None = None
    mime_type: str | None = None
    modified_time: str | None = None
    sha256: str | None = None  # computed locally from the fetched bytes
    size: int | None = None
    reason: str | None = None  # why it could not be fetched, verbatim
    fetched_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "drive_file_id": self.drive_file_id,
            "fetched": self.fetched,
            "name": self.name,
            "mime_type": self.mime_type,
            "modified_time": self.modified_time,
            "sha256": self.sha256,
            "size": self.size,
            "reason": self.reason,
            "fetched_at": self.fetched_at,
        }


def drive_fetch_record(
    drive_file_id: str, metadata: Mapping[str, Any], content: bytes, *, fetched_at: str
) -> DriveFetch:
    """A successful fetch: Drive's metadata, and the SHA-256 of the bytes computed here."""
    if not valid_drive_file_id(drive_file_id):
        raise StagingInconsistent(f"{drive_file_id!r} is not a Drive file id")
    returned = metadata.get("id")
    if returned is not None and returned != drive_file_id:
        raise StagingInconsistent(
            f"asked Drive for {drive_file_id} and got metadata for {returned}: not the same file"
        )
    return DriveFetch(
        drive_file_id=drive_file_id,
        fetched=True,
        name=metadata.get("name") or metadata.get("title"),
        mime_type=metadata.get("mimeType") or metadata.get("mime_type"),
        modified_time=metadata.get("modifiedTime") or metadata.get("modified_time"),
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        fetched_at=fetched_at,
    )


def drive_unavailable_record(drive_file_id: str, reason: str, *, fetched_at: str) -> DriveFetch:
    """A fetch that failed, with the exact error text."""
    return DriveFetch(drive_file_id=drive_file_id, fetched=False, reason=reason, fetched_at=fetched_at)


def load_drive_fetches(path: Path | None) -> dict[str, DriveFetch]:
    """Fetch results keyed by exact Drive file id. A missing file means nothing was fetched."""
    if path is None or not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        rows = json.load(f)
    out: dict[str, DriveFetch] = {}
    for row in rows:
        fid = row.get("drive_file_id")
        if not valid_drive_file_id(fid):
            raise StagingInconsistent(f"{path}: {fid!r} is not a Drive file id")
        if fid in out:
            raise StagingInconsistent(f"{path}: Drive file {fid} is recorded twice")
        out[fid] = DriveFetch(**{k: row.get(k) for k in DriveFetch.__dataclass_fields__})
    return out


@dataclass(frozen=True)
class DriveEvidence:
    document_sha256: str
    drive_file_id: str | None
    # Where the manifest names this id: (email_id, source_attachment_id).
    manifest_occurrences: tuple[tuple[int, int], ...]
    status: str
    fetch: DriveFetch | None
    reason: str | None

    @property
    def available(self) -> bool:
        return self.status != DRIVE_UNAVAILABLE


@dataclass(frozen=True)
class DriveEvidenceIndex:
    by_document: dict[str, tuple[DriveEvidence, ...]]
    # Fetched ids no staged document names: shown, attached to nothing.
    unreferenced_fetches: tuple[DriveFetch, ...]

    def for_document(self, sha256: str) -> tuple[DriveEvidence, ...]:
        return self.by_document.get(sha256) or (
            DriveEvidence(sha256, None, (), DRIVE_UNAVAILABLE, None, REASON_NO_DRIVE_ID),
        )


def build_drive_evidence(
    staging: Staging, review: DocumentReview, fetches: Mapping[str, DriveFetch]
) -> DriveEvidenceIndex:
    """Attach each fetch to the document whose manifest entry names its exact id."""
    refs: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for it in staging.items:
        for d in it.documents:
            if not d.drive_file_id or not d.bytes_hash_verified:
                continue
            if not valid_drive_file_id(d.drive_file_id):
                raise StagingInconsistent(
                    f"email {it.email_id} attachment {d.source_attachment_id}: "
                    f"{d.drive_file_id!r} is not a Drive file id"
                )
            refs.setdefault(d.sha256, {}).setdefault(d.drive_file_id, []).append(
                (it.email_id, d.source_attachment_id)
            )

    candidate_shas = {c.sha256 for c in review.candidates}
    by_document: dict[str, tuple[DriveEvidence, ...]] = {}
    referenced: set[str] = set()
    for sha, ids in refs.items():
        referenced.update(ids)
        if sha not in candidate_shas:
            continue
        rows = []
        for fid, occ in sorted(ids.items()):
            fetch = fetches.get(fid)
            if fetch is None:
                status, reason = DRIVE_UNAVAILABLE, f"Drive file {fid} is in the manifest but was not fetched"
            elif not fetch.fetched:
                status, reason = DRIVE_UNAVAILABLE, fetch.reason or "fetch failed with no reason recorded"
            elif fetch.sha256 == sha:
                status, reason = DRIVE_HASH_MATCH, None
            else:
                status, reason = DRIVE_HASH_MISMATCH, (
                    f"Drive bytes hash to {fetch.sha256}, not the staged {sha}: a different file"
                )
            rows.append(DriveEvidence(sha, fid, tuple(occ), status, fetch, reason))
        by_document[sha] = tuple(rows)

    unreferenced = tuple(f for fid, f in sorted(fetches.items()) if fid not in referenced)
    return DriveEvidenceIndex(by_document=by_document, unreferenced_fetches=unreferenced)
