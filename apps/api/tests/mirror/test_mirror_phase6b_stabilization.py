"""API-3 Phase 6B: post-removal stabilization policy checks."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from origenlab_api.main import create_app

from parity_routes import REQUIRED_MIRROR_OPENAPI_PATHS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_LEGACY_ROOT = _REPO_ROOT / "apps/email-pipeline/src/origenlab_api"
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


def test_strict_phase6_grep_gate_passes_phase6b() -> None:
    proc = subprocess.run(
        ["bash", str(_GATE_SCRIPT)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "Unallowlisted hits: 0" in proc.stdout
