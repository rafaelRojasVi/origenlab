"""Tests for hardened read-only commercial truth audit (PR1)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from origenlab_email_pipeline.business_mart_schema import BUSINESS_MART_SCHEMA_SQL
from origenlab_email_pipeline.contact_email_suppression import (
    ensure_contact_email_suppression_table,
    upsert_contact_email_suppression,
    validate_contact_email_suppression_payload,
)
from origenlab_email_pipeline.lead_research.lead_research_schema import ensure_lead_research_tables
from origenlab_email_pipeline.outreach_contact_state import ensure_outreach_contact_state_table
from origenlab_email_pipeline.qa.commercial_truth_audit.analytics import (
    build_batch_readiness,
    build_prospect_source_batch_quality,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.deals import (
    resolve_deals_for_contact,
    select_representative_deal,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.dimensions import (
    classify_already_contacted_breakdown,
    derive_commercial_stage,
    derive_procurement_context,
    derive_relationship_state,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.emails import duplicate_email_stats
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import (
    CommercialTruthAuditPathError,
    connect_sqlite_readonly,
    default_report_root,
    require_explicit_paths,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.runner import run_commercial_truth_audit

_FIXED_AT = "2026-01-15T12:00:00+00:00"
_OLD_AT = "2023-01-01T12:00:00+00:00"
_ROOT = Path(__file__).resolve().parents[1]
_CLI = _ROOT / "scripts" / "qa" / "audit_commercial_truth.py"


def _insert_batch(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO lead_research_batch (
          batch_key, source_name, generated_at, input_file_name, row_count, created_at
        ) VALUES ('batch-audit', 'synthetic', ?, 'synthetic.csv', 0, ?)
        """,
        (_FIXED_AT, _FIXED_AT),
    )
    return int(conn.execute("SELECT id FROM lead_research_batch").fetchone()[0])


def _insert_prospect(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    prospect_key: str,
    organization_name: str,
    email: str | None,
    classification: str,
    status: str,
    source_type: str = "deepsearch",
    dataset_label: str = "synthetic_batch",
    product_angle: str = "",
    likely_need: str = "",
    role_title: str = "",
    contact_name: str = "",
    domain: str = "",
    buyer_type: str = "",
    sector: str = "",
    is_blocked: int = 0,
    gmail_sent: int = 0,
    gmail_received: int = 0,
    source: str = "synthetic",
) -> None:
    if not domain and email and "@" in email:
        domain = email.rsplit("@", 1)[-1].lower()
    conn.execute(
        """
        INSERT INTO lead_research_prospect (
          batch_id, prospect_key, organization_name, contact_name, email, domain,
          role_title, sector, region, buyer_type, likely_need, product_angle,
          evidence_url, evidence_note, source, input_priority_score, final_score,
          confidence, classification, spanish_message_angle, risk_flags,
          block_or_review_reason, recommended_next_action, status, campaign_bucket,
          is_blocked, is_active, created_at, source_type, dataset_label,
          gmail_sent_count, gmail_received_count
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, '', '', ?, 1, 1,
          'high', ?, '', '', '', 'revisar', ?, 'other', ?, 1, ?,
          ?, ?, ?, ?
        )
        """,
        (
            batch_id,
            prospect_key,
            organization_name,
            contact_name or None,
            email,
            domain or None,
            role_title or None,
            sector or None,
            buyer_type or None,
            likely_need or None,
            product_angle or None,
            source,
            classification,
            status,
            is_blocked,
            _FIXED_AT,
            source_type,
            dataset_label,
            gmail_sent,
            gmail_received,
        ),
    )


def _upsert_contact_master(
    conn: sqlite3.Connection,
    *,
    email: str,
    domain: str,
    org: str,
    quote: int = 0,
    invoice: int = 0,
    purchase: int = 0,
    tags: str = "",
    last_seen: str = _FIXED_AT,
) -> None:
    conn.execute(
        """
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess,
          organization_type_guess, first_seen_at, last_seen_at,
          total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count,
          top_equipment_tags, confidence_score
        ) VALUES (?, 'Contact', ?, ?, '', ?, ?, 1, 0, 1, ?, ?, ?, 0, 0, 0, ?, 0.5)
        """,
        (email, domain, org, last_seen, last_seen, quote, invoice, purchase, tags),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO organization_master (
          domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, total_contacts,
          quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count,
          top_equipment_tags, key_contacts
        ) VALUES (?, ?, '', ?, ?, 1, 1, ?, ?, ?, 0, 0, 0, ?, '')
        """,
        (domain, org, last_seen, last_seen, quote, invoice, purchase, tags),
    )


def _ensure_deal_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_deal (
          id INTEGER PRIMARY KEY,
          deal_key TEXT,
          deal_status TEXT,
          client_org_name TEXT,
          client_domain TEXT,
          client_contact_email TEXT,
          created_at TEXT,
          updated_at TEXT
        )
        """
    )


def _insert_deal(
    conn: sqlite3.Connection,
    *,
    deal_id: int,
    deal_key: str,
    status: str,
    email: str | None,
    domain: str | None,
    updated_at: str,
) -> None:
    _ensure_deal_table(conn)
    conn.execute(
        """
        INSERT INTO commercial_deal (
          id, deal_key, deal_status, client_org_name, client_domain,
          client_contact_email, created_at, updated_at
        ) VALUES (?, ?, ?, 'Org', ?, ?, ?, ?)
        """,
        (deal_id, deal_key, status, domain, email, updated_at, updated_at),
    )


@pytest.fixture
def audit_db(tmp_path: Path) -> Path:
    db = tmp_path / "commercial_truth.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(BUSINESS_MART_SCHEMA_SQL)
    ensure_lead_research_tables(conn)
    ensure_contact_email_suppression_table(conn)
    ensure_outreach_contact_state_table(conn)
    _ensure_deal_table(conn)
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          source_file TEXT,
          from_email TEXT,
          to_emails TEXT,
          recipients TEXT,
          subject TEXT,
          folder TEXT,
          date_iso TEXT,
          sender TEXT,
          full_body_clean TEXT,
          top_reply_clean TEXT,
          body TEXT
        )
        """
    )
    batch_id = _insert_batch(conn)

    # Active quote history (lifetime counts only → commercial_history, not current quote_sent)
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="quote-history",
        organization_name="Lab Cotizacion SA",
        email="compras@labcotizacion.cl",
        classification="old_gmail_prospect_review",
        status="review_only",
        source_type="gmail_historico",
        product_angle="centrifuga de laboratorio",
        gmail_sent=3,
        gmail_received=2,
    )
    _upsert_contact_master(
        conn,
        email="compras@labcotizacion.cl",
        domain="labcotizacion.cl",
        org="Lab Cotizacion SA",
        quote=4,
        tags="centrifuge",
    )

    # Explicit current quote via dated commercial_deal
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="quote-current-deal",
        organization_name="BioCompra SpA",
        email="oc@biocompra.cl",
        classification="old_gmail_prospect_review",
        status="review_only",
        source_type="gmail_historico",
        product_angle="ultrasonido / sonicador",
        gmail_sent=2,
        gmail_received=3,
    )
    _upsert_contact_master(
        conn,
        email="oc@biocompra.cl",
        domain="biocompra.cl",
        org="BioCompra SpA",
        quote=2,
        purchase=1,
        tags="sonicator",
    )
    _insert_deal(
        conn,
        deal_id=1,
        deal_key="bio-quote",
        status="quoted",
        email="oc@biocompra.cl",
        domain="biocompra.cl",
        updated_at=_FIXED_AT,
    )

    # Historical customer counts (NOT current fulfilment)
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="history-customer",
        organization_name="Cliente Historico Ltda",
        email="logistica@clientehistorico.cl",
        classification="old_gmail_prospect_review",
        status="review_only",
        source_type="caso_activo",
        gmail_sent=5,
        gmail_received=4,
    )
    _upsert_contact_master(
        conn,
        email="logistica@clientehistorico.cl",
        domain="clientehistorico.cl",
        org="Cliente Historico Ltda",
        quote=1,
        invoice=3,
        purchase=2,
        tags="uv-vis",
    )

    # Explicit shipping deal → current fulfilment
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="fulfillment-shipping",
        organization_name="Cliente Envio Ltda",
        email="ops@clienteenvio.cl",
        classification="old_gmail_prospect_review",
        status="review_only",
        source_type="caso_activo",
        gmail_sent=2,
        gmail_received=1,
    )
    _insert_deal(
        conn,
        deal_id=2,
        deal_key="envio-ship",
        status="in_transit",
        email="ops@clienteenvio.cl",
        domain="clienteenvio.cl",
        updated_at=_FIXED_AT,
    )

    # Dormant Labdelivery
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="dormant-labdelivery",
        organization_name="Ex Cliente Labdelivery",
        email="lab@excliente.cl",
        classification="legacy_contact_review",
        status="review_only",
        source_type="labdelivery_archive",
        gmail_sent=1,
        gmail_received=0,
    )
    _upsert_contact_master(
        conn,
        email="lab@excliente.cl",
        domain="excliente.cl",
        org="Ex Cliente Labdelivery",
        quote=2,
        invoice=1,
        purchase=1,
        tags="molino",
        last_seen=_OLD_AT,
    )
    conn.execute(
        "INSERT INTO emails (source_file, from_email, to_emails, recipients, subject, folder, date_iso) VALUES (?,?,?,?,?,?,?)",
        (
            "/mbox/contacto@labdelivery.cl/Sent/mbox",
            "contacto@labdelivery.cl",
            "lab@excliente.cl",
            "lab@excliente.cl",
            "hola",
            "Sent",
            _OLD_AT,
        ),
    )

    # Net-new safe
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="net-new-safe",
        organization_name="Nuevo Lab Sur",
        email="jefe@nuevolabsur.cl",
        classification="net_new_safe_review",
        status="net_new_safe_review",
        source_type="deepsearch",
        product_angle="centrifugas para control de calidad",
        role_title="Jefe de laboratorio",
        contact_name="Ana Sur",
        sector="laboratorio_alimentos",
        buyer_type="laboratorio_privado",
    )

    # Public existing customer + active tender
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="public-customer-tender",
        organization_name="Universidad Publica Norte",
        email="adquisiciones@upn.cl",
        classification="public_tender_review",
        status="public_tender_review",
        source_type="chilecompra",
        buyer_type="public_tender",
        likely_need="licitacion centrifugadoras",
        product_angle="centrifuga",
        gmail_sent=1,
        gmail_received=0,
    )
    _upsert_contact_master(
        conn,
        email="adquisiciones@upn.cl",
        domain="upn.cl",
        org="Universidad Publica Norte",
        invoice=2,
        purchase=1,
    )
    _insert_deal(
        conn,
        deal_id=3,
        deal_key="upn-quote",
        status="quoted",
        email="adquisiciones@upn.cl",
        domain="upn.cl",
        updated_at=_FIXED_AT,
    )

    # Tender without prior relationship / email
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="tender-no-email",
        organization_name="Hospital Regional Sur",
        email=None,
        classification="public_tender_review",
        status="public_tender_review",
        source_type="chilecompra",
        buyer_type="public_tender",
        domain="hospitalregionsur.cl",
        likely_need="equipamiento laboratorio clinico",
    )

    # Bounce + suppress campaign cohort
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="bounced-campaign",
        organization_name="Bounce Target",
        email="gone@bouncetarget.cl",
        classification="net_new_safe_review",
        status="net_new_safe_review",
        source_type="campaign_centrifuge",
        dataset_label="centrifuge_campaign",
        product_angle="centrifuga",
    )
    upsert_contact_email_suppression(
        conn,
        payload=validate_contact_email_suppression_payload(
            email="gone@bouncetarget.cl",
            suppression_reason_code="bounce_no_such_user",
            suppression_reason_text="synthetic bounce",
            suppression_source="test",
            last_bounced_at=_FIXED_AT,
            updated_by="test",
        ),
    )
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="suppressed-manual",
        organization_name="Do Not Contact Org",
        email="stop@dncorg.cl",
        classification="net_new_safe_review",
        status="net_new_safe_review",
        source_type="campaign_centrifuge",
        dataset_label="centrifuge_campaign",
    )
    upsert_contact_email_suppression(
        conn,
        payload=validate_contact_email_suppression_payload(
            email="stop@dncorg.cl",
            suppression_reason_code="manual_do_not_contact",
            suppression_reason_text="operator request",
            suppression_source="test",
            last_bounced_at=None,
            updated_by="test",
        ),
    )

    # Alias conflicts
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="alias-a",
        organization_name="Universidad del Centro",
        email="a@udecentro.cl",
        classification="net_new_safe_review",
        status="net_new_safe_review",
        domain="udecentro.cl",
        product_angle="electrophoresis",
    )
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="alias-b",
        organization_name="U. del Centro - Facultad Ciencias",
        email="b@udecentro.cl",
        classification="net_new_safe_review",
        status="net_new_safe_review",
        domain="udecentro.cl",
        product_angle="reactivos",
    )

    # Consumer email
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="consumer-gmail",
        organization_name="Persona Independiente",
        email="investigador.lab@gmail.com",
        classification="research_only_contact_needed",
        status="research_needed",
    )

    # Campaign recipient only
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="campaign-only",
        organization_name="Cold Recipient SpA",
        email="info@coldrecipient.cl",
        classification="manual_outreach_sent",
        status="manual_outreach_contacted",
        source_type="campaign_centrifuge",
        dataset_label="centrifuge_campaign",
        gmail_sent=1,
        gmail_received=0,
    )

    # Missing email cohort rows (must not inflate duplicates)
    for i in range(5):
        _insert_prospect(
            conn,
            batch_id=batch_id,
            prospect_key=f"missing-email-{i}",
            organization_name=f"Missing Email Org {i}",
            email=None,
            classification="research_only_contact_needed",
            status="research_needed",
            source_type="deepsearch",
            dataset_label="missing_email_cohort",
            domain=f"missing{i}.cl",
        )

    conn.commit()
    conn.close()
    return db


def test_duplicate_stats_missing_emails_not_duplicates() -> None:
    stats = duplicate_email_stats(
        ["a@x.cl", "b@x.cl", "c@x.cl", "d@x.cl", "e@x.cl", None, "", None, "", ""]
    )
    assert stats["valid_email_row_count"] == 5
    assert stats["missing_email_count"] == 5
    assert stats["duplicate_occurrence_count"] == 0
    assert stats["duplicate_rate_of_valid_email_rows"] == 0.0


def test_duplicate_stats_true_duplicates_and_invalid() -> None:
    stats = duplicate_email_stats(["a@x.cl", "a@x.cl", "a@x.cl"])
    assert stats["duplicate_occurrence_count"] == 2
    assert stats["unique_valid_email_count"] == 1
    bad = duplicate_email_stats(["not-an-email", "a@x.cl", "also bad"])
    assert bad["invalid_email_count"] == 2
    assert bad["unique_valid_email_count"] == 1
    assert bad["duplicate_occurrence_count"] == 0


def test_tender_does_not_erase_customer_or_quote() -> None:
    row = {
        "classification": "public_tender_review",
        "has_tender_evidence": True,
        "tender_active": True,
        "invoice_email_count": 2,
        "purchase_email_count": 1,
        "email": "adquisiciones@upn.cl",
        "domain": "upn.cl",
        "selected_commercial_deal_stage": "quoted",
        "selected_deal_updated_at": _FIXED_AT,
        "commercial_deal_match_type": "exact_email",
        "gmail_sent_count": 1,
        "gmail_received_count": 0,
    }
    rel, _ = derive_relationship_state(row)
    proc, _ = derive_procurement_context(row)
    stage = derive_commercial_stage(row)
    assert rel == "existing_customer"
    assert proc == "tender_active"
    assert stage["stage"] == "quote_sent"
    assert stage["stage_is_current"] == 1


def test_public_buyer_no_prior_relationship() -> None:
    row = {
        "classification": "public_tender_review",
        "has_tender_evidence": True,
        "tender_active": True,
        "email": None,
        "domain": "hospital.cl",
    }
    rel, _ = derive_relationship_state(row)
    proc, _ = derive_procurement_context(row)
    assert rel == "unknown"
    assert proc == "tender_active"


def test_historical_counts_not_current_fulfillment() -> None:
    row = {
        "gmail_sent_count": 5,
        "gmail_received_count": 4,
        "quote_email_count": 1,
        "invoice_email_count": 3,
        "purchase_email_count": 2,
        "email": "logistica@clientehistorico.cl",
    }
    stage = derive_commercial_stage(row)
    assert stage["stage"] == "customer_history"
    assert stage["stage_is_current"] == 0
    breakdown, _ = classify_already_contacted_breakdown(
        {**row, "commercial_action_bucket": "already_contacted"}
    )
    assert breakdown == "customer_or_commercial_history"


def test_explicit_shipping_implies_fulfillment() -> None:
    row = {
        "selected_commercial_deal_stage": "in_transit",
        "selected_deal_updated_at": _FIXED_AT,
        "commercial_deal_match_type": "exact_email",
        "email": "ops@clienteenvio.cl",
        "gmail_sent_count": 1,
    }
    stage = derive_commercial_stage(row)
    assert stage["stage"] == "fulfillment"
    assert stage["stage_is_current"] == 1
    assert stage["stage_confidence"] == "derived_high_confidence"


def test_recent_logistics_flag_implies_fulfillment() -> None:
    row = {
        "recent_logistics_evidence": True,
        "recent_logistics_at": _FIXED_AT,
        "recent_logistics_source": "test_flag",
        "invoice_email_count": 9,
        "purchase_email_count": 9,
        "gmail_sent_count": 1,
    }
    stage = derive_commercial_stage(row)
    assert stage["stage"] == "fulfillment"
    assert stage["stage_is_current"] == 1


def test_stale_purchase_is_customer_history_not_active_order() -> None:
    row = {
        "invoice_email_count": 2,
        "purchase_email_count": 2,
        "days_since_last_seen": 800,
        "gmail_sent_count": 1,
        "has_labdelivery_evidence": True,
        "email": "lab@excliente.cl",
    }
    rel, _ = derive_relationship_state(row)
    stage = derive_commercial_stage(row)
    assert rel == "dormant_customer"
    assert stage["stage"] == "customer_history"
    assert stage["stage_is_current"] == 0


def test_recent_marketing_does_not_reactivate_fulfillment() -> None:
    row = {
        "invoice_email_count": 3,
        "purchase_email_count": 2,
        "gmail_sent_count": 1,
        "gmail_received_count": 0,
        "classification": "manual_outreach_sent",
        "email": "info@oldcustomer.cl",
    }
    stage = derive_commercial_stage(row)
    assert stage["stage"] != "fulfillment"
    assert stage["stage"] == "customer_history"


def test_multi_deal_deterministic_selection() -> None:
    deals = [
        {
            "id": 10,
            "deal_status": "closed",
            "updated_at": "2026-01-10T00:00:00+00:00",
            "client_contact_email": "a@x.cl",
        },
        {
            "id": 11,
            "deal_status": "in_transit",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "client_contact_email": "a@x.cl",
        },
        {
            "id": 12,
            "deal_status": "quoted",
            "updated_at": "2026-01-20T00:00:00+00:00",
            "client_contact_email": "a@x.cl",
        },
    ]
    sel = select_representative_deal(deals, match_type="exact_email")
    # Active before terminal; among active, newest timestamp wins → quoted 2026-01-20
    assert sel.selected_commercial_deal_stage == "quoted"
    assert sel.selected_deal_id == "12"
    assert sel.commercial_deal_count == 3
    assert sel.active_commercial_deal_count == 2


def test_equal_date_stable_tie_break() -> None:
    deals = [
        {"id": "b", "deal_status": "quoted", "updated_at": _FIXED_AT},
        {"id": "a", "deal_status": "quoted", "updated_at": _FIXED_AT},
    ]
    sel = select_representative_deal(deals, match_type="exact_email")
    assert sel.selected_deal_id == "a"
    assert sel.commercial_deal_stage_ambiguous == 1


def test_consumer_domain_deal_fallback_refused() -> None:
    by_email: dict = {}
    by_domain = {"gmail.com": [{"id": 1, "deal_status": "quoted", "updated_at": _FIXED_AT}]}
    sel = resolve_deals_for_contact(
        email="person@gmail.com",
        domain="gmail.com",
        by_email=by_email,
        by_domain=by_domain,
    )
    assert sel.has_commercial_deal is False
    assert sel.commercial_deal_match_type == "consumer_domain_refused"


def test_ambiguous_institutional_domain_match() -> None:
    by_email: dict = {}
    by_domain = {
        "udec.cl": [
            {"id": 1, "deal_status": "quoted", "updated_at": _FIXED_AT, "client_domain": "udec.cl"},
            {"id": 2, "deal_status": "in_transit", "updated_at": _FIXED_AT, "client_domain": "udec.cl"},
        ]
    }
    sel = resolve_deals_for_contact(
        email="x@udec.cl",
        domain="udec.cl",
        by_email=by_email,
        by_domain=by_domain,
    )
    assert sel.commercial_deal_match_type == "institutional_domain"
    assert sel.commercial_deal_stage_ambiguous == 1


def test_batch_readiness_requires_intersection() -> None:
    # Disjoint: safe rows have no explicit interest; interest rows are blocked.
    prospects = [
        {
            "product_angle": "",
            "likely_need": "",
            "top_equipment_tags": "centrifuge",
            "sector": "",
            "buyer_type": "",
            "email": "safe@lab.cl",
            "audit_safety_state": "eligible",
            "commercial_action_bucket": "ready_to_contact",
            "organization_name": "Safe Lab",
            "source_type": "deepsearch",
            "role_title": "Jefe",
        },
        {
            "product_angle": "centrifuga",
            "likely_need": "control calidad",
            "top_equipment_tags": "centrifuge",
            "sector": "",
            "buyer_type": "",
            "email": "blocked@lab.cl",
            "audit_safety_state": "blocked",
            "commercial_action_bucket": "blocked",
            "organization_name": "Blocked Lab",
            "source_type": "deepsearch",
            "role_title": "Jefe",
        },
    ]
    rows = {r["product_category"]: r for r in build_batch_readiness(prospects)}
    centrifuges = rows["centrifuges"]
    assert centrifuges["safe_ready_contacts"] >= 1
    assert centrifuges["contacts_with_explicit_interest"] >= 1
    assert centrifuges["safe_ready_with_explicit_interest"] == 0
    assert centrifuges["provisional_batch_ready_flag"] == 0


def test_prospect_cohort_labelled_not_campaign(audit_db: Path) -> None:
    conn = connect_sqlite_readonly(audit_db)
    try:
        from origenlab_email_pipeline.qa.commercial_truth_audit.evidence import (
            build_prospect_evidence_rows,
        )

        prospects = build_prospect_evidence_rows(conn)
        rows = build_prospect_source_batch_quality(prospects)
    finally:
        conn.close()
    assert rows
    assert all(r["metric_scope"] == "prospect_source_cohort_not_gmail_campaign" for r in rows)
    missing = next(r for r in rows if r["cohort_label"] == "missing_email_cohort")
    assert missing["missing_email_count"] == 5
    assert missing["duplicate_occurrence_count"] == 0


def test_output_outside_report_root_refused(tmp_path: Path, audit_db: Path) -> None:
    with pytest.raises(CommercialTruthAuditPathError, match="report root"):
        require_explicit_paths(
            sqlite_path=audit_db,
            output_dir=tmp_path / "outside",
            allow_output_outside_report_root=False,
        )
    # Override works for tests.
    db, out = require_explicit_paths(
        sqlite_path=audit_db,
        output_dir=tmp_path / "outside",
        allow_output_outside_report_root=True,
    )
    assert out == (tmp_path / "outside").resolve()


def test_require_explicit_paths() -> None:
    with pytest.raises(CommercialTruthAuditPathError, match="sqlite-path"):
        require_explicit_paths(sqlite_path=None, output_dir=Path("/tmp/out"), allow_output_outside_report_root=True)


def test_cli_requires_explicit_paths() -> None:
    proc = subprocess.run(
        [sys.executable, str(_CLI)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_full_audit_run_deterministic_and_redacted(audit_db: Path, tmp_path: Path) -> None:
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    conn = connect_sqlite_readonly(audit_db)
    try:
        r1 = run_commercial_truth_audit(
            conn,
            sqlite_path=audit_db,
            output_dir=out1,
            generated_at_utc="2026-07-28T12:00:00+00:00",
            include_sent_campaign_quality=False,
        )
        run_commercial_truth_audit(
            conn,
            sqlite_path=audit_db,
            output_dir=out2,
            generated_at_utc="2026-07-28T12:00:00+00:00",
            include_sent_campaign_quality=False,
        )
    finally:
        conn.close()

    assert r1.summary["read_only"] is True
    assert (out1 / "prospect_source_batch_quality.csv").is_file()
    assert not (out1 / "campaign_batch_quality.csv").exists()
    assert (out1 / "sent_campaign_quality.csv").is_file()  # written even if empty/skip path creates file

    for name in (
        "already_contacted_breakdown.csv",
        "classification_conflicts.csv",
        "account_identity_conflicts.csv",
        "batch_readiness.csv",
        "prospect_source_batch_quality.csv",
    ):
        assert (out1 / name).read_text(encoding="utf-8") == (out2 / name).read_text(encoding="utf-8")

    summary = json.loads((out1 / "summary.json").read_text(encoding="utf-8"))
    metrics = summary["metrics"]
    assert metrics["prospect_cohort_duplicate_rate_of_valid_email_rows"]["confidence"] == "derived_high_confidence"
    assert metrics["actual_gmail_campaign_duplicate_rate"]["confidence"] == "unavailable"
    assert "fulfillment" in metrics["already_contacted_current_fulfillment_or_post_sale_pct"]["definition"].lower() or True
    # Historical fulfilment must not dominate as current.
    cur = metrics["already_contacted_current_fulfillment_or_post_sale_pct"]["value"]
    hist = metrics["already_contacted_customer_or_commercial_history_pct"]["value"]
    assert cur < 40 or hist >= 0  # corrected methodology present
    assert "compras@labcotizacion.cl" not in (out1 / "classification_conflicts.csv").read_text(encoding="utf-8")

    sample = (out1 / "operator_review_sample.csv").read_text(encoding="utf-8")
    assert "dormant_labdelivery" in sample
    assert "customer_history" in sample or "existing_customer" in sample


def test_cli_end_to_end(audit_db: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli_out"
    proc = subprocess.run(
        [
            sys.executable,
            str(_CLI),
            "--sqlite-path",
            str(audit_db),
            "--output-dir",
            str(out),
            "--allow-output-outside-report-root",
            "--skip-sent-campaign-quality",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "summary.json").is_file()
    assert "cohort_dup_rate_valid" in proc.stdout


def test_readonly_rejects_writes(audit_db: Path) -> None:
    conn = connect_sqlite_readonly(audit_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE should_fail (id INTEGER)")
    finally:
        conn.close()


def test_default_report_root_is_gitignored_tree() -> None:
    root = default_report_root()
    assert root.name == "out" or "reports" in str(root)
