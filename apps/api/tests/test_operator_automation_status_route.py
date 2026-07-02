"""GET /operator/automation-status — read-only automation health."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import Settings

AUTOMATION_STATUS_KEYS = frozenset(
    {
        "generated_at_utc",
        "active_current_dir",
        "active_current_dir_info",
        "path_redaction_applied",
        "verdict",
        "daily_core",
        "mail_auto_refresh",
        "dashboard_auto_mirror",
        "chilecompra_equipment_auto_refresh",
        "ndr_pending_review",
        "cron",
        "recommended_action",
        "warnings",
        "source",
        "snapshot_updated_at",
        "snapshot_stale",
        "dashboard_mirror_sync",
    }
)

_FORBIDDEN_IN_REDACTED_PATHS = (
    "/home/",
    "/mnt/",
    "\\",
    "postgres://",
    "ORIGENLAB_",
)


def _assert_redacted_paths_safe(payload: object) -> None:
    blob = json.dumps(payload)
    for forbidden in _FORBIDDEN_IN_REDACTED_PATHS:
        assert forbidden not in blob

DAILY_CORE_MANIFEST_NAME = "daily_core_run_manifest.json"
MAIL_STATE_NAME = "mail_auto_refresh_state.json"
MIRROR_STATE_NAME = "dashboard_auto_mirror_state.json"
CHILECOMPRA_STATE_NAME = "chilecompra_equipment_auto_refresh_state.json"
_DAILY_CORE_TS = "2026-06-10T18:12:48+00:00"
_MIRROR_TS = "2026-06-10T18:18:33+00:00"
_T0 = datetime(2026, 6, 10, 18, 30, 0, tzinfo=timezone.utc)


def _client_with_active_current(active_current: Path) -> TestClient:
    settings = Settings(active_current=active_current)
    app = create_app()
    app.dependency_overrides.clear()
    from origenlab_api.settings import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _write_manifest(active_current: Path, **kwargs: object) -> None:
    payload = {
        "schema_version": 1,
        "workflow": "daily-core",
        "generated_at_utc": _DAILY_CORE_TS,
        "status": "success",
        "returncode": 0,
        "steps": [{"label": "gmail-ingest", "returncode": 0}] * 8,
        **kwargs,
    }
    active_current.mkdir(parents=True, exist_ok=True)
    (active_current / DAILY_CORE_MANIFEST_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_mail_state(active_current: Path, **kwargs: object) -> None:
    payload = {
        "dirty": False,
        "last_result": "no_change",
        "last_successful_refresh_at": _DAILY_CORE_TS,
        "last_seen_inbox_total": 403,
        "last_seen_sent_total": 971,
        "consecutive_failures": 0,
        **kwargs,
    }
    (active_current / MAIL_STATE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_mirror_state(active_current: Path, **kwargs: object) -> None:
    payload = {
        "last_result": "success",
        "last_successful_mirror_at": _MIRROR_TS,
        "last_mirrored_daily_core_generated_at": _DAILY_CORE_TS,
        "consecutive_failures": 0,
        **kwargs,
    }
    (active_current / MIRROR_STATE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_chilecompra_state(active_current: Path, **kwargs: object) -> None:
    payload = {
        "last_result": "refreshed",
        "last_successful_refresh_at": _DAILY_CORE_TS,
        "last_successful_publish_at": _MIRROR_TS,
        "consecutive_failures": 0,
        "published_rows": 7,
        "candidate_summaries": 81,
        "detail_cache_hits": 50,
        **kwargs,
    }
    (active_current / CHILECOMPRA_STATE_NAME).write_text(json.dumps(payload), encoding="utf-8")


def _healthy_fixture(tmp_path: Path) -> Path:
    active = tmp_path / "active" / "current"
    _write_manifest(active)
    _write_mail_state(active)
    _write_mirror_state(active)
    return active


def test_route_returns_200_and_stable_keys(tmp_path: Path) -> None:
    client = _client_with_active_current(_healthy_fixture(tmp_path))
    res = client.get("/operator/automation-status")
    assert res.status_code == 200
    data = res.json()
    assert set(data.keys()) == AUTOMATION_STATUS_KEYS
    assert data["verdict"] == "healthy"
    assert data["recommended_action"] == "none"
    assert data["source"] == "filesystem_active_current"
    assert data["dashboard_mirror_sync"] is None


def test_healthy_fixture_verdict(tmp_path: Path) -> None:
    client = _client_with_active_current(_healthy_fixture(tmp_path))
    data = client.get("/operator/automation-status").json()
    assert data["verdict"] == "healthy"
    assert data["mail_auto_refresh"]["dirty"] is False
    assert data["dashboard_auto_mirror"]["mirror_matches_daily_core"] is True
    assert data["cron"] == {"note": "not inspected by API"}


def test_attention_mail_dirty(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    _write_manifest(active)
    _write_mail_state(active, dirty=True)
    _write_mirror_state(active)
    data = _client_with_active_current(active).get("/operator/automation-status").json()
    assert data["verdict"] == "attention"
    assert data["recommended_action"] == "wait_for_mail_quiet_window"


def test_blocked_failed_daily_core(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    _write_manifest(active, status="failed", returncode=1)
    _write_mail_state(active)
    _write_mirror_state(active)
    data = _client_with_active_current(active).get("/operator/automation-status").json()
    assert data["verdict"] == "blocked"
    assert data["recommended_action"] == "inspect_failed_daily_core"


def test_malformed_mail_state_blocked(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    _write_manifest(active)
    active.mkdir(parents=True, exist_ok=True)
    (active / MAIL_STATE_NAME).write_text("{bad", encoding="utf-8")
    _write_mirror_state(active)
    data = _client_with_active_current(active).get("/operator/automation-status").json()
    assert data["verdict"] == "blocked"


def test_email_pipeline_importable() -> None:
    from origenlab_email_pipeline.operator_cli.operator_automation_status import (
        build_operator_automation_status,
    )

    assert callable(build_operator_automation_status)


def test_no_subprocess_or_shell(