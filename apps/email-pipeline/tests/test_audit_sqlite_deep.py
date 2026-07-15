"""Tests for read-only SQLite deep forensic audit (synthetic DBs only)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from origenlab_email_pipeline.config import Settings
from origenlab_email_pipeline.db import init_schema
from origenlab_email_pipeline.qa.sqlite_deep_audit import (
    AUDIT_SCHEMA_VERSION,
    DEFAULT_PHASE_NAMES,
    PRODUCTION_LIGHT_PHASE_NAMES,
    AuditOptions,
    assert_sql_allowed,
    build_conclusions,
    connect_readonly,
    fingerprint_db_files,
    fingerprints_equal,
    is_configured_production_db,
    ordered_phases,
    run_audit,
    run_column_bytes,
    run_duplicate_analysis,
    run_physical_dbstat,
    run_structural_light,
    run_structural_quick,
    run_usefulness_classification,
    scan_for_pii_leaks,
    validate_audit_access,
    validate_resume_checkpoint,
    write_outputs,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "audit_sqlite_deep.py"

CANONICAL_SRC = "gmail:contacto@origenlab.cl/[Gmail]/Enviados"
LEGACY_SRC = "/mbox/contacto@labdelivery/inbox.mbox"


def _build_rich_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    init_schema(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_emails_message_id_named ON emails(message_id)"
    )
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


def _build_same_length_different_content_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    init_schema(conn)
    # Same message_id, same per-column byte lengths, different fingerprints.
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
                "<same-len@x>",
                "s1",
                "a@b.co",
                "c@d.co",
                "2026-01-01T00:00:00Z",
                "abcd",
                "wxyz",
                "",
                "",
                "",
                "",
            ),
            (
                2,
                CANONICAL_SRC,
                "INBOX",
                "<same-len@x>",
                "s2",
                "a@b.co",
                "c@d.co",
                "2026-01-02T00:00:00Z",
                "wxyz",
                "abcd",
                "",
                "",
                "",
                "",
            ),
        ],
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
def same_len_db(tmp_path: Path) -> Path:
    db = tmp_path / "same_len.sqlite"
    _build_same_length_different_content_db(db)
    return db


@pytest.fixture
def fk_db(tmp_path: Path) -> Path:
    db = tmp_path / "fk_bad.sqlite"
    _build_fk_violation_db(db)
    return db


def test_default_phases_exclude_structural_full() -> None:
    assert "structural_full" not in DEFAULT_PHASE_NAMES
    assert ordered_phases(DEFAULT_PHASE_NAMES, full_integrity_check=False) == [
        "structural_quick",
        "physical_dbstat",
        "column_bytes",
        "duplicate_analysis",
        "usefulness_classification",
    ]


def test_default_audit_never_runs_integrity_check(rich_db: Path, tmp_path: Path) -> None:
    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_structural_full",
        side_effect=AssertionError("integrity_check must not run by default"),
    ):
        report = run_audit(
            AuditOptions(
                db=rich_db,
                confirm_offline_copy=True,
                output_dir=tmp_path / "out",
            )
        )
    assert "structural_full" not in report["phases"]
    assert report["phase_order"] == ordered_phases(DEFAULT_PHASE_NAMES, full_integrity_check=False)


def test_full_integrity_check_opt_in_only(rich_db: Path, tmp_path: Path) -> None:
    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_structural_full",
        return_value={
            "phase": "structural_full",
            "exact": True,
            "integrity_check_ok": True,
            "integrity_message_count": 1,
            "integrity_messages_sample": ["ok"],
            "integrity_messages_truncated": False,
        },
    ) as mocked:
        report = run_audit(
            AuditOptions(
                db=rich_db,
                confirm_offline_copy=True,
                full_integrity_check=True,
                output_dir=tmp_path / "out",
            )
        )
    mocked.assert_called_once()
    assert "structural_full" in report["phases"]


def test_production_light_mode_is_constant_time_only(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_structural_light(conn, rich_db)
    finally:
        conn.close()
    assert report["phase"] == "structural_light"
    assert "quick_check" not in report
    assert "foreign_key_violation_count" not in report
    assert "table_stats" not in report
    assert report["page_size"] > 0
    assert "emails" in report["tables"]


def test_heavy_phases_require_confirm_offline_copy(rich_db: Path) -> None:
    options = AuditOptions(db=rich_db, confirm_offline_copy=False)
    err = validate_audit_access(options)
    assert err is not None
    assert "confirm-offline-copy" in err


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
    err = validate_audit_access(options)
    assert err is not None
    assert "production" in err.lower()


def test_production_light_allowed_without_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prod = tmp_path / "emails.sqlite"
    _build_rich_db(prod)
    settings = Settings(sqlite_path=prod)
    monkeypatch.setattr(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.load_settings",
        lambda: settings,
    )
    options = AuditOptions(
        db=prod,
        phases=PRODUCTION_LIGHT_PHASE_NAMES,
        confirm_offline_copy=False,
        settings=settings,
    )
    assert validate_audit_access(options) is None


def test_production_alias_detected_via_samefile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prod = tmp_path / "emails.sqlite"
    _build_rich_db(prod)
    alias = tmp_path / "alias.sqlite"
    os.link(prod, alias)
    settings = Settings(sqlite_path=prod)
    monkeypatch.setattr(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.load_settings",
        lambda: settings,
    )
    assert is_configured_production_db(alias.resolve(), settings) is True
    options = AuditOptions(db=alias, confirm_offline_copy=True, settings=settings)
    err = validate_audit_access(options)
    assert err is not None


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


def test_structural_quick_reports_fk_violations_not_integrity(fk_db: Path) -> None:
    conn = connect_readonly(fk_db)
    try:
        report = run_structural_quick(conn, fk_db)
    finally:
        conn.close()
    assert report["foreign_key_violation_count"] >= 1
    assert report["quick_check"] == "ok"
    conclusions = build_conclusions({"phases": {"structural_quick": {**report, "status": "completed"}}})
    assert conclusions["foreign_key_violations"] == "yes"
    assert conclusions["sqlite_integrity_failure"] == "not_assessed"


def test_physical_dbstat_distinguishes_table_index_and_named_index(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_physical_dbstat(conn)
    finally:
        conn.close()
    by_name = {o["name"]: o for o in report["objects"]}
    assert by_name["emails"]["kind"] == "table"
    assert by_name["idx_emails_message_id_named"]["kind"] == "index"
    assert by_name["idx_emails_message_id_named"]["master_type"] == "index"
    autoindexes = [o for o in report["objects"] if o["kind"] == "autoindex"]
    assert autoindexes
    assert report["reconciliation"]["page_file_bytes"] > 0
    assert report["indexes_top"]


def test_column_bytes_profiles_body_and_within_row_redundancy(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_column_bytes(conn)
    finally:
        conn.close()
    by_col = {item["column"]: item for item in report["emails_body_columns"]}
    assert by_col["body"]["aggregate_bytes"] > 0
    assert report["within_row_body_redundancy"]["aggregate_redundant_bytes"] > 0
    assert "secret-body-alpha" not in json.dumps(report)


def test_duplicate_analysis_sha256_exact_vs_same_length_different_content(
    rich_db: Path, same_len_db: Path
) -> None:
    conn = connect_readonly(rich_db)
    try:
        exact_report = run_duplicate_analysis(conn)
    finally:
        conn.close()
    canonical = next(
        c for c in exact_report["message_id_cohorts"] if c["cohort"] == "canonical_gmail"
    )
    assert canonical["duplicate_message_id_groups"] == 1
    assert canonical["sha256_exact_duplicate_groups"] == 1
    assert canonical["sha256_same_length_different_content_groups"] == 0
    assert canonical["estimated_repeated_body_bytes_in_sqlite"] > 0

    conn2 = connect_readonly(same_len_db)
    try:
        mixed_report = run_duplicate_analysis(conn2)
    finally:
        conn2.close()
    mixed = next(
        c for c in mixed_report["message_id_cohorts"] if c["cohort"] == "canonical_gmail"
    )
    assert mixed["duplicate_message_id_groups"] == 1
    assert mixed["sha256_exact_duplicate_groups"] == 0
    assert mixed["sha256_same_length_different_content_groups"] == 1


def test_attachment_duplication_is_external_payload_only(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_duplicate_analysis(conn)
    finally:
        conn.close()
    att = report["attachment_sha256"]
    assert att["duplicate_sha256_groups"] == 1
    assert att["duplicate_external_payload_bytes"] == 1000
    assert "external" in att["interpretation"].lower()
    conclusions = build_conclusions(
        {
            "phases": {
                "duplicate_analysis": {**report, "status": "completed"},
            }
        }
    )
    savings_text = " ".join(conclusions.get("possible_space_savings_estimates") or [])
    assert "duplicate_external_payload_bytes" not in savings_text
    assert "1000" not in savings_text


def test_usefulness_reports_body_bytes_and_discovered_refs(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        report = run_usefulness_classification(conn)
    finally:
        conn.close()
    assert report["cohort_aggregate_body_bytes"]["canonical_gmail"] > 0
    assert report["cohort_aggregate_body_bytes"]["legacy_labdelivery"] > 0
    discovered = {(d["table"], d["column"]) for d in report["discovered_reference_sources"]}
    assert ("attachments", "email_id") in discovered
    assert ("document_master", "email_id") in discovered
    assert report["attachment_linked_email_id_count"] >= 1
    assert report["operational_reference_email_id_count"] >= 1
    assert "automatically deletable" in report["deletion_review_candidates"]["policy"]


def test_conclusions_tri_state_without_full_integrity(rich_db: Path, tmp_path: Path) -> None:
    report = run_audit(
        AuditOptions(db=rich_db, confirm_offline_copy=True, output_dir=tmp_path / "out")
    )
    conclusions = report["conclusions"]
    assert conclusions["sqlite_integrity_failure"] == "not_assessed"
    assert conclusions["foreign_key_violations"] == "no"
    assert conclusions["quick_check_failure"] == "no"
    assert conclusions["duplication_present"] == "yes"
    assert conclusions["phase_status"]["structural_quick"] == "completed"
    assert conclusions["phase_status"]["structural_full"] == "not_run"


def test_resume_refuses_identity_mismatch(rich_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    run_audit(AuditOptions(db=rich_db, confirm_offline_copy=True, output_dir=out))
    checkpoint = json.loads((out / "audit_sqlite_deep_checkpoint.json").read_text())
    assert checkpoint["identity"]["audit_schema_version"] == AUDIT_SCHEMA_VERSION

    mismatched = AuditOptions(
        db=rich_db,
        confirm_offline_copy=True,
        output_dir=out,
        resume=True,
        full_integrity_check=True,
    )
    with pytest.raises(RuntimeError, match="identity mismatch"):
        validate_resume_checkpoint(checkpoint, mismatched)


def test_resume_skips_completed_phase(rich_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    quick_only = frozenset({"structural_quick"})
    options = AuditOptions(
        db=rich_db,
        confirm_offline_copy=True,
        output_dir=out,
        phases=quick_only,
    )
    first = run_audit(options)
    checkpoint = json.loads((out / "audit_sqlite_deep_checkpoint.json").read_text())
    assert checkpoint["phases"]["structural_quick"]["status"] == "completed"
    assert "identity" in checkpoint

    options_resume = AuditOptions(
        db=rich_db,
        confirm_offline_copy=True,
        output_dir=out,
        resume=True,
        phases=quick_only,
    )
    second = run_audit(options_resume)
    assert second["phases"]["structural_quick"]["status"] == "completed"
    assert first["phases"]["structural_quick"]["elapsed_seconds"] == (
        second["phases"]["structural_quick"]["elapsed_seconds"]
    )


def test_run_audit_never_mutates_db(rich_db: Path, tmp_path: Path) -> None:
    before = fingerprint_db_files(rich_db)
    report = run_audit(
        AuditOptions(db=rich_db, confirm_offline_copy=True, output_dir=tmp_path / "out")
    )
    after = fingerprint_db_files(rich_db)
    assert fingerprints_equal(before, after)
    assert report["mutation"] is False
    assert report["privacy_scan_ok"] is True
    assert report["schema_version"] == AUDIT_SCHEMA_VERSION


def test_privacy_scan_detects_generic_email_and_paths() -> None:
    assert scan_for_pii_leaks({"note": "contact user@example.org please"})
    assert scan_for_pii_leaks({"note": "path /var/lib/data/emails.sqlite"})
    assert scan_for_pii_leaks({"rows": [{"subject": "hello"}]})


def test_write_outputs_json_and_markdown(rich_db: Path, tmp_path: Path) -> None:
    report = run_audit(
        AuditOptions(db=rich_db, confirm_offline_copy=True, output_dir=tmp_path / "out")
    )
    json_path, md_path = write_outputs(report, tmp_path / "out")
    assert json_path.is_file()
    assert md_path.is_file()
    md_text = md_path.read_text(encoding="utf-8")
    assert "secret-body" not in md_text
    assert "SQLite integrity failure" in md_text
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["conclusions"]["duplication_present"] == "yes"


def test_cli_refuses_production_heavy_without_offline_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_cli_light_only_runs_structural_light_only(rich_db: Path, tmp_path: Path) -> None:
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
    assert "structural_light" in payload["phases"]
    assert "structural_quick" not in payload["phases"]
    assert "physical_dbstat" not in payload["phases"]
    assert payload["conclusions"]["sqlite_integrity_failure"] == "not_assessed"
