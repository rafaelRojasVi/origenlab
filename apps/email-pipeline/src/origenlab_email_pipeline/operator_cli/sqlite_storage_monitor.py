"""Read-only SQLite storage telemetry (observation only — never VACUUM/mutate)."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.config import load_settings

GiB = 1024**3

SCHEMA_VERSION = 1
SNAPSHOT_FILENAME = "sqlite_storage_snapshot.json"
HISTORY_FILENAME = "sqlite_storage_history.json"
MAX_HISTORY_SAMPLES = 35

DISK_FREE_GIB_WARNING = 150.0
DISK_FREE_PERCENT_WARNING = 15.0
GROWTH_7D_GIB_WARNING = 5.0
SNAPSHOT_STALE_SECONDS = 8 * 24 * 3600
# Seven-day growth only uses prior samples separated by 6–8 UTC days inclusive.
GROWTH_7D_TARGET_SECONDS = 7 * 24 * 3600
GROWTH_7D_MIN_SECONDS = 6 * 24 * 3600
GROWTH_7D_MAX_SECONDS = 8 * 24 * 3600

AUTO_VACUUM_NAMES = {
    0: "none",
    1: "full",
    2: "incremental",
}

ClockFn = Callable[[], datetime]
DiskUsageFn = Callable[[Path], os.statvfs_result | Any]
BytesStatFn = Callable[[Path], int | None]


def _iso_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _bytes_or_none(path: Path) -> int | None:
    """Return file size in bytes, or None if missing / races away during stat."""
    try:
        if not path.is_file():
            return None
        return int(path.stat().st_size)
    except (OSError, FileNotFoundError):
        return None


def _coerce_float(value: Any) -> tuple[float | None, bool]:
    """Return (parsed, ok). ``ok=False`` means the value was present but not numeric."""
    if value is None:
        return None, True
    if isinstance(value, bool):
        return None, False
    try:
        return float(value), True
    except (TypeError, ValueError):
        return None, False


def _connect_storage_ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30.0)
    conn.execute("PRAGMA query_only=ON")
    return conn


def auto_vacuum_name(value: int | None) -> str | None:
    if value is None:
        return None
    return AUTO_VACUUM_NAMES.get(int(value), f"unknown({value})")


def _disk_metrics(
    path: Path,
    *,
    disk_usage: DiskUsageFn | None = None,
) -> dict[str, Any]:
    try:
        usage = disk_usage(path) if disk_usage is not None else os.statvfs(path)
        # os.statvfs_result-compatible duck typing
        block_size = int(getattr(usage, "f_frsize", None) or getattr(usage, "f_bsize"))
        total_blocks = int(usage.f_blocks)
        free_blocks = int(usage.f_bavail)
        total = block_size * total_blocks
        free = block_size * free_blocks
        used = max(0, total - free)
        free_percent = round((free / total) * 100.0, 4) if total else None
        return {
            "disk_total_bytes": total,
            "disk_free_bytes": free,
            "disk_used_bytes": used,
            "disk_total_gib": round(total / GiB, 4),
            "disk_free_gib": round(free / GiB, 4),
            "disk_used_gib": round(used / GiB, 4),
            "disk_free_percent": free_percent,
        }
    except OSError:
        return {
            "disk_total_bytes": None,
            "disk_free_bytes": None,
            "disk_used_bytes": None,
            "disk_total_gib": None,
            "disk_free_gib": None,
            "disk_used_gib": None,
            "disk_free_percent": None,
        }


def collect_sqlite_storage_telemetry(
    db_path: Path,
    *,
    now: datetime | None = None,
    disk_usage: DiskUsageFn | None = None,
    bytes_stat: BytesStatFn | None = None,
) -> dict[str, Any]:
    """Collect constant-time SQLite storage metadata (no application-table queries)."""
    now_dt = now or datetime.now(timezone.utc)
    size_fn = bytes_stat or _bytes_or_none
    db = Path(db_path)

    page_size = page_count = freelist_count = None
    auto_vacuum_value = None
    journal_mode = None
    conn = _connect_storage_ro(db)
    try:
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        auto_vacuum_value = int(conn.execute("PRAGMA auto_vacuum").fetchone()[0])
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        conn.close()

    allocated_bytes = int(page_size) * int(page_count)
    freelist_bytes = int(page_size) * int(freelist_count)
    active_page_count = max(0, int(page_count) - int(freelist_count))
    active_bytes = int(page_size) * active_page_count
    freelist_ratio = (
        round((freelist_bytes / allocated_bytes) * 100.0, 4) if allocated_bytes else None
    )

    payload: dict[str, Any] = {
        "captured_at_utc": _iso_now(now_dt),
        "schema_version": SCHEMA_VERSION,
        "database_basename": db.name,
        "database_size_bytes": size_fn(db),
        "wal_size_bytes": size_fn(Path(str(db) + "-wal")),
        "shm_size_bytes": size_fn(Path(str(db) + "-shm")),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": allocated_bytes,
        "allocated_gib": round(allocated_bytes / GiB, 4),
        "freelist_bytes": freelist_bytes,
        "freelist_gib": round(freelist_bytes / GiB, 4),
        "active_page_count": active_page_count,
        "active_bytes": active_bytes,
        "active_gib": round(active_bytes / GiB, 4),
        "freelist_ratio_percent": freelist_ratio,
        "auto_vacuum": auto_vacuum_value,
        "auto_vacuum_name": auto_vacuum_name(auto_vacuum_value),
        "journal_mode": journal_mode,
        "read_only": True,
        "mutation": False,
    }
    payload.update(_disk_metrics(db, disk_usage=disk_usage))
    return payload


def atomic_write_json(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    """Write JSON via temp file + os.replace; set restrictive permissions when supported."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    try:
        fd = os.open(tmp, flags, mode)
    except OSError:
        fd = os.open(tmp, flags, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _utc_date(ts: str | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return None


def merge_storage_history(
    history: list[dict[str, Any]],
    sample: dict[str, Any],
    *,
    max_samples: int = MAX_HISTORY_SAMPLES,
) -> list[dict[str, Any]]:
    """Upsert by UTC date; keep chronological order; cap at max_samples."""
    sample_date = _utc_date(sample.get("captured_at_utc"))
    merged: list[dict[str, Any]] = []
    replaced = False
    for item in history:
        if not isinstance(item, dict):
            continue
        item_date = _utc_date(item.get("captured_at_utc"))
        if sample_date and item_date == sample_date:
            merged.append(sample)
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(sample)

    def sort_key(item: dict[str, Any]) -> str:
        return str(item.get("captured_at_utc") or "")

    merged.sort(key=sort_key)
    if len(merged) > max_samples:
        merged = merged[-max_samples:]
    return merged


def load_storage_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("samples"), list):
        return [s for s in data["samples"] if isinstance(s, dict)]
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)]
    return []


def write_storage_evidence(
    sample: dict[str, Any],
    *,
    active_current: Path,
) -> tuple[Path, Path]:
    """Atomically write snapshot + capped history under active/current."""
    active_current = Path(active_current)
    snapshot_path = active_current / SNAPSHOT_FILENAME
    history_path = active_current / HISTORY_FILENAME
    existing = load_storage_history(history_path)
    merged = merge_storage_history(existing, sample)
    history_payload = {
        "schema_version": SCHEMA_VERSION,
        "sample_count": len(merged),
        "samples": merged,
        "read_only_source": True,
        "mutation": False,
    }
    atomic_write_json(snapshot_path, sample)
    atomic_write_json(history_path, history_payload)
    return snapshot_path, history_path


def collect_and_store_sqlite_storage_evidence(
    *,
    db_path: Path | None = None,
    reports_dir: Path | None = None,
    now: datetime | None = None,
    disk_usage: DiskUsageFn | None = None,
) -> dict[str, Any] | None:
    """Collect telemetry and persist aggregate evidence. Returns sample or None on failure."""
    try:
        settings = load_settings()
        db = Path(db_path) if db_path is not None else settings.resolved_sqlite_path()
        reports = Path(reports_dir) if reports_dir is not None else settings.resolved_reports_dir()
        active_current = reports / "active" / "current"
        sample = collect_sqlite_storage_telemetry(db, now=now, disk_usage=disk_usage)
        write_storage_evidence(sample, active_current=active_current)
        return sample
    except Exception:
        return None


def growth_7d_gib(history: list[dict[str, Any]], *, now: datetime | None = None) -> float | None:
    """Compare newest allocated_gib to the closest prior sample in the 6–8 day window.

    Only samples whose separation from the newest sample is within
    ``[GROWTH_7D_MIN_SECONDS, GROWTH_7D_MAX_SECONDS]`` are eligible. Among those,
    pick the one closest to exactly seven days earlier. Return ``None`` when no
    eligible sample exists (e.g. only a 3-day or 20-day prior point).
    """
    if len(history) < 2:
        return None
    _ = now or datetime.now(timezone.utc)

    def parse(item: dict[str, Any]) -> datetime | None:
        ts = item.get("captured_at_utc")
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None

    dated: list[tuple[datetime, float]] = []
    for item in history:
        ts = parse(item)
        gib = item.get("allocated_gib")
        if ts is None or gib is None:
            continue
        coerced, ok = _coerce_float(gib)
        if not ok or coerced is None:
            continue
        dated.append((ts, coerced))
    if len(dated) < 2:
        return None
    dated.sort(key=lambda x: x[0])
    newest_ts, newest_gib = dated[-1]
    target_ts = newest_ts.timestamp() - GROWTH_7D_TARGET_SECONDS

    best: tuple[datetime, float] | None = None
    best_delta: float | None = None
    for ts, gib in dated[:-1]:
        separation = (newest_ts - ts).total_seconds()
        if separation < GROWTH_7D_MIN_SECONDS or separation > GROWTH_7D_MAX_SECONDS:
            continue
        delta = abs(ts.timestamp() - target_ts)
        if best is None or best_delta is None or delta < best_delta:
            best = (ts, gib)
            best_delta = delta
    if best is None:
        return None
    return round(newest_gib - best[1], 4)


def build_sqlite_storage_status(
    *,
    reports_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read snapshot/history aggregates for operator automation status."""
    now_dt = now or datetime.now(timezone.utc)
    settings = load_settings()
    reports = Path(reports_dir) if reports_dir is not None else settings.resolved_reports_dir()
    active_current = reports / "active" / "current"
    snapshot_path = active_current / SNAPSHOT_FILENAME
    history_path = active_current / HISTORY_FILENAME

    thresholds = {
        "disk_free_gib_warning": DISK_FREE_GIB_WARNING,
        "disk_free_percent_warning": DISK_FREE_PERCENT_WARNING,
        "growth_7d_gib_warning": GROWTH_7D_GIB_WARNING,
        "snapshot_stale_seconds": SNAPSHOT_STALE_SECONDS,
    }

    base: dict[str, Any] = {
        "state_exists": snapshot_path.is_file(),
        "parse_error": None,
        "captured_at_utc": None,
        "age_seconds": None,
        "snapshot_stale": None,
        "history_sample_count": 0,
        "allocated_gib": None,
        "freelist_gib": None,
        "active_gib": None,
        "freelist_ratio_percent": None,
        "disk_free_gib": None,
        "disk_free_percent": None,
        "auto_vacuum_name": None,
        "journal_mode": None,
        "growth_7d_gib": None,
        "thresholds": thresholds,
        "status": "unknown",
        "reasons": [],
        "read_only": True,
        "mutation": False,
    }

    history = load_storage_history(history_path)
    base["history_sample_count"] = len(history)

    if not snapshot_path.is_file():
        base["reasons"] = ["snapshot_missing"]
        return base

    try:
        snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        base["parse_error"] = "malformed"
        base["reasons"] = ["snapshot_malformed"]
        return base
    if not isinstance(snap, dict):
        base["parse_error"] = "malformed"
        base["reasons"] = ["snapshot_malformed"]
        return base

    captured = snap.get("captured_at_utc")
    base["captured_at_utc"] = captured
    age = None
    if captured:
        try:
            parsed = datetime.fromisoformat(str(captured).replace("Z", "+00:00"))
            age = max(0, int((now_dt - parsed).total_seconds()))
        except ValueError:
            base["parse_error"] = "malformed_timestamp"
    base["age_seconds"] = age
    stale = bool(age is not None and age > SNAPSHOT_STALE_SECONDS)
    base["snapshot_stale"] = stale

    for key in (
        "allocated_gib",
        "freelist_gib",
        "active_gib",
        "freelist_ratio_percent",
        "disk_free_gib",
        "disk_free_percent",
        "auto_vacuum_name",
        "journal_mode",
        "database_basename",
        "page_size",
        "page_count",
        "freelist_count",
        "allocated_bytes",
        "freelist_bytes",
        "active_bytes",
        "database_size_bytes",
        "wal_size_bytes",
        "shm_size_bytes",
        "auto_vacuum",
        "disk_total_gib",
        "disk_used_gib",
    ):
        if key in snap:
            base[key] = snap.get(key)

    numeric_keys = (
        "allocated_gib",
        "freelist_gib",
        "active_gib",
        "freelist_ratio_percent",
        "disk_free_gib",
        "disk_free_percent",
        "allocated_bytes",
        "freelist_bytes",
        "active_bytes",
        "disk_total_gib",
        "disk_used_gib",
    )
    coerced: dict[str, float | None] = {}
    for key in numeric_keys:
        if key not in snap:
            continue
        value, ok = _coerce_float(snap.get(key))
        if not ok:
            base["parse_error"] = "malformed_numeric"
            base["status"] = "unknown"
            base["reasons"] = ["snapshot_malformed_numeric"]
            base["growth_7d_gib"] = None
            return base
        coerced[key] = value
        base[key] = value

    growth = growth_7d_gib(history, now=now_dt)
    base["growth_7d_gib"] = growth

    reasons: list[str] = []
    attention = False

    disk_free_gib = coerced.get("disk_free_gib", base.get("disk_free_gib"))
    disk_free_percent = coerced.get("disk_free_percent", base.get("disk_free_percent"))
    if disk_free_gib is not None and disk_free_gib < DISK_FREE_GIB_WARNING:
        reasons.append("disk_free_below_150_gib")
        attention = True
    if disk_free_percent is not None and disk_free_percent < DISK_FREE_PERCENT_WARNING:
        reasons.append("disk_free_below_15_percent")
        attention = True
    if growth is not None and growth > GROWTH_7D_GIB_WARNING:
        reasons.append("db_growth_7d_above_5_gib")
        attention = True
    if stale:
        reasons.append("snapshot_stale_informational")
    # High freelist is informational only — never drives attention alone.
    freelist_ratio = coerced.get("freelist_ratio_percent", base.get("freelist_ratio_percent"))
    if freelist_ratio is not None and freelist_ratio >= 40.0:
        reasons.append("high_freelist_ratio_informational")

    if attention:
        base["status"] = "attention"
        if "disk_free_below_150_gib" in reasons or "disk_free_below_15_percent" in reasons:
            reasons.append("inspect_disk_capacity_no_vacuum")
        if "db_growth_7d_above_5_gib" in reasons:
            reasons.append("inspect_storage_growth_trends_no_vacuum")
    else:
        base["status"] = "healthy"

    base["reasons"] = reasons
    return base
