"""Operational overlay: exact-email contacted/suppressed overrides DeepSearch classification."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.contact_domain_suppression import (
    ensure_contact_domain_suppression_table,
)
from origenlab_email_pipeline.contact_email_suppression import (
    ensure_contact_email_suppression_table,
    upsert_contact_email_suppression,
    validate_contact_email_suppression_payload,
)
from origenlab_email_pipeline.lead_research.lead_research_builder import build_lead_research_sqlite
from origenlab_email_pipeline.lead_research.lead_research_mirror_read_model import (
    load_lead_research_mirror_payload,
)
from origenlab_email_pipeline.lead_research.lead_research_operational_overlay import (
    CLASS_BOUNCED_SUPPRESSED,
    CLASS_MANUAL_OUTREACH_SENT,
    CLASS_RESEARCH_ONLY,
    CLASS_SAME_DOMAIN_CONTACTED_REVIEW,
    STATUS_BOUNCED_SUPPRESSED,
    STATUS_MANUAL_CONTACTED,
    STATUS_SAME_DOMAIN_REVIEW,
    apply_operational_overlay_to_prospect,
    load_operational_indexes_from_sqlite,
    summarize_prospects_for_dashboard,
)
from origenlab_email_pipeline.lead_research.lead_research_schema import ensure_lead_research_tables
from origenlab_email_pipeline.outreach_contact_state import (
    ensure_outreach_contact_state_table,
    upsert_outreach_contact_state,
    validate_outreach_contact_state_payload,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "lead_research"

GIBA = "giba@udec.cl"
HANNELORE = "hannelore.valentin@sgs.com"
MFBARRAR = "mfbarrar@ug.uchile.cl"
MLE = "mle@mlelab.cl"
SAME_DOMAIN_UNTOUCHED = "acid@5m.cl"


def _seed_same_domain_prospects(conn: sqlite3.Connection) -> None:
    build_lead_research_sqlite(
        conn,
        review_csv=_FIXTURES / "mini_review.csv",
        blocked_csv=_FIXTURES / "mini_blocked.csv",
        dry_run=False,
    )
    batch_id = int(conn.execute("SELECT id FROM lead_research_batch LIMIT 1").fetchone()[0])
    template = conn.execute(
        "SELECT * FROM lead_research_prospect WHERE prospect_key = 'other-origenlab-cl'"
    ).fetchone()
    cols = [
        d[0]
        for d in conn.execute("SELECT * FROM lead_research_prospect LIMIT 0").description
        if d[0] != "id"
    ]

    def _insert_same_domain(
        prospect_key: str,
        org: str,
        email: str,
        domain: str,
    ) -> None:
        row = dict(zip([d[0] for d in conn.execute("SELECT * FROM lead_research_prospect LIMIT 0").description], template))
        del row["id"]
        row["batch_id"] = batch_id
        row["prospect_key"] = prospect_key
        row["organization_name"] = org
        row["email"] = email
        row["domain"] = domain
        row["classification"] = "same_domain_contacted_review"
        row["status"] = "same_domain_review"
        row["is_blocked"] = 0
        row["source_type"] = "deepsearch"
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO lead_research_prospect ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(row[c] for c in cols),
        )

    conn.execute(
        """
        UPDATE lead_research_prospect
        SET email = ?, organization_name = ?, classification = 'same_domain_contacted_review',
            status = 'same_domain_review', is_blocked = 0, source_type = 'deepsearch',
            domain = 'udec.cl'
        WHERE prospect_key = 'contacto-acme-cl'
        """,
        (GIBA, "Centro EULA"),
    )
    _insert_same_domain("hannelore-valentin-sgs-com", "SGS Chile", HANNELORE, "sgs.com")
    _insert_same_domain(
        "mfbarrar-ug-uchile-cl",
        "UChile Castro",
        MFBARRAR,
        "ug.uchile.cl",
    )
    conn.execute(
        """
        UPDATE lead_research_prospect
        SET email = ?, organization_name = '5M S.A.', domain = '5m.cl'
        WHERE prospect_key = 'other-origenlab-cl'
        """,
        (SAME_DOMAIN_UNTOUCHED,),
    )
    conn.commit()


def _mark_contacted(conn: sqlite3.Connection, email: str, source: str) -> None:
    upsert_outreach_contact_state(
        conn,
        payload=validate_outreach_contact_state_payload(
            contact_email=email,
            state="contacted",
            source=source,
        ),
    )


def _mark_suppressed(conn: sqlite3.Connection, email: str, code: str = "bounce_no_such_user") -> None:
    upsert_contact_email_suppression(
        conn,
        payload=validate_contact_email_suppression_payload(
            email=email,
            suppression_reason_code=code,
            suppression_reason_text=None,
            suppression_source="test",
            last_bounced_at=None,
            updated_by="test",
        ),
    )


@pytest.fixture
def overlay_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_lead_research_tables(conn)
    ensure_outreach_contact_state_table(conn)
    ensure_contact_email_suppression_table(conn)
    _seed_same_domain_prospects(conn)
    _mark_contacted(conn, GIBA, "manual_prospect_outreach_2026_06_01")
    _mark_suppressed(conn, HANNELORE)
    _mark_suppressed(conn, MFBARRAR)
    _mark_contacted(conn, MLE, "cyber_bcc_extra_2026_06_01")
    return conn


def test_giba_contacted_not_same_domain_review(overlay_db: sqlite3.Connection) -> None:
    payload = load_lead_research_mirror_payload(overlay_db)
    giba = next(p for p in payload["prospects"] if p["email"] == GIBA)
    assert giba["classification"] == CLASS_MANUAL_OUTREACH_SENT
    assert giba["status"] == STATUS_MANUAL_CONTACTED
    assert giba["is_blocked"] is False


def test_hannelore_suppressed_blocked(overlay_db: sqlite3.Connection) -> None:
    payload = load_lead_research_mirror_payload(overlay_db)
    row = next(p for p in payload["prospects"] if p["email"] == HANNELORE)
    assert row["classification"] == CLASS_BOUNCED_SUPPRESSED
    assert row["status"] == STATUS_BOUNCED_SUPPRESSED
    assert row["is_blocked"] is True
    codes = [b["reason_code"] for b in payload["block_reasons"] if b["prospect_key"] == row["prospect_key"]]
    assert "bounce_no_such_user" in codes


def test_mfbarrar_hidden_when_blocked_filter(overlay_db: sqlite3.Connection) -> None:
    payload = load_lead_research_mirror_payload(overlay_db)
    visible = [p for p in payload["prospects"] if not p["is_blocked"]]
    assert all(p["email"] != MFBARRAR for p in visible)


def test_mle_contacted_not_bounced(overlay_db: sqlite3.Connection) -> None:
    payload = load_lead_research_mirror_payload(overlay_db)
    mle = next((p for p in payload["prospects"] if p["email"] == MLE), None)
    if mle is None:
        conn = overlay_db
        batch_id = conn.execute("SELECT id FROM lead_research_batch LIMIT 1").fetchone()[0]
        conn.execute(
            """
            INSERT INTO lead_research_prospect (
              batch_id, prospect_key, organization_name, email, domain,
              role_title, sector, region, buyer_type, likely_need, product_angle,
              evidence_url, evidence_note, source, input_priority_score, final_score,
              confidence, classification, spanish_message_angle, risk_flags,
              block_or_review_reason, recommended_next_action, status, campaign_bucket,
              is_blocked, is_active, created_at, source_type, dataset_label
            ) VALUES (
              ?, 'mle', 'MLE Lab', ?, 'mlelab.cl',
              NULL, 'Lab', 'RM', 'laboratorio_privado', NULL, 'equipos',
              NULL, NULL, 'sitio', 0, 70, 'media', 'net_new_safe_review', NULL, '',
              NULL, NULL, 'net_new_safe_review', 'private_lab',
              0, 1, datetime('now'), 'deepsearch', 'test'
            )
            """,
            (batch_id, MLE),
        )
        conn.commit()
        payload = load_lead_research_mirror_payload(conn)
        mle = next(p for p in payload["prospects"] if p["email"] == MLE)
    assert mle["classification"] == CLASS_MANUAL_OUTREACH_SENT
    assert mle["is_blocked"] is False


def test_same_domain_untouched_stays_review(overlay_db: sqlite3.Connection) -> None:
    payload = load_lead_research_mirror_payload(overlay_db)
    row = next(p for p in payload["prospects"] if p["email"] == SAME_DOMAIN_UNTOUCHED)
    assert row["classification"] == "same_domain_contacted_review"


def test_kpi_counts_after_overlay(overlay_db: sqlite3.Connection) -> None:
    payload = load_lead_research_mirror_payload(overlay_db)
    agg = summarize_prospects_for_dashboard(payload["prospects"])
    assert agg["same_domain_review"] >= 1
    assert agg["blocked_count"] >= 2
    assert agg["review_count"] == sum(1 for p in payload["prospects"] if not p["is_blocked"])


def test_gmail_historico_in_kpi_after_source_type_set(overlay_db: sqlite3.Connection) -> None:
    overlay_db.execute(
        """
        UPDATE lead_research_prospect
        SET source_type = 'gmail_historico', classification = 'old_gmail_prospect_review',
            status = 'revision_individual', is_blocked = 0, email = 'hist@example.cl'
        WHERE prospect_key = 'hospital-demo-hospitaldemo-cl'
        """
    )
    overlay_db.commit()
    payload = load_lead_research_mirror_payload(overlay_db)
    agg = summarize_prospects_for_dashboard(payload["prospects"])
    assert agg["gmail_historico"] >= 1


def test_built_block_reason_count_differs_from_raw_sqlite(overlay_db: sqlite3.Connection) -> None:
    """Mirror block_reason rows are rebuilt on overlay; raw SQLite totals are not authoritative."""
    from origenlab_email_pipeline.lead_research.lead_research_builder import (
        sqlite_lead_research_counts,
    )

    raw = sqlite_lead_research_counts(overlay_db)
    built = len(load_lead_research_mirror_payload(overlay_db)["block_reasons"])
    assert built != raw["block_reasons"]


def _insert_empty_email_prospect(
    conn: sqlite3.Connection,
    *,
    prospect_key: str,
    domain: str,
    gmail_sent_count: int = 0,
) -> None:
    batch_id = int(conn.execute("SELECT id FROM lead_research_batch LIMIT 1").fetchone()[0])
    conn.execute(
        """
        INSERT INTO lead_research_prospect (
          batch_id, prospect_key, organization_name, email, domain,
          input_priority_score, final_score, classification, status,
          campaign_bucket, is_blocked, is_active, created_at, source_type, dataset_label,
          gmail_sent_count
        ) VALUES (?, ?, ?, NULL, ?, 0, 70, ?, 'research_needed', 'other', 0, 1, datetime('now'), 'deepsearch', 'test', ?)
        """,
        (batch_id, prospect_key, prospect_key, domain, CLASS_RESEARCH_ONLY, gmail_sent_count),
    )


def test_empty_email_same_domain_contacted_becomes_review(overlay_db: sqlite3.Connection) -> None:
    _insert_empty_email_prospect(overlay_db, prospect_key="inia-empty-email", domain="inia.cl")
    overlay_db.commit()
    _mark_contacted(overlay_db, "oficinadepartesdn@inia.cl", "manual_outreach")
    payload = load_lead_research_mirror_payload(overlay_db)
    row = next(p for p in payload["prospects"] if p["prospect_key"] == "inia-empty-email")
    assert row["classification"] == CLASS_SAME_DOMAIN_CONTACTED_REVIEW
    assert row["status"] == STATUS_SAME_DOMAIN_REVIEW
    assert row["is_blocked"] is False
    assert row["email"] in (None, "")


def test_empty_email_without_evidence_stays_falta(overlay_db: sqlite3.Connection) -> None:
    _insert_empty_email_prospect(
        overlay_db, prospect_key="no-evidence-empty", domain="sinhistorial.cl"
    )
    overlay_db.commit()
    payload = load_lead_research_mirror_payload(overlay_db)
    row = next(p for p in payload["prospects"] if p["prospect_key"] == "no-evidence-empty")
    assert row["classification"] == CLASS_RESEARCH_ONLY


def test_empty_email_domain_suppression_wins(overlay_db: sqlite3.Connection) -> None:
    ensure_contact_domain_suppression_table(overlay_db)
    overlay_db.execute(
        """
        INSERT INTO contact_domain_suppression (domain_norm, suppression_reason_text, updated_at, updated_by)
        VALUES ('blocked-domain.cl', 'test', datetime('now'), 'test')
        """
    )
    _insert_empty_email_prospect(
        overlay_db,
        prospect_key="domain-blocked-empty",
        domain="blocked-domain.cl",
        gmail_sent_count=3,
    )
    _mark_contacted(overlay_db, "someone@blocked-domain.cl", "manual")
    overlay_db.commit()
    payload = load_lead_research_mirror_payload(overlay_db)
    row = next(p for p in payload["prospects"] if p["prospect_key"] == "domain-blocked-empty")
    assert row["classification"] == CLASS_BOUNCED_SUPPRESSED
    assert row["is_blocked"] is True


def test_same_domain_empty_email_not_net_new_safe(overlay_db: sqlite3.Connection) -> None:
    _mark_contacted(overlay_db, GIBA, "test")
    overlay_db.commit()
    indexes = load_operational_indexes_from_sqlite(overlay_db)
    out = apply_operational_overlay_to_prospect(
        {
            "prospect_key": "y",
            "email": None,
            "domain": "udec.cl",
            "classification": CLASS_RESEARCH_ONLY,
            "status": "research_needed",
            "is_blocked": False,
            "gmail_sent_count": 0,
        },
        indexes,
    )
    agg = summarize_prospects_for_dashboard([out])
    assert out["classification"] == CLASS_SAME_DOMAIN_CONTACTED_REVIEW
    assert agg["net_new_safe"] == 0
    assert agg["same_domain_review"] == 1
    assert agg["research_needed"] == 0


def test_suppression_wins_over_contacted() -> None:
    load_operational_indexes_from_sqlite(sqlite3.connect(":memory:"))
    # build indexes manually
    from origenlab_email_pipeline.lead_research.lead_research_operational_overlay import (
        OperationalEmailIndexes,
        OutreachHit,
        SuppressionHit,
    )

    em = "both@example.com"
    indexes = OperationalEmailIndexes(
        suppressions={em: SuppressionHit("bounce_no_such_user")},
        outreach={em: OutreachHit("contacted", "manual")},
    )
    out = apply_operational_overlay_to_prospect(
        {
            "email": em,
            "classification": "same_domain_contacted_review",
            "status": "same_domain_review",
            "is_blocked": False,
        },
        indexes,
    )
    assert out["classification"] == CLASS_BOUNCED_SUPPRESSED
