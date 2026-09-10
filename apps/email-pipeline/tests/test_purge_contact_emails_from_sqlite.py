"""CLI tests for purge_contact_emails_from_sqlite (subprocess, temp DB)."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "tools" / "purge_contact_emails_from_sqlite.py"


def _init_minimal_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            recipients TEXT
        );
        CREATE TABLE attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER NOT NULL,
            FOREIGN KEY(email_id) REFERENCES emails(id) ON DELETE CASCADE
        );
        CREATE TABLE contact_master (
            email TEXT PRIMARY KEY
        );
        CREATE TABLE opportunity_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER,
            entity_key TEXT
        );
        INSERT INTO emails (sender, recipients) VALUES
            ('Other <other@x.cl>', 'Target <keep@y.cl>'),
            ('From <contacto.ejemplo1@example.invalid>', 'Us <internal@origenlab.cl>');
        INSERT INTO attachments (email_id) VALUES (2);
        INSERT INTO contact_master (email) VALUES ('contacto.ejemplo1@example.invalid');
        INSERT INTO opportunity_signals (email_id, entity_key) VALUES (2, 'x');
        INSERT INTO opportunity_signals (email_id, entity_key) VALUES (NULL, 'contacto.ejemplo1@example.invalid');
        """
    )
    conn.commit()
    conn.close()


def test_purge_contact_emails_dry_run_counts(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _init_minimal_db(db)
    r = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--db",
            str(db),
            "--email",
            "contacto.ejemplo1@example.invalid",
        ],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "Unique emails.id to delete: 1" in r.stdout
    assert "Dry run" in r.stdout

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 2
    conn.close()


def test_purge_contact_emails_apply_removes_rows(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    _init_minimal_db(db)
    r = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--db",
            str(db),
            "--apply",
            "--email",
            "contacto.ejemplo1@example.invalid",
            "--no-commercial-candidates",
        ],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr + r.stdout

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM contact_master").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0] == 0
    conn.close()
