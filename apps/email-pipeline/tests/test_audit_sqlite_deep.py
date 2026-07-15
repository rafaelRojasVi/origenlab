"""Tests for read-only SQLite deep forensic audit (synthetic DBs only)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.config import Settings
from origenlab_email_pipeline.db import init_schema
from origenlab_email_pipeline.qa.sqlite_deep_audit import (
    AUDIT_SCHEMA_VERSION,
    AuditOptions,
    assert_sql_allowed,
    connect_readonly,
    fingerprint_db_files,
    fingerprints_equal,
    is_configured_production_db,
    run_audit,
    run_column_bytes,
    run_duplicate_analysis,
    run_physical_dbstat,
    run_structural_quick,
    scan_for_pii_leaks,
    validate_heavy_access,
    write_outputs,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "audit_sqlite_deep.py"

CANONICAL_SRC = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"
LEGACY_SRC = "/mbox/contacto@labdelivery/inbox.mbox"


def _build_rich_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    init_schema(conn)
    conn.executemany(
        """
        INSERT INTO emails (
          id, source_file, folder, message_id, subject, sender, recipients,
          date_iso, body, body_html, body_text_raw, body_text_clean,
          full_body_clean, top_reply_clean
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                1,
                CANONICAL_SRC,
                "INBOX",
                "<dup@x>",
                "secret-subject-one",
                "sender@origenlab.cl",
                "recipients@example.com",
                "2026-01-01T00:00:00Z",
                "secret-body-alpha",
                "<p>secret-body-alpha</p>",
                "secret-body-alpha",
                "secret-body-alpha",
                "secret-body-alpha-full",
                "secret-body-alpha-top",
            ),
            (
                2,
                CANONICAL_SRC,
                "INBOX",
                "<dup@x>",
                "secret-subject-two",
                "sender@origenlab.cl",
                "recipients@example.com",
                "2026-01-02T00:00:00Z",
                "secret-body-alpha",
                "<p>secret-body-alpha</p>",
                "secret-body-alpha",
                "secret-body-alpha",
                "secret-body-alpha-full",
                "secret-body-alpha-top",
            ),
            (
                3,
                CANONICAL_SRC,
                "INBOX",
                "<unique@x>",
                "secret-subject-three",
                "sender@origenlab.cl",
                "recipients@example.com",
                "2026-02-01T00:00:00Z",
                "secret-body-beta",
                "",
                "",
                "",
                "",
                "",
            ),
            (
                4,
                LEGACY_SRC,
                "INBOX",
                "<legacy@x>",
                "legacy-subject",
                "legacy@labdelivery",
                "recipients@example.com",
                "2020-01-01T00:00:00Z",
                "legacy-body",
                "",
                "",
                "",
                "",
                "",
            ),
        ],
    )
    conn.execute(
        """
        INSERT INTO attachments (id, email_id, part_index, filename, sha256, size_bytes)
        VALUES (1, 3, 0, 'a.pdf', 'sha-aaa', 1000)
        """
    )
    conn.execute(
        """
        INSERT INTO attachments (id, email_id, part_index, filename, sha256, size_bytes)
        VALUES (2, 3, 1, 'b.pdf', 'sha-aaa', 1000)
        """
    )
    conn.execute(
        """
        INSERT INTO attachment_extracts (
          attachment_id, extract_status, extract_method, text_preview, text_truncated, char_count
        ) VALUES (1, 'success', 'pdf', 'secret-preview', 'secret-truncated', 20)
        """
    )
    conn.execute(
        """
        INSERT INTO document_master (attachment_id, email_id, filename, doc_type)
        VALUES (1, 3, 'a.pdf', 'quote')
        """
    )
    conn.commit()
    conn.close()


def _build_fk_violation_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    init_schema(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT INTO attachments (id, email_id, part_index, filename, sha256, size_bytes)
        VALUES (99, 99999, 0, 'orphan.pdf', 'sha-orphan', 500)
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def rich_db(tmp_path: Path) -> Path:
    db = tmp_path / "audit_copy.sqlite"
    _build_rich_db(db)
    return db


@pytest.fixture
def fk_db(tmp_path: Path) -> Path:
    db = tmp_path / "fk_bad.sqlite"
    _build_fk_violation_db(db)
    return db


def test_assert_sql_allowed_blocks_mutations() -> None:
    with pytest.raises(ValueError, match="blocked SQL"):
        assert_sql_allowed("VACUUM")
    with pytest.raises(ValueError, match="blocked SQL"):
        assert_sql_allowed("DELETE FROM emails")
    assert_sql_allowed("SELECT COUNT(*) FROM emails") is None


def test_structural_quick_reports_schema_and_counts(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_structural_quick(conn, rich_db)
    finally:
        conn.close()
    assert report["quick_check"] == "ok"
    assert report["foreign_key_violation_count"] == 0
    assert report["table_stats"]["emails"]["row_count"] == 4
    assert "emails" in report["tables"]


def test_structural_quick_reports_fk_violation(fk_db: Path) -> None:
    conn = connect_readonly(fk_db)
    try:
        report = run_structural_quick(conn, fk_db)
    finally:
        conn.close()
    assert report["foreign_key_violation_count"] >= 1
    assert report["structural_corruption_detected"] is True


def test_physical_dbstat_attributes_tables_and_indexes(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_physical_dbstat(conn)
    finally:
        conn.close()
    assert report["objects"]
    names = {o["name"] for o in report["objects"]}
    assert "emails" in names
    assert report["reconciliation"]["page_file_bytes"] > 0


def test_column_bytes_profiles_body_and_extract_fields(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_column_bytes(conn)
    finally:
        conn.close()
    by_col = {item["column"]: item for item in report["emails_body_columns"]}
    assert by_col["body"]["aggregate_bytes"] > 0
    assert by_col["full_body_clean"]["aggregate_bytes"] > 0
    extract = {item["column"]: item for item in report["attachment_extract_text_columns"]}
    assert extract["text_preview"]["aggregate_bytes"] > 0
    assert "secret-body-alpha" not in json.dumps(report)
    assert "secret-preview" not in json.dumps(report)


def test_duplicate_analysis_counts_message_id_and_sha_groups(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_duplicate_analysis(conn)
    finally:
        conn.close()
    canonical = next(
        c for c in report["message_id_cohorts"] if c["cohort"] == "canonical_gmail"
    )
    assert canonical["duplicate_message_id_groups"] == 1
    assert canonical["duplicate_extra_rows"] == 1
    assert canonical["exact_duplicate_body_groups"] == 1
    assert canonical["estimated_repeated_body_bytes"] > 0
    att = report["attachment_sha256"]
    assert att["duplicate_sha256_groups"] == 1
    assert att["duplicate_extra_rows"] == 1


def test_run_audit_never_mutates_db(rich_db: Path, tmp_path: Path) -> None:
    before = fingerprint_db_files(rich_db)
    options = AuditOptions(
        db=rich_db,
        confirm_offline_copy=True,
        output_dir=tmp_path / "out",
    )
    report = run_audit(options)
    after = fingerprint_db_files(rich_db)
    assert fingerprints_equal(before, after)
    assert report["mutation"] is False
    assert report["privacy_scan_ok"] is True
    assert report["schema_version"] == AUDIT_SCHEMA_VERSION
    assert "structural_quick" in report["phases"]
    assert "physical_dbstat" in report["phases"]


def test_privacy_scan_rejects_leaked_content() -> None:
    violations = scan_for_pii_leaks({"note": "path /home/rafael/data/emails.sqlite"})
    assert violations
    violations2 = scan_for_pii_leaks({"rows": [{"subject": "hello"}]})
    assert violations2


def test_production_path_refusal_for_heavy_phases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prod = tmp_path / "emails.sqlite"
    prod.touch()
    settings = Settings(sqlite_path=prod)
    monkeypatch.setattr(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.load_settings",
        lambda: settings,
    )
    assert is_configured_production_db(prod, settings) is True
    options = AuditOptions(db=prod, confirm_offline_copy=True, settings=settings)
    err = validate_heavy_access(options)
    assert err is not None
    assert "production" in err.lower()


def test_heavy_phases_require_confirm_offline_copy(rich_db: Path) -> None:
    options = AuditOptions(db=rich_db, confirm_offline_copy=False)
    err = validate_heavy_access(options)
    assert err is not None
    assert "confirm-offline-copy" in err


def test_resume_checkpoint_skips_completed_phase(rich_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    options = AuditOptions(db=rich_db, confirm_offline_copy=True, output_dir=out)
    first = run_audit(options)
    checkpoint = json.loads((out / "audit_sqlite_deep_checkpoint.json").read_text())
    assert checkpoint["phases"]["structural_quick"]["status"] == "completed"

    options_resume = AuditOptions(
        db=rich_db,
        confirm_offline_copy=True,
        output_dir=out,
        resume=True,
        phases=frozenset({"structural_quick"}),
    )
    second = run_audit(options_resume)
    assert second["phases"]["structural_quick"]["status"] == "completed"
    assert first["phases"]["structural_quick"]["elapsed_seconds"] == (
        second["phases"]["structural_quick"]["elapsed_seconds"]
    )


def test_write_outputs_json_and_markdown(rich_db: Path, tmp_path: Path) -> None:
    report = run_audit(
        AuditOptions(db=rich_db, confirm_offline_copy=True, output_dir=tmp_path / "out")
    )
    json_path, md_path = write_outputs(report, tmp_path / "out")
    assert json_path.is_file()
    assert md_path.is_file()
    md_text = md_path.read_text(encoding="utf-8")
    assert "secret-body" not in md_text
    assert "Structural corruption" in md_text
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["conclusions"]["duplication_present"] is True


def test_cli_refuses_production_without_offline_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prod = tmp_path / "emails.sqlite"
    _build_rich_db(prod)
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(prod))
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(prod), "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 2
    assert "confirm-offline-copy" in cp.stderr.lower() or "production" in cp.stderr.lower()


def test_cli_light_only_runs_without_confirm_on_copy(rich_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli_out"
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(rich_db),
            "--light-only",
            "--output-dir",
            str(out),
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert "structural_quick" in payload["phases"]
    assert "physical_dbstat" not in payload["phases"]
