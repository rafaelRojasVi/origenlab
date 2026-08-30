"""Tests for read-only operator automation status command."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.operator_cli.chilecompra_auto_refresh import (
    LOCK_FILENAME as CHILECOMPRA_LOCK_FILENAME,
    STATE_FILENAME as CHILECOMPRA_STATE_FILENAME,
)
from origenlab_email_pipeline.operator_cli.daily_core_manifest import MANIFEST_FILENAME
from origenlab_email_pipeline.operator_cli.dashboard_auto_mirror import STATE_FILENAME as MIRROR_STATE_FILENAME
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import (
    STATE_FILENAME as MAIL_STATE_FILENAME,
    STALE_LOCK_SECONDS,
)
from origenlab_email_pipeline.operator_cli.operator_automation_status import (
    LEGACY_MIRROR_CRON_WRAPPER,
    TRACKED_CHILECOMPRA_CRON_SCRIPT,
    TRACKED_MAIL_CRON_SCRIPT,
    TRACKED_MIRROR_CRON_SCRIPT,
    OperatorAutomationStatusOptions,
    _inspect_crontab_content,
    build_operator_automation_status,
    format_operator_automation_status_text,
    read_user_crontab,
    run_operator_automation_status,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

_T0 = datetime(2026, 6, 10, 18, 30, 0, tzinfo=timezone.utc)
_DAILY_CORE_TS = "2026-06-10T18:12:48+00:00"
_MIRROR_TS = "2026-06-10T18:18:33+00:00"


@pytest.fixture
def active_current(tmp_path: Path) -> Path:
    path = tmp_path / "active" / "current"
    path.mkdir(parents=True)
    return path


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
    (active_current / MANIFEST_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


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
    (active_current / MAIL_STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_mirror_state(active_current: Path, **kwargs: object) -> None:
    payload = {
        "last_result": "success",
        "last_successful_mirror_at": _MIRROR_TS,
        "last_mirrored_daily_core_generated_at": _DAILY_CORE_TS,
        "consecutive_failures": 0,
        **kwargs,
    }
    (active_current / MIRROR_STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_chilecompra_state(active_current: Path, **kwargs: object) -> None:
    payload = {
        "last_result": "refreshed",
        "last_successful_refresh_at": _DAILY_CORE_TS,
        "last_successful_publish_at": _MIRROR_TS,
        "consecutive_failures": 0,
        "fetched_summaries": 10,
        "candidate_summaries": 3,
        "prefilter_skipped_summaries": 7,
        "detail_requests": 2,
        "detail_cache_hits": 1,
        "detail_error_count": 0,
        "output_rows": 1,
        "published_rows": 1,
        "published_queue": "equipment_first_operator_queue_20260610.csv",
        "candidate_audit": "chilecompra_equipment_candidate_audit_20260610.csv",
        "next_recommended_run_at": (_T0 + timedelta(hours=2)).isoformat(),
        **kwargs,
    }
    (active_current / CHILECOMPRA_STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def _healthy_fixture(active_current: Path) -> Path:
    _write_manifest(active_current)
    _write_mail_state(active_current)
    _write_mirror_state(active_current)
    return active_current.parent.parent


def _write_ndr_review_queue(active_current: Path, *, date_label: str, summary: dict[str, object]) -> None:
    queue_dir = active_current / f"ndr_review_queue_{date_label}"
    queue_dir.mkdir(parents=True)
    (queue_dir / "ndr_review_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _healthy_tracked_crontab() -> dict[str, Any]:
    return _inspect_crontab_content(
        "\n".join(
            [
                f"*/3 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MAIL_CRON_SCRIPT}",
                f"*/15 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MIRROR_CRON_SCRIPT}",
            ]
        )
    )


def _healthy_tracked_crontab_with_chilecompra() -> dict[str, Any]:
    return _inspect_crontab_content(
        "\n".join(
            [
                f"*/3 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MAIL_CRON_SCRIPT}",
                f"*/15 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MIRROR_CRON_SCRIPT}",
                f"12 8-20/2 * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_CHILECOMPRA_CRON_SCRIPT}",
            ]
        )
    )


def _crontab_from_lines(*lines: str) -> dict[str, Any]:
    return _inspect_crontab_content("\n".join(lines))


def test_healthy_state(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "healthy"
    assert report["recommended_action"] == "none"
    assert report["mail_auto_refresh"]["dirty"] is False
    assert report["dashboard_auto_mirror"]["mirror_matches_daily_core"] is True


def test_json_output_keys(active_current: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reports = _healthy_fixture(active_current)
    rc = run_operator_automation_status(
        OperatorAutomationStatusOptions(json_output=True),
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    for key in (
        "generated_at_utc",
        "active_current_dir",
        "verdict",
        "daily_core",
        "mail_auto_refresh",
        "dashboard_auto_mirror",
        "chilecompra_equipment_auto_refresh",
        "ndr_pending_review",
        "cron",
        "recommended_action",
        "warnings",
    ):
        assert key in data
    assert data["cron"]["inspected"] is True
    assert data["ndr_pending_review"]["queue_exists"] is False


def test_chilecompra_prefilter_skips_surface_in_operator_status(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current, prefilter_skipped_summaries=7)

    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )

    chilecompra = report["chilecompra_equipment_auto_refresh"]
    assert chilecompra["fetched_summaries"] == 10
    assert chilecompra["candidate_summaries"] == 3
    assert chilecompra["prefilter_skipped_summaries"] == 7
    assert chilecompra["detail_requests"] == 2


def test_healthy_with_pending_ndr_sets_review_recommended_action(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_ndr_review_queue(
        active_current,
        date_label="2026_06_11",
        summary={
            "generated_at": "2026-06-11T21:43:08+00:00",
            "since_days": 1,
            "date_label": "2026_06_11",
            "candidates_total": 129,
            "candidates_already_suppressed": 53,
            "candidates_unsuppressed": 76,
            "batch_counts": {"A": 53, "B": 28, "C": 1, "D": 42, "E": 5},
            "allowlist_batch_a_count": 18,
            "allowlist_batch_b_count": 14,
        },
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "healthy"
    assert report["recommended_action"] == "review_ndr_allowlists"
    ndr = report["ndr_pending_review"]
    assert ndr["pending_review"] is True
    assert ndr["allowlist_batch_a_count"] == 18
    assert ndr["allowlist_batch_b_count"] == 14
    assert ndr["batch_counts"]["D"] == 42
    assert ndr["batch_cde_count"] == 48


def test_missing_daily_core_manifest(active_current: Path) -> None:
    _write_mail_state(active_current)
    _write_mirror_state(active_current)
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "create_missing_state_by_running_dry_run"


def test_failed_daily_core_manifest(active_current: Path) -> None:
    _write_manifest(active_current, status="failed", returncode=1)
    _write_mail_state(active_current)
    _write_mirror_state(active_current)
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "blocked"
    assert report["recommended_action"] == "inspect_failed_daily_core"


def test_mail_dirty(active_current: Path) -> None:
    _write_manifest(active_current)
    _write_mail_state(active_current, dirty=True)
    _write_mirror_state(active_current)
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "wait_for_mail_quiet_window"


def test_mail_pending(active_current: Path) -> None:
    _write_manifest(active_current)
    _write_mail_state(active_current, pending_inbox_total=404)
    _write_mirror_state(active_current)
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "attention"
    assert report["mail_auto_refresh"]["pending"] is True


def test_mirror_behind_daily_core(active_current: Path) -> None:
    _write_manifest(active_current)
    _write_mail_state(active_current)
    _write_mirror_state(
        active_current,
        last_mirrored_daily_core_generated_at="2026-06-10T17:00:00+00:00",
        last_successful_mirror_at=(_T0 - timedelta(seconds=1200)).isoformat(),
    )
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "run_auto_mirror_dashboard"
    assert report["dashboard_auto_mirror"]["mirror_matches_daily_core"] is False


def test_mirror_cooldown_when_behind(active_current: Path) -> None:
    _write_manifest(active_current)
    _write_mail_state(active_current)
    _write_mirror_state(
        active_current,
        last_mirrored_daily_core_generated_at="2026-06-10T17:00:00+00:00",
        last_successful_mirror_at=(_T0 - timedelta(seconds=60)).isoformat(),
    )
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
        options=OperatorAutomationStatusOptions(mirror_cooldown_seconds=900),
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "wait_for_mirror_cooldown"
    assert report["dashboard_auto_mirror"]["cooldown_remaining_seconds"] > 0


def test_mirror_cooldown_healthy_when_already_mirrored(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_mirror_state(
        active_current,
        last_successful_mirror_at=(_T0 - timedelta(seconds=60)).isoformat(),
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        options=OperatorAutomationStatusOptions(mirror_cooldown_seconds=900),
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "healthy"
    assert report["dashboard_auto_mirror"]["cooldown_remaining_seconds"] > 0


def test_live_mail_lock(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    (active_current / "auto_refresh.lock").write_text(
        json.dumps({"pid": 12345, "started_at": _T0.isoformat()}),
        encoding="utf-8",
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        process_alive=lambda pid: pid == 12345,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "wait_for_running_mail_refresh"


def test_live_mirror_lock(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    (active_current / "dashboard_auto_mirror.lock").write_text(
        json.dumps({"pid": 99999, "started_at": _T0.isoformat()}),
        encoding="utf-8",
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        process_alive=lambda pid: pid == 99999,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "wait_for_running_mirror_refresh"


def test_malformed_mail_state(active_current: Path) -> None:
    _write_manifest(active_current)
    (active_current / MAIL_STATE_FILENAME).write_text("{not json", encoding="utf-8")
    _write_mirror_state(active_current)
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "blocked"
    assert report["recommended_action"] == "inspect_logs"


def test_consecutive_failures_blocked(active_current: Path) -> None:
    _write_manifest(active_current)
    _write_mail_state(active_current, consecutive_failures=3)
    _write_mirror_state(active_current)
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "blocked"


def test_pause_file(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    (active_current / "auto_refresh_paused").write_text("", encoding="utf-8")
    report = build_operator_automation_status(reports_dir=reports, now=_T0)
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "resume_or_leave_paused"


def test_healthy_with_tracked_cron_entries(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "healthy"
    assert report["recommended_action"] == "none"
    assert report["cron"]["mail_uses_tracked_script"] is True
    assert report["cron"]["mirror_uses_tracked_script"] is True


def test_missing_mail_cron_entry_attention(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=lambda: _crontab_from_lines(
            f"*/15 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MIRROR_CRON_SCRIPT}",
        ),
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "inspect_crontab"
    assert report["cron"]["mail_entry_present"] is False
    assert report["cron"]["mirror_entry_present"] is True


def test_missing_mirror_cron_entry_attention(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=lambda: _crontab_from_lines(
            f"*/3 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MAIL_CRON_SCRIPT}",
        ),
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "inspect_crontab"
    assert report["cron"]["mirror_entry_present"] is False


def test_legacy_runtime_wrapper_attention(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=lambda: _crontab_from_lines(
            f"*/3 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MAIL_CRON_SCRIPT}",
            f"*/15 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{LEGACY_MIRROR_CRON_WRAPPER}",
        ),
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "migrate_cron_to_tracked_scripts"
    assert report["cron"]["legacy_runtime_wrapper_present"] is True


def test_broken_joined_flags_attention(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=lambda: _crontab_from_lines(
            "*/3 * * * * uv run origenlab auto-refresh-mail --once--apply",
            f"*/15 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MIRROR_CRON_SCRIPT}",
        ),
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "fix_crontab_spacing"
    assert report["cron"]["broken_joined_flags"] is True


def test_crontab_unavailable_warning_not_blocked(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=lambda: {
            "inspected": True,
            "crontab_available": False,
            "mail_entry_present": False,
            "mirror_entry_present": False,
            "chilecompra_entry_present": False,
            "mail_uses_tracked_script": False,
            "mirror_uses_tracked_script": False,
            "chilecompra_uses_tracked_script": False,
            "legacy_runtime_wrapper_present": False,
            "broken_joined_flags": False,
            "warnings": ["crontab_command_unavailable"],
        },
    )
    assert report["verdict"] == "healthy"
    assert "crontab_command_unavailable" in report["warnings"]


def test_skip_cron_inspection_preserves_legacy_output(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        options=OperatorAutomationStatusOptions(skip_cron_inspection=True),
    )
    assert report["verdict"] == "healthy"
    assert report["cron"] == {"note": "not inspected by this command"}


def test_read_user_crontab_mocked_no_real_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = f"*/3 * * * * {TRACKED_MAIL_CRON_SCRIPT}\n"
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: object) -> _Result:
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr(
        "origenlab_email_pipeline.operator_cli.operator_automation_status.subprocess.run",
        fake_run,
    )
    data = read_user_crontab()
    assert calls == [["crontab", "-l"]]
    assert data["inspected"] is True
    assert data["mail_entry_present"] is True


def test_tracked_cron_wrapper_scripts_exist_and_contain_commands() -> None:
    mail_script = _REPO_ROOT / "scripts/operator/run_auto_refresh_mail.sh"
    mirror_script = _REPO_ROOT / "scripts/operator/run_auto_mirror_dashboard.sh"
    chilecompra_script = _REPO_ROOT / "scripts/operator/run_auto_refresh_chilecompra_equipment.sh"
    assert mail_script.is_file()
    assert mirror_script.is_file()
    assert chilecompra_script.is_file()
    mail_text = mail_script.read_text(encoding="utf-8")
    mirror_text = mirror_script.read_text(encoding="utf-8")
    chilecompra_text = chilecompra_script.read_text(encoding="utf-8")
    assert "auto-refresh-mail --once --apply" in mail_text
    assert "auto-mirror-dashboard" in mirror_text
    assert "--once" in mirror_text
    assert "--apply" in mirror_text
    assert "--allow-non-scratch-postgres" in mirror_text
    assert "ORIGENLAB_UV_BIN" in mail_text
    assert "ORIGENLAB_OPERATOR_NAME" in mirror_text
    assert "run --group gmail" in mail_text
    assert "run --group postgres" in mirror_text
    assert "auto-refresh-chilecompra-equipment" in chilecompra_text
    assert "--once" in chilecompra_text
    assert "--apply" in chilecompra_text
    assert "--publish-institution-prospects" in chilecompra_text
    # Legacy direct Postgres equipment writer defaults false now, but the
    # explicit negative flag is required so a future default change can't
    # silently reactivate it on the next cron tick.
    assert "--no-publish-read-model" in chilecompra_text
    assert "--group gmail" not in chilecompra_text
    assert "--group postgres" not in chilecompra_text
    for text in (mail_text, mirror_text, chilecompra_text):
        assert "ORIGENLAB_API_AUTH_TOKEN" not in text
        assert "client_secret" not in text.lower()


def test_healthy_report_includes_chilecompra_section_in_json(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    chilecompra = report["chilecompra_equipment_auto_refresh"]
    assert chilecompra["state_exists"] is True
    assert chilecompra["last_result"] == "refreshed"
    assert chilecompra["output_rows"] == 1
    assert chilecompra["next_run_due"] is False


def test_text_output_includes_chilecompra_section(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    text = format_operator_automation_status_text(report)
    assert "chilecompra_equipment_auto_refresh" in text
    assert "  last_result=refreshed" in text
    assert "  output_rows=1" in text


def test_malformed_chilecompra_state_blocks(active_current: Path) -> None:
    _write_manifest(active_current)
    _write_mail_state(active_current)
    _write_mirror_state(active_current)
    (active_current / CHILECOMPRA_STATE_FILENAME).write_text("{not json", encoding="utf-8")
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "blocked"
    assert report["recommended_action"] == "inspect_logs"
    assert report["chilecompra_equipment_auto_refresh"]["parse_error"] == "malformed"


def test_chilecompra_consecutive_failures_blocked(active_current: Path) -> None:
    _write_manifest(active_current)
    _write_mail_state(active_current)
    _write_mirror_state(active_current)
    _write_chilecompra_state(active_current, consecutive_failures=3)
    report = build_operator_automation_status(
        reports_dir=active_current.parent.parent,
        now=_T0,
    )
    assert report["verdict"] == "blocked"
    assert report["recommended_action"] == "inspect_logs"


def test_live_chilecompra_lock_gives_attention(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current)
    (active_current / CHILECOMPRA_LOCK_FILENAME).write_text(
        json.dumps({"pid": 42424, "started_at": _T0.isoformat()}),
        encoding="utf-8",
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        process_alive=lambda pid: pid == 42424,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "wait_for_running_chilecompra_refresh"
    assert report["chilecompra_equipment_auto_refresh"]["lock_live"] is True


def test_stale_chilecompra_lock_blocks(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current)
    stale_started = (_T0 - timedelta(seconds=STALE_LOCK_SECONDS + 60)).isoformat()
    (active_current / CHILECOMPRA_LOCK_FILENAME).write_text(
        json.dumps({"pid": 42424, "started_at": stale_started}),
        encoding="utf-8",
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        process_alive=lambda pid: pid == 42424,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "blocked"
    assert report["recommended_action"] == "clear_stale_lock_after_manual_review"
    assert "stale_chilecompra_lock_detected" in report["warnings"]


def test_due_chilecompra_refresh_recommends_run_without_breaking_mail_mirror(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(
        active_current,
        next_recommended_run_at=(_T0 - timedelta(minutes=30)).isoformat(),
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "run_auto_refresh_chilecompra_equipment"
    assert "chilecompra_refresh_due" in report["warnings"]
    assert report["mail_auto_refresh"]["dirty"] is False
    assert report["dashboard_auto_mirror"]["mirror_matches_daily_core"] is True
    assert report["chilecompra_equipment_auto_refresh"]["next_run_due"] is True



def test_chilecompra_between_slots_remains_healthy(active_current: Path) -> None:
    """Between valid daytime slots, next_run_due must stay false (no false attention)."""
    reports = _healthy_fixture(active_current)
    now = datetime(2026, 6, 15, 15, 0, 0, tzinfo=timezone.utc)  # 11:00 Santiago winter
    _write_chilecompra_state(
        active_current,
        last_successful_refresh_at=datetime(2026, 6, 15, 12, 14, 0, tzinfo=timezone.utc).isoformat(),
        last_successful_refresh_started_at=datetime(2026, 6, 15, 12, 12, 3, tzinfo=timezone.utc).isoformat(),
        last_successful_scheduled_slot_at=datetime(2026, 6, 15, 12, 12, 0, tzinfo=timezone.utc).isoformat(),
        cadence_anchor_kind="scheduled_slot",
        next_recommended_run_at=datetime(2026, 6, 15, 16, 12, 0, tzinfo=timezone.utc).isoformat(),  # 12:12 Santiago
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=now,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    chilecompra = report["chilecompra_equipment_auto_refresh"]
    assert chilecompra["next_run_due"] is False
    assert chilecompra["cadence_anchor_kind"] == "scheduled_slot"
    assert chilecompra["last_successful_scheduled_slot_at"] is not None
    assert chilecompra["last_successful_refresh_started_at"] is not None
    assert report["verdict"] == "healthy"
    assert "chilecompra_refresh_due" not in report["warnings"]


def test_chilecompra_overnight_before_0812_remains_healthy(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    # After 20:12 success, next is next-day 08:12; overnight 02:00 local must stay healthy.
    now = datetime(2026, 6, 16, 6, 0, 0, tzinfo=timezone.utc)  # 02:00 Santiago
    _write_chilecompra_state(
        active_current,
        last_successful_refresh_at=datetime(2026, 6, 16, 0, 14, 0, tzinfo=timezone.utc).isoformat(),
        last_successful_refresh_started_at=datetime(2026, 6, 16, 0, 12, 0, tzinfo=timezone.utc).isoformat(),
        last_successful_scheduled_slot_at=datetime(2026, 6, 16, 0, 12, 0, tzinfo=timezone.utc).isoformat(),
        cadence_anchor_kind="scheduled_slot",
        next_recommended_run_at=datetime(2026, 6, 16, 12, 12, 0, tzinfo=timezone.utc).isoformat(),  # 08:12
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=now,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    assert report["chilecompra_equipment_auto_refresh"]["next_run_due"] is False
    assert report["verdict"] == "healthy"
    assert "chilecompra_refresh_due" not in report["warnings"]


def test_missed_chilecompra_scheduled_slot_still_attention(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    now = datetime(2026, 6, 15, 16, 30, 0, tzinfo=timezone.utc)  # past 12:12 Santiago
    _write_chilecompra_state(
        active_current,
        last_successful_refresh_at=datetime(2026, 6, 15, 12, 14, 0, tzinfo=timezone.utc).isoformat(),
        last_successful_refresh_started_at=datetime(2026, 6, 15, 12, 12, 3, tzinfo=timezone.utc).isoformat(),
        last_successful_scheduled_slot_at=datetime(2026, 6, 15, 12, 12, 0, tzinfo=timezone.utc).isoformat(),
        cadence_anchor_kind="scheduled_slot",
        next_recommended_run_at=datetime(2026, 6, 15, 16, 12, 0, tzinfo=timezone.utc).isoformat(),
    )
    report = build_operator_automation_status(
        reports_dir=reports,
        now=now,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "run_auto_refresh_chilecompra_equipment"
    assert "chilecompra_refresh_due" in report["warnings"]
    assert report["chilecompra_equipment_auto_refresh"]["next_run_due"] is True


def test_missing_chilecompra_state_remains_non_blocking(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "healthy"
    assert report["chilecompra_equipment_auto_refresh"]["state_exists"] is False
    assert report["chilecompra_equipment_auto_refresh"]["parse_error"] == "missing"


def test_crontab_with_mail_mirror_chilecompra_scripts_is_healthy(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    assert report["verdict"] == "healthy"
    assert report["recommended_action"] == "none"
    assert report["cron"]["chilecompra_entry_present"] is True
    assert report["cron"]["chilecompra_uses_tracked_script"] is True


def test_missing_chilecompra_cron_when_state_exists(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "attention"
    assert report["recommended_action"] == "install_chilecompra_cron"
    assert report["cron"]["chilecompra_entry_present"] is False
    assert "chilecompra_cron_missing" in report["warnings"]


def test_missing_chilecompra_cron_not_blocking_without_state(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab,
    )
    assert report["verdict"] == "healthy"
    assert report["recommended_action"] == "none"
    assert report["cron"]["chilecompra_entry_present"] is False
    assert "chilecompra_cron_missing" not in report["warnings"]


def test_wrong_chilecompra_cron_detected_not_tracked(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=lambda: _crontab_from_lines(
            f"*/3 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MAIL_CRON_SCRIPT}",
            f"*/15 * * * * /home/rafael/dev/freelance/origenlab/apps/email-pipeline/{TRACKED_MIRROR_CRON_SCRIPT}",
            "12 8-20/2 * * * uv run origenlab auto-refresh-chilecompra-equipment --once --apply",
        ),
    )
    assert report["cron"]["chilecompra_entry_present"] is True
    assert report["cron"]["chilecompra_uses_tracked_script"] is False
    assert report["verdict"] == "healthy"
    assert report["recommended_action"] == "none"


# --- Institution-prospect publication observability (W2) ---


def _write_institution_prospect_bundle(
    active_current: Path,
    *,
    as_of_utc: str,
    contract_version: str = "institution_prospect_contract_v4",
    current_opportunity_rows: int = 2,
) -> None:
    import csv as _csv

    from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
        OPERATOR_QUEUE_NAMES as _QUEUE_NAMES,
    )
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.production_publish import (
        INSTITUTION_PROSPECTS_DIRNAME,
    )
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
        EMPTY_QUEUE_HEADERS,
    )

    dest = active_current / INSTITUTION_PROSPECTS_DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    packet = {
        "ok": True,
        "as_of_utc": as_of_utc,
        "run_context": "production_apply",
        "planner_version": "procurement_institution_prospect_planner_v4",
        "recognition_layer_version": "procurement_prospect_recognition_pr5e2_v1",
        "contract_version": contract_version,
        "not_persisted": True,
        "contact_authorization": False,
        "outreach_authorization": False,
        "profiles": [{"institution_id": f"inst-{i}"} for i in range(5)],
        "counts": {"institution_count": 5},
        "fingerprints": {"build_fingerprint": "digest-xyz"},
    }
    (dest / "institution_prospect_packet.json").write_text(json.dumps(packet), encoding="utf-8")
    sizes = {name: 0 for name in _QUEUE_NAMES}
    sizes["current_opportunity_queue"] = current_opportunity_rows
    (dest / "summary.json").write_text(
        json.dumps({"ok": True, "contract_version": contract_version, "operator_queue_sizes": sizes}),
        encoding="utf-8",
    )
    for queue_name in _QUEUE_NAMES:
        rows: list[dict[str, object]] = []
        if queue_name == "current_opportunity_queue":
            rows = [{"queue_row_id": f"r{i}"} for i in range(current_opportunity_rows)]
        header = EMPTY_QUEUE_HEADERS[queue_name]
        with (dest / f"{queue_name}.csv").open("w", encoding="utf-8", newline="") as fh:
            writer = _csv.DictWriter(fh, fieldnames=header, extrasaction="ignore", restval="")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def test_institution_prospect_missing_bundle_reported_cleanly_pre_activation(
    active_current: Path,
) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current, institution_prospect_result="disabled")
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    section = report["institution_prospect"]
    assert section["bundle_exists"] is False
    assert section["bundle_valid"] is None
    assert section["parse_error"] is None
    assert section["last_publish_result"] == "disabled"
    # A bundle simply not existing yet (pre-activation) must never block or
    # degrade the rest of operator-automation-status.
    assert report["verdict"] == "healthy"
    assert "institution_prospect_publication_failed" not in report["warnings"]


def test_institution_prospect_healthy_valid_bundle(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(
        active_current,
        institution_prospect_result="applied",
        last_successful_institution_prospect_publish_at=_T0.isoformat(),
    )
    _write_institution_prospect_bundle(active_current, as_of_utc=(_T0 - timedelta(hours=1)).isoformat())
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    section = report["institution_prospect"]
    assert section["bundle_exists"] is True
    assert section["bundle_valid"] is True
    assert section["contract_version"] == "institution_prospect_contract_v4"
    assert section["supported_contract_version"] is True
    assert section["stale"] is False
    assert section["current_opportunity_count"] == 2
    assert section["operator_queue_sizes"]["current_opportunity_queue"] == 2
    assert section["last_publish_result"] == "applied"
    assert report["verdict"] == "healthy"


def test_institution_prospect_stale_bundle_flagged(active_current: Path) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current, institution_prospect_result="applied")
    old_as_of = (_T0 - timedelta(hours=72)).isoformat()  # older than the 48h threshold
    _write_institution_prospect_bundle(active_current, as_of_utc=old_as_of)
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    section = report["institution_prospect"]
    assert section["bundle_valid"] is True
    assert section["stale"] is True
    # Staleness alone is informational here, not an escalation (mirrors the
    # W1 API's own stale != unavailable distinction) — conservative by design.
    assert "institution_prospect_publication_failed" not in report["warnings"]


def test_institution_prospect_failed_requested_publication_is_attention(
    active_current: Path,
) -> None:
    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current, institution_prospect_result="failed")
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    assert report["institution_prospect"]["last_publish_result"] == "failed"
    assert "institution_prospect_publication_failed" in report["warnings"]
    assert report["verdict"] == "attention"


def test_institution_prospect_malformed_bundle_reported_not_crashing(active_current: Path) -> None:
    from origenlab_email_pipeline.commercial_procurement_institution_prospects.production_publish import (
        INSTITUTION_PROSPECTS_DIRNAME,
    )

    reports = _healthy_fixture(active_current)
    _write_chilecompra_state(active_current, institution_prospect_result="applied")
    dest = active_current / INSTITUTION_PROSPECTS_DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "institution_prospect_packet.json").write_text("{not valid json", encoding="utf-8")
    report = build_operator_automation_status(
        reports_dir=reports,
        now=_T0,
        read_crontab=_healthy_tracked_crontab_with_chilecompra,
    )
    section = report["institution_prospect"]
    assert section["bundle_exists"] is True
    assert section["bundle_valid"] is False
    assert section["parse_error"] == "malformed"
