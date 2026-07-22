#!/usr/bin/env python3
"""Staged SQLite production cutover orchestrator CLI.

Default is zero-write ``plan_preflight``. Every mutating stage requires its own
``--apply`` plus high-friction production authorization. Never runs the full
workflow in one command.

Does not authorize improvising dual ``mv`` when rename exchange is unavailable.
Production apply readiness is derived (pause, stop, flock, write barrier, FD
scan, WAL quiesce, valid plan). ChileCompra does not block SQLite RPO=0.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    sanitize_error_message,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    EXIT_APPLY,
    EXIT_OK,
    CutoverError,
    CutoverOptions,
    CutoverStage,
    STAGE_ORDER,
    abort_before_swap,
    apply_stage,
    attempt_rollback_before_writers,
    plan_preflight,
    rollback_finalize,
    _sanitized_backup_failure_evidence,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Fail-closed SQLite production cutover orchestrator. "
            "Stages must be applied one at a time."
        )
    )
    p.add_argument(
        "--stage",
        choices=[s.value for s in STAGE_ORDER],
        default=CutoverStage.PLAN_PREFLIGHT.value,
        help="Single stage to plan or apply",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Perform the selected stage write (forbidden for plan_preflight)",
    )
    p.add_argument(
        "--confirm-production-cutover",
        action="store_true",
        help="Acknowledge real production cutover authorization",
    )
    p.add_argument("--maintenance-id", default="", help="Unique maintenance ID")
    p.add_argument(
        "--expected-main-sha",
        default="",
        help="Complete 40-character git SHA (exact match required)",
    )
    p.add_argument(
        "--expected-production-path",
        type=Path,
        default=None,
        help="Exact expected production SQLite path (canonical only for real apply)",
    )
    p.add_argument(
        "--expected-production-fingerprint",
        default="",
        help="size:mtime_ns:device:inode token",
    )
    p.add_argument(
        "--approve-swap",
        action="store_true",
        help="Separate explicit approval for approve_swap / atomic_swap",
    )
    p.add_argument(
        "--journal-path",
        type=Path,
        default=None,
        help="Must stay under production .origenlab_cutover_journals/",
    )
    p.add_argument("--backup-dest", type=Path, default=None)
    p.add_argument("--staging-dest", type=Path, default=None)
    p.add_argument("--reports-dir", type=Path, default=None)
    p.add_argument("--api-base-url", default="http://127.0.0.1:8001")
    p.add_argument(
        "--rollback-before-writers",
        action="store_true",
        help="Attempt verified atomic rollback before writer_resume_started",
    )
    p.add_argument(
        "--abort-before-swap",
        action="store_true",
        help="Abort before swap intent/exchange; restore perms/services/markers",
    )
    p.add_argument(
        "--rollback-finalize",
        action="store_true",
        help=(
            "Finalize a verified pre-PoNR rollback into terminal ABANDONED "
            "(never COMPLETED). Requires --apply and the full production approval "
            "contract; mutually exclusive with --stage cutover application, "
            "--abort-before-swap, and --rollback-before-writers"
        ),
    )
    p.add_argument(
        "--pre-cutover-path",
        type=Path,
        default=None,
        help="Optional; must exactly match journal-approved pre-cutover path",
    )
    p.add_argument("--expected-old-fingerprint", default="")
    p.add_argument("--expected-new-fingerprint", default="")
    p.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        opts = CutoverOptions(
            stage=CutoverStage(args.stage),
            apply=bool(args.apply),
            confirm_production_cutover=bool(args.confirm_production_cutover),
            maintenance_id=str(args.maintenance_id),
            expected_main_sha=str(args.expected_main_sha),
            expected_production_path=args.expected_production_path,
            expected_production_fingerprint=str(args.expected_production_fingerprint),
            approve_swap=bool(args.approve_swap),
            journal_path=args.journal_path,
            backup_dest=args.backup_dest,
            staging_dest=args.staging_dest,
            reports_dir=args.reports_dir,
            api_base_url=str(args.api_base_url),
        )
        mutually_exclusive = [
            ("--abort-before-swap", bool(args.abort_before_swap)),
            ("--rollback-before-writers", bool(args.rollback_before_writers)),
            ("--rollback-finalize", bool(args.rollback_finalize)),
        ]
        selected = [name for name, on in mutually_exclusive if on]
        if len(selected) > 1:
            print(
                f"error: {' and '.join(selected)} are mutually exclusive",
                file=sys.stderr,
            )
            return 2
        if args.rollback_finalize and args.stage != CutoverStage.PLAN_PREFLIGHT.value:
            print(
                "error: --rollback-finalize does not take --stage "
                "(it is a dedicated operation, not a cutover stage)",
                file=sys.stderr,
            )
            return 2
        if args.rollback_finalize:
            report = rollback_finalize(opts)
        elif args.abort_before_swap:
            report = abort_before_swap(opts)
        elif args.rollback_before_writers:
            report = attempt_rollback_before_writers(
                opts,
                pre_cutover_path=(
                    args.pre_cutover_path.expanduser()
                    if args.pre_cutover_path is not None
                    else None
                ),
                expected_old_fingerprint=args.expected_old_fingerprint,
                expected_new_fingerprint=args.expected_new_fingerprint,
            )
        elif opts.stage == CutoverStage.PLAN_PREFLIGHT and not opts.apply:
            report = plan_preflight(opts)
        else:
            report = apply_stage(opts)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(
                f"ok stage={report.get('stage')} apply={report.get('apply')} "
                f"mode={report.get('mode')}"
            )
        return EXIT_OK
    except CutoverError as exc:
        print(f"error: {sanitize_error_message(str(exc))}", file=sys.stderr)
        if exc.recovery:
            print(
                f"recovery: {sanitize_error_message(exc.recovery)}",
                file=sys.stderr,
            )
        evidence = exc.evidence if isinstance(exc.evidence, dict) else {}
        op = evidence.get("operational_error")
        if isinstance(op, dict):
            print(
                "operational_error "
                f"category={op.get('category')} "
                f"phase={op.get('phase')} "
                f"sqlite_errorcode={op.get('sqlite_errorcode')} "
                f"sqlite_errorname={op.get('sqlite_errorname')} "
                f"retryable={op.get('retryable')}",
                file=sys.stderr,
            )
        fd = evidence.get("fd_observation")
        if isinstance(fd, dict):
            print(
                "fd_observation "
                f"verdict={fd.get('verdict')} "
                f"blocker_count={fd.get('blocker_count')} "
                f"ambiguous_count={fd.get('ambiguous_count')}",
                file=sys.stderr,
            )
        return int(exc.exit_code)
    except BackupError as exc:
        # Adapter should convert; still never report as mere "unexpected failure".
        # Re-allowlist detail before printing (never dump raw BackupError.detail).
        print(f"error: {sanitize_error_message(str(exc))}", file=sys.stderr)
        evidence = _sanitized_backup_failure_evidence(
            exc.detail if isinstance(exc.detail, dict) else None
        )
        op = evidence.get("operational_error")
        if isinstance(op, dict):
            print(
                "operational_error "
                f"category={op.get('category')} "
                f"phase={op.get('phase')} "
                f"sqlite_errorcode={op.get('sqlite_errorcode')} "
                f"sqlite_errorname={op.get('sqlite_errorname')} "
                f"retryable={op.get('retryable')}",
                file=sys.stderr,
            )
            recovery = op.get("recovery")
            if isinstance(recovery, str):
                print(
                    f"recovery: {sanitize_error_message(recovery)}",
                    file=sys.stderr,
                )
        fd = evidence.get("fd_observation")
        if isinstance(fd, dict):
            print(
                "fd_observation "
                f"verdict={fd.get('verdict')} "
                f"blocker_count={fd.get('blocker_count')} "
                f"ambiguous_count={fd.get('ambiguous_count')}",
                file=sys.stderr,
            )
        return EXIT_APPLY
    except Exception:  # noqa: BLE001
        print("error: unexpected failure", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
