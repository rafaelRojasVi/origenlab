"""API-3 Phase 6: legacy email-pipeline :8000 API removed."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from origenlab_api.main import create_app

from parity_routes import REQUIRED_MIRROR_OPENAPI_PATHS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_LEGACY_ROOT = _REPO_ROOT / "apps/email-pipeline/src/origenlab_api"
_PHASE6_DOC = _REPO_ROOT / "apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md"
_GATE_SCRIPT = _REPO_ROOT / "apps/api/scripts/api3_phase6_grep_gate.sh"
_OPERATOR_CLIENT = _REPO_ROOT / "apps/dashboard/src/api/operatorClient.ts"
_DASHBOARD_PKG = _REPO_ROOT / "apps/dashboard/package.json"
_LEGACY_SMOKE = _REPO_ROOT / "apps/dashboard/scripts/legacy-smoke.mjs"


def test_phase6_legacy_removal_doc_exists() -> None:
    assert _PHASE6_DOC.is_file()
    text = _PHASE6_DOC.read_text(encoding="utf-8")
    assert "Phase 6" in text
    assert "origenlab_api" in text
    assert "smoke:legacy" in text


def test_legacy_email_pipeline_tree_removed() -> None:
    assert not _LEGACY_ROOT.exists()


def test_legacy_api_tests_removed() -> None:
    tests = _REPO_ROOT / "apps/email-pipeline/tests"
    for name in (
        "test_api_slice1.py",
        "test_api_classification.py",
        "test_api_commercial_purchase_events.py",
        "test_api_meta.py",
        "test_api_cors.py",
        "test_api_deprecation.py",
    ):
        assert not (tests / name).is_file(), name


def test_smoke_legacy_and_script_removed() -> None:
    assert not _LEGACY_SMOKE.is_file()
    pkg = _DASHBOARD_PKG.read_text(encoding="utf-8")
    assert "smoke:legacy" not in pkg
    assert '"smoke:mirror"' in pkg


def test_active_dashboard_unchanged_phase6() -> None:
    text = _OPERATOR_CLIENT.read_text(encoding="utf-8")
    assert "/mirror/" not in text
    assert not re.search(r"127\.0\.0\.1:8000|localhost:8000", text)


def test_mirror_routes_still_registered_phase6() -> None:
    paths = set(create_app().openapi()["paths"])
    for required in REQUIRED_MIRROR_OPENAPI_PATHS:
        assert required in paths


def test_mirror_routes_remain_get_only_phase6() -> None:
    unsafe: list[str] = []
    for route in create_app().routes:
        path = getattr(route, "path", "") or ""
        if not path.startswith("/mirror"):
            continue
        methods = getattr(route, "methods", None)
        if methods and methods - {"GET", "HEAD", "OPTIONS"}:
            unsafe.append(f"{path} {sorted(methods)}")
    assert unsafe == []
