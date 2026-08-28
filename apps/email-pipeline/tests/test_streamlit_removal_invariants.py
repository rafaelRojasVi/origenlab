"""Runtime guardrails for the Streamlit retirement (2026-06-04).

Restored from the deleted docs-guardrail file `test_active_stack_docs.py`
(commercial platform reset, 2026-08): that file bundled these structural/
filesystem invariants together with assertions on
`docs/audits/ACTIVE_STACK_AND_STREAMLIT_RETIREMENT_PLAN_20260604.md` and
`docs/audits/STREAMLIT_LAUNCH_SURFACE_REMOVAL_PLAN_20260604.md`, both of
which were deleted as historical narrative. Deleting the whole file would
have silently dropped these runtime checks too; only the doc-content
assertions are gone here — the invariants that don't depend on a
Markdown file are preserved.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_REMOVED_UI_IMPORT = re.compile(
    r"^\s*(?:from\s+origenlab_email_pipeline\.(?:"
    r"streamlit_prioridad_pages|streamlit_prioridad_handoffs|streamlit_page_status"
    r")\b|import\s+origenlab_email_pipeline\.(?:"
    r"streamlit_prioridad_pages|streamlit_prioridad_handoffs|streamlit_page_status"
    r")\b)",
)

_STALE_STREAMLIT_LAUNCH = re.compile(
    r"uv run --group ui streamlit run apps/business_mart_app\.py",
    re.I,
)

# Active docs must not instruct operators to launch the removed UI.
_ACTIVE_DOC_PATHS: tuple[Path, ...] = (
    _REPO / "README.md",
    _REPO / "AGENTS.md",
    _REPO / "docs" / "RUNBOOK.md",
    _REPO / "docs" / "APP_CONTEXT.md",
    _REPO / "docs" / "OUTBOUND_SOURCE_OF_TRUTH.md",
    _REPO / "docs" / "OPERATOR_COMMAND_SURFACE.md",
)

# Phrases that imply Streamlit is the current operator UI (retirement notices are OK).
_STREAMLIT_AS_ACTIVE_UI = re.compile(
    r"(?i)(?:"
    r"active operator ui[^\n]{0,80}streamlit(?!.*\bremoved\b)"
    r"|operator ui \(active\)[^\n]{0,40}streamlit"
    r"|use streamlit (?:for|as|to run)"
    r"|streamlit run apps/business_mart_app"
    r")",
)


def test_streamlit_lan_launcher_removed() -> None:
    assert not (_REPO / "scripts" / "tools" / "run_streamlit_lan.sh").exists()


def test_streamlit_docker_removed() -> None:
    assert not (_REPO / "Dockerfile").exists()
    assert not (_REPO / "docker-compose.yml").exists()
    assert (_REPO / "docker-compose.dashboard-postgres.yml").is_file()


def test_streamlit_python_ui_modules_removed() -> None:
    for rel in (
        "apps/business_mart_app.py",
        "src/origenlab_email_pipeline/streamlit_prioridad_pages.py",
        "src/origenlab_email_pipeline/streamlit_prioridad_handoffs.py",
        "src/origenlab_email_pipeline/streamlit_page_status.py",
    ):
        assert not (_REPO / rel).exists(), f"removed Streamlit UI module resurrected: {rel}"


def test_draft_review_helpers_module_renamed() -> None:
    path = _REPO / "src" / "origenlab_email_pipeline" / "tatiana_copilot" / "draft_review_helpers.py"
    assert path.is_file()
    assert not (
        _REPO / "src" / "origenlab_email_pipeline" / "tatiana_copilot" / "streamlit_draft_helpers.py"
    ).exists()


def test_no_active_python_imports_removed_streamlit_ui_modules() -> None:
    roots = (_REPO / "src", _REPO / "tests", _REPO / "scripts")
    violations: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if path.name.startswith("."):
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if line.strip().startswith("#"):
                    continue
                if _REMOVED_UI_IMPORT.search(line):
                    violations.append(f"{path.relative_to(_REPO)}:{i}:{line.strip()}")
                if "business_mart_app" in line and (
                    "import" in line or "from " in line
                ):
                    violations.append(f"{path.relative_to(_REPO)}:{i}:{line.strip()}")
    assert not violations, "removed Streamlit UI modules must not be imported:\n" + "\n".join(violations)


def _iter_active_markdown_docs() -> list[Path]:
    paths = list(_ACTIVE_DOC_PATHS)
    for sub in ("docs/pipeline", "docs/leads", "docs/ingest"):
        d = _REPO / sub
        if d.is_dir():
            paths.extend(sorted(d.glob("*.md")))
    return [p for p in paths if p.is_file()]


def test_active_docs_no_stale_streamlit_launch_command() -> None:
    violations: list[str] = []
    for path in _iter_active_markdown_docs():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _STALE_STREAMLIT_LAUNCH.search(line):
                violations.append(f"{path.relative_to(_REPO)}:{i}:{line.strip()}")
    assert not violations, "active docs must not contain stale Streamlit launch:\n" + "\n".join(
        violations
    )


def test_active_docs_do_not_present_streamlit_as_current_ui() -> None:
    violations: list[str] = []
    for path in _iter_active_markdown_docs():
        rel = path.relative_to(_REPO)
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _STREAMLIT_AS_ACTIVE_UI.search(line):
                violations.append(f"{rel}:{i}:{line.strip()}")
    assert not violations, "active docs must not present Streamlit as current UI:\n" + "\n".join(
        violations
    )
