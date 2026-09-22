"""Read-only mailbox source inventory and intake classification (no writes, no addresses).

Answers, from ``emails``/``email_mart_features`` alone: which identity each
``source_file`` group belongs to, how much of it is sent vs received, and what
each folder is allowed to do in V2 intake.

**Intake rules encoded here** (owner decision, 2026-09-22):

- ``primary_evidence`` — Inbox and Sent are the primary commercial evidence lane.
- ``archived`` — live archived non-draft mail is a *candidate* for the next
  evidence replay; it is not primary and is not promoted by this module.
- ``metadata_only`` — Drafts are metadata only. A draft is never correspondence,
  a quote, consent, or an opportunity, whatever its body says.
- ``excluded_spam`` — Spam is inventoried separately and excluded from automatic intake.
- ``excluded_trash`` — Trash is inventoried separately as historical/purged
  material and excluded from automatic promotion.

Identity rules: ``contacto@labdelivery.cl`` is a distinct historical identity.
Mail *from* it arriving in the current OrigenLab mailbox is flagged
``cross_identity`` — evidence to review by hand, never an automatic merge.

This module never opens SQLite read-write and never emits an email address
unless the caller explicitly asks for identity detail.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from origenlab_email_pipeline.contacto_gmail_source import classify_email_source

IntakeClass = Literal[
    "primary_evidence",
    "archived",
    "metadata_only",
    "excluded_spam",
    "excluded_trash",
    "unclassified",
]

IntakeLane = Literal[
    "current_origenlab",
    "legacy_labdelivery",
    "legacy_outlook_backup",
    "other",
]

#: Intake classes that may feed automatic intake. Everything else is inventory only.
AUTOMATIC_INTAKE_CLASSES: frozenset[str] = frozenset({"primary_evidence"})

#: Intake classes eligible for an operator-approved evidence replay batch.
REPLAY_CANDIDATE_CLASSES: frozenset[str] = frozenset({"archived"})

#: Domains this business has sent mail from. Order matters only for reporting.
CURRENT_IDENTITY_DOMAINS: tuple[str, ...] = ("origenlab.cl", "origenlab.com")
LEGACY_IDENTITY_DOMAINS: tuple[str, ...] = ("labdelivery.cl",)

# Folder-name markers, matched case-insensitively against the folder/source path.
# Checked in this order: a spam or trash subfolder beats the inbox it hangs under.
_SPAM_MARKERS = ("spam", "correo no deseado", "junk", "bulk")
_TRASH_MARKERS = ("papelera", "elementos eliminados", "trash", "deleted items")
_DRAFT_MARKERS = ("borrador", "draft")
_SENT_MARKERS = ("enviados", "elementos enviados", "sent")
_ARCHIVE_MARKERS = ("[gmail]/todos", "all mail", "archive", "archivo de correo")
_INBOX_MARKERS = ("inbox", "bandeja de entrada")


def classify_intake_folder(folder: str | None, source_file: str | None = None) -> IntakeClass:
    """Map a folder (falling back to ``source_file``) onto its intake class."""
    text = f"{folder or ''} {source_file or ''}".strip().lower()
    if not text:
        return "unclassified"
    for markers, label in (
        (_SPAM_MARKERS, "excluded_spam"),
        (_TRASH_MARKERS, "excluded_trash"),
        (_DRAFT_MARKERS, "metadata_only"),
        (_SENT_MARKERS, "primary_evidence"),
        (_ARCHIVE_MARKERS, "archived"),
        (_INBOX_MARKERS, "primary_evidence"),
    ):
        if any(m in text for m in markers):
            return label  # type: ignore[return-value]
    return "unclassified"


def classify_intake_lane(source_file: str | None) -> IntakeLane:
    """Map ``source_file`` onto the identity lane that owns it.

    The Outlook ``backup*`` mbox exports carry no identity in their path; they are
    reported as their own lane rather than folded into either live identity.
    """
    tier = classify_email_source(source_file)
    if tier == "canonical_gmail":
        return "current_origenlab"
    if tier == "legacy_labdelivery":
        return "legacy_labdelivery"
    if tier == "mbox":
        return "legacy_outlook_backup"
    return "other"


def _domain_of(address: str | None) -> str:
    """Domain of a bare address or of a full ``Name <a@b.cl>`` header value."""
    a = (address or "").strip().lower()
    if "<" in a and ">" in a:
        a = a[a.rfind("<") + 1 : a.rfind(">")]
    a = a.strip().strip("<>").strip()
    if "@" not in a:
        return ""
    return a.rsplit("@", 1)[-1].strip(" \t\"'>;,")


def sender_identity(sender_email: str | None) -> Literal["current", "legacy", "external", "unknown"]:
    domain = _domain_of(sender_email)
    if not domain:
        return "unknown"
    if domain in CURRENT_IDENTITY_DOMAINS:
        return "current"
    if domain in LEGACY_IDENTITY_DOMAINS:
        return "legacy"
    return "external"


@dataclass
class GroupInventory:
    """One ``(lane, folder class)`` group. Counts only — never an address."""

    lane: IntakeLane
    intake_class: IntakeClass
    rows: int = 0
    sent_by_current_identity: int = 0
    sent_by_legacy_identity: int = 0
    received: int = 0
    unknown_sender: int = 0
    cross_identity: int = 0
    first_date: str | None = None
    last_date: str | None = None

    @property
    def eligible_for_automatic_intake(self) -> bool:
        return self.intake_class in AUTOMATIC_INTAKE_CLASSES

    @property
    def replay_candidate(self) -> bool:
        return self.intake_class in REPLAY_CANDIDATE_CLASSES

    def as_dict(self) -> dict[str, object]:
        return {
            "lane": self.lane,
            "intake_class": self.intake_class,
            "rows": self.rows,
            "sent_by_current_identity": self.sent_by_current_identity,
            "sent_by_legacy_identity": self.sent_by_legacy_identity,
            "received": self.received,
            "unknown_sender": self.unknown_sender,
            "cross_identity": self.cross_identity,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "eligible_for_automatic_intake": self.eligible_for_automatic_intake,
            "replay_candidate": self.replay_candidate,
        }


@dataclass
class MailboxInventory:
    total_rows: int = 0
    groups: list[GroupInventory] = field(default_factory=list)
    lane_totals: dict[str, int] = field(default_factory=dict)
    intake_class_totals: dict[str, int] = field(default_factory=dict)
    cross_identity_total: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "total_rows": self.total_rows,
            "lane_totals": self.lane_totals,
            "intake_class_totals": self.intake_class_totals,
            "cross_identity_total": self.cross_identity_total,
            "automatic_intake_rows": sum(
                g.rows for g in self.groups if g.eligible_for_automatic_intake
            ),
            "replay_candidate_rows": sum(g.rows for g in self.groups if g.replay_candidate),
            "groups": [g.as_dict() for g in self.groups],
        }


def connect_read_only(db_path: Path | str) -> sqlite3.Connection:
    """Open SQLite strictly read-only (``mode=ro`` + ``query_only``)."""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=120.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


_MART_SQL = """
SELECT source_file, folder, sender_email, substr(mart_date_iso, 1, 10) AS day
FROM email_mart_features
"""

_EMAILS_SQL = """
SELECT source_file, folder, lower(sender) AS sender_email, substr(date_iso, 1, 10) AS day
FROM emails
"""


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


def build_inventory(conn: sqlite3.Connection) -> MailboxInventory:
    """Aggregate the mailbox into lane × intake-class groups.

    Prefers ``email_mart_features`` (a compact projection with a parsed sender and a
    sanitized date); falls back to ``emails`` when the mart has not been built.
    """
    sql = _MART_SQL if _has_table(conn, "email_mart_features") else _EMAILS_SQL
    groups: dict[tuple[str, str], GroupInventory] = {}
    lane_totals: Counter[str] = Counter()
    class_totals: Counter[str] = Counter()
    total = 0
    cross_total = 0

    for row in conn.execute(sql):
        total += 1
        lane = classify_intake_lane(row["source_file"])
        intake_class = classify_intake_folder(row["folder"], row["source_file"])
        lane_totals[lane] += 1
        class_totals[intake_class] += 1
        key = (lane, intake_class)
        group = groups.get(key)
        if group is None:
            group = GroupInventory(lane=lane, intake_class=intake_class)
            groups[key] = group
        group.rows += 1

        identity = sender_identity(row["sender_email"])
        if identity == "current":
            group.sent_by_current_identity += 1
        elif identity == "legacy":
            group.sent_by_legacy_identity += 1
            if lane == "current_origenlab":
                # Legacy identity writing into the current mailbox: review, never merge.
                group.cross_identity += 1
                cross_total += 1
        elif identity == "external":
            group.received += 1
        else:
            group.unknown_sender += 1

        day = row["day"]
        if day:
            if group.first_date is None or day < group.first_date:
                group.first_date = day
            if group.last_date is None or day > group.last_date:
                group.last_date = day

    ordered = sorted(groups.values(), key=lambda g: (g.lane, g.intake_class))
    return MailboxInventory(
        total_rows=total,
        groups=ordered,
        lane_totals=dict(sorted(lane_totals.items())),
        intake_class_totals=dict(sorted(class_totals.items())),
        cross_identity_total=cross_total,
    )


def inventory_from_path(db_path: Path | str) -> MailboxInventory:
    conn = connect_read_only(db_path)
    try:
        return build_inventory(conn)
    finally:
        conn.close()
