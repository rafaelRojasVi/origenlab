"""Dry-run and guarded Batch A apply for safe NDR auto-apply."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.mart_core_postgres_migrate import resolve_sqlite_path
from origenlab_email_pipeline.operator_cli.paths import repo_root
from origenlab_email_pipeline.qa.ndr_pending_review_status import (
    NDR_REVIEW_SUMMARY_FILENAME,
    find_latest_ndr_review_queue_dir,
    load_ndr_review_summary_json,
)
from origenlab_email_pipeline.qa.ndr_review_queue import (
    APPLY_ONLY_CODE_BATCH_A,
    BatchName,
    build_ndr_review_queue,
    default_date_label,
    default_out_dir,
    resolve_reviewed_ndr_scan_start_date_utc,
)

SUPPORTED_DRY_RUN_BATCHES: frozenset[BatchName] = frozenset({"A"})
SUPPORTED_APPLY_BATCHES: frozenset[BatchName] = frozenset({"A"})
ALLOWLIST_BATCH_A_FILENAME = "apply_allowlist_batch_a.txt"
NDR_SAFE_AUTO_APPLY_AUDIT_FILENAME = "ndr_safe_auto_apply_audit.jsonl"
FLAG_NDR_BOUNCES_SCRIPT = "scripts/tools/flag_ndr_bounces_from_contacto.py"
DEFAULT_MAX_APPLY = 50
DEFAULT_MAX_PARSER_UNCERTAIN = 10

ExitCode = Literal[0, 1, 2]
NowFn = Callable[[], datetime]
SubprocessRunFn = Callable[..., subprocess.CompletedProcess[str]]
RebuildQueueFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class NdrSafeAutoApplyOptions:
    batch: BatchName
    dry_run: bool = True
    apply: bool = False
    confirm_reviewed: bool = False
    json_output: bool = False
    queue_dir: Path | None = None
    reports_dir: Path | None = None
    operator: str | None = None
    max_apply: int = DEFAULT_MAX_APPLY
    max_parser_uncertain: int = DEFAULT_MAX_PARSER_UNCERTAIN


def _iso_now(now_fn: NowFn | None = None) -> str:
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def ndr_safe_auto_apply_audit_path(active_current: Path) -> Path:
    return active_current / NDR_SAFE_AUTO_APPLY_AUDIT_FILENAME


def build_ndr_safe_auto_apply_audit_record(
    plan: dict[str, Any],
    *,
    operator: str | None,
    timestamp_utc: str,
    dry_run: bool,
    applied: bool,
    confirm_reviewed: bool | None = None,
    exit_code: int | None = None,
    phase: str | None = None,
    subprocess_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    emails = plan.get("emails")
    if not isinstance(emails, list):
        emails = []
    allowlist_count = plan.get("allowlist_count")
    if allowlist_count is None and emails:
        allowlist_count = len(emails)
    record: dict[str, Any] = {
        "timestamp_utc": timestamp_utc,
        "dry_run": dry_run,
        "batch": plan.get("batch"),
        "reason": plan.get("reason"),
        "queue_dir": plan.get("queue_dir"),
        "queue_generated_at_utc": plan.get("generated_at_utc"),
        "candidates_total": plan.get("candidates_total"),
        "candidates_already_suppressed": plan.get("candidates_already_suppressed"),
        "candidates_unsuppressed": plan.get("candidates_unsuppressed"),
        "allowlist_count": allowlist_count,
        "emails": emails,
        "only_code": plan.get("only_code"),
        "applied": applied,
        "operator": operator,
        "confirm_reviewed": confirm_reviewed,
        "scan_start_date_utc": plan.get("scan_start_date_utc"),
    }
    if exit_code is not None:
        record["exit_code"] = exit_code
    if phase is not None:
        record["phase"] = phase
    if subprocess_results is not None:
        record["subprocess_results"] = subprocess_results
    for key in ("refresh_safety_completed", "needs_safety_refresh"):
        if key in plan:
            record[key] = plan[key]
    return record


def append_ndr_safe_auto_apply_audit_record(
    active_current: Path,
    record: dict[str, Any],
) -> Path:
    """Append one JSONL audit line under active/current."""
    active_current.mkdir(parents=True, exist_ok=True)
    audit_path = ndr_safe_auto_apply_audit_path(active_current)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return audit_path


def _active_current_path(reports_dir: Path | None = None) -> Path:
    base = reports_dir or load_settings().resolved_reports_dir()
    return base / "active" / "current"


def load_ndr_allowlist_emails(path: Path) -> list[str]:
    """Load one email per line; ignore blanks and ``#`` comments."""
    emails: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        emails.append(line.lower())
    return emails


def is_valid_allowlist_email(email: str) -> bool:
    normalized = email.strip().lower()
    if not normalized:
        return False
    found = emails_in(normalized)
    return bool(found) and found[0] == normalized


def resolve_ndr_review_queue_dir(
    *,
    active_current: Path,
    queue_dir: Path | None,
) -> Path | None:
    if queue_dir is not None:
        return queue_dir if queue_dir.is_dir() else None
    return find_latest_ndr_review_queue_dir(active_current)


def _batch_e_count(summary: dict[str, Any]) -> int:
    batch_counts = summary.get("batch_counts")
    if not isinstance(batch_counts, dict):
        return 0
    try:
        return int(batch_counts.get("E") or 0)
    except (TypeError, ValueError):
        return 0


def build_ndr_safe_auto_apply_plan(
    options: NdrSafeAutoApplyOptions,
    *,
    now_fn: NowFn | None = None,
) -> tuple[dict[str, Any], ExitCode, dict[str, Any] | None]:
    """Build a plan from on-disk ndr_review_queue artifacts."""
    supported = SUPPORTED_APPLY_BATCHES if options.apply else SUPPORTED_DRY_RUN_BATCHES
    if options.batch not in supported:
        return (
            {
                "dry_run": options.dry_run,
                "batch": options.batch,
                "reason": "unsupported_batch",
                "message": (
                    f"Batch {options.batch} is not enabled for ndr-safe-auto-apply "
                    f"({'apply' if options.apply else 'dry-run'} supports Batch A only)."
                ),
            },
            1,
            None,
        )

    active_current = _active_current_path(options.reports_dir)
    queue_dir = resolve_ndr_review_queue_dir(
        active_current=active_current,
        queue_dir=options.queue_dir,
    )
    if queue_dir is None:
        return (
            {
                "dry_run": options.dry_run,
                "batch": options.batch,
                "reason": "missing_queue",
                "message": "No ndr_review_queue_* directory found. Run: uv run origenlab ndr-review",
                "queue_dir": None,
            },
            2,
            None,
        )

    summary, parse_error = load_ndr_review_summary_json(queue_dir)
    if summary is None:
        return (
            {
                "dry_run": options.dry_run,
                "batch": options.batch,
                "reason": parse_error or "malformed_summary",
                "message": f"Could not read {NDR_REVIEW_SUMMARY_FILENAME} under {queue_dir}",
                "queue_dir": str(queue_dir.resolve()),
            },
            2,
            None,
        )

    allowlist_path = queue_dir / ALLOWLIST_BATCH_A_FILENAME
    if not allowlist_path.is_file():
        return (
            {
                "dry_run": options.dry_run,
                "batch": options.batch,
                "reason": "missing_allowlist",
                "message": f"Missing {ALLOWLIST_BATCH_A_FILENAME} in queue directory",
                "queue_dir": str(queue_dir.resolve()),
                "generated_at_utc": summary.get("generated_at"),
            },
            2,
            None,
        )

    emails = load_ndr_allowlist_emails(allowlist_path)
    reason = "ready" if emails else "no_candidates"
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    scan_start_date_utc, scan_window_error = resolve_reviewed_ndr_scan_start_date_utc(
        summary,
        now_utc=now,
    )

    plan: dict[str, Any] = {
        "dry_run": options.dry_run,
        "batch": options.batch,
        "only_code": APPLY_ONLY_CODE_BATCH_A,
        "reason": reason,
        "queue_dir": str(queue_dir.resolve()),
        "allowlist_path": str(allowlist_path.resolve()),
        "generated_at_utc": summary.get("generated_at"),
        "since_days": summary.get("since_days"),
        "scan_start_date_utc": scan_start_date_utc,
        "scan_window_error": scan_window_error,
        "date_label": summary.get("date_label"),
        "candidates_total": summary.get("candidates_total"),
        "candidates_already_suppressed": summary.get("candidates_already_suppressed"),
        "candidates_unsuppressed": summary.get("candidates_unsuppressed"),
        "batch_e_count": _batch_e_count(summary),
        "allowlist_count": len(emails),
        "emails": emails,
    }
    return plan, 0, summary


def validate_apply_guards(
    options: NdrSafeAutoApplyOptions,
    plan: dict[str, Any],
    *,
    summary: dict[str, Any],
) -> tuple[str, ExitCode]:
    if not options.operator:
        return "missing_operator", 1
    if not options.confirm_reviewed:
        return "missing_confirm_reviewed", 1
    if plan.get("reason") == "no_candidates":
        return "no_candidates", 1

    emails = plan.get("emails") or []
    for email in emails:
        if not is_valid_allowlist_email(str(email)):
            return "invalid_allowlist_email", 1

    allowlist_count = len(emails)
    if allowlist_count > options.max_apply:
        return "max_apply_exceeded", 1

    batch_e = _batch_e_count(summary)
    if batch_e > options.max_parser_uncertain:
        return "parser_uncertain_exceeded", 1

    if plan.get("reason") != "ready":
        return str(plan.get("reason") or "not_ready"), 1

    scan_window_error = plan.get("scan_window_error")
    if scan_window_error:
        return str(scan_window_error), 1
    if not plan.get("scan_start_date_utc"):
        return "missing_scan_start_date_utc", 1

    return "ready", 0


def build_targeted_ndr_apply_command(
    *,
    allowlist_path: Path,
    since_date_utc: str,
) -> list[str]:
    cmd = [
        sys.executable,
        str(repo_root() / FLAG_NDR_BOUNCES_SCRIPT),
        "--emails-file",
        str(allowlist_path),
        "--only-code",
        APPLY_ONLY_CODE_BATCH_A,
        "--since-date-utc",
        str(since_date_utc),
        "--apply",
    ]
    return cmd

def build_refresh_safety_command() -> list[str]:
    return [sys.executable, "-m", "origenlab_email_pipeline.cli", "refresh-safety"]


def _subprocess_summary(
    *,
    label: str,
    argv: list[str],
    result: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    return {
        "label": label,
        "argv": argv,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def rebuild_ndr_review_queue_after_apply(
    *,
    since_days: int,
    sqlite_path: Path | None = None,
) -> dict[str, Any]:
    date_label = default_date_label()
    out_dir = default_out_dir(repo_root(), date_label)
    sqlite = sqlite_path or resolve_sqlite_path(None)
    result = build_ndr_review_queue(
        sqlite_path=sqlite,
        out_dir=out_dir,
        since_days=since_days,
        date_label=date_label,
    )
    summary = result.summary_json()
    return {
        "out_dir": str(out_dir.resolve()),
        "allowlist_batch_a_count": summary.get("allowlist_batch_a_count"),
        "candidates_unsuppressed": summary.get("candidates_unsuppressed"),
    }


def format_ndr_safe_auto_apply_text(plan: dict[str, Any]) -> str:
    lines = [
        "ndr_safe_auto_apply",
        f"dry_run={plan.get('dry_run', True)}",
        f"batch={plan.get('batch', '')}",
        f"reason={plan.get('reason', '')}",
    ]
    if plan.get("applied") is not None:
        lines.append(f"applied={plan.get('applied')}")
    if plan.get("message"):
        lines.append(f"message={plan['message']}")
    for key in (
        "only_code",
        "queue_dir",
        "generated_at_utc",
        "since_days",
        "scan_start_date_utc",
        "date_label",
        "candidates_total",
        "candidates_already_suppressed",
        "candidates_unsuppressed",
        "allowlist_count",
        "exit_code",
        "refresh_safety_completed",
        "needs_safety_refresh",
    ):
        if key in plan and plan[key] is not None:
            lines.append(f"{key}={plan[key]}")
    emails = plan.get("emails")
    if isinstance(emails, list) and emails:
        lines.append("emails:")
        for email in emails:
            lines.append(f"  - {email}")
    return "\n".join(lines) + "\n"


def _emit_result(
    plan: dict[str, Any],
    *,
    options: NdrSafeAutoApplyOptions,
    active_current: Path,
    now_fn: NowFn | None,
    exit_code: int,
) -> int:
    plan["audit_path"] = str(ndr_safe_auto_apply_audit_path(active_current).resolve())
    plan["exit_code"] = exit_code
    if options.json_output:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(format_ndr_safe_auto_apply_text(plan), end="")
    return exit_code


def _run_apply_path(
    options: NdrSafeAutoApplyOptions,
    plan: dict[str, Any],
    summary: dict[str, Any],
    *,
    active_current: Path,
    now_fn: NowFn | None,
    subprocess_run: SubprocessRunFn,
    rebuild_queue_fn: RebuildQueueFn,
) -> int:
    guard_reason, guard_exit = validate_apply_guards(options, plan, summary=summary)
    if guard_exit != 0:
        plan = {**plan, "dry_run": False, "reason": guard_reason, "applied": False}
        append_ndr_safe_auto_apply_audit_record(
            active_current,
            build_ndr_safe_auto_apply_audit_record(
                plan,
                operator=options.operator,
                timestamp_utc=_iso_now(now_fn),
                dry_run=False,
                applied=False,
                confirm_reviewed=options.confirm_reviewed,
                exit_code=guard_exit,
            ),
        )
        return _emit_result(plan, options=options, active_current=active_current, now_fn=now_fn, exit_code=guard_exit)

    plan = {**plan, "dry_run": False, "reason": "apply_started", "applied": False}
    append_ndr_safe_auto_apply_audit_record(
        active_current,
        build_ndr_safe_auto_apply_audit_record(
            plan,
            operator=options.operator,
            timestamp_utc=_iso_now(now_fn),
            dry_run=False,
            applied=False,
            confirm_reviewed=options.confirm_reviewed,
            phase="before_apply",
            exit_code=None,
        ),
    )

    allowlist_path = Path(str(plan["allowlist_path"]))
    since_days = plan.get("since_days")
    since_days_int = int(since_days) if since_days is not None else None
    scan_start_date_utc = str(plan["scan_start_date_utc"])
    ndr_argv = build_targeted_ndr_apply_command(
        allowlist_path=allowlist_path,
        since_date_utc=scan_start_date_utc,
    )
    ndr_result = subprocess_run(
        ndr_argv,
        cwd=str(repo_root()),
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess_results: dict[str, Any] = {
        "ndr_apply": _subprocess_summary(label="ndr_apply", argv=ndr_argv, result=ndr_result),
    }
    if ndr_result.returncode != 0:
        plan = {**plan, "reason": "ndr_apply_failed", "applied": False}
        append_ndr_safe_auto_apply_audit_record(
            active_current,
            build_ndr_safe_auto_apply_audit_record(
                plan,
                operator=options.operator,
                timestamp_utc=_iso_now(now_fn),
                dry_run=False,
                applied=False,
                confirm_reviewed=options.confirm_reviewed,
                phase="after_apply",
                exit_code=2,
                subprocess_results=subprocess_results,
            ),
        )
        return _emit_result(plan, options=options, active_current=active_current, now_fn=now_fn, exit_code=2)

    refresh_argv = build_refresh_safety_command()
    refresh_result = subprocess_run(
        refresh_argv,
        cwd=str(repo_root()),
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess_results["refresh_safety"] = _subprocess_summary(
        label="refresh_safety",
        argv=refresh_argv,
        result=refresh_result,
    )
    if refresh_result.returncode != 0:
        plan = {
            **plan,
            "reason": "refresh_safety_failed",
            "applied": True,
            "refresh_safety_completed": False,
            "needs_safety_refresh": True,
        }
        append_ndr_safe_auto_apply_audit_record(
            active_current,
            build_ndr_safe_auto_apply_audit_record(
                plan,
                operator=options.operator,
                timestamp_utc=_iso_now(now_fn),
                dry_run=False,
                applied=True,
                confirm_reviewed=options.confirm_reviewed,
                phase="after_apply",
                exit_code=2,
                subprocess_results=subprocess_results,
            ),
        )
        return _emit_result(plan, options=options, active_current=active_current, now_fn=now_fn, exit_code=2)

    rebuild_summary = rebuild_queue_fn(since_days=since_days_int or 1)
    plan = {
        **plan,
        "reason": "applied",
        "applied": True,
        "rebuilt_queue": rebuild_summary,
    }
    append_ndr_safe_auto_apply_audit_record(
        active_current,
        build_ndr_safe_auto_apply_audit_record(
            plan,
            operator=options.operator,
            timestamp_utc=_iso_now(now_fn),
            dry_run=False,
            applied=True,
            confirm_reviewed=options.confirm_reviewed,
            phase="after_apply",
            exit_code=0,
            subprocess_results=subprocess_results,
        ),
    )
    return _emit_result(plan, options=options, active_current=active_current, now_fn=now_fn, exit_code=0)


def run_ndr_safe_auto_apply(
    options: NdrSafeAutoApplyOptions,
    *,
    now_fn: NowFn | None = None,
    subprocess_run: SubprocessRunFn | None = None,
    rebuild_queue_fn: RebuildQueueFn | None = None,
) -> int:
    run_subprocess = subprocess_run or subprocess.run
    rebuild_fn = rebuild_queue_fn or rebuild_ndr_review_queue_after_apply
    active_current = _active_current_path(options.reports_dir)

    plan, exit_code, summary = build_ndr_safe_auto_apply_plan(options, now_fn=now_fn)
    if options.dry_run:
        audit_record = build_ndr_safe_auto_apply_audit_record(
            plan,
            operator=options.operator,
            timestamp_utc=_iso_now(now_fn),
            dry_run=True,
            applied=False,
            confirm_reviewed=None,
            exit_code=int(exit_code),
        )
        append_ndr_safe_auto_apply_audit_record(active_current, audit_record)
        return _emit_result(
            plan,
            options=options,
            active_current=active_current,
            now_fn=now_fn,
            exit_code=int(exit_code),
        )

    if exit_code != 0 or summary is None:
        plan = {**plan, "dry_run": False, "applied": False}
        append_ndr_safe_auto_apply_audit_record(
            active_current,
            build_ndr_safe_auto_apply_audit_record(
                plan,
                operator=options.operator,
                timestamp_utc=_iso_now(now_fn),
                dry_run=False,
                applied=False,
                confirm_reviewed=options.confirm_reviewed,
                exit_code=int(exit_code),
            ),
        )
        return _emit_result(
            plan,
            options=options,
            active_current=active_current,
            now_fn=now_fn,
            exit_code=int(exit_code),
        )

    return _run_apply_path(
        options,
        plan,
        summary,
        active_current=active_current,
        now_fn=now_fn,
        subprocess_run=run_subprocess,
        rebuild_queue_fn=rebuild_fn,
    )


def parse_ndr_safe_auto_apply_args(argv: list[str]) -> NdrSafeAutoApplyOptions:
    parser = argparse.ArgumentParser(prog="ndr-safe-auto-apply", add_help=True)
    parser.add_argument(
        "--batch",
        required=True,
        choices=["A", "B", "C", "D", "E"],
        help="Human-review batch (Batch A apply/dry-run only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview allowlist and metadata without writing suppressions",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply Batch A exact-email suppressions after guard checks",
    )
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="Required with --apply: operator reviewed the allowlist",
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument(
        "--queue-dir",
        type=Path,
        default=None,
        help="Use a specific ndr_review_queue_* directory instead of the latest",
    )
    parser.add_argument(
        "--operator",
        default=None,
        help="Operator name (required for --apply; optional for dry-run audit)",
    )
    parser.add_argument(
        "--max-apply",
        type=int,
        default=DEFAULT_MAX_APPLY,
        help=f"Refuse apply when allowlist exceeds this count (default {DEFAULT_MAX_APPLY})",
    )
    parser.add_argument(
        "--max-parser-uncertain",
        type=int,
        default=DEFAULT_MAX_PARSER_UNCERTAIN,
        help=(
            "Refuse apply when Batch E count in queue summary exceeds this "
            f"(default {DEFAULT_MAX_PARSER_UNCERTAIN})"
        ),
    )
    ns = parser.parse_args(argv)
    if ns.apply and ns.dry_run:
        parser.error("Use either --apply or --dry-run, not both")
    operator = (ns.operator or "").strip() or None
    apply = bool(ns.apply)
    dry_run = not apply
    return NdrSafeAutoApplyOptions(
        batch=ns.batch,
        dry_run=dry_run,
        apply=apply,
        confirm_reviewed=bool(ns.confirm_reviewed),
        json_output=ns.json,
        queue_dir=ns.queue_dir,
        operator=operator,
        max_apply=int(ns.max_apply),
        max_parser_uncertain=int(ns.max_parser_uncertain),
    )


def print_ndr_safe_auto_apply_help() -> None:
    print(
        "ndr-safe-auto-apply — dry-run or guarded Batch A NDR suppressions\n\n"
        "  uv run origenlab ndr-safe-auto-apply --batch A --dry-run --operator rafael\n"
        "  uv run origenlab ndr-safe-auto-apply --batch A --apply --operator rafael --confirm-reviewed\n"
        "  Flags: --max-apply 50 --max-parser-uncertain 10 --queue-dir PATH --json\n\n"
        "Dry-run reads ndr_review_queue artifacts only. Apply uses targeted "
        f"{FLAG_NDR_BOUNCES_SCRIPT}, then refresh-safety, then rebuilds ndr-review. "
        f"Audit JSONL: active/current/{NDR_SAFE_AUTO_APPLY_AUDIT_FILENAME}. "
        "No mirror, cron, or domain suppression.\n"
    )
