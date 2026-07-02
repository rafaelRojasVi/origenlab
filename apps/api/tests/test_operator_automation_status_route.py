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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    def fail_subprocess(*args: object, **kwargs: object):
        raise AssertionError("subprocess must not run for automation status")

    monkeypatch.setattr(subprocess, "run", fail_subprocess)
    monkeypatch.setattr(subprocess, "Popen", fail_subprocess)
    client = _client_with_active_current(_healthy_fixture(tmp_path))
    assert client.get("/operator/automation-status").status_code == 200


def test_uses_postgres_snapshot_before_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    pg_snapshot = {
        "updated_at": now.isoformat(),
        "snapshot": {
            "generated_at_utc": now.isoformat(),
            "active_current_dir": "<local-active-current>",
            "verdict": "healthy",
            "daily_core": {"exists": True, "status": "success", "returncode": 0},
            "mail_auto_refresh": {"state_exists": True, "dirty": False, "pending": False},
            "dashboard_auto_mirror": {
                "state_exists": True,
                "mirror_matches_daily_core": True,
            },
            "chilecompra_equipment_auto_refresh": {
                "state_exists": True,
                "last_result": "refreshed",
                "published_rows": 7,
                "candidate_summaries": 81,
                "detail_cache_hits": 50,
            },
            "cron": {"note": "not inspected by API"},
            "recommended_action": "none",
            "warnings": [],
        },
    }

    def _fake_pg(_settings: object) -> dict[str, object]:
        return pg_snapshot

    def _fake_mirror_sync(_settings: object) -> dict[str, object]:
        return {
            "table_available": True,
            "status": "success",
            "latest_sync_id": 135,
            "started_at": "2026-06-19T18:55:00+00:00",
            "finished_at": "2026-06-19T18:59:56+00:00",
            "elapsed_seconds": 296.0,
            "canonical_contact_count": 2318,
            "canonical_organization_count": 1015,
            "canonical_opportunity_signal_count": 983,
            "archive_contact_count": 27225,
            "archive_organization_count": 10701,
            "archive_opportunity_signal_count": 2702,
            "email_suppression_count": 363,
            "domain_suppression_count": 91,
            "outreach_state_count": 1450,
            "error_message": None,
        }

    monkeypatch.setattr(
        "origenlab_api.services.operator_automation_status_service.snapshot_repo.get_operator_automation_status_snapshot",
        _fake_pg,
    )
    monkeypatch.setattr(
        "origenlab_api.services.operator_automation_status_service.snapshot_repo.get_latest_dashboard_sync_snapshot",
        _fake_mirror_sync,
    )
    get_settings = __import__(
        "origenlab_api.settings", fromlist=["get_settings"]
    ).get_settings
    get_settings.cache_clear()
    settings = Settings(active_current=_healthy_fixture(tmp_path))
    app = create_app()
    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    data = client.get("/operator/automation-status").json()
    assert data["source"] == "postgres_snapshot"
    assert data["snapshot_updated_at"] == now.isoformat()
    assert data["verdict"] == "healthy"
    chilecompra = data["chilecompra_equipment_auto_refresh"]
    assert chilecompra["state_exists"] is True
    assert chilecompra["published_rows"] == 7
    mirror_sync = data["dashboard_mirror_sync"]
    assert mirror_sync is not None
    assert mirror_sync["status"] == "success"
    assert mirror_sync["latest_sync_id"] == 135
    get_settings.cache_clear()


def test_dashboard_mirror_sync_none_when_helper_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "origenlab_api.services.operator_automation_status_service.snapshot_repo.get_operator_automation_status_snapshot",
        lambda _settings: None,
    )
    monkeypatch.setattr(
        "origenlab_api.services.operator_automation_status_service.snapshot_repo.get_latest_dashboard_sync_snapshot",
        lambda _settings: None,
    )
    client = _client_with_active_current(_healthy_fixture(tmp_path))
    res = client.get("/operator/automation-status")
    assert res.status_code == 200
    assert res.json()["dashboard_mirror_sync"] is None
    _assert_redacted_paths_safe(res.json())


def test_dashboard_mirror_sync_uses_mirror_resolver_without_direct_postgres_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager
    from datetime import datetime, timezone
    from typing import Any, Generator

    class _MirrorSyncFakeCursor:
        def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
            self._rows = rows or []

        def fetchone(self) -> dict[str, Any] | None:
            return self._rows[0] if self._rows else None

        def fetchall(self) -> list[dict[str, Any]]:
            return list(self._rows)

    class _MirrorSyncFakeConn:
        def __init__(self, sync_row: dict[str, Any]) -> None:
            self._sync_row = sync_row

        def execute(self, sql: str, params: Any = None) -> _MirrorSyncFakeCursor:
            s = " ".join(sql.split()).lower()
            if "information_schema.tables" in s:
                schema = params[0]
                table = params[1]
                ok = schema == "reporting" and table == "dashboard_sync_run"
                return _MirrorSyncFakeCursor([{"?": 1}] if ok else [])
            if "select 1" in s and "information_schema" not in s:
                return _MirrorSyncFakeCursor([{"?": 1}])
            if "from reporting.dashboard_sync_run" in s:
                return _MirrorSyncFakeCursor([self._sync_row])
            return _MirrorSyncFakeCursor([])

    started = datetime(2026, 6, 19, 18, 55, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 6, 19, 18, 59, 56, tzinfo=timezone.utc)
    fake = _MirrorSyncFakeConn(
        {
            "id": 135,
            "started_at": started,
            "finished_at": finished,
            "status": "success",
            "canonical_contact_count": 2318,
            "canonical_organization_count": 1015,
            "canonical_opportunity_signal_count": 983,
            "archive_contact_count": 27225,
            "archive_organization_count": 10701,
            "archive_opportunity_signal_count": 2702,
            "email_suppression_count": 363,
            "domain_suppression_count": 91,
            "outreach_state_count": 1450,
            "error_message": None,
        }
    )

    @contextmanager
    def _fake_mirror_pg(_url: str) -> Generator[_MirrorSyncFakeConn, None, None]:
        assert _url == "postgresql://mirror-fallback/scratch"
        yield fake

    monkeypatch.delenv("ORIGENLAB_POSTGRES_URL", raising=False)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "origenlab_api.mirror.deps.resolve_postgres_url",
        lambda *args, **kwargs: "postgresql://mirror-fallback/scratch",
    )
    monkeypatch.setattr(
        "origenlab_api.repositories.postgres.dashboard_snapshots.mirror_postgres_connection",
        _fake_mirror_pg,
    )

    get_settings = __import__(
        "origenlab_api.settings", fromlist=["get_settings"]
    ).get_settings
    get_settings.cache_clear()
    settings = Settings(active_current=_healthy_fixture(tmp_path), postgres_url=None)
    app = create_app()
    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    res = client.get("/operator/automation-status")
    assert res.status_code == 200
    data = res.json()
    assert data["source"] == "filesystem_active_current"
    mirror_sync = data["dashboard_mirror_sync"]
    assert mirror_sync is not None
    assert mirror_sync["status"] == "success"
    assert mirror_sync["latest_sync_id"] == 135
    assert mirror_sync["finished_at"] == finished.isoformat()
    _assert_redacted_paths_safe(data)
    get_settings.cache_clear()


def test_filesystem_includes_chilecompra_equipment_auto_refresh(tmp_path: Path) -> None:
    active = _healthy_fixture(tmp_path)
    _write_chilecompra_state(active)
    data = _client_with_active_current(active).get("/operator/automation-status").json()
    assert data["source"] == "filesystem_active_current"
    chilecompra = data["chilecompra_equipment_auto_refresh"]
    assert chilecompra["state_exists"] is True
    assert chilecompra["published_rows"] == 7
    assert chilecompra["detail_cache_hits"] == 50


def test_mirror_behind_attention(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    _write_manifest(active)
    _write_mail_state(active)
    _write_mirror_state(
        active,
        last_mirrored_daily_core_generated_at="2026-06-10T17:00:00+00:00",
        last_successful_mirror_at=(_T0 - timedelta(seconds=1200)).isoformat(),
    )
    data = _client_with_active_current(active).get("/operator/automation-status").json()
    assert data["verdict"] == "attention"
    assert data["recommended_action"] == "run_auto_mirror_dashboard"


def test_automation_status_includes_redacted_path_companions(tmp_path: Path) -> None:
    active = _healthy_fixture(tmp_path)
    queue_path = str(active / "equipment_first_operator_queue_20260616.csv")
    audit_path = str(active / "chilecompra_equipment_candidate_audit_20260616.csv")
    _write_chilecompra_state(
        active,
        published_queue=queue_path,
        candidate_audit=audit_path,
    )
    data = _client_with_active_current(active).get("/operator/automation-status").json()

    assert data["path_redaction_applied"] is True
    assert data["active_current_dir"] == active.name
    assert "/home/" not in json.dumps(data)
    assert data["active_current_dir_info"] == {
        "redacted": True,
        "basename": active.name,
        "kind": "directory",
    }
    _assert_redacted_paths_safe(data["active_current_dir_info"])

    chilecompra = data["chilecompra_equipment_auto_refresh"]
    assert chilecompra["published_queue"] == "equipment_first_operator_queue_20260616.csv"
    assert chilecompra["candidate_audit"] == "chilecompra_equipment_candidate_audit_20260616.csv"
    path_info = chilecompra["path_info"]
    assert path_info["published_queue"]["basename"] == "equipment_first_operator_queue_20260616.csv"
    assert path_info["candidate_audit"]["basename"] == "chilecompra_equipment_candidate_audit_20260616.csv"
    _assert_redacted_paths_safe(path_info)


def test_postgres_snapshot_includes_redacted_active_current_dir_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    pg_snapshot = {
        "updated_at": now.isoformat(),
        "snapshot": {
            "generated_at_utc": now.isoformat(),
            "active_current_dir": "<local-active-current>",
            "verdict": "healthy",
            "daily_core": {"exists": True, "status": "success", "returncode": 0},
            "mail_auto_refresh": {"state_exists": True, "dirty": False, "pending": False},
            "dashboard_auto_mirror": {
                "state_exists": True,
                "mirror_matches_daily_core": True,
            },
            "chilecompra_equipment_auto_refresh": {
                "state_exists": True,
                "published_queue": "/home/ops/reports/out/active/current/equipment_first_operator_queue_20260616.csv",
                "candidate_audit": "/home/ops/reports/out/active/current/chilecompra_equipment_candidate_audit_20260616.csv",
            },
            "cron": {"note": "not inspected by API"},
            "recommended_action": "none",
            "warnings": [],
        },
    }

    monkeypatch.setattr(
        "origenlab_api.services.operator_automation_status_service.snapshot_repo.get_operator_automation_status_snapshot",
        lambda _settings: pg_snapshot,
    )
    get_settings = __import__(
        "origenlab_api.settings", fromlist=["get_settings"]
    ).get_settings
    get_settings.cache_clear()
    settings = Settings(active_current=_healthy_fixture(tmp_path))
    app = create_app()
    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    data = client.get("/operator/automation-status").json()

    assert data["path_redaction_applied"] is True
    assert data["active_current_dir"] == "current"
    assert data["active_current_dir_info"]["basename"] == "current"
    path_info = data["chilecompra_equipment_auto_refresh"]["path_info"]
    assert path_info["published_queue"]["kind"] == "file"
    assert data["chilecompra_equipment_auto_refresh"]["published_queue"] == (
        "equipment_first_operator_queue_20260616.csv"
    )
    assert "/home/" not in json.dumps(data)
    get_settings.cache_clear()
