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
from datetime import UTC, datetime
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
    v2_state: str  # always 'sent' (bounces live in outbound.contact_control)


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
    for email_norm, attempted_at, gmail_id, _v1_state in attempts:
        address = _address(email_norm)
        # Always «sent»: V1 flips a recipient to «bounced» after later reconciles, and a stored
        # state must never change (the archived trigger forbids it). Bounces are read from the
        # block list (outbound.contact_control), which is where they are reliable anyway.
        recipients[address] = "sent"
        out_attempts.append(SentAttempt(address, attempted_at, gmail_id or None))
    return SentCampaign(
        v1_campaign_id=v1_campaign_id, name=name, subject=subject, sender_email=_address(sender),
        target_attempt_count=max(int(target or 1), len(out_attempts)),
        recipients=tuple(SentRecipient(a, s) for a, s in sorted(recipients.items())),
        attempts=tuple(sorted(out_attempts, key=lambda a: (a.address_norm, a.accepted_at))),
    )


@dataclass
class ImportResult:
    dry_run: bool
    campaign_id: str | None = None
    inserted: dict[str, int] = field(default_factory=dict)
    already_present: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"dry_run": self.dry_run, "campaign_id": self.campaign_id,
                "inserted": self.inserted, "already_present": self.already_present}


def _instant(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def apply_sent_campaign(conn: Any, campaign: SentCampaign, *, dry_run: bool = True) -> ImportResult:
    """Load ``campaign`` through ``conn`` in one transaction; roll it back when ``dry_run``.

    ``conn`` is a psycopg connection with autocommit off whose login may ``set role``
    :data:`IMPORT_ROLE`. Inserts only what the target lacks; refuses when anything stored
    disagrees with the extraction.
    """
    result = ImportResult(dry_run=dry_run)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(f"set local role {IMPORT_ROLE}")
            except Exception as exc:  # noqa: BLE001 - re-raised as this boundary's refusal
                raise SentCampaignRefused(
                    f"cannot take the {IMPORT_ROLE} role on this target ({exc.__class__.__name__}); connect as a "
                    "login holding SET membership of it (origenlab_migrator)") from exc
            cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (campaign.dedupe_key(),))
            _apply(cur, campaign, result)
    except Exception:
        conn.rollback()
        raise
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return result


def _apply(cur: Any, campaign: SentCampaign, result: ImportResult) -> None:
    cur.execute("select id from comms.mailbox where address_norm = %s", (campaign.sender_email,))
    mailbox = cur.fetchone()
    if mailbox is None:
        raise SentCampaignRefused("the campaign's sender mailbox is not in the target")
    mailbox_id = mailbox[0]
    cur.execute(
        "select c.id from outbound.campaign c join evidence.source_record s on s.id = c.origin_source_record_id "
        "where s.kind = 'migration_manifest' and s.payload->>'v1_campaign_id' = %s and s.dedupe_key <> %s",
        (campaign.v1_campaign_id, campaign.dedupe_key()),
    )
    if cur.fetchone() is not None:
        raise SentCampaignRefused("this V1 campaign is already in the target under another import; refusing a second copy")

    manifest = campaign.manifest()
    sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    cur.execute("select id from evidence.source_record where dedupe_key = %s", (campaign.dedupe_key(),))
    found = cur.fetchone()
    inserted = {"evidence.source_record": 0, "outbound.campaign": 0,
                "outbound.campaign_recipient": 0, "outbound.send_attempt": 0}
    present = {"outbound.campaign_recipient": 0, "outbound.send_attempt": 0}
    if found is None:
        # The manifest records what the first import saw; later waves append under it.
        cur.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, payload_sha256, review_status) "
            "values ('migration_manifest', %s, %s, %s, 'pending') returning id",
            (campaign.dedupe_key(), json.dumps({**manifest, "sha256": sha}), sha),
        )
        source_id = cur.fetchone()[0]
        inserted["evidence.source_record"] = 1
    else:
        source_id = found[0]

    cur.execute("select id, name, status from outbound.campaign where origin_source_record_id = %s", (source_id,))
    rows = cur.fetchall()
    if len(rows) > 1:
        raise SentCampaignRefused("the target holds two campaigns for this manifest")
    if rows:
        campaign_id, name, status = rows[0]
        if (name, status) != (campaign.name, "archived"):
            raise SentCampaignRefused("the stored campaign differs from the extraction (name or status)")
    else:
        # The September V1 campaigns came in through the Wave importer, whose manifest names no
        # single campaign: a same-named campaign is that import, and a second copy is refused.
        cur.execute("select 1 from outbound.campaign where name = %s", (campaign.name,))
        if cur.fetchone() is not None:
            raise SentCampaignRefused("a campaign with this name is already in the target under another import")
        cur.execute(
            "insert into outbound.campaign (name, subject, status, mailbox_id, max_sends, recontact_interval_days, "
            "policy_include_suppliers, origin_source_record_id) values (%s, %s, 'archived', %s, %s, null, false, %s) "
            "returning id",
            (campaign.name, campaign.subject, mailbox_id, campaign.target_attempt_count, source_id),
        )
        campaign_id = cur.fetchone()[0]
        inserted["outbound.campaign"] = 1
    result.campaign_id = str(campaign_id)

    cur.execute("select address_norm, state, id from outbound.campaign_recipient where campaign_id = %s", (campaign_id,))
    stored = {a: (s, i) for a, s, i in cur.fetchall()}
    planned = {r.address_norm: r.v2_state for r in campaign.recipients}
    if set(stored) - set(planned) or any(stored[a][0] != planned[a] for a in stored):
        raise SentCampaignRefused("the stored audience differs from the extraction")
    recipient_ids = {a: i for a, (_, i) in stored.items()}
    for address, state in sorted(planned.items()):
        if address in stored:
            present["outbound.campaign_recipient"] += 1
            continue
        cur.execute(
            "insert into outbound.campaign_recipient (campaign_id, address_norm, state, exclusion_reasons) "
            "values (%s, %s, %s, '{}') returning id", (campaign_id, address, state))
        recipient_ids[address] = cur.fetchone()[0]
        inserted["outbound.campaign_recipient"] += 1

    cur.execute(
        "select address_norm, accepted_at, provider_message_id from outbound.send_attempt "
        "where campaign_id = %s and origin_source_record_id = %s", (campaign_id, source_id))
    existing = Counter((a, _instant(t), m) for a, t, m in cur.fetchall())
    wanted = Counter((a.address_norm, _instant(a.accepted_at), a.gmail_message_id) for a in campaign.attempts)
    if existing - wanted:
        raise SentCampaignRefused("the stored send ledger differs from the extraction")
    present["outbound.send_attempt"] = sum(existing.values())
    for (address, at, gmail_id), n in sorted((wanted - existing).items()):
        for _ in range(n):
            cur.execute(
                "insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id, mailbox_id, address_norm, "
                "submission_state, delivery_state, accepted_at, provider_message_id, origin_source_record_id) "
                "values ('marketing', %s, %s, %s, %s, 'accepted', 'pending', %s, %s, %s)",
                (campaign_id, recipient_ids[address], mailbox_id, address, at, gmail_id, source_id))
            inserted["outbound.send_attempt"] += 1
    result.inserted, result.already_present = inserted, present
