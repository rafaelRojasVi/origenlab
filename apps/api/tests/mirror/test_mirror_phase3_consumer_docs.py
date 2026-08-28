"""API-3 Phase 3A/6: non-Dashboard consumers use :8001 /mirror routes."""

from __future__ import annotations

from pathlib import Path

from parity_routes import LEGACY_TO_MIRROR_ROUTE_PAIRS

_REPO_ROOT = Path(__file__).resolve().parents[4]
_RUNBOOK = _REPO_ROOT / "apps" / "email-pipeline" / "docs" / "RUNBOOK.md"
_SYNC_PY = _REPO_ROOT / "apps" / "email-pipeline" / "src" / "origenlab_email_pipeline" / "dashboard_postgres_sync.py"
_ENV_EXAMPLE = _REPO_ROOT / "apps" / "email-pipeline" / ".env.example"
_LEGACY_API_ROOT = _REPO_ROOT / "apps" / "email-pipeline" / "src" / "origenlab_api"
_MIRROR_SMOKE = _REPO_ROOT / "apps" / "dashboard" / "scripts" / "mirror-smoke.mjs"
_DASHBOARD_PKG = _REPO_ROOT / "apps" / "dashboard" / "package.json"
_DASHBOARD_ACTIVE = (
    _REPO_ROOT / "apps" / "dashboard" / "src" / "api" / "operatorClient.ts",
    _REPO_ROOT / "apps" / "dashboard" / "src" / "api" / "mirrorCommercialClient.ts",
    _REPO_ROOT / "apps" / "dashboard" / "src" / "pages" / "DashboardApp.tsx",
)
_COMMERCIAL_DEALS_MIRROR_LIST = "/mirror/commercial/deals"
_FORBIDDEN_DASHBOARD_MIRROR_PATHS = (
    "/mirror/commercial/purchase-events",
    "/mirror/commercial/deals/{deal_key}",
    "/mirror/commercial/deals/",
)


def test_runbook_documents_mirror_api_on_8001_only() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "http://127.0.0.1:8001/mirror/dashboard/summary" in text
    assert "Phase 6" in text
    assert "127.0.0.1:8000/dashboard/summary" not in text


def test_runbook_documents_all_mirror_route_prefixes() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    for _legacy, mirror, _note in LEGACY_TO_MIRROR_ROUTE_PAIRS:
        assert mirror in text, f"RUNBOOK missing mirror path {mirror}"


def test_sync_post_apply_hints_mirror_only() -> None:
    text = _SYNC_PY.read_text(encoding="utf-8")
    assert "8001/mirror/dashboard/summary" in text
    assert "8000/dashboard/summary" not in text


def test_env_example_defaults_document_8001_mirror() -> None:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "127.0.0.1:8001" in text
    assert "/mirror/*" in text or "mirror" in text.lower()


def test_mirror_smoke_script_and_npm_command_exist() -> None:
    assert _MIRROR_SMOKE.is_file()
    smoke_text = _MIRROR_SMOKE.read_text(encoding="utf-8")
    assert "/mirror/health/dependencies" in smoke_text
    assert "/mirror/dashboard/summary" in smoke_text
    pkg = _DASHBOARD_PKG.read_text(encoding="utf-8")
    assert '"smoke:mirror"' in pkg
    assert '"smoke:legacy"' not in pkg


def test_legacy_api_tree_removed_phase6() -> None:
    assert not _LEGACY_API_ROOT.exists()


def test_active_dashboard_mirror_limited_to_commercial_deals_list() -> None:
    for path in _DASHBOARD_ACTIVE:
        text = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_DASHBOARD_MIRROR_PATHS:
            assert forbidden not in text, f"{path.name} must not reference {forbidden}"
        if "/mirror/" in text:
            assert _COMMERCIAL_DEALS_MIRROR_LIST in text
