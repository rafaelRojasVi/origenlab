"""Tests for the ARCH-2A-P1 operator CLI surface: build-commercial-identity,
build-commercial-opportunity, and refresh-commercial-opportunity-models.

These tests never touch the real production SQLite database. Every fixture DB
lives under pytest's tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_PRODUCTION_APPLY,
    RUN_CONTEXT_SYNTHETIC_FIXTURE,
)
from origenlab_email_pipeline.operator_cli.commercial_opportunity_refresh_command import (
    CommercialIdentityBuildOptions,
    CommercialOpportunityBuildOptions,
    RefreshCommercialOpportunityModelsOptions,
    commercial_identity_opportunity_run_log_path,
    parse_build_commercial_identity_args,
    parse_build_commercial_opportunity_args,
    parse_refresh_commercial_opportunity_models_args,
    run_build_commercial_identity,
    run_build_commercial_opportunity,
    run_refresh_commercial_opportunity_models,
)


def _seed_minimal_db(path: Path) -> None:
    """Minimal fixture matching production contact_master / commercial_deal* DDL
    closely enough for PR2 identity resolution and PR3 opportunity resolution to
    run end-to-end. Deliberately self-contained (no cross-test-file import)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          date_iso TEXT,
          sender TEXT,
          source_file TEXT
        );
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE commercial_deal (
          id INTEGER PRIMARY KEY,
          deal_key TEXT NOT NULL UNIQUE,
          deal_status TEXT NOT NULL,
          client_org_name TEXT NOT NULL,
          client_domain TEXT,
          client_contact_email TEXT,
          supplier_org_name TEXT,
          supplier_domain TEXT,
          confidence TEXT NOT NULL DEFAULT 'extracted_high',
          created_at TEXT,
          updated_at TEXT
        );
        CREATE TABLE commercial_deal_event (
          id INTEGER PRIMARY KEY,
          deal_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          event_at TEXT,
          confidence TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          source_email_id INTEGER,
          source_attachment_id INTEGER,
          created_at TEXT
        );
        CREATE TABLE commercial_deal_document (
          id INTEGER PRIMARY KEY,
          deal_id INTEGER NOT NULL,
          document_type TEXT NOT NULL,
          issued_at TEXT,
          confidence TEXT NOT NULL,
          source_email_id INTEGER,
          source_attachment_id INTEGER,
          created_at TEXT
        );
        CREATE TABLE commercial_deal_payment (
          id INTEGER PRIMARY KEY,
          deal_id INTEGER NOT NULL,
          direction TEXT NOT NULL,
          paid_at TEXT,
          confidence TEXT NOT NULL,
          created_at TEXT
        );
        INSERT INTO organization_master (
          domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, total_contacts,
          quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count,
          top_equipment_tags, key_contacts
        ) VALUES (
          'hospital.cl', 'Hospital Sur', 'institution',
          '2023-01-01', '2024-01-01', 10, 1,
          0, 0, 0, 0, 0, 0, NULL, NULL
        );
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count,
          top_equipment_tags, confidence_score
        ) VALUES (
          'buyer@hospital.cl', 'Buyer', 'hospital.cl', 'Hospital Sur', 'institution',
          '2023-02-01', '2024-02-01', 3, 1, 2,
          0, 0, 0, 0, 0, 0, NULL, 0.5
        );
        INSERT INTO commercial_deal (
          id, deal_key, deal_status, client_org_name, client_domain,
          client_contact_email, confidence, created_at, updated_at
        ) VALUES (
          1, 'fixture-deal', 'quoted', 'Hospital Sur', 'hospital.cl',
          'buyer@hospital.cl', 'extracted_high',
          '2026-01-01T00:00:00+00:00', '2026-01-10T00:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()


def _add_extra_contact(path: Path) -> None:
    """Mutate source data after a PR2 build so a fresh identity fingerprint diverges."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count,
          top_equipment_tags, confidence_score
        ) VALUES (
          'second@hospital.cl', 'Second Buyer', 'hospital.cl', 'Hospital Sur', 'institution',
          '2023-02-01', '2024-02-01', 1, 1, 0,
          0, 0, 0, 0, 0, 0, NULL, 0.5
        )
        """
    )
    conn.commit()
    conn.close()


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- 1/2: CLI routes to the existing builder correctly ---


def test_build_commercial_identity_routes_to_run_identity_build(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    log = tmp_path / "runs.jsonl"
    rc = run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=db,
            apply=False,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    assert rc == 0
    records = _read_jsonl(log)
    assert len(records) == 1
    assert records[0]["model"] == "commercial_identity"
    assert records[0]["applied"] is False
    assert records[0]["status"] == "success"
    assert records[0]["schema_version"] == "commercial_identity_v1"


def test_build_commercial_opportunity_routes_to_run_opportunity_build(
    tmp_path: Path,
) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    log = tmp_path / "runs.jsonl"
    rc = run_build_commercial_opportunity(
        CommercialOpportunityBuildOptions(
            sqlite_path=db,
            apply=False,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    assert rc == 0
    records = _read_jsonl(log)
    assert len(records) == 1
    assert records[0]["model"] == "commercial_opportunity"
    assert records[0]["applied"] is False
    assert records[0]["status"] == "success"
    assert records[0]["schema_version"] == "commercial_opportunity_v1"
    assert records[0]["build_contract"] == "opportunity_stage_read_model_v2"


# --- 3: no implicit production SQLite path exists ---


@pytest.mark.parametrize(
    "parse_fn",
    [
        parse_build_commercial_identity_args,
        parse_build_commercial_opportunity_args,
    ],
)
def test_sqlite_path_is_required_no_fallback(parse_fn) -> None:
    with pytest.raises(SystemExit):
        parse_fn([])  # no --sqlite-path at all


def test_refresh_sequence_sqlite_path_is_required() -> None:
    with pytest.raises(SystemExit):
        parse_refresh_commercial_opportunity_models_args(
            ["--apply", "--confirm-sequenced-apply"]
        )


# --- 4: default behavior is dry-run/non-apply ---


@pytest.mark.parametrize(
    "parse_fn",
    [
        parse_build_commercial_identity_args,
        parse_build_commercial_opportunity_args,
    ],
)
def test_default_mode_is_dry_run(tmp_path: Path, parse_fn) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    opts = parse_fn(["--sqlite-path", str(db)])
    assert opts.apply is False


def test_dry_run_performs_no_mutation(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    before = db.read_bytes()
    log = tmp_path / "runs.jsonl"
    run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=db,
            apply=False,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    after = db.read_bytes()
    assert before == after


# --- 5: explicit production_apply + apply passed through correctly ---


def test_explicit_apply_and_production_apply_context_pass_through(
    tmp_path: Path,
) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    opts = parse_build_commercial_identity_args(
        [
            "--sqlite-path",
            str(db),
            "--apply",
            "--run-context",
            RUN_CONTEXT_PRODUCTION_APPLY,
        ]
    )
    assert opts.apply is True
    assert opts.run_context == RUN_CONTEXT_PRODUCTION_APPLY
    log = tmp_path / "runs.jsonl"
    rc = run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=db,
            apply=True,
            run_context=RUN_CONTEXT_PRODUCTION_APPLY,
            log_path=log,
        )
    )
    assert rc == 0
    records = _read_jsonl(log)
    assert records[0]["applied"] is True
    assert records[0]["run_context"] == RUN_CONTEXT_PRODUCTION_APPLY


# --- 6: invalid run context fails ---


@pytest.mark.parametrize(
    "parse_fn",
    [
        parse_build_commercial_identity_args,
        parse_build_commercial_opportunity_args,
    ],
)
def test_invalid_run_context_fails(tmp_path: Path, parse_fn) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    with pytest.raises(SystemExit):
        parse_fn(["--sqlite-path", str(db), "--run-context", "not_a_real_context"])


# --- 7: PR3 fails closed on stale/mismatched PR2 identity ---


def test_pr3_fails_closed_on_mismatched_identity_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    log = tmp_path / "runs.jsonl"

    identity_rc = run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=db,
            apply=True,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    assert identity_rc == 0

    # Source data changes after PR2's build -> PR2's persisted fingerprint is now stale.
    _add_extra_contact(db)

    opportunity_rc = run_build_commercial_opportunity(
        CommercialOpportunityBuildOptions(
            sqlite_path=db,
            apply=True,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    assert opportunity_rc == 3  # IdentitySnapshotError -> gate_rejected

    records = _read_jsonl(log)
    opportunity_records = [r for r in records if r["model"] == "commercial_opportunity"]
    assert len(opportunity_records) == 1
    assert opportunity_records[0]["status"] == "gate_rejected"
    assert opportunity_records[0]["applied"] is False


# --- 8: combined sequencing ---


def test_refresh_sequence_pr2_success_then_pr3_runs(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    log = tmp_path / "runs.jsonl"
    rc = run_refresh_commercial_opportunity_models(
        RefreshCommercialOpportunityModelsOptions(
            sqlite_path=db, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE, log_path=log
        )
    )
    assert rc == 0
    records = _read_jsonl(log)
    assert [r["model"] for r in records] == [
        "commercial_identity",
        "commercial_opportunity",
    ]
    assert all(r["applied"] is True for r in records)
    assert all(r["status"] == "success" for r in records)


def test_refresh_sequence_pr2_failure_stops_before_pr3(tmp_path: Path) -> None:
    db = (
        tmp_path / "db.sqlite"
    )  # never seeded -> require_explicit_sqlite_path raises (not a file)
    log = tmp_path / "runs.jsonl"
    rc = run_refresh_commercial_opportunity_models(
        RefreshCommercialOpportunityModelsOptions(
            sqlite_path=db, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE, log_path=log
        )
    )
    assert rc != 0
    records = _read_jsonl(log)
    # Only PR2's failed attempt is recorded; PR3 must never have run.
    assert [r["model"] for r in records] == ["commercial_identity"]
    assert records[0]["status"] == "path_error"


def test_refresh_sequence_requires_apply_and_confirm_flags() -> None:
    with pytest.raises(SystemExit):
        parse_refresh_commercial_opportunity_models_args(
            ["--sqlite-path", "/tmp/x.sqlite"]
        )
    with pytest.raises(SystemExit):
        parse_refresh_commercial_opportunity_models_args(
            ["--sqlite-path", "/tmp/x.sqlite", "--apply"]
        )  # missing --confirm-sequenced-apply


# --- 9: durable JSONL output ---


def test_durable_log_is_append_only_valid_jsonl_no_sensitive_content(
    tmp_path: Path,
) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    log = commercial_identity_opportunity_run_log_path(reports_dir=tmp_path)

    run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=db,
            apply=False,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    first_bytes = log.read_bytes()
    run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=db,
            apply=False,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    second_bytes = log.read_bytes()

    # Append semantics: prior bytes are an unmodified prefix of the file after a second run.
    assert second_bytes.startswith(first_bytes)

    records = _read_jsonl(log)
    assert len(records) == 2
    for record in records:
        # No full machine-specific path leaked — only the basename + classification.
        assert "sqlite_path_basename" in record
        serialized = json.dumps(record)
        assert str(db.parent) not in serialized
        # No raw email address or document body content — only structural/count fields.
        assert "@" not in serialized
        assert "body" not in serialized.lower()


def test_durable_log_records_git_sha_and_path_classification(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    _seed_minimal_db(db)
    log = tmp_path / "runs.jsonl"
    run_build_commercial_identity(
        CommercialIdentityBuildOptions(
            sqlite_path=db,
            apply=False,
            run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE,
            log_path=log,
        )
    )
    record = _read_jsonl(log)[0]
    assert record["git_sha"]
    assert record["sqlite_path_classification"] == "non_production"
    assert record["sqlite_path_basename"] == "db.sqlite"
