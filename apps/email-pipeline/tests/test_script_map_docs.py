"""Guardrails for canonical SCRIPT_MAP.md operator classification (docs-only)."""

from __future__ import annotations

import re
from pathlib import Path

from origenlab_email_pipeline.operator_cli.constants import SUBCOMMAND_SCRIPTS

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT_MAP = _REPO / "docs" / "SCRIPT_MAP.md"
_OPERATOR_SURFACE = _REPO / "docs" / "OPERATOR_COMMAND_SURFACE.md"

_CLASSIFICATION_HEADER = "Canonical classification table"
_CATEGORY_TERMS = (
    "active_operator_command",
    "read_only_qa_report",
    "import_ingest",
    "write_apply_send_purge_dangerous",
    "break_glass_manual",
    "superseded_by_origenlab",
    "parked_legacy",
)

_DANGER_CAUTION_TERMS = (
    "break-glass",
    "break_glass",
    "--apply",
    "purge",
    "send",
    "Postgres",
    "Gmail",
)

_STALE_STREAMLIT_LAUNCH = re.compile(
    r"uv run --group ui streamlit run apps/business_mart_app\.py",
    re.I,
)


def test_script_map_md_exists() -> None:
    assert _SCRIPT_MAP.is_file(), f"missing canonical map: {_SCRIPT_MAP}"


def test_script_map_has_classification_table_section() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    assert _CLASSIFICATION_HEADER in text
    assert "script path | category | entrypoint" in text.replace("`", "")
    for term in _CATEGORY_TERMS:
        assert term in text, f"SCRIPT_MAP must document category {term!r}"


def test_script_map_recommends_origenlab_cli() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    assert "uv run origenlab" in text
    assert "superseded_by_origenlab" in text
    assert "operator_cli/constants.py" in text or "origenlab status" in text
    surface = _OPERATOR_SURFACE.read_text(encoding="utf-8")
    assert "uv run origenlab" in surface


def test_script_map_documents_subcommand_script_paths() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    missing: list[str] = []
    for subcommand, rel_path in SUBCOMMAND_SCRIPTS.items():
        if rel_path not in text:
            missing.append(f"{subcommand} -> {rel_path}")
    assert not missing, "SCRIPT_MAP must list origenlab fallback script paths:\n" + "\n".join(missing)


def test_script_map_dangerous_operations_have_caution_wording() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    lower = text.lower()
    for term in _DANGER_CAUTION_TERMS:
        assert term.lower() in lower or term in text, (
            f"SCRIPT_MAP must mention {term!r} with operator caution context"
        )
    assert "real mail" in lower or "sends real email" in lower or "gmail send" in lower
    assert "dry-run" in lower or "dry run" in lower
    assert "not send approval" in lower or "not send-ready" in lower or "≠ send" in text


def test_script_map_postgres_and_gmail_are_optional_or_cautioned() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    assert "Postgres" in text
    assert "Gmail" in text
    assert (
        "EXPERIMENTAL_PARKED" in text
        or "optional" in text.lower()
        or "parked" in text.lower()
        or "mirror" in text.lower()
    )
    assert "SQLite" in text and "operational" in text.lower() or "source of truth" in text.lower()


def test_script_map_streamlit_not_listed_as_active_operator_path() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    assert _STALE_STREAMLIT_LAUNCH.search(text) is None
    assert "streamlit run" not in text.lower()
    assert "business_mart_app.py" in text
    assert "apps/dashboard" in text
    assert "removed" in text.lower() or "retired" in text.lower()


def test_script_map_points_to_read_only_planners() -> None:
    text = _SCRIPT_MAP.read_text(encoding="utf-8")
    for script in (
        "plan_script_consolidation.py",
        "plan_reports_out_cleanup.py",
        "plan_source_quality.py",
        "plan_function_surface.py",
        "plan_import_surface.py",
    ):
        assert script in text
