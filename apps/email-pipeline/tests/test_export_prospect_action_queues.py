"""Tests for Prospectos commercial action queue CSV export."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from origenlab_email_pipeline.lead_research.lead_research_builder import build_lead_research_sqlite
from origenlab_email_pipeline.lead_research.lead_research_mirror_read_model import (
    load_lead_research_mirror_payload,
)
from origenlab_email_pipeline.lead_research.prospect_action_queue_export import (
    EXPORT_FIELDNAMES,
    SUMMARY_FILENAME,
    export_prospect_action_queues,
    export_prospect_action_queues_from_prospects,
    partition_prospect_queues,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "lead_research"


def _build_fixture_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    build_lead_research_sqlite(
        conn,
        review_csv=_FIXTURES / "mini_review.csv",
        blocked_csv=_FIXTURES / "mini_blocked.csv",
        batch_key="export_test",
        dry_run=False,
    )
    return conn


def test_partition_queues_by_commercial_bucket() -> None:
    conn = _build_fixture_conn()
    try:
        prospects = load_lead_research_mirror_payload(conn)["prospects"]
    finally:
        conn.close()

    partitioned = partition_prospect_queues(prospects)
    assert partitioned["ready_to_contact.csv"]
    assert partitioned["needs_email_enrichment.csv"]
    assert partitioned["tender_opportunities.csv"]
    assert partitioned["review_history.csv"]

    ready = partitioned["ready_to_contact.csv"]
    assert all(row["commercial_action_bucket"] == "ready_to_contact" for row in ready)
    assert any(row["organization_name"] == "Acme Labs" for row in ready)

    tender = partitioned["tender_opportunities.csv"]
    assert len(tender) == 1
    assert tender[0]["organization_name"] == "Hospital Demo"

    research = partitioned["needs_email_enrichment.csv"]
    assert len(research) == 1
    assert research[0]["organization_name"] == "No Email Org"

    review = partitioned["review_history.csv"]
    assert any(row["organization_name"] == "Old Domain Co" for row in review)

    followup = partitioned["already_contacted_followup_review.csv"]
    assert all(row["commercial_action_bucket"] == "already_contacted" for row in followup)


def test_export_writes_csvs_and_summary(tmp_path: Path) -> None:
    conn = _build_fixture_conn()
    try:
        summary = export_prospect_action_queues(conn, output_dir=tmp_path, dry_run=False)
    finally:
        conn.close()

    assert summary["read_only"] is True
    assert summary["total_active_prospects"] == 5
    assert summary["exports"]["ready_to_contact.csv"] >= 1
    assert (tmp_path / "ready_to_contact.csv").is_file()
    assert (tmp_path / SUMMARY_FILENAME).is_file()

    saved = json.loads((tmp_path / SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert saved["exports"] == summary["exports"]

    ready_text = (tmp_path / "ready_to_contact.csv").read_text(encoding="utf-8")
    header = ",".join(EXPORT_FIELDNAMES)
    assert ready_text.startswith(header)


def test_dry_run_does_not_write_files(tmp_path: Path) -> None:
    conn = _build_fixture_conn()
    try:
        summary = export_prospect_action_queues(conn, output_dir=tmp_path, dry_run=True)
    finally:
        conn.close()

    assert summary["total_active_prospects"] == 5
    assert not (tmp_path / "ready_to_contact.csv").exists()
    assert not (tmp_path / SUMMARY_FILENAME).exists()


def test_followup_queue_uses_manual_outreach_rows() -> None:
    prospects = [
        {
            "organization_name": "Manual Outreach Co",
            "contact_name": "Ops",
            "email": "ops@example.cl",
            "domain": "example.cl",
            "sector": "Lab",
            "region": "RM",
            "final_score": 80,
            "classification": "manual_outreach_sent",
            "status": "manual_outreach_contacted",
            "is_blocked": False,
            "gmail_sent_count": 0,
            "gmail_received_count": 0,
            "recommended_next_action": "Esperar respuesta; no reenviar ahora",
            "evidence_url": None,
            "product_angle": "centrífugas",
        }
    ]
    summary = export_prospect_action_queues_from_prospects(
        prospects,
        output_dir=Path("/tmp/unused"),
        dry_run=True,
    )
    assert summary["exports"]["already_contacted_followup_review.csv"] == 1
