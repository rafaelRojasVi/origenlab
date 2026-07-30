"""Tests for PR4 commercial procurement planner hardening (dry-run / no apply)."""

from __future__ import annotations

import json
import sqlite3
import time
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_procurement.builder import (
    connect_production_readonly,
    run_procurement_dry_run,
)
from origenlab_email_pipeline.commercial_procurement.ids import (
    canonical_json,
    semantic_plan_digest,
)
from origenlab_email_pipeline.commercial_procurement.link_routes import build_account_index
from origenlab_email_pipeline.commercial_procurement.normalize import (
    buyer_fields_from_raw_and_lead,
    extract_status_fields,
    nested_get,
    pick_first,
    scalar_text,
)
from origenlab_email_pipeline.commercial_procurement.planner import plan_procurement
from origenlab_email_pipeline.commercial_procurement.validate_temp import (
    TempSchemaValidationError,
    validate_plan_in_temp_sqlite,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    RESOLUTION_LINKED,
    RESOLUTION_UNLINKED,
    ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
    ROUTE_NO_MATCH,
    TENDER_KEY_CODIGO_EXTERNO,
)


def _index():
    accounts = [
        {
            "account_id": "a_hospital",
            "canonical_name_norm": "hospital regional sur",
            "primary_domain_norm": "hrs.cl",
        },
        {
            "account_id": "a_lab",
            "canonical_name_norm": "laboratorio nacional",
            "primary_domain_norm": "labnac.cl",
        },
    ]
    aliases = [{"account_id": "a_lab", "alias_norm": "lab nacional chile"}]
    domains = [
        {"account_id": "a_hospital", "domain_norm": "hrs.cl"},
        {"account_id": "a_lab", "domain_norm": "labnac.cl"},
    ]
    return build_account_index(accounts=accounts, aliases=aliases, domains=domains)


def _line(**overrides: object) -> dict:
    row = {
        "source_system": "chilecompra",
        "lead_id": 1,
        "source_record_id": "1",
        "raw_lead_join_status": "matched",
        "has_raw_source": True,
        "has_lead_source": True,
        "raw_source_record_id": "1",
        "lead_source_record_id": "1",
        "raw_json_valid": True,
        "raw_json_malformed": False,
        "verified": True,
        "tender_key": "T-1",
        "tender_key_kind": TENDER_KEY_CODIGO_EXTERNO,
        "buyer_display": "Hospital Regional Sur",
        "buyer_name_norm": "hospital regional sur",
        "buyer_domain": "hrs.cl",
        "email_norm": None,
        "email_domain": None,
        "region": "RM",
        "title": "kit",
        "status_code": "6",
        "status_name": "Cerrada",
        "publication_date": "2025-01-01",
        "close_date": "2025-02-01",
        "publication_date_parsed": "2025-01-01",
        "close_date_parsed": "2025-02-01",
        "first_seen_at": "2025-01-01",
        "last_seen_at": "2025-01-02",
        "weak_public_unit_name": False,
    }
    row.update(overrides)
    return row


def _plan(lines: list[dict], *, as_of: date = date(2026, 7, 30), identity: str = "idfp"):
    return plan_procurement(
        source_lines=lines,
        account_index=_index(),
        identity_fingerprint=identity,
        identity_fingerprint_algorithm_version="identity_fp_v2",
        as_of_date=as_of,
        run_context="synthetic_fixture",
        known_account_ids=frozenset({"a_hospital", "a_lab"}),
    )


def test_semantic_digest_stable_without_build_stamp() -> None:
    lines = [_line()]
    p1 = _plan(lines)
    time.sleep(1.05)
    p2 = _plan(deepcopy(lines))
    assert p1.semantic_plan_digest == p2.semantic_plan_digest
    assert canonical_json(p1.semantic_table_rows()) == canonical_json(p2.semantic_table_rows())


def test_materialization_stamp_does_not_change_semantic_digest() -> None:
    lines = [_line()]
    a = plan_procurement(
        source_lines=lines,
        account_index=_index(),
        identity_fingerprint="idfp",
        identity_fingerprint_algorithm_version="identity_fp_v2",
        as_of_date=date(2026, 7, 30),
        run_context="synthetic_fixture",
        known_account_ids=frozenset({"a_hospital", "a_lab"}),
        materialization_stamp="2026-07-30T12:00:00+00:00",
    )
    b = plan_procurement(
        source_lines=lines,
        account_index=_index(),
        identity_fingerprint="idfp",
        identity_fingerprint_algorithm_version="identity_fp_v2",
        as_of_date=date(2026, 7, 30),
        run_context="synthetic_fixture",
        known_account_ids=frozenset({"a_hospital", "a_lab"}),
        materialization_stamp="2026-07-30T13:00:00+00:00",
    )
    assert a.semantic_plan_digest == b.semantic_plan_digest
    # created_at differs when stamps differ — prove via unresolved conflicts
    lines2 = [
        _line(),
        _line(
            source_record_id="9",
            verified=False,
            tender_key="9",
            tender_key_kind="unresolved_tender_key",
            has_raw_source=True,
            has_lead_source=True,
            raw_source_record_id="9",
            lead_source_record_id="9",
            buyer_domain=None,
        ),
    ]
    a2 = plan_procurement(
        source_lines=lines2,
        account_index=_index(),
        identity_fingerprint="idfp",
        identity_fingerprint_algorithm_version="identity_fp_v2",
        as_of_date=date(2026, 7, 30),
        run_context="synthetic_fixture",
        known_account_ids=frozenset({"a_hospital", "a_lab"}),
        materialization_stamp="t1",
    )
    b2 = plan_procurement(
        source_lines=lines2,
        account_index=_index(),
        identity_fingerprint="idfp",
        identity_fingerprint_algorithm_version="identity_fp_v2",
        as_of_date=date(2026, 7, 30),
        run_context="synthetic_fixture",
        known_account_ids=frozenset({"a_hospital", "a_lab"}),
        materialization_stamp="t2",
    )
    assert a2.semantic_plan_digest == b2.semantic_plan_digest
    assert {c.created_at for c in a2.conflicts} == {"t1"}
    assert {c.created_at for c in b2.conflicts} == {"t2"}


def test_semantic_row_change_changes_digest() -> None:
    assert _plan([_line(title="A")]).semantic_plan_digest != _plan(
        [_line(title="B")]
    ).semantic_plan_digest


def test_as_of_and_identity_affect_build_plan_and_semantic_when_derived_rows_change() -> None:
    lines = [
        _line(
            status_code="5",
            status_name="Publicada",
            close_date="2026-08-20",
            close_date_parsed="2026-08-20",
        )
    ]
    early = _plan(lines, as_of=date(2026, 7, 30))
    late = _plan(lines, as_of=date(2026, 9, 1))
    assert early.source_fingerprint == late.source_fingerprint
    assert early.build_plan_fingerprint != late.build_plan_fingerprint
    assert early.semantic_plan_digest != late.semantic_plan_digest

    a = _plan([_line()], identity="aaa")
    b = _plan([_line()], identity="bbb")
    assert a.source_fingerprint == b.source_fingerprint
    assert a.build_plan_fingerprint != b.build_plan_fingerprint


def test_every_source_outcome_class_in_fingerprint() -> None:
    lines = [
        _line(source_record_id="v1", raw_source_record_id="v1", lead_source_record_id="v1", tender_key="T-V"),
        _line(
            source_record_id="u1",
            raw_source_record_id="u1",
            lead_source_record_id="u1",
            verified=False,
            tender_key="9",
            tender_key_kind="unresolved_tender_key",
            buyer_domain=None,
            buyer_name_norm="x",
        ),
        _line(
            source_record_id="r1",
            lead_id=None,
            verified=False,
            tender_key="",
            tender_key_kind="missing",
            raw_lead_join_status="raw_only",
            has_raw_source=True,
            has_lead_source=False,
            raw_source_record_id="r1",
            lead_source_record_id=None,
            buyer_domain=None,
        ),
        _line(
            source_record_id="l1",
            verified=True,
            tender_key="T-L",
            raw_lead_join_status="lead_only",
            has_raw_source=False,
            has_lead_source=True,
            raw_source_record_id=None,
            lead_source_record_id="l1",
            raw_json_valid=False,
            buyer_domain=None,
            buyer_name_norm="organismo desconocido",
            buyer_display="Organismo Desconocido",
        ),
    ]
    plan = _plan(lines)
    assert plan.metrics["source_outcome_count"] == 4
    assert plan.metrics["signal_count"] == 2
    assert plan.metrics["unresolved_source_row_count"] == 2


def test_routes_and_resolution_cardinality() -> None:
    lines = [
        _line(source_record_id="1", tender_key="T-A", buyer_domain="hrs.cl"),
        _line(
            source_record_id="2",
            tender_key="T-F",
            raw_source_record_id="2",
            lead_source_record_id="2",
            buyer_domain=None,
            buyer_name_norm="organismo desconocido xyz",
            buyer_display="Organismo Desconocido XYZ",
        ),
    ]
    plan = _plan(lines)
    assert len(plan.signals) == len(plan.resolutions) == 2
    by_tender = {s.canonical_tender_key: s.procurement_id for s in plan.signals}
    res = {r.procurement_id: r for r in plan.resolutions}
    assert res[by_tender["T-A"]].resolution_status == RESOLUTION_LINKED
    assert res[by_tender["T-A"]].link_route == ROUTE_EXACT_INSTITUTIONAL_DOMAIN
    assert res[by_tender["T-F"]].resolution_status == RESOLUTION_UNLINKED
    assert res[by_tender["T-F"]].link_route == ROUTE_NO_MATCH


def test_matched_verified_evidence_planes() -> None:
    plan = _plan([_line(title="kit diagnostico", status_code="6", region="RM")])
    ev = plan.evidence
    assert any(
        e.source_table == "external_leads_raw" and e.evidence_type == "title" for e in ev
    )
    assert any(
        e.source_table == "external_leads_raw" and e.evidence_type == "status_code" for e in ev
    )
    assert any(
        e.source_table == "lead_master" and e.evidence_type == "buyer_institution" for e in ev
    )
    assert any(
        e.source_table == "lead_master" and e.evidence_type == "region" for e in ev
    )
    for e in ev:
        if e.source_table in {"external_leads_raw", "lead_master"}:
            assert (e.source_table, e.source_record_id) in plan.source_pointer_registry


def test_raw_only_verified_evidence() -> None:
    plan = _plan(
        [
            _line(
                source_record_id="r1",
                raw_lead_join_status="raw_only",
                has_raw_source=True,
                has_lead_source=False,
                raw_source_record_id="r1",
                lead_source_record_id=None,
                lead_id=None,
                tender_key="T-RAW",
                buyer_domain=None,
                buyer_name_norm="hospital regional sur",
                region=None,
            )
        ]
    )
    assert all(
        e.source_table != "lead_master" or e.subject_kind == "resolution" for e in plan.evidence
    )
    assert any(e.source_table == "external_leads_raw" for e in plan.evidence)


def test_lead_only_unresolved_evidence() -> None:
    plan = _plan(
        [
            _line(
                source_record_id="l9",
                verified=False,
                tender_key="9",
                tender_key_kind="unresolved_tender_key",
                raw_lead_join_status="lead_only",
                has_raw_source=False,
                has_lead_source=True,
                raw_source_record_id=None,
                lead_source_record_id="l9",
                buyer_domain=None,
                title=None,
                status_code=None,
            )
        ]
    )
    assert plan.metrics["signal_count"] == 0
    assert any(e.source_table == "lead_master" for e in plan.evidence)
    assert not any(e.source_table == "external_leads_raw" for e in plan.evidence)


def test_malformed_raw_evidence() -> None:
    plan = _plan(
        [
            _line(
                source_record_id="m1",
                verified=False,
                tender_key="m1",
                tender_key_kind="unresolved_tender_key",
                raw_json_malformed=True,
                raw_json_valid=False,
                title=None,
                status_code=None,
            )
        ]
    )
    assert any(e.evidence_type == "raw_json_malformed" for e in plan.evidence)


def test_shuffled_input_identical_evidence() -> None:
    lines = [
        _line(source_record_id="1", tender_key="T-1"),
        _line(
            source_record_id="2",
            tender_key="T-1",
            raw_source_record_id="2",
            lead_source_record_id="2",
            title="other",
        ),
        _line(
            source_record_id="9",
            verified=False,
            tender_key="9",
            tender_key_kind="unresolved_tender_key",
            raw_source_record_id="9",
            lead_source_record_id="9",
        ),
    ]
    p1 = _plan(lines)
    p2 = _plan(list(reversed(lines)))
    assert p1.semantic_plan_digest == p2.semantic_plan_digest
    assert canonical_json([e.to_db_row() for e in p1.evidence]) == canonical_json(
        [e.to_db_row() for e in p2.evidence]
    )


def test_temp_validation_rejects_unknown_subject_kind() -> None:
    plan = _plan([_line()])
    # Mutate a copy via table rows injection path — build invalid plan by hacking evidence
    from origenlab_email_pipeline.commercial_procurement.models import EvidenceRow

    bad = EvidenceRow(
        evidence_id="e_bad",
        subject_kind="not_a_real_kind",
        subject_id=plan.signals[0].procurement_id,
        source_system="chilecompra",
        source_table="lead_master",
        source_record_id="1",
        subject_key=None,
        evidence_type="x",
        evidence_at=None,
        reason_code="x",
        detail_json=None,
    )
    from dataclasses import replace

    evil = replace(plan, evidence=plan.evidence + (bad,))
    with pytest.raises(TempSchemaValidationError, match="unknown evidence subject_kind"):
        validate_plan_in_temp_sqlite(
            evil,
            known_account_ids=frozenset({"a_hospital", "a_lab"}),
            source_pointer_registry=plan.source_pointer_registry,
        )


def test_temp_sqlite_schema_validation_ok() -> None:
    plan = _plan(
        [
            _line(),
            _line(
                source_record_id="9",
                verified=False,
                tender_key="9",
                tender_key_kind="unresolved_tender_key",
                raw_source_record_id="9",
                lead_source_record_id="9",
            ),
        ]
    )
    counts = validate_plan_in_temp_sqlite(
        plan,
        known_account_ids=frozenset({"a_hospital", "a_lab"}),
        source_pointer_registry=plan.source_pointer_registry,
    )
    assert counts["commercial_procurement_signal"] == 1


def test_nested_chilecompra_extraction() -> None:
    raw = {
        "CodigoExterno": "NEST-1",
        "Fechas": {"FechaCierre": "2026-08-15", "FechaFinal": "2026-08-20"},
        "Comprador": {"NombreOrganismo": "Hospital Nested", "RegionUnidad": "RM"},
        "OrganismoComprador": {"NombreOrganismo": "Should Not Win"},
    }
    status = extract_status_fields(raw)
    assert status["close_date"] == "2026-08-15"
    buyer = buyer_fields_from_raw_and_lead(org_name=None, domain=None, email=None, raw=raw)
    assert buyer["buyer_display"] == "Hospital Nested"
    assert buyer["region"] == "RM"
    # Top-level wins over nested
    raw2 = dict(raw)
    raw2["FechaCierre"] = "2026-01-01"
    raw2["NombreOrganismo"] = "Top Level Org"
    assert extract_status_fields(raw2)["close_date"] == "2026-01-01"
    buyer2 = buyer_fields_from_raw_and_lead(org_name=None, domain=None, email=None, raw=raw2)
    assert buyer2["buyer_display"] == "Top Level Org"
    # Dict values must not stringify
    assert scalar_text({"NombreOrganismo": "X"}) == ""
    assert pick_first({"Comprador": {"NombreOrganismo": "X"}}, "Comprador") == ""
    assert nested_get(raw, "Fechas", "FechaCierre") == "2026-08-15"


def _fixture_db(tmp_path: Path) -> tuple[Path, str, int]:
    db = tmp_path / "proc.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE external_leads_raw(
          source_name TEXT, source_record_id TEXT, raw_json TEXT, source_url TEXT, fetched_at TEXT
        );
        CREATE TABLE lead_master(
          id INTEGER PRIMARY KEY, source_name TEXT, source_record_id TEXT, org_name TEXT, org_name_norm TEXT,
          domain TEXT, domain_norm TEXT, email TEXT, email_norm TEXT, region TEXT, status TEXT,
          evidence_summary TEXT, first_seen_at TEXT, last_seen_at TEXT, lead_type TEXT
        );
        CREATE TABLE commercial_identity_account(
          account_id TEXT PRIMARY KEY, canonical_name TEXT, normalized_name TEXT, primary_domain TEXT,
          identity_confidence TEXT, identity_status TEXT
        );
        CREATE TABLE commercial_identity_account_alias(
          account_id TEXT, alias_name TEXT, normalized_alias TEXT, evidence_count INTEGER,
          PRIMARY KEY(account_id, normalized_alias)
        );
        CREATE TABLE commercial_identity_account_domain(
          account_id TEXT, domain_norm TEXT, is_institutional INTEGER, link_method TEXT,
          PRIMARY KEY(account_id, domain_norm)
        );
        CREATE TABLE commercial_identity_build_meta(meta_key TEXT PRIMARY KEY, meta_value TEXT);
        CREATE TABLE commercial_opportunity(opportunity_id TEXT PRIMARY KEY, canonical_stage TEXT);
        """
    )
    raw = {
        "Codigo": "1",
        "CodigoExterno": "T-1",
        "NombreOrganismo": "Hospital Regional Sur",
        "CodigoEstado": "6",
        "Estado": "Cerrada",
        "FechaCierre": "2025-02-01",
        "Nombre": "kit",
    }
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "1", json.dumps(raw), None, "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (1,'chilecompra','1','Hospital Regional Sur','hospital regional sur','hrs.cl','hrs.cl',
                  NULL,NULL,'RM','nuevo','1','2026-07-01','2026-07-01','tender_buyer')"""
    )
    conn.execute(
        "INSERT INTO commercial_identity_account VALUES ('a_hospital','Hospital Regional Sur','hospital regional sur','hrs.cl','high','active')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_account_domain VALUES ('a_hospital','hrs.cl',1,'institutional_domain')"
    )
    for k, v in [
        ("schema_version", "commercial_identity_v1"),
        ("identity_fingerprint", "abc123"),
        ("identity_fingerprint_algorithm_version", "identity_fp_v2"),
        ("run_context", "local_fixture"),
    ]:
        conn.execute("INSERT INTO commercial_identity_build_meta VALUES (?,?)", (k, v))
    conn.execute("INSERT INTO commercial_opportunity VALUES ('o1','fulfillment')")
    conn.commit()
    id_fp = conn.execute(
        "SELECT meta_value FROM commercial_identity_build_meta WHERE meta_key='identity_fingerprint'"
    ).fetchone()[0]
    opp_n = conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0]
    conn.close()
    return db, id_fp, opp_n


def test_dry_run_cli_path_readonly_and_no_pr2_pr3_mutation(tmp_path: Path) -> None:
    db, id_fp, opp_n = _fixture_db(tmp_path)
    result = run_procurement_dry_run(
        sqlite_path=db,
        as_of_date="2026-07-30",
        run_context="local_fixture",
        apply=False,
    )
    assert result.summary["mode"] == "dry-run"
    assert result.summary["applied"] is False
    assert "semantic_plan_digest" in result.summary
    assert "plan_digest" not in result.summary
    assert result.summary["generated_at_utc"]
    assert result.summary["signal_count"] == 1
    assert result.plan.resolutions[0].resolution_status == RESOLUTION_LINKED

    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.startswith("commercial_procurement") for t in tables)
    assert (
        conn.execute(
            "SELECT meta_value FROM commercial_identity_build_meta WHERE meta_key='identity_fingerprint'"
        ).fetchone()[0]
        == id_fp
    )
    assert conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0] == opp_n
    conn.close()

    # --apply is implemented for fixtures (local_fixture); dry-run still creates no tables.
    applied = run_procurement_dry_run(
        sqlite_path=db,
        as_of_date="2026-07-30",
        run_context="local_fixture",
        apply=True,
    )
    assert applied.summary["applied"] is True
    assert applied.summary["signal_count"] == 1
    conn = sqlite3.connect(db)
    assert (
        conn.execute("SELECT COUNT(*) FROM commercial_procurement_signal").fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT meta_value FROM commercial_identity_build_meta WHERE meta_key='identity_fingerprint'"
        ).fetchone()[0]
        == id_fp
    )
    assert conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0] == opp_n
    conn.close()


def test_pinned_read_transaction_and_same_connection(tmp_path: Path) -> None:
    db, _, _ = _fixture_db(tmp_path)
    from origenlab_email_pipeline.commercial_procurement import builder as bmod
    from origenlab_email_pipeline.commercial_procurement.sources import (
        _REQUIRE_TXN_CONN_IDS,
    )

    seen: list[bool] = []
    orig = bmod.plan_procurement_from_connection

    def wrapped(*, conn, **kwargs):
        seen.append(conn.in_transaction)
        assert id(conn) in _REQUIRE_TXN_CONN_IDS
        return orig(conn=conn, **kwargs)

    bmod.plan_procurement_from_connection = wrapped  # type: ignore[assignment]
    try:
        run_procurement_dry_run(
            sqlite_path=db, as_of_date="2026-07-30", run_context="local_fixture"
        )
    finally:
        bmod.plan_procurement_from_connection = orig  # type: ignore[assignment]
    assert seen and all(seen)


def test_concurrent_update_cannot_mix_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "wal.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE commercial_identity_account(
          account_id TEXT PRIMARY KEY, canonical_name TEXT, normalized_name TEXT, primary_domain TEXT,
          identity_confidence TEXT, identity_status TEXT
        );
        CREATE TABLE commercial_identity_account_domain(
          account_id TEXT, domain_norm TEXT, is_institutional INTEGER, link_method TEXT,
          PRIMARY KEY(account_id, domain_norm)
        );
        CREATE TABLE commercial_identity_build_meta(meta_key TEXT PRIMARY KEY, meta_value TEXT);
        CREATE TABLE external_leads_raw(
          source_name TEXT, source_record_id TEXT, raw_json TEXT, source_url TEXT, fetched_at TEXT
        );
        CREATE TABLE lead_master(
          id INTEGER PRIMARY KEY, source_name TEXT, source_record_id TEXT, org_name TEXT, org_name_norm TEXT,
          domain TEXT, domain_norm TEXT, email TEXT, email_norm TEXT, region TEXT, status TEXT,
          evidence_summary TEXT, first_seen_at TEXT, last_seen_at TEXT, lead_type TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO commercial_identity_build_meta VALUES ('identity_fingerprint','OLD')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_build_meta VALUES "
        "('identity_fingerprint_algorithm_version','identity_fp_v2')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_account VALUES "
        "('a1','A','a','a.cl','high','active')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_account_domain VALUES ('a1','a.cl',1,'institutional_domain')"
    )
    conn.commit()
    conn.close()

    reader = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    reader.row_factory = sqlite3.Row
    reader.execute("PRAGMA query_only=ON")
    reader.execute("BEGIN DEFERRED")
    from origenlab_email_pipeline.commercial_procurement.sources import (
        enable_require_active_read_transaction,
        disable_require_active_read_transaction,
    )

    enable_require_active_read_transaction(reader)
    first = reader.execute(
        "SELECT meta_value FROM commercial_identity_build_meta WHERE meta_key='identity_fingerprint'"
    ).fetchone()[0]
    assert first == "OLD"

    writer = sqlite3.connect(db)
    writer.execute(
        "UPDATE commercial_identity_build_meta SET meta_value='NEW' WHERE meta_key='identity_fingerprint'"
    )
    writer.commit()
    writer.close()

    second = reader.execute(
        "SELECT meta_value FROM commercial_identity_build_meta WHERE meta_key='identity_fingerprint'"
    ).fetchone()[0]
    assert second == "OLD"
    reader.rollback()
    disable_require_active_read_transaction(reader)
    reader.close()


def test_production_access_remains_readonly(tmp_path: Path) -> None:
    db, _, _ = _fixture_db(tmp_path)
    conn = connect_production_readonly(db)
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE nope(x INTEGER)")
    finally:
        conn.close()


def test_semantic_digest_helper_algorithm() -> None:
    plan = _plan([_line()])
    assert semantic_plan_digest(table_rows=plan.semantic_table_rows()) == plan.semantic_plan_digest
