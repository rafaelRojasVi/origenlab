"""Tests for ndr-safe-auto-apply dry-run and guarded apply."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from origenlab_email_pipeline.operator_cli.ndr_safe_auto_apply import (
    ALLOWLIST_BATCH_A_FILENAME,
    NDR_SAFE_AUTO_APPLY_AUDIT_FILENAME,
    NdrSafeAutoApplyOptions,
    build_ndr_safe_auto_apply_plan,
    build_targeted_ndr_apply_command,
    run_ndr_safe_auto_apply,
)
from origenlab_email_pipeline.operator_cli.parser import main
from origenlab_email_pipeline.qa.ndr_review_queue import APPLY_ONLY_CODE_BATCH_A


def _write_queue(
    active_current: Path,
    *,
    date_label: str,
    summary: dict[str, object],
    allowlist_emails: list[str] | None = None,
    include_allowlist: bool = True,
) -> Path:
    queue_dir = active_current / f"ndr_review_queue_{date_label}"
    queue_dir.mkdir(parents=True)
    (queue_dir / "ndr_review_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    if include_allowlist:
        lines = [
            "# SUGGESTED allowlist only — DO NOT APPLY WITHOUT OPERATOR APPROVAL",
            f"# Use with: --emails-file <this-file> --only-code {APPLY_ONLY_CODE_BATCH_A} --apply",
            "",
        ]
        for email in allowlist_emails or []:
            lines.append(email)
        (queue_dir / ALLOWLIST_BATCH_A_FILENAME).write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    return queue_dir


def _ready_summary(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "generated_at": "2026-06-11T21:43:08+00:00",
        "since_days": 1,
        "date_label": "2026_06_11",
        "candidates_total": 2,
        "candidates_already_suppressed": 0,
        "candidates_unsuppressed": 2,
        "batch_counts": {"A": 2, "B": 0, "C": 0, "D": 0, "E": 0},
    }
    base.update(overrides)
    return base


_FIXED_NOW = datetime(2026, 6, 11, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    active_current = tmp_path / "active" / "current"
    active_current.mkdir(parents=True)
    return tmp_path


def _read_audit_lines(active_current: Path) -> list[dict[str, object]]:
    audit_path = active_current / NDR_SAFE_AUTO_APPLY_AUDIT_FILENAME
    if not audit_path.is_file():
        return []
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _run_with_reports(
    reports_dir: Path,
    options: NdrSafeAutoApplyOptions,
    *,
    subprocess_run: MagicMock | None = None,
    rebuild_queue_fn: MagicMock | None = None,
) -> int:
    with patch(
        "origenlab_email_pipeline.operator_cli.ndr_safe_auto_apply.load_settings"
    ) as mock_settings:
        mock_settings.return_value.resolved_reports_dir.return_value = reports_dir
        kwargs: dict[str, object] = {"now_fn": lambda: _FIXED_NOW}
        if subprocess_run is not None:
            kwargs["subprocess_run"] = subprocess_run
        if rebuild_queue_fn is not None:
            kwargs["rebuild_queue_fn"] = rebuild_queue_fn
        return run_ndr_safe_auto_apply(options, **kwargs)


def _apply_options(reports_dir: Path, **kwargs: object) -> NdrSafeAutoApplyOptions:
    defaults: dict[str, object] = {
        "batch": "A",
        "dry_run": False,
        "apply": True,
        "confirm_reviewed": True,
        "operator": "rafael",
        "reports_dir": reports_dir,
    }
    defaults.update(kwargs)
    return NdrSafeAutoApplyOptions(**defaults)  # type: ignore[arg-type]


def _success_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")


def test_batch_a_dry_run_lists_candidates(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    queue_dir = _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(
            candidates_total=129,
            candidates_already_suppressed=53,
            candidates_unsuppressed=76,
            batch_counts={"A": 53, "B": 28, "C": 1, "D": 42, "E": 5},
        ),
        allowlist_emails=["alpha@example.cl", "beta@example.cl"],
    )
    plan, exit_code, _summary = build_ndr_safe_auto_apply_plan(
        NdrSafeAutoApplyOptions(batch="A", queue_dir=queue_dir, reports_dir=reports_dir),
        now_fn=lambda: _FIXED_NOW,
    )
    assert exit_code == 0
    assert plan["reason"] == "ready"
    assert plan["only_code"] == APPLY_ONLY_CODE_BATCH_A
    assert plan["allowlist_count"] == 2
    assert plan["emails"] == ["alpha@example.cl", "beta@example.cl"]
    assert plan["queue_dir"] == str(queue_dir.resolve())


def test_empty_batch_a_returns_no_op_success(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(
            candidates_total=5,
            candidates_already_suppressed=5,
            candidates_unsuppressed=0,
            batch_counts={"A": 5, "B": 0, "C": 0, "D": 0, "E": 0},
        ),
        allowlist_emails=[],
    )
    plan, exit_code, _ = build_ndr_safe_auto_apply_plan(
        NdrSafeAutoApplyOptions(batch="A", reports_dir=reports_dir),
        now_fn=lambda: _FIXED_NOW,
    )
    assert exit_code == 0
    assert plan["reason"] == "no_candidates"


@pytest.mark.parametrize("batch", ["B", "C", "D", "E"])
def test_unsupported_batches_refused_dry_run(reports_dir: Path, batch: str) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary={"generated_at": "2026-06-11T21:43:08+00:00", "candidates_total": 1},
        allowlist_emails=["x@example.cl"],
    )
    plan, exit_code, _ = build_ndr_safe_auto_apply_plan(
        NdrSafeAutoApplyOptions(batch=batch, reports_dir=reports_dir),  # type: ignore[arg-type]
        now_fn=lambda: _FIXED_NOW,
    )
    assert exit_code == 1
    assert plan["reason"] == "unsupported_batch"


@pytest.mark.parametrize("batch", ["B", "C", "D", "E"])
def test_unsupported_batches_apply_refused(reports_dir: Path, batch: str) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["x@example.cl"],
    )
    rc = _run_with_reports(
        reports_dir,
        _apply_options(reports_dir, batch=batch),  # type: ignore[arg-type]
    )
    assert rc == 1
    record = _read_audit_lines(active_current)[-1]
    assert record["reason"] == "unsupported_batch"
    assert record["applied"] is False
    assert record["dry_run"] is False


def test_missing_queue_dir_returns_error(reports_dir: Path) -> None:
    plan, exit_code, _ = build_ndr_safe_auto_apply_plan(
        NdrSafeAutoApplyOptions(batch="A", reports_dir=reports_dir),
        now_fn=lambda: _FIXED_NOW,
    )
    assert exit_code == 2
    assert plan["reason"] == "missing_queue"


def test_dry_run_writes_audit_record(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    queue_dir = _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["audit@example.cl"],
    )
    rc = _run_with_reports(
        reports_dir,
        NdrSafeAutoApplyOptions(batch="A", operator="rafael", reports_dir=reports_dir),
    )
    assert rc == 0
    record = _read_audit_lines(active_current)[0]
    assert record["dry_run"] is True
    assert record["applied"] is False
    assert record["reason"] == "ready"
    assert record["queue_dir"] == str(queue_dir.resolve())


def test_apply_refuses_without_operator(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["a@example.cl"],
    )
    rc = _run_with_reports(
        reports_dir,
        _apply_options(reports_dir, operator=None),
    )
    assert rc == 1
    record = _read_audit_lines(active_current)[-1]
    assert record["reason"] == "missing_operator"
    assert record["applied"] is False


def test_apply_refuses_without_confirm_reviewed(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["a@example.cl"],
    )
    rc = _run_with_reports(
        reports_dir,
        _apply_options(reports_dir, confirm_reviewed=False),
    )
    assert rc == 1
    assert _read_audit_lines(active_current)[-1]["reason"] == "missing_confirm_reviewed"


def test_apply_refuses_no_candidates(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(candidates_unsuppressed=0),
        allowlist_emails=[],
    )
    rc = _run_with_reports(reports_dir, _apply_options(reports_dir))
    assert rc == 1
    assert _read_audit_lines(active_current)[-1]["reason"] == "no_candidates"


def test_apply_refuses_max_apply_exceeded(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    emails = [f"user{i}@example.cl" for i in range(3)]
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(batch_counts={"A": 3, "B": 0, "C": 0, "D": 0, "E": 0}),
        allowlist_emails=emails,
    )
    rc = _run_with_reports(
        reports_dir,
        _apply_options(reports_dir, max_apply=2),
    )
    assert rc == 1
    assert _read_audit_lines(active_current)[-1]["reason"] == "max_apply_exceeded"


def test_apply_refuses_parser_uncertain_exceeded(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(batch_counts={"A": 1, "B": 0, "C": 0, "D": 0, "E": 11}),
        allowlist_emails=["a@example.cl"],
    )
    rc = _run_with_reports(reports_dir, _apply_options(reports_dir))
    assert rc == 1
    assert _read_audit_lines(active_current)[-1]["reason"] == "parser_uncertain_exceeded"


def test_refresh_safety_failure_records_partial_apply(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["partial@example.cl"],
    )

    def _ndr_ok_refresh_fails(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "refresh-safety" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="refresh failed\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    mock_subprocess = MagicMock(side_effect=_ndr_ok_refresh_fails)
    mock_rebuild = MagicMock()
    rc = _run_with_reports(
        reports_dir,
        _apply_options(reports_dir, json_output=True),
        subprocess_run=mock_subprocess,
        rebuild_queue_fn=mock_rebuild,
    )
    assert rc == 2
    mock_rebuild.assert_not_called()

    record = _read_audit_lines(active_current)[-1]
    assert record["reason"] == "refresh_safety_failed"
    assert record["applied"] is True
    assert record["refresh_safety_completed"] is False
    assert record["needs_safety_refresh"] is True
    assert record["exit_code"] == 2
    assert record["subprocess_results"]["ndr_apply"]["returncode"] == 0
    assert record["subprocess_results"]["refresh_safety"]["returncode"] == 1


def test_successful_apply_runs_targeted_ndr_refresh_and_rebuild(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    queue_dir = _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["apply@example.cl"],
    )
    allowlist_path = queue_dir / ALLOWLIST_BATCH_A_FILENAME
    mock_subprocess = MagicMock(side_effect=_success_subprocess)
    mock_rebuild = MagicMock(
        return_value={
            "out_dir": str(active_current / "ndr_review_queue_2026_06_12"),
            "allowlist_batch_a_count": 0,
        }
    )
    rc = _run_with_reports(
        reports_dir,
        _apply_options(reports_dir),
        subprocess_run=mock_subprocess,
        rebuild_queue_fn=mock_rebuild,
    )
    assert rc == 0
    assert mock_subprocess.call_count == 2

    ndr_argv = mock_subprocess.call_args_list[0].args[0]
    assert "--emails-file" in ndr_argv
    assert str(allowlist_path) in ndr_argv
    assert "--only-code" in ndr_argv
    assert APPLY_ONLY_CODE_BATCH_A in ndr_argv
    assert "--apply" in ndr_argv
    assert "--since-date-utc" in ndr_argv
    assert "2026-06-10" in ndr_argv
    assert "--since-days" not in ndr_argv

    refresh_argv = mock_subprocess.call_args_list[1].args[0]
    assert refresh_argv[-1] == "refresh-safety"

    mock_rebuild.assert_called_once_with(since_days=1)

    records = _read_audit_lines(active_current)
    assert len(records) == 2
    assert records[0]["phase"] == "before_apply"
    assert records[0]["applied"] is False
    assert records[1]["phase"] == "after_apply"
    assert records[1]["applied"] is True
    assert records[1]["dry_run"] is False
    assert records[1]["confirm_reviewed"] is True
    assert records[1]["exit_code"] == 0
    assert records[1]["emails"] == ["apply@example.cl"]


def test_targeted_ndr_apply_command_shape() -> None:
    cmd = build_targeted_ndr_apply_command(
        allowlist_path=Path("/tmp/apply_allowlist_batch_a.txt"),
        since_date_utc="2026-07-22",
    )
    assert "--emails-file" in cmd
    assert "--only-code" in cmd
    assert APPLY_ONLY_CODE_BATCH_A in cmd
    assert "--apply" in cmd
    assert "--since-date-utc" in cmd
    assert "2026-07-22" in cmd
    assert "--since-days" not in cmd


@patch("subprocess.run")
def test_dry_run_does_not_subprocess(
    mock_run: MagicMock,
    reports_dir: Path,
) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["safe@example.cl"],
    )
    rc = _run_with_reports(
        reports_dir,
        NdrSafeAutoApplyOptions(batch="A", reports_dir=reports_dir),
    )
    assert rc == 0
    mock_run.assert_not_called()


def test_cli_batch_a_dry_run_integration(reports_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["cli@example.cl"],
    )
    with patch(
        "origenlab_email_pipeline.operator_cli.ndr_safe_auto_apply.load_settings"
    ) as mock_settings, patch(
        "origenlab_email_pipeline.operator_cli.ndr_safe_auto_apply.datetime"
    ) as mock_datetime:
        mock_settings.return_value.resolved_reports_dir.return_value = reports_dir
        mock_datetime.now.return_value = _FIXED_NOW
        rc = main(["ndr-safe-auto-apply", "--batch", "A", "--dry-run", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True
    assert data["emails"] == ["cli@example.cl"]
    assert data["reason"] == "ready"


def test_cli_apply_refuses_without_confirm_reviewed(reports_dir: Path) -> None:
    active_current = reports_dir / "active" / "current"
    _write_queue(
        active_current,
        date_label="2026_06_11",
        summary=_ready_summary(),
        allowlist_emails=["a@example.cl"],
    )
    with patch(
        "origenlab_email_pipeline.operator_cli.ndr_safe_auto_apply.load_settings"
    ) as mock_settings:
        mock_settings.return_value.resolved_reports_dir.return_value = reports_dir
        rc = main(
            [
                "ndr-safe-auto-apply",
                "--batch",
                "A",
                "--apply",
                "--operator",
                "rafael",
            ]
        )
    assert rc == 1
