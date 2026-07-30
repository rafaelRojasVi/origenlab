"""Focused tests for PR4 commercial procurement data walkthrough / redaction."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from origenlab_email_pipeline.commercial_procurement.walkthrough import (
    build_case_d_synthetic,
    build_walkthrough,
    render_markdown,
)
from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
    assert_no_pii_leaks,
    contains_email_pattern,
    redact_account,
    redact_domain,
    redact_email,
    redact_mapping,
    redact_org,
    redact_source,
    redact_tender,
    redact_token,
)


def test_redaction_tokens_are_deterministic_and_kind_prefixed() -> None:
    assert redact_org("Hospital Demo") == redact_org("  HOSPITAL DEMO ")
    assert redact_org("Hospital Demo").startswith("org_")
    assert redact_domain("hrs.cl") == redact_domain("HRS.CL")
    assert redact_domain("hrs.cl").startswith("domain_")
    assert redact_email("a@hrs.cl").startswith("email_")
    assert redact_account("acct_1").startswith("account_")
    assert redact_source("9632017").startswith("source_")
    assert redact_tender("1234-56-LE26").startswith("tender_")
    assert redact_token("org", "x") == redact_token("org", "x")


def test_redaction_hides_emails_and_domains_in_mappings() -> None:
    row = redact_mapping(
        {
            "buyer_email_norm": "buyer@hrs.cl",
            "buyer_domain_norm": "hrs.cl",
            "buyer_name_raw": "Hospital Regional Sur",
            "constituent_source_ids_json": '["9632017"]',
            "subject_key": "v1|procurement-source|chilecompra|9632017",
            "detail_json": json.dumps(
                {"source_record_id": "9632017", "field": "buyer_domain"}
            ),
        }
    )
    blob = json.dumps(row)
    assert not contains_email_pattern(blob)
    assert "hrs.cl" not in blob
    assert "9632017" not in blob
    assert "Hospital Regional Sur" not in blob
    assert row["buyer_email_norm"].startswith("email_")
    assert row["buyer_domain_norm"].startswith("domain_")
    assert "source_" in row["constituent_source_ids_json"]
    assert_no_pii_leaks(blob, forbidden_domains=frozenset({"hrs.cl"}))


def test_assert_no_pii_leaks_catches_email_and_forbidden_domain() -> None:
    try:
        assert_no_pii_leaks("contact me@example.com")
        raise AssertionError("expected email leak")
    except AssertionError as exc:
        assert "email" in str(exc)
    try:
        assert_no_pii_leaks(
            "see hrs.cl portal", forbidden_domains=frozenset({"hrs.cl"})
        )
        raise AssertionError("expected domain leak")
    except AssertionError as exc:
        assert "hrs.cl" in str(exc)


def _seed_walkthrough_source(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE external_leads_raw(
          source_name TEXT, source_record_id TEXT, raw_json TEXT, source_url TEXT, fetched_at TEXT
        );
        CREATE TABLE lead_master(
          id INTEGER PRIMARY KEY, source_name TEXT, source_record_id TEXT, org_name TEXT,
          org_name_norm TEXT, domain TEXT, domain_norm TEXT, email TEXT, email_norm TEXT,
          region TEXT, status TEXT, evidence_summary TEXT, first_seen_at TEXT, last_seen_at TEXT,
          lead_type TEXT
        );
        CREATE TABLE commercial_identity_account(
          account_id TEXT PRIMARY KEY, canonical_name TEXT, normalized_name TEXT,
          primary_domain TEXT, identity_confidence TEXT, identity_status TEXT
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
        CREATE TABLE commercial_opportunity(
          opportunity_id TEXT PRIMARY KEY, canonical_stage TEXT
        );
        CREATE TABLE commercial_deal(id INTEGER PRIMARY KEY, deal_key TEXT);
        CREATE TABLE opportunity_signals(id INTEGER PRIMARY KEY, signal_key TEXT);
        """
    )
    # Case A/C style: matched verified tender with both_equal buyer name
    linked_raw = {
        "CodigoExterno": "WALK-LINK-1",
        "NombreOrganismo": "Hospital Regional Sur",
        "Dominio": "hrs.cl",
        "Email": "compras@hrs.cl",
        "CodigoEstado": "6",
        "Estado": "Cerrada",
        "FechaCierre": "2025-02-01",
        "Nombre": "kit linked",
        "Region": "RM",
    }
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "10", json.dumps(linked_raw), None, "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (1,'chilecompra','10',?,?,?,?,?,?, 'RM','nuevo','1','2026-07-01','2026-07-01','tender_buyer')""",
        (
            "Hospital Regional Sur",
            "hospital regional sur",
            "hrs.cl",
            "hrs.cl",
            "compras@hrs.cl",
            "compras@hrs.cl",
        ),
    )
    # Case B style: unresolved tender key
    unresolved_raw = {
        "Codigo": "436",
        "NombreOrganismo": "Unresolved Buyer Org",
        "Nombre": "kit unresolved",
    }
    conn.execute(
        "INSERT INTO external_leads_raw VALUES (?,?,?,?,?)",
        ("chilecompra", "20", json.dumps(unresolved_raw), None, "2026-07-01"),
    )
    conn.execute(
        """INSERT INTO lead_master(
            id, source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
            email, email_norm, region, status, evidence_summary, first_seen_at, last_seen_at, lead_type
        ) VALUES (2,'chilecompra','20',?,?, NULL,NULL, NULL,NULL, NULL,'nuevo','1',
                  '2026-07-01','2026-07-01','tender_buyer')""",
        ("Unresolved Buyer Org", "unresolved buyer org"),
    )
    conn.execute(
        "INSERT INTO commercial_identity_account VALUES "
        "('a_hospital','Hospital Regional Sur','hospital regional sur',"
        "'hrs.cl','high','active')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_account_domain VALUES "
        "('a_hospital','hrs.cl',1,'institutional_domain')"
    )
    for k, v in [
        ("schema_version", "commercial_identity_v1"),
        ("identity_fingerprint", "b" * 64),
        ("identity_fingerprint_algorithm_version", "identity_fp_v2"),
        ("run_context", "local_fixture"),
    ]:
        conn.execute("INSERT INTO commercial_identity_build_meta VALUES (?,?)", (k, v))
    conn.execute("INSERT INTO commercial_opportunity VALUES ('o1','fulfillment')")
    conn.execute("INSERT INTO commercial_deal VALUES (1,'d1')")
    conn.execute("INSERT INTO opportunity_signals VALUES (1,'s1')")
    conn.commit()
    conn.close()


def test_walkthrough_fixture_is_read_only_on_source_and_marks_origins(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite"
    _seed_walkthrough_source(source)
    before = source.read_bytes()
    case_a_db = tmp_path / "case_a.sqlite"
    case_d_db = tmp_path / "case_d.sqlite"
    bundle = build_walkthrough(
        sqlite_path=source,
        as_of=date(2026, 7, 30),
        synthetic_db=case_d_db,
        case_a_disposable_db=case_a_db,
    )
    after = source.read_bytes()
    assert before == after
    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        pr4 = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'commercial_procurement%'"
        ).fetchall()
    finally:
        conn.close()
    assert pr4 == []
    assert bundle.case_a["origin"] == "production-derived"
    assert bundle.case_b["origin"] == "production-derived"
    assert bundle.case_c["origin"] == "production-derived"
    assert bundle.case_d["origin"] == "synthetic"
    assert bundle.case_a["disposable_readback"]["matches_planned_core_fields"] is True
    assert bundle.case_d["resolution"]["review_status"] == "needs_review"
    assert all(v is None for v in bundle.case_d["resolution_safe"].values())
    md = render_markdown(bundle)
    assert_no_pii_leaks(md, forbidden_domains=bundle.forbidden_domains)
    assert "hrs.cl" not in md
    assert "compras@" not in md
    assert "Hospital Regional Sur" not in md
    assert "SYNTHETIC" in md
    # Stable selection keys across rerun
    bundle2 = build_walkthrough(
        sqlite_path=source,
        as_of=date(2026, 7, 30),
        synthetic_db=tmp_path / "case_d2.sqlite",
        case_a_disposable_db=tmp_path / "case_a2.sqlite",
    )
    assert bundle.case_a["selected_key"] == bundle2.case_a["selected_key"]
    assert bundle.case_b["selected_key"] == bundle2.case_b["selected_key"]
    assert bundle.case_c["selected_key"] == bundle2.case_c["selected_key"]


def test_case_d_synthetic_marked_and_persists_without_touching_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "untouched.sqlite"
    _seed_walkthrough_source(source)
    before = source.read_bytes()
    case_a = {
        "selected_key": {"link_route": "A_exact_institutional_domain"},
    }
    out = build_case_d_synthetic(
        case_a=case_a,
        tmp_db=tmp_path / "syn.sqlite",
        as_of=date(2026, 7, 30),
    )
    assert out["origin"] == "synthetic"
    assert "SYNTHETIC" in out["synthetic_mutations"]["label"]
    assert out["apply_summary"]["applied"] is True
    assert out["safety"]["production_mutated"] is False
    assert len(out["plane_conflict_rows"]) >= 3
    assert source.read_bytes() == before
    blob = json.dumps(out)
    assert not contains_email_pattern(blob)
    assert "synthetic-lead-hospital.cl" not in blob
    assert_no_pii_leaks(blob)


def test_source_and_account_ids_redacted_consistently() -> None:
    sid = "9632017"
    aid = "a_hospital"
    assert (
        redact_source(sid)
        == redact_mapping({"source_record_id": sid})["source_record_id"]
    )
    assert redact_account(aid) == redact_mapping({"account_id": aid})["account_id"]
