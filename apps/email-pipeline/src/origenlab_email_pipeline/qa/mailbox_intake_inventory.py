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

Identity rules: the business's former commercial identity is a *distinct* lane.
Mail *from* it arriving in the current mailbox is flagged ``cross_identity`` —
evidence to review by hand, never an automatic merge.

**No mailbox address is written in this file.** The identity vocabulary is derived
at call time from the two modules that already own it —
:mod:`~origenlab_email_pipeline.contacto_gmail_source` and
:mod:`~origenlab_email_pipeline.business_filter_rules` — so there is one place to
correct if an identity ever changes, and a caller (a test, another deployment) may
pass its own :class:`IdentityRules` instead.

This module never opens SQLite read-write and never emits an email address
unless the caller explicitly asks for identity detail.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from origenlab_email_pipeline.business_filter_rules import INTERNAL_DOMAINS
from origenlab_email_pipeline.contacto_gmail_source import (
    LEGACY_LABDELIVERY_SOURCE_LIKE,
    classify_email_source,
)

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

@dataclass(frozen=True)
class IdentityRules:
    """Which sender domains count as this business, and which as its former identity.

    Both tuples are lower-case bare domains. They must be disjoint: a domain that is
    both identities at once would make ``cross_identity`` meaningless.
    """

    current_domains: tuple[str, ...]
    legacy_domains: tuple[str, ...]

    def __post_init__(self) -> None:
        overlap = set(self.current_domains) & set(self.legacy_domains)
        if overlap:
            raise ValueError(
                f"a domain cannot be both the current and the former identity: {sorted(overlap)}"
            )


def _legacy_domain_token() -> str:
    """The former identity's domain label, taken from the canonical source-file marker.

    ``contacto_gmail_source`` already owns the one string that recognises the legacy
    mailbox in a ``source_file``; the domain label is the part of it after the ``@``.
    Deriving it here means this module states no address of its own.
    """
    return LEGACY_LABDELIVERY_SOURCE_LIKE.strip("%").rsplit("@", 1)[-1].strip(". ")


def default_identity_rules() -> IdentityRules:
    """Partition the canonical internal-domain list into current and former identity.

    ``business_filter_rules.INTERNAL_DOMAINS`` is the repository's existing list of the
    domains this business owns. This function splits it — it adds nothing to it — so a
    domain added there is picked up here without a second edit.
    """
    token = _legacy_domain_token()
    domains = tuple(d.strip().lower() for d in INTERNAL_DOMAINS if d and d.strip())
    legacy = tuple(d for d in domains if d.split(".", 1)[0] == token)
    current = tuple(d for d in domains if d not in legacy)
    return IdentityRules(current_domains=current, legacy_domains=legacy)

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


def sender_identity(
    sender_email: str | None,
    *,
    rules: IdentityRules | None = None,
) -> Literal["current", "legacy", "external", "unknown"]:
    """Whose identity sent this. ``rules`` defaults to :func:`default_identity_rules`."""
    active = rules if rules is not None else default_identity_rules()
    domain = _domain_of(sender_email)
    if not domain:
        return "unknown"
    if domain in active.current_domains:
        return "current"
    if domain in active.legacy_domains:
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


def build_inventory(
    conn: sqlite3.Connection,
    *,
    rules: IdentityRules | None = None,
) -> MailboxInventory:
    """Aggregate the mailbox into lane × intake-class groups.

    Prefers ``email_mart_features`` (a compact projection with a parsed sender and a
    sanitized date); falls back to ``emails`` when the mart has not been built.
    """
    active = rules if rules is not None else default_identity_rules()
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

        identity = sender_identity(row["sender_email"], rules=active)
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


def inventory_from_path(
    db_path: Path | str,
    *,
    rules: IdentityRules | None = None,
) -> MailboxInventory:
    conn = connect_read_only(db_path)
    try:
        return build_inventory(conn, rules=rules)
    finally:
        conn.close()
