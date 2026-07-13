"""Argparse setup and main dispatch for operator CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from origenlab_email_pipeline.operator_cli.constants import (
    AUTO_MIRROR_DASHBOARD_COMMAND,
    AUTO_REFRESH_CHILECOMPRA_EQUIPMENT_COMMAND,
    AUTO_REFRESH_MAIL_COMMAND,
    CLI_COMMAND_NAMES,
    NDR_SAFE_AUTO_APPLY_COMMAND,
    OPERATOR_AUTOMATION_STATUS_COMMAND,
    DAILY_CORE_COMMAND,
    GMAIL_INGEST_SCRIPT,
    HELP_ONLY_SUBCOMMANDS,
    MIRROR_DASHBOARD_COMMAND,
    REFRESH_DASHBOARD_COMMAND,
    SPECIAL_COMMANDS,
    SUBCOMMAND_HELP,
    SUBCOMMAND_SCRIPTS,
)
from origenlab_email_pipeline.operator_cli.chilecompra_auto_refresh import (
    parse_chilecompra_equipment_auto_refresh_args,
    print_chilecompra_equipment_auto_refresh_help,
    run_chilecompra_equipment_auto_refresh,
)
from origenlab_email_pipeline.operator_cli.dashboard_auto_mirror import (
    parse_dashboard_auto_mirror_args,
    print_dashboard_auto_mirror_help,
    run_dashboard_auto_mirror,
)
from origenlab_email_pipeline.operator_cli.ndr_safe_auto_apply import (
    parse_ndr_safe_auto_apply_args,
    print_ndr_safe_auto_apply_help,
    run_ndr_safe_auto_apply,
)
from origenlab_email_pipeline.operator_cli.operator_automation_status import (
    parse_operator_automation_status_args,
    print_operator_automation_status_help,
    run_operator_automation_status,
)
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import (
    parse_mail_auto_refresh_args,
    print_mail_auto_refresh_help,
    run_mail_auto_refresh,
)
from origenlab_email_pipeline.operator_cli.gmail import (
    print_gmail_ingest_folders_help,
    print_gmail_ingest_help_help,
    validate_gmail_ingest_passthrough,
)
from origenlab_email_pipeline.operator_cli.mirror import (
    parse_mirror_dashboard_wrapper_args,
    print_mirror_dashboard_help,
)
from origenlab_email_pipeline.operator_cli.paths import normalize_passthrough_args
from origenlab_email_pipeline.operator_cli.refresh import (
    parse_daily_core_wrapper_args,
    parse_refresh_dashboard_wrapper_args,
    print_daily_core_help,
    print_refresh_dashboard_help,
    run_daily_core,
    run_refresh_dashboard,
)
from origenlab_email_pipeline.operator_cli.runner import run_subcommand


def _add_refresh_wrapper_flags(
    parser: argparse.ArgumentParser,
    *,
    include_mirror_dry_run: bool,
    no_mirror_help: str = "With --apply: skip Postgres mirror step",
) -> None:
    parser.add_argument("--apply", action="store_true", help="Run workflow steps (stop on first failure)")
    parser.add_argument("--no-mirror", action="store_true", help=no_mirror_help)
    if include_mirror_dry_run:
        parser.add_argument(
            "--mirror-dry-run",
            action="store_true",
            help="With --apply: end with mirror-dashboard dry-run (not --apply)",
        )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="With --apply: skip gmail-ingest (use when already ingested)",
    )
    parser.add_argument(
        "--since-days",
        metavar="N",
        type=int,
        help="With --apply: pass --since-days N to gmail-ingest only",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="origenlab-email-pipeline",
        description="Operator CLI wrapper — delegates to existing scripts under scripts/.",
        epilog=(
            "Pass script-specific flags after ``--``. Example: "
            "origenlab status -- --json. "
            "gmail-ingest: safe INBOX + Sent ingest (--replace-source rejected). "
            "gmail-ingest-help: ingest --help only. "
            "mirror-dashboard: Postgres mirror sync (dry-run default; requires Postgres URL env). "
            "refresh-dashboard: orchestrated stack refresh (plan-only default). "
            "daily-core: daily operating alias (plan-only default; --apply uses feature-backed mart rebuild). "
            "auto-refresh-mail: debounced mailbox probe (--once; --apply runs daily-core when gates pass). "
            "auto-mirror-dashboard: debounced Postgres publish (--once; separate from mail watcher). "
            "auto-refresh-chilecompra-equipment: ChileCompra API equipment queue refresh (--once --apply). "
            "operator-automation-status: read-only automation health (--json optional). "
            "ndr-safe-auto-apply: Batch A dry-run plan from ndr_review_queue (--apply not enabled)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name in CLI_COMMAND_NAMES:
        script_rel = SUBCOMMAND_SCRIPTS.get(name, GMAIL_INGEST_SCRIPT)
        if name == NDR_SAFE_AUTO_APPLY_COMMAND:
            p = sub.add_parser(
                name,
                help=SUBCOMMAND_HELP[name],
                description=SUBCOMMAND_HELP[name],
            )
            p.add_argument(
                "--batch",
                required=True,
                choices=["A", "B", "C", "D", "E"],
                help="Human-review batch to plan (Batch A dry-run only in this release)",
            )
            p.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview allowlist without writing suppressions (default mode)",
            )
            p.add_argument(
                "--apply",
                action="store_true",
                help="Apply Batch A exact-email suppressions after guard checks",
            )
            p.add_argument(
                "--confirm-reviewed",
                action="store_true",
                help="Required with --apply: operator reviewed the allowlist",
            )
            p.add_argument("--json", action="store_true", help="Emit structured JSON")
            p.add_argument(
                "--queue-dir",
                type=Path,
                default=None,
                help="Use a specific ndr_review_queue_* directory",
            )
            p.add_argument(
                "--operator",
                default=None,
                help="Operator name (required for --apply)",
            )
            p.add_argument(
                "--max-apply",
                type=int,
                default=50,
                help="Refuse apply when allowlist exceeds this count",
            )
            p.add_argument(
                "--max-parser-uncertain",
                type=int,
                default=10,
                help="Refuse apply when Batch E count exceeds this threshold",
            )
            continue
        if name == OPERATOR_AUTOMATION_STATUS_COMMAND:
            p = sub.add_parser(
                name,
                help=SUBCOMMAND_HELP[name],
                description=SUBCOMMAND_HELP[name],
            )
            p.add_argument("--json", action="store_true", help="Emit structured JSON")
            p.add_argument(
                "--cooldown-seconds",
                type=int,
                default=60,
                help="Dashboard mirror cooldown for remaining-seconds calculation",
            )
            continue
        if name == AUTO_MIRROR_DASHBOARD_COMMAND:
            p = sub.add_parser(
                name,
                help=SUBCOMMAND_HELP[name],
                description=SUBCOMMAND_HELP[name],
            )
            p.add_argument("--apply", action="store_true", help="Run mirror-dashboard when gates pass")
            p.add_argument("--once", action="store_true", help="Single evaluation (required)")
            p.add_argument(
                "--daemon",
                action="store_true",
                help="Loop mode (not implemented — use external scheduler with --once)",
            )
            p.add_argument("--cooldown-seconds", type=int, default=60)
            p.add_argument("--operator", default="rafael")
            p.add_argument(
                "--reason",
                default="Automated dashboard mirror after successful daily-core",
            )
            p.add_argument(
                "--allow-non-scratch-postgres",
                action="store_true",
                help="Required with --apply before writing to non-scratch Postgres",
            )
            continue
        if name == AUTO_REFRESH_MAIL_COMMAND:
            p = sub.add_parser(
                name,
                help=SUBCOMMAND_HELP[name],
                description=SUBCOMMAND_HELP[name],
            )
            p.add_argument("--apply", action="store_true", help="Run daily-core --apply when gates pass")
            p.add_argument("--once", action="store_true", help="Single evaluation (required)")
            p.add_argument(
                "--daemon",
                action="store_true",
                help="Loop mode (not implemented — use external scheduler with --once)",
            )
            p.add_argument("--interval-seconds", type=int, default=120)
            p.add_argument("--quiet-seconds", type=int, default=180)
            p.add_argument("--cooldown-seconds", type=int, default=600)
            p.add_argument("--large-sent-delta", type=int, default=50)
            p.add_argument("--large-sent-quiet-seconds", type=int, default=900)
            continue
        if name in (REFRESH_DASHBOARD_COMMAND, DAILY_CORE_COMMAND):
            p = sub.add_parser(name, help=SUBCOMMAND_HELP[name], description=SUBCOMMAND_HELP[name])
            _add_refresh_wrapper_flags(
                p,
                include_mirror_dry_run=(name == REFRESH_DASHBOARD_COMMAND),
                no_mirror_help=(
                    "Accepted for compatibility; daily-core never runs mirror"
                    if name == DAILY_CORE_COMMAND
                    else "With --apply: skip Postgres mirror step"
                ),
            )
            if name == DAILY_CORE_COMMAND:
                p.add_argument(
                    "--mirror-dry-run",
                    action="store_true",
                    help=argparse.SUPPRESS,
                )
            continue
        if name == MIRROR_DASHBOARD_COMMAND:
            p = sub.add_parser(
                name,
                help=SUBCOMMAND_HELP[name],
                description=SUBCOMMAND_HELP[name],
            )
            p.add_argument(
                "--apply",
                action="store_true",
                help="Run sync without --dry-run (writes Postgres mirror)",
            )
            p.add_argument(
                "--alembic",
                action="store_true",
                help="With --apply: run alembic upgrade head before sync",
            )
            p.add_argument(
                "--live",
                action="store_true",
                help=(
                    "Include live dashboard optional loaders (warm cases, equipment "
                    "opportunities, commercial deals); dry-run unless --apply"
                ),
            )
            p.add_argument(
                "--operator",
                metavar="VALUE",
                help="With --live --apply: operator id for optional loader audit (alias: --updated-by)",
            )
            p.add_argument(
                "--updated-by",
                metavar="VALUE",
                help="With --live --apply: operator id for optional loader audit",
            )
            p.add_argument(
                "--reason",
                metavar="VALUE",
                help="With --live --apply: audit reason for optional loader writes",
            )
            continue
        sub.add_parser(
            name,
            help=SUBCOMMAND_HELP.get(name, script_rel),
            description=f"Run {script_rel}" if name in SUBCOMMAND_SCRIPTS else SUBCOMMAND_HELP.get(name, ""),
        )
    return parser


def _is_known_command(command: str) -> bool:
    return command in SUBCOMMAND_SCRIPTS or command in SPECIAL_COMMANDS


def _wrapper_help_requested(argv_tail: list[str]) -> bool:
    return len(argv_tail) == 1 and argv_tail[0] in ("-h", "--help")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0

    command = argv[0]
    if not _is_known_command(command):
        parser.error(f"unknown command {command!r}")

    if command == REFRESH_DASHBOARD_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_refresh_dashboard_help()
            return 0
        try:
            refresh_opts = parse_refresh_dashboard_wrapper_args(argv[1:])
        except ValueError as exc:
            parser.error(str(exc))
        return run_refresh_dashboard(refresh_opts)

    if command == DAILY_CORE_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_daily_core_help()
            return 0
        try:
            daily_opts = parse_daily_core_wrapper_args(argv[1:])
        except ValueError as exc:
            parser.error(str(exc))
        return run_daily_core(daily_opts)

    if command == AUTO_REFRESH_MAIL_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_mail_auto_refresh_help()
            return 0
        try:
            auto_opts = parse_mail_auto_refresh_args(argv[1:])
        except SystemExit as exc:
            raise exc
        except ValueError as exc:
            parser.error(str(exc))
        try:
            return run_mail_auto_refresh(auto_opts)
        except ValueError as exc:
            parser.error(str(exc))

    if command == AUTO_MIRROR_DASHBOARD_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_dashboard_auto_mirror_help()
            return 0
        try:
            mirror_opts = parse_dashboard_auto_mirror_args(argv[1:])
        except SystemExit as exc:
            raise exc
        except ValueError as exc:
            parser.error(str(exc))
        try:
            return run_dashboard_auto_mirror(mirror_opts)
        except ValueError as exc:
            parser.error(str(exc))

    if command == AUTO_REFRESH_CHILECOMPRA_EQUIPMENT_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_chilecompra_equipment_auto_refresh_help()
            return 0
        try:
            refresh_opts = parse_chilecompra_equipment_auto_refresh_args(argv[1:])
        except SystemExit as exc:
            raise exc
        except ValueError as exc:
            parser.error(str(exc))
        try:
            return run_chilecompra_equipment_auto_refresh(refresh_opts)
        except ValueError as exc:
            parser.error(str(exc))

    if command == OPERATOR_AUTOMATION_STATUS_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_operator_automation_status_help()
            return 0
        try:
            status_opts = parse_operator_automation_status_args(argv[1:])
        except SystemExit as exc:
            raise exc
        return run_operator_automation_status(status_opts)

    if command == NDR_SAFE_AUTO_APPLY_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_ndr_safe_auto_apply_help()
            return 0
        try:
            ndr_opts = parse_ndr_safe_auto_apply_args(argv[1:])
        except SystemExit as exc:
            raise exc
        return run_ndr_safe_auto_apply(ndr_opts)

    mirror_apply = False
    mirror_alembic = False
    passthrough: list[str]
    if command == MIRROR_DASHBOARD_COMMAND:
        if _wrapper_help_requested(argv[1:]):
            print_mirror_dashboard_help()
            return 0
        try:
            mirror_apply, mirror_alembic, passthrough = parse_mirror_dashboard_wrapper_args(argv[1:])
        except ValueError as exc:
            parser.error(str(exc))
    elif command == "gmail-ingest-folders" and _wrapper_help_requested(argv[1:]):
        print_gmail_ingest_folders_help()
        return 0
    elif command == "gmail-ingest-help" and _wrapper_help_requested(argv[1:]):
        print_gmail_ingest_help_help()
        return 0
    else:
        passthrough = normalize_passthrough_args(argv[1:])

    if command in HELP_ONLY_SUBCOMMANDS and passthrough:
        parser.error(
            f"{command!r} does not accept extra arguments (runs ingest --help only). "
            "Use gmail-ingest for daily INBOX + Sent refresh."
        )
    if command == "gmail-ingest-folders" and passthrough:
        parser.error(
            f"{command!r} does not accept extra arguments (runs ingest --list-folders only)."
        )
    if command == "gmail-ingest":
        try:
            validate_gmail_ingest_passthrough(passthrough)
        except ValueError as exc:
            parser.error(str(exc))

    return run_subcommand(
        command,
        passthrough,
        mirror_apply=mirror_apply,
        mirror_alembic=mirror_alembic,
    )
