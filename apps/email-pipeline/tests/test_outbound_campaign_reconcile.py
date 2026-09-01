"""Tests for reconciliation: updates campaign rows from Sent history + suppression evidence,
never mutates contact_email_suppression itself."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.contact_email_suppression import (
    ensure_contact_email_suppression_table,
    upsert_contact_email_suppression,
    validate_contact_email_suppression_payload,
)
from origenlab_email_pipeline.db import connect, init_schema
from origenlab_email_pipeline.outbound_campaign_reconcile import reconcile_campaign
from origenlab_email_pipeline.outbound_campaign_schema import ensure_outbound_campaign_tables
from origenlab_email_pipeline.outbound_campaign_store import (
    create_campaign,
    record_attempt,
    upsert_recipient_candidate,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "t.sqlite")
    init_schema(c)
    ensure_outbound_campaign_tables(c)
    ensure_contact_email_suppression_table(c)
    create_campaign(
        c, campaign_id="hielscher-sonicators-2026", name="Hielscher Sonicadores Laboratorio",
        sender_email="contacto@origenlab.cl", sender_name="Tatiana Vivanco | OrigenLab",
        subject="Sonicadores Hielscher para laboratorio | OrigenLab",
        target_attempt_count=2000, baseline_attempt_count=874,
    )
    yield c
    c.close()


def _seed_sent_email(conn: sqlite3.Connection, *, gmail_user: str, recipient: str) -> None:
    conn.execute(
        "INSERT INTO emails (source_file, folder, recipients) VALUES (?, ?, ?)",
        (f"gmail:{gmail_user}/Sent Mail/1.eml", "[Gmail]/Sent Mail", recipient),
    )
    conn.commit()


def test_reconcile_marks_confirmed_sent_from_sent_history(conn: sqlite3.Connection) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    record_attempt(
        conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl",
        batch_id="b1", mode="live", result="accepted", gmail_message_id="M1",
    )
    conn.commit()
    _seed_sent_email(conn, gmail_user="contacto@origenlab.cl", recipient="a@x.cl")

    summary = reconcile_campaign(
        conn, "hielscher-sonicators-2026", gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Sent Mail",),
    )
    assert summary.confirmed_sent == 1
    status = conn.execute(
        "SELECT reconciliation_status FROM outbound_send_attempt WHERE recipient_id=?", (rid,)
    ).fetchone()[0]
    assert status == "confirmed_sent"


def test_reconcile_marks_bounced_from_suppression_without_mutating_suppression_table(conn: sqlite3.Connection) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="bounced@x.cl", source_kind="manual")
    record_attempt(
        conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="bounced@x.cl",
        batch_id="b1", mode="live", result="accepted", gmail_message_id="M2",
    )
    conn.commit()
    upsert_contact_email_suppression(
        conn,
        payload=validate_contact_email_suppression_payload(
            email="bounced@x.cl", suppression_reason_code="bounce_no_such_user",
            suppression_reason_text=None, suppression_source="ndr", last_bounced_at=None, updated_by="test",
        ),
    )
    conn.commit()
    before_suppression_rows = conn.execute("SELECT COUNT(*) FROM contact_email_suppression").fetchone()[0]

    summary = reconcile_campaign(
        conn, "hielscher-sonicators-2026", gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Sent Mail",),
    )
    assert summary.bounced == 1
    recipient_row = conn.execute(
        "SELECT state, bounce_state FROM outbound_campaign_recipient WHERE id=?", (rid,)
    ).fetchone()
    assert recipient_row == ("bounced", "bounced")
    after_suppression_rows = conn.execute("SELECT COUNT(*) FROM contact_email_suppression").fetchone()[0]
    assert after_suppression_rows == before_suppression_rows  # reconciliation never writes suppression


def test_reconcile_tolerates_missing_suppression_table(tmp_path: Path) -> None:
    """A DB without contact_email_suppression ever created (e.g. campaign-only DB) must not error."""
    c = connect(tmp_path / "no_suppression.sqlite")
    init_schema(c)
    ensure_outbound_campaign_tables(c)
    create_campaign(
        c, campaign_id="hielscher-sonicators-2026", name="N", sender_email="s@x.cl",
        sender_name="S", subject="Subj", target_attempt_count=2000, baseline_attempt_count=874,
    )
    rid = upsert_recipient_candidate(c, campaign_id="hielscher-sonicators-2026", email="a@x.cl", source_kind="manual")
    record_attempt(
        c, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="a@x.cl",
        batch_id="b1", mode="live", result="accepted", gmail_message_id="M1",
    )
    c.commit()
    summary = reconcile_campaign(
        c, "hielscher-sonicators-2026", gmail_user="contacto@origenlab.cl", sent_folders=("[Gmail]/Sent Mail",),
    )
    assert summary.no_evidence == 1
    c.close()


def test_reconcile_marks_no_evidence_when_neither_matches(conn: sqlite3.Connection) -> None:
    rid = upsert_recipient_candidate(conn, campaign_id="hielscher-sonicators-2026", email="unknown@x.cl", source_kind="manual")
    record_attempt(
        conn, campaign_id="hielscher-sonicators-2026", recipient_id=rid, email_norm="unknown@x.cl",
        batch_id="b1", mode="live", result="accepted", gmail_message_id="M3",
    )
    conn.commit()
    summary = reconcile_campaign(
        conn, "hielscher-sonicators-2026", gmail_user="contacto@origenlab.cl",
        sent_folders=("[Gmail]/Sent Mail",),
    )
    assert summary.no_evidence == 1
    assert summary.checked == 1
