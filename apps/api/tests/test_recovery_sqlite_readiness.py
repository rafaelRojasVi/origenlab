"""Synthetic tests for immutable recovery-readiness SQLite opens."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from origenlab_api.sqlite_ro import existing_companions, open_sqlite_readonly

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "recovery_sqlite_readiness.py"


def _build_candidate(path: Path, *, wal_header: bool = True) -> None:
    conn = sqlite3.connect(path)
    if wal_header:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          date_iso TEXT,
          subject TEXT,
          source_file TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE attachments (
          id INTEGER PRIMARY KEY,
          email_id INTEGER,
          filename TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE email_mart_features (
          email_id INTEGER PRIMARY KEY,
          score REAL
        )
        """
    )
    for i in range(1, 21):
        conn.execute(
            "INSERT INTO emails (id, date_iso, subject, source_file) VALUES (?,?,?,?)",
            (i, f"2026-07-0{(i % 9) + 1}", f"subj-{i}", "gmail:x/INBOX"),
        )
        if i % 2 == 0:
            conn.execute(
                "INSERT INTO attachments (id, email_id, filename) VALUES (?,?,?)",
                (i, i, f"f{i}.pdf"),
            )
        conn.execute(
            "INSERT INTO email_mart_features (email_id, score) VALUES (?,?)",
            (i, float(i)),
        )
    conn.commit()
    if wal_header:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for suf in ("-wal", "-shm", "-journal"):
        companion = Path(str(path) + suf)
        if companion.exists():
            companion.unlink()


def test_immutable_open_rejects_writes_and_leaves_no_sidecars(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    _build_candidate(db)
    assert existing_companions(db) == []
    conn = open_sqlite_readonly(db, immutable=True)
    try:
        assert int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]) == 20
        with pytest.raises(sqlite3.Error):
            conn.execute("CREATE TABLE should_fail(x INTEGER)")
    finally:
        conn.close()
    assert existing_companions(db) == []
    assert db.stat().st_size > 0


def test_recovery_script_json_success(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    _build_candidate(db)
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--confirm-offline-copy",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "ORIGENLAB_SQLITE_PATH": str(tmp_path / "production.sqlite")},
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["completed"] is True
    assert payload["checks"]["emails_count"] == 20
    assert payload["checks"]["write_rejected"] is True
    assert payload["fingerprint_unchanged"] is True
    assert existing_companions(db) == []


def test_recovery_script_requires_confirm(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    _build_candidate(db)
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 2
    assert "confirm-offline-copy" in cp.stderr


def test_recovery_script_refuses_known_production_path(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    prod = fake_home / "data" / "origenlab-email" / "sqlite" / "emails.sqlite"
    prod.parent.mkdir(parents=True)
    _build_candidate(prod)
    env = {**os.environ, "HOME": str(fake_home)}
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(prod),
            "--confirm-offline-copy",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert cp.returncode == 2
    assert "refusing known production" in cp.stderr


def test_api_immutable_flag_uses_recovery_open(tmp_path: Path) -> None:
    from origenlab_api.main import create_app
    from origenlab_api.settings import Settings, get_settings

    db = tmp_path / "candidate.sqlite"
    active = tmp_path / "current"
    active.mkdir()
    (active / "manifest.json").write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          date_iso TEXT,
          source_file TEXT,
          folder TEXT,
          sender TEXT,
          subject TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO emails (date_iso, source_file, folder, sender, subject) VALUES (?,?,?,?,?)",
        (
            "2026-07-17T10:00:00-04:00",
            "gmail:contacto@origenlab.cl/[Gmail]/Enviados",
            "[Gmail]/Enviados",
            "client@example.cl",
            "Cotización",
        ),
    )
    conn.commit()
    conn.close()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=db,
        active_current=active,
        api_backend="sqlite",
        postgres_url=None,
        sqlite_immutable_ro=True,
    )
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    recent = client.get("/emails/recent", params={"limit": 5})
    assert recent.status_code == 200
    body = recent.json()
    assert "items" in body or "meta" in body or isinstance(body, dict)
    assert existing_companions(db) == []
    app.dependency_overrides.clear()
