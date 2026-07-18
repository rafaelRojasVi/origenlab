"""Synthetic tests for fail-closed immutable recovery-readiness."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from origenlab_api.backends.factory import validate_api_settings
from origenlab_api.settings import Settings
from origenlab_api.sqlite_ro import (
    SqliteAdmissionError,
    admit_immutable_candidate,
    existing_companions,
    fingerprint_path,
    open_operator_sqlite,
    open_sqlite_readonly,
    resolve_open_policy,
    sqlite_readonly_uri,
    validate_compaction_manifest_for_candidate,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "recovery_sqlite_readiness.py"


def _build_candidate(path: Path, *, rows: int = 20) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          date_iso TEXT,
          subject TEXT,
          source_file TEXT,
          folder TEXT,
          sender TEXT
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
    for i in range(1, rows + 1):
        conn.execute(
            "INSERT INTO emails (id, date_iso, subject, source_file, folder, sender) "
            "VALUES (?,?,?,?,?,?)",
            (
                i,
                f"2026-07-0{(i % 9) + 1}T10:00:00-04:00",
                f"subj-{i}",
                "gmail:contacto@origenlab.cl/[Gmail]/Enviados",
                "[Gmail]/Enviados",
                "client@example.cl",
            ),
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
    conn.close()


def _write_manifest(path: Path, candidate: Path, *, completed: bool = True, **overrides):
    fp = fingerprint_path(candidate)
    payload = {
        "completed": completed,
        "method": "VACUUM INTO",
        "destination_basename": candidate.name,
        "destination_size_bytes": fp.size_bytes,
        "verification": {
            "quick_check": "ok",
            "foreign_key_violation_count": 0,
            "schema_fingerprint": "abc",
            "critical_table_counts": {
                "emails": 20,
                "attachments": 10,
                "email_mart_features": 20,
                "conversations": None,
            },
        },
    }
    payload.update(overrides)
    if "verification" in overrides and isinstance(overrides["verification"], dict):
        payload["verification"] = {
            **{
                "quick_check": "ok",
                "foreign_key_violation_count": 0,
                "schema_fingerprint": "abc",
                "critical_table_counts": {
                    "emails": 20,
                    "attachments": 10,
                    "email_mart_features": 20,
                },
            },
            **overrides["verification"],
        }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_default_open_is_mode_ro_not_immutable(tmp_path: Path) -> None:
    db = tmp_path / "live.sqlite"
    _build_candidate(db)
    uri = sqlite_readonly_uri(db, immutable=False)
    assert "mode=ro" in uri
    assert "immutable=1" not in uri
    conn = open_sqlite_readonly(db, immutable=False)
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        assert int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]) == 20
    finally:
        conn.close()
    assert existing_companions(db) == []


def test_successful_confirmed_offline_immutable_open(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "candidate.sqlite.compaction.manifest.json"
    _build_candidate(db)
    _write_manifest(man, db)
    admission = admit_immutable_candidate(
        db, confirm_offline_copy=True, manifest_path=man
    )
    assert admission["admitted"] is True
    settings = Settings(
        sqlite_path=db,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        sqlite_compaction_manifest=man,
        api_backend="sqlite",
        postgres_url=None,
    )
    conn = open_operator_sqlite(db, settings=settings)
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.Error):
            conn.execute("CREATE TABLE should_fail(x INTEGER)")
    finally:
        conn.close()
    assert existing_companions(db) == []


def test_immutable_flag_without_offline_confirmation(tmp_path: Path) -> None:
    settings = Settings(
        sqlite_path=tmp_path / "x.sqlite",
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=False,
        api_backend="sqlite",
    )
    with pytest.raises(SqliteAdmissionError, match="CONFIRM_OFFLINE_COPY"):
        resolve_open_policy(settings)


def test_production_path_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prod = tmp_path / "production_emails.sqlite"
    _build_candidate(prod)
    man = tmp_path / "m.json"
    _write_manifest(man, prod)

    class _EP:
        def resolved_sqlite_path(self):
            return prod

    monkeypatch.setattr(
        "origenlab_email_pipeline.config.load_settings",
        lambda: _EP(),
    )
    # Patch is_configured_production_db path used inside sqlite_ro
    with patch(
        "origenlab_api.sqlite_ro.is_configured_production_db",
        return_value=True,
    ):
        with pytest.raises(SqliteAdmissionError, match="production"):
            admit_immutable_candidate(
                prod, confirm_offline_copy=True, manifest_path=man
            )


def test_symlink_alias_refusal(tmp_path: Path) -> None:
    real = tmp_path / "candidate.sqlite"
    link = tmp_path / "alias.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(real)
    _write_manifest(man, real)
    link.symlink_to(real)
    with pytest.raises(SqliteAdmissionError, match="symlink"):
        admit_immutable_candidate(link, confirm_offline_copy=True, manifest_path=man)


def test_hardlink_device_inode_production_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prod = tmp_path / "prod.sqlite"
    alias = tmp_path / "alias.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(prod)
    os.link(prod, alias)
    _write_manifest(man, alias)
    with patch(
        "origenlab_api.sqlite_ro.is_configured_production_db",
        return_value=True,
    ):
        with pytest.raises(SqliteAdmissionError, match="production"):
            admit_immutable_candidate(
                alias, confirm_offline_copy=True, manifest_path=man
            )


def test_missing_manifest(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    _build_candidate(db)
    with pytest.raises(SqliteAdmissionError, match="manifest not found"):
        admit_immutable_candidate(
            db,
            confirm_offline_copy=True,
            manifest_path=tmp_path / "missing.json",
        )


def test_incomplete_manifest(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db, completed=False)
    with pytest.raises(SqliteAdmissionError, match="completed!=true"):
        admit_immutable_candidate(db, confirm_offline_copy=True, manifest_path=man)


def test_mismatched_manifest_basename(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db, destination_basename="other.sqlite")
    with pytest.raises(SqliteAdmissionError, match="destination_basename"):
        admit_immutable_candidate(db, confirm_offline_copy=True, manifest_path=man)


def test_wal_shm_journal_refusal(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db)
    Path(str(db) + "-wal").write_bytes(b"")
    with pytest.raises(SqliteAdmissionError, match="companion"):
        admit_immutable_candidate(db, confirm_offline_copy=True, manifest_path=man)


def test_uri_special_character_paths(tmp_path: Path) -> None:
    weird = tmp_path / "cand#idate space?.sqlite"
    _build_candidate(weird)
    uri = sqlite_readonly_uri(weird, immutable=True)
    assert "mode=ro&immutable=1" in uri
    assert " " not in uri.split("?", 1)[0]
    assert "%20" in uri or "%23" in uri or "%3F" in uri
    conn = open_sqlite_readonly(weird, immutable=True)
    try:
        assert int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]) == 20
    finally:
        conn.close()


def test_query_only_verification(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    _build_candidate(db)
    conn = open_sqlite_readonly(db, immutable=True, query_only=True)
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
    finally:
        conn.close()


def test_fingerprint_drift_detected(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db)
    fp = fingerprint_path(db)
    with pytest.raises(SqliteAdmissionError, match="size does not match"):
        validate_compaction_manifest_for_candidate(
            json.loads(man.read_text()),
            db,
            fingerprint=type(fp)(
                size_bytes=fp.size_bytes + 1,
                mtime_ns=fp.mtime_ns,
                device=fp.device,
                inode=fp.inode,
            ),
        )


def test_startup_failure_without_fallback(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    _build_candidate(db)
    settings = Settings(
        sqlite_path=db,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        sqlite_compaction_manifest=None,
        api_backend="sqlite",
        postgres_url=None,
    )
    with pytest.raises(ValueError, match="recovery admission failed"):
        validate_api_settings(settings)


def test_startup_refuses_postgres_in_recovery(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db)
    settings = Settings(
        sqlite_path=db,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        sqlite_compaction_manifest=man,
        api_backend="sqlite",
        postgres_url="postgresql://example.invalid/db",
    )
    with pytest.raises(ValueError, match="POSTGRES_URL"):
        validate_api_settings(settings)


def test_repositories_use_central_policy(tmp_path: Path) -> None:
    from origenlab_api.main import create_app
    from origenlab_api.settings import get_settings

    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    active = tmp_path / "current"
    active.mkdir()
    (active / "manifest.json").write_text("{}", encoding="utf-8")
    _build_candidate(db)
    _write_manifest(man, db)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        sqlite_path=db,
        active_current=active,
        api_backend="sqlite",
        postgres_url=None,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        sqlite_compaction_manifest=man,
    )
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["recovery_mode"] is True
    assert health["sqlite_immutable"] is True
    assert health["sqlite_query_only"] is True
    assert "/home/" not in json.dumps(health)
    recent = client.get("/emails/recent", params={"limit": 5})
    assert recent.status_code == 200
    warm = client.get("/cases/warm", params={"limit": 5})
    assert warm.status_code == 200
    assert existing_companions(db) == []
    app.dependency_overrides.clear()


def test_harness_success_and_exit_codes(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "candidate.sqlite.compaction.manifest.json"
    _build_candidate(db)
    _write_manifest(man, db)
    env = {
        **os.environ,
        "ORIGENLAB_SQLITE_PATH": str(tmp_path / "production_emails.sqlite"),
    }
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--manifest",
            str(man),
            "--confirm-offline-copy",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["completed"] is True
    assert payload["checks"]["query_only"] == 1
    assert payload["fingerprint_unchanged"] is True
    assert "/home/" not in cp.stdout

    cp2 = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--manifest", str(man), "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert cp2.returncode == 2

    cp3 = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--manifest",
            str(tmp_path / "missing.json"),
            "--confirm-offline-copy",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert cp3.returncode in (2, 3)


def test_no_sidecars_before_after_and_mutation_detection(tmp_path: Path) -> None:
    db = tmp_path / "candidate.sqlite"
    man = tmp_path / "m.json"
    _build_candidate(db)
    _write_manifest(man, db)
    before = fingerprint_path(db)
    settings = Settings(
        sqlite_path=db,
        sqlite_immutable_ro=True,
        sqlite_confirm_offline_copy=True,
        sqlite_compaction_manifest=man,
        api_backend="sqlite",
        postgres_url=None,
    )
    conn = open_operator_sqlite(db, settings=settings)
    conn.close()
    assert existing_companions(db) == []
    assert fingerprint_path(db) == before
