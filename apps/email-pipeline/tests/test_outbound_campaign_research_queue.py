"""Tests for the fresh-public organization research queue (read-only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.contact_domain_suppression import (
    ContactDomainSuppressionPayload,
    ensure_contact_domain_suppression_table,
    upsert_contact_domain_suppression,
)
from origenlab_email_pipeline.db import connect, init_schema
from origenlab_email_pipeline.leads_schema import ensure_leads_tables_ddl_base
from origenlab_email_pipeline.outbound_campaign_research_queue import compute_research_queue
from origenlab_email_pipeline.supplier_schema import ensure_supplier_tables


def _insert_lead(
    conn: sqlite3.Connection,
    *,
    source_record_id: str,
    org_name: str,
    domain_norm: str = "",
    org_name_norm: str | None = None,
    fit_bucket: str = "high_fit",
    priority_score: float = 5.0,
    lab_context_score: float = 1.0,
    lab_context_tags: str = "laboratorio",
    equipment_match_tags: str = "centrifuga",
    email: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO lead_master (
          source_name, source_record_id, org_name, org_name_norm, domain, domain_norm,
          fit_bucket, priority_score, lab_context_score, lab_context_tags,
          equipment_match_tags, email, email_norm, upstream_sync_state
        ) VALUES ('chilecompra', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
        (
            source_record_id, org_name, org_name_norm or org_name.lower(), domain_norm, domain_norm,
            fit_bucket, priority_score, lab_context_score, lab_context_tags,
            equipment_match_tags, email, (email.lower() if email else None),
        ),
    )
    return int(cur.lastrowid)


@pytest.fixture()
def conn(tmp_path: Path):
    db_path = tmp_path / "t.sqlite"
    c = connect(db_path)
    init_schema(c)
    ensure_leads_tables_ddl_base(c)
    ensure_supplier_tables(c)
    ensure_contact_domain_suppression_table(c)
    c.commit()
    try:
        yield c
    finally:
        c.close()


def test_relevant_org_with_no_contact_is_included(conn: sqlite3.Connection) -> None:
    _insert_lead(conn, source_record_id="1", org_name="UNIVERSIDAD DE PRUEBA", domain_norm="uprueba.cl")
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.leads_scanned == 1
    assert stats.final_queue_count == 1
    assert orgs[0].org_name == "UNIVERSIDAD DE PRUEBA"


def test_low_fit_excluded_by_default(conn: sqlite3.Connection) -> None:
    _insert_lead(conn, source_record_id="1", org_name="LOW FIT ORG", fit_bucket="low_fit")
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 0
    assert stats.blocked_too_low_relevance_leads == 1


def test_no_structured_tag_excluded_even_if_high_fit(conn: sqlite3.Connection) -> None:
    """A high_fit row with no lab_context_tags and no equipment_match_tags carries
    no structured lab-relevance evidence (mirrors a medical-ultrasound tender row
    that only matched on the literal word "ultrasonido")."""
    _insert_lead(
        conn, source_record_id="1", org_name="HOSPITAL ECO", fit_bucket="high_fit",
        lab_context_tags="", equipment_match_tags="",
    )
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 0
    assert stats.blocked_too_low_relevance_leads == 1


def test_org_with_existing_valid_email_is_excluded(conn: sqlite3.Connection) -> None:
    _insert_lead(conn, source_record_id="1", org_name="HAS CONTACT ORG", email="contacto@hascontact.cl")
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 0
    assert stats.blocked_already_has_contact == 1


def test_org_with_researched_contactable_email_is_excluded(conn: sqlite3.Connection) -> None:
    lead_id = _insert_lead(conn, source_record_id="1", org_name="RESEARCHED ORG", domain_norm="researched.cl")
    conn.execute(
        "INSERT INTO lead_contact_research (lead_id, contact_research_status, resolved_contact_email, updated_at) "
        "VALUES (?, 'contacto_encontrado', 'jefe@researched.cl', 't')",
        (lead_id,),
    )
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 0
    assert stats.blocked_already_has_contact == 1


def test_supplier_domain_is_excluded(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO supplier_master (domain_norm, trade_name, created_at, updated_at) "
        "VALUES ('kalstein.cl', 'Kalstein', 't', 't')"
    )
    _insert_lead(conn, source_record_id="1", org_name="KALSTEIN CHILE", domain_norm="kalstein.cl")
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 0
    assert stats.blocked_supplier == 1


def test_suppressed_domain_is_excluded(conn: sqlite3.Connection) -> None:
    upsert_contact_domain_suppression(
        conn,
        payload=ContactDomainSuppressionPayload(
            domain_norm="suppressed.cl", suppression_reason_text="test", updated_by="test",
        ),
    )
    _insert_lead(conn, source_record_id="1", org_name="SUPPRESSED ORG", domain_norm="suppressed.cl")
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 0
    assert stats.blocked_suppression == 1


def test_discarded_research_status_excluded_by_default(conn: sqlite3.Connection) -> None:
    lead_id = _insert_lead(conn, source_record_id="1", org_name="DISCARDED ORG", domain_norm="discarded.cl")
    conn.execute(
        "INSERT INTO lead_contact_research (lead_id, contact_research_status, updated_at) "
        "VALUES (?, 'descartado', 't')",
        (lead_id,),
    )
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 0
    assert stats.blocked_discarded == 1

    orgs2, stats2 = compute_research_queue(conn, include_discarded=True)
    assert stats2.final_queue_count == 1


def test_multiple_tender_rows_for_same_org_collapse_to_one(conn: sqlite3.Connection) -> None:
    _insert_lead(
        conn, source_record_id="1", org_name="MULTI TENDER ORG", domain_norm="multi.cl",
        priority_score=5.0, equipment_match_tags="centrifuga",
    )
    _insert_lead(
        conn, source_record_id="2", org_name="MULTI TENDER ORG", domain_norm="multi.cl",
        priority_score=8.0, equipment_match_tags="autoclave",
    )
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert stats.final_queue_count == 1
    assert orgs[0].priority_score == 8.0
    assert set(orgs[0].equipment_match_tags.split(",")) == {"centrifuga", "autoclave"}
    assert len(orgs[0].lead_ids) == 2


def test_ranking_prefers_high_fit_then_priority_score(conn: sqlite3.Connection) -> None:
    _insert_lead(conn, source_record_id="1", org_name="MEDIUM ORG", domain_norm="medium.cl",
                 fit_bucket="medium_fit", priority_score=9.0)
    _insert_lead(conn, source_record_id="2", org_name="HIGH ORG", domain_norm="high.cl",
                 fit_bucket="high_fit", priority_score=1.0)
    conn.commit()
    orgs, stats = compute_research_queue(conn)
    assert [o.org_name for o in orgs] == ["HIGH ORG", "MEDIUM ORG"]


def test_limit_caps_output(conn: sqlite3.Connection) -> None:
    for i in range(5):
        _insert_lead(conn, source_record_id=str(i), org_name=f"ORG {i}", domain_norm=f"org{i}.cl")
    conn.commit()
    orgs, stats = compute_research_queue(conn, limit=2)
    assert len(orgs) == 2
    assert stats.orgs_scanned == 5
    assert stats.final_queue_count == 2
