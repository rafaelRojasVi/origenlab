"""Tests for scripts/migrate/sqlite_outbound_sidecars_to_postgres.py."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from datetime import timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "migrate" / "sqlite_outbound_sidecars_to_postgres.py"


def _load():
    spec = importlib.util.spec_from_file_location("sqlite_outbound_sidecars_to_postgres", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = _load()


def _make_source_sidecar_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE contact_email_suppression (
          email TEXT PRIMARY KEY,
          suppression_reason_code TEXT NOT NULL,
          suppression_reason_text TEXT,
          suppression_source TEXT,
          last_bounced_at TEXT,
          updated_at TEXT NOT NULL,
          updated_by TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE contact_domain_suppression (
          domain_norm TEXT PRIMARY KEY,
          suppression_reason_text TEXT,
          updated_at TEXT NOT NULL,
          updated_by TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE outreach_contact_state (
          contact_email_norm TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          first_contacted_at TEXT,
          last_contacted_at TEXT,
          source TEXT,
          notes TEXT,
          updated_at TEXT NOT NULL,
          updated_by TEXT,
          lead_id INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO contact_email_suppression (
          email, suppression_reason_code, updated_at
        ) VALUES ('a@x.com', 'manual_do_not_contact', '2024-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO contact_domain_suppression (
          domain_norm, updated_at
        ) VALUES ('x.com', '2024-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO outreach_contact_state (
          contact_email_norm, state, updated_at, lead_id
        ) VALUES ('a@x.com', 'contacted', '2024-01-01T00:00:00Z', 123)
        """
    )
    conn.commit()
    conn.close()


def test_missing_postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIGENLAB_POSTGRES_URL", raising=False)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="Postgres URL"):
        m.resolve_postgres_url(None)


def test_missing_sqlite_sidecar_table_warning_and_zero(tmp_path: Path) -> None:
    db = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE just_one(id INTEGER)")
    conn.commit()
    counts, warnings, exists_map = m.collect_sqlite_source_counts(conn, m.TABLE_SPECS)
    conn.close()
    assert counts["contact_email_suppression"] == 0
    assert counts["contact_domain_suppression"] == 0
    assert counts["outreach_contact_state"] == 0
    assert exists_map["contact_email_suppression"] is False
    assert any("treated as 0 rows" in w for w in warnings)


def test_target_nonempty_without_replace_fails_decision() -> None:
    assert m.should_refuse_nonempty_targets(any_nonempty=True, replace=False, dry_run=False) is True
    assert m.should_refuse_nonempty_targets(any_nonempty=True, replace=True, dry_run=False) is False
    assert m.should_refuse_nonempty_targets(any_nonempty=True, replace=False, dry_run=True) is False


def test_timestamp_conversion() -> None:
    assert m.iso_text_to_datetime(None) is None
    dt = m.iso_text_to_datetime("2024-01-01T00:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    naive = m.iso_text_to_datetime("2024-01-01T00:00:00")
    assert naive is not None
    assert naive.tzinfo == timezone.utc


def test_json_summary_shape() -> None:
    doc = m._empty_result()
    assert set(
        [
            "ok",
            "dry_run",
            "replace",
            "transaction_committed",
            "sqlite_counts",
            "postgres_counts_before",
            "postgres_counts_after",
            "loaded",
            "attempted_loaded",
            "validation",
            "errors",
            "warnings",
            "elapsed_seconds",
        ]
    ).issubset(set(doc.keys()))


def test_progress_formatter_shape() -> None:
    line = m.format_load_progress(
        pg_table="outbound.outreach_contact_state",
        loaded_so_far=20,
        total=100,
        elapsed_s=1.5,
        batch_len=10,
    )
    assert "outbound.outreach_contact_state" in line
    assert "20/100" in line
    assert "20.0%" in line
    assert "elapsed=1.5s" in line
    assert "batch=10" in line


@pytest.mark.skipif(
    not os.environ.get("ORIGENLAB_POSTGRES_TEST_URL"),
    reason="Set ORIGENLAB_POSTGRES_TEST_URL for optional integration test.",
)
def test_dry_run_does_not_write_integration(tmp_path: Path) -> None:
    db = tmp_path / "sidecar.sqlite"
    _make_source_sidecar_db(db)
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--sqlite-db",
            str(db),
            "--postgres-url",
            os.environ["ORIGENLAB_POSTGRES_TEST_URL"],
            "--dry-run",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
