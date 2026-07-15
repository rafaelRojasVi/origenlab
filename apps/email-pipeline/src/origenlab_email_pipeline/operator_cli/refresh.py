"""Refresh-dashboard workflow orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from origenlab_email_pipeline.core.step_runner import StepResult, run_step_sequence
from origenlab_email_pipeline.operator_cli.constants import (
    DAILY_CORE_USAGE,
    REFRESH_DASHBOARD_USAGE,
)
from origenlab_email_pipeline.operator_cli.daily_core_manifest import write_daily_core_run_manifest
from origenlab_email_pipeline.operator_cli.sqlite_storage_monitor import (
    collect_and_store_sqlite_storage_evidence,
)

SubcommandRunner = Callable[..., int]


@dataclass(frozen=True)
class RefreshDashboardStep:
    """One step in the refresh-dashboard workflow."""

    label: str
    command: str
    passthrough: tuple[str, ...] = ()
    mirror_apply: bool = False
    mirror_alembic: bool = False


@dataclass(frozen=True)
class RefreshDashboardOptions:
    apply: bool = False
    no_mirror: bool = False
    mirror_dry_run: bool = False
    skip_ingest: bool = False
    since_days: int | None = None


def parse_refresh_dashboard_wrapper_args(argv: list[str]) -> RefreshDashboardOptions:
    apply = False
    no_mirror = False
    mirror_dry_run = False
    skip_ingest = False
    since_days: int | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-h", "--help"):
            i += 1
            continue
        if tok == "--apply":
            apply = True
            i += 1
            continue
        if tok == "--no-mirror":
            no_mirror = True
            i += 1
            continue
        if tok == "--mirror-dry-run":
            mirror_dry_run = True
            i += 1
            continue
        if tok == "--skip-ingest":
            skip_ingest = True
            i += 1
            continue
        if tok == "--since-days":
            if i + 1 >= len(argv):
                raise ValueError("refresh-dashboard --since-days requires a value")
            since_days = int(argv[i + 1])
            i += 2
            continue
        if tok.startswith("-"):
            raise ValueError(f"refresh-dashboard: unknown flag {tok!r}")
        raise ValueError(f"refresh-dashboard: unexpected argument {tok!r}")
    if mirror_dry_run and no_mirror:
        raise ValueError("refresh-dashboard: --mirror-dry-run and --no-mirror are mutually exclusive")
    if since_days is not None and since_days < 1:
        raise ValueError("refresh-dashboard --since-days must be a positive integer")
    return RefreshDashboardOptions(
        apply=apply,
        no_mirror=no_mirror,
        mirror_dry_run=mirror_dry_run,
        skip_ingest=skip_ingest,
        since_days=since_days,
    )


def _gmail_ingest_step(options: RefreshDashboardOptions) -> RefreshDashboardStep:
    ingest_pt: tuple[str, ...] = ()
    ingest_label = "gmail-ingest"
    if options.since_days is not None:
        ingest_pt = ("--", "--since-days", str(options.since_days))
        ingest_label = f"gmail-ingest -- --since-days {options.since_days}"
    return RefreshDashboardStep(label=ingest_label, command="gmail-ingest", passthrough=ingest_pt)


def _refresh_dashboard_post_mart_steps() -> list[RefreshDashboardStep]:
    return [
        RefreshDashboardStep(label="build-commercial-intel", command="build-commercial-intel"),
        RefreshDashboardStep(label="refresh-safety", command="refresh-safety"),
        RefreshDashboardStep(label="ndr-review", command="ndr-review"),
        RefreshDashboardStep(label="post-send-digest", command="post-send-digest"),
        RefreshDashboardStep(label="status", command="status"),
    ]


def _daily_core_mart_steps() -> list[RefreshDashboardStep]:
    return [
        RefreshDashboardStep(
            label="build-email-mart-features --missing-only --apply",
            command="build-email-mart-features",
            passthrough=("--", "--missing-only", "--apply"),
        ),
        RefreshDashboardStep(
            label="build-mart -- --rebuild --use-email-mart-features",
            command="build-mart",
            passthrough=("--", "--rebuild", "--use-email-mart-features"),
        ),
    ]


def build_refresh_dashboard_steps(options: RefreshDashboardOptions) -> list[RefreshDashboardStep]:
    """Build ordered workflow steps from options (used for plan and apply)."""
    steps: list[RefreshDashboardStep] = []
    if not options.skip_ingest:
        steps.append(_gmail_ingest_step(options))
    steps.append(
        RefreshDashboardStep(
            label="build-mart -- --rebuild",
            command="build-mart",
            passthrough=("--", "--rebuild"),
        )
    )
    steps.extend(_refresh_dashboard_post_mart_steps())
    if not options.no_mirror:
        if options.mirror_dry_run:
            steps.append(RefreshDashboardStep(label="mirror-dashboard", command="mirror-dashboard"))
        else:
            steps.append(
                RefreshDashboardStep(
                    label="mirror-dashboard --apply",
                    command="mirror-dashboard",
                    mirror_apply=True,
                )
            )
    return steps


def build_daily_core_steps(options: RefreshDashboardOptions) -> list[RefreshDashboardStep]:
    """Build daily-core workflow steps (feature refresh + feature-backed mart rebuild)."""
    steps: list[RefreshDashboardStep] = []
    if not options.skip_ingest:
        steps.append(_gmail_ingest_step(options))
    steps.extend(_daily_core_mart_steps())
    steps.extend(_refresh_dashboard_post_mart_steps())
    return steps


def refresh_dashboard_usage_line(*flags: str) -> str:
    """Example command line for plan/help; always keeps a space before flags."""
    return f"  {REFRESH_DASHBOARD_USAGE} {' '.join(flags)}"


def daily_core_usage_line(*flags: str) -> str:
    return f"  {DAILY_CORE_USAGE} {' '.join(flags)}"


def parse_daily_core_wrapper_args(argv: list[str]) -> RefreshDashboardOptions:
    """Parse daily-core flags; always forces ``no_mirror=True``."""
    opts = parse_refresh_dashboard_wrapper_args(argv)
    if opts.mirror_dry_run:
        raise ValueError(
            "daily-core: --mirror-dry-run is not supported (daily core never runs mirror). "
            "Use refresh-dashboard --apply --mirror-dry-run or mirror-dashboard separately."
        )
    return RefreshDashboardOptions(
        apply=opts.apply,
        no_mirror=True,
        mirror_dry_run=False,
        skip_ingest=opts.skip_ingest,
        since_days=opts.since_days,
    )


def print_refresh_dashboard_plan(
    steps: list[RefreshDashboardStep],
    options: RefreshDashboardOptions,
    *,
    workflow_label: str = "refresh-dashboard",
) -> None:
    total = len(steps)
    print(
        f"{workflow_label} — plan only (no Gmail ingest, mart rebuild, commercial intel, or Postgres writes)\n"
    )
    print(f"Planned steps ({total}) when you pass --apply:")
    for i, step in enumerate(steps, 1):
        note = ""
        if step.command == "build-mart":
            note = "  # break-glass: deletes mart tables"
        elif step.command == "build-email-mart-features":
            note = "  # inserts missing email_mart_features rows only"
        print(f"  {i}/{total} {step.label}{note}")
    if workflow_label == "daily-core":
        print(
            "\nMart path: feature refresh + --use-email-mart-features "
            "(refresh-dashboard --apply --no-mirror still uses legacy body scan)."
        )
        print("\nVariants:")
        print(daily_core_usage_line("--apply"))
        print(daily_core_usage_line("--apply", "--skip-ingest"))
        if options.since_days is None:
            print(daily_core_usage_line("--apply", "--since-days", "14"))
        print("\nNever includes mirror-dashboard. Use mirror-dashboard --apply separately when needed.")
        print("No alembic in this workflow.")
        return
    print("\nVariants:")
    print(refresh_dashboard_usage_line("--apply"))
    print(refresh_dashboard_usage_line("--apply", "--no-mirror"))
    print(refresh_dashboard_usage_line("--apply", "--mirror-dry-run"))
    print(refresh_dashboard_usage_line("--apply", "--skip-ingest"))
    if options.since_days is None:
        print(refresh_dashboard_usage_line("--apply", "--since-days", "14"))
    print("\nNo alembic in this workflow; use mirror-dashboard --alembic --apply separately if needed.")


def print_daily_core_help() -> None:
    print(
        "daily-core — canonical daily operating alias (SQLite + reports, no Postgres mirror)\n\n"
        f"{daily_core_usage_line()}                              # plan only (safe default)\n"
        f"{daily_core_usage_line('--apply')}                      # feature-backed mart rebuild\n"
        f"{daily_core_usage_line('--apply', '--skip-ingest')}\n"
        f"{daily_core_usage_line('--apply', '--since-days', '14')}\n\n"
        "Runs the daily core steps only (no mirror-dashboard). "
        "See docs/pipeline/DAILY_CORE.md.\n"
    )


def run_daily_core(
    options: RefreshDashboardOptions,
    runner: SubcommandRunner | None = None,
) -> int:
    """Run daily-core workflow (always ``no_mirror=True``)."""
    if options.mirror_dry_run:
        raise ValueError(
            "daily-core: --mirror-dry-run is not supported (daily core never runs mirror). "
            "Use refresh-dashboard --apply --mirror-dry-run or mirror-dashboard separately."
        )
    forced = RefreshDashboardOptions(
        apply=options.apply,
        no_mirror=True,
        mirror_dry_run=False,
        skip_ingest=options.skip_ingest,
        since_days=options.since_days,
    )
    if not forced.apply:
        return run_refresh_dashboard(forced, runner=runner, workflow_label="daily-core")

    step_results: list[StepResult] = []
    rc = run_refresh_dashboard(
        forced,
        runner=runner,
        workflow_label="daily-core",
        step_results=step_results,
    )
    write_daily_core_run_manifest(
        step_results=step_results,
        returncode=rc,
        skip_ingest=forced.skip_ingest,
        since_days=forced.since_days,
    )
    # Non-critical operator evidence only — never a 9th workflow step and never
    # changes daily-core return codes on telemetry failure.
    if rc == 0:
        try:
            collect_and_store_sqlite_storage_evidence()
        except Exception:
            pass
    return rc


def print_refresh_dashboard_help() -> None:
    print(
        "refresh-dashboard — orchestrated operator stack refresh\n\n"
        f"{refresh_dashboard_usage_line()}                    # plan only (default)\n"
        f"{refresh_dashboard_usage_line('--apply')}            # full workflow + mirror apply\n"
        f"{refresh_dashboard_usage_line('--apply', '--no-mirror')}\n"
        f"{refresh_dashboard_usage_line('--apply', '--mirror-dry-run')}\n"
        f"{refresh_dashboard_usage_line('--apply', '--skip-ingest')}\n"
        f"{refresh_dashboard_usage_line('--apply', '--since-days', '14')}\n\n"
        "Includes build-mart -- --rebuild (break-glass) and incremental build-commercial-intel. "
        "No alembic in this workflow.\n"
    )


def run_refresh_dashboard(
    options: RefreshDashboardOptions,
    runner: SubcommandRunner | None = None,
    *,
    workflow_label: str = "refresh-dashboard",
    step_results: list[StepResult] | None = None,
) -> int:
    """Run refresh-dashboard plan or apply workflow via existing CLI subcommands."""
    from origenlab_email_pipeline.operator_cli.runner import run_subcommand

    execute = runner or run_subcommand
    if workflow_label == "daily-core":
        steps = build_daily_core_steps(options)
    else:
        steps = build_refresh_dashboard_steps(options)
    if not options.apply:
        print_refresh_dashboard_plan(steps, options, workflow_label=workflow_label)
        return 0

    def _run_step(step: RefreshDashboardStep) -> int:
        return execute(
            step.command,
            list(step.passthrough) or None,
            mirror_apply=step.mirror_apply,
            mirror_alembic=step.mirror_alembic,
        )

    return run_step_sequence(
        steps,
        _run_step,
        prefix=f"[{workflow_label}]",
        step_results=step_results,
    )
