"""Safe SQLite Online Backup API helper (never plain cp/rsync of a live WAL DB)."""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

GiB = 1024**3
BACKUP_SCHEMA_VERSION = 1
DEFAULT_PAGES_PER_BATCH = 100
DEFAULT_CAPACITY_MARGIN_RATIO = 0.05
DEFAULT_CAPACITY_MARGIN_MIN_BYTES = 256 * 1024 * 1024  # 256 MiB
DEFAULT_BUSY_TIMEOUT_MS = 30_000
DEFAULT_PROGRESS_INTERVAL_SECONDS = 2.0
LOCK_DIR_NAME = ".origenlab_sqlite_online_backup_locks"

PRIVACY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"(?:^|[\s\"'])(?:/home|/mnt|/var|/tmp|/Users|/opt)[^\s\"']+",
        r"(?:^|[\s\"'])[A-Za-z]:\\[^\s\"']+",
    )
)


class BackupError(RuntimeError):
    """Operator-facing backup failure."""


@dataclass
class FileFingerprint:
    size_bytes: int
    mtime_ns: int
    device: int
    inode: int

    def to_dict(self) -> dict[str, int]:
        return {
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "device": self.device,
            "inode": self.inode,
        }


@dataclass
class BackupOptions:
    source: Path
    destination: Path
    pages_per_batch: int = DEFAULT_PAGES_PER_BATCH
    allow_same_filesystem: bool = False
    capacity_margin_ratio: float = DEFAULT_CAPACITY_MARGIN_RATIO
    capacity_margin_min_bytes: int = DEFAULT_CAPACITY_MARGIN_MIN_BYTES
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
    progress_interval_seconds: float = DEFAULT_PROGRESS_INTERVAL_SECONDS
    lock_dir: Path | None = None
    sleep: Callable[[float], None] = field(default=time.sleep)
    clock: Callable[[], float] = field(default=time.perf_counter)
    progress_sink: Callable[[str], None] | None = None
    should_abort: Callable[[], bool] | None = None
    fail_if_source_fingerprint_changes: bool = False


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_path_for_log(path: Path | str) -> str:
    """Log basename only; never absolute local paths."""
    return Path(path).name


def scan_manifest_privacy(payload: Any) -> list[str]:
    violations: list[str] = []

    def walk(obj: Any, p: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                walk(value, f"{p}.{key}")
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                walk(item, f"{p}[{idx}]")
        elif isinstance(obj, str):
            for pattern in PRIVACY_PATTERNS:
                if pattern.search(obj):
                    violations.append(f"{p}: matched privacy pattern")
                    break

    walk(payload, "$")
    return violations


def fingerprint_file(path: Path) -> FileFingerprint:
    st = path.stat()
    return FileFingerprint(
        size_bytes=int(st.st_size),
        mtime_ns=int(st.st_mtime_ns),
        device=int(st.st_dev),
        inode=int(st.st_ino),
    )


def paths_same_file(a: Path, b: Path) -> bool:
    try:
        if a.resolve() == b.resolve():
            return True
    except OSError:
        pass
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        pass
    if not a.exists() or not b.exists():
        return False
    fa = fingerprint_file(a)
    fb = fingerprint_file(b)
    return fa.device == fb.device and fa.inode == fb.inode


def same_filesystem(a: Path, b: Path) -> bool:
    """Compare device IDs of existing paths (or parents for not-yet-created dest)."""
    a_path = a if a.exists() else a.parent
    b_path = b if b.exists() else b.parent
    if not a_path.exists() or not b_path.exists():
        raise BackupError(
            f"cannot determine filesystem for {sanitize_path_for_log(a)} / "
            f"{sanitize_path_for_log(b)}"
        )
    return os.stat(a_path).st_dev == os.stat(b_path).st_dev


def disk_free_bytes(path: Path) -> int:
    target = path if path.exists() else path.parent
    usage = os.statvfs(target)
    return int(usage.f_bavail * usage.f_frsize)


def required_capacity_bytes(
    source_size: int,
    *,
    margin_ratio: float = DEFAULT_CAPACITY_MARGIN_RATIO,
    margin_min_bytes: int = DEFAULT_CAPACITY_MARGIN_MIN_BYTES,
) -> int:
    margin = max(int(source_size * margin_ratio), int(margin_min_bytes))
    return int(source_size) + margin


def assert_sqlite_header(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 16:
        raise BackupError(f"source is not a readable SQLite file: {sanitize_path_for_log(path)}")
    with path.open("rb") as handle:
        magic = handle.read(16)
    if not magic.startswith(b"SQLite format 3\x00"):
        raise BackupError(
            f"source header is not SQLite format 3: {sanitize_path_for_log(path)}"
        )


def connect_source_readonly(path: Path, *, busy_timeout_ms: int) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=max(1.0, busy_timeout_ms / 1000.0))
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    conn.execute("PRAGMA query_only=ON")
    return conn


def connect_destination(path: Path, *, busy_timeout_ms: int) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=max(1.0, busy_timeout_ms / 1000.0))
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


def read_cheap_sqlite_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    tables = [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    ]
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": page_size * page_count,
        "freelist_bytes": page_size * freelist_count,
        "schema_version": schema_version,
        "user_version": user_version,
        "journal_mode": journal_mode,
        "table_count": len(tables),
        "tables": tables,
    }


def verify_destination_cheap(path: Path) -> dict[str, Any]:
    assert_sqlite_header(path)
    conn = connect_source_readonly(path, busy_timeout_ms=5_000)
    try:
        conn.execute("PRAGMA query_only=ON")
        meta = read_cheap_sqlite_meta(conn)
    finally:
        conn.close()
    return meta


def partial_path_for(destination: Path) -> Path:
    return destination.with_name(destination.name + ".partial")


def manifest_path_for(destination: Path) -> Path:
    return destination.with_name(destination.name + ".manifest.json")


def lock_path_for(source: Path, lock_dir: Path | None) -> Path:
    base = lock_dir or (Path.home() / ".cache" / "origenlab" / LOCK_DIR_NAME)
    base.mkdir(parents=True, exist_ok=True)
    # Stable lock key from device+inode (or basename fallback before open).
    try:
        fp = fingerprint_file(source)
        key = f"dev{fp.device}_ino{fp.inode}.lock"
    except OSError:
        key = f"name_{source.name}.lock"
    return base / key


class BackupLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError(
                "another sqlite online backup appears to be running "
                f"(lock={sanitize_path_for_log(self.path)})"
            ) from exc
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"pid={os.getpid()} started_at={_iso_now()}\n")
        self._fh.flush()

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


def validate_backup_options(options: BackupOptions) -> None:
    source = options.source
    destination = options.destination
    if options.pages_per_batch <= 0:
        raise BackupError("--pages-per-batch must be a positive integer (pages=0 is forbidden)")
    if not source.is_file():
        raise BackupError(f"source database not found: {sanitize_path_for_log(source)}")
    assert_sqlite_header(source)
    try:
        if source.resolve() == destination.resolve():
            raise BackupError("source and destination resolve to the same path (alias)")
    except OSError:
        pass
    if destination.exists() and paths_same_file(source, destination):
        raise BackupError("source and destination resolve to the same file (alias/samefile)")
    if destination.exists():
        raise BackupError(
            f"destination already exists (refusing overwrite): {sanitize_path_for_log(destination)}"
        )
    partial = partial_path_for(destination)
    if partial.exists():
        raise BackupError(
            f"partial destination already exists (refusing resume of foreign file): "
            f"{sanitize_path_for_log(partial)}"
        )
    if not destination.parent.is_dir():
        raise BackupError(
            f"destination parent directory missing: {sanitize_path_for_log(destination.parent)}"
        )
    if paths_same_file(source, partial) if partial.exists() else False:
        raise BackupError("source and destination partial resolve to the same file (alias/samefile)")
    if not options.allow_same_filesystem and same_filesystem(source, destination):
        raise BackupError(
            "destination is on the same filesystem as source; refuse by default. "
            "Use --allow-same-filesystem only for synthetic tests/emergencies."
        )
    source_size = source.stat().st_size
    needed = required_capacity_bytes(
        source_size,
        margin_ratio=options.capacity_margin_ratio,
        margin_min_bytes=options.capacity_margin_min_bytes,
    )
    free = disk_free_bytes(destination.parent)
    if free < needed:
        raise BackupError(
            "insufficient destination capacity: "
            f"free={free} needed>={needed} "
            f"(source={source_size} + margin)"
        )


def _emit_progress(
    options: BackupOptions,
    *,
    remaining: int,
    pagecount: int,
    started: float,
    page_size: int,
) -> None:
    done = max(0, pagecount - remaining)
    pct = (100.0 * done / pagecount) if pagecount else 100.0
    bytes_est = done * page_size
    elapsed = options.clock() - started
    line = (
        f"backup progress: pages={done}/{pagecount} ({pct:.1f}%) "
        f"bytes_est={bytes_est} elapsed_s={elapsed:.1f}"
    )
    sink = options.progress_sink or (lambda s: print(s, file=sys.stderr))
    sink(line)


def run_online_backup(options: BackupOptions) -> dict[str, Any]:
    """Execute Online Backup API copy; never publishes a partial destination."""
    validate_backup_options(options)
    source = options.source.resolve()
    destination = options.destination
    partial = partial_path_for(destination)
    lock = BackupLock(lock_path_for(source, options.lock_dir))

    source_fp_before = fingerprint_file(source)
    started_wall = _iso_now()
    t0 = options.clock()
    interrupted = False
    src_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    source_meta: dict[str, Any] | None = None

    def _on_sigint(signum: int, frame: Any) -> None:  # noqa: ARG001
        nonlocal interrupted
        interrupted = True

    previous_handler = signal.signal(signal.SIGINT, _on_sigint)
    lock.acquire()
    try:
        src_conn = connect_source_readonly(source, busy_timeout_ms=options.busy_timeout_ms)
        source_meta = read_cheap_sqlite_meta(src_conn)
        page_size = int(source_meta["page_size"])

        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(str(partial), flags, 0o600)
        os.close(fd)

        dest_conn = connect_destination(partial, busy_timeout_ms=options.busy_timeout_ms)

        last_progress = t0
        progress_events = 0

        def progress_callback(status: int, remaining: int, pagecount: int) -> None:  # noqa: ARG001
            nonlocal last_progress, progress_events, interrupted
            if interrupted or (options.should_abort and options.should_abort()):
                raise BackupError("backup interrupted; partial file not published")
            now = options.clock()
            if remaining == 0 or (now - last_progress) >= options.progress_interval_seconds:
                _emit_progress(
                    options,
                    remaining=int(remaining),
                    pagecount=int(pagecount),
                    started=t0,
                    page_size=page_size,
                )
                last_progress = now
                progress_events += 1

        # pages > 0 required: Connection.backup loops batches internally with sleep between them.
        # pages<=0 would copy in one exclusive step — forbidden.
        sleep_s = max(0.001, min(0.25, options.busy_timeout_ms / 1000.0 / 100.0))
        src_conn.backup(
            dest_conn,
            pages=options.pages_per_batch,
            progress=progress_callback,
            sleep=sleep_s,
        )

        dest_conn.commit()
        dest_conn.close()
        dest_conn = None
        src_conn.close()
        src_conn = None

        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        dir_fd = os.open(str(partial.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

        dest_meta = verify_destination_cheap(partial)
        os.rename(partial, destination)
        dir_fd = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

        source_fp_after = fingerprint_file(source)
        source_changed = source_fp_before != source_fp_after
        if options.fail_if_source_fingerprint_changes and source_changed:
            raise BackupError(
                "source fingerprint changed during backup while "
                "fail_if_source_fingerprint_changes=True"
            )

        elapsed = options.clock() - t0
        assert source_meta is not None
        manifest: dict[str, Any] = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "completed": True,
            "method": "sqlite3.Connection.backup",
            "started_at_utc": started_wall,
            "finished_at_utc": _iso_now(),
            "elapsed_seconds": round(elapsed, 3),
            "source_basename": source.name,
            "destination_basename": destination.name,
            "source_size_bytes": source_fp_before.size_bytes,
            "destination_size_bytes": destination.stat().st_size,
            "pages_per_batch": options.pages_per_batch,
            "progress_events": progress_events,
            "allow_same_filesystem": options.allow_same_filesystem,
            "python_version": sys.version.split()[0],
            "sqlite_version": sqlite3.sqlite_version,
            "source_meta": {
                k: source_meta[k]
                for k in (
                    "page_size",
                    "page_count",
                    "freelist_count",
                    "schema_version",
                    "user_version",
                    "journal_mode",
                    "table_count",
                )
            },
            "destination_meta": {
                k: dest_meta[k]
                for k in (
                    "page_size",
                    "page_count",
                    "freelist_count",
                    "schema_version",
                    "user_version",
                    "journal_mode",
                    "table_count",
                )
            },
            "source_fingerprint_before": source_fp_before.to_dict(),
            "source_fingerprint_after": source_fp_after.to_dict(),
            "source_opened_readonly": True,
            "source_mutated_by_utility": False,
            "source_fingerprint_changed_during_backup": source_changed,
            "verification": "header+query_only+cheap_pragmas+schema_inventory",
            "notes": [
                "Online Backup API snapshot; not a plain cp/rsync of a live WAL database.",
                "Cheap verification only; integrity_check/dbstat/deep-audit not run.",
                "Utility opens source with URI mode=ro; concurrent writers may still change source.",
            ],
        }
        privacy = scan_manifest_privacy(manifest)
        if privacy:
            raise BackupError(f"manifest privacy violation: {privacy[:3]}")
        manifest_path = manifest_path_for(destination)
        tmp_manifest = manifest_path.with_name(manifest_path.name + ".partial")
        tmp_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with tmp_manifest.open("rb") as handle:
            os.fsync(handle.fileno())
        os.rename(tmp_manifest, manifest_path)
        return manifest
    except Exception:
        if dest_conn is not None:
            try:
                dest_conn.close()
            except Exception:
                pass
        if src_conn is not None:
            try:
                src_conn.close()
            except Exception:
                pass
        if partial.exists() and not destination.exists():
            try:
                partial.unlink()
            except OSError:
                pass
        raise
    finally:
        signal.signal(signal.SIGINT, previous_handler)
        lock.release()
