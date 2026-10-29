"""Import one **sent** V1-lane campaign into the CRM — and append each later wave of it.

Campaigns still go out through the V1 lane (the PC's systemd runner and its SQLite ledger), so
the hosted CRM never saw Cyber or OHAUS: the Marketing page could only show their plan. This
module loads what the V1 ledger recorded as sent into the same tables the September V1 imports
live in (``outbound.campaign`` / ``campaign_recipient`` / ``send_attempt``), as an archived,
imported campaign, so every existing campaign read (totals, recipients, audit) works for it.

* **Read-only source.** The SQLite is opened ``mode=ro`` with ``query_only`` on; only live
  ``accepted`` attempts and their recipients are read. A campaign with an attempt still in
  flight, or never sent, is refused (never-sent campaigns belong to ``supplemental_campaign``
  in ``apps/email-pipeline``).
* **One manifest per campaign, stable key.** ``evidence.source_record`` with
  ``dedupe_key = migration_manifest:v1-sent-campaign:<v1 id>`` is the campaign's provenance;
  the key carries no hash so a later wave appends under the same campaign.
* **Append, never rewrite.** A second apply compares every stored recipient and attempt with
  the extraction. Anything stored that the extraction disagrees with is a refusal; only what is
  missing is inserted. The archived-campaign trigger lets nobody but ``origenlab_owner`` insert
  into an archived campaign, so the importer takes that role for its transaction.
* **Dry run by default**, and the manifest carries counts and hashes, never an address.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IMPORT_ROLE = "origenlab_owner"
MANIFEST_VERSION = "v1-sent-campaign/2026-10-29.v1"
DEDUPE_PREFIX = "migration_manifest:v1-sent-campaign:"
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SentCampaignRefused(Exception):
    """The V1 campaign cannot be imported as it stands, or the target disagrees with it."""


@dataclass(frozen=True)
class SentRecipient:
    address_norm: str
    v2_state: str  # 'sent' | 'bounced'


@dataclass(frozen=True)
class SentAttempt:
    address_norm: str
    accepted_at: str  # ISO-8601 UTC, as V1 recorded it
    gmail_message_id: str | None


@dataclass(frozen=True)
class SentCampaign:
    v1_campaign_id: str
    name: str
    subject: str
    sender_email: str
    target_attempt_count: int
    recipients: tuple[SentRecipient, ...]
    attempts: tuple[SentAttempt, ...]

    def dedupe_key(self) -> str:
        return f"{DEDUPE_PREFIX}{self.v1_campaign_id}"

    def manifest(self) -> dict[str, Any]:
        """Counts and row hashes only: the manifest stored in the CRM carries no address."""
        rec = "\n".join(f"{r.address_norm}\t{r.v2_state}" for r in self.recipients)
        att = "\n".join(f"{a.address_norm}\t{a.accepted_at}\t{a.gmail_message_id or ''}" for a in self.attempts)
        return {
            "manifest_version": MANIFEST_VERSION,
            "v1_campaign_id": self.v1_campaign_id,
            "name": self.name,
            "recipients": len(self.recipients),
            "recipients_by_state": dict(sorted(Counter(r.v2_state for r in self.recipients).items())),
            "accepted_attempts": len(self.attempts),
            "recipient_rows_sha256": hashlib.sha256(rec.encode("utf-8")).hexdigest(),
            "attempt_rows_sha256": hashlib.sha256(att.encode("utf-8")).hexdigest(),
        }


def _address(raw: str | None) -> str:
    a = (raw or "").strip().lower()
    if not _ADDRESS.match(a):
        raise SentCampaignRefused("a recipient address does not normalize")
    return a


def extract_sent_campaign(sqlite_path: Path, v1_campaign_id: str) -> SentCampaign:
    """Read one sent campaign from the V1 SQLite, read-only, or refuse."""
    conn = sqlite3.connect(f"file:{Path(sqlite_path)}?mode=ro", uri=True)
    try:
        conn.execute("pragma query_only = 1")
        row = conn.execute(
            "select name, subject, sender_email, target_attempt_count from outbound_campaign where campaign_id = ?",
            (v1_campaign_id,),
        ).fetchone()
        if row is None:
            raise SentCampaignRefused(f"V1 has no campaign {v1_campaign_id!r}")
        in_flight = conn.execute(
            "select count(*) from outbound_send_attempt where campaign_id = ? and mode = 'live' and result = 'in_flight'",
            (v1_campaign_id,),
        ).fetchone()[0]
        attempts = conn.execute(
            "select a.email_norm, a.attempted_at, a.gmail_message_id, r.state "
            "from outbound_send_attempt a join outbound_campaign_recipient r on r.id = a.recipient_id "
            "where a.campaign_id = ? and a.mode = 'live' and a.result = 'accepted' order by a.id",
            (v1_campaign_id,),
        ).fetchall()
    finally:
        conn.close()
    if in_flight:
        raise SentCampaignRefused(f"campaign {v1_campaign_id!r} has {in_flight} attempt(s) in flight; import after the wave ends")
    if not attempts:
        raise SentCampaignRefused(f"campaign {v1_campaign_id!r} was never sent (no accepted live attempt)")
    name, subject, sender, target = row
    recipients: dict[str, str] = {}
    out_attempts: list[SentAttempt] = []
    for email_norm, attempted_at, gmail_id, state in attempts:
        address = _address(email_norm)
        recipients[address] = "bounced" if (state or "").lower() == "bounced" else "sent"
        out_attempts.append(SentAttempt(address, attempted_at, gmail_id or None))
    return SentCampaign(
        v1_campaign_id=v1_campaign_id, name=name, subject=subject, sender_email=_address(sender),
        target_attempt_count=max(int(target or 1), len(out_attempts)),
        recipients=tuple(SentRecipient(a, s) for a, s in sorted(recipients.items())),
        attempts=tuple(sorted(out_attempts, key=lambda a: (a.address_norm, a.accepted_at))),
    )
