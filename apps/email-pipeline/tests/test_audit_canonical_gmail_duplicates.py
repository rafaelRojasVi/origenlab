from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "audit_canonical_gmail_duplicates.py"
DEDUPE = REPO / "scripts" / "maintenance" / "dedupe_canonical_gmail_messages.py"
LEGACY_DEDUPE = REPO / "scripts" / "tools" / "dedupe_emails_by_message_id.py"


def _seed(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY,
            source_file TEXT,
            folder TEXT,
            message_id TEXT,
            subject TEXT,
            date_iso TEXT,
            body TEXT,
            full_body_clean TEXT,
            top_reply_clean TEXT,
            body_text_clean TEXT,
            attachment_count INTEGER
        );
        CREATE TABLE attachments (
            id INTEGER PRIMARY KEY,
            email_id INTEGER NOT NULL,
            part_index INTEGER NOT NULL,
            filename TEXT,
            content_type TEXT,
            content_disposition TEXT,
            size_bytes INTEGER,
            content_id TEXT,
            is_inline INTEGER,
            sha256 TEXT,
            saved_path TEXT,
            created_at TEXT,
            FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE
        );
        """
    )
    src = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"
    mid = "<dup@example.com>"
    conn.executemany(
        """
        INSERT INTO emails (id, source_file, folder, message_id, subject, date_iso, body,
          full_body_clean, top_reply_clean, body_text_clean, attachment_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (1, src, "[Gmail]/Enviados", mid, "S", "2026-01-01T00:00:00Z", "b", "", "", "", 0),
            (2, src, "[Gmail]/Enviados", mid, "S", "2026-01-01T00:00:00Z", "b", "", "", "", 0),
            (3, "/mbox/contacto@labdelivery/x", "INBOX", mid, "S", "2026-01-01T00:00:00Z", "x", "", "", "", 0),
        ],
    )
    conn.execute("INSERT INTO attachments VALUES (1, 2, 0, 'f', 't', NULL, 0, NULL, 0, NULL, NULL, NULL)")
    conn.commit()
    conn.close()


def _email_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
    finally:
        conn.close()


def test_audit_canonical_gmail_duplicates_json(tmp_path: Path) -> None:
    db = tmp_path / "d.sqlite"
    _seed(db)
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["duplicate_message_id_groups"] == 1
    assert payload["duplicate_extra_rows"] == 1
    assert payload["duplicate_groups_with_multi_folder"] == 0


def test_dedupe_dry_run_does_not_delete(tmp_path: Path) -> None:
    db = tmp_path / "e.sqlite"
    _seed(db)
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    cp = subprocess.run(
        [sys.executable, str(DEDUPE), "--db", str(db)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert before == after == 3


def test_legacy_dedupe_wrapper_dry_run_does_not_delete(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite"
    _seed(db)
    before = _email_count(db)
    cp = subprocess.run(
        [sys.executable, str(LEGACY_DEDUPE), "--db", str(db)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    assert "Compatibility wrapper" in cp.stderr
    assert "Dry-run only" in cp.stderr
    assert _email_count(db) == before == 3


def test_legacy_dedupe_wrapper_apply_requires_backup_ack(tmp_path: Path) -> None:
    db = tmp_path / "legacy-apply.sqlite"
    _seed(db)
    cp = subprocess.run(
        [sys.executable, str(LEGACY_DEDUPE), "--db", str(db), "--apply"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 2
    assert "--apply requires --ack-sqlite-backup" in cp.stderr
    assert _email_count(db) == 3


def test_dedupe_apply_only_canonical_gmail(tmp_path: Path) -> None:
    db = tmp_path / "f.sqlite"
    _seed(db)
    cp = subprocess.run(
        [
            sys.executable,
            str(DEDUPE),
            "--db",
            str(db),
            "--apply",
            "--ack-sqlite-backup",
            "--log-dir",
            str(tmp_path / "logs"),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    conn = sqlite3.connect(db)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM emails ORDER BY id").fetchall()]
        labs = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE source_file LIKE '%labdelivery%'"
        ).fetchone()[0]
        att = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    finally:
        conn.close()
    assert labs == 1
    assert ids == [1, 3]
    assert att == 0
