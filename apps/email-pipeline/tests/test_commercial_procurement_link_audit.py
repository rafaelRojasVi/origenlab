"""Synthetic tests for PR4 procurement ↔ account-link audit invariants."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.coalesce import (
    coalesce_verified_tender_lines,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    PROCUREMENT_CONTEXT_HISTORICAL,
    PROCUREMENT_CONTEXT_TENDER_ACTIVE,
    PROCUREMENT_CONTEXT_TENDER_WATCH,
    PROCUREMENT_CONTEXT_UNKNOWN,
    REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY,
    ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
    ROUTE_DOMAIN_REFUSED,
    ROUTE_EXACT_ALIAS,
    ROUTE_EXACT_CANONICAL_NAME,
    ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
    ROUTE_NAME_DOMAIN_CONFLICT,
    ROUTE_NO_MATCH,
    TENDER_KEY_CODIGO_EXTERNO,
    TENDER_KEY_UNRESOLVED,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.fingerprint import (
    procurement_build_plan_fingerprint,
    procurement_source_fingerprint,
    semantic_signal_fingerprint_row,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.link_routes import (
    build_account_index,
    classify_account_link_route,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.normalize import (
    canonical_tender_key_from_raw,
    is_marketplace_domain,
    is_weak_public_unit_name,
    sanitize_buyer_domain,
    stable_token,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.output_redaction import (
    assert_no_leakage,
    redact_email,
    scrub_row,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.runner import (
    run_procurement_link_audit,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.sqlite_readonly import (
    ProcurementLinkAuditPathError,
    connect_readonly,
    require_explicit_paths,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.status import (
    classify_procurement_context,
)


def _index() -> object:
    accounts = [
        {
            "account_id": "a_hospital",
            "canonical_name_norm": "hospital regional sur",
            "primary_domain_norm": "hrs.cl",
        },
        {
            "account_id": "a_muni_a",
            "canonical_name_norm": "municipalidad de prueba",
            "primary_domain_norm": "muni-a.cl",
        },
        {
            "account_id": "a_muni_b",
            "canonical_name_norm": "municipalidad de prueba",
            "primary_domain_norm": "muni-b.cl",
        },
        {
            "account_id": "a_lab",
            "canonical_name_norm": "laboratorio nacional",
            "primary_domain_norm": "labnac.cl",
        },
        {
            "account_id": "a_alias_only",
            "canonical_name_norm": "organismo alias only",
            "primary_domain_norm": "aliasonly.cl",
        },
        {
            "account_id": "a_canon_other",
            "canonical_name_norm": "shared buyer label",
            "primary_domain_norm": "canon-other.cl",
        },
    ]
    aliases = [
        {"account_id": "a_lab", "alias_norm": "lab nacional chile"},
        {"account_id": "a_alias_only", "alias_norm": "shared buyer label"},
    ]
    domains = [
        {"account_id": "a_hospital", "domain_norm": "hrs.cl"},
        {"account_id": "a_muni_a", "domain_norm": "muni-a.cl"},
        {"account_id": "a_muni_b", "domain_norm": "muni-b.cl"},
        {"account_id": "a_lab", "domain_norm": "labnac.cl"},
        {"account_id": "a_alias_only", "domain_norm": "aliasonly.cl"},
        {"account_id": "a_canon_other", "domain_norm": "canon-other.cl"},
    ]
    return build_account_index(accounts=accounts, aliases=aliases, domains=domains)


def test_line_items_prefer_verified_codigo_externo() -> None:
    raw = {"Codigo": "111", "CodigoExterno": "2277-2-LR25", "NombreOrganismo": "HOSPITAL"}
    key, kind, verified = canonical_tender_key_from_raw(source_record_id="111", raw=raw)
    assert key == "2277-2-LR25"
    assert kind == TENDER_KEY_CODIGO_EXTERNO
    assert verified is True
    key2, _, v2 = canonical_tender_key_from_raw(
        source_record_id="222", raw={"Codigo": "222", "CodigoExterno": "2277-2-LR25"}
    )
    assert key == key2 and v2 is True


def test_line_level_codigo_is_unresolved_not_canonical() -> None:
    key, kind, verified = canonical_tender_key_from_raw(
        source_record_id="111", raw={"Codigo": "111", "NombreOrganismo": "X"}
    )
    assert key == "111"
    assert kind == TENDER_KEY_UNRESOLVED
    assert verified is False


def test_exact_unique_institutional_domain() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="hospital regional sur",
        buyer_domain="hrs.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_EXACT_INSTITUTIONAL_DOMAIN
    assert r.auto_link_allowed is True
    assert r.account_ids == ("a_hospital",)


def test_exact_unique_alias() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="lab nacional chile",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_EXACT_ALIAS
    assert r.auto_link_allowed is True


def test_alias_and_canonical_different_accounts_ambiguous() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="shared buyer label",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_AMBIGUOUS_MULTI_ACCOUNT
    assert set(r.account_ids) == {"a_alias_only", "a_canon_other"}
    assert r.auto_link_allowed is False


def test_ambiguous_same_name_accounts() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="municipalidad de prueba",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_AMBIGUOUS_MULTI_ACCOUNT
    assert r.auto_link_allowed is False
    assert set(r.account_ids) == {"a_muni_a", "a_muni_b"}


def test_distinct_institutional_domains_not_merged_by_name() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="municipalidad de prueba",
        buyer_domain="muni-a.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_EXACT_INSTITUTIONAL_DOMAIN
    assert r.account_ids == ("a_muni_a",)


def test_name_domain_conflict() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="hospital regional sur",
        buyer_domain="labnac.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_NAME_DOMAIN_CONFLICT
    assert r.auto_link_allowed is False


def test_consumer_email_does_not_block_institutional_domain() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="hospital regional sur",
        buyer_domain="hrs.cl",
        email_domain="gmail.com",
        email_norm="buyer@gmail.com",
    )
    assert r.route == ROUTE_EXACT_INSTITUTIONAL_DOMAIN
    assert r.auto_link_allowed is True
    assert REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY in r.auxiliary_reason_codes


def test_consumer_email_alone_is_route_h() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm=None,
        buyer_domain=None,
        email_domain="gmail.com",
        email_norm="buyer@gmail.com",
    )
    assert r.route == ROUTE_DOMAIN_REFUSED
    assert r.auto_link_allowed is False


def test_consumer_email_with_unmatched_name_is_no_match_not_h() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="organismo desconocido xyz",
        buyer_domain=None,
        email_domain="gmail.com",
        email_norm="buyer@gmail.com",
    )
    assert r.route == ROUTE_NO_MATCH
    assert REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY in r.auxiliary_reason_codes


def test_internal_domain_refused_alone() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm=None,
        buyer_domain="origenlab.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_DOMAIN_REFUSED


def test_marketplace_domain_ignored() -> None:
    assert is_marketplace_domain("mercadopublico.cl")
    assert sanitize_buyer_domain("www.mercadopublico.cl") is None
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm=None,
        buyer_domain="mercadopublico.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_DOMAIN_REFUSED


def test_missing_tender_id() -> None:
    key, kind, verified = canonical_tender_key_from_raw(source_record_id=None, raw={})
    assert key is None
    assert kind == "missing"
    assert verified is False


def test_missing_status_date_not_active() -> None:
    ctx = classify_procurement_context(
        status_code=None,
        status_name=None,
        close_date=None,
        as_of_date=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_UNKNOWN


def test_closed_tender_historical() -> None:
    ctx = classify_procurement_context(
        status_code="6",
        status_name="Cerrada",
        close_date="2026-08-01",
        as_of_date=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_HISTORICAL


def test_publicada_future_close_active() -> None:
    ctx = classify_procurement_context(
        status_code="5",
        status_name="Publicada",
        close_date="2026-08-15",
        as_of_date=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_TENDER_ACTIVE


def test_publicada_missing_close_is_watch_not_active() -> None:
    ctx = classify_procurement_context(
        status_code="5",
        status_name="Publicada",
        close_date=None,
        as_of_date=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_TENDER_WATCH


def test_as_of_date_required() -> None:
    with pytest.raises(ValueError, match="as_of_date"):
        classify_procurement_context(status_code="5", status_name="Publicada", close_date="2026-08-15")


def test_coalesce_conflicting_lines_no_silent_pick() -> None:
    lines = [
        {
            "source_record_id": "1",
            "lead_id": 1,
            "buyer_name_norm": "hospital a",
            "buyer_display": "Hospital A",
            "buyer_domain": "hrs.cl",
            "email_norm": None,
            "email_domain": None,
            "status_code": "5",
            "status_name": "Publicada",
            "publication_date": "2026-01-01",
            "close_date": "2026-08-01",
            "first_seen_at": "2026-01-01",
            "last_seen_at": "2026-01-02",
            "weak_public_unit_name": False,
            "region": "RM",
            "title": "t1",
        },
        {
            "source_record_id": "2",
            "lead_id": 2,
            "buyer_name_norm": "hospital b",
            "buyer_display": "Hospital B",
            "buyer_domain": "hrs.cl",
            "email_norm": None,
            "email_domain": None,
            "status_code": "5",
            "status_name": "Publicada",
            "publication_date": "2026-01-01",
            "close_date": "2026-08-01",
            "first_seen_at": "2026-01-03",
            "last_seen_at": "2026-01-04",
            "weak_public_unit_name": False,
            "region": "RM",
            "title": "t1",
        },
    ]
    shuffled = list(reversed(lines))
    a = coalesce_verified_tender_lines(
        tender_key="T-1",
        tender_key_kind=TENDER_KEY_CODIGO_EXTERNO,
        lines=lines,
        as_of_date=date(2026, 7, 30),
    )
    b = coalesce_verified_tender_lines(
        tender_key="T-1",
        tender_key_kind=TENDER_KEY_CODIGO_EXTERNO,
        lines=shuffled,
        as_of_date=date(2026, 7, 30),
    )
    assert a["signal"]["constituent_source_record_ids"] == ["1", "2"]
    assert a["signal"]["buyer_name_norm"] is None  # conflict → no silent pick
    assert any(c["field"] == "buyer_name_norm" for c in a["conflicts"])
    assert a["signal"]["first_seen_at"] == "2026-01-01"
    assert a["signal"]["last_seen_at"] == "2026-01-04"
    assert a["signal"] == b["signal"]


def test_fingerprint_stable_under_shuffle_and_surrogate_id() -> None:
    base = {
        "tender_key": "T-1",
        "tender_key_kind": TENDER_KEY_CODIGO_EXTERNO,
        "constituent_source_record_ids": ["1", "2"],
        "buyer_display": "Hospital",
        "buyer_name_norm": "hospital",
        "buyer_domain": "hrs.cl",
        "email_norm": "",
        "email_domain": "",
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
        "procurement_context": "historical_tender",
        "context_reason_code": "status_inactive_or_closed",
        "line_item_count": 2,
        "line_conflicts": [],
        "source_system": "chilecompra",
        "lead_id": 99,
    }
    other = dict(base)
    other["lead_id"] = 12345
    rows_a = [base, {**base, "tender_key": "T-2", "constituent_source_record_ids": ["3"]}]
    rows_b = list(reversed([{**other, "tender_key": "T-2", "constituent_source_record_ids": ["3"]}, other]))
    # Drop lead_id from semantic row explicitly via fingerprint helper
    fp1 = procurement_source_fingerprint(semantic_rows=rows_a)
    fp2 = procurement_source_fingerprint(semantic_rows=rows_b)
    assert fp1["fingerprint"] == fp2["fingerprint"]
    assert "lead_id" not in semantic_signal_fingerprint_row(base)

    changed = dict(base)
    changed["status_code"] = "5"
    changed["status_name"] = "Publicada"
    fp3 = procurement_source_fingerprint(semantic_rows=[changed])
    assert fp3["fingerprint"] != procurement_source_fingerprint(semantic_rows=[base])["fingerprint"]

    membership = dict(base)
    membership["constituent_source_record_ids"] = ["1", "2", "9"]
    assert (
        procurement_source_fingerprint(semantic_rows=[membership])["fingerprint"]
        != procurement_source_fingerprint(semantic_rows=[base])["fingerprint"]
    )

    plan1 = procurement_build_plan_fingerprint(
        source_fingerprint=fp1["fingerprint"],
        identity_fingerprint="abc",
        as_of_date="2026-07-30",
    )
    plan2 = procurement_build_plan_fingerprint(
        source_fingerprint=fp1["fingerprint"],
        identity_fingerprint="abc",
        as_of_date="2026-07-31",
    )
    assert plan1["fingerprint"] != plan2["fingerprint"]


def test_redaction_strips_email_and_paths() -> None:
    row = scrub_row({"note": "contact me at person@example.com path=/home/rafael/secret"})
    text = json.dumps(row)
    assert "/home/" not in text
    assert_no_leakage(text)
    assert redact_email("a@b.cl").startswith("email:")


def test_weak_public_unit_name_flags_generic() -> None:
    assert is_weak_public_unit_name("municipalidad x", "Municipalidad X")


def test_require_explicit_paths(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.close()
    with pytest.raises(ProcurementLinkAuditPathError):
        require_explicit_paths(sqlite_path=None, output_dir=tmp_path / "out")
    out = tmp_path / "out"
    with pytest.raises(ProcurementLinkAuditPathError):
        require_explicit_paths(sqlite_path=db, output_dir=out)
    got_db, got_out = require_explicit_paths(
        sqlite_path=db,
        output_dir=out,
        allow_output_outside_report_root=True,
    )
    assert got_db == db.resolve()
    assert got_out == out.resolve()


def test_connect_readonly_query_only(tmp_path: Path) -> None:
    db = tmp_path / "ro.sqlite"
    sqlite3.connect(db).close()
    conn = connect_readonly(db)
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE nope(x INTEGER)")
    finally:
        conn.close()


def test_fixture_audit_runner_verified_coalesce_and_pr3_isolation(tmp_path: Path) -> None:
    db = tmp_path / "audit.sqlite"
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
        "CodigoEstado": "5",
        "Estado": "Publicada",
        "FechaCierre": "2026-08-20",
    }
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "1", json.dumps(raw), "http://www.mercadopublico.cl/x", "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (1,'chilecompra','1','Hospital Regional Sur','hospital regional sur','hrs.cl','hrs.cl',
                  'buyer@gmail.com','buyer@gmail.com','RM','nuevo','1 — title','2026-07-01','2026-07-01','tender_buyer')"""
    )
    raw2 = dict(raw)
    raw2["Codigo"] = "2"
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "2", json.dumps(raw2), "http://www.mercadopublico.cl/x", "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (2,'chilecompra','2','Hospital Regional Sur','hospital regional sur',NULL,NULL,
                  NULL,NULL,'RM','nuevo','2 — title','2026-07-02','2026-07-02','tender_buyer')"""
    )
    # unresolved line-only row
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "9", json.dumps({"Codigo": "9", "NombreOrganismo": "X"}), None, "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (9,'chilecompra','9','X','x',NULL,NULL,NULL,NULL,'RM','nuevo','9','2026-07-01','2026-07-01','tender_buyer')"""
    )
    conn.execute(
        "INSERT INTO commercial_identity_account VALUES ('a_hospital','Hospital Regional Sur','hospital regional sur','hrs.cl','high','active')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_account_domain VALUES ('a_hospital','hrs.cl',1,'institutional_domain')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_build_meta VALUES ('identity_fingerprint','abc'), ('schema_version','commercial_identity_v1')"
    )
    conn.execute("INSERT INTO commercial_opportunity VALUES ('o1','fulfillment')")
    conn.commit()
    conn.close()

    out = tmp_path / "out"
    summary = run_procurement_link_audit(
        sqlite_path=db, output_dir=out, as_of_date=date(2026, 7, 30)
    )
    assert summary["metrics"]["coalesced_verified_tenders"] == 1
    assert summary["metrics"]["multi_line_verified_tenders"] == 1
    assert summary["metrics"]["unresolved_tender_key_rows"] == 1
    assert summary["metrics"]["auto_link_allowed_signals"] == 1
    assert summary["as_of_date"] == "2026-07-30"
    assert summary["safety"]["mutations"] is False
    conn2 = sqlite3.connect(db)
    assert conn2.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0] == 1
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.startswith("commercial_procurement") for t in tables)
    conn2.close()
    report = (out / "audit_report.md").read_text(encoding="utf-8")
    assert_no_leakage(report)
    assert "buyer@gmail.com" not in report
    assert (out / "proposed_schema.md").is_file()
    assert (out / "tender_key_kind_distribution.csv").is_file()


def test_exact_canonical_name_route() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="laboratorio nacional",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_EXACT_CANONICAL_NAME
    assert r.auto_link_allowed is True


def test_no_match_when_unknown_buyer() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="organismo desconocido xyz",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_NO_MATCH
    assert r.auto_link_allowed is False


def test_stable_token_not_randomized() -> None:
    assert stable_token("buyer", "Hospital Sur") == stable_token("buyer", "Hospital Sur")
    assert stable_token("buyer", "A") != stable_token("buyer", "B")
