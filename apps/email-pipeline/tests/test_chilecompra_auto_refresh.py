"""Tests for ChileCompra equipment auto-refresh operator command (mocked build/publish)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from origenlab_email_pipeline.chilecompra_api import TICKET_ENV_VAR
from origenlab_email_pipeline.operator_cli.chilecompra_auto_refresh import (
    CADENCE_KIND_SCHEDULED_SLOT,
    CADENCE_KIND_WALL_CLOCK,
    CHILECOMPRA_SCHEDULE_TZ,
    DEFAULT_COOLDOWN_SECONDS,
    ChilecompraEquipmentAutoRefreshOptions,
    ChilecompraEquipmentAutoRefreshState,
    apply_successful_chilecompra_cadence,
    compute_chilecompra_next_recommended_run_at,
    evaluate_chilecompra_equipment_auto_refresh,
    load_state,
    next_chilecompra_scheduled_slot,
    resolve_chilecompra_cadence_anchor,
    run_chilecompra_equipment_auto_refresh,
    save_state,
    state_path,
)
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import acquire_lock

_SECRET_TICKET = "00000000-0000-0000-0000-000000000099"
_T0 = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _santiago(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=CHILECOMPRA_SCHEDULE_TZ)


def _opts(**kwargs: object) -> ChilecompraEquipmentAutoRefreshOptions:
    return ChilecompraEquipmentAutoRefreshOptions(once=True, **kwargs)


def _parse_output(out: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in out.strip().splitlines():
        if "=" in line and line != "chilecompra_equipment_auto_refresh":
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _mock_build_result() -> tuple[list[dict[str, str]], dict[str, object], list[dict[str, str]]]:
    rows = [
        {
            "codigo_licitacion": "1051-1-LP26",
            "buyer": "Hospital Demo",
            "region": "RM",
            "close_date": "2026-06-17T19:00:00",
            "title": "Centrifuga",
            "item_description": "Centrifuga refrigerada",
            "equipment_category": "centrifuge",
            "fit_score": "85",
            "reason": "source:chilecompra_api; equipment:centrifuge",
            "next_action": "quote_now",
            "validity_status": "open",
            "chilecompra_status_code": "5",
            "chilecompra_status": "Publicada",
            "api_checked_at_utc": _T0.isoformat(),
            "source": "chilecompra_api",
        }
    ]
    manifest = {
        "fetched_summaries": 10,
        "candidate_summaries": 3,
        "prefilter_skipped_summaries": 7,
        "detail_requests": 2,
        "detail_cache_hits": 1,
        "detail_error_count": 0,
        "output_rows": 1,
    }
    audit_rows = [{"codigo": "1051-1-LP26", "prefilter_match": "true"}]
    return rows, manifest, audit_rows


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_dry_run_does_not_write_publish_output(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = MagicMock()
    publish = MagicMock()
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)

    run_chilecompra_equipment_auto_refresh(
        _opts(apply=False),
        reports_dir=reports_dir,
        build_fn=build,
        publish_fn=publish,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["apply"] == "false"
    assert out["reason"] == "dry_run"
    assert out["ran_refresh"] == "false"
    assert out["published"] == "false"
    build.assert_not_called()
    publish.assert_not_called()
    assert not list((reports_dir / "active" / "current").glob("equipment_first_operator_queue_*.csv"))


def test_apply_writes_state_and_calls_mocked_build_publish(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    publish = MagicMock(
        return_value={
            "out_csv": str(reports_dir / "active/current/equipment_first_operator_queue_20260614.csv"),
            "output_rows": 1,
            "coalesced_duplicate_rows": 0,
            "unique_codigo_count": 1,
        }
    )

    rc = run_chilecompra_equipment_auto_refresh(
        _opts(apply=True),
        reports_dir=reports_dir,
        build_fn=lambda **kwargs: _mock_build_result(),
        publish_fn=publish,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "refreshed"
    assert out["ran_refresh"] == "true"
    assert out["published"] == "true"
    assert out["output_rows"] == "1"
    assert out["published_rows"] == "1"
    assert out["detail_requests"] == "2"
    assert out["detail_cache_hits"] == "1"
    assert out["prefilter_skipped_summaries"] == "7"
    publish.assert_called_once()

    state = load_state(state_path(reports_dir))
    assert state.last_result == "refreshed"
    assert state.fetched_summaries == 10
    assert state.candidate_summaries == 3
    assert state.prefilter_skipped_summaries == 7
    assert state.output_rows == 1
    assert state.published_rows == 1
    assert state.next_recommended_run_at is not None
    assert state.last_successful_refresh_started_at is not None
    assert state.cadence_anchor_kind in {CADENCE_KIND_SCHEDULED_SLOT, CADENCE_KIND_WALL_CLOCK}
    assert state.schema_version == 2
    assert _SECRET_TICKET not in json.dumps(state.to_dict())


def test_missing_ticket_gives_clean_failure(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TICKET_ENV_VAR, raising=False)
    rc = run_chilecompra_equipment_auto_refresh(
        _opts(apply=True),
        reports_dir=reports_dir,
        build_fn=MagicMock(),
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert rc == 2
    assert out["reason"] == "ticket_missing"
    state = load_state(state_path(reports_dir))
    assert state.consecutive_failures == 1
    assert _SECRET_TICKET not in (state.last_error or "")


def test_lock_live_skips(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "origenlab_email_pipeline.operator_cli.chilecompra_auto_refresh._lock_is_live",
        lambda _lock: True,
    )
    run_chilecompra_equipment_auto_refresh(
        _opts(apply=True),
        reports_dir=reports_dir,
        build_fn=MagicMock(side_effect=AssertionError("build must not run")),
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "lock_live"
    assert out["ran_refresh"] == "false"


def test_cooldown_skips_when_recent_success_and_no_force() -> None:
    state = ChilecompraEquipmentAutoRefreshState(
        last_successful_refresh_at=(_T0 - timedelta(seconds=60)).isoformat(),
    )
    result = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS),
        state=state,
        now=_T0,
    )
    assert result.reason == "cooldown"
    assert result.should_run is False


def test_force_bypasses_cooldown() -> None:
    state = ChilecompraEquipmentAutoRefreshState(
        last_successful_refresh_at=(_T0 - timedelta(seconds=60)).isoformat(),
    )
    result = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True, force=True),
        state=state,
        now=_T0,
    )
    assert result.reason == "ready"
    assert result.should_run is True


def test_build_failure_increments_failures_and_redacts_ticket(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)

    def _fail(**_kwargs: object) -> tuple[list[dict[str, str]], dict[str, object], list[dict[str, str]]]:
        raise RuntimeError(f"boom ticket={_SECRET_TICKET}")

    rc = run_chilecompra_equipment_auto_refresh(
        _opts(apply=True),
        reports_dir=reports_dir,
        build_fn=_fail,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert rc == 1
    assert out["reason"] == "build_failed"
    state = load_state(state_path(reports_dir))
    assert state.consecutive_failures == 1
    assert _SECRET_TICKET not in (state.last_error or "")
    assert "<redacted>" in (state.last_error or "")


def test_apply_without_publish_skips_publish_call(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    publish = MagicMock()

    run_chilecompra_equipment_auto_refresh(
        _opts(apply=True, publish=False),
        reports_dir=reports_dir,
        build_fn=lambda **kwargs: _mock_build_result(),
        publish_fn=publish,
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["published"] == "false"
    publish.assert_not_called()


def test_live_lock_prevents_overlap(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    monkeypatch.setattr(
        "origenlab_email_pipeline.operator_cli.chilecompra_auto_refresh._lock_is_live",
        lambda _lock: False,
    )
    monkeypatch.setattr(
        "origenlab_email_pipeline.operator_cli.mail_auto_refresh._process_alive",
        lambda pid: True,
    )
    active = reports_dir / "active" / "current"
    active.mkdir(parents=True)
    acquire_lock(active / "chilecompra_equipment_auto_refresh.lock", now=_T0)

    run_chilecompra_equipment_auto_refresh(
        _opts(apply=True),
        reports_dir=reports_dir,
        build_fn=MagicMock(side_effect=AssertionError("build must not run")),
        now_fn=lambda: _T0,
    )
    out = _parse_output(capsys.readouterr().out)
    assert out["reason"] == "lock_live"


def test_cron_startup_seconds_do_not_cause_four_hour_skip() -> None:
    """Finish a few minutes after 08:12 must recommend 10:12, not ~12:14."""
    started = _santiago(2026, 6, 15, 8, 12, 3)
    finished = _santiago(2026, 6, 15, 8, 14, 0)
    state = ChilecompraEquipmentAutoRefreshState()
    apply_successful_chilecompra_cadence(
        state,
        started_at=started,
        finished_at=finished,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    assert state.cadence_anchor_kind == CADENCE_KIND_SCHEDULED_SLOT
    assert state.last_successful_scheduled_slot_at == _santiago(2026, 6, 15, 8, 12).astimezone(
        timezone.utc
    ).replace(microsecond=0).isoformat()
    expected_next = _santiago(2026, 6, 15, 10, 12).astimezone(timezone.utc).replace(microsecond=0)
    assert state.next_recommended_run_at == expected_next.isoformat()

    # Next cron tick at 10:12 must be eligible (not skipped as early).
    at_next_slot = _santiago(2026, 6, 15, 10, 12, 5)
    result = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True),
        state=state,
        now=at_next_slot,
    )
    assert result.reason == "ready"
    assert result.should_run is True


def test_exactly_five_minutes_late_still_snaps_to_scheduled_slot() -> None:
    started = _santiago(2026, 6, 15, 8, 17, 0)  # exactly +300s after 08:12
    finished = _santiago(2026, 6, 15, 8, 17, 30)
    anchor, kind, slot = resolve_chilecompra_cadence_anchor(started)
    assert kind == CADENCE_KIND_SCHEDULED_SLOT
    assert slot == _santiago(2026, 6, 15, 8, 12).astimezone(timezone.utc)
    assert anchor == slot
    next_at = compute_chilecompra_next_recommended_run_at(
        cadence_anchor=anchor,
        finished_at=finished,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    assert next_at == _santiago(2026, 6, 15, 10, 12).astimezone(timezone.utc)


def test_just_beyond_five_minutes_uses_wall_clock_and_later_slot() -> None:
    started = _santiago(2026, 6, 15, 8, 17, 1)  # +301s — beyond snap tolerance
    finished = _santiago(2026, 6, 15, 8, 17, 30)
    anchor, kind, slot = resolve_chilecompra_cadence_anchor(started)
    assert kind == CADENCE_KIND_WALL_CLOCK
    assert slot is None
    assert anchor == started.astimezone(timezone.utc)
    next_at = compute_chilecompra_next_recommended_run_at(
        cadence_anchor=anchor,
        finished_at=finished,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    # max(08:17:01+2h, finish) = 10:17:01 → next canonical slot 12:12
    assert next_at == _santiago(2026, 6, 15, 12, 12).astimezone(timezone.utc)


def test_cooldown_before_exact_due_boundary() -> None:
    next_due = _santiago(2026, 6, 15, 10, 12)
    state = ChilecompraEquipmentAutoRefreshState(
        next_recommended_run_at=next_due.astimezone(timezone.utc).isoformat(),
        last_successful_refresh_at=_santiago(2026, 6, 15, 8, 14).astimezone(timezone.utc).isoformat(),
    )
    one_second_early = next_due - timedelta(seconds=1)
    blocked = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True),
        state=state,
        now=one_second_early,
    )
    assert blocked.reason == "cooldown"
    assert blocked.should_run is False

    at_due = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True),
        state=state,
        now=next_due,
    )
    assert at_due.reason == "ready"
    assert at_due.should_run is True


def test_final_2012_slot_rolls_to_next_day_0812() -> None:
    finished = _santiago(2026, 6, 15, 20, 14, 0)
    next_at = compute_chilecompra_next_recommended_run_at(
        cadence_anchor=_santiago(2026, 6, 15, 20, 12),
        finished_at=finished,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    expected = _santiago(2026, 6, 16, 8, 12).astimezone(timezone.utc)
    assert next_at == expected


def test_america_santiago_dst_safe_slot_conversion() -> None:
    local = _santiago(2026, 9, 7, 8, 12, 0)
    utc_slot = local.astimezone(timezone.utc)
    assert next_chilecompra_scheduled_slot(utc_slot) == utc_slot
    assert next_chilecompra_scheduled_slot(utc_slot - timedelta(seconds=1)) == utc_slot

    winter = _santiago(2026, 6, 15, 8, 12)
    summerish = _santiago(2026, 1, 15, 8, 12)
    assert winter.utcoffset() != summerish.utcoffset()
    assert next_chilecompra_scheduled_slot(winter.astimezone(timezone.utc)) == winter.astimezone(
        timezone.utc
    )


def test_off_slot_manual_success_uses_wall_clock_anchor() -> None:
    started = _santiago(2026, 6, 15, 9, 0, 0)
    finished = _santiago(2026, 6, 15, 9, 2, 0)
    anchor, kind, slot = resolve_chilecompra_cadence_anchor(started)
    assert kind == CADENCE_KIND_WALL_CLOCK
    assert slot is None
    assert anchor == started.astimezone(timezone.utc)

    next_at = compute_chilecompra_next_recommended_run_at(
        cadence_anchor=anchor,
        finished_at=finished,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    # max(09:00+2h, 09:02) = 11:00 → next canonical slot 12:12
    assert next_at == _santiago(2026, 6, 15, 12, 12).astimezone(timezone.utc)


def test_long_running_job_advances_past_next_slot() -> None:
    finished = _santiago(2026, 6, 15, 10, 30, 0)
    next_at = compute_chilecompra_next_recommended_run_at(
        cadence_anchor=_santiago(2026, 6, 15, 8, 12),
        finished_at=finished,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    assert next_at == _santiago(2026, 6, 15, 12, 12).astimezone(timezone.utc)


def test_force_bypasses_scheduled_next_recommended() -> None:
    next_due = _santiago(2026, 6, 15, 12, 12)
    state = ChilecompraEquipmentAutoRefreshState(
        next_recommended_run_at=next_due.astimezone(timezone.utc).isoformat(),
        last_successful_refresh_at=_santiago(2026, 6, 15, 10, 14).astimezone(timezone.utc).isoformat(),
    )
    result = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True, force=True),
        state=state,
        now=_santiago(2026, 6, 15, 10, 30),
    )
    assert result.reason == "ready"
    assert result.should_run is True


def test_legacy_state_without_next_recommended_uses_elapsed_fallback() -> None:
    state = ChilecompraEquipmentAutoRefreshState(
        last_successful_refresh_at=(_T0 - timedelta(seconds=60)).isoformat(),
        next_recommended_run_at=None,
    )
    result = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS),
        state=state,
        now=_T0,
    )
    assert result.reason == "cooldown"
    assert result.should_run is False


def test_legacy_naive_next_recommended_does_not_raise() -> None:
    """Persisted naive next_recommended_run_at must evaluate without TypeError."""
    state = ChilecompraEquipmentAutoRefreshState(
        next_recommended_run_at="2026-06-15T14:12:00",  # naive (no offset)
        last_successful_refresh_at="2026-06-15T12:14:00",
    )
    early = datetime(2026, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
    blocked = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True),
        state=state,
        now=early,
    )
    assert blocked.reason == "cooldown"
    assert blocked.should_run is False

    at_due = evaluate_chilecompra_equipment_auto_refresh(
        options=_opts(apply=True),
        state=state,
        now=datetime(2026, 6, 15, 14, 12, 0, tzinfo=timezone.utc),
    )
    assert at_due.reason == "ready"
    assert at_due.should_run is True


def test_legacy_state_file_loads_missing_cadence_fields(tmp_path: Path) -> None:
    path = tmp_path / "chilecompra_equipment_auto_refresh_state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "last_result": "refreshed",
                "last_successful_refresh_at": "2026-06-15T12:14:00+00:00",
                "next_recommended_run_at": "2026-06-15T14:14:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state.last_successful_refresh_started_at is None
    assert state.last_successful_scheduled_slot_at is None
    assert state.cadence_anchor_kind is None
    assert state.next_recommended_run_at == "2026-06-15T14:14:00+00:00"


def test_cooldown_skip_does_not_advance_success_anchor(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    success_started = _santiago(2026, 6, 15, 8, 12, 3)
    success_finished = _santiago(2026, 6, 15, 8, 14, 0)
    state = ChilecompraEquipmentAutoRefreshState()
    apply_successful_chilecompra_cadence(
        state,
        started_at=success_started,
        finished_at=success_finished,
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    next_before = state.next_recommended_run_at
    slot_before = state.last_successful_scheduled_slot_at
    kind_before = state.cadence_anchor_kind
    success_at_before = state.last_successful_refresh_at
    save_state(state_path(reports_dir), state)

    early_tick = _santiago(2026, 6, 15, 10, 0, 0)
    rc = run_chilecompra_equipment_auto_refresh(
        _opts(apply=True),
        reports_dir=reports_dir,
        build_fn=MagicMock(side_effect=AssertionError("build must not run on cooldown")),
        now_fn=lambda: early_tick,
    )
    out = _parse_output(capsys.readouterr().out)
    assert rc == 0
    assert out["reason"] == "cooldown"
    reloaded = load_state(state_path(reports_dir))
    assert reloaded.next_recommended_run_at == next_before
    assert reloaded.last_successful_scheduled_slot_at == slot_before
    assert reloaded.cadence_anchor_kind == kind_before
    assert reloaded.last_successful_refresh_at == success_at_before
    assert reloaded.last_result == "cooldown"


def test_build_failure_preserves_prior_success_cadence_anchor(
    reports_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TICKET_ENV_VAR, _SECRET_TICKET)
    state = ChilecompraEquipmentAutoRefreshState()
    apply_successful_chilecompra_cadence(
        state,
        started_at=_santiago(2026, 6, 15, 8, 12),
        finished_at=_santiago(2026, 6, 15, 8, 14),
        cooldown_seconds=DEFAULT_COOLDOWN_SECONDS,
    )
    next_before = state.next_recommended_run_at
    slot_before = state.last_successful_scheduled_slot_at
    success_at_before = state.last_successful_refresh_at
    save_state(state_path(reports_dir), state)

    def _fail(**_kwargs: object) -> tuple[list[dict[str, str]], dict[str, object], list[dict[str, str]]]:
        raise RuntimeError("api down")

    due = _santiago(2026, 6, 15, 10, 12, 5)
    rc = run_chilecompra_equipment_auto_refresh(
        _opts(apply=True),
        reports_dir=reports_dir,
        build_fn=_fail,
        now_fn=lambda: due,
    )
    out = _parse_output(capsys.readouterr().out)
    assert rc == 1
    assert out["reason"] == "build_failed"
    reloaded = load_state(state_path(reports_dir))
    assert reloaded.next_recommended_run_at == next_before
    assert reloaded.last_successful_scheduled_slot_at == slot_before
    assert reloaded.last_successful_refresh_at == success_at_before
    assert reloaded.consecutive_failures == 1

