"""Operator-safe ChileCompra equipment queue refresh into canonical dashboard CSV/read model."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from origenlab_email_pipeline.chilecompra_api import (
    ChileCompraTicketMissingError,
    redact_ticket,
    ticket_from_env,
)
from origenlab_email_pipeline.equipment_first_chilecompra_publish import (
    default_canonical_operator_queue_path,
    publish_chilecompra_equipment_queue_for_dashboard,
)
from origenlab_email_pipeline.equipment_first_chilecompra_queue import (
    build_equipment_queue_from_chilecompra_api,
    default_chilecompra_api_queue_csv_path,
    default_chilecompra_candidate_audit_path,
    default_chilecompra_detail_cache_dir,
    write_candidate_audit_csv,
    write_chilecompra_api_queue_outputs,
)
from origenlab_email_pipeline.equipment_first_chilecompra_read_model import (
    apply_chilecompra_equipment_read_model,
    record_chilecompra_read_model_sync,
)
from origenlab_email_pipeline.mart_core_postgres_migrate import resolve_postgres_url
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import (
    acquire_lock,
    active_current_dir,
    read_lock,
    release_lock,
)
from origenlab_email_pipeline.operator_cli.mirror import postgres_url_configured

STATE_FILENAME = "chilecompra_equipment_auto_refresh_state.json"
LOCK_FILENAME = "chilecompra_equipment_auto_refresh.lock"
STATE_SCHEMA_VERSION = 2
STALE_LOCK_SECONDS = 2 * 60 * 60
DEFAULT_COOLDOWN_SECONDS = 7200
DEFAULT_MAX_DETAILS = 50
DEFAULT_DETAIL_SLEEP_SECONDS = 3.0

# Canonical daytime cron slots (host crontab: ``12 8-20/2 * * *``).
CHILECOMPRA_SCHEDULE_TZ = ZoneInfo("America/Santiago")
CHILECOMPRA_SCHEDULE_HOURS = (8, 10, 12, 14, 16, 18, 20)
CHILECOMPRA_SCHEDULE_MINUTE = 12
# Cron/process may start seconds/minutes after :12; still snap to that slot.
CHILECOMPRA_SLOT_START_TOLERANCE_SECONDS = 300
CHILECOMPRA_SLOT_PREEMPT_TOLERANCE_SECONDS = 60

CADENCE_KIND_SCHEDULED_SLOT = "scheduled_slot"
CADENCE_KIND_WALL_CLOCK = "wall_clock"

BuildQueueFn = Callable[..., tuple[list[dict[str, str]], dict[str, Any], list[dict[str, str]]]]
PublishQueueFn = Callable[..., dict[str, Any]]
ReadModelPublishFn = Callable[..., dict[str, Any]]
NowFn = Callable[[], datetime]


@dataclass(frozen=True)
class ChilecompraEquipmentAutoRefreshOptions:
    apply: bool = False
    once: bool = True
    estado: str = "activas"
    fecha: str | None = None
    max_details: int = DEFAULT_MAX_DETAILS
    detail_sleep_seconds: float = DEFAULT_DETAIL_SLEEP_SECONDS
    detail_cache_dir: Path | None = None
    write_candidate_audit: bool = True
    publish: bool = True
    publish_read_model: bool = True
    force: bool = False
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS


@dataclass
class ChilecompraEquipmentAutoRefreshState:
    schema_version: int = STATE_SCHEMA_VERSION
    last_result: str | None = None
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    last_successful_refresh_at: str | None = None
    last_successful_refresh_started_at: str | None = None
    last_successful_scheduled_slot_at: str | None = None
    cadence_anchor_kind: str | None = None
    last_successful_publish_at: str | None = None
    last_successful_read_model_publish_at: str | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    fetched_summaries: int | None = None
    candidate_summaries: int | None = None
    prefilter_skipped_summaries: int | None = None
    detail_requests: int | None = None
    detail_cache_hits: int | None = None
    detail_error_count: int | None = None
    output_rows: int | None = None
    published_rows: int | None = None
    read_model_rows: int | None = None
    read_model_result: str | None = None
    read_model_source_kind: str | None = None
    read_model_source_id: int | None = None
    published_queue: str | None = None
    candidate_audit: str | None = None
    next_recommended_run_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChilecompraEquipmentAutoRefreshState:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: data[k] for k in data if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChilecompraEquipmentAutoRefreshResult:
    apply: bool
    reason: str
    should_run: bool
    ran_refresh: bool
    published: bool
    cooldown_seconds: int
    force: bool
    extra: dict[str, str] = field(default_factory=dict)

    def output_lines(self) -> list[str]:
        lines = [
            "chilecompra_equipment_auto_refresh",
            f"apply={'true' if self.apply else 'false'}",
            f"reason={self.reason}",
            f"should_run={'true' if self.should_run else 'false'}",
            f"ran_refresh={'true' if self.ran_refresh else 'false'}",
            f"published={'true' if self.published else 'false'}",
            f"cooldown_seconds={self.cooldown_seconds}",
            f"force={'true' if self.force else 'false'}",
        ]
        for key, value in sorted(self.extra.items()):
            lines.append(f"{key}={value}")
        return lines


def state_path(reports_dir: Path | None = None) -> Path:
    return active_current_dir(reports_dir) / STATE_FILENAME


def lock_path(reports_dir: Path | None = None) -> Path:
    return active_current_dir(reports_dir) / LOCK_FILENAME


def load_state(path: Path) -> ChilecompraEquipmentAutoRefreshState:
    if not path.is_file():
        return ChilecompraEquipmentAutoRefreshState()
    data = json.loads(path.read_text(encoding="utf-8"))
    return ChilecompraEquipmentAutoRefreshState.from_dict(data)


def save_state(path: Path, state: ChilecompraEquipmentAutoRefreshState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _iso_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_schedule_local(dt: datetime) -> datetime:
    return _ensure_aware_utc(dt).astimezone(CHILECOMPRA_SCHEDULE_TZ)


def _local_slot(day: date, hour: int) -> datetime:
    """Build a Santiago-local canonical slot (DST-safe via ZoneInfo)."""
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        CHILECOMPRA_SCHEDULE_MINUTE,
        tzinfo=CHILECOMPRA_SCHEDULE_TZ,
    )


def chilecompra_scheduled_slots_for_local_day(day: date) -> list[datetime]:
    return [_local_slot(day, hour) for hour in CHILECOMPRA_SCHEDULE_HOURS]


def next_chilecompra_scheduled_slot(at: datetime) -> datetime:
    """Return the first canonical slot at or after ``at``, as UTC."""
    local = _to_schedule_local(at)
    day = local.date()
    for _ in range(0, 4):  # today .. +3 days covers weekend edge cases
        for slot_local in chilecompra_scheduled_slots_for_local_day(day):
            if slot_local >= local:
                return slot_local.astimezone(timezone.utc)
        day = day + timedelta(days=1)
    raise RuntimeError("unable to resolve next ChileCompra scheduled slot")


def previous_chilecompra_scheduled_slot_at_or_before(at: datetime) -> datetime | None:
    """Return the latest canonical slot at or before ``at``, as UTC (or None)."""
    local = _to_schedule_local(at)
    day = local.date()
    for _ in range(0, 4):
        slots = chilecompra_scheduled_slots_for_local_day(day)
        for slot_local in reversed(slots):
            if slot_local <= local:
                return slot_local.astimezone(timezone.utc)
        day = day - timedelta(days=1)
    return None


def resolve_chilecompra_cadence_anchor(
    started_at: datetime,
) -> tuple[datetime, str, datetime | None]:
    """Snap a successful start to a canonical slot when within cron startup tolerance.

    Returns ``(cadence_anchor_utc, cadence_kind, scheduled_slot_utc_or_none)``.
    """
    started_utc = _ensure_aware_utc(started_at)
    # Candidate: previous/equal slot if we started slightly after :12.
    prior = previous_chilecompra_scheduled_slot_at_or_before(started_utc)
    if prior is not None:
        lag = (started_utc - prior).total_seconds()
        if 0 <= lag <= CHILECOMPRA_SLOT_START_TOLERANCE_SECONDS:
            return prior, CADENCE_KIND_SCHEDULED_SLOT, prior
    # Candidate: next slot if we started slightly early (clock skew).
    upcoming = next_chilecompra_scheduled_slot(started_utc)
    lead = (upcoming - started_utc).total_seconds()
    if 0 < lead <= CHILECOMPRA_SLOT_PREEMPT_TOLERANCE_SECONDS:
        return upcoming, CADENCE_KIND_SCHEDULED_SLOT, upcoming
    return started_utc, CADENCE_KIND_WALL_CLOCK, None


def compute_chilecompra_next_recommended_run_at(
    *,
    cadence_anchor: datetime,
    finished_at: datetime,
    cooldown_seconds: int,
) -> datetime:
    """First canonical slot at or after max(anchor+cooldown, finish)."""
    anchor = _ensure_aware_utc(cadence_anchor)
    finished = _ensure_aware_utc(finished_at)
    earliest = max(anchor + timedelta(seconds=int(cooldown_seconds)), finished)
    return next_chilecompra_scheduled_slot(earliest)


def apply_successful_chilecompra_cadence(
    state: ChilecompraEquipmentAutoRefreshState,
    *,
    started_at: datetime,
    finished_at: datetime,
    cooldown_seconds: int,
) -> None:
    """Update success cadence fields after a successful refresh (mutates ``state``)."""
    anchor, kind, slot = resolve_chilecompra_cadence_anchor(started_at)
    state.schema_version = STATE_SCHEMA_VERSION
    state.last_successful_refresh_at = _iso_now(finished_at)
    state.last_successful_refresh_started_at = _iso_now(started_at)
    state.last_successful_scheduled_slot_at = _iso_now(slot) if slot is not None else None
    state.cadence_anchor_kind = kind
    state.next_recommended_run_at = _iso_now(
        compute_chilecompra_next_recommended_run_at(
            cadence_anchor=anchor,
            finished_at=finished_at,
            cooldown_seconds=cooldown_seconds,
        )
    )


def _seconds_since(ts: str | None, now: datetime) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds()


def _lock_is_live(lock_file: Path) -> bool:
    from origenlab_email_pipeline.operator_cli.mail_auto_refresh import _process_alive

    existing = read_lock(lock_file)
    if not existing:
        return False
    pid = int(existing.get("pid") or -1)
    return _process_alive(pid)


def _resolved_detail_cache_dir(
    options: ChilecompraEquipmentAutoRefreshOptions,
    reports_dir: Path | None,
) -> Path:
    if options.detail_cache_dir is not None:
        return options.detail_cache_dir
    base = active_current_dir(reports_dir).parent.parent
    return default_chilecompra_detail_cache_dir(base)


def evaluate_chilecompra_equipment_auto_refresh(
    *,
    options: ChilecompraEquipmentAutoRefreshOptions,
    state: ChilecompraEquipmentAutoRefreshState,
    now: datetime,
) -> ChilecompraEquipmentAutoRefreshResult:
    base = {
        "apply": options.apply,
        "ran_refresh": False,
        "published": False,
        "cooldown_seconds": options.cooldown_seconds,
        "force": options.force,
    }
    if not options.apply:
        return ChilecompraEquipmentAutoRefreshResult(
            reason="dry_run",
            should_run=True,
            extra={},
            **base,
        )

    if not options.force:
        now_utc = _ensure_aware_utc(now)
        next_due = _parse_iso(state.next_recommended_run_at)
        if next_due is not None:
            # Exact due timestamp is eligible (now >= next_due → ready).
            if now_utc < next_due.astimezone(timezone.utc):
                remaining = max(0, int((next_due - now_utc).total_seconds()))
                return ChilecompraEquipmentAutoRefreshResult(
                    reason="cooldown",
                    should_run=False,
                    extra={"cooldown_remaining_seconds": str(remaining)},
                    **base,
                )
        else:
            # Legacy fallback for older state files that only store completion time.
            elapsed = _seconds_since(state.last_successful_refresh_at, now_utc)
            if elapsed is not None and elapsed < options.cooldown_seconds:
                remaining = int(options.cooldown_seconds - elapsed)
                return ChilecompraEquipmentAutoRefreshResult(
                    reason="cooldown",
                    should_run=False,
                    extra={"cooldown_remaining_seconds": str(remaining)},
                    **base,
                )

    return ChilecompraEquipmentAutoRefreshResult(
        reason="ready",
        should_run=True,
        extra={},
        **base,
    )


def _update_state_counts_from_manifest(
    state: ChilecompraEquipmentAutoRefreshState,
    manifest: dict[str, Any],
    *,
    api_queue_path: str,
    candidate_audit_path: str | None,
) -> None:
    state.fetched_summaries = int(manifest.get("fetched_summaries") or 0)
    state.candidate_summaries = int(manifest.get("candidate_summaries") or 0)
    state.prefilter_skipped_summaries = int(manifest.get("prefilter_skipped_summaries") or 0)
    state.detail_requests = int(manifest.get("detail_requests") or 0)
    state.detail_cache_hits = int(manifest.get("detail_cache_hits") or 0)
    state.detail_error_count = int(manifest.get("detail_error_count") or 0)
    state.output_rows = int(manifest.get("output_rows") or 0)
    state.published_queue = api_queue_path
    state.candidate_audit = candidate_audit_path


def _read_model_result(summary: dict[str, Any] | None, *, skipped_reason: str = "") -> str:
    if summary is None:
        return skipped_reason or "skipped"
    if summary.get("applied"):
        return "applied"
    if summary.get("error"):
        return str(summary["error"])
    if summary.get("warning"):
        return str(summary["warning"])
    return "not_applied"


def _update_state_from_read_model_summary(
    state: ChilecompraEquipmentAutoRefreshState,
    summary: dict[str, Any] | None,
    *,
    result: str,
) -> None:
    state.read_model_result = result
    if summary is None:
        return
    row_count = summary.get("rows_inserted")
    if row_count is None:
        row_count = summary.get("row_count")
    if row_count is not None:
        state.read_model_rows = int(row_count)
    if summary.get("source_kind") is not None:
        state.read_model_source_kind = str(summary["source_kind"])
    if summary.get("source_id") is not None:
        state.read_model_source_id = int(summary["source_id"])


def run_chilecompra_equipment_auto_refresh(
    options: ChilecompraEquipmentAutoRefreshOptions,
    *,
    reports_dir: Path | None = None,
    build_fn: BuildQueueFn | None = None,
    publish_fn: PublishQueueFn | None = None,
    read_model_publish_fn: ReadModelPublishFn | None = None,
    now_fn: NowFn | None = None,
) -> int:
    if not options.once:
        raise ValueError("auto-refresh-chilecompra-equipment requires --once.")

    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    reports_base = active_current_dir(reports_dir).parent.parent
    refresh_lock = lock_path(reports_dir)

    if _lock_is_live(refresh_lock):
        result = ChilecompraEquipmentAutoRefreshResult(
            apply=options.apply,
            reason="lock_live",
            should_run=False,
            ran_refresh=False,
            published=False,
            cooldown_seconds=options.cooldown_seconds,
            force=options.force,
        )
        for line in result.output_lines():
            print(line)
        return 0

    acquired, _ = acquire_lock(refresh_lock, now=now, stale_seconds=STALE_LOCK_SECONDS)
    if not acquired:
        result = ChilecompraEquipmentAutoRefreshResult(
            apply=options.apply,
            reason="lock_live",
            should_run=False,
            ran_refresh=False,
            published=False,
            cooldown_seconds=options.cooldown_seconds,
            force=options.force,
        )
        for line in result.output_lines():
            print(line)
        return 0

    state_file = state_path(reports_dir)
    state = load_state(state_file)
    state.last_run_started_at = _iso_now(now)

    try:
        result = evaluate_chilecompra_equipment_auto_refresh(
            options=options,
            state=state,
            now=now,
        )

        if result.should_run and options.apply:
            try:
                ticket = ticket_from_env()
            except ChileCompraTicketMissingError as exc:
                finished = (now_fn or (lambda: datetime.now(timezone.utc)))()
                state.last_run_finished_at = _iso_now(finished)
                state.last_result = "ticket_missing"
                state.last_error = str(exc)
                state.consecutive_failures += 1
                save_state(state_file, state)
                result = ChilecompraEquipmentAutoRefreshResult(
                    apply=options.apply,
                    reason="ticket_missing",
                    should_run=False,
                    ran_refresh=False,
                    published=False,
                    cooldown_seconds=options.cooldown_seconds,
                    force=options.force,
                    extra={"last_error": state.last_error or ""},
                )
                for line in result.output_lines():
                    print(line)
                return 2

            detail_cache_dir = _resolved_detail_cache_dir(options, reports_dir)
            builder = build_fn or build_equipment_queue_from_chilecompra_api
            try:
                rows, manifest, audit_rows = builder(
                    ticket=ticket,
                    estado=options.estado,
                    fecha=options.fecha,
                    max_details=options.max_details,
                    detail_sleep_seconds=options.detail_sleep_seconds,
                    detail_cache_dir=detail_cache_dir,
                    now=now,
                )
            except Exception as exc:
                finished = (now_fn or (lambda: datetime.now(timezone.utc)))()
                state.last_run_finished_at = _iso_now(finished)
                state.last_result = "build_failed"
                state.last_error = redact_ticket(str(exc), ticket)
                state.consecutive_failures += 1
                save_state(state_file, state)
                result = ChilecompraEquipmentAutoRefreshResult(
                    apply=options.apply,
                    reason="build_failed",
                    should_run=False,
                    ran_refresh=False,
                    published=False,
                    cooldown_seconds=options.cooldown_seconds,
                    force=options.force,
                    extra={"last_error": state.last_error or ""},
                )
                for line in result.output_lines():
                    print(line)
                return 1

            api_queue_csv = default_chilecompra_api_queue_csv_path(reports_base, now=now)
            build_stats = write_chilecompra_api_queue_outputs(
                rows=rows,
                manifest=manifest,
                out_csv=api_queue_csv,
            )
            candidate_audit_path: str | None = None
            if options.write_candidate_audit:
                audit_path = default_chilecompra_candidate_audit_path(reports_base, now=now)
                write_candidate_audit_csv(audit_rows, audit_path)
                candidate_audit_path = str(audit_path)

            published_rows = 0
            published_queue = ""
            publish_stats: dict[str, Any] = {}
            read_model_summary: dict[str, Any] | None = None
            read_model_result = "skipped"
            if options.publish:
                canonical_csv = default_canonical_operator_queue_path(reports_base, now=now)
                publisher = publish_fn or publish_chilecompra_equipment_queue_for_dashboard
                publish_stats = publisher(
                    source_csv=api_queue_csv,
                    out_csv=canonical_csv,
                    source_manifest=Path(build_stats["manifest_path"]),
                    update_manifest=True,
                    active_current=active_current_dir(reports_dir),
                )
                published_rows = int(publish_stats.get("output_rows") or 0)
                published_queue = str(publish_stats.get("out_csv") or canonical_csv)
                state.last_successful_publish_at = _iso_now(
                    (now_fn or (lambda: datetime.now(timezone.utc)))()
                )

                if options.publish_read_model:
                    if read_model_publish_fn is not None or postgres_url_configured():
                        try:
                            pg_url = (
                                "postgresql://mock/direct-read-model"
                                if read_model_publish_fn is not None
                                else resolve_postgres_url(None)
                            )
                            read_model_publisher = (
                                read_model_publish_fn or apply_chilecompra_equipment_read_model
                            )
                            read_model_summary = read_model_publisher(
                                pg_url,
                                rows,
                                source_manifest=Path(build_stats["manifest_path"]),
                                export_csv_path=canonical_csv,
                                now=now,
                                updated_by="auto_chilecompra_equipment",
                                reason="auto_chilecompra_equipment_refresh",
                                replace_source=True,
                            )
                            read_model_result = _read_model_result(read_model_summary)
                            if read_model_summary.get("error"):
                                raise RuntimeError(str(read_model_summary["error"]))
                            record_chilecompra_read_model_sync(
                                active_current_dir(reports_dir), read_model_summary
                            )
                            state.last_successful_read_model_publish_at = _iso_now(
                                (now_fn or (lambda: datetime.now(timezone.utc)))()
                            )
                        except Exception as exc:
                            finished = (now_fn or (lambda: datetime.now(timezone.utc)))()
                            state.last_run_finished_at = _iso_now(finished)
                            state.last_result = "read_model_failed"
                            state.last_error = str(exc)
                            state.consecutive_failures += 1
                            _update_state_counts_from_manifest(
                                state,
                                manifest,
                                api_queue_path=str(api_queue_csv),
                                candidate_audit_path=candidate_audit_path,
                            )
                            state.published_rows = published_rows
                            _update_state_from_read_model_summary(
                                state,
                                read_model_summary,
                                result=read_model_result,
                            )
                            save_state(state_file, state)
                            result = ChilecompraEquipmentAutoRefreshResult(
                                apply=options.apply,
                                reason="read_model_failed",
                                should_run=False,
                                ran_refresh=True,
                                published=True,
                                cooldown_seconds=options.cooldown_seconds,
                                force=options.force,
                                extra={"last_error": state.last_error or ""},
                            )
                            for line in result.output_lines():
                                print(line)
                            return 1
                    else:
                        read_model_result = "postgres_not_configured"
                else:
                    read_model_result = "disabled"

            finished = (now_fn or (lambda: datetime.now(timezone.utc)))()
            state.last_run_finished_at = _iso_now(finished)
            started = _parse_iso(state.last_run_started_at) or now
            apply_successful_chilecompra_cadence(
                state,
                started_at=started,
                finished_at=finished,
                cooldown_seconds=options.cooldown_seconds,
            )
            state.consecutive_failures = 0
            state.last_result = "refreshed"
            state.last_error = None
            state.published_rows = published_rows
            _update_state_counts_from_manifest(
                state,
                manifest,
                api_queue_path=str(api_queue_csv),
                candidate_audit_path=candidate_audit_path,
            )
            if published_queue:
                state.published_queue = published_queue
            _update_state_from_read_model_summary(
                state,
                read_model_summary,
                result=read_model_result,
            )
            save_state(state_file, state)

            result = ChilecompraEquipmentAutoRefreshResult(
                apply=options.apply,
                reason="refreshed",
                should_run=True,
                ran_refresh=True,
                published=bool(options.publish),
                cooldown_seconds=options.cooldown_seconds,
                force=options.force,
                extra={
                    "output_rows": str(state.output_rows or 0),
                    "published_rows": str(published_rows),
                    "read_model_result": read_model_result,
                    "read_model_rows": str(state.read_model_rows or 0),
                    "read_model_source_kind": state.read_model_source_kind or "",
                    "detail_requests": str(state.detail_requests or 0),
                    "detail_cache_hits": str(state.detail_cache_hits or 0),
                    "detail_error_count": str(state.detail_error_count or 0),
                    "fetched_summaries": str(state.fetched_summaries or 0),
                    "candidate_summaries": str(state.candidate_summaries or 0),
                    "prefilter_skipped_summaries": str(state.prefilter_skipped_summaries or 0),
                    "published_queue": published_queue,
                    "api_queue": str(api_queue_csv),
                    "coalesced_duplicate_rows": str(publish_stats.get("coalesced_duplicate_rows", 0)),
                    "unique_codigo_count": str(publish_stats.get("unique_codigo_count", 0)),
                },
            )
        else:
            finished = (now_fn or (lambda: datetime.now(timezone.utc)))()
            state.last_run_finished_at = _iso_now(finished)
            state.last_result = result.reason
            save_state(state_file, state)

        for line in result.output_lines():
            print(line)
        return 0
    finally:
        release_lock(refresh_lock)


def parse_chilecompra_equipment_auto_refresh_args(
    argv: list[str],
) -> ChilecompraEquipmentAutoRefreshOptions:
    parser = argparse.ArgumentParser(prog="auto-refresh-chilecompra-equipment", add_help=True)
    parser.add_argument("--apply", action="store_true", help="Run ChileCompra fetch, write, and publish")
    parser.add_argument("--once", action="store_true", help="Single evaluation (required)")
    parser.add_argument("--estado", default="activas", help="Mercado Público estado filter")
    parser.add_argument("--fecha", default=None, help="Optional fecha filter in ddmmaaaa format")
    parser.add_argument("--max-details", type=int, default=DEFAULT_MAX_DETAILS)
    parser.add_argument("--detail-sleep-seconds", type=float, default=DEFAULT_DETAIL_SLEEP_SECONDS)
    parser.add_argument(
        "--detail-cache-dir",
        type=Path,
        default=None,
        help="Per-codigo detail JSON cache directory",
    )
    parser.add_argument(
        "--write-candidate-audit",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write candidate audit CSV (default: true)",
    )
    parser.add_argument(
        "--publish",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Publish canonical dashboard CSV when --apply (default: true)",
    )
    parser.add_argument(
        "--publish-read-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Publish typed Postgres equipment read model directly from ChileCompra rows (default: true)",
    )
    parser.add_argument("--force", action="store_true", help="Bypass cooldown gate")
    parser.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    ns = parser.parse_args(argv)
    if not ns.once:
        parser.error("--once is required")
    return ChilecompraEquipmentAutoRefreshOptions(
        apply=ns.apply,
        once=ns.once,
        estado=ns.estado,
        fecha=ns.fecha,
        max_details=ns.max_details,
        detail_sleep_seconds=ns.detail_sleep_seconds,
        detail_cache_dir=ns.detail_cache_dir,
        write_candidate_audit=ns.write_candidate_audit,
        publish=ns.publish,
        publish_read_model=ns.publish_read_model,
        force=ns.force,
        cooldown_seconds=ns.cooldown_seconds,
    )


def print_chilecompra_equipment_auto_refresh_help() -> None:
    print(
        "auto-refresh-chilecompra-equipment — operator ChileCompra equipment queue refresh\n\n"
        "  uv run origenlab auto-refresh-chilecompra-equipment --once\n"
        "  uv run origenlab auto-refresh-chilecompra-equipment --once --apply\n\n"
        "Writes API queue CSV, optional candidate audit, canonical dashboard CSV, "
        "and typed Postgres equipment read model when Postgres is configured. "
        "Does not send email or mutate SQLite. See docs/operator/CHILECOMPRA_EQUIPMENT_REFRESH.md.\n"
    )
