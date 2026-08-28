"""Phase 6F / 8C taxonomy unit tests for plan_source_quality.classify_vertical (path-only)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "plan_source_quality.py"


def _classify(rel: str) -> str:
    name = "plan_source_quality_taxonomy_pytest"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.classify_vertical(rel)


def test_phase6f_src_verticals() -> None:
    assert _classify("src/origenlab_email_pipeline/campaigns/cyber_outreach_campaign.py") == "campaigns"
    assert _classify("src/origenlab_email_pipeline/qa/outreach_volume_rollup.py") == "qa"
    assert _classify("src/origenlab_email_pipeline/postgres_dashboard_api/schemas.py") == "postgres_api"
    assert _classify("src/origenlab_email_pipeline/catalog/catalog_builder.py") == "catalog"
    assert _classify("src/origenlab_email_pipeline/core/mart/build_runner.py") == "mart"
    assert _classify("src/origenlab_email_pipeline/business_mart.py") == "mart"
    assert _classify("src/origenlab_email_pipeline/validation/attachment_validation.py") == "validation"
    assert _classify("src/origenlab_email_pipeline/ingest/gmail_imap.py") == "ingest"


def test_phase6f_warm_cases_and_ndr() -> None:
    assert _classify("src/origenlab_email_pipeline/warm_case_sender_rules.py") == "warm_cases"
    assert _classify("src/origenlab_email_pipeline/ndr_contacto_scan.py") == "ndr"
    assert _classify("src/origenlab_email_pipeline/reported_non_delivery_contacto_scan.py") == "ndr"


def test_phase6f_script_verticals() -> None:
    assert _classify("scripts/qa/plan_source_quality.py") == "planners"
    assert _classify("scripts/qa/plan_script_consolidation.py") == "planners"
    assert _classify("scripts/tools/check_system.py") == "tooling"
    assert _classify("scripts/tools/inspect_sqlite.py") == "tooling"
    assert _classify("scripts/qa/verify_dashboard_postgres_mirror.py") == "postgres_verify"
    assert _classify("scripts/sync/sync_lead_research_postgres_mirror.py") == "postgres_mirror"
    assert _classify("scripts/migrate/sqlite_archive_to_postgres.py") == "postgres_mirror"
    assert _classify("scripts/ingest/04_imap_to_sqlite.py") == "ingest"
    assert _classify("scripts/mart/build_business_mart.py") == "mart"
    assert _classify("scripts/qa/build_ndr_review_queue.py") == "ndr"


def test_phase8c_operator_cli() -> None:
    assert _classify("src/origenlab_email_pipeline/cli.py") == "operator_cli"
    assert _classify("src/origenlab_email_pipeline/operator_cli/parser.py") == "operator_cli"
    assert _classify("src/origenlab_email_pipeline/operator_status_report.py") == "operator_cli"


def test_phase8c_postgres_mirror() -> None:
    assert _classify("src/origenlab_email_pipeline/mart_core_postgres_migrate.py") == "postgres_mirror"
    assert _classify("src/origenlab_email_pipeline/dashboard_postgres_sync.py") == "postgres_mirror"
    assert _classify("src/origenlab_email_pipeline/catalog/catalog_postgres_mirror.py") == "postgres_mirror"
    assert _classify("scripts/sync/sync_dashboard_postgres_mirror.py") == "postgres_mirror"


def test_phase8c_equipment_first() -> None:
    assert _classify("src/origenlab_email_pipeline/equipment_first_operator_queue.py") == "equipment_first"
    assert _classify("src/origenlab_email_pipeline/equipment_opportunity_mirror.py") == "equipment_first"
    assert _classify("scripts/sync/load_equipment_opportunity_mirror.py") == "equipment_first"


def test_phase8c_core_infrastructure() -> None:
    assert _classify("src/origenlab_email_pipeline/db.py") == "core_infrastructure"
    assert _classify("src/origenlab_email_pipeline/parse_mbox.py") == "core_infrastructure"
    assert _classify("src/origenlab_email_pipeline/core/reports_out.py") == "core_infrastructure"


def test_phase8c_qa_exports() -> None:
    assert _classify("scripts/qa/export_do_not_repeat_master.py") == "qa_exports"
    assert _classify("scripts/qa/validate_campaign_csvs.py") == "qa_exports"
    assert _classify("scripts/qa/audit_canonical_contacto_gmail.py") == "qa_exports"


def test_phase8c_campaign_scripts() -> None:
    assert _classify("scripts/qa/build_cyber_outreach_campaign.py") == "campaign_scripts"
    assert _classify("scripts/qa/build_presentacion_origenlab_quality.py") == "campaign_scripts"


def test_phase8c_research_lab() -> None:
    assert _classify("scripts/research/run_deep_research_prospecting.py") == "research_lab"
    assert _classify("src/origenlab_email_pipeline/core/research_automation.py") == "research_lab"
    assert _classify("scripts/qa/verify_research_candidate_evidence.py") == "research_lab"


def test_phase8c_draft_review_helpers_is_tatiana_lab() -> None:
    assert _classify("src/origenlab_email_pipeline/tatiana_copilot/draft_review_helpers.py") == "tatiana_lab"


def test_phase8c_streamlit_ui_modules_removed() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "apps/business_mart_app.py",
        "src/origenlab_email_pipeline/streamlit_prioridad_pages.py",
        "src/origenlab_email_pipeline/streamlit_prioridad_handoffs.py",
        "src/origenlab_email_pipeline/streamlit_page_status.py",
    ):
        assert not (root / rel).is_file(), rel


def test_phase8c_purge_break_glass() -> None:
    assert _classify("scripts/tools/purge_contact_emails_from_sqlite.py") == "purge_break_glass"
    assert _classify("scripts/tools/archive_reports_out_generated.py") == "purge_break_glass"
