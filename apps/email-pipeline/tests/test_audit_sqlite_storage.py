"""Read-only tests for scripts/qa/audit_sqlite_storage.py."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "audit_sqlite_storage.py"


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
            body TEXT
        );
        """
    )
    src_sent = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"
    conn.executemany(
        """
        INSERT INTO emails (id, source_file, folder, message_id, subject, date_iso, body)
        VALUES (?,?,?,?,?,?,?)
        """,
        [
            (1, src_sent, "[Gmail]/Enviados", "<a@x>", "S1", "2026-01-01T00:00:00Z", "b"),
            (2, src_sent, "[Gmail]/Enviados", "<a@x>", "S1dup", "2026-01-01T00:00:00Z", "b"),
            (3, src_sent, "[Gmail]/Enviados", None, "S-missing", "2026-02-01T00:00:00Z", "b"),
            (
                4,
                src_sent,
                "[Gmail]/Enviados",
                "<future@x>",
                "S-future",
                "2033-01-15T00:00:00Z",
                "",
            ),
            (
                5,
                "/mbox/contacto@labdelivery/x",
                "INBOX",
                "<legacy@x>",
                "legacy",
                "2020-01-01T00:00:00Z",
                "x",
            ),
        ],
    )
    conn.commit()
    conn.close()


def test_audit_sqlite_storage_quick_json(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
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
    assert payload["read_only"] is True
    assert payload["mutation"] is False
    assert payload["dbstat_included"] is False
    assert payload["email_row_count"] == 5
    assert payload["canonical_sent_row_count"] == 4
    assert payload["canonical_sent_distinct_message_id_count"] == 2
    assert payload["canonical_sent_missing_message_id_count"] == 1
    assert payload["suspicious_future_date_count"] == 1
    assert payload["page_size"] > 0
    assert payload["page_count"] > 0
    assert "subject" not in json.dumps(payload.get("suspicious_future_date_rows_metadata"))
    future = payload["suspicious_future_date_rows_metadata"][0]
    assert future["id"] == 4
    assert future["empty_body"] is True
    assert payload["mutation"] is False
    assert payload["read_only"] is True


def test_audit_sqlite_storage_storage_only_json(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE emails (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--storage-only", "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["mode"] == "storage-only"
    assert "email_row_count" not in payload
    assert payload["read_only"] is True


def test_audit_sqlite_storage_include_dbstat(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _seed(db)
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--json", "--include-dbstat"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr + cp.stdout
    payload = json.loads(cp.stdout)
    assert payload["dbstat_included"] is True
    assert "dbstat" in payload
    assert "WARNING" in cp.stderr or "heavy" in cp.stderr.lower()


def test_audit_never_mutates(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _seed(db)
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--json", "--include-dbstat"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    after = sqlite3.connect(db).execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    assert before == after == 5
