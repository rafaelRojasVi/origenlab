"""Tests for read-only SQLite storage telemetry and audit --storage-only."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from origenlab_email_pipeline.operator_cli.refresh import run_daily_core
from origenlab_email_pipeline.cli import RefreshDashboardOptions
from origenlab_email_pipeline.operator_cli.sqlite_storage_monitor import (
    MAX_HISTORY_SAMPLES,
    SNAPSHOT_FILENAME,
    HISTORY_FILENAME,
    auto_vacuum_name,
    build_sqlite_storage_status,
    collect_sqlite_storage_telemetry,
    growth_7d_gib,
    merge_storage_history,
    write_storage_evidence,
)
from origenlab_email_pipeline.operator_cli.operator_automation_status import (
    OperatorAutomationStatusOptions,
    build_operator_automation_status,
)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "qa" / "audit_sqlite_storage.py"


def _empty_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    return path


def test_storage_only_succeeds_without_emails_table(tmp_path: Path) -> None:
    db = _empty_db(tmp_path / "empty.sqlite")
    cp = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db), "--storage-only", "--json"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    payload = json.loads(cp.stdout)
    assert payload["mode"] == "storage-only"
    assert payload["read_only"] is True
    assert payload["mutation"] is False
    assert "email_row_count" not in payload
    assert "canonical_sent_row_count" not in payload
    assert "suspicious_future_date_count" not in payload
    assert "dbstat" not in payload
    assert payload["page_size"] > 0
    assert payload["active_gib"] is not None
    assert payload["freelist_ratio_percent"] is not None


def test_storage_only_rejects_dbstat_and_subjects(tmp_path: Path) -> None:
    db = _empty_db(tmp_path / "empty.sqlite")
    cp = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--db",
            str(db),
            "--storage-only",
            "--include-dbstat",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode == 2
    assert "cannot be combined" in cp.stderr


def test_active_bytes_and_freelist_ratio(tmp_path: Path) -> None:
    db = _empty_db(tmp_path / "t.sqlite")
    telem = collect_sqlite_storage_telemetry(db)
    assert telem["active_page_count"] == telem["page_count"] - telem["freelist_count"]
    assert telem["active_bytes"] == telem["page_size"] * telem["active_page_count"]
    assert telem["freelist_bytes"] == telem["page_size"] * telem["freelist_count"]
    expected_ratio = round((telem["freelist_bytes"] / telem["allocated_bytes"]) * 100.0, 4)
    assert telem["freelist_ratio_percent"] == expected_ratio


def test_auto_vacuum_name_mapping() -> None:
    assert auto_vacuum_name(0) == "none"
    assert auto_vacuum_name(1) == "full"
    assert auto_vacuum_name(2) == "incremental"
    assert auto_vacuum_name(9) == "unknown(9)"


def test_disk_metrics_injected(tmp_path: Path) -> None:
    db = _empty_db(tmp_path / "t.sqlite")
    block = 4096
    usage = SimpleNamespace(f_frsize=block, f_blocks=1000, f_bavail=200)

    telem = collect_sqlite_storage_telemetry(db, disk_usage=lambda _p: usage)
    assert telem["disk_total_bytes"] == block * 1000
    assert telem["disk_free_bytes"] == block * 200
    assert telem["disk_used_bytes"] == block * 800
    assert telem["disk_free_percent"] == 20.0


def test_snapshot_atomic_write_and_same_day_replace(tmp_path: Path) -> None:
    active = tmp_path / "active" / "current"
    active.mkdir(parents=True)
    day = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    first = {
        "captured_at_utc": day.isoformat(),
        "allocated_gib": 1.0,
        "schema_version": 1,
    }
    write_storage_evidence(first, active_current=active)
    second = {
        "captured_at_utc": (day + timedelta(hours=3)).isoformat(),
        "allocated_gib": 1.5,
        "schema_version": 1,
    }
    write_storage_evidence(second, active_current=active)
    history = json.loads((active / HISTORY_FILENAME).read_text(encoding="utf-8"))
    assert history["sample_count"] == 1
    assert history["samples"][0]["allocated_gib"] == 1.5
    snap = json.loads((active / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    assert snap["allocated_gib"] == 1.5


def test_history_35_day_cap(tmp_path: Path) -> None:
    samples = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(40):
        samples.append(
            {
                "captured_at_utc": (base + timedelta(days=i)).isoformat(),
                "allocated_gib": float(i),
            }
        )
    merged = merge_storage_history([], samples[0])
    for sample in samples[1:]:
        merged = merge_storage_history(merged, sample)
    assert len(merged) == MAX_HISTORY_SAMPLES
    assert merged[0]["allocated_gib"] == 5.0
    assert merged[-1]["allocated_gib"] == 39.0


def test_growth_7d_exact_seven_day() -> None:
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    history = [
        {"captured_at_utc": (now - timedelta(days=10)).isoformat(), "allocated_gib": 100.0},
        {"captured_at_utc": (now - timedelta(days=7)).isoformat(), "allocated_gib": 120.0},
        {"captured_at_utc": now.isoformat(), "allocated_gib": 126.5},
    ]
    assert growth_7d_gib(history, now=now) == 6.5


def test_growth_3d_returns_none_and_cannot_trigger_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    history = [
        {"captured_at_utc": (now - timedelta(days=3)).isoformat(), "allocated_gib": 100.0},
        {"captured_at_utc": now.isoformat(), "allocated_gib": 120.0},
    ]
    assert growth_7d_gib(history, now=now) is None

    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    write_storage_evidence(
        {
            "captured_at_utc": now.isoformat(),
            "allocated_gib": 120.0,
            "freelist_gib": 1.0,
            "active_gib": 119.0,
            "freelist_ratio_percent": 1.0,
            "disk_free_gib": 200.0,
            "disk_free_percent": 20.0,
        },
        active_current=active,
    )
    # Inject a history file with only 3-day prior growth (large delta must not warn).
    from origenlab_email_pipeline.operator_cli.sqlite_storage_monitor import (
        HISTORY_FILENAME,
        SCHEMA_VERSION,
        atomic_write_json,
    )

    atomic_write_json(
        active / HISTORY_FILENAME,
        {"schema_version": SCHEMA_VERSION, "sample_count": 2, "samples": history, "mutation": False},
    )
    status = build_sqlite_storage_status(reports_dir=reports, now=now)
    assert status["growth_7d_gib"] is None
    assert "db_growth_7d_above_5_gib" not in status["reasons"]
    assert status["status"] == "healthy"


def test_growth_lone_older_sample_outside_window_returns_none() -> None:
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    history = [
        {"captured_at_utc": (now - timedelta(days=20)).isoformat(), "allocated_gib": 50.0},
        {"captured_at_utc": now.isoformat(), "allocated_gib": 120.0},
    ]
    assert growth_7d_gib(history, now=now) is None


def test_valid_seven_day_growth_over_5_gib_drives_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 7, 15, 17, 0, tzinfo=timezone.utc)
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    history = [
        {"captured_at_utc": (now - timedelta(days=7)).isoformat(), "allocated_gib": 100.0},
        {
            "captured_at_utc": now.isoformat(),
            "allocated_gib": 108.0,
            "freelist_gib": 1.0,
            "active_gib": 107.0,
            "freelist_ratio_percent": 1.0,
            "disk_free_gib": 200.0,
            "disk_free_percent": 20.0,
        },
    ]
    write_storage_evidence(history[-1], active_current=active)
    from origenlab_email_pipeline.operator_cli.sqlite_storage_monitor import (
        HISTORY_FILENAME,
        SCHEMA_VERSION,
        atomic_write_json,
    )

    atomic_write_json(
        active / HISTORY_FILENAME,
        {"schema_version": SCHEMA_VERSION, "sample_count": 2, "samples": history, "mutation": False},
    )
    status = build_sqlite_storage_status(reports_dir=reports, now=now)
    assert status["growth_7d_gib"] == 8.0
    assert status["status"] == "attention"
    assert "db_growth_7d_above_5_gib" in status["reasons"]

    (active / "daily_core_run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "returncode": 0,
                "generated_at_utc": "2026-07-15T15:55:28+00:00",
                "steps": [{}] * 8,
            }
        ),
        encoding="utf-8",
    )
    (active / "mail_auto_refresh_state.json").write_text(
        json.dumps(
            {
                "dirty": False,
                "last_result": "no_change",
                "last_run_started_at": "2026-07-15T16:00:00+00:00",
                "last_run_finished_at": "2026-07-15T16:00:02+00:00",
                "consecutive_failures": 0,
            }
        ),
        encoding="utf-8",
    )
    (active / "dashboard_auto_mirror_state.json").write_text(
        json.dumps(
            {
                "last_result": "already_mirrored",
                "last_successful_mirror_at": "2026-07-15T16:17:49+00:00",
                "last_mirrored_daily_core_generated_at": "2026-07-15T15:55:28+00:00",
                "last_run_started_at": "2026-07-15T16:17:49+00:00",
                "last_run_finished_at": "2026-07-15T16:17:49+00:00",
                "consecutive_failures": 0,
            }
        ),
        encoding="utf-8",
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        options=OperatorAutomationStatusOptions(skip_cron_inspection=True),
        now=now,
        process_alive=lambda _pid: False,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "inspect_sqlite_storage_capacity_no_vacuum"
    assert "db_growth_7d_above_5_gib" in report["warnings"]


def test_bytes_or_none_tolerates_disappearing_wal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from origenlab_email_pipeline.operator_cli import sqlite_storage_monitor as mod

    wal = tmp_path / "emails.sqlite-wal"
    wal.write_bytes(b"abc")

    real_is_file = Path.is_file
    real_stat = Path.stat

    def flaky_is_file(self: Path) -> bool:
        if self == wal:
            return True
        return real_is_file(self)

    def flaky_stat(self: Path, *args, **kwargs):
        if self == wal:
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", flaky_is_file)
    monkeypatch.setattr(Path, "stat", flaky_stat)
    assert mod._bytes_or_none(wal) is None


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("disk_free_gib", "not-a-number"),
        ("disk_free_percent", {"x": 1}),
        ("freelist_ratio_percent", "high"),
        ("allocated_gib", "big"),
        ("allocated_bytes", "oops"),
    ],
)
def test_invalid_numeric_snapshot_fields_stay_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_field: str,
    bad_value: object,
) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    sample = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "allocated_gib": 10.0,
        "freelist_gib": 1.0,
        "active_gib": 9.0,
        "freelist_ratio_percent": 10.0,
        "disk_free_gib": 200.0,
        "disk_free_percent": 20.0,
        bad_field: bad_value,
    }
    (active / SNAPSHOT_FILENAME).write_text(json.dumps(sample), encoding="utf-8")
    status = build_sqlite_storage_status(reports_dir=reports)
    assert status["status"] == "unknown"
    assert status["parse_error"] == "malformed_numeric"
    assert status["reasons"] == ["snapshot_malformed_numeric"]
    assert "db_growth_7d_above_5_gib" not in status["reasons"]


def test_high_freelist_alone_stays_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    sample = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "allocated_gib": 127.5,
        "freelist_gib": 66.5,
        "active_gib": 61.0,
        "freelist_ratio_percent": 52.2,
        "disk_free_gib": 200.0,
        "disk_free_percent": 20.0,
        "auto_vacuum_name": "none",
        "journal_mode": "wal",
    }
    write_storage_evidence(sample, active_current=active)
    status = build_sqlite_storage_status(reports_dir=reports)
    assert status["status"] == "healthy"
    assert "high_freelist_ratio_informational" in status["reasons"]


def test_low_disk_threshold_attention(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    sample = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "allocated_gib": 10.0,
        "freelist_gib": 1.0,
        "active_gib": 9.0,
        "freelist_ratio_percent": 10.0,
        "disk_free_gib": 100.0,
        "disk_free_percent": 10.0,
        "auto_vacuum_name": "none",
        "journal_mode": "wal",
    }
    write_storage_evidence(sample, active_current=active)
    status = build_sqlite_storage_status(reports_dir=reports)
    assert status["status"] == "attention"
    assert "disk_free_below_150_gib" in status["reasons"]
    assert "disk_free_below_15_percent" in status["reasons"]
    assert "inspect_disk_capacity_no_vacuum" in status["reasons"]


def test_missing_snapshot_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reports = tmp_path / "reports"
    (reports / "active" / "current").mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    status = build_sqlite_storage_status(reports_dir=reports)
    assert status["state_exists"] is False
    assert status["status"] == "unknown"
    assert status["parse_error"] is None


def test_malformed_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    (active / SNAPSHOT_FILENAME).write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    status = build_sqlite_storage_status(reports_dir=reports)
    assert status["parse_error"] == "malformed"
    assert status["status"] == "unknown"


def test_daily_core_success_writes_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    db = _empty_db(tmp_path / "emails.sqlite")
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))

    def fake_runner(cmd, passthrough=None, *, mirror_apply=False, mirror_alembic=False):
        return 0

    assert run_daily_core(RefreshDashboardOptions(apply=True), runner=fake_runner) == 0
    assert (active / SNAPSHOT_FILENAME).is_file()
    assert (active / HISTORY_FILENAME).is_file()
    snap = json.loads((active / SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    assert snap["database_basename"] == "emails.sqlite"
    assert "/" not in snap["database_basename"]
    assert snap["mutation"] is False


def test_daily_core_plan_only_does_not_write_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    db = _empty_db(tmp_path / "emails.sqlite")
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))
    assert run_daily_core(RefreshDashboardOptions(apply=False), runner=lambda *a, **k: 0) == 0
    assert not (active / SNAPSHOT_FILENAME).is_file()


def test_daily_core_failed_does_not_write_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    db = _empty_db(tmp_path / "emails.sqlite")
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(db))

    def fake_runner(cmd, passthrough=None, *, mirror_apply=False, mirror_alembic=False):
        return 1

    assert run_daily_core(RefreshDashboardOptions(apply=True), runner=fake_runner) == 1
    assert not (active / SNAPSHOT_FILENAME).is_file()


def test_telemetry_failure_does_not_change_returncode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "reports"
    (reports / "active" / "current").mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    monkeypatch.setenv("ORIGENLAB_SQLITE_PATH", str(tmp_path / "missing.sqlite"))

    def fake_runner(cmd, passthrough=None, *, mirror_apply=False, mirror_alembic=False):
        return 0

    assert run_daily_core(RefreshDashboardOptions(apply=True), runner=fake_runner) == 0


def test_elapsed_time_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reports = tmp_path / "reports"
    active = reports / "active" / "current"
    active.mkdir(parents=True)
    monkeypatch.setenv("ORIGENLAB_REPORTS_DIR", str(reports))
    (active / "daily_core_run_manifest.json").write_text(
        json.dumps(
            {
                "status": "success",
                "returncode": 0,
                "generated_at_utc": "2026-07-15T15:55:28+00:00",
                "steps": [{}] * 8,
            }
        ),
        encoding="utf-8",
    )
    (active / "mail_auto_refresh_state.json").write_text(
        json.dumps(
            {
                "dirty": False,
                "last_result": "no_change",
                "last_run_started_at": "2026-07-15T16:00:00+00:00",
                "last_run_finished_at": "2026-07-15T16:00:05+00:00",
                "consecutive_failures": 0,
            }
        ),
        encoding="utf-8",
    )
    (active / "dashboard_auto_mirror_state.json").write_text(
        json.dumps(
            {
                "last_result": "already_mirrored",
                "last_successful_mirror_at": "2026-07-15T16:17:49+00:00",
                "last_mirrored_daily_core_generated_at": "2026-07-15T15:55:28+00:00",
                "last_run_started_at": "2026-07-15T16:15:00+00:00",
                "last_run_finished_at": "2026-07-15T16:16:50+00:00",
                "consecutive_failures": 0,
            }
        ),
        encoding="utf-8",
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        options=OperatorAutomationStatusOptions(skip_cron_inspection=True),
        now=datetime(2026, 7, 15, 17, 0, tzinfo=timezone.utc),
        process_alive=lambda _pid: False,
    )
    assert report["mail_auto_refresh"]["last_run_elapsed_seconds"] == 5
    assert report["dashboard_auto_mirror"]["last_run_elapsed_seconds"] == 110
    assert report["dashboard_auto_mirror"]["runtime_exceeds_schedule_interval"] is True
    assert "sqlite_storage" in report
    # Mirror over schedule must not alone force attention when otherwise healthy.
    assert report["verdict"] in {"healthy", "attention"}
