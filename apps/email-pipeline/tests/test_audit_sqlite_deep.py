"""Tests for read-only SQLite deep forensic audit (synthetic DBs only)."""

from __future__ import annotations

import errno
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
    USEFULNESS_ID_BATCH_SIZE,
    AuditOptions,
    assert_sql_allowed,
    build_conclusions,
    checkpoint_identity,
    configure_readonly_memory_bounds,
    connect_readonly,
    fingerprint_db_files,
    fingerprints_equal,
    is_configured_production_db,
    load_checkpoint,
    ordered_phases,
    run_audit,
    run_column_bytes,
    run_duplicate_analysis,
    run_physical_dbstat,
    run_structural_light,
    run_structural_quick,
    run_usefulness_classification,
    save_checkpoint,
    scan_for_pii_leaks,
    validate_audit_access,
    validate_resume_checkpoint,
    write_outputs,
    _body_bytes_sum_expr,
    _cohort_body_bytes,
    _source_tier_counts_batched,
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


def _build_cross_cohort_shared_message_id_db(path: Path) -> None:
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
                "<shared@x>",
                "c1",
                "a@b.co",
                "c@d.co",
                "2026-01-01T00:00:00Z",
                "body-a",
                "",
                "",
                "",
                "",
                "",
            ),
            (
                2,
                LEGACY_SRC,
                "INBOX",
                "<shared@x>",
                "l1",
                "a@b.co",
                "c@d.co",
                "2020-01-01T00:00:00Z",
                "body-b",
                "",
                "",
                "",
                "",
                "",
            ),
        ],
    )
    conn.commit()
    conn.close()


def _build_orphan_reference_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO emails (
          id, source_file, folder, message_id, subject, sender, recipients,
          date_iso, body, body_html, body_text_raw, body_text_clean,
          full_body_clean, top_reply_clean
        ) VALUES (1, ?, 'INBOX', '<x@y>', 's', 'a@b.co', 'c@d.co',
          '2026-01-01T00:00:00Z', 'b', '', '', '', '', '')
        """,
        (CANONICAL_SRC,),
    )
    conn.execute(
        """
        INSERT INTO document_master (attachment_id, email_id, filename, doc_type)
        VALUES (NULL, 99999, 'missing.pdf', 'quote')
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
def same_len_db(tmp_path: Path) -> Path:
    db = tmp_path / "same_len.sqlite"
    _build_same_length_different_content_db(db)
    return db


@pytest.fixture
def fk_db(tmp_path: Path) -> Path:
    db = tmp_path / "fk_bad.sqlite"
    _build_fk_violation_db(db)
    return db


@pytest.fixture
def cross_cohort_db(tmp_path: Path) -> Path:
    db = tmp_path / "cross_cohort.sqlite"
    _build_cross_cohort_shared_message_id_db(db)
    return db


@pytest.fixture
def orphan_ref_db(tmp_path: Path) -> Path:
    db = tmp_path / "orphan_ref.sqlite"
    _build_orphan_reference_db(db)
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
    assert canonical["sha256_mixed_fingerprint_duplicate_groups"] == 0
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
    assert mixed["sha256_mixed_fingerprint_duplicate_groups"] == 1
    assert mixed["sha256_same_length_different_content_groups"] == 1
    assert mixed["estimated_repeated_body_bytes_in_sqlite"] == 0


def test_duplicate_analysis_scopes_candidates_to_cohort(cross_cohort_db: Path) -> None:
    conn = connect_readonly(cross_cohort_db)
    try:
        report = run_duplicate_analysis(conn)
    finally:
        conn.close()
    by_cohort = {c["cohort"]: c for c in report["message_id_cohorts"]}
    assert by_cohort["canonical_gmail"]["duplicate_message_id_groups"] == 0
    assert by_cohort["legacy_labdelivery"]["duplicate_message_id_groups"] == 0
    assert by_cohort["all_emails"]["duplicate_message_id_groups"] == 1


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


def test_usefulness_reports_orphan_reference_ids_separately(orphan_ref_db: Path) -> None:
    conn = connect_readonly(orphan_ref_db)
    try:
        report = run_usefulness_classification(conn)
    finally:
        conn.close()
    assert report["referenced_email_id_count"] == 0
    assert report["orphan_reference_email_id_count"] == 1
    assert report["historical_only_email_rows"] == 1


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
    assert report["orphan_reference_email_id_count"] == 0
    assert report["invalid_date_iso_validation"] == "iso_text_to_datetime"
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
        validate_resume_checkpoint(
            checkpoint,
            mismatched,
            current_fingerprint=fingerprint_db_files(rich_db),
        )


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


def test_checkpoint_identity_is_immutable_across_phases(rich_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    quick_only = frozenset({"structural_quick", "physical_dbstat"})
    run_audit(
        AuditOptions(
            db=rich_db,
            confirm_offline_copy=True,
            output_dir=out,
            phases=quick_only,
        )
    )
    checkpoint = json.loads((out / "audit_sqlite_deep_checkpoint.json").read_text())
    identity_after_two = dict(checkpoint["identity"])
    assert identity_after_two["file_fingerprint"]["database"] is not None


def test_resume_refuses_database_change_after_partial_run(rich_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    quick_only = frozenset({"structural_quick"})
    run_audit(
        AuditOptions(
            db=rich_db,
            confirm_offline_copy=True,
            output_dir=out,
            phases=quick_only,
        )
    )
    rich_db.write_bytes(rich_db.read_bytes() + b"\0")
    with pytest.raises(RuntimeError, match="fingerprint"):
        run_audit(
            AuditOptions(
                db=rich_db,
                confirm_offline_copy=True,
                output_dir=out,
                resume=True,
                phases=quick_only,
            )
        )


def test_privacy_violation_in_phase_is_not_persisted(rich_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    leaked = {
        "phase": "structural_light",
        "exact": True,
        "note": "contact user@example.org on /var/lib/data/emails.sqlite",
        "page_size": 4096,
        "page_count": 1,
        "freelist_count": 0,
        "allocated_bytes": 4096,
        "freelist_bytes": 0,
        "journal_mode": "delete",
        "auto_vacuum": 0,
        "tables": [],
        "indexes": [],
    }
    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_structural_light",
        return_value=leaked,
    ):
        with pytest.raises(RuntimeError, match="privacy leak"):
            run_audit(
                AuditOptions(
                    db=rich_db,
                    confirm_offline_copy=True,
                    output_dir=out,
                    phases=PRODUCTION_LIGHT_PHASE_NAMES,
                )
            )
    checkpoint_path = out / "audit_sqlite_deep_checkpoint.json"
    if checkpoint_path.is_file():
        payload = checkpoint_path.read_text(encoding="utf-8")
        assert "user@example.org" not in payload
        assert "/var/lib/data" not in payload
    else:
        assert "structural_light" not in json.loads(
            (out / "audit_sqlite_deep.json").read_text()
        ) if (out / "audit_sqlite_deep.json").is_file() else True


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
    assert report["immutable_open"] is True
    assert report["sqlite_open_uri"] == "mode=ro&immutable=1"


def _as_wal_format_offline_copy_no_sidecars(path: Path) -> None:
    """Leave WAL-format header bytes while removing companions (online-backup-like)."""
    conn = sqlite3.connect(path)
    assert str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() == "wal"
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for suffix in ("-wal", "-shm", "-journal"):
        companion = Path(str(path) + suffix)
        if companion.exists():
            companion.unlink()
    with path.open("rb") as handle:
        header = handle.read(20)
    assert header[18] == 2, "expected WAL-format header write version"


def test_wal_format_offline_copy_immutable_audit_no_sidecars(
    rich_db: Path, tmp_path: Path
) -> None:
    offline = tmp_path / "wal_offline.sqlite"
    offline.write_bytes(rich_db.read_bytes())
    _as_wal_format_offline_copy_no_sidecars(offline)
    before = fingerprint_db_files(offline)
    assert before["wal"] is None and before["shm"] is None
    assert not Path(str(offline) + "-journal").exists()

    report = run_audit(
        AuditOptions(
            db=offline,
            confirm_offline_copy=True,
            output_dir=tmp_path / "wal_out",
        )
    )
    after = fingerprint_db_files(offline)
    assert fingerprints_equal(before, after)
    assert report["immutable_open"] is True
    assert report["sqlite_open_uri"] == "mode=ro&immutable=1"
    assert not Path(str(offline) + "-wal").exists()
    assert not Path(str(offline) + "-shm").exists()
    assert not Path(str(offline) + "-journal").exists()
    assert report["privacy_scan_ok"] is True


def test_nonempty_wal_refused_before_immutable_open(
    rich_db: Path, tmp_path: Path
) -> None:
    offline = tmp_path / "with_wal.sqlite"
    offline.write_bytes(rich_db.read_bytes())
    conn = sqlite3.connect(offline)
    assert str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() == "wal"
    conn.execute("UPDATE emails SET subject = subject || '-x' WHERE id = 1")
    conn.commit()
    # Keep a dirty WAL by leaving the writer process-closed without truncate.
    conn.close()
    wal = Path(str(offline) + "-wal")
    if not wal.is_file() or wal.stat().st_size == 0:
        # Some SQLite builds auto-truncate; plant a non-empty companion for the gate.
        wal.write_bytes(b"\x37\x7f\x06\x82" + b"\0" * 128)
    assert wal.is_file() and wal.stat().st_size > 0

    with pytest.raises(PermissionError, match="non-empty WAL"):
        run_audit(
            AuditOptions(
                db=offline,
                confirm_offline_copy=True,
                output_dir=tmp_path / "blocked",
            )
        )
    assert wal.exists()  # tool never deletes sidecars


def test_production_light_only_uses_ordinary_mode_ro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prod = tmp_path / "emails.sqlite"
    _build_rich_db(prod)
    settings = Settings(sqlite_path=str(prod))
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(prod))
    opened: list[bool] = []

    real_connect = connect_readonly

    def tracking_connect(db: Path, *, timeout: float = 120.0, immutable: bool = False):
        opened.append(immutable)
        return real_connect(db, timeout=timeout, immutable=immutable)

    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.connect_readonly",
        side_effect=tracking_connect,
    ):
        report = run_audit(
            AuditOptions(
                db=prod,
                confirm_offline_copy=False,
                phases=frozenset(PRODUCTION_LIGHT_PHASE_NAMES),
                settings=settings,
                output_dir=tmp_path / "light_out",
            )
        )
    assert opened == [False]
    assert report["immutable_open"] is False
    assert report["sqlite_open_uri"] == "mode=ro"
    assert report["production_path"] is True


def test_confirm_cannot_bypass_production_for_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prod = tmp_path / "emails.sqlite"
    _build_rich_db(prod)
    settings = Settings(sqlite_path=str(prod))
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(prod))
    # Heavy + confirm on production still refused (access gate before immutable).
    with pytest.raises(PermissionError, match="production"):
        run_audit(
            AuditOptions(
                db=prod,
                confirm_offline_copy=True,
                settings=settings,
                output_dir=tmp_path / "prod_heavy",
            )
        )
    # Light + confirm on production must still use ordinary mode=ro (never immutable).
    opened: list[bool] = []
    real_connect = connect_readonly

    def tracking_connect(db: Path, *, timeout: float = 120.0, immutable: bool = False):
        opened.append(immutable)
        return real_connect(db, timeout=timeout, immutable=immutable)

    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.connect_readonly",
        side_effect=tracking_connect,
    ):
        report = run_audit(
            AuditOptions(
                db=prod,
                confirm_offline_copy=True,
                phases=frozenset(PRODUCTION_LIGHT_PHASE_NAMES),
                settings=settings,
                output_dir=tmp_path / "prod_light_confirm",
            )
        )
    assert opened == [False]
    assert report["immutable_open"] is False
    assert report["sqlite_open_uri"] == "mode=ro"


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


def test_cli_validates_usefulness_batch_size(rich_db: Path, tmp_path: Path) -> None:
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(rich_db),
            "--confirm-offline-copy",
            "--output-dir",
            str(tmp_path / "out"),
            "--usefulness-batch-size",
            "0",
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 2
    assert "usefulness-batch-size" in cp.stderr


def test_v2_checkpoint_resumes_skipping_four_completed_phases(
    rich_db: Path, tmp_path: Path
) -> None:
    """Existing expensive checkpoint schema v2 must resume usefulness only."""
    out = tmp_path / "out"
    out.mkdir()
    # Seed completed heavy phases (as on the real offline checkpoint).
    prior_phases = {
        "structural_quick": {
            "phase": "structural_quick",
            "status": "completed",
            "quick_check": "ok",
            "quick_check_ok": True,
            "foreign_key_violation_count": 0,
            "elapsed_seconds": 1.0,
        },
        "physical_dbstat": {
            "phase": "physical_dbstat",
            "status": "completed",
            "elapsed_seconds": 1.0,
        },
        "column_bytes": {
            "phase": "column_bytes",
            "status": "completed",
            "elapsed_seconds": 1.0,
        },
        "duplicate_analysis": {
            "phase": "duplicate_analysis",
            "status": "completed",
            "elapsed_seconds": 1.0,
        },
    }
    options = AuditOptions(
        db=rich_db,
        confirm_offline_copy=True,
        output_dir=out,
        phases=frozenset(DEFAULT_PHASE_NAMES),
    )
    identity = checkpoint_identity(
        options, file_fingerprint=_fingerprint_dict_for_test(rich_db)
    )
    assert identity["audit_schema_version"] == AUDIT_SCHEMA_VERSION
    save_checkpoint(
        out / "audit_sqlite_deep_checkpoint.json",
        {"identity": identity, "phases": prior_phases},
    )

    ran: list[str] = []
    real_usefulness = run_usefulness_classification

    def tracking_usefulness(conn, **kwargs):
        ran.append("usefulness_classification")
        return real_usefulness(conn, **kwargs)

    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_structural_quick",
        side_effect=AssertionError("must skip structural_quick"),
    ), patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_physical_dbstat",
        side_effect=AssertionError("must skip physical_dbstat"),
    ), patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_column_bytes",
        side_effect=AssertionError("must skip column_bytes"),
    ), patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_duplicate_analysis",
        side_effect=AssertionError("must skip duplicate_analysis"),
    ), patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit.run_usefulness_classification",
        side_effect=tracking_usefulness,
    ):
        report = run_audit(
            AuditOptions(
                db=rich_db,
                confirm_offline_copy=True,
                output_dir=out,
                resume=True,
                phases=frozenset(DEFAULT_PHASE_NAMES),
            )
        )
    assert ran == ["usefulness_classification"]
    assert report["phases"]["usefulness_classification"]["status"] == "completed"
    assert report["phases"]["structural_quick"]["status"] == "completed"
    assert report["immutable_open"] is True


def test_save_checkpoint_atomic_write_failure_preserves_previous_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.json"
    save_checkpoint(path, {"schema_version": 2, "phases": {"old": {"status": "completed"}}})
    before = json.loads(path.read_text(encoding="utf-8"))

    def fail_during_write(handle, payload: str) -> None:
        handle.write(payload[:10])
        raise OSError("simulated partial write failure")

    with pytest.raises(OSError, match="simulated partial write failure"):
        save_checkpoint(
            path,
            {"schema_version": 2, "phases": {"new": {"status": "completed"}}},
            write_hook=fail_during_write,
        )

    assert json.loads(path.read_text(encoding="utf-8")) == before
    assert list(tmp_path.glob(".checkpoint.json.*.tmp")) == []


def test_save_checkpoint_atomic_replace_failure_preserves_previous_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoint.json"
    save_checkpoint(path, {"schema_version": 2, "phases": {"old": {"status": "completed"}}})
    before = json.loads(path.read_text(encoding="utf-8"))

    def fail_before_replace(tmp: Path, final: Path) -> None:
        assert tmp.is_file()
        assert final == path
        raise OSError("simulated replace failure")

    with pytest.raises(OSError, match="simulated replace failure"):
        save_checkpoint(
            path,
            {"schema_version": 2, "phases": {"new": {"status": "completed"}}},
            replace_hook=fail_before_replace,
        )

    assert json.loads(path.read_text(encoding="utf-8")) == before
    assert list(tmp_path.glob(".checkpoint.json.*.tmp")) == []


def test_save_checkpoint_directory_fsync_warning_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    warnings: list[str] = []

    def unsupported_dir_fsync(_fd: int) -> None:
        raise OSError(errno.EINVAL, "unsupported")

    info = save_checkpoint(
        path,
        {"schema_version": 2, "phases": {}},
        dir_fsync=unsupported_dir_fsync,
        warning_sink=warnings.append,
    )

    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
    assert info["directory_fsync_supported"] is False
    assert "checkpoint directory fsync unsupported" in info["directory_fsync_warning"]
    assert any("checkpoint durability warning" in warning for warning in warnings)


def _fingerprint_dict_for_test(db: Path) -> dict:
    from origenlab_email_pipeline.qa.sqlite_deep_audit import _fingerprint_dict_from_files

    return _fingerprint_dict_from_files(fingerprint_db_files(db))


def test_usefulness_is_batched_and_exact(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        full = run_usefulness_classification(conn, batch_size=10_000)
        batched = run_usefulness_classification(conn, batch_size=2)
    finally:
        conn.close()
    assert batched["memory_bounds"]["id_batch_size"] == 2
    assert batched["memory_bounds"]["batches_executed"] >= 1
    assert batched["memory_bounds"]["max_observed_batch_row_count"] <= 2
    assert batched["memory_bounds"]["body_payloads_fetched"] == 0
    assert batched["memory_bounds"]["nested_body_materializing_subquery"] is False
    assert batched["source_tier_counts"] == full["source_tier_counts"]
    assert batched["cohort_aggregate_body_bytes"] == full["cohort_aggregate_body_bytes"]
    assert batched["referenced_email_id_count"] == full["referenced_email_id_count"]
    assert batched["orphan_reference_email_id_count"] == full["orphan_reference_email_id_count"]
    assert batched["historical_only_email_rows"] == full["historical_only_email_rows"]


def test_usefulness_sql_never_selects_raw_body_columns(rich_db: Path) -> None:
    """Guard: usefulness body SQL never fetches bodies or groups body-length work."""
    seen_sql: list[str] = []
    from origenlab_email_pipeline.qa import sqlite_deep_audit as mod

    real_execute_ro = mod.execute_ro

    def tracking_execute_ro(conn, sql, params=()):
        seen_sql.append(str(sql))
        return real_execute_ro(conn, sql, params)

    conn = connect_readonly(rich_db)
    try:
        with patch(
            "origenlab_email_pipeline.qa.sqlite_deep_audit.execute_ro",
            side_effect=tracking_execute_ro,
        ):
            run_usefulness_classification(conn, batch_size=2)
    finally:
        conn.close()

    body_cols = (
        "body",
        "body_html",
        "body_text_raw",
        "body_text_clean",
        "full_body_clean",
        "top_reply_clean",
    )
    for sql in seen_sql:
        compact = " ".join(sql.lower().split())
        if "from emails" not in compact:
            continue
        if "length(cast(" in compact:
            assert " group by " not in compact
        for col in body_cols:
            # Forbid projecting raw body columns; length(cast(...)) remains allowed.
            assert f"select {col} " not in compact
            assert f"select {col}," not in compact
            assert f", {col}," not in compact
            assert f", {col} from" not in compact
            assert f"e.{col}," not in compact.replace("length(cast(e." + col, "LEN(")
            # Stronger: any bare e.col not inside length(cast(...))
            needle = f"e.{col}"
            idx = 0
            while True:
                pos = compact.find(needle, idx)
                if pos < 0:
                    break
                window_start = max(0, pos - len("length(cast("))
                window = compact[window_start : pos + len(needle)]
                assert window.startswith("length(cast(") or "length(cast(" in compact[max(0, pos - 40) : pos + 1], (
                    f"raw body column reference in SQL: {sql[:200]}"
                )
                idx = pos + len(needle)

    conn = connect_readonly(rich_db)
    try:
        result = run_usefulness_classification(conn, batch_size=2)
    finally:
        conn.close()
    evidence = result["memory_bounds"]["query_plan_evidence"]
    assert evidence["source_tier_stream_uses_group_by"] is False
    assert evidence["source_tier_stream_temp_sorter"] is False
    assert evidence["referenced_body_outer_aggregate_shape_explained"] is True
    assert evidence["referenced_body_outer_aggregate_shape_temp_sorter"] is False
    assert evidence["referenced_body_actual_partition_explained"] is True
    assert evidence["referenced_body_actual_query_group_sorter"] is False
    assert "referenced_body_actual_partition_checked" not in evidence


def test_query_plan_report_does_not_claim_actual_reference_partition_when_absent(
    rich_db: Path,
) -> None:
    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit._referenced_email_ids_sql",
        return_value=("", []),
    ):
        conn = connect_readonly(rich_db)
        try:
            result = run_usefulness_classification(conn, batch_size=2)
        finally:
            conn.close()

    evidence = result["memory_bounds"]["query_plan_evidence"]
    assert evidence["referenced_body_outer_aggregate_shape_explained"] is True
    assert evidence["referenced_body_actual_partition_explained"] is False
    assert evidence["referenced_body_actual_query_temp_btree"] is None
    assert evidence["referenced_id_union_temp_btree"] is None
    assert "referenced_body_actual_partition_checked" not in evidence


def test_usefulness_substep_resume_does_not_repeat_completed(
    rich_db: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    events: list[str] = []

    def on_substep(partial: dict) -> None:
        events.extend(partial.get("substeps_completed") or [])
        save_checkpoint(
            out / "audit_sqlite_deep_checkpoint.json",
            {
                "identity": checkpoint_identity(
                    AuditOptions(db=rich_db, confirm_offline_copy=True, output_dir=out),
                    file_fingerprint=_fingerprint_dict_for_test(rich_db),
                ),
                "usefulness_progress": partial,
                "phases": {
                    "usefulness_classification": {
                        "phase": "usefulness_classification",
                        "status": "in_progress",
                        "substeps_completed": list(partial.get("substeps_completed") or []),
                    }
                },
            },
        )
        if "source_tiers" in (partial.get("substeps_completed") or []) and "references" not in (
            partial.get("substeps_completed") or []
        ):
            raise RuntimeError("simulated interrupt after source_tiers")

    conn = connect_readonly(rich_db)
    try:
        with pytest.raises(RuntimeError, match="simulated interrupt"):
            run_usefulness_classification(
                conn, on_substep=on_substep, batch_size=2, progress={}
            )
    finally:
        conn.close()

    cp = load_checkpoint(out / "audit_sqlite_deep_checkpoint.json")
    assert cp["usefulness_progress"]["substeps_completed"] == ["source_tiers"]
    assert scan_for_pii_leaks(cp["usefulness_progress"]) == []

    tier_calls = {"n": 0}
    real_tiers = _source_tier_counts_batched

    def counting_tiers(*args, **kwargs):
        tier_calls["n"] += 1
        return real_tiers(*args, **kwargs)

    progress = cp["usefulness_progress"]
    with patch(
        "origenlab_email_pipeline.qa.sqlite_deep_audit._source_tier_counts_batched",
        side_effect=counting_tiers,
    ):
        conn = connect_readonly(rich_db)
        try:
            result = run_usefulness_classification(
                conn, progress=progress, batch_size=2
            )
        finally:
            conn.close()
    assert tier_calls["n"] == 0
    assert result["phase"] == "usefulness_classification"
    assert result["memory_bounds"]["body_payloads_fetched"] == 0
    assert "canonical_gmail" in result["source_tier_counts"]


def test_usefulness_resume_after_intermediate_id_batch_no_double_count(
    rich_db: Path,
) -> None:
    captured: list[dict] = []
    seen_ranges: list[tuple[int, int]] = []
    from origenlab_email_pipeline.qa import sqlite_deep_audit as mod

    real_execute_ro = mod.execute_ro

    def tracking_execute_ro(conn, sql, params=()):
        text = " ".join(str(sql).lower().split())
        if "select case" in text and "length(cast(" in text and "between ? and ?" in text:
            seen_ranges.append((int(params[0]), int(params[1])))
        return real_execute_ro(conn, sql, params)

    def interrupt_after_second_batch(partial: dict) -> None:
        captured.append(json.loads(json.dumps(partial)))
        batch = partial.get("batch") or {}
        if (
            partial.get("current_substep") == "source_tiers"
            and int(batch.get("completed_batches") or 0) == 2
        ):
            raise RuntimeError("simulated interrupt after two id batches")

    conn = connect_readonly(rich_db)
    try:
        with patch(
            "origenlab_email_pipeline.qa.sqlite_deep_audit.execute_ro",
            side_effect=tracking_execute_ro,
        ), pytest.raises(RuntimeError, match="simulated interrupt"):
            run_usefulness_classification(
                conn,
                batch_size=1,
                progress={},
                on_substep=interrupt_after_second_batch,
            )
    finally:
        conn.close()

    interrupted = captured[-1]
    assert interrupted["batch"]["next_id"] == 3
    first_run_ranges = list(seen_ranges)
    assert first_run_ranges[:2] == [(1, 1), (2, 2)]
    assert scan_for_pii_leaks(interrupted) == []

    resumed_ranges: list[tuple[int, int]] = []

    def tracking_resume_execute_ro(conn, sql, params=()):
        text = " ".join(str(sql).lower().split())
        if "select case" in text and "length(cast(" in text and "between ? and ?" in text:
            resumed_ranges.append((int(params[0]), int(params[1])))
        return real_execute_ro(conn, sql, params)

    conn = connect_readonly(rich_db)
    try:
        with patch(
            "origenlab_email_pipeline.qa.sqlite_deep_audit.execute_ro",
            side_effect=tracking_resume_execute_ro,
        ):
            resumed = run_usefulness_classification(
                conn, batch_size=1, progress=interrupted
            )
        clean = run_usefulness_classification(conn, batch_size=100)
    finally:
        conn.close()

    assert resumed_ranges
    assert all(start >= 3 for start, _end in resumed_ranges)
    assert resumed["source_tier_counts"] == clean["source_tier_counts"]
    assert resumed["cohort_aggregate_body_bytes"] == clean["cohort_aggregate_body_bytes"]


def test_usefulness_malformed_partial_batch_checkpoint_refused(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        with pytest.raises(RuntimeError, match="batch next_id must be an integer"):
            run_usefulness_classification(
                conn,
                batch_size=2,
                progress={
                    "id_batch_size": 2,
                    "current_substep": "source_tiers",
                    "substeps_completed": [],
                    "partial": {},
                    "batch": {
                        "substep": "source_tiers",
                        "next_id": "not-an-int",
                        "high_id": 10,
                        "batch_size": 2,
                        "completed_batches": 1,
                    },
                },
            )
        with pytest.raises(RuntimeError, match="batch size mismatch"):
            run_usefulness_classification(
                conn,
                batch_size=3,
                progress={"id_batch_size": 2, "substeps_completed": [], "partial": {}},
            )
    finally:
        conn.close()


class _FakePragmaCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakePragmaConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str):
        self.statements.append(sql)
        normalized = sql.strip().lower()
        if normalized == "pragma temp_store":
            return _FakePragmaCursor([(0,)])  # ignored/compiled default behavior
        if normalized == "pragma cache_size":
            return _FakePragmaCursor([(2000,)])  # pages, not requested KiB form
        if normalized == "pragma compile_options":
            return _FakePragmaCursor([("TEMP_STORE=3",), ("THREADSAFE=1",)])
        return _FakePragmaCursor([])


def test_connection_setting_readback_surfaces_overrides() -> None:
    fake = _FakePragmaConnection()
    settings = configure_readonly_memory_bounds(fake)  # type: ignore[arg-type]
    assert "PRAGMA temp_store=FILE" in fake.statements
    assert settings["temp_store_requested"] == "FILE"
    assert settings["temp_store_actual"] == "DEFAULT"
    assert settings["cache_size_actual_units"] == "pages"
    assert settings["compile_temp_store"] == "TEMP_STORE=3"
    assert settings["pragma_bounds_are_hard_rss_limit"] is False


def test_usefulness_output_matches_unbatched_synthetic_oracle(rich_db: Path) -> None:
    conn = connect_readonly(rich_db)
    try:
        result = run_usefulness_classification(conn, batch_size=1)
        from origenlab_email_pipeline.qa.sqlite_deep_audit import (
            _referenced_email_ids_sql,
            _valid_referenced_email_ids_sql,
        )

        canonical_where = "lower(coalesce(e.source_file, '')) LIKE 'gmail:contacto@origenlab.cl/%'"
        legacy_where = "lower(e.source_file) LIKE '%contacto@labdelivery%'"
        ref_sql, _discovered = _referenced_email_ids_sql(conn)
        valid_ref_sql = _valid_referenced_email_ids_sql(ref_sql) if ref_sql else None
        referenced = (
            _cohort_body_bytes(conn, f"e.id IN ({valid_ref_sql})")
            if valid_ref_sql
            else 0
        )
        total = _cohort_body_bytes(conn, "1=1")
        oracle = {
            "canonical_gmail": _cohort_body_bytes(conn, canonical_where),
            "legacy_labdelivery": _cohort_body_bytes(conn, legacy_where),
            "referenced_by_discovered_tables": referenced,
            "historical_unreferenced": total - referenced,
        }
    finally:
        conn.close()

    assert result["cohort_aggregate_body_bytes"] == oracle
    derivation = result["memory_bounds"]["cohort_byte_derivation"]
    assert derivation["canonical_gmail"] == "reused_source_tier_counts"
    assert derivation["historical_unreferenced"] == "total_body_bytes_minus_referenced_body_bytes"


def test_usefulness_partial_checkpoint_is_privacy_safe(rich_db: Path) -> None:
    captured: list[dict] = []
    conn = connect_readonly(rich_db)
    try:
        run_usefulness_classification(
            conn,
            batch_size=2,
            on_substep=lambda p: captured.append(dict(p)),
        )
    finally:
        conn.close()
    assert captured
    for partial in captured:
        assert scan_for_pii_leaks(partial) == []
        assert partial.get("status") == "in_progress"


def test_default_batch_size_constant() -> None:
    assert USEFULNESS_ID_BATCH_SIZE == 5_000
    assert "length(CAST(" in _body_bytes_sum_expr("e")
