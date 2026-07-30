"""Tests for PR4 commercial procurement planner (dry-run / no production apply)."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_procurement.builder import (
    ApplyNotImplementedError,
    connect_production_readonly,
    run_procurement_dry_run,
)
from origenlab_email_pipeline.commercial_procurement.ids import (
    canonical_json,
    plan_digest,
    stable_procurement_id,
)
from origenlab_email_pipeline.commercial_procurement.link_routes import build_account_index
from origenlab_email_pipeline.commercial_procurement.planner import plan_procurement
from origenlab_email_pipeline.commercial_procurement.validate_temp import (
    validate_plan_in_temp_sqlite,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    PROCUREMENT_PLAN_DIGEST_ALGORITHM,
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
        "raw_json_valid": True,
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


def _plan(lines: list[dict], *, as_of: date = date(2026, 7, 30), identity: str = "idfp") -> object:
    return plan_procurement(
        source_lines=lines,
        account_index=_index(),
        identity_fingerprint=identity,
        identity_fingerprint_algorithm_version="identity_fp_v2",
        as_of_date=as_of,
        run_context="synthetic_fixture",
        known_account_ids=frozenset({"a_hospital", "a_lab"}),
        build_stamp="2026-07-30T00:00:00+00:00",
    )


def test_every_source_outcome_class_in_fingerprint() -> None:
    lines = [
        _line(source_record_id="v1", verified=True, tender_key="T-V"),
        _line(
            source_record_id="u1",
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
            buyer_domain=None,
        ),
        _line(
            source_record_id="l1",
            verified=True,
            tender_key="T-L",
            raw_lead_join_status="lead_only",
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
    comps = plan.source_fingerprint_components
    assert comps["all_source_lines"]["n"] == 4
    assert comps["verified_tender_key_lines"]["n"] == 2
    assert comps["unresolved_tender_key_lines"]["n"] == 2


def test_routes_and_resolution_cardinality() -> None:
    lines = [
        _line(source_record_id="1", tender_key="T-A", buyer_domain="hrs.cl"),
        _line(
            source_record_id="2",
            tender_key="T-F",
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
    assert res[by_tender["T-A"]].auto_link_allowed == 1
    assert res[by_tender["T-F"]].resolution_status == RESOLUTION_UNLINKED
    assert res[by_tender["T-F"]].link_route == ROUTE_NO_MATCH
    assert res[by_tender["T-F"]].account_id is None


def test_multiline_coalesce_and_field_conflict() -> None:
    lines = [
        _line(source_record_id="1", tender_key="T-M", title="A", region="RM"),
        _line(
            source_record_id="2",
            tender_key="T-M",
            title="B",
            region="V",
            lead_id=99,
            first_seen_at="2025-01-03",
            last_seen_at="2025-01-04",
        ),
    ]
    plan = _plan(lines)
    assert len(plan.signals) == 1
    assert plan.signals[0].line_item_count == 2
    assert any(c.reason_code == "line_field_conflict_across_tender_lines" for c in plan.conflicts)


def test_shuffled_source_and_tender_lines_identical() -> None:
    lines = [
        _line(source_record_id="1", tender_key="T-1", lead_id=10),
        _line(source_record_id="2", tender_key="T-1", lead_id=20, title="other"),
        _line(
            source_record_id="3",
            tender_key="T-2",
            buyer_domain=None,
            buyer_name_norm="organismo desconocido",
            buyer_display="Organismo Desconocido",
            lead_id=30,
        ),
        _line(
            source_record_id="9",
            verified=False,
            tender_key="9",
            tender_key_kind="unresolved_tender_key",
            lead_id=40,
        ),
    ]
    p1 = _plan(lines)
    shuffled = list(reversed(lines))
    p2 = _plan(shuffled)
    assert p1.source_fingerprint == p2.source_fingerprint
    assert p1.build_plan_fingerprint == p2.build_plan_fingerprint
    assert p1.plan_digest == p2.plan_digest
    assert canonical_json([r.to_db_row() for r in p1.signals]) == canonical_json(
        [r.to_db_row() for r in p2.signals]
    )
    assert canonical_json([r.to_db_row() for r in p1.resolutions]) == canonical_json(
        [r.to_db_row() for r in p2.resolutions]
    )


def test_surrogate_lead_ids_do_not_affect_result() -> None:
    a = [_line(source_record_id="1", tender_key="T-1", lead_id=1)]
    b = [_line(source_record_id="1", tender_key="T-1", lead_id=999999)]
    assert _plan(a).plan_digest == _plan(b).plan_digest
    assert _plan(a).source_fingerprint == _plan(b).source_fingerprint


def test_repeated_planning_byte_equivalent() -> None:
    lines = [_line()]
    p1 = _plan(lines)
    p2 = _plan(deepcopy(lines))
    assert canonical_json(p1.table_rows()) == canonical_json(p2.table_rows())
    assert p1.plan_digest == p2.plan_digest


def test_source_semantic_change_affects_fingerprints() -> None:
    a = _plan([_line(title="A")])
    b = _plan([_line(title="B")])
    assert a.source_fingerprint != b.source_fingerprint
    assert a.build_plan_fingerprint != b.build_plan_fingerprint
    assert a.plan_digest != b.plan_digest


def test_as_of_changes_build_plan_not_source() -> None:
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
    assert early.signals[0].procurement_context != late.signals[0].procurement_context


def test_identity_change_affects_resolutions_and_build_plan() -> None:
    lines = [_line()]
    a = _plan(lines, identity="aaa")
    b = _plan(lines, identity="bbb")
    assert a.source_fingerprint == b.source_fingerprint
    assert a.build_plan_fingerprint != b.build_plan_fingerprint


def test_no_duplicate_stable_ids() -> None:
    plan = _plan(
        [
            _line(source_record_id="1", tender_key="T-1"),
            _line(source_record_id="2", tender_key="T-2", buyer_domain=None, buyer_name_norm="x"),
            _line(
                source_record_id="9",
                verified=False,
                tender_key="9",
                tender_key_kind="unresolved_tender_key",
            ),
        ]
    )
    for name, rows in plan.table_rows().items():
        if name == "commercial_procurement_build_meta":
            keys = [r["meta_key"] for r in rows]
            assert len(keys) == len(set(keys))
            continue
        pk = {
            "commercial_procurement_signal": "procurement_id",
            "commercial_procurement_account_resolution": "resolution_id",
            "commercial_procurement_evidence": "evidence_id",
            "commercial_procurement_conflict": "conflict_id",
            "commercial_procurement_enrichment_candidate": "candidate_id",
        }[name]
        ids = [r[pk] for r in rows]
        assert len(ids) == len(set(ids)), name


def test_temp_sqlite_schema_validation() -> None:
    plan = _plan(
        [
            _line(),
            _line(
                source_record_id="9",
                verified=False,
                tender_key="9",
                tender_key_kind="unresolved_tender_key",
            ),
        ]
    )
    counts = validate_plan_in_temp_sqlite(
        plan, known_account_ids=frozenset({"a_hospital", "a_lab"})
    )
    assert counts["commercial_procurement_signal"] == 1
    assert counts["commercial_procurement_account_resolution"] == 1


def test_stable_procurement_id_ignores_lead() -> None:
    assert stable_procurement_id(source_system="chilecompra", canonical_tender_key="T-1") == (
        stable_procurement_id(source_system="chilecompra", canonical_tender_key="T-1")
    )


def test_plan_digest_sensitive_to_row_change() -> None:
    a = _plan([_line(title="A")])
    b = _plan([_line(title="B")])
    assert a.plan_digest != b.plan_digest
    # Algorithm constant present
    assert plan_digest(table_rows=a.table_rows(), algorithm=PROCUREMENT_PLAN_DIGEST_ALGORITHM)


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
    # Snapshot PR2/PR3 markers
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

    with pytest.raises(ApplyNotImplementedError):
        run_procurement_dry_run(
            sqlite_path=db,
            as_of_date="2026-07-30",
            run_context="local_fixture",
            apply=True,
        )


def test_production_access_remains_readonly(tmp_path: Path) -> None:
    db, _, _ = _fixture_db(tmp_path)
    conn = connect_production_readonly(db)
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE nope(x INTEGER)")
    finally:
        conn.close()
