"""Tests for debounced dashboard mirror automation (mocked mirror + state files)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from origenlab_email_pipeline.operator_cli.daily_core_manifest import MANIFEST_FILENAME
from origenlab_email_pipeline.operator_cli.dashboard_auto_mirror import (
    DashboardAutoMirrorOptions,
    DashboardAutoMirrorState,
    compute_dashboard_input_fingerprint,
    evaluate_dashboard_auto_mirror,
    load_state,
    run_dashboard_auto_mirror,
    state_path,
)
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import (
    MailAutoRefreshState,
    acquire_lock,
    state_path as mail_state_path,
)

_T0 = datetime(2026, 6, 10, 15, 0, 0, tzinfo=timezone.utc)
_DAILY_CORE_TS = "2026-06-10T14:30:00+00:00"


def _opts(**kwargs: object) -> DashboardAutoMirrorOptions:
    return DashboardAutoMirrorOptions(once=True, **kwargs)


def _parse_output(out: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in out.strip().splitlines():
        if "=" in line and line != "dashboard_auto_mirror":
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _write_daily_core_manifest(
    active_current: Path,
    *,
    status: str = "success",
    returncode: int = 0,
    generated_at: str = _DAILY_CORE_TS,
) -> None:
    payload = {
        "schema_version": 1,
        "workflow": "daily-core",
        "generated_at_utc": generated_at,
        "status": status,
        "returncode": returncode,
        "steps": [],
    }
    active_current.mkdir(parents=True, exist_ok=True)
    (active_current / MANIFEST_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_mail_state(
    active_current: Path,
    *,
    dirty: bool = False,
    pending_inbox: int | None = None,
) -> None:
    state = MailAutoRefreshState(dirty=dirty)
    if pending_inbox is not None:
        state.pending_inbox_total = pending_inbox
    path = mail_state_path(active_current.parent.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict()), encoding="utf-8")


def _ready_fixture(active_current: Path) -> None:
    _write_daily_core_manifest(active_current)
    _write_mail_state(active_current, dirty=False)


def _write_dashboard_manifest(active_current: Path, *, canonical_files: list[str]) -> None:
    active_current.mkdir(parents=True, exist_ok=True)
    (active_current / "manifest.json").write_text(
        json.dumps({"campaign_mode": "equipment_first", "canonical_files": canonical_files}),
        encoding="utf-8",
    )


def _write_equipment_queue_csv(active_current: Path, *, filename: str, content: str) -> Path:
    active_current.mkdir(parents=True, exist_ok=True)
    path = active_current / filename
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def active_current(reports_dir: Path) -> Path:
    path = reports_dir / "active" / "current"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def mock_publish_snapshot(monkeypatch: pytest.MonkeyPatch) -> list[tuple[bool, bool, Path | None]]:
    calls: list[tuple[bool, bool, Path | None]] = []

    def _record(
        options: DashboardAutoMirrorOptions,
        reports_dir: Path | None = None,
    ) -> None:
        if options.apply and options.allow_non_scratch_postgres:
            calls.append(
                (options.apply, options.allow_non_scratch_postgres, reports_dir)
            )

    monkeypatch.setattr(
        "origenlab_email_pipeline.operator_cli.dashboard_auto_mirror."
        "_publish_automation_status_snapshot_if_configured",
        _record,
    )
    return calls


def test_missing_daily_core_manifest_no_run(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_mail_state(reports_dir / "active" / "current")
    run_dashboard_auto_mirror(_opts(), reports_dir=reports_dir, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "daily_core_manifest_missing"
    assert out["should_run"] == "false"
    assert out["ran_mirror"] == "false"


def test_daily_core_failed_no_run(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_daily_core_manifest(active_current, status="failed", returncode=1)
    _write_mail_state(active_current)
    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=active_current.parent.parent,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "daily_core_failed"
    assert out["should_run"] == "false"


def test_mail_dirty_no_run(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_daily_core_manifest(active_current)
    _write_mail_state(active_current, dirty=True)
    run_dashboard_auto_mirror(_opts(), reports_dir=active_current.parent.parent, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "mail_dirty"
    assert out["mail_dirty"] == "true"
    assert out["should_run"] == "false"


def test_mail_pending_no_run(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_daily_core_manifest(active_current)
    _write_mail_state(active_current, pending_inbox=101)
    run_dashboard_auto_mirror(_opts(), reports_dir=active_current.parent.parent, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "mail_pending"
    assert out["mail_pending"] == "true"
    assert out["should_run"] == "false"


def test_already_mirrored_no_run(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ready_fixture(active_current)
    reports = active_current.parent.parent
    fingerprint = compute_dashboard_input_fingerprint(reports)
    mirror_state = DashboardAutoMirrorState(
        last_mirrored_daily_core_generated_at=_DAILY_CORE_TS,
        last_successful_mirror_at=_T0.isoformat(),
        last_mirrored_dashboard_input_fingerprint=fingerprint,
    )
    state_file = state_path(reports)
    state_file.write_text(json.dumps(mirror_state.to_dict()), encoding="utf-8")

    run_dashboard_auto_mirror(_opts(), reports_dir=reports, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "already_mirrored"
    assert out["should_run"] == "false"


def test_cooldown_prevents_repeated_mirror(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ready_fixture(active_current)
    mirror_state = DashboardAutoMirrorState(
        last_successful_mirror_at=(_T0 - timedelta(seconds=30)).isoformat(),
    )
    state_file = state_path(active_current.parent.parent)
    state_file.write_text(json.dumps(mirror_state.to_dict()), encoding="utf-8")

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True, cooldown_seconds=60),
        reports_dir=active_current.parent.parent,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "cooldown"
    assert out["should_run"] == "false"


def test_post_daily_core_ready_after_default_60s_cooldown(
    active_current: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mirror finished; newer daily-core a few minutes later; next 1-min tick with cooldown=60 is ready."""
    older_core = "2026-06-10T14:00:00+00:00"
    newer_core = "2026-06-10T14:55:00+00:00"
    mirror_finished = datetime(2026, 6, 10, 14, 52, 0, tzinfo=timezone.utc)
    eval_at = datetime(2026, 6, 10, 14, 56, 30, tzinfo=timezone.utc)  # ~4.5 min after mirror

    _write_daily_core_manifest(active_current, generated_at=newer_core)
    _write_mail_state(active_current, dirty=False)
    reports = active_current.parent.parent
    fingerprint = compute_dashboard_input_fingerprint(reports)
    mirror_state = DashboardAutoMirrorState(
        last_successful_mirror_at=mirror_finished.isoformat(),
        last_mirrored_daily_core_generated_at=older_core,
        last_mirrored_dashboard_input_fingerprint="stale-fingerprint",
    )
    state_path(reports).write_text(json.dumps(mirror_state.to_dict()), encoding="utf-8")

    # Cooldown elapsed (>60s) and daily-core is newer → ready (dry-run).
    run_dashboard_auto_mirror(
        _opts(apply=False, cooldown_seconds=60),
        reports_dir=reports,
        now_fn=lambda: eval_at,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "dry_run"
    assert out["should_run"] == "true"
    assert out["daily_core_generated_at"] == newer_core

    # already_mirrored still wins when fingerprint + daily-core match.
    mirrored = DashboardAutoMirrorState(
        last_successful_mirror_at=mirror_finished.isoformat(),
        last_mirrored_daily_core_generated_at=newer_core,
        last_mirrored_dashboard_input_fingerprint=fingerprint,
    )
    state_path(reports).write_text(json.dumps(mirrored.to_dict()), encoding="utf-8")
    run_dashboard_auto_mirror(
        _opts(apply=False, cooldown_seconds=60),
        reports_dir=reports,
        now_fn=lambda: eval_at,
    )
    out2 = _parse_output(capsys.readouterr().out)
    assert out2["reason"] == "already_mirrored"
    assert out2["should_run"] == "false"


def test_dry_run_gates_passing_should_run_true(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ready_fixture(active_current)
    run_dashboard_auto_mirror(_opts(apply=False), reports_dir=active_current.parent.parent, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["should_run"] == "true"
    assert out["ran_mirror"] == "false"
    assert out["reason"] == "dry_run"
    assert out["allow_non_scratch_postgres"] == "false"


def test_apply_without_allow_flag_no_run(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ready_fixture(active_current)
    run_dashboard_auto_mirror(_opts(apply=True), reports_dir=active_current.parent.parent, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "allow_non_scratch_required"
    assert out["should_run"] == "false"
    assert out["allow_non_scratch_postgres"] == "false"


def test_apply_with_gates_runs_mirror_once(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ready_fixture(active_current)
    mirror_calls: list[str] = []

    def fake_mirror() -> int:
        mirror_calls.append("ran")
        return 0

    rc = run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=active_current.parent.parent,
        run_mirror_fn=fake_mirror,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert rc == 0
    assert mirror_calls == ["ran"]
    assert out["ran_mirror"] == "true"
    assert out["reason"] == "mirrored"
    assert out["mirror_returncode"] == "0"


def test_mirror_success_updates_state(active_current: Path) -> None:
    _ready_fixture(active_current)
    reports = active_current.parent.parent

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=reports,
        run_mirror_fn=lambda: 0,
        now_fn=lambda: _T0,
    )
    state = load_state(state_path(reports))
    assert state.last_successful_mirror_at is not None
    assert state.last_mirrored_daily_core_generated_at == _DAILY_CORE_TS
    assert state.last_mirrored_dashboard_input_fingerprint == compute_dashboard_input_fingerprint(
        reports
    )
    assert state.consecutive_failures == 0
    assert state.last_result == "success"


def test_mirror_failure_increments_failures_and_returns_rc(
    active_current: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ready_fixture(active_current)
    reports = active_current.parent.parent

    rc = run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=reports,
        run_mirror_fn=lambda: 3,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert rc == 3
    assert out["reason"] == "mirror_failed"
    state = load_state(state_path(reports))
    assert state.consecutive_failures == 1
    assert state.last_result == "mirror_failed"


def test_dashboard_mirror_lock_prevents_overlap(
    active_current: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "origenlab_email_pipeline.operator_cli.dashboard_auto_mirror._process_alive",
        lambda pid: True,
    )
    acquire_lock(active_current / "dashboard_auto_mirror.lock", now=_T0)

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=active_current.parent.parent,
        run_mirror_fn=lambda: pytest.fail("mirror must not run when lock held"),
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "already_running"


def test_auto_refresh_lock_prevents_mirror(
    active_current: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready_fixture(active_current)
    monkeypatch.setattr(
        "origenlab_email_pipeline.operator_cli.dashboard_auto_mirror._process_alive",
        lambda pid: True,
    )
    acquire_lock(active_current / "auto_refresh.lock", now=_T0)

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=active_current.parent.parent,
        run_mirror_fn=lambda: pytest.fail("mirror must not run during mail refresh"),
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "mail_refresh_running"


def test_pause_file_prevents_run(
    active_current: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ready_fixture(active_current)
    (active_current / "dashboard_auto_mirror_paused").write_text("", encoding="utf-8")

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=active_current.parent.parent,
        run_mirror_fn=lambda: pytest.fail("mirror must not run when paused"),
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "paused"


def test_evaluate_unit_missing_manifest() -> None:
    result = evaluate_dashboard_auto_mirror(
        options=_opts(),
        mirror_state=DashboardAutoMirrorState(),
        manifest=None,
        mail_state=MailAutoRefreshState(),
        now=_T0,
        dashboard_input_fingerprint="abc123",
    )
    assert result.reason == "daily_core_manifest_missing"


def test_docs_mention_separate_cron() -> None:
    doc = Path(__file__).resolve().parents[1] / "docs" / "pipeline" / "DASHBOARD_AUTO_MIRROR.md"
    text = doc.read_text(encoding="utf-8")
    assert "every minute" in text.lower() or "one-minute" in text.lower() or "1 minute" in text.lower()
    assert "60" in text
    assert "auto-refresh-mail" in text
    assert "not send approval" in text.lower()


def test_mail_dirty_skip_publishes_automation_snapshot_when_apply_allowed(
    active_current: Path,
    mock_publish_snapshot: list[tuple[bool, bool, Path | None]],
) -> None:
    _write_daily_core_manifest(active_current)
    _write_mail_state(active_current, dirty=True)
    reports = active_current.parent.parent

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=reports,
        now_fn=lambda: _T0,
    )
    state = load_state(state_path(reports))
    assert state.last_result == "mail_dirty"
    assert mock_publish_snapshot == [(True, True, reports)]


def test_cooldown_skip_publishes_automation_snapshot_when_apply_allowed(
    active_current: Path,
    mock_publish_snapshot: list[tuple[bool, bool, Path | None]],
) -> None:
    _ready_fixture(active_current)
    mirror_state = DashboardAutoMirrorState(
        last_successful_mirror_at=(_T0 - timedelta(seconds=60)).isoformat(),
    )
    state_file = state_path(active_current.parent.parent)
    state_file.write_text(json.dumps(mirror_state.to_dict()), encoding="utf-8")

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=active_current.parent.parent,
        now_fn=lambda: _T0,
    )
    assert mock_publish_snapshot == [(True, True, active_current.parent.parent)]


def test_dry_run_does_not_publish_automation_snapshot(
    active_current: Path,
    mock_publish_snapshot: list[tuple[bool, bool, Path | None]],
) -> None:
    _ready_fixture(active_current)
    run_dashboard_auto_mirror(
        _opts(apply=False),
        reports_dir=active_current.parent.parent,
        now_fn=lambda: _T0,
    )
    assert mock_publish_snapshot == []


def test_apply_without_allow_flag_does_not_publish_automation_snapshot(
    active_current: Path,
    mock_publish_snapshot: list[tuple[bool, bool, Path | None]],
) -> None:
    _write_daily_core_manifest(active_current)
    _write_mail_state(active_current, dirty=True)
    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=False),
        reports_dir=active_current.parent.parent,
        now_fn=lambda: _T0,
    )
    assert mock_publish_snapshot == []


def test_mirror_success_publishes_automation_snapshot_after_state_update(
    active_current: Path,
    mock_publish_snapshot: list[tuple[bool, bool, Path | None]],
) -> None:
    _ready_fixture(active_current)
    reports = active_current.parent.parent

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=reports,
        run_mirror_fn=lambda: 0,
        now_fn=lambda: _T0,
    )
    state = load_state(state_path(reports))
    assert state.last_result == "success"
    assert mock_publish_snapshot == [(True, True, reports)]


def test_publish_automation_snapshot_called_after_release_lock(
    active_current: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import origenlab_email_pipeline.operator_cli.dashboard_auto_mirror as mirror_mod

    _ready_fixture(active_current)
    reports = active_current.parent.parent
    order: list[str] = []
    real_release = mirror_mod.release_lock

    def track_release(lock_file: Path) -> None:
        order.append("release_lock")
        real_release(lock_file)

    def track_publish(
        options: DashboardAutoMirrorOptions,
        reports_dir: Path | None = None,
    ) -> None:
        order.append("publish_snapshot")

    monkeypatch.setattr(mirror_mod, "release_lock", track_release)
    monkeypatch.setattr(
        mirror_mod,
        "_publish_automation_status_snapshot_if_configured",
        track_publish,
    )

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=reports,
        run_mirror_fn=lambda: 0,
        now_fn=lambda: _T0,
    )
    assert order == ["release_lock", "publish_snapshot"]


def test_mirror_success_snapshot_does_not_report_live_mirror_lock(
    active_current: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import origenlab_email_pipeline.operator_cli.dashboard_auto_mirror as mirror_mod

    from origenlab_email_pipeline.dashboard_snapshot_publish import (
        build_operator_automation_status_snapshot,
    )

    _ready_fixture(active_current)
    reports = active_current.parent.parent
    lock_live_at_publish: list[bool] = []
    published_snapshots: list[dict[str, object]] = []

    def publish_and_verify(
        options: DashboardAutoMirrorOptions,
        reports_dir: Path | None = None,
    ) -> None:
        lock_file = mirror_mod.lock_path(reports_dir)
        lock_live_at_publish.append(mirror_mod._lock_is_live(lock_file))
        snapshot = build_operator_automation_status_snapshot(
            mirror_mod.active_current_dir(reports_dir),
            mirror_cooldown_seconds=options.cooldown_seconds,
        )
        published_snapshots.append(snapshot)

    monkeypatch.setattr(
        mirror_mod,
        "_publish_automation_status_snapshot_if_configured",
        publish_and_verify,
    )
    monkeypatch.setattr(
        mirror_mod,
        "_process_alive",
        lambda pid: True,
    )

    run_dashboard_auto_mirror(
        _opts(apply=True, allow_non_scratch_postgres=True),
        reports_dir=reports,
        run_mirror_fn=lambda: 0,
        now_fn=lambda: _T0,
    )

    assert lock_live_at_publish == [False]
    mirror_section = published_snapshots[0]["dashboard_auto_mirror"]
    assert isinstance(mirror_section, dict)
    assert mirror_section["lock_live"] is False


def test_daily_core_mirrored_but_fingerprint_changed_should_run(
    active_current: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ready_fixture(active_current)
    reports = active_current.parent.parent
    _write_dashboard_manifest(
        active_current,
        canonical_files=["equipment_first_operator_queue_20260614.csv"],
    )
    _write_equipment_queue_csv(
        active_current,
        filename="equipment_first_operator_queue_20260614.csv",
        content="codigo_licitacion,buyer\n1051-1-LP26,Hospital\n",
    )
    current_fingerprint = compute_dashboard_input_fingerprint(reports)
    mirror_state = DashboardAutoMirrorState(
        last_mirrored_daily_core_generated_at=_DAILY_CORE_TS,
        last_successful_mirror_at=(_T0 - timedelta(hours=2)).isoformat(),
        last_mirrored_dashboard_input_fingerprint="stale-fingerprint",
    )
    state_path(reports).write_text(json.dumps(mirror_state.to_dict()), encoding="utf-8")

    run_dashboard_auto_mirror(_opts(apply=False), reports_dir=reports, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "dry_run"
    assert out["should_run"] == "true"
    assert out["dashboard_input_fingerprint"] == current_fingerprint


def test_daily_core_and_fingerprint_matched_is_already_mirrored(
    active_current: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _ready_fixture(active_current)
    reports = active_current.parent.parent
    _write_dashboard_manifest(
        active_current,
        canonical_files=["equipment_first_operator_queue_20260614.csv"],
    )
    _write_equipment_queue_csv(
        active_current,
        filename="equipment_first_operator_queue_20260614.csv",
        content="codigo_licitacion,buyer\n1051-1-LP26,Hospital\n",
    )
    fingerprint = compute_dashboard_input_fingerprint(reports)
    mirror_state = DashboardAutoMirrorState(
        last_mirrored_daily_core_generated_at=_DAILY_CORE_TS,
        last_successful_mirror_at=_T0.isoformat(),
        last_mirrored_dashboard_input_fingerprint=fingerprint,
    )
    state_path(reports).write_text(json.dumps(mirror_state.to_dict()), encoding="utf-8")

    run_dashboard_auto_mirror(_opts(), reports_dir=reports, now_fn=lambda: _T0)
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "already_mirrored"
    assert out["should_run"] == "false"


def test_compute_dashboard_input_fingerprint_changes_when_manifest_or_queue_changes(
    active_current: Path,
) -> None:
    reports = active_current.parent.parent
    empty_fp = compute_dashboard_input_fingerprint(reports)

    _write_equipment_queue_csv(
        active_current,
        filename="equipment_first_operator_queue_20260614.csv",
        content="codigo_licitacion,buyer\nA-1,Buyer\n",
    )
    queue_fp = compute_dashboard_input_fingerprint(reports)
    assert queue_fp != empty_fp

    _write_dashboard_manifest(
        active_current,
        canonical_files=["equipment_first_operator_queue_20260614.csv"],
    )
    manifest_fp = compute_dashboard_input_fingerprint(reports)
    assert manifest_fp != empty_fp

    _write_equipment_queue_csv(
        active_current,
        filename="equipment_first_operator_queue_20260614.csv",
        content="codigo_licitacion,buyer\nB-2,Buyer\n",
    )
    updated_fp = compute_dashboard_input_fingerprint(reports)
    assert updated_fp != manifest_fp


def test_missing_dashboard_manifest_fingerprint_is_stable(active_current: Path) -> None:
    reports = active_current.parent.parent
    first = compute_dashboard_input_fingerprint(reports)
    second = compute_dashboard_input_fingerprint(reports)
    assert first == second
    assert len(first) == 64
