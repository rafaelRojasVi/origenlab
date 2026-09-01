"""Regression: new ensure_* functions must be safe on an existing, populated DB."""

from __future__ import annotations

from pathlib import Path

from origenlab_email_pipeline.db import connect, init_schema
from origenlab_email_pipeline.manual_contact_status import ensure_manual_contact_status_table
from origenlab_email_pipeline.outbound_campaign_schema import (
    ensure_outbound_campaign_tables,
    outbound_campaign_tables_exist,
)
from origenlab_email_pipeline.outbound_campaign_store import create_campaign, get_campaign
from origenlab_email_pipeline.outreach_contact_state import ensure_outreach_contact_state_table


def test_ensure_tables_on_a_populated_existing_db(tmp_path: Path) -> None:
    db_path = tmp_path / "existing.sqlite"
    conn = connect(db_path)
    init_schema(conn)
    ensure_outreach_contact_state_table(conn)
    conn.execute(
        "INSERT INTO emails (source_file, folder, recipients) VALUES (?, ?, ?)",
        ("gmail:contacto@origenlab.cl/Sent Mail/1.eml", "[Gmail]/Sent Mail", "a@x.cl"),
    )
    conn.commit()

    # New tables layer on cleanly, twice (idempotent), without touching existing rows.
    ensure_outbound_campaign_tables(conn)
    ensure_outbound_campaign_tables(conn)
    ensure_manual_contact_status_table(conn)
    assert outbound_campaign_tables_exist(conn)

    create_campaign(
        conn, campaign_id="hielscher-sonicators-2026", name="Hielscher Sonicadores Laboratorio",
        sender_email="contacto@origenlab.cl", sender_name="Tatiana Vivanco | OrigenLab",
        subject="Sonicadores Hielscher para laboratorio | OrigenLab",
        target_attempt_count=2000, baseline_attempt_count=874,
    )
    conn.commit()
    assert get_campaign(conn, "hielscher-sonicators-2026") is not None

    email_count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert email_count == 1  # pre-existing data untouched
    conn.close()
