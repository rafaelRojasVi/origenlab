from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from origenlab_email_pipeline.db import connect, init_schema
from origenlab_email_pipeline.outreach_ingest_sync import (
    _batch_hits_from_ndr_dsn,
    apply_bounce_batch_scan,
    classify_ndr_suppression_reason,
    load_batch_emails_from_file,
    merge_suppression_reason,
    scan_batch_against_ingested_bounces,
)


def _utc_iso_days_ago(days: int) -> str:
    """Stamp inside ``since_days`` windows regardless of when the test runs (UTC date axis)."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_load_batch_emails_from_file_parses_and_dedupes() -> None:
    text = """
# c
alice@Example.COM
bob@y.cl \t
alice@example.com
"""
    assert load_batch_emails_from_file(text) == ["alice@example.com", "bob@y.cl"]


def test_classify_ndr_suppression_reason() -> None:
    assert classify_ndr_suppression_reason("550 5.1.1 User unknown") == "bounce_no_such_user"
    assert classify_ndr_suppression_reason("try again later") == "bounce_other"


def test_merge_suppression_reason() -> None:
    assert merge_suppression_reason("bounce_other", "bounce_no_such_user") == "bounce_no_such_user"
    assert merge_suppression_reason("bounce_other", "bounce_other") == "bounce_other"


def test_scan_batch_spanish_microsoft_dsn_snippet(tmp_path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    init_schema(conn)
    body = (
        "** No se encontró la dirección **\n\n"
        "Tu mensaje no se entregó a bodegalosromos@antufen.com porque la dirección no se encuentra.\n"
    )
    conn.execute(
        """
        INSERT INTO emails (
          source_file, folder, message_id, subject, sender, recipients,
          date_raw, date_iso, body, attachment_count, has_attachments
        ) VALUES (
          'gmail:x/INBOX', 'INBOX', '<ndr_es>', 'Delivery Status Notification (Failure)',
          'Microsoft Outlook <microsoftemail@notification.microsoft.com>', 'a@b.cl',
          '', ?,
          ?, 0, 0
        )
        """,
        (_utc_iso_days_ago(5), body),
    )
    conn.commit()
    r = scan_batch_against_ingested_bounces(
        conn, ["bodegalosromos@antufen.com", "other@x.cl"], since_days=30, source_like="gmail:%"
    )
    assert r.bad.keys() == {"bodegalosromos@antufen.com"}
    assert r.good == ["other@x.cl"]
    conn.close()


def test_scan_batch_finds_bounce_mention(tmp_path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO emails (
          source_file, folder, message_id, subject, sender, recipients,
          date_raw, date_iso, body, attachment_count, has_attachments
        ) VALUES (
          'gmail:contacto@origenlab.cl/INBOX', 'INBOX', '<ndr1>', 'Undeliverable',
          'Mail Delivery Subsystem <mailer-daemon@googlemail.com>', 'contacto@origenlab.cl',
          '', ?,
          '550 5.1.1 baduser@lab.cl does not exist', 0, 0
        )
        """,
        (_utc_iso_days_ago(5),),
    )
    conn.commit()

    batch = ["baduser@lab.cl", "okuser@lab.cl"]
    r = scan_batch_against_ingested_bounces(conn, batch, since_days=90, source_like="gmail:%")
    assert r.bad.keys() == {"baduser@lab.cl"}
    assert r.good == ["okuser@lab.cl"]
    conn.close()


def test_apply_writes_suppression_and_contacted(tmp_path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO emails (
          source_file, folder, message_id, subject, sender, recipients,
          date_raw, date_iso, body, attachment_count, has_attachments
        ) VALUES (
          'gmail:x/INBOX', 'INBOX', '<ndr1>', 'Delivery Status Notification (Failure)',
          'MAILER-DAEMON@example.com', 'sender@origenlab.cl',
          '', ?,
          'The following address failed: dead@client.cl permanent error 5.1.1', 0, 0
        )
        """,
        (_utc_iso_days_ago(5),),
    )
    conn.commit()

    batch = ["dead@client.cl", "alive@client.cl"]
    scan = scan_batch_against_ingested_bounces(conn, batch, since_days=30, source_like="gmail:%")
    out = apply_bounce_batch_scan(
        conn,
        scan,
        updated_by="test",
        suppression_source="test_bounce",
        outreach_source="test_outreach",
        outreach_notes="pilot",
        mark_contacted_for_good=True,
    )
    conn.commit()

    assert out["suppressed"] == 1
    assert out["marked_contacted"] == 1

    sup = conn.execute(
        "SELECT email, suppression_reason_code FROM contact_email_suppression WHERE email = ?",
        ("dead@client.cl",),
    ).fetchone()
    assert sup is not None
    assert sup[1] == "bounce_no_such_user"

    st = conn.execute(
        "SELECT state FROM outreach_contact_state WHERE contact_email_norm = ?",
        ("alive@client.cl",),
    ).fetchone()
    assert st is not None
    assert st[0] == "contacted"
    conn.close()


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "sync_outreach_batch_from_ingested_bounces.py"


def test_bcc_visible_to_not_suppressed_when_ndr_only_for_bcc_recipient(tmp_path) -> None:
    """NDR for dfuente@durandin.cl must not suppress visible To mle@mlelab.cl in quoted headers."""
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    init_schema(conn)
    body = (
        "Your message to dfuente@durandin.cl couldn't be delivered.\n"
        "dfuente wasn't found at durandin.cl.\n\n"
        "--- Original message ---\n"
        "To: mle@mlelab.cl\n"
        "Bcc: dfuente@durandin.cl\n"
        "Subject: CYBERDAY OrigenLab\n"
    )
    conn.execute(
        """
        INSERT INTO emails (
          source_file, folder, message_id, subject, sender, recipients,
          date_raw, date_iso, body, attachment_count, has_attachments
        ) VALUES (
          'gmail:contacto@origenlab.cl/INBOX', 'INBOX', '<ndr-cyber-bcc>',
          'Undeliverable: CYBERDAY OrigenLab — equipos de laboratorio seleccionados hasta el 7 de junio',
          'postmaster@corp.onmicrosoft.com', 'contacto@origenlab.cl',
          '', ?, ?, 0, 0
        )
        """,
        (_utc_iso_days_ago(1), body),
    )
    conn.commit()
    batch = ["mle@mlelab.cl", "dfuente@durandin.cl"]
    r = scan_batch_against_ingested_bounces(conn, batch, since_days=7, source_like="gmail:%")
    assert "dfuente@durandin.cl" in r.bad
    assert "mle@mlelab.cl" not in r.bad
    assert "mle@mlelab.cl" in r.good
    hits = _batch_hits_from_ndr_dsn(body, frozenset(batch))
    assert hits == {"dfuente@durandin.cl"}
    conn.close()


def test_sync_outreach_batch_cli_json(tmp_path) -> None:
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO emails (
          source_file, folder, message_id, subject, sender, recipients,
          date_raw, date_iso, body, attachment_count, has_attachments
        ) VALUES (
          'gmail:x/INBOX', 'INBOX', '<ndr1>', 'Returned mail: see transcript for details',
          'postmaster@corp.cl', 'a@b.cl',
          '', ?,
          'failed for x@y.cl (reason 5.1.1)', 0, 0
        )
        """,
        (_utc_iso_days_ago(5),),
    )
    conn.commit()
    conn.close()

    batch = tmp_path / "b.txt"
    batch.write_text("x@y.cl\n", encoding="utf-8")

    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db_path),
            "--batch-file",
            str(batch),
            "--since-days",
            "30",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    data = json.loads(run.stdout)
    assert data["bad_count"] == 1
    assert data["good_count"] == 0
