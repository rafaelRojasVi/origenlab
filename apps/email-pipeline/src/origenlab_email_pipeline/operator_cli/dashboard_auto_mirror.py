"""Debounced dashboard mirror automation — publish after successful daily-core."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.operator_cli.daily_core_manifest import daily_core_run_manifest_path
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import (
    MailAutoRefreshState,
    _process_alive,
    acquire_lock,
    active_current_dir,
    load_state as load_mail_auto_refresh_state,
    lock_path as mail_auto_refresh_lock_path,
    pause_path as mail_auto_refresh_pause_path,
    read_lock,
    release_lock,
    state_path as mail_auto_refresh_state_path,
)
from origenlab_email_pipeline.operator_cli.mirror import (
    build_live_dashboard_passthrough,
    postgres_url_configured,
    run_mirror_dashboard,
)

STATE_FILENAME = "dashboard_auto_mirror_state.json"
LOCK_FILENAME = "dashboard_auto_mirror.lock"
PAUSE_FILENAME = "dashboard_auto_mirror_paused"
STATE_SCHEMA_VERSION = 1
STALE_LOCK_SECONDS = 2 * 60 * 60
ACTIVE_MANIFEST_FILENAME = "manifest.json"
EQUIPMENT_OPERATOR_QUEUE_GLOB = "equipment_first_operator_queue_*.csv"
MAX_DASHBOARD_INPUT_CONTENT_HASH_BYTES = 256 * 1024

DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_OPERATOR = "rafael"
DEFAULT_REASON = "Automated dashboard mirror after successful daily-core"

MirrorRunnerFn = Callable[[], int]
NowFn = Callable[[], datetime]


@dataclass(frozen=True)
class DashboardAutoMirrorOptions:
    apply: bool = False
    once: bool = True
    daemon: bool = False
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    operator: str = DEFAULT_OPERATOR
    reason: str = DEFAULT_REASON
    allow_non_scratch_postgres: bool = False


@dataclass
class DashboardAutoMirrorState:
    schema_version: int = STATE_SCHEMA_VERSION
    last_successful_mirror_at: str | None = None
    last_mirrored_daily_core_generated_at: str | None = None
    last_mirrored_dashboard_input_fingerprint: str | None = None
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    last_result: str | None = None
    consecutive_failures: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DashboardAutoMirrorState:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: data[k] for k in data if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DashboardAutoMirrorResult:
    apply: bool
    reason: str
    daily_core_status: str | None
    daily_core_returncode: int | None
    daily_core_generated_at: str | None
    mail_dirty: bool
    mail_pending: bool
    last_mirrored_daily_core_generated_at: str | None
    last_mirrored_dashboard_input_fingerprint: str | None
    dashboard_input_fingerprint: str | None
    last_successful_mirror_at: str | None
    cooldown_seconds: int
    should_run: bool
    ran_mirror: bool
    allow_non_scratch_postgres: bool
    mirror_returncode: int | None = None

    def output_lines(self) -> list[str]:
        lines = [
            "dashboard_auto_mirror",
            f"apply={'true' if self.apply else 'false'}",
            f"reason={self.reason}",
            f"daily_core_status={self.daily_core_status or ''}",
            f"daily_core_returncode={self.daily_core_returncode if self.daily_core_returncode is not None else ''}",
            f"daily_core_generated_at={self.daily_core_generated_at or ''}",
            f"mail_dirty={'true' if self.mail_dirty else 'false'}",
            f"mail_pending={'true' if self.mail_pending else 'false'}",
            f"last_mirrored_daily_core_generated_at={self.last_mirrored_daily_core_generated_at or ''}",
            f"last_mirrored_dashboard_input_fingerprint={self.last_mirrored_dashboard_input_fingerprint or ''}",
            f"dashboard_input_fingerprint={self.dashboard_input_fingerprint or ''}",
            f"last_successful_mirror_at={self.last_successful_mirror_at or ''}",
            f"cooldown_seconds={self.cooldown_seconds}",
            f"should_run={'true' if self.should_run else 'false'}",
            f"ran_mirror={'true' if self.ran_mirror else 'false'}",
            f"allow_non_scratch_postgres={'true' if self.allow_non_scratch_postgres else 'false'}",
        ]
        if self.mirror_returncode is not None:
            lines.append(f"mirror_returncode={self.mirror_returncode}")
        return lines


def state_path(reports_dir: Path | None = None) -> Path:
    return active_current_dir(reports_dir) / STATE_FILENAME


def lock_path(reports_dir: Path | None = None) -> Path:
    return active_current_dir(reports_dir) / LOCK_FILENAME


def pause_path(reports_dir: Path | None = None) -> Path:
    return active_current_dir(reports_dir) / PAUSE_FILENAME


def load_state(path: Path) -> DashboardAutoMirrorState:
    if not path.is_file():
        return DashboardAutoMirrorState()
    data = json.loads(path.read_text(encoding="utf-8"))
    return DashboardAutoMirrorState.from_dict(data)


def save_state(path: Path, state: DashboardAutoMirrorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _iso_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _seconds_since(ts: str | None, now: datetime) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds()


def _lock_is_live(lock_file: Path) -> bool:
    existing = read_lock(lock_file)
    if not existing:
        return False
    pid = int(existing.get("pid") or -1)
    return _process_alive(pid)


def load_daily_core_manifest(reports_dir: Path | None = None) -> dict[str, Any] | None:
    path = daily_core_run_manifest_path(reports_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _mail_pending(mail_state: MailAutoRefreshState) -> bool:
    return any(
        value is not None
        for value in (
            mail_state.pending_inbox_total,
            mail_state.pending_sent_total,
            mail_state.pending_inbox_max_uid,
            mail_state.pending_sent_max_uid,
        )
    )


def _already_mirrored_daily_core(
    *,
    last_mirrored_at: str | None,
    daily_core_generated_at: str,
) -> bool:
    if not last_mirrored_at:
        return False
    mirrored = _parse_iso(last_mirrored_at)
    generated = _parse_iso(daily_core_generated_at)
    if mirrored is None or generated is None:
        return last_mirrored_at == daily_core_generated_at
    return mirrored >= generated


def _resolve_dashboard_input_files(active_current: Path) -> list[Path]:
    files: list[Path] = []
    manifest_path = active_current / ACTIVE_MANIFEST_FILENAME
    if manifest_path.is_file():
        files.append(manifest_path)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
        for name in manifest.get("canonical_files") or []:
            rel = str(name).strip()
            if not rel:
                continue
            candidate = active_current / rel
            if candidate.is_file() and candidate not in files:
                files.append(candidate)
        return sorted(files, key=lambda path: path.name)
    for path in sorted(active_current.glob(EQUIPMENT_OPERATOR_QUEUE_GLOB)):
        if path.is_file():
            files.append(path)
    return files


def _dashboard_input_file_entry(path: Path) -> str:
    stat = path.stat()
    content_hash = ""
    if stat.st_size <= MAX_DASHBOARD_INPUT_CONTENT_HASH_BYTES:
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}|{content_hash}"


def compute_dashboard_input_fingerprint(reports_dir: Path | None = None) -> str:
    """Hash stable metadata/content for dashboard-visible active/current inputs."""
    active_current = active_current_dir(reports_dir)
    entries = [_dashboard_input_file_entry(path) for path in _resolve_dashboard_input_files(active_current)]
    payload = "\n".join(entries) if entries else "no_dashboard_inputs"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _already_mirrored(
    *,
    last_mirrored_at: str | None,
    daily_core_generated_at: str,
    last_dashboard_input_fingerprint: str | None,
    current_dashboard_input_fingerprint: str,
) -> bool:
    if not _already_mirrored_daily_core(
        last_mirrored_at=last_mirrored_at,
        daily_core_generated_at=daily_core_generated_at,
    ):
        return False
    if not last_dashboard_input_fingerprint:
        return False
    return last_dashboard_input_fingerprint == current_dashboard_input_fingerprint


def _base_result_fields(
    *,
    options: DashboardAutoMirrorOptions,
    mirror_state: DashboardAutoMirrorState,
    manifest: dict[str, Any] | None,
    mail_state: MailAutoRefreshState | None,
) -> dict[str, Any]:
    mail_dirty = bool(mail_state.dirty) if mail_state else False
    mail_pending = _mail_pending(mail_state) if mail_state else False
    return {
        "apply": options.apply,
        "daily_core_status": manifest.get("status") if manifest else None,
        "daily_core_returncode": manifest.get("returncode") if manifest else None,
        "daily_core_generated_at": manifest.get("generated_at_utc") if manifest else None,
        "mail_dirty": mail_dirty,
        "mail_pending": mail_pending,
        "last_mirrored_daily_core_generated_at": mirror_state.last_mirrored_daily_core_generated_at,
        "last_mirrored_dashboard_input_fingerprint": mirror_state.last_mirrored_dashboard_input_fingerprint,
        "dashboard_input_fingerprint": None,
        "last_successful_mirror_at": mirror_state.last_successful_mirror_at,
        "cooldown_seconds": options.cooldown_seconds,
        "allow_non_scratch_postgres": options.allow_non_scratch_postgres,
        "ran_mirror": False,
        "mirror_returncode": None,
    }


def evaluate_dashboard_auto_mirror(
    *,
    options: DashboardAutoMirrorOptions,
    mirror_state: DashboardAutoMirrorState,
    manifest: dict[str, Any] | None,
    mail_state: MailAutoRefreshState | None,
    now: datetime,
    dashboard_input_fingerprint: str,
) -> DashboardAutoMirrorResult:
    base = _base_result_fields(
        options=options,
        mirror_state=mirror_state,
        manifest=manifest,
        mail_state=mail_state,
    )
    base["dashboard_input_fingerprint"] = dashboard_input_fingerprint

    if manifest is None:
        return DashboardAutoMirrorResult(reason="daily_core_manifest_missing", should_run=False, **base)

    status = manifest.get("status")
    returncode = manifest.get("returncode")
    generated_at = manifest.get("generated_at_utc")

    if status != "success" or returncode != 0:
        return DashboardAutoMirrorResult(reason="daily_core_failed", should_run=False, **base)

    if not generated_at:
        return DashboardAutoMirrorResult(reason="daily_core_manifest_invalid", should_run=False, **base)

    if mail_state is None:
        return DashboardAutoMirrorResult(reason="mail_state_missing", should_run=False, **base)

    if mail_state.dirty:
        return DashboardAutoMirrorResult(reason="mail_dirty", should_run=False, **base)

    if _mail_pending(mail_state):
        return DashboardAutoMirrorResult(reason="mail_pending", should_run=False, **base)

    if _already_mirrored(
        last_mirrored_at=mirror_state.last_mirrored_daily_core_generated_at,
        daily_core_generated_at=generated_at,
        last_dashboard_input_fingerprint=mirror_state.last_mirrored_dashboard_input_fingerprint,
        current_dashboard_input_fingerprint=dashboard_input_fingerprint,
    ):
        return DashboardAutoMirrorResult(reason="already_mirrored", should_run=False, **base)

    cooldown_elapsed = _seconds_since(mirror_state.last_successful_mirror_at, now)
    if cooldown_elapsed is not None and cooldown_elapsed < options.cooldown_seconds:
        return DashboardAutoMirrorResult(reason="cooldown", should_run=False, **base)

    if options.apply and not options.allow_non_scratch_postgres:
        return DashboardAutoMirrorResult(reason="allow_non_scratch_required", should_run=False, **base)

    if not options.apply:
        return DashboardAutoMirrorResult(reason="dry_run", should_run=True, **base)

    return DashboardAutoMirrorResult(reason="ready", should_run=True, **base)


def build_mirror_passthrough(options: DashboardAutoMirrorOptions) -> list[str]:
    return build_live_dashboard_passthrough(
        updated_by=options.operator,
        reason=options.reason,
        passthrough=["--allow-non-scratch-postgres"],
    )


def _publish_automation_status_snapshot_if_configured(
    options: DashboardAutoMirrorOptions,
    reports_dir: Path | None,
) -> None:
    """Publish operator automation status to Postgres after local state is saved."""
    if not options.apply or not options.allow_non_scratch_postgres:
        return
    if not postgres_url_configured():
        return
    try:
        from origenlab_email_pipeline.dashboard_snapshot_publish import (
            OPERATOR_AUTOMATION_STATUS_SNAPSHOT_KV_KEY,
            build_operator_automation_status_snapshot,
            upsert_pipeline_kv_snapshot,
        )
        from origenlab_email_pipeline.mart_core_postgres_migrate import resolve_postgres_url

        pg_url = resolve_postgres_url(None)
        active = active_current_dir(reports_dir)
        snapshot = build_operator_automation_status_snapshot(
            active,
            mirror_cooldown_seconds=options.cooldown_seconds,
        )
        upsert_pipeline_kv_snapshot(
            pg_url,
            OPERATOR_AUTOMATION_STATUS_SNAPSHOT_KV_KEY,
            snapshot,
            dry_run=False,
        )
    except Exception as exc:  # pragma: no cover - exercised via mock in unit tests
        print(f"automation_status_snapshot_publish_failed={exc}", file=sys.stderr)


def run_dashboard_auto_mirror(
    options: DashboardAutoMirrorOptions,
    *,
    reports_dir: Path | None = None,
    run_mirror_fn: MirrorRunnerFn | None = None,
    now_fn: NowFn | None = None,
) -> int:
    if options.daemon:
        raise ValueError(
            "auto-mirror-dashboard --daemon is not implemented yet; use --once with an external scheduler "
            "(cron/systemd timer every minute; default cooldown 60s)."
        )
    if not options.once:
        raise ValueError("auto-mirror-dashboard requires --once (daemon mode not implemented yet).")

    now = (now_fn or (lambda: datetime.now(timezone.utc)))()

    if pause_path(reports_dir).is_file() or mail_auto_refresh_pause_path(reports_dir).is_file():
        result = DashboardAutoMirrorResult(
            apply=options.apply,
            reason="paused",
            daily_core_status=None,
            daily_core_returncode=None,
            daily_core_generated_at=None,
            mail_dirty=False,
            mail_pending=False,
            last_mirrored_daily_core_generated_at=None,
            last_mirrored_dashboard_input_fingerprint=None,
            dashboard_input_fingerprint=None,
            last_successful_mirror_at=None,
            cooldown_seconds=options.cooldown_seconds,
            should_run=False,
            ran_mirror=False,
            allow_non_scratch_postgres=options.allow_non_scratch_postgres,
        )
        for line in result.output_lines():
            print(line)
        return 0

    if _lock_is_live(mail_auto_refresh_lock_path(reports_dir)):
        result = DashboardAutoMirrorResult(
            apply=options.apply,
            reason="mail_refresh_running",
            daily_core_status=None,
            daily_core_returncode=None,
            daily_core_generated_at=None,
            mail_dirty=False,
            mail_pending=False,
            last_mirrored_daily_core_generated_at=None,
            last_mirrored_dashboard_input_fingerprint=None,
            dashboard_input_fingerprint=None,
            last_successful_mirror_at=None,
            cooldown_seconds=options.cooldown_seconds,
            should_run=False,
            ran_mirror=False,
            allow_non_scratch_postgres=options.allow_non_scratch_postgres,
        )
        for line in result.output_lines():
            print(line)
        return 0

    mirror_lock = lock_path(reports_dir)
    if _lock_is_live(mirror_lock):
        result = DashboardAutoMirrorResult(
            apply=options.apply,
            reason="already_running",
            daily_core_status=None,
            daily_core_returncode=None,
            daily_core_generated_at=None,
            mail_dirty=False,
            mail_pending=False,
            last_mirrored_daily_core_generated_at=None,
            last_mirrored_dashboard_input_fingerprint=None,
            dashboard_input_fingerprint=None,
            last_successful_mirror_at=None,
            cooldown_seconds=options.cooldown_seconds,
            should_run=False,
            ran_mirror=False,
            allow_non_scratch_postgres=options.allow_non_scratch_postgres,
        )
        for line in result.output_lines():
            print(line)
        return 0

    acquired, _ = acquire_lock(mirror_lock, now=now, stale_seconds=STALE_LOCK_SECONDS)
    if not acquired:
        result = DashboardAutoMirrorResult(
            apply=options.apply,
            reason="already_running",
            daily_core_status=None,
            daily_core_returncode=None,
            daily_core_generated_at=None,
            mail_dirty=False,
            mail_pending=False,
            last_mirrored_daily_core_generated_at=None,
            last_mirrored_dashboard_input_fingerprint=None,
            dashboard_input_fingerprint=None,
            last_successful_mirror_at=None,
            cooldown_seconds=options.cooldown_seconds,
            should_run=False,
            ran_mirror=False,
            allow_non_scratch_postgres=options.allow_non_scratch_postgres,
        )
        for line in result.output_lines():
            print(line)
        return 0

    state_file = state_path(reports_dir)
    mirror_state = load_state(state_file)
    mirror_state.last_run_started_at = _iso_now(now)

    exit_rc = 0
    should_publish_snapshot = False
    try:
        manifest = load_daily_core_manifest(reports_dir)
        mail_state_path = mail_auto_refresh_state_path(reports_dir)
        mail_state = (
            load_mail_auto_refresh_state(mail_state_path)
            if mail_state_path.is_file()
            else None
        )
        dashboard_input_fingerprint = compute_dashboard_input_fingerprint(reports_dir)

        result = evaluate_dashboard_auto_mirror(
            options=options,
            mirror_state=mirror_state,
            manifest=manifest,
            mail_state=mail_state,
            now=now,
            dashboard_input_fingerprint=dashboard_input_fingerprint,
        )

        mirror_rc: int | None = None
        if result.should_run and options.apply:
            passthrough = build_mirror_passthrough(options)
            runner = run_mirror_fn or (
                lambda: run_mirror_dashboard(apply=True, alembic=False, passthrough=passthrough)
            )
            mirror_rc = runner()
            result.ran_mirror = True
            result.mirror_returncode = mirror_rc
            finished = (now_fn or (lambda: datetime.now(timezone.utc)))()
            mirror_state.last_run_finished_at = _iso_now(finished)
            generated_at = manifest.get("generated_at_utc") if manifest else None
            if mirror_rc == 0 and generated_at:
                mirror_state.last_successful_mirror_at = mirror_state.last_run_finished_at
                mirror_state.last_mirrored_daily_core_generated_at = generated_at
                mirror_state.last_mirrored_dashboard_input_fingerprint = dashboard_input_fingerprint
                mirror_state.consecutive_failures = 0
                mirror_state.last_result = "success"
                result.last_successful_mirror_at = mirror_state.last_successful_mirror_at
                result.last_mirrored_daily_core_generated_at = (
                    mirror_state.last_mirrored_daily_core_generated_at
                )
                result.last_mirrored_dashboard_input_fingerprint = (
                    mirror_state.last_mirrored_dashboard_input_fingerprint
                )
                result.reason = "mirrored"
            else:
                mirror_state.consecutive_failures += 1
                mirror_state.last_result = "mirror_failed"
                result.reason = "mirror_failed"
        else:
            mirror_state.last_run_finished_at = _iso_now(
                (now_fn or (lambda: datetime.now(timezone.utc)))()
            )
            mirror_state.last_result = result.reason

        save_state(state_file, mirror_state)
        should_publish_snapshot = True
        for line in result.output_lines():
            print(line)
        if mirror_rc is not None and mirror_rc != 0:
            exit_rc = mirror_rc
    finally:
        release_lock(mirror_lock)
        if should_publish_snapshot:
            _publish_automation_status_snapshot_if_configured(options, reports_dir)

    return exit_rc


def parse_dashboard_auto_mirror_args(argv: list[str]) -> DashboardAutoMirrorOptions:
    import argparse

    parser = argparse.ArgumentParser(prog="auto-mirror-dashboard", add_help=True)
    parser.add_argument("--apply", action="store_true", help="Run mirror-dashboard when gates pass")
    parser.add_argument("--once", action="store_true", help="Single evaluation (required)")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Loop mode (not implemented — use external scheduler with --once)",
    )
    parser.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--operator", default=DEFAULT_OPERATOR)
    parser.add_argument("--reason", default=DEFAULT_REASON)
    parser.add_argument(
        "--allow-non-scratch-postgres",
        action="store_true",
        help="Required with --apply before writing to non-scratch Postgres",
    )
    ns = parser.parse_args(argv)
    if not ns.once and not ns.daemon:
        parser.error("--once is required (daemon mode not implemented yet)")
    return DashboardAutoMirrorOptions(
        apply=ns.apply,
        once=ns.once,
        daemon=ns.daemon,
        cooldown_seconds=ns.cooldown_seconds,
        operator=ns.operator,
        reason=ns.reason,
        allow_non_scratch_postgres=ns.allow_non_scratch_postgres,
    )


def print_dashboard_auto_mirror_help() -> None:
    print(
        "auto-mirror-dashboard — debounced Postgres/dashboard publish after daily-core\n\n"
        "  uv run origenlab auto-mirror-dashboard --once\n"
        "  uv run origenlab auto-mirror-dashboard --once --apply "
        "--allow-non-scratch-postgres\n\n"
        "Separate publishing loop (not part of auto-refresh-mail). "
        "See docs/pipeline/DASHBOARD_AUTO_MIRROR.md.\n"
    )
