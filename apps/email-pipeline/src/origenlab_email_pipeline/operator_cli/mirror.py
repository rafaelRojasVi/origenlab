"""Postgres dashboard mirror CLI builders and runners."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

from origenlab_email_pipeline.core.step_runner import run_step_sequence
from origenlab_email_pipeline.operator_cli.constants import (
    MIRROR_DASHBOARD_SYNC_SCRIPT,
    POSTGRES_ENV_VARS,
)
from origenlab_email_pipeline.operator_cli.paths import (
    mirror_dashboard_sync_script_path,
    normalize_passthrough_args,
    repo_root,
)

# Equipment opportunities are now direct-published by auto-refresh-chilecompra-equipment
# when Postgres is configured. Keep --include-equipment-opportunities available as an
# explicit legacy/backfill passthrough, but do not reopen the CSV export in the normal
# live mirror loop.
LIVE_DASHBOARD_FLAGS = (
    "--include-warm-cases",
    "--include-commercial-deals",
    "--include-operator-snapshots",
)

LIVE_WARM_DAYS = "14"
LIVE_WARM_LIMIT = "100"


@dataclass(frozen=True)
class _MirrorDashboardStep:
    label: str
    argv: list[str]


def postgres_url_configured() -> bool:
    return any((os.environ.get(name) or "").strip() for name in POSTGRES_ENV_VARS)


def missing_postgres_env_message() -> str:
    names = " or ".join(POSTGRES_ENV_VARS)
    return (
        f"mirror-dashboard requires {names} to be set (see .env.example). "
        "Postgres mirror is optional reporting only — not send approval."
    )


def mirror_dashboard_uses_cloud_postgres_only() -> bool:
    """True when sync resolves ORIGENLAB_CLOUD_POSTGRES_URL (no scratch URL env set)."""
    if (os.environ.get("ORIGENLAB_POSTGRES_URL") or "").strip():
        return False
    if (os.environ.get("ALEMBIC_DATABASE_URL") or "").strip():
        return False
    return bool((os.environ.get("ORIGENLAB_CLOUD_POSTGRES_URL") or "").strip())


def mirror_dashboard_sync_safety_flags() -> list[str]:
    """Extra sync flags when target is non-scratch (e.g. Render cloud mirror)."""
    if mirror_dashboard_uses_cloud_postgres_only():
        return ["--allow-non-scratch-postgres"]
    return []


def _passthrough_flag_value(passthrough: list[str], flag: str) -> str | None:
    for index, token in enumerate(passthrough):
        if token == flag and index + 1 < len(passthrough):
            return passthrough[index + 1]
    return None


def _passthrough_has_flag(passthrough: list[str], flag: str) -> bool:
    return flag in passthrough


def _effective_updated_by(updated_by: str | None, passthrough: list[str]) -> str | None:
    if updated_by:
        return updated_by
    for flag in ("--updated-by", "--operator"):
        value = _passthrough_flag_value(passthrough, flag)
        if value:
            return value
    return None


def _effective_reason(reason: str | None, passthrough: list[str]) -> str | None:
    if reason:
        return reason
    return _passthrough_flag_value(passthrough, "--reason")


def build_live_dashboard_passthrough(
    *,
    updated_by: str | None,
    reason: str | None,
    passthrough: list[str],
) -> list[str]:
    result = normalize_passthrough_args(passthrough)
    for flag in LIVE_DASHBOARD_FLAGS:
        if flag not in result:
            result.append(flag)
    if not _passthrough_has_flag(result, "--warm-days"):
        result.extend(["--warm-days", LIVE_WARM_DAYS])
    if not _passthrough_has_flag(result, "--warm-limit"):
        result.extend(["--warm-limit", LIVE_WARM_LIMIT])
    if not _passthrough_has_flag(result, "--close-missing-warm-cases"):
        result.append("--close-missing-warm-cases")
    if updated_by and not (
        _passthrough_has_flag(result, "--updated-by")
        or _passthrough_has_flag(result, "--operator")
    ):
        result.extend(["--updated-by", updated_by])
    if reason and not _passthrough_has_flag(result, "--reason"):
        result.extend(["--reason", reason])
    return result


def parse_mirror_dashboard_wrapper_args(argv: list[str]) -> tuple[bool, bool, list[str]]:
    """Return ``(apply, alembic, passthrough)`` for mirror-dashboard wrapper flags."""
    apply = False
    alembic = False
    live = False
    updated_by: str | None = None
    reason: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            rest.extend(argv[i:])
            break
        if tok in ("-h", "--help"):
            rest.append(tok)
            i += 1
            continue
        if tok == "--apply":
            apply = True
            i += 1
            continue
        if tok == "--alembic":
            alembic = True
            i += 1
            continue
        if tok == "--live":
            live = True
            i += 1
            continue
        if tok in ("--operator", "--updated-by"):
            if i + 1 >= len(argv):
                raise ValueError(f"mirror-dashboard {tok} requires a value")
            updated_by = argv[i + 1]
            i += 2
            continue
        if tok == "--reason":
            if i + 1 >= len(argv):
                raise ValueError("mirror-dashboard --reason requires a value")
            reason = argv[i + 1]
            i += 2
            continue
        if tok.startswith("-"):
            raise ValueError(f"mirror-dashboard: unknown flag {tok!r}")
        rest.append(tok)
        i += 1
    if alembic and not apply:
        raise ValueError("mirror-dashboard --alembic requires --apply")
    if not live and (updated_by is not None or reason is not None):
        raise ValueError(
            "mirror-dashboard --operator/--updated-by/--reason require --live; "
            "pass advanced sync flags after `--` when not using --live"
        )
    passthrough = normalize_passthrough_args(rest)
    if live and apply:
        if not _effective_updated_by(updated_by, passthrough):
            raise ValueError(
                "mirror-dashboard --live --apply requires --operator or --updated-by"
            )
        if not _effective_reason(reason, passthrough):
            raise ValueError("mirror-dashboard --live --apply requires --reason")
    if live:
        passthrough = build_live_dashboard_passthrough(
            updated_by=updated_by,
            reason=reason,
            passthrough=passthrough,
        )
    return apply, alembic, passthrough


def build_mirror_dashboard_sync_argv(
    *,
    apply: bool,
    passthrough: list[str] | None = None,
) -> list[str]:
    cmd = [sys.executable, str(mirror_dashboard_sync_script_path())]
    if not apply:
        cmd.append("--dry-run")
    extra = normalize_passthrough_args(passthrough or [])
    for flag in mirror_dashboard_sync_safety_flags():
        if flag not in extra:
            cmd.append(flag)
    cmd.extend(extra)
    return cmd


def build_mirror_dashboard_alembic_argv() -> list[str]:
    return ["alembic", "-c", "alembic.ini", "upgrade", "head"]


def build_mirror_dashboard_argv_list(
    *,
    apply: bool = False,
    alembic: bool = False,
    passthrough: list[str] | None = None,
) -> list[list[str]]:
    """Build argv for optional alembic then sync (no subprocess)."""
    cmds: list[list[str]] = []
    if alembic:
        cmds.append(build_mirror_dashboard_alembic_argv())
    cmds.append(build_mirror_dashboard_sync_argv(apply=apply, passthrough=passthrough))
    return cmds


def _mirror_dashboard_step_label(argv: list[str]) -> str:
    if argv and argv[0] == "alembic":
        return "alembic upgrade head"
    return MIRROR_DASHBOARD_SYNC_SCRIPT


def _mirror_dashboard_steps(
    *,
    apply: bool = False,
    alembic: bool = False,
    passthrough: list[str] | None = None,
) -> list[_MirrorDashboardStep]:
    return [
        _MirrorDashboardStep(label=_mirror_dashboard_step_label(argv), argv=argv)
        for argv in build_mirror_dashboard_argv_list(
            apply=apply,
            alembic=alembic,
            passthrough=passthrough,
        )
    ]


def run_mirror_dashboard(
    *,
    apply: bool = False,
    alembic: bool = False,
    passthrough: list[str] | None = None,
) -> int:
    if not postgres_url_configured():
        print(missing_postgres_env_message(), file=sys.stderr)
        return 2
    cwd = str(repo_root())

    def _run_step(step: _MirrorDashboardStep) -> int:
        proc = subprocess.run(step.argv, cwd=cwd, check=False)
        return int(proc.returncode)

    return run_step_sequence(
        _mirror_dashboard_steps(apply=apply, alembic=alembic, passthrough=passthrough),
        _run_step,
        prefix="[mirror-dashboard]",
    )


def print_mirror_dashboard_help() -> None:
    print(
        "mirror-dashboard — Postgres dashboard mirror (EXPERIMENTAL_PARKED)\n\n"
        "  uv run origenlab mirror-dashboard              # dry-run sync (default)\n"
        "  uv run origenlab mirror-dashboard --apply      # write Postgres mirror (core loaders only)\n"
        "  uv run origenlab mirror-dashboard --live       # dry-run with live dashboard optional loaders\n"
        "  uv run origenlab mirror-dashboard --live --apply --operator rafael "
        '--reason "Daily live dashboard refresh"\n'
        "  uv run origenlab mirror-dashboard --alembic --apply\n"
        "  uv run origenlab mirror-dashboard -- --only mart --json-out path\n\n"
        "--live includes warm cases, commercial deals, and operator snapshots. "
        "Equipment opportunities are direct-published by auto-refresh-chilecompra-equipment; "
        "pass --include-equipment-opportunities after `--` only for legacy/backfill CSV reloads.\n"
        "--live uses the same default warm-case window as Hoy: 14 days / 100 cases "
        "(--warm-days 14 --warm-limit 100) and closes stale warm_queue_promotion rows "
        "missing from the current snapshot (--close-missing-warm-cases).\n"
        "Override with passthrough after `--`, e.g. "
        "mirror-dashboard --live -- --warm-days 30 --warm-limit 200.\n"
        "Dry-run remains the default. --live --apply requires --operator (or --updated-by) and --reason.\n"
        "daily-core remains separate and never runs mirror-dashboard.\n\n"
        f"Requires {' or '.join(POSTGRES_ENV_VARS)}.\n"
        "When only ORIGENLAB_CLOUD_POSTGRES_URL is set, adds --allow-non-scratch-postgres "
        "(Render/cloud target).\n"
        f"Advanced: {MIRROR_DASHBOARD_SYNC_SCRIPT}\n"
    )
