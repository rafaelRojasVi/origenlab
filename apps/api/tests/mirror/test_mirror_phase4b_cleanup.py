"""API-3 Phase 4B/6: grep gate and mirror policy after legacy removal."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from origenlab_api.main import create_app

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CURRENT_SYSTEM_TRUTH = _REPO_ROOT / "docs/architecture/CURRENT_SYSTEM_TRUTH.md"
_LEGACY_DASHBOARD_ROOT = _REPO_ROOT / "apps/dashboard/src/legacy"
_LEGACY_ROOT = _REPO_ROOT / "apps/email-pipeline/src/origenlab_api"
_GATE_SCRIPT = _REPO_ROOT / "apps/api/scripts/api3_phase6_grep_gate.sh"
_OPERATOR_CLIENT = _REPO_ROOT / "apps/dashboard/src/api/operatorClient.ts"
_PHASE6_DOC = _REPO_ROOT / "apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md"


def test_phase6_legacy_removal_doc_exists() -> None:
    assert _PHASE6_DOC.is_file()


def test_current_system_truth_notes_legacy_removed() -> None:
    text = _CURRENT_SYSTEM_TRUTH.read_text(encoding="utf-8")
    assert "8001" in text
    assert "/mirror/" in text
    assert "Phase 6" in text or "removed" in text.lower() or "no FastAPI" in text


def test_parked_legacy_dashboard_removed() -> None:
    assert not _LEGACY_DASHBOARD_ROOT.exists()


def test_strict_phase6_grep_gate_passes() -> None:
    proc = subprocess.run(
        ["bash", str(_GATE_SCRIPT)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_legacy_tree_removed() -> None:
    assert not _LEGACY_ROOT.exists()


def test_active_dashboard_no_mirror_or_legacy_port() -> None:
    text = _OPERATOR_CLIENT.read_text(encoding="utf-8")
    assert "/mirror/" not in text
    assert not re.search(r"127\.0\.0\.1:8000|localhost:8000", text)


def test_mirror_routes_remain_get_only() -> None:
    unsafe: list[str] = []
    for route in create_app().routes:
        path = getattr(route, "path", "") or ""
        if not path.startswith("/mirror"):
            continue
        methods = getattr(route, "methods", None)
        if methods and methods - {"GET", "HEAD", "OPTIONS"}:
            unsafe.append(f"{path} {sorted(methods)}")
    assert unsafe == []
