"""Stage 1: bounded, indexed scan for Sent-folder quote candidates.

Only the two folders confirmed by the coverage audit ever carried OrigenLab's
own outbound mail (see design.md §2). Everything else in `emails` is
Inbox-only and is never scanned here — this keeps Stage 1 O(rows-in-two-folders),
not O(full archive).

SENT_FOLDERS[1] is the exact `folder` column literal for the labdelivery.cl
Outlook archive's Sent folder, confirmed 2026-08-28 against the real
production DB (read-only query, see task-2-report.md). It is the full
absolute mbox path baked in at ingestion time, not a bare folder name —
6,229 rows, 2017-04-24 through 2020-03-13, matching the coverage audit.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

SENT_FOLDERS: tuple[str, ...] = (
    "[Gmail]/Enviados",
    "/home/rafael/data/origenlab-email/mbox/contacto@labdelivery.cl/contacto@labdelivery.cl/Elementos enviados",
)


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
) -> list[SendCandidateRow]:
    placeholders = ",".join("?" for _ in folders)
    rows = conn.execute(
        f"""
        SELECT id, source_file, folder, message_id, subject, sender, recipients,
               date_iso, has_attachments
        FROM emails
        WHERE folder IN ({placeholders})
        ORDER BY date_iso ASC
        """,
        folders,
    ).fetchall()
    return [
        SendCandidateRow(
            email_id=r[0], source_file=r[1], folder=r[2], message_id=r[3],
            subject=r[4], sender=r[5], recipients=r[6], date_iso=r[7],
            has_attachments=bool(r[8]),
        )
        for r in rows
    ]
