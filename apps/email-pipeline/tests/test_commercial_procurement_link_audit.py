"""Synthetic tests for PR4 procurement ↔ account-link audit invariants."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.fingerprint import (
    procurement_source_fingerprint,
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
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.redaction import (
    assert_no_leakage,
    redact_email,
    scrub_row,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.readonly import (
    ProcurementLinkAuditPathError,
    connect_readonly,
    require_explicit_paths,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.runner import (
    run_procurement_link_audit,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.status import (
    classify_procurement_context,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    PROCUREMENT_CONTEXT_HISTORICAL,
    PROCUREMENT_CONTEXT_TENDER_ACTIVE,
    PROCUREMENT_CONTEXT_TENDER_WATCH,
    PROCUREMENT_CONTEXT_UNKNOWN,
    ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
    ROUTE_DOMAIN_REFUSED,
    ROUTE_EXACT_ALIAS,
    ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
    ROUTE_NAME_DOMAIN_CONFLICT,
    ROUTE_NO_MATCH,
)


def _index() -> object:
    accounts = [
        {"account_id": "a_hospital", "canonical_name_norm": "hospital regional sur", "primary_domain_norm": "hrs.cl"},
        {"account_id": "a_muni_a", "canonical_name_norm": "municipalidad de prueba", "primary_domain_norm": "muni-a.cl"},
        {"account_id": "a_muni_b", "canonical_name_norm": "municipalidad de prueba", "primary_domain_norm": "muni-b.cl"},
        {"account_id": "a_lab", "canonical_name_norm": "laboratorio nacional", "primary_domain_norm": "labnac.cl"},
    ]
    aliases = [
        {"account_id": "a_lab", "alias_norm": "lab nacional chile"},
    ]
    domains = [
        {"account_id": "a_hospital", "domain_norm": "hrs.cl"},
        {"account_id": "a_muni_a", "domain_norm": "muni-a.cl"},
        {"account_id": "a_muni_b", "domain_norm": "muni-b.cl"},
        {"account_id": "a_lab", "domain_norm": "labnac.cl"},
    ]
    return build_account_index(accounts=accounts, aliases=aliases, domains=domains)


def test_line_items_coalesce_prefer_codigo_externo() -> None:
    raw = {"Codigo": "111", "CodigoExterno": "2277-2-LR25", "NombreOrganismo": "HOSPITAL"}
    key, kind = canonical_tender_key_from_raw(source_record_id="111", raw=raw)
    assert key == "2277-2-LR25"
    assert kind == "codigo_externo"
    key2, _ = canonical_tender_key_from_raw(source_record_id="222", raw={"Codigo": "222", "CodigoExterno": "2277-2-LR25"})
    assert key == key2


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
    # Domain decides uniquely to a_muni_a (compatible with name set).
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


def test_consumer_email_does_not_link() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="hospital regional sur",
        buyer_domain=None,
        email_domain="gmail.com",
        email_norm="buyer@gmail.com",
    )
    assert r.route == ROUTE_DOMAIN_REFUSED
    assert r.auto_link_allowed is False


def test_internal_domain_refused() -> None:
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
        buyer_name_norm="hospital regional sur",
        buyer_domain="mercadopublico.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_DOMAIN_REFUSED


def test_missing_tender_id() -> None:
    key, kind = canonical_tender_key_from_raw(source_record_id=None, raw={})
    assert key is None
    assert kind == "missing"


def test_missing_status_date_not_active() -> None:
    ctx = classify_procurement_context(
        status_code=None,
        status_name=None,
        close_date=None,
        today=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_UNKNOWN


def test_closed_tender_historical() -> None:
    ctx = classify_procurement_context(
        status_code="6",
        status_name="Cerrada",
        close_date="2026-08-01",
        today=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_HISTORICAL


def test_publicada_future_close_active() -> None:
    ctx = classify_procurement_context(
        status_code="5",
        status_name="Publicada",
        close_date="2026-08-15",
        today=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_TENDER_ACTIVE


def test_publicada_missing_close_is_watch_not_active() -> None:
    ctx = classify_procurement_context(
        status_code="5",
        status_name="Publicada",
        close_date=None,
        today=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_TENDER_WATCH


def test_stable_token_not_randomized() -> None:
    assert stable_token("buyer", "Hospital Sur") == stable_token("buyer", "Hospital Sur")
    assert stable_token("buyer", "A") != stable_token("buyer", "B")


def test_fingerprint_stable_under_shuffle() -> None:
    rows_a = [{"tender_key": "t1", "n": 1}, {"tender_key": "t2", "n": 2}]
    rows_b = list(reversed(rows_a))
    fp1 = procurement_source_fingerprint(
        chilecompra_raw_keys=rows_a,
        chilecompra_lead_keys=rows_a,
        coalesced_tender_keys=rows_a,
    )
    fp2 = procurement_source_fingerprint(
        chilecompra_raw_keys=rows_b,
        chilecompra_lead_keys=rows_b,
        coalesced_tender_keys=rows_b,
    )
    assert fp1["fingerprint"] == fp2["fingerprint"]


def test_redaction_strips_email_and_paths() -> None:
    row = scrub_row({"note": "contact me at person@example.com path=/home/rafael/secret"})
    text = json.dumps(row)
    assert "@" not in text or "email:" in text
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
    # outside reports/out without allow flag
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


def test_fixture_audit_runner_does_not_create_pr3_or_mutate(tmp_path: Path) -> None:
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
                  NULL,NULL,'RM','nuevo','1 — title','2026-07-01','2026-07-01','tender_buyer')"""
    )
    # second line same tender
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
                  NULL,NULL,'RM','nuevo','2 — title','2026-07-01','2026-07-01','tender_buyer')"""
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
    summary = run_procurement_link_audit(sqlite_path=db, output_dir=out, today=date(2026, 7, 30))
    assert summary["metrics"]["coalesced_tenders"] == 1
    assert summary["metrics"]["multi_line_tenders"] == 1
    assert summary["safety"]["mutations"] is False
    # PR3 count observed but not mutated
    assert summary["metrics"]["chilecompra_line_rows"] == 2
    conn2 = sqlite3.connect(db)
    assert conn2.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0] == 1
    assert conn2.execute("SELECT COUNT(*) FROM commercial_identity_account").fetchone()[0] == 1
    # no procurement tables created
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.startswith("commercial_procurement") for t in tables)
    conn2.close()
    # outputs exist and are redacted
    report = (out / "audit_report.md").read_text(encoding="utf-8")
    assert_no_leakage(report)
    assert (out / "proposed_schema.md").is_file()
    assert (out / "summary.json").is_file()


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
