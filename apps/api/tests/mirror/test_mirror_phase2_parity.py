"""API-3 Phase 2: mirror parity checklist enforcement."""

from __future__ import annotations

from pathlib import Path

from origenlab_api.main import create_app

from parity_routes import (
    LEGACY_HEALTH_NO_MIRROR_ALIAS,
    LEGACY_TO_MIRROR_ROUTE_PAIRS,
    OPERATOR_TODAY_PATHS,
    REQUIRED_MIRROR_OPENAPI_PATHS,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_LEGACY_API_ROOT = _REPO_ROOT / "apps" / "email-pipeline" / "src" / "origenlab_api"
_DASHBOARD_ACTIVE_SOURCES = (
    _REPO_ROOT / "apps" / "dashboard" / "src" / "api" / "operatorClient.ts",
    _REPO_ROOT / "apps" / "dashboard" / "src" / "api" / "mirrorCommercialClient.ts",
    _REPO_ROOT / "apps" / "dashboard" / "src" / "pages" / "DashboardApp.tsx",
    _REPO_ROOT / "apps" / "dashboard" / "src" / "App.tsx",
    _REPO_ROOT / "apps" / "dashboard" / "vite.config.ts",
)
_COMMERCIAL_DEALS_MIRROR_LIST = "/mirror/commercial/deals"
_FORBIDDEN_DASHBOARD_MIRROR_PATHS = (
    "/mirror/commercial/purchase-events",
    "/mirror/commercial/deals/{deal_key}",
    "/mirror/commercial/deals/",
    "/mirror/dashboard",
    "/mirror/contacts",
    "/mirror/organizations",
    "/mirror/classification",
    "/mirror/outbound",
    "/mirror/meta",
    "/mirror/health",
)


def test_legacy_email_pipeline_tree_removed_phase6() -> None:
    assert not _LEGACY_API_ROOT.exists()


def test_documented_mirror_routes_exist_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    missing = [p for p in REQUIRED_MIRROR_OPENAPI_PATHS if p not in paths]
    assert missing == [], f"mirror paths missing from OpenAPI: {missing}"


def test_documented_mirror_routes_are_get_only_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    unsafe: list[str] = []
    for mirror_path in REQUIRED_MIRROR_OPENAPI_PATHS:
        ops = paths.get(mirror_path) or {}
        for method in ops:
            if method.lower() not in ("get", "parameters"):
                unsafe.append(f"{mirror_path} {method.upper()}")
    assert unsafe == [], f"non-GET mirror OpenAPI operations: {unsafe}"


def test_operator_health_distinct_from_mirror_dependencies() -> None:
    paths = create_app().openapi()["paths"]
    assert LEGACY_HEALTH_NO_MIRROR_ALIAS in paths
    assert "/mirror/health/dependencies" in paths
    assert "/mirror/health" not in paths


def test_operator_contact_detail_distinct_from_mirror_contacts_list() -> None:
    paths = create_app().openapi()["paths"]
    assert "/mirror/contacts" in paths
    assert "/contacts/{email}" in paths
    assert "/mirror/contacts/{email}" not in paths
    assert "/mirror/contacts/{event_id}" not in paths


def test_operator_today_routes_remain_in_openapi() -> None:
    paths = create_app().openapi()["paths"]
    missing = [p for p in OPERATOR_TODAY_PATHS if p not in paths]
    assert missing == [], f"operator Today paths missing: {missing}"


def test_active_dashboard_mirror_limited_to_commercial_deals_list() -> None:
    """Today may call GET /mirror/commercial/deals only — not purchase-events or other mirror routes."""
    for path in _DASHBOARD_ACTIVE_SOURCES:
        text = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_DASHBOARD_MIRROR_PATHS:
            assert forbidden not in text, f"{path.name} must not reference {forbidden}"
        if "/mirror/" in text or '"/mirror"' in text:
            if path.name == "vite.config.ts":
                assert '"/mirror"' in text
                assert "/mirror/commercial/purchase-events" not in text
                continue
            assert _COMMERCIAL_DEALS_MIRROR_LIST in text, (
                f"{path.name} references /mirror/* but not the commercial deals list route"
            )


def test_parity_route_pair_count_matches_phase2_checklist() -> None:
    assert len(LEGACY_TO_MIRROR_ROUTE_PAIRS) == 15


def test_phase6_legacy_removal_doc_exists() -> None:
    doc = Path(__file__).resolve().parents[2] / "docs" / "API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md"
    assert doc.is_file()


def test_mirror_parity_checklist_doc_exists() -> None:
    doc = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "archive"
        / "api3"
        / "API-3_PHASE2_PARITY_CHECKLIST.md"
    )
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    for _legacy, mirror, _note in LEGACY_TO_MIRROR_ROUTE_PAIRS:
        assert mirror in text, f"doc missing mirror path {mirror}"
