"""Stage 1: bounded, indexed scan for Sent-folder quote candidates.

Only the folders confirmed by the coverage audit ever carried OrigenLab's
own outbound mail (see design.md §2). Everything else in `emails` is
Inbox-only and is never scanned here — this keeps Stage 1 O(rows-in-known-
sent-folders), not O(full archive).

Two ingestion sources, two matching strategies:

- Gmail-sourced mail stores `folder` as a short, portable literal
  (`[Gmail]/Enviados`) — matched exactly, via `SENT_FOLDERS`.
- The legacy Outlook/mbox archive (labdelivery.cl) stores `folder` as the
  *full absolute mbox path* baked in at ingestion time, e.g.
  `/home/rafael/data/origenlab-email/mbox/contacto@labdelivery.cl/contacto@labdelivery.cl/Elementos enviados`
  (confirmed 2026-08-28 against the real production DB, read-only query —
  see task-2-report.md). That path is specific to the machine/data_root the
  archive was ingested under, so exact-literal matching would silently
  return zero legacy-era Sent candidates (no error, just quietly wrong) if
  the archive is ever regenerated elsewhere. It is matched by trailing path
  segment instead — see `LEGACY_MBOX_SENT_FOLDER_SUFFIX`.

  Suffix matching also picks up two unrelated, low-volume (2 rows each)
  backup folders elsewhere in the archive that happen to share the same
  trailing folder name. Accepted tradeoff: a handful of extra low-volume
  rows is far safer than silently losing the ~6,229 real legacy Sent rows
  on a re-ingested archive.

`is_sent_folder` (and the two more specific predicates it composes) are the
public "is this a Sent folder" check for callers outside this module —
e.g. Stage 8's reply-search needs to exclude Sent folders, and Stage 13's
attachment recovery needs to distinguish Gmail vs. legacy-mbox sourcing to
pick the right recovery path.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

SENT_FOLDERS: tuple[str, ...] = (
    "[Gmail]/Enviados",
)
"""Exact-match `folder` column literals — Sent folders whose stored value is
a short, portable name (Gmail-sourced ingests only)."""

LEGACY_MBOX_SENT_FOLDER_SUFFIX = "/Elementos enviados"
"""Suffix-match pattern for the legacy Outlook/mbox archive's Sent folder.
See module docstring for why this is a suffix rather than an exact literal."""


def is_gmail_sent_folder(folder: str | None) -> bool:
    """True if `folder` is an exact-match Gmail Sent-folder literal."""
    return folder in SENT_FOLDERS


def is_legacy_mbox_sent_folder(folder: str | None) -> bool:
    """True if `folder` is the legacy mbox archive's Sent folder, matched by
    trailing path segment rather than full-path equality."""
    return folder is not None and folder.endswith(LEGACY_MBOX_SENT_FOLDER_SUFFIX)


def is_sent_folder(folder: str | None) -> bool:
    """True if `folder` is any known Sent folder for OrigenLab's own outbound
    mail (Gmail exact match or legacy-mbox suffix match). Public so callers
    outside Stage 1 can apply the same matching rules to a `folder` value
    they already have, without re-deriving them."""
    return is_gmail_sent_folder(folder) or is_legacy_mbox_sent_folder(folder)


@dataclass(frozen=True)
class SendCandidateRow:
    email_id: int
    source_file: str
    folder: str
    message_id: str | None
    subject: str | None
    sender: str | None
    recipients: str | None
    date_iso: str | None
    has_attachments: bool


def fetch_send_candidates(
    conn: sqlite3.Connection,
    *,
    folders: tuple[str, ...] = SENT_FOLDERS,
    legacy_mbox_suffix: str = LEGACY_MBOX_SENT_FOLDER_SUFFIX,
) -> list[SendCandidateRow]:
    placeholders = ",".join("?" for _ in folders)
    rows = conn.execute(
        f"""
        SELECT id, source_file, folder, message_id, subject, sender, recipients,
               date_iso, has_attachments
        FROM emails
        WHERE folder IN ({placeholders}) OR folder LIKE ?
        ORDER BY date_iso ASC
        """,
        (*folders, f"%{legacy_mbox_suffix}"),
    ).fetchall()
    return [
        SendCandidateRow(
            email_id=r[0], source_file=r[1], folder=r[2], message_id=r[3],
            subject=r[4], sender=r[5], recipients=r[6], date_iso=r[7],
            has_attachments=bool(r[8]),
        )
        for r in rows
    ]
