"""Tests for read-only commercial truth audit (PR1)."""

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
from origenlab_email_pipeline.outreach_contact_state import (
    ensure_outreach_contact_state_table,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.dimensions import (
    classify_already_contacted_breakdown,
    derive_commercial_stage,
    derive_relationship_state,
    derive_safety_state,
    enrich_audit_dimensions,
    is_consumer_email,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import (
    CommercialTruthAuditPathError,
    connect_sqlite_readonly,
    require_explicit_paths,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.redaction import redact_email
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
          ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, '', '', 'synthetic', 1, 1,
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


def _upsert_signal(
    conn: sqlite3.Connection,
    *,
    email: str,
    domain: str,
    quote: int = 0,
    procurement: int = 0,
    technical: int = 0,
) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS commercial_contact_signal_rollup (
          contact_email TEXT PRIMARY KEY,
          org_domain TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          evidence_email_count INTEGER NOT NULL,
          positive_signal_count INTEGER NOT NULL,
          suppression_signal_count INTEGER NOT NULL,
          suppression_reason_codes TEXT NOT NULL,
          positive_reason_codes TEXT NOT NULL,
          quote_signal_count INTEGER NOT NULL,
          procurement_signal_count INTEGER NOT NULL,
          technical_signal_count INTEGER NOT NULL,
          repeated_interaction_count INTEGER NOT NULL,
          invoice_or_payment_signal_count INTEGER NOT NULL,
          logistics_signal_count INTEGER NOT NULL,
          vendor_like_signal_count INTEGER NOT NULL,
          existing_client_signal_count INTEGER NOT NULL,
          confidence_score REAL NOT NULL,
          strength_score REAL NOT NULL,
          is_suppressed INTEGER NOT NULL,
          suppression_summary TEXT NOT NULL,
          run_id INTEGER,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO commercial_contact_signal_rollup (
          contact_email, org_domain, first_seen_at, last_seen_at,
          evidence_email_count, positive_signal_count, suppression_signal_count,
          suppression_reason_codes, positive_reason_codes,
          quote_signal_count, procurement_signal_count, technical_signal_count,
          repeated_interaction_count, invoice_or_payment_signal_count,
          logistics_signal_count, vendor_like_signal_count, existing_client_signal_count,
          confidence_score, strength_score, is_suppressed, suppression_summary,
          run_id, updated_at
        ) VALUES (?, ?, ?, ?, 1, 1, 0, '', '', ?, ?, ?, 0, 0, 0, 0, 0, 0.8, 0.8, 0, '', 1, ?)
        """,
        (email, domain, _FIXED_AT, _FIXED_AT, quote, procurement, technical, _FIXED_AT),
    )


@pytest.fixture
def audit_db(tmp_path: Path) -> Path:
    db = tmp_path / "commercial_truth.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(BUSINESS_MART_SCHEMA_SQL)
    ensure_lead_research_tables(conn)
    ensure_contact_email_suppression_table(conn)
    ensure_outreach_contact_state_table(conn)
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          source_file TEXT,
          from_email TEXT,
          to_emails TEXT
        )
        """
    )
    batch_id = _insert_batch(conn)

    # 1) Active quote misclassified as generic contacted (gmail history → already_contacted)
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="quote-active",
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
    _upsert_signal(conn, email="compras@labcotizacion.cl", domain="labcotizacion.cl", quote=2)

    # 2) Purchase-pending opportunity
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="purchase-pending",
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
    _upsert_signal(
        conn,
        email="oc@biocompra.cl",
        domain="biocompra.cl",
        quote=1,
        procurement=2,
    )

    # 3) Existing customer fulfilment
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="fulfillment-customer",
        organization_name="Cliente Activo Ltda",
        email="logistica@clienteactivo.cl",
        classification="old_gmail_prospect_review",
        status="review_only",
        source_type="caso_activo",
        gmail_sent=5,
        gmail_received=4,
    )
    _upsert_contact_master(
        conn,
        email="logistica@clienteactivo.cl",
        domain="clienteactivo.cl",
        org="Cliente Activo Ltda",
        quote=1,
        invoice=3,
        purchase=2,
        tags="uv-vis",
    )

    # 4) Dormant Labdelivery relationship
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
        """
        INSERT INTO emails (source_file, from_email, to_emails)
        VALUES (?, ?, ?)
        """,
        (
            "/mbox/contacto@labdelivery.cl/Sent/mbox",
            "contacto@labdelivery.cl",
            "lab@excliente.cl",
        ),
    )

    # 5) Net-new safe prospect
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

    # 6) Tender with known contact
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="tender-with-contact",
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

    # 7) Tender without contact email
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

    # 8) Hard-bounced campaign recipient
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
    payload = validate_contact_email_suppression_payload(
        email="gone@bouncetarget.cl",
        suppression_reason_code="bounce_no_such_user",
        suppression_reason_text="synthetic bounce",
        suppression_source="test",
        last_bounced_at=_FIXED_AT,
        updated_by="test",
    )
    upsert_contact_email_suppression(conn, payload=payload)

    # 9) Suppressed recipient
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
    payload2 = validate_contact_email_suppression_payload(
        email="stop@dncorg.cl",
        suppression_reason_code="manual_do_not_contact",
        suppression_reason_text="operator request",
        suppression_source="test",
        last_bounced_at=None,
        updated_by="test",
    )
    upsert_contact_email_suppression(conn, payload=payload2)

    # 10) Duplicate account aliases (same domain, different org names)
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

    # 11) Consumer email (unsafe domain join)
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="consumer-gmail",
        organization_name="Persona Independiente",
        email="investigador.lab@gmail.com",
        classification="research_only_contact_needed",
        status="research_needed",
        product_angle="",
    )

    # 12) Campaign recipient only (sent, no reply, no commercial depth)
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
        product_angle="",
        gmail_sent=1,
        gmail_received=0,
    )

    # 13) Ambiguous / missing evidence
    _insert_prospect(
        conn,
        batch_id=batch_id,
        prospect_key="ambiguous",
        organization_name="Sin Senales",
        email="x@sinsenales.cl",
        classification="revision_individual",
        status="review_only",
        source_type="deepsearch",
    )

    conn.commit()
    conn.close()
    return db


def test_require_explicit_paths() -> None:
    with pytest.raises(CommercialTruthAuditPathError, match="sqlite-path"):
        require_explicit_paths(sqlite_path=None, output_dir=Path("/tmp/out"))
    with pytest.raises(CommercialTruthAuditPathError, match="output-dir"):
        require_explicit_paths(sqlite_path=Path("/tmp/x.sqlite"), output_dir=None)


def test_cli_requires_explicit_paths(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_CLI)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "--sqlite-path" in (proc.stderr + proc.stdout)


def test_dimensions_quote_vs_campaign() -> None:
    quote_row = enrich_audit_dimensions(
        {
            "classification": "old_gmail_prospect_review",
            "commercial_action_bucket": "already_contacted",
            "gmail_sent_count": 3,
            "gmail_received_count": 2,
            "quote_email_count": 4,
            "quote_signal_count": 2,
            "quote_outbound": True,
            "email": "compras@labcotizacion.cl",
            "domain": "labcotizacion.cl",
        }
    )
    assert quote_row["audit_commercial_stage"] == "quote_sent"
    assert quote_row["audit_already_contacted_breakdown"] == "quotation_related"

    campaign = enrich_audit_dimensions(
        {
            "classification": "manual_outreach_sent",
            "commercial_action_bucket": "already_contacted",
            "gmail_sent_count": 1,
            "gmail_received_count": 0,
            "email": "info@coldrecipient.cl",
            "domain": "coldrecipient.cl",
        }
    )
    assert campaign["audit_already_contacted_breakdown"] == "campaign_recipient_only"
    assert campaign["audit_commercial_stage"] == "contacted_no_reply"


def test_purchase_pending_and_fulfillment() -> None:
    pending = {
        "gmail_sent_count": 2,
        "gmail_received_count": 3,
        "quote_email_count": 2,
        "quote_signal_count": 1,
        "procurement_signal_count": 2,
        "purchase_email_count": 1,
        "invoice_email_count": 0,
        "email": "oc@biocompra.cl",
    }
    stage, _ = derive_commercial_stage(pending)
    assert stage == "purchase_pending"
    breakdown, _ = classify_already_contacted_breakdown(
        {**pending, "commercial_action_bucket": "already_contacted"}
    )
    assert breakdown == "purchase_pending"

    fulfillment = {
        "gmail_sent_count": 5,
        "gmail_received_count": 4,
        "quote_email_count": 1,
        "invoice_email_count": 3,
        "purchase_email_count": 2,
        "email": "logistica@clienteactivo.cl",
    }
    stage2, _ = derive_commercial_stage(fulfillment)
    assert stage2 == "fulfillment"
    rel, _ = derive_relationship_state(fulfillment)
    assert rel == "existing_customer"


def test_dormant_labdelivery_and_net_new() -> None:
    dormant = {
        "has_labdelivery_evidence": True,
        "quote_email_count": 2,
        "invoice_email_count": 1,
        "purchase_email_count": 1,
        "days_since_last_seen": 800,
        "email": "lab@excliente.cl",
        "domain": "excliente.cl",
    }
    rel, _ = derive_relationship_state(dormant)
    assert rel == "dormant_customer"

    net_new = {
        "classification": "net_new_safe_review",
        "email": "jefe@nuevolabsur.cl",
        "domain": "nuevolabsur.cl",
        "gmail_sent_count": 0,
        "gmail_received_count": 0,
    }
    rel2, _ = derive_relationship_state(net_new)
    assert rel2 == "net_new"
    safety, _ = derive_safety_state(net_new)
    assert safety == "eligible"


def test_tender_bounce_suppress_consumer() -> None:
    tender = enrich_audit_dimensions(
        {
            "classification": "public_tender_review",
            "has_tender_evidence": True,
            "tender_active": True,
            "email": "adquisiciones@upn.cl",
        }
    )
    assert tender["audit_commercial_stage"] == "tender_active"
    assert tender["audit_relationship_state"] == "public_buyer"

    bounced = derive_safety_state(
        {"suppression_reason_code": "bounce_no_such_user", "email": "gone@x.cl"}
    )
    assert bounced[0] == "bounced"

    suppressed = derive_safety_state(
        {"suppression_reason_code": "manual_do_not_contact", "email": "stop@x.cl"}
    )
    assert suppressed[0] == "suppressed"

    assert is_consumer_email("investigador.lab@gmail.com") is True
    assert is_consumer_email("jefe@nuevolabsur.cl") is False


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
        )
        run_commercial_truth_audit(
            conn,
            sqlite_path=audit_db,
            output_dir=out2,
            generated_at_utc="2026-07-28T12:00:00+00:00",
        )
    finally:
        conn.close()

    assert r1.summary["read_only"] is True
    assert r1.summary["gmail_mutations"] is False
    assert r1.summary["sqlite_mutations"] is False

    # Expected artifacts present.
    for name in (
        "summary.json",
        "source_inventory.csv",
        "source_overlap.csv",
        "account_identity_conflicts.csv",
        "contact_identity_conflicts.csv",
        "classification_distribution.csv",
        "classification_conflicts.csv",
        "already_contacted_breakdown.csv",
        "opportunity_stage_candidates.csv",
        "open_thread_without_next_action.csv",
        "bounce_leakage.csv",
        "campaign_batch_quality.csv",
        "product_interest_inventory.csv",
        "batch_readiness.csv",
        "labdelivery_relationships.csv",
        "tender_account_links.csv",
        "operator_review_sample.csv",
        "audit_report.md",
    ):
        assert (out1 / name).is_file(), name

    # Deterministic CSV ordering / content for key files (ignore summary path fields).
    for name in (
        "already_contacted_breakdown.csv",
        "classification_conflicts.csv",
        "account_identity_conflicts.csv",
        "batch_readiness.csv",
    ):
        assert (out1 / name).read_text(encoding="utf-8") == (out2 / name).read_text(encoding="utf-8")

    conflicts = (out1 / "classification_conflicts.csv").read_text(encoding="utf-8")
    assert "active_commercial_hidden_in_already_contacted" in conflicts
    assert "compras@labcotizacion.cl" not in conflicts  # redacted
    assert "#" in conflicts  # redacted email marker

    aliases = (out1 / "account_identity_conflicts.csv").read_text(encoding="utf-8")
    assert "domain_multiple_org_names" in aliases
    assert "udecentro.cl" in aliases

    consumer = (out1 / "contact_identity_conflicts.csv").read_text(encoding="utf-8")
    assert "consumer_email_domain_join_unsafe" in consumer

    breakdown = (out1 / "already_contacted_breakdown.csv").read_text(encoding="utf-8")
    assert "campaign_recipient_only" in breakdown
    assert "quotation_related" in breakdown
    assert "purchase_pending" in breakdown

    sample = (out1 / "operator_review_sample.csv").read_text(encoding="utf-8")
    for case in (
        "net_new_verified_prospect",
        "purchase_pending",
        "existing_customer",
        "dormant_labdelivery",
        "tender_opportunity",
        "bounced_address",
        "suppressed_or_blocked",
        "generic_campaign_recipient",
    ):
        assert case in sample

    summary = json.loads((out1 / "summary.json").read_text(encoding="utf-8"))
    assert summary["metrics"]["prospect_rows"] >= 10
    assert summary["metrics"]["already_contacted_count"] >= 1
    assert summary["metrics"]["active_cases_hidden_in_generic_buckets_count"] >= 1

    # Redaction helper itself.
    assert "labcotizacion.cl" in redact_email("compras@labcotizacion.cl")
    assert "compras@" not in redact_email("compras@labcotizacion.cl")


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
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "summary.json").is_file()
    assert "commercial_truth_audit" in proc.stdout


def test_readonly_rejects_writes(audit_db: Path) -> None:
    conn = connect_sqlite_readonly(audit_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE should_fail (id INTEGER)")
    finally:
        conn.close()
