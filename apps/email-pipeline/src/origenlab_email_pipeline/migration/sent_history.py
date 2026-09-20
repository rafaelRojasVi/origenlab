"""Canonical Gmail Sent-recipient evidence for the Wave 1B safety delta.

The combined prior-contact baseline cannot be "accepted campaign attempts plus
recipients in ``sent`` / ``bounced`` / ``replied``". That definition sees only
addresses the *campaign machinery* touched, and misses the ordinary mail an
operator sends by hand from ``contacto@origenlab.cl``. Those recipients are
prior contacts too, and the live cold-export gate already blocks them via
``candidate_export_gate.REASON_SENT_HISTORY``.

This module therefore reads the **same** evidence the live gate reads, through
the **same** functions — it does not parse a mailbox, and it makes no Gmail
network call of any kind. Everything comes from the already-ingested ``emails``
rows in the V1 SQLite database:

===========================================  ==================================
Canonical implementation reused              What it fixes
===========================================  ==================================
``marketing_export_context.sent_history_where``   one definition of "a Sent row
                                                  of this mailbox" (the
                                                  ``source_file`` LIKE pattern
                                                  and the ``folder IN`` set)
``marketing_export_context.load_sent_recipient_norms``  the canonical full Sent
                                                  recipient universe
``business_mart.emails_in``                       address extraction and
                                                  lowercasing, exactly as the
                                                  gate normalizes
``outbound_sent_preflight.probe_sent_history``    the fail-closed probe
``outbound_sent_preflight.evaluate_sent_history_preflight``  the fail-closed rule
``outbound_core.resolve_outbound_gmail_user``     mailbox identity resolution
``outbound_core.resolve_outbound_sent_folders``   Sent folder selection
===========================================  ==================================

Because the SQL predicate and the address parser are *imported*, the migration
extractor cannot drift away from the live outbound gate: a change to either
moves both at once.

Fail-closed, with no override — unlike the export CLIs, this tool has no
``--allow-empty-sent-history`` equivalent, because a migration baseline that
silently omits prior contacts is worse than no baseline:

* the ``emails`` table absent, the mailbox unresolved, no folder matching, zero
  Sent rows or zero parsed recipients — refused;
* a Sent row that parses to at least one recipient but whose ``date_iso`` is
  missing or unparsable — refused, because it cannot be placed on either side
  of the Wave 1A snapshot instant;
* a post-snapshot address that is not in the canonical full Sent set — refused,
  because the two reads disagree and one of them is wrong.

Nothing here exports a subject, a body or a message id. Only the normalized
recipient address travels, with its first/last Sent timestamp and a message
count. Refusal messages never carry an address — the mailbox is reported
through :func:`~origenlab_email_pipeline.migration.wave1b_extract.redact_address`
by the caller.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.marketing_export_context import (
    DEFAULT_SENT_FOLDERS,
    load_sent_recipient_norms,
    sent_history_where,
)
from origenlab_email_pipeline.outbound_core import DEFAULT_GMAIL_USER_FALLBACK
from origenlab_email_pipeline.outbound_sent_preflight import (
    evaluate_sent_history_preflight,
    probe_sent_history,
)

#: Non-sensitive source categories recorded on every combined prior-contact row.
SOURCE_CAMPAIGN_ACCEPTED = "campaign_accepted"
SOURCE_SENT_HISTORY = "sent_history"
SOURCE_OUTREACH_STATE = "outreach_state"

SOURCE_CATEGORIES: tuple[str, ...] = (
    SOURCE_CAMPAIGN_ACCEPTED,
    SOURCE_SENT_HISTORY,
    SOURCE_OUTREACH_STATE,
)


class SentHistoryRefused(Exception):
    """Canonical Sent evidence could not be trusted, so nothing was extracted.

    Messages never carry an address, a folder path or a filesystem path.
    """


@dataclass(frozen=True)
class MailboxSelection:
    """Which mailbox and folders were read, and how that was decided.

    ``gmail_user`` is an operator mailbox identity, not a credential and not a
    private path. It is recorded in the (owner-only) bundle and redacted on the
    terminal.
    """

    gmail_user: str
    sent_folders: tuple[str, ...]
    gmail_user_source: str
    sent_folders_source: str

    def to_record(self) -> dict[str, Any]:
        return {
            "gmail_user": self.gmail_user,
            "gmail_user_source": self.gmail_user_source,
            "sent_folders": list(self.sent_folders),
            "sent_folders_source": self.sent_folders_source,
        }


@dataclass
class SentHistoryEvidence:
    """Post-snapshot Sent recipients plus the facts that justify trusting them."""

    mailbox: MailboxSelection
    sent_row_count: int
    parsed_recipient_count: int
    rows_scanned: int
    rows_after_snapshot: int
    recipients: dict[str, dict[str, Any]] = field(default_factory=dict)
    latest_sent_at: str | None = None
    complete: bool = True
    incomplete_reasons: tuple[str, ...] = ()

    def to_report(self) -> dict[str, Any]:
        return {
            "mailbox": self.mailbox.to_record(),
            "canonical_implementation_reused": [
                "marketing_export_context.sent_history_where",
                "marketing_export_context.load_sent_recipient_norms",
                "business_mart.emails_in",
                "outbound_sent_preflight.probe_sent_history",
                "outbound_sent_preflight.evaluate_sent_history_preflight",
                "outbound_core.resolve_outbound_gmail_user",
                "outbound_core.resolve_outbound_sent_folders",
            ],
            "gmail_network_calls": 0,
            "source": "already-ingested `emails` rows in the V1 SQLite database",
            "sent_row_count": self.sent_row_count,
            "parsed_recipient_count_whole_history": self.parsed_recipient_count,
            "rows_scanned": self.rows_scanned,
            "rows_after_snapshot": self.rows_after_snapshot,
            "recipients_after_snapshot": len(self.recipients),
            "latest_sent_at": self.latest_sent_at,
            "complete": self.complete,
            "incomplete_reasons": list(self.incomplete_reasons),
        }


def resolve_mailbox(
    *,
    gmail_user: str | None,
    sent_folders: tuple[str, ...] | None,
) -> MailboxSelection:
    """Resolve mailbox identity and Sent folders the way the outbound CLIs do.

    ``outbound_core.resolve_outbound_gmail_user`` takes a ``Settings`` object
    and would read the operator's ``.env``; this tool refuses to depend on
    ambient configuration, so the operator passes the mailbox explicitly and
    the shared fallback constant covers the default. The folder rule is the
    shared one verbatim.
    """
    from origenlab_email_pipeline.outbound_core import resolve_outbound_sent_folders

    explicit_user = (gmail_user or "").strip()
    user = explicit_user or DEFAULT_GMAIL_USER_FALLBACK
    user_source = "operator" if explicit_user else "outbound_core.DEFAULT_GMAIL_USER_FALLBACK"

    folders = resolve_outbound_sent_folders(sent_folders)
    folders_source = (
        "operator"
        if folders != DEFAULT_SENT_FOLDERS
        else "marketing_export_context.DEFAULT_SENT_FOLDERS"
    )
    return MailboxSelection(
        gmail_user=user,
        sent_folders=folders,
        gmail_user_source=user_source,
        sent_folders_source=folders_source,
    )


def _parse_sent_instant(value: Any) -> datetime | None:
    """Parse an ``emails.date_iso`` value into an aware UTC instant.

    ``date_iso`` is ``parsedate_to_datetime(...).isoformat()``, so it may carry
    an offset or be naive (RFC 5322 ``-0000``, "time zone unknown"). A naive
    value is read as UTC, which is the standard reading of ``-0000``. Anything
    unparsable returns ``None`` and is handled as unparsable evidence.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _snapshot_instant(snapshot: str) -> datetime:
    parsed = _parse_sent_instant(snapshot)
    if parsed is None:
        raise SentHistoryRefused("the Wave 1A snapshot instant is not a parsable timestamp")
    return parsed


def _emails_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", ("emails",)
    ).fetchone()
    return bool(row)


def collect_sent_history(
    conn: sqlite3.Connection,
    *,
    mailbox: MailboxSelection,
    snapshot: str,
    latest_campaign_activity_at: str | None = None,
) -> SentHistoryEvidence:
    """Read post-snapshot Gmail Sent recipients, failing closed on bad evidence."""
    if not _emails_table_exists(conn):
        raise SentHistoryRefused(
            "the source database has no `emails` table, so canonical Gmail Sent "
            "evidence cannot be read; the combined prior-contact baseline would be "
            "incomplete and is refused"
        )

    probe = probe_sent_history(
        conn, gmail_user=mailbox.gmail_user, sent_folders=mailbox.sent_folders
    )
    outcome = evaluate_sent_history_preflight(probe, allow_empty=False)
    if not outcome.ok:
        raise SentHistoryRefused(
            "canonical Sent-history preflight failed for the configured mailbox "
            f"({probe.sent_row_count} Sent row(s), {probe.parsed_recipient_count} parsed "
            f"recipient(s), {len(mailbox.sent_folders)} folder label(s)); "
            "this migration extractor has no allow-empty override"
        )

    canonical = load_sent_recipient_norms(
        conn, gmail_user=mailbox.gmail_user, sent_folders=mailbox.sent_folders
    )
    boundary = _snapshot_instant(snapshot)

    where, params = sent_history_where(
        gmail_user=mailbox.gmail_user, sent_folders=mailbox.sent_folders
    )
    recipients: dict[str, dict[str, Any]] = {}
    rows_scanned = 0
    rows_after = 0
    undatable_with_recipients = 0
    latest: datetime | None = None

    cursor = conn.execute(
        f"SELECT recipients, date_iso FROM emails WHERE {where} ORDER BY id", params
    )
    for raw_recipients, date_iso in cursor:
        rows_scanned += 1
        addresses = sorted(set(emails_in(raw_recipients or "")))
        instant = _parse_sent_instant(date_iso)
        if instant is None:
            if addresses:
                undatable_with_recipients += 1
            continue
        if latest is None or instant > latest:
            latest = instant
        if instant <= boundary:
            continue
        rows_after += 1
        stamp = instant.strftime("%Y-%m-%dT%H:%M:%SZ")
        for address in addresses:
            record = recipients.setdefault(
                address,
                {
                    "address": address,
                    "sent_message_count": 0,
                    "first_sent_at": stamp,
                    "last_sent_at": stamp,
                },
            )
            record["sent_message_count"] += 1
            record["first_sent_at"] = min(record["first_sent_at"], stamp)
            record["last_sent_at"] = max(record["last_sent_at"], stamp)

    if undatable_with_recipients:
        raise SentHistoryRefused(
            f"{undatable_with_recipients} Gmail Sent row(s) carry recipients but no "
            "parsable `date_iso`, so they cannot be placed before or after the Wave 1A "
            "snapshot; the prior-contact delta would be guesswork and is refused"
        )

    drifted = sorted(set(recipients) - canonical)
    if drifted:
        raise SentHistoryRefused(
            f"{len(drifted)} post-snapshot Sent recipient(s) are absent from the "
            "canonical `load_sent_recipient_norms` set; the dated read and the "
            "canonical read disagree, so the extraction is refused"
        )

    latest_stamp = latest.strftime("%Y-%m-%dT%H:%M:%SZ") if latest else None
    incomplete: list[str] = []
    if latest_stamp is None:
        incomplete.append("no Gmail Sent row carries a parsable timestamp")
    campaign_instant = _parse_sent_instant(latest_campaign_activity_at)
    if campaign_instant is not None and (latest is None or latest < campaign_instant):
        incomplete.append(
            "the newest ingested Gmail Sent row predates the newest campaign send "
            "attempt; the Sent ingest is stale relative to the campaigns and the "
            "combined baseline may miss recent manual mail"
        )

    return SentHistoryEvidence(
        mailbox=mailbox,
        sent_row_count=probe.sent_row_count,
        parsed_recipient_count=probe.parsed_recipient_count,
        rows_scanned=rows_scanned,
        rows_after_snapshot=rows_after,
        recipients=dict(sorted(recipients.items())),
        latest_sent_at=latest_stamp,
        complete=not incomplete,
        incomplete_reasons=tuple(incomplete),
    )


def combine_prior_contact(
    *,
    campaign_accepted: dict[str, dict[str, Any]],
    sent_history: dict[str, dict[str, Any]],
    outreach_state: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deduplicate the three prior-contact sources, keeping source provenance.

    Returns ``(rows, counts)``. Each row carries the normalized migration
    address, its ``source_categories`` and the non-sensitive structured facts
    each source contributed — nothing else from a source row travels.
    """
    sources: dict[str, dict[str, dict[str, Any]]] = {
        SOURCE_CAMPAIGN_ACCEPTED: campaign_accepted,
        SOURCE_SENT_HISTORY: sent_history,
        SOURCE_OUTREACH_STATE: outreach_state,
    }
    combined: dict[str, dict[str, Any]] = {}
    for category in SOURCE_CATEGORIES:
        for address, facts in sources[category].items():
            row = combined.setdefault(
                address,
                {
                    "address": address,
                    "source_categories": [],
                    "first_at": None,
                    "last_at": None,
                },
            )
            row["source_categories"].append(category)
            row[category] = {k: v for k, v in facts.items() if k != "address"}
            for key in ("first_at", "last_at", "first_sent_at", "last_sent_at"):
                value = facts.get(key)
                if not isinstance(value, str) or not value:
                    continue
                if row["first_at"] is None or value < row["first_at"]:
                    row["first_at"] = value
                if row["last_at"] is None or value > row["last_at"]:
                    row["last_at"] = value

    rows = [combined[address] for address in sorted(combined)]
    for row in rows:
        row["source_categories"] = sorted(row["source_categories"])
        row["multiple_sources"] = len(row["source_categories"]) > 1

    keys = {category: set(sources[category]) for category in SOURCE_CATEGORIES}
    counts = {
        "raw_by_source": {category: len(keys[category]) for category in SOURCE_CATEGORIES},
        "raw_total_before_dedup": sum(len(keys[c]) for c in SOURCE_CATEGORIES),
        "overlaps": {
            f"{SOURCE_CAMPAIGN_ACCEPTED}+{SOURCE_SENT_HISTORY}": len(
                keys[SOURCE_CAMPAIGN_ACCEPTED] & keys[SOURCE_SENT_HISTORY]
            ),
            f"{SOURCE_CAMPAIGN_ACCEPTED}+{SOURCE_OUTREACH_STATE}": len(
                keys[SOURCE_CAMPAIGN_ACCEPTED] & keys[SOURCE_OUTREACH_STATE]
            ),
            f"{SOURCE_SENT_HISTORY}+{SOURCE_OUTREACH_STATE}": len(
                keys[SOURCE_SENT_HISTORY] & keys[SOURCE_OUTREACH_STATE]
            ),
            "all_three": len(
                keys[SOURCE_CAMPAIGN_ACCEPTED]
                & keys[SOURCE_SENT_HISTORY]
                & keys[SOURCE_OUTREACH_STATE]
            ),
        },
        "addresses_with_multiple_sources": sum(1 for r in rows if r["multiple_sources"]),
        "deduplicated_total": len(rows),
    }
    return rows, counts
