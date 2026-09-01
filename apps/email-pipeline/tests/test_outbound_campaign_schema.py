"""Tests for the outbound campaign ledger schema owner module."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.outbound_campaign_schema import (
    OUTBOUND_CAMPAIGN_SCHEMA_SQL,
    ensure_outbound_campaign_tables,
    outbound_campaign_tables_exist,
)


def test_ddl_defines_all_four_tables_with_no_fk_into_rebuildable_projections() -> None:
    for table in (
        "outbound_campaign",
        "outbound_campaign_recipient",
        "outbound_send_attempt",
        "manual_contact_status",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in OUTBOUND_CAMPAIGN_SCHEMA_SQL
    assert "campaign_id TEXT PRIMARY KEY" in OUTBOUND_CAMPAIGN_SCHEMA_SQL
    assert "UNIQUE(campaign_id, email_norm)" in OUTBOUND_CAMPAIGN_SCHEMA_SQL
    assert "UNIQUE(campaign_id, recipient_id, attempt_seq)" in OUTBOUND_CAMPAIGN_SCHEMA_SQL
    assert "REFERENCES" not in OUTBOUND_CAMPAIGN_SCHEMA_SQL


def test_ensure_creates_tables_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(str(db))
    assert not outbound_campaign_tables_exist(conn)
    ensure_outbound_campaign_tables(conn)
    assert outbound_campaign_tables_exist(conn)
    ensure_outbound_campaign_tables(conn)  # second call must not raise
    conn.close()


def test_campaign_unique_key_enforced(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    ensure_outbound_campaign_tables(conn)
    conn.execute(
        "INSERT INTO outbound_campaign (campaign_id, name, sender_email, sender_name, "
        "subject, target_attempt_count, baseline_attempt_count, status, created_at, updated_at) "
        "VALUES ('c1','n','s@x.cl','S','subj',10,0,'active','t','t')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO outbound_campaign (campaign_id, name, sender_email, sender_name, "
            "subject, target_attempt_count, baseline_attempt_count, status, created_at, updated_at) "
            "VALUES ('c1','n2','s2@x.cl','S2','subj2',20,0,'active','t','t')"
        )
    conn.close()


def test_recipient_uniqueness_per_campaign_and_email(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    ensure_outbound_campaign_tables(conn)
    conn.execute(
        "INSERT INTO outbound_campaign_recipient (campaign_id, email, email_norm, state, created_at, updated_at) "
        "VALUES ('c1','a@x.cl','a@x.cl','candidate','t','t')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO outbound_campaign_recipient (campaign_id, email, email_norm, state, created_at, updated_at) "
            "VALUES ('c1','A@X.CL','a@x.cl','candidate','t','t')"
        )
    # Same email in a different campaign is allowed.
    conn.execute(
        "INSERT INTO outbound_campaign_recipient (campaign_id, email, email_norm, state, created_at, updated_at) "
        "VALUES ('c2','a@x.cl','a@x.cl','candidate','t','t')"
    )
    conn.close()


def test_attempt_seq_unique_per_recipient(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "t.sqlite"))
    ensure_outbound_campaign_tables(conn)
    conn.execute(
        "INSERT INTO outbound_campaign_recipient (campaign_id, email, email_norm, state, created_at, updated_at) "
        "VALUES ('c1','a@x.cl','a@x.cl','reserved','t','t')"
    )
    conn.execute(
        "INSERT INTO outbound_send_attempt (campaign_id, recipient_id, email_norm, batch_id, attempt_seq, "
        "attempted_at, mode, result, created_at) VALUES ('c1',1,'a@x.cl','b1',1,'t','dry_run','accepted','t')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO outbound_send_attempt (campaign_id, recipient_id, email_norm, batch_id, attempt_seq, "
            "attempted_at, mode, result, created_at) VALUES ('c1',1,'a@x.cl','b2',1,'t','dry_run','accepted','t')"
        )
    conn.close()
