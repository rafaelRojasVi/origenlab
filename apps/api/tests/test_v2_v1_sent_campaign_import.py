"""Importing a sent V1-lane campaign into the CRM (`origenlab_api.v2.v1_sent_campaign_import`).

The extraction half runs against a throwaway SQLite carrying the V1 ledger's shape; the apply
half against a disposable PostgreSQL migrated from `supabase/migrations`. Every address is
fictitious (`.invalid`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_api.v2.v1_sent_campaign_import import (
    SentCampaignRefused,
    extract_sent_campaign,
)

# The V1 ledger's three tables, as `apps/email-pipeline/.../outbound_campaign_schema.py` creates
# them (only the columns the importer reads, plus the NOT NULL ones).
V1_DDL = """
create table outbound_campaign (
  campaign_id text primary key, name text not null, sender_email text not null,
  sender_name text not null, subject text not null, target_attempt_count integer not null,
  status text not null default 'active', created_at text not null, updated_at text not null);
create table outbound_campaign_recipient (
  id integer primary key autoincrement, campaign_id text not null, email text not null,
  email_norm text not null, state text not null, sent_at text, last_gmail_message_id text,
  created_at text not null, updated_at text not null, unique(campaign_id, email_norm));
create table outbound_send_attempt (
  id integer primary key autoincrement, campaign_id text not null, recipient_id integer not null,
  email_norm text not null, batch_id text not null, attempt_seq integer not null,
  attempted_at text not null, mode text not null, result text not null,
  gmail_message_id text, created_at text not null);
"""

CID = "promo-ficticia-2026-10"
T0 = "2026-10-05T12:00:00Z"


def _v1(path: Path, recipients: list[tuple[str, str]], attempts: list[tuple[str, str, str, str, str | None]],
        campaign_id: str = CID) -> Path:
    """recipients: (address, state); attempts: (address, attempted_at, mode, result, gmail_id)."""
    conn = sqlite3.connect(path)
    if not conn.execute("select 1 from sqlite_master where name='outbound_campaign'").fetchone():
        conn.executescript(V1_DDL)
    conn.execute(
        "insert or ignore into outbound_campaign values (?,?,?,?,?,?,?,?,?)",
        (campaign_id, "Promo Ficticia", "contacto@origenlab.cl", "OrigenLab", "Asunto ficticio", 5000, "active", T0, T0),
    )
    ids = {}
    for address, state in recipients:
        cur = conn.execute(
            "insert into outbound_campaign_recipient (campaign_id, email, email_norm, state, created_at, updated_at) "
            "values (?,?,?,?,?,?)", (campaign_id, address, address.lower(), state, T0, T0))
        ids[address.lower()] = cur.lastrowid
    for seq, (address, at, mode, result, gid) in enumerate(attempts, 1):
        conn.execute(
            "insert into outbound_send_attempt (campaign_id, recipient_id, email_norm, batch_id, attempt_seq, "
            "attempted_at, mode, result, gmail_message_id, created_at) values (?,?,?,?,?,?,?,?,?,?)",
            (campaign_id, ids[address.lower()], address.lower(), "b1", seq, at, mode, result, gid, at))
    conn.commit()
    conn.close()
    return path


def _three(tmp_path: Path) -> Path:
    return _v1(
        tmp_path / "v1.sqlite",
        [("uno@instituto.invalid", "sent"), ("dos@instituto.invalid", "sent"),
         ("tres@instituto.invalid", "bounced"), ("cuatro@instituto.invalid", "blocked"),
         ("cinco@instituto.invalid", "inactive")],
        [("uno@instituto.invalid", "2026-10-05T12:31:41Z", "live", "accepted", "g1"),
         ("dos@instituto.invalid", "2026-10-05T12:32:00Z", "live", "accepted", "g2"),
         ("tres@instituto.invalid", "2026-10-06T12:00:00Z", "live", "accepted", "g3"),
         ("cuatro@instituto.invalid", "2026-10-06T12:01:00Z", "live", "failed", None),
         ("uno@instituto.invalid", "2026-10-04T10:00:00Z", "dry_run", "accepted", None)],
    )


def test_extract_keeps_only_accepted_live_attempts(tmp_path: Path) -> None:
    c = extract_sent_campaign(_three(tmp_path), CID)
    assert len(c.attempts) == 3
    states: dict[str, int] = {}
    for r in c.recipients:
        states[r.v2_state] = states.get(r.v2_state, 0) + 1
    assert states == {"sent": 2, "bounced": 1}
    assert c.name == "Promo Ficticia" and c.subject == "Asunto ficticio"
    assert {a.gmail_message_id for a in c.attempts} == {"g1", "g2", "g3"}


def test_extract_skips_recipients_without_accepted_attempt(tmp_path: Path) -> None:
    c = extract_sent_campaign(_three(tmp_path), CID)
    assert {r.address_norm for r in c.recipients} == {
        "uno@instituto.invalid", "dos@instituto.invalid", "tres@instituto.invalid"}


def test_extract_refuses_in_flight(tmp_path: Path) -> None:
    p = _v1(tmp_path / "v1.sqlite", [("uno@instituto.invalid", "reserved")],
            [("uno@instituto.invalid", T0, "live", "in_flight", None)])
    with pytest.raises(SentCampaignRefused, match="in flight"):
        extract_sent_campaign(p, CID)


def test_extract_refuses_campaign_never_sent(tmp_path: Path) -> None:
    p = _v1(tmp_path / "v1.sqlite", [("uno@instituto.invalid", "candidate")], [])
    with pytest.raises(SentCampaignRefused, match="never sent"):
        extract_sent_campaign(p, CID)


def test_extract_refuses_unknown_campaign(tmp_path: Path) -> None:
    with pytest.raises(SentCampaignRefused, match="no campaign"):
        extract_sent_campaign(_three(tmp_path), "otra")


def test_manifest_carries_no_address(tmp_path: Path) -> None:
    c = extract_sent_campaign(_three(tmp_path), CID)
    assert "@" not in json.dumps(c.manifest())
    assert c.dedupe_key() == f"migration_manifest:v1-sent-campaign:{CID}"


def test_sqlite_opened_read_only(tmp_path: Path) -> None:
    p = _three(tmp_path)
    before = (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    extract_sent_campaign(p, CID)
    assert (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) == before
