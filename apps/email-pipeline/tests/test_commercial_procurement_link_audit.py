"""Synthetic tests for PR4 procurement ↔ account-resolution audit invariants."""

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
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_LINKED,
    RESOLUTION_REFUSED,
    RESOLUTION_UNLINKED,
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
    conflict_id_for_source_row,
    procurement_build_plan_fingerprint,
    procurement_source_fingerprint,
    source_line_semantic_payload,
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
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.resolution import (
    assert_resolution_invariants,
    build_account_resolution,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.runner import (
    run_procurement_link_audit,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.schema_contract import (
    PR2_LOGICAL_REFERENCE_NOTE,
    PROPOSED_TABLES,
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


def _base_line(**overrides: object) -> dict:
    row = {
        "source_system": "chilecompra",
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
        "title": "kit diagnostico",
        "status_code": "6",
        "status_name": "Cerrada",
        "publication_date": "2025-01-01",
        "close_date": "2025-02-01",
        "first_seen_at": "2025-01-01",
        "last_seen_at": "2025-01-02",
        "weak_public_unit_name": False,
    }
    row.update(overrides)
    return row


def test_line_items_prefer_verified_codigo_externo() -> None:
    raw = {"Codigo": "111", "CodigoExterno": "2277-2-LR25", "NombreOrganismo": "HOSPITAL"}
    key, kind, verified = canonical_tender_key_from_raw(source_record_id="111", raw=raw)
    assert key == "2277-2-LR25"
    assert kind == TENDER_KEY_CODIGO_EXTERNO
    assert verified is True


def test_line_level_codigo_is_unresolved_not_canonical() -> None:
    key, kind, verified = canonical_tender_key_from_raw(
        source_record_id="111", raw={"Codigo": "111", "NombreOrganismo": "X"}
    )
    assert kind == TENDER_KEY_UNRESOLVED
    assert verified is False


def test_exact_unique_institutional_domain_resolves_linked() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="hospital regional sur",
        buyer_domain="hrs.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_EXACT_INSTITUTIONAL_DOMAIN
    res = build_account_resolution(procurement_id="p_test", result=r)
    assert_resolution_invariants(res)
    assert res.resolution_status == RESOLUTION_LINKED
    assert res.account_id == "a_hospital"
    assert res.auto_link_allowed is True


def test_exact_unique_alias_linked() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="lab nacional chile",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_EXACT_ALIAS
    res = build_account_resolution(procurement_id="p_test", result=r)
    assert res.resolution_status == RESOLUTION_LINKED
    assert res.account_id == "a_lab"


def test_alias_and_canonical_different_accounts_ambiguous_no_account_id() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="shared buyer label",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_AMBIGUOUS_MULTI_ACCOUNT
    res = build_account_resolution(procurement_id="p_test", result=r)
    assert_resolution_invariants(res)
    assert res.resolution_status == RESOLUTION_AMBIGUOUS
    assert res.account_id is None
    assert set(res.candidate_account_ids) == {"a_alias_only", "a_canon_other"}


def test_ambiguous_same_name_accounts() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="municipalidad de prueba",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_AMBIGUOUS_MULTI_ACCOUNT
    res = build_account_resolution(procurement_id="p_test", result=r)
    assert res.account_id is None


def test_name_domain_conflict_ambiguous_no_selected_account() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="hospital regional sur",
        buyer_domain="labnac.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_NAME_DOMAIN_CONFLICT
    res = build_account_resolution(procurement_id="p_test", result=r)
    assert res.resolution_status == RESOLUTION_AMBIGUOUS
    assert res.account_id is None


def test_consumer_email_does_not_block_institutional_domain() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="hospital regional sur",
        buyer_domain="hrs.cl",
        email_domain="gmail.com",
        email_norm="buyer@gmail.com",
    )
    assert r.route == ROUTE_EXACT_INSTITUTIONAL_DOMAIN
    assert REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY in r.auxiliary_reason_codes


def test_consumer_email_alone_is_refused() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm=None,
        buyer_domain=None,
        email_domain="gmail.com",
        email_norm="buyer@gmail.com",
    )
    assert r.route == ROUTE_DOMAIN_REFUSED
    res = build_account_resolution(procurement_id="p_test", result=r)
    assert res.resolution_status == RESOLUTION_REFUSED
    assert res.account_id is None


def test_no_match_unlinked_no_account_id() -> None:
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm="organismo desconocido xyz",
        buyer_domain=None,
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_NO_MATCH
    res = build_account_resolution(procurement_id="p_test", result=r)
    assert res.resolution_status == RESOLUTION_UNLINKED
    assert res.account_id is None


def test_marketplace_and_internal_refused() -> None:
    assert is_marketplace_domain("mercadopublico.cl")
    assert sanitize_buyer_domain("www.mercadopublico.cl") is None
    r = classify_account_link_route(
        index=_index(),
        buyer_name_norm=None,
        buyer_domain="origenlab.cl",
        email_domain=None,
        email_norm=None,
    )
    assert r.route == ROUTE_DOMAIN_REFUSED


def test_linked_routes_abc_e_only() -> None:
    for name, domain, route in [
        ("hospital regional sur", "hrs.cl", ROUTE_EXACT_INSTITUTIONAL_DOMAIN),
        ("laboratorio nacional", None, ROUTE_EXACT_CANONICAL_NAME),
        ("lab nacional chile", None, ROUTE_EXACT_ALIAS),
    ]:
        r = classify_account_link_route(
            index=_index(),
            buyer_name_norm=name,
            buyer_domain=domain,
            email_domain=None,
            email_norm=None,
        )
        assert r.route == route
        res = build_account_resolution(procurement_id="p_x", result=r)
        assert res.resolution_status == RESOLUTION_LINKED
        assert res.account_id is not None


def test_missing_status_date_not_active() -> None:
    ctx = classify_procurement_context(
        status_code=None,
        status_name=None,
        close_date=None,
        as_of_date=date(2026, 7, 30),
    )
    assert ctx["procurement_context"] == PROCUREMENT_CONTEXT_UNKNOWN


def test_closed_and_active_contexts() -> None:
    assert (
        classify_procurement_context(
            status_code="6",
            status_name="Cerrada",
            close_date="2026-08-01",
            as_of_date=date(2026, 7, 30),
        )["procurement_context"]
        == PROCUREMENT_CONTEXT_HISTORICAL
    )
    assert (
        classify_procurement_context(
            status_code="5",
            status_name="Publicada",
            close_date="2026-08-15",
            as_of_date=date(2026, 7, 30),
        )["procurement_context"]
        == PROCUREMENT_CONTEXT_TENDER_ACTIVE
    )
    assert (
        classify_procurement_context(
            status_code="5",
            status_name="Publicada",
            close_date=None,
            as_of_date=date(2026, 7, 30),
        )["procurement_context"]
        == PROCUREMENT_CONTEXT_TENDER_WATCH
    )


def test_coalesce_title_conflict_fingerprint_changes() -> None:
    a = _base_line(source_record_id="1", title="title-A", lead_id=99)
    b = _base_line(source_record_id="2", title="title-B", lead_id=1)
    c = _base_line(source_record_id="2", title="title-C", lead_id=1)
    agg_ab = coalesce_verified_tender_lines(
        tender_key="T-1",
        tender_key_kind=TENDER_KEY_CODIGO_EXTERNO,
        lines=[a, b],
        as_of_date=date(2026, 7, 30),
    )
    agg_ac = coalesce_verified_tender_lines(
        tender_key="T-1",
        tender_key_kind=TENDER_KEY_CODIGO_EXTERNO,
        lines=[a, c],
        as_of_date=date(2026, 7, 30),
    )
    assert agg_ab["signal"]["title"] is None
    assert agg_ac["signal"]["title"] is None
    assert any(x["field"] == "title" for x in agg_ab["conflicts"])
    fp_ab = procurement_source_fingerprint(
        source_line_payloads=[source_line_semantic_payload(a), source_line_semantic_payload(b)]
    )
    fp_ac = procurement_source_fingerprint(
        source_line_payloads=[source_line_semantic_payload(a), source_line_semantic_payload(c)]
    )
    assert fp_ab["fingerprint"] != fp_ac["fingerprint"]
    # Shuffled stability
    fp_ba = procurement_source_fingerprint(
        source_line_payloads=[source_line_semantic_payload(b), source_line_semantic_payload(a)]
    )
    assert fp_ab["fingerprint"] == fp_ba["fingerprint"]
    # lead_id does not affect order / selection
    assert agg_ab["signal"]["constituent_source_record_ids"] == ["1", "2"]


def test_coalesce_region_email_status_conflicts() -> None:
    base = _base_line(source_record_id="1")
    region_b = _base_line(source_record_id="2", region="V")
    email_b = _base_line(source_record_id="2", email_norm="a@hrs.cl", email_domain="hrs.cl")
    email_c = _base_line(source_record_id="2", email_norm="b@hrs.cl", email_domain="other.cl")
    status_b = _base_line(source_record_id="2", status_code="5", status_name="Publicada", close_date="2026-08-01")

    reg = coalesce_verified_tender_lines(
        tender_key="T-1",
        tender_key_kind=TENDER_KEY_CODIGO_EXTERNO,
        lines=[base, region_b],
        as_of_date=date(2026, 7, 30),
    )
    assert any(c["field"] == "region" for c in reg["conflicts"])

    em = coalesce_verified_tender_lines(
        tender_key="T-1",
        tender_key_kind=TENDER_KEY_CODIGO_EXTERNO,
        lines=[email_b, email_c],
        as_of_date=date(2026, 7, 30),
    )
    assert any(c["field"] == "email_domain" for c in em["conflicts"])
    # No raw emails in conflict detail
    blob = json.dumps(em["conflicts"])
    assert "@" not in blob

    st = coalesce_verified_tender_lines(
        tender_key="T-1",
        tender_key_kind=TENDER_KEY_CODIGO_EXTERNO,
        lines=[base, status_b],
        as_of_date=date(2026, 7, 30),
    )
    assert any(c["field"] == "status_code" for c in st["conflicts"])


def test_source_fp_includes_unresolved_and_changes_with_fields() -> None:
    verified = source_line_semantic_payload(_base_line())
    unresolved = source_line_semantic_payload(
        _base_line(
            verified=False,
            tender_key="9",
            tender_key_kind=TENDER_KEY_UNRESOLVED,
            source_record_id="9",
            title="u1",
        )
    )
    fp1 = procurement_source_fingerprint(source_line_payloads=[verified, unresolved])
    unresolved2 = dict(unresolved)
    unresolved2["title"] = "u2"
    fp2 = procurement_source_fingerprint(source_line_payloads=[verified, unresolved2])
    assert fp1["fingerprint"] != fp2["fingerprint"]
    assert fp1["components"]["unresolved_tender_key_lines"]["n"] == 1

    fp3 = procurement_source_fingerprint(source_line_payloads=[verified])
    assert fp3["fingerprint"] != fp1["fingerprint"]  # removal of unresolved


def test_as_of_and_identity_affect_build_plan_not_source() -> None:
    payloads = [source_line_semantic_payload(_base_line())]
    src = procurement_source_fingerprint(source_line_payloads=payloads)
    # as_of is not in source payloads
    assert "as_of_date" not in json.dumps(payloads)
    plan_a = procurement_build_plan_fingerprint(
        source_fingerprint=src["fingerprint"],
        identity_fingerprint="id_a",
        as_of_date="2026-07-30",
    )
    plan_b = procurement_build_plan_fingerprint(
        source_fingerprint=src["fingerprint"],
        identity_fingerprint="id_a",
        as_of_date="2026-07-31",
    )
    plan_c = procurement_build_plan_fingerprint(
        source_fingerprint=src["fingerprint"],
        identity_fingerprint="id_b",
        as_of_date="2026-07-30",
    )
    plan_d = procurement_build_plan_fingerprint(
        source_fingerprint=src["fingerprint"],
        identity_fingerprint="id_a",
        as_of_date="2026-07-30",
        resolver_build_contract_version="procurement_resolver_vX",
    )
    assert plan_a["fingerprint"] != plan_b["fingerprint"]
    assert plan_a["fingerprint"] != plan_c["fingerprint"]
    assert plan_a["fingerprint"] != plan_d["fingerprint"]
    # source unchanged by as_of
    assert src["fingerprint"] == procurement_source_fingerprint(source_line_payloads=payloads)["fingerprint"]


def test_unresolved_conflict_id_direct_provenance() -> None:
    cid = conflict_id_for_source_row(
        source_system="chilecompra",
        source_record_id="99",
        reason_code="tender_key_unresolved_line_or_fallback",
    )
    assert cid.startswith("c_")
    assert cid == conflict_id_for_source_row(
        source_system="chilecompra",
        source_record_id="99",
        reason_code="tender_key_unresolved_line_or_fallback",
    )


def test_schema_uses_account_resolution_not_link() -> None:
    assert "commercial_procurement_account_resolution" in PROPOSED_TABLES
    assert "commercial_procurement_account_link" not in PROPOSED_TABLES
    assert "logical" in PR2_LOGICAL_REFERENCE_NOTE and "PR2 account reference" in PR2_LOGICAL_REFERENCE_NOTE


def test_physical_cross_model_fk_would_interfere_with_pr2_rebuild_design_note() -> None:
    """Synthetic design assertion: cross-model FK couples PR2 rebuilds to PR4."""
    res = PROPOSED_TABLES["commercial_procurement_account_resolution"]
    assert "logical PR2" in res["notes"] or "Logical PR2" in res["notes"]
    # No physical FK to commercial_identity_account
    assert not any("commercial_identity_account" in fk and "physical" in fk.lower() for fk in res["fk"])
    assert any("procurement_id → commercial_procurement_signal" in fk for fk in res["fk"])
    # Simulated: PR2 rebuild deletes accounts while PR4 still references them.
    pr2_accounts = {"a_hospital"}
    linked_account_id = "a_hospital"
    pr2_accounts.clear()  # independent DELETE+INSERT rebuild
    assert linked_account_id not in pr2_accounts
    # Apply-time validation must recheck existence rather than rely on SQLite FK.


def test_redaction_and_paths() -> None:
    row = scrub_row({"note": "contact me at person@example.com path=/home/rafael/secret"})
    text = json.dumps(row)
    assert "/home/" not in text
    assert_no_leakage(text)
    assert redact_email("a@b.cl").startswith("email:")


def test_weak_public_unit_name_flags_generic() -> None:
    assert is_weak_public_unit_name("municipalidad x", "Municipalidad X")


def test_require_explicit_paths(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    sqlite3.connect(db).close()
    with pytest.raises(ProcurementLinkAuditPathError):
        require_explicit_paths(sqlite_path=None, output_dir=tmp_path / "out")
    with pytest.raises(ProcurementLinkAuditPathError):
        require_explicit_paths(sqlite_path=db, output_dir=tmp_path / "out")
    got_db, got_out = require_explicit_paths(
        sqlite_path=db,
        output_dir=tmp_path / "out",
        allow_output_outside_report_root=True,
    )
    assert got_db == db.resolve()


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


def test_fixture_audit_runner_resolution_and_source_fp(tmp_path: Path) -> None:
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
        "INSERT INTO commercial_identity_build_meta VALUES ('identity_fingerprint','abc')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_build_meta VALUES ('identity_fingerprint_algorithm_version','identity_fp_v2')"
    )
    conn.execute(
        "INSERT INTO commercial_identity_build_meta VALUES ('schema_version','commercial_identity_v1')"
    )
    conn.execute("INSERT INTO commercial_opportunity VALUES ('o1','fulfillment')")
    conn.commit()
    conn.close()

    out = tmp_path / "out"
    summary = run_procurement_link_audit(
        sqlite_path=db, output_dir=out, as_of_date=date(2026, 7, 30)
    )
    assert summary["metrics"]["coalesced_verified_tenders"] == 1
    assert summary["metrics"]["unresolved_tender_key_rows"] == 1
    assert summary["metrics"]["auto_link_allowed_signals"] == 1
    assert summary["metrics"]["source_fingerprint_line_count"] == 3
    assert summary["metrics"]["resolution_status_distribution"].get("linked") == 1
    assert summary["safety"]["mutations"] is False
    conn2 = sqlite3.connect(db)
    tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.startswith("commercial_procurement") for t in tables)
    conn2.close()
    assert (out / "account_resolution_distribution.csv").is_file()
    report = (out / "audit_report.md").read_text(encoding="utf-8")
    assert_no_leakage(report)
    assert "buyer@gmail.com" not in report


def test_stable_token_not_randomized() -> None:
    assert stable_token("buyer", "Hospital Sur") == stable_token("buyer", "Hospital Sur")
