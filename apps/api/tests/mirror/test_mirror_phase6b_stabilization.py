"""API-3 Phase 6B: post-removal stabilization policy checks."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from origenlab_api.main import create_app

from parity_routes import REQUIRED_MIRROR_OPENAPI_PATHS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_LEGACY_ROOT = _REPO_ROOT / "apps/email-pipeline/src/origenlab_api"
_PHASE6B_DOC = _REPO_ROOT / "apps/api/docs/API-3_PHASE6B_STABILIZATION.md"
_ARCHIVE_INDEX = _REPO_ROOT / "apps/api/docs/archive/api3/README.md"
_GATE_SCRIPT = _REPO_ROOT / "apps/api/scripts/api3_phase6_grep_gate.sh"
_OPERATOR_CLIENT = _REPO_ROOT / "apps/dashboard/src/api/operatorClient.ts"
_DASHBOARD_PKG = _REPO_ROOT / "apps/dashboard/package.json"
_PROJECT_CONTEXT = _REPO_ROOT / "docs/PROJECT_CONTEXT.md"

_ACTIVE_OPERATOR_DOCS = (
    _REPO_ROOT / "apps/email-pipeline/docs/RUNBOOK.md",
    _REPO_ROOT / "apps/dashboard/docs/V1_FREEZE_OPERATOR_HANDOFF.md",
    _REPO_ROOT / "apps/dashboard/README.md",
    _PROJECT_CONTEXT,
    _REPO_ROOT / "apps/api/README.md",
)

_LIVE_LEGACY_RUN_PATTERNS = (
    re.compile(r"uv\s+run\s+uvicorn\b[^\n]*:8000", re.I),
    re.compile(r"curl\b[^\n]*127\.0\.0\.1:8000", re.I),
    re.compile(r"curl\b[^\n]*localhost:8000", re.I),
    re.compile(r"--port\s+8000\b"),
    re.compile(r"smoke:legacy"),
)


def test_phase6b_stabilization_doc_exists() -> None:
    assert _PHASE6B_DOC.is_file()
    text = _PHASE6B_DOC.read_text(encoding="utf-8")
    assert "apps/api" in text
    assert "fastapi package" in text.lower()
    assert "/operator/" in text or "dashboard routes" in text.lower()


def test_api3_archive_index_exists() -> None:
    assert _ARCHIVE_INDEX.is_file()
    text = _ARCHIVE_INDEX.read_text(encoding="utf-8")
    assert "historical" in text.lower()
    assert "API-3_PHASE2_PARITY_CHECKLIST.md" in text


def test_legacy_email_pipeline_tree_must_not_exist() -> None:
    assert not _LEGACY_ROOT.exists()


def test_package_json_must_not_expose_smoke_legacy() -> None:
    assert "smoke:legacy" not in _DASHBOARD_PKG.read_text(encoding="utf-8")


def test_mirror_routes_still_exist_phase6b() -> None:
    paths = set(create_app().openapi()["paths"])
    for required in REQUIRED_MIRROR_OPENAPI_PATHS:
        assert required in paths


def test_dashboard_today_must_not_call_mirror_phase6b() -> None:
    text = _OPERATOR_CLIENT.read_text(encoding="utf-8")
    assert "/mirror/" not in text


def test_active_operator_docs_have_no_live_legacy_run_instructions() -> None:
    hits: list[str] = []
    for path in _ACTIVE_OPERATOR_DOCS:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pat in _LIVE_LEGACY_RUN_PATTERNS:
                if pat.search(line):
                    hits.append(f"{path.relative_to(_REPO_ROOT)}:{line_no}: {line.strip()[:80]}")
    assert hits == [], "live legacy :8000 run instructions in operator docs:\n" + "\n".join(hits)
