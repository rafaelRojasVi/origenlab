"""Tests for PR4 commercial procurement persistence (fixture-only --apply)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
    RUN_CONTEXT_PRODUCTION_APPLY,
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
)
from origenlab_email_pipeline.commercial_procurement.builder import (
    PersistenceValidationError,
    SchemaIncompatibilityError,
    StaleProcurementBuildPlanError,
    UnsafeInvocationError,
    run_procurement_build,
)
from origenlab_email_pipeline.commercial_procurement.schema import (
    ensure_commercial_procurement_tables,
    procurement_tables_present,
)

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "commercial"
    / "build_commercial_procurement_read_model.py"
)


def _seed_fixture(db: Path, *, tender_key: str = "T-1", org: str = "Hospital Regional Sur") -> None:
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS external_leads_raw(
          source_name TEXT, source_record_id TEXT, raw_json TEXT, source_url TEXT, fetched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS lead_master(
          id INTEGER PRIMARY KEY, source_name TEXT, source_record_id TEXT, org_name TEXT,
          org_name_norm TEXT, domain TEXT, domain_norm TEXT, email TEXT, email_norm TEXT,
          region TEXT, status TEXT, evidence_summary TEXT, first_seen_at TEXT, last_seen_at TEXT,
          lead_type TEXT
        );
        CREATE TABLE IF NOT EXISTS commercial_identity_account(
          account_id TEXT PRIMARY KEY, canonical_name TEXT, normalized_name TEXT,
          primary_domain TEXT, identity_confidence TEXT, identity_status TEXT
        );
        CREATE TABLE IF NOT EXISTS commercial_identity_account_alias(
          account_id TEXT, alias_name TEXT, normalized_alias TEXT, evidence_count INTEGER,
          PRIMARY KEY(account_id, normalized_alias)
        );
        CREATE TABLE IF NOT EXISTS commercial_identity_account_domain(
          account_id TEXT, domain_norm TEXT, is_institutional INTEGER, link_method TEXT,
          PRIMARY KEY(account_id, domain_norm)
        );
        CREATE TABLE IF NOT EXISTS commercial_identity_build_meta(
          meta_key TEXT PRIMARY KEY, meta_value TEXT
        );
        CREATE TABLE IF NOT EXISTS commercial_opportunity(
          opportunity_id TEXT PRIMARY KEY, canonical_stage TEXT
        );
        CREATE TABLE IF NOT EXISTS commercial_deal(id INTEGER PRIMARY KEY, deal_key TEXT);
        CREATE TABLE IF NOT EXISTS opportunity_signals(id INTEGER PRIMARY KEY, signal_key TEXT);
        """
    )
    raw = {
        "Codigo": "1",
        "CodigoExterno": tender_key,
        "NombreOrganismo": org,
        "CodigoEstado": "6",
        "Estado": "Cerrada",
        "FechaCierre": "2025-02-01",
        "Nombre": "kit",
    }
    conn.execute("DELETE FROM external_leads_raw")
    conn.execute("DELETE FROM lead_master")
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "1", json.dumps(raw), None, "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (1,'chilecompra','1',?,?, 'hrs.cl','hrs.cl', NULL,NULL,'RM','nuevo','1',
                  '2026-07-01','2026-07-01','tender_buyer')""",
        (org, org.lower()),
    )
    conn.execute("DELETE FROM commercial_identity_account")
    conn.execute("DELETE FROM commercial_identity_account_domain")
    conn.execute(
        "INSERT INTO commercial_identity_account VALUES "
        "('a_hospital',?,?, 'hrs.cl','high','active')",
        (org, org.lower()),
    )
    conn.execute(
        "INSERT INTO commercial_identity_account_domain VALUES "
        "('a_hospital','hrs.cl',1,'institutional_domain')"
    )
    conn.execute("DELETE FROM commercial_identity_build_meta")
    for k, v in [
        ("schema_version", "commercial_identity_v1"),
        ("identity_fingerprint", "fixture-id-fp"),
        ("identity_fingerprint_algorithm_version", "identity_fp_v2"),
        ("run_context", "local_fixture"),
    ]:
        conn.execute("INSERT INTO commercial_identity_build_meta VALUES (?,?)", (k, v))
    if conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0] == 0:
        conn.execute("INSERT INTO commercial_opportunity VALUES ('o1','fulfillment')")
    if conn.execute("SELECT COUNT(*) FROM commercial_deal").fetchone()[0] == 0:
        conn.execute("INSERT INTO commercial_deal VALUES (1,'d1')")
    if conn.execute("SELECT COUNT(*) FROM opportunity_signals").fetchone()[0] == 0:
        conn.execute("INSERT INTO opportunity_signals VALUES (1,'s1')")
    conn.commit()
    conn.close()


def _snapshot_pr4(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = [
        "commercial_procurement_signal",
        "commercial_procurement_account_resolution",
        "commercial_procurement_evidence",
        "commercial_procurement_conflict",
        "commercial_procurement_enrichment_candidate",
        "commercial_procurement_build_meta",
    ]
    out: dict[str, list[tuple]] = {}
    for t in tables:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone():
            out[t] = []
            continue
        out[t] = list(conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall())
    return out


def test_default_cli_is_dry_run(tmp_path: Path) -> None:
    db = tmp_path / "cli.sqlite"
    _seed_fixture(db)
    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--sqlite-path",
            str(db),
            "--as-of-date",
            "2026-07-30",
            "--run-context",
            RUN_CONTEXT_LOCAL_FIXTURE,
            "--json-summary",
        ],
        cwd=str(_SCRIPT.parents[2]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["mode"] == "dry-run"
    assert summary["applied"] is False
    conn = sqlite3.connect(db)
    assert not procurement_tables_present(conn)
    conn.close()


def test_first_apply_creates_schema_and_rows(tmp_path: Path) -> None:
    db = tmp_path / "first.sqlite"
    _seed_fixture(db)
    result = run_procurement_build(
        sqlite_path=db,
        as_of_date="2026-07-30",
        run_context=RUN_CONTEXT_LOCAL_FIXTURE,
        apply=True,
    )
    assert result.summary["applied"] is True
    assert result.summary["written_rows"]["commercial_procurement_signal"] == 1
    assert result.summary["identity_snapshot_unchanged"] is True
    assert result.summary["opportunity_snapshot_unchanged"] is True
    assert result.summary["procurement_sources_unchanged"] is True
    conn = sqlite3.connect(db)
    assert procurement_tables_present(conn)
    assert conn.execute("SELECT COUNT(*) FROM commercial_procurement_signal").fetchone()[0] == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM commercial_procurement_account_resolution").fetchone()[
            0
        ]
        == 1
    )
    conn.close()


def test_identical_rebuild_idempotent_semantic(tmp_path: Path) -> None:
    db = tmp_path / "idem.sqlite"
    _seed_fixture(db)
    a = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    b = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    assert a.plan.semantic_plan_digest == b.plan.semantic_plan_digest
    assert a.summary["readback_semantic_digest"] == b.summary["readback_semantic_digest"]


def test_changed_valid_plan_replaces_atomically(tmp_path: Path) -> None:
    db = tmp_path / "change.sqlite"
    _seed_fixture(db, tender_key="T-OLD")
    first = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    assert first.plan.signals[0].canonical_tender_key == "T-OLD"
    _seed_fixture(db, tender_key="T-NEW")
    second = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    assert second.plan.signals[0].canonical_tender_key == "T-NEW"
    assert second.plan.semantic_plan_digest != first.plan.semantic_plan_digest
    conn = sqlite3.connect(db)
    keys = [
        r[0]
        for r in conn.execute(
            "SELECT canonical_tender_key FROM commercial_procurement_signal"
        ).fetchall()
    ]
    assert keys == ["T-NEW"]
    conn.close()


def test_stale_expected_refuses_before_delete(tmp_path: Path) -> None:
    db = tmp_path / "stale.sqlite"
    _seed_fixture(db)
    first = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    conn = sqlite3.connect(db)
    before = _snapshot_pr4(conn)
    conn.close()
    with pytest.raises(StaleProcurementBuildPlanError):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_PRODUCTION_APPLY,
            apply=True,
            expected_source_fingerprint=first.plan.source_fingerprint,
            expected_identity_fingerprint=first.plan.identity_fingerprint,
            expected_build_plan_fingerprint="0" * 64,
            expected_semantic_plan_digest=first.plan.semantic_plan_digest,
        )
    conn = sqlite3.connect(db)
    assert _snapshot_pr4(conn) == before
    conn.close()


def test_stale_source_mutation_rolls_back(tmp_path: Path) -> None:
    db = tmp_path / "mut.sqlite"
    _seed_fixture(db)
    first = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    conn = sqlite3.connect(db)
    before = _snapshot_pr4(conn)
    conn.close()

    def mutate(c: sqlite3.Connection) -> None:
        c.execute(
            "UPDATE lead_master SET org_name=?, org_name_norm=? WHERE source_record_id='1'",
            ("Mutated Org", "mutated org"),
        )

    with pytest.raises(StaleProcurementBuildPlanError):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
            expected_source_fingerprint=first.plan.source_fingerprint,
            expected_identity_fingerprint=first.plan.identity_fingerprint,
            expected_build_plan_fingerprint=first.plan.build_plan_fingerprint,
            expected_semantic_plan_digest=first.plan.semantic_plan_digest,
            inject_source_change=mutate,
        )
    conn = sqlite3.connect(db)
    # Source mutation may persist outside rollback if inject commits? inject runs inside txn
    # so lead_master mutation should roll back with the apply txn.
    assert _snapshot_pr4(conn) == before
    org = conn.execute("SELECT org_name FROM lead_master WHERE source_record_id='1'").fetchone()[0]
    assert org == "Hospital Regional Sur"
    conn.close()


def test_failure_after_delete_restores_prior_pr4(tmp_path: Path) -> None:
    db = tmp_path / "adel.sqlite"
    _seed_fixture(db)
    first = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    conn = sqlite3.connect(db)
    before = _snapshot_pr4(conn)
    conn.close()

    def boom(_c: sqlite3.Connection) -> None:
        return None

    with pytest.raises(PersistenceValidationError, match="after-delete"):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
            inject_after_delete=boom,
        )
    conn = sqlite3.connect(db)
    assert _snapshot_pr4(conn) == before
    assert first.plan.semantic_plan_digest == conn.execute(
        "SELECT meta_value FROM commercial_procurement_build_meta "
        "WHERE meta_key='semantic_plan_digest'"
    ).fetchone()[0]
    conn.close()


def test_mid_insert_failure_rolls_back(tmp_path: Path) -> None:
    db = tmp_path / "mid.sqlite"
    _seed_fixture(db)
    first = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    conn = sqlite3.connect(db)
    before = _snapshot_pr4(conn)
    conn.close()

    with pytest.raises(PersistenceValidationError, match="mid-signal"):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
            inject_mid_signal_insert=lambda _c: None,
        )
    conn = sqlite3.connect(db)
    assert _snapshot_pr4(conn) == before
    assert first.summary["signal_count"] == 1
    conn.close()


def test_failure_before_commit_rolls_back(tmp_path: Path) -> None:
    db = tmp_path / "bc.sqlite"
    _seed_fixture(db)
    run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    conn = sqlite3.connect(db)
    before = _snapshot_pr4(conn)
    conn.close()

    def boom(_c: sqlite3.Connection) -> None:
        raise RuntimeError("injected before commit")

    with pytest.raises(RuntimeError, match="before commit"):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
            inject_before_commit=boom,
        )
    conn = sqlite3.connect(db)
    assert _snapshot_pr4(conn) == before
    conn.close()


def test_bad_readback_digest_rolls_back(tmp_path: Path) -> None:
    db = tmp_path / "bad.sqlite"
    _seed_fixture(db)
    run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    conn = sqlite3.connect(db)
    before = _snapshot_pr4(conn)
    conn.close()

    def corrupt(c: sqlite3.Connection) -> None:
        c.execute(
            "UPDATE commercial_procurement_signal SET title='corrupted-title' "
            "WHERE procurement_id IS NOT NULL"
        )

    with pytest.raises(PersistenceValidationError, match="readback semantic digest"):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
            inject_bad_readback=corrupt,
        )
    conn = sqlite3.connect(db)
    assert _snapshot_pr4(conn) == before
    conn.close()


def test_incompatible_schema_refuses(tmp_path: Path) -> None:
    db = tmp_path / "badschema.sqlite"
    _seed_fixture(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE commercial_procurement_signal (procurement_id TEXT PRIMARY KEY, junk TEXT)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(SchemaIncompatibilityError):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
        )


def test_production_apply_requires_expected(tmp_path: Path) -> None:
    db = tmp_path / "prod.sqlite"
    _seed_fixture(db)
    with pytest.raises(UnsafeInvocationError):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_PRODUCTION_APPLY,
            apply=True,
        )


def test_production_apply_cli_exit_6_missing_expected(tmp_path: Path) -> None:
    db = tmp_path / "cli6.sqlite"
    _seed_fixture(db)
    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--sqlite-path",
            str(db),
            "--as-of-date",
            "2026-07-30",
            "--run-context",
            RUN_CONTEXT_PRODUCTION_APPLY,
            "--apply",
            "--json-summary",
        ],
        cwd=str(_SCRIPT.parents[2]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 6


def test_apply_with_production_dry_run_rejected(tmp_path: Path) -> None:
    db = tmp_path / "pdry.sqlite"
    _seed_fixture(db)
    proc = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--sqlite-path",
            str(db),
            "--as-of-date",
            "2026-07-30",
            "--run-context",
            RUN_CONTEXT_PRODUCTION_DRY_RUN,
            "--apply",
        ],
        cwd=str(_SCRIPT.parents[2]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2


def test_retry_after_stale_succeeds(tmp_path: Path) -> None:
    db = tmp_path / "retry.sqlite"
    _seed_fixture(db)
    first = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )

    def mutate(c: sqlite3.Connection) -> None:
        c.execute(
            "UPDATE lead_master SET region=? WHERE source_record_id='1'",
            ("BIOBIO",),
        )

    with pytest.raises(StaleProcurementBuildPlanError):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
            inject_source_change=mutate,
        )
    # Retry without mutation succeeds (same approved plan)
    second = run_procurement_build(
        sqlite_path=db,
        as_of_date="2026-07-30",
        run_context=RUN_CONTEXT_LOCAL_FIXTURE,
        apply=True,
        expected_source_fingerprint=first.plan.source_fingerprint,
        expected_identity_fingerprint=first.plan.identity_fingerprint,
        expected_build_plan_fingerprint=first.plan.build_plan_fingerprint,
        expected_semantic_plan_digest=first.plan.semantic_plan_digest,
    )
    assert second.plan.semantic_plan_digest == first.plan.semantic_plan_digest


def test_schema_remains_after_failed_first_apply(tmp_path: Path) -> None:
    db = tmp_path / "schema_remain.sqlite"
    _seed_fixture(db)

    def boom(_c: sqlite3.Connection) -> None:
        raise RuntimeError("fail after schema")

    with pytest.raises(RuntimeError, match="fail after schema"):
        run_procurement_build(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context=RUN_CONTEXT_LOCAL_FIXTURE,
            apply=True,
            inject_after_schema=boom,
        )
    conn = sqlite3.connect(db)
    assert procurement_tables_present(conn)
    # No complete data replacement
    n = conn.execute("SELECT COUNT(*) FROM commercial_procurement_signal").fetchone()[0]
    assert n == 0
    conn.close()
    # Retry succeeds
    ok = run_procurement_build(
        sqlite_path=db, as_of_date="2026-07-30", run_context=RUN_CONTEXT_LOCAL_FIXTURE, apply=True
    )
    assert ok.summary["applied"] is True


def test_ensure_schema_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "ens.sqlite"
    _seed_fixture(db)
    conn = sqlite3.connect(db)
    ensure_commercial_procurement_tables(conn)
    ensure_commercial_procurement_tables(conn)
    conn.close()
