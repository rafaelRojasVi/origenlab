"""API-3 Phase 4A/6: reference audit policy after legacy removal."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PHASE4A = _REPO_ROOT / "apps/api/docs/archive/api3/API-3_PHASE4A_REFERENCE_AUDIT.md"
_PHASE6 = _REPO_ROOT / "apps/api/docs/API-3_PHASE6_LEGACY_REMOVAL_COMPLETE.md"
_RUNBOOK = _REPO_ROOT / "apps/email-pipeline/docs/RUNBOOK.md"
_LEGACY_ROOT = _REPO_ROOT / "apps/email-pipeline/src/origenlab_api"
_OPERATOR_CLIENT = _REPO_ROOT / "apps/dashboard/src/api/operatorClient.ts"
_TODAY_PAGE = _REPO_ROOT / "apps/dashboard/src/pages/DashboardApp.tsx"
_VITE_CONFIG = _REPO_ROOT / "apps/dashboard/vite.config.ts"
_DASHBOARD_PKG = _REPO_ROOT / "apps/dashboard/package.json"

_ACTIVE_8000_ALLOWLIST = (
    _REPO_ROOT / "apps/dashboard/src/lib/devApiConfig.ts",
    _REPO_ROOT / "apps/dashboard/src/lib/devApiConfig.test.ts",
    _REPO_ROOT / "apps/dashboard/src/pages/DashboardApp.today.test.tsx",
    _REPO_ROOT / "apps/dashboard/src/components/operator/DevLegacyPortWarning.tsx",
)


def test_phase4a_and_phase6_docs_exist() -> None:
    assert _PHASE4A.is_file()
    assert _PHASE6.is_file()
    assert "Phase 6" in _PHASE6.read_text(encoding="utf-8")


def test_runbook_mirror_only_no_live_8000_curls() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "/mirror/" in text
    assert "127.0.0.1:8001/mirror" in text
    assert "127.0.0.1:8000/dashboard" not in text


def test_legacy_tree_removed_phase6() -> None:
    assert not _LEGACY_ROOT.exists()


def test_active_dashboard_has_no_mirror_paths() -> None:
    for path in (_OPERATOR_CLIENT, _TODAY_PAGE, _VITE_CONFIG):
        assert "/mirror/" not in path.read_text(encoding="utf-8")


def test_active_dashboard_no_runtime_8000_except_guardrails() -> None:
    active_dir = _REPO_ROOT / "apps/dashboard/src"
    hits: list[str] = []
    for path in active_dir.rglob("*"):
        if not path.is_file():
            continue
        if "/legacy/" in path.as_posix() or path.suffix not in (".ts", ".tsx"):
            continue
        if path in _ACTIVE_8000_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"127\.0\.0\.1:8000|localhost:8000", text):
            hits.append(str(path.relative_to(_REPO_ROOT)))
    assert hits == [], f"unexpected :8000 in active dashboard src: {hits}"


def test_smoke_mirror_without_smoke_legacy() -> None:
    pkg = _DASHBOARD_PKG.read_text(encoding="utf-8")
    assert '"smoke:mirror"' in pkg
    assert '"smoke:legacy"' not in pkg
