"""Unit tests for Render dashboard mirror verify assertions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERIFY = REPO / "scripts" / "qa" / "verify_dashboard_postgres_mirror.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_dashboard_postgres_mirror", VERIFY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_render_dashboard_assertions_pass() -> None:
    mod = _load_module()
    out = {
        "archive_emails": 0,
        "api_v_warm_case": 34,
        "api_v_equipment_opportunity": 9,
        "dashboard_sync_run_latest": (7, "success", "2026-05-18T10:00:00+00:00", "2026-05-18T10:01:00+00:00", "host/db"),
    }
    assert mod.evaluate_render_dashboard_assertions(out) == []


def test_render_dashboard_assertions_fail_warm_and_archive() -> None:
    mod = _load_module()
    out = {
        "archive_emails": 3,
        "api_v_warm_case": 0,
        "api_v_equipment_opportunity": 9,
        "dashboard_sync_run_latest": (1, "failed", "2026-05-18T10:00:00+00:00", None, "host/db"),
    }
    failures = mod.evaluate_render_dashboard_assertions(out)
    assert any("archive.emails" in f for f in failures)
    assert any("api.v_warm_case" in f for f in failures)
    assert any("status=" in f for f in failures)
    assert any("finished_at" in f for f in failures)


def test_render_dashboard_assertions_equipment_mismatch() -> None:
    """Explicit --expect-equipment-count still works as a manual diagnostic."""
    mod = _load_module()
    out = {
        "archive_emails": 0,
        "api_v_warm_case": 5,
        "api_v_equipment_opportunity": 8,
        "dashboard_sync_run_latest": (2, "success", "t0", "t1", "host/db"),
    }
    failures = mod.evaluate_render_dashboard_assertions(out, expect_equipment_count=9)
    assert len(failures) == 1
    assert "api.v_equipment_opportunity" in failures[0]


def test_render_dashboard_assertions_default_does_not_gate_equipment_count() -> None:
    """PHASE W1: the legacy equipment writer is frozen for observation, so
    Render readiness must not fail on a stale/mismatched equipment count
    unless an operator explicitly opts in via --expect-equipment-count."""
    mod = _load_module()
    out = {
        "archive_emails": 0,
        "api_v_warm_case": 5,
        "api_v_equipment_opportunity": 8,
        "dashboard_sync_run_latest": (2, "success", "t0", "t1", "host/db"),
    }
    assert mod.evaluate_render_dashboard_assertions(out) == []
