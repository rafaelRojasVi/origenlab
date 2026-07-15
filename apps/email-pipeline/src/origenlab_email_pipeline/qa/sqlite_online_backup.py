"""Safe SQLite Online Backup API helper (never plain cp/rsync of a live WAL DB)."""

from __future__ import annotations

import errno
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

BACKUP_SCHEMA_VERSION = 1
DEFAULT_PAGES_PER_BATCH = 4096
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

_ABS_PATH_IN_TEXT = re.compile(
    r"(?:/home|/mnt|/var|/tmp|/Users|/opt)/[^\s\"']+|[A-Za-z]:\\[^\s\"']+"
)
_EMAIL_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


class BackupError(RuntimeError):
    """Operator-facing backup failure (already sanitized)."""


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
    apply: bool = False
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
    # Test hooks
    dir_fsync: Callable[[int], None] | None = None
    manifest_write_hook: Callable[[Path, str], None] | None = None
    post_copy_hook: Callable[[], None] | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize_path_for_log(path: Path | str) -> str:
    """Log basename only; never absolute local paths."""
    return Path(path).name


def sanitize_error_message(exc: BaseException | str) -> str:
    """Strip absolute paths and mailbox-looking text from exception messages."""
    if isinstance(exc, BackupError):
        text = str(exc)
    elif isinstance(exc, BaseException):
        text = f"{type(exc).__name__}: {exc}"
    else:
        text = str(exc)
    text = _ABS_PATH_IN_TEXT.sub("<path>", text)
    text = _EMAIL_IN_TEXT.sub("<email>", text)
    return text


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


def manifest_partial_path_for(destination: Path) -> Path:
    return destination.with_name(destination.name + ".manifest.json.partial")


def partial_companion_paths(partial: Path) -> list[Path]:
    return [
        partial,
        Path(str(partial) + "-journal"),
        Path(str(partial) + "-wal"),
        Path(str(partial) + "-shm"),
    ]


def cleanup_script_owned_artifacts(destination: Path) -> list[str]:
    """Remove only this run's partial artifacts. Never deletes final destination/manifest."""
    removed: list[str] = []
    partial = partial_path_for(destination)
    for path in [*partial_companion_paths(partial), manifest_partial_path_for(destination)]:
        if path.exists():
            try:
                path.unlink()
                removed.append(sanitize_path_for_log(path))
            except OSError:
                pass
    return removed


def backup_is_completed(destination: Path) -> bool:
    """Completed only when final DB and final completed manifest both exist."""
    man = manifest_path_for(destination)
    if not (destination.is_file() and man.is_file()):
        return False
    try:
        payload = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("completed") is True)


def detect_orphan_destination(destination: Path) -> str | None:
    """
    Detect crash window: final DB without completed final manifest.

    Orphan is never treated as completed. Operator must clean up explicitly.
    """
    man = manifest_path_for(destination)
    man_partial = manifest_partial_path_for(destination)
    if destination.exists() and not man.exists():
        return (
            f"orphaned/uncommitted destination detected: {sanitize_path_for_log(destination)} "
            f"exists without completed manifest {sanitize_path_for_log(man)}. "
            "Do not treat as completed. Explicit cleanup required, e.g. move/delete the orphan "
            "DB (and any leftover .manifest.json.partial) after operator review."
        )
    if destination.exists() and man_partial.exists() and not man.exists():
        return (
            f"orphaned destination with unfinished manifest.partial: "
            f"{sanitize_path_for_log(destination)}. Explicit cleanup required."
        )
    if destination.exists() and man.exists():
        try:
            payload = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return (
                f"destination exists with unreadable manifest: "
                f"{sanitize_path_for_log(destination)}. Explicit cleanup required."
            )
        if payload.get("completed") is not True:
            return (
                f"destination exists with incomplete manifest completed!=true: "
                f"{sanitize_path_for_log(destination)}. Explicit cleanup required."
            )
    return None


def lock_path_for(source: Path, lock_dir: Path | None) -> Path:
    base = lock_dir or (Path.home() / ".cache" / "origenlab" / LOCK_DIR_NAME)
    base.mkdir(parents=True, exist_ok=True)
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
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            # Close handle before raising so lock-contention leaves no leaked FD/handler state.
            try:
                self._fh.close()
            finally:
                self._fh = None
            raise BackupError(
                "another sqlite online backup appears to be running "
                f"(lock={sanitize_path_for_log(self.path)})"
            ) from exc
        self.acquired = True
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"pid={os.getpid()} started_at={_iso_now()}\n")
        self._fh.flush()

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if self.acquired:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
            self.acquired = False


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_directory(path: Path, *, dir_fsync: Callable[[int], None] | None = None) -> dict[str, Any]:
    """
    Attempt directory fsync. EINVAL/ENOTSUP (e.g. some 9p mounts) become durability warnings.
    """
    fn = dir_fsync or os.fsync
    dir_fd = os.open(str(path), os.O_RDONLY)
    try:
        fn(dir_fd)
        return {"directory_fsync_supported": True, "directory_fsync_warning": None}
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
            return {
                "directory_fsync_supported": False,
                "directory_fsync_warning": (
                    f"directory fsync unsupported on this filesystem "
                    f"(errno={exc.errno}); file fsync still required and performed"
                ),
            }
        raise BackupError(f"directory fsync failed: errno={exc.errno}") from None
    finally:
        os.close(dir_fd)


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

    orphan = detect_orphan_destination(destination)
    if orphan:
        raise BackupError(orphan)

    if backup_is_completed(destination):
        raise BackupError(
            f"destination already completed (refusing overwrite): "
            f"{sanitize_path_for_log(destination)}"
        )
    if destination.exists():
        raise BackupError(
            f"destination already exists (refusing overwrite): {sanitize_path_for_log(destination)}"
        )

    partial = partial_path_for(destination)
    for companion in partial_companion_paths(partial):
        if companion.exists():
            raise BackupError(
                f"conflicting partial companion already exists: {sanitize_path_for_log(companion)}"
            )
    man_partial = manifest_partial_path_for(destination)
    if man_partial.exists():
        raise BackupError(
            f"conflicting manifest.partial already exists: {sanitize_path_for_log(man_partial)}"
        )
    man = manifest_path_for(destination)
    if man.exists() and not destination.exists():
        raise BackupError(
            f"conflicting final manifest without destination DB: {sanitize_path_for_log(man)}"
        )

    if not destination.parent.is_dir():
        raise BackupError(
            f"destination parent directory missing: {sanitize_path_for_log(destination.parent)}"
        )
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


def build_preflight_report(options: BackupOptions) -> dict[str, Any]:
    """Read-only preflight; performs zero writes (no lock/partial/manifest)."""
    validate_backup_options(options)
    source = options.source
    destination = options.destination
    source_size = source.stat().st_size
    free = disk_free_bytes(destination.parent)
    needed = required_capacity_bytes(
        source_size,
        margin_ratio=options.capacity_margin_ratio,
        margin_min_bytes=options.capacity_margin_min_bytes,
    )
    same_fs = same_filesystem(source, destination)
    source_meta: dict[str, Any] | None = None
    conn = connect_source_readonly(source, busy_timeout_ms=min(5_000, options.busy_timeout_ms))
    try:
        source_meta = read_cheap_sqlite_meta(conn)
    finally:
        conn.close()
    return {
        "mode": "preflight",
        "apply": False,
        "completed": False,
        "writes_performed": False,
        "source_basename": source.name,
        "destination_basename": destination.name,
        "source_size_bytes": source_size,
        "estimated_output_bytes": source_size,
        "destination_free_bytes": free,
        "capacity_required_bytes": needed,
        "filesystem_separated": not same_fs,
        "allow_same_filesystem": options.allow_same_filesystem,
        "pages_per_batch": options.pages_per_batch,
        "busy_timeout_ms": options.busy_timeout_ms,
        "progress_interval_seconds": options.progress_interval_seconds,
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
        "notes": [
            "Preflight only: no lock, partial, backup, or manifest created.",
            "Re-run with --apply to execute the Online Backup API snapshot.",
            "Completion requires both final DB and completed final manifest.",
        ],
    }


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
    """
    Execute Online Backup API copy.

    Without ``apply=True``, runs sanitized preflight only (zero writes).
    A backup is completed only when final DB + completed final manifest both exist.
    """
    if not options.apply:
        return build_preflight_report(options)

    validate_backup_options(options)
    source = options.source.resolve()
    destination = options.destination
    partial = partial_path_for(destination)
    manifest_path = manifest_path_for(destination)
    manifest_partial = manifest_partial_path_for(destination)
    lock = BackupLock(lock_path_for(source, options.lock_dir))

    source_fp_before = fingerprint_file(source)
    started_wall = _iso_now()
    t0 = options.clock()
    interrupted = False
    src_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    source_meta: dict[str, Any] | None = None
    dir_fsync_info: dict[str, Any] = {
        "directory_fsync_supported": True,
        "directory_fsync_warning": None,
    }
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ARG001
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, _on_signal)
    try:
        signal.signal(signal.SIGTERM, _on_signal)
    except (ValueError, OSError):
        # Some environments restrict SIGTERM replacement.
        previous_sigterm = None

    published = False
    try:
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
            final_progress_emitted = False

            def progress_callback(status: int, remaining: int, pagecount: int) -> None:  # noqa: ARG001
                nonlocal last_progress, progress_events, final_progress_emitted, interrupted
                if interrupted or (options.should_abort and options.should_abort()):
                    raise BackupError("backup interrupted; partial file not published")
                now = options.clock()
                remaining_i = int(remaining)
                pagecount_i = int(pagecount)
                due = remaining_i == 0 or (now - last_progress) >= options.progress_interval_seconds
                if due:
                    _emit_progress(
                        options,
                        remaining=remaining_i,
                        pagecount=pagecount_i,
                        started=t0,
                        page_size=page_size,
                    )
                    last_progress = now
                    progress_events += 1
                    if remaining_i == 0:
                        final_progress_emitted = True

            sleep_s = max(0.001, min(0.25, options.busy_timeout_ms / 1000.0 / 100.0))
            src_conn.backup(
                dest_conn,
                pages=options.pages_per_batch,
                progress=progress_callback,
                sleep=sleep_s,
            )
            if options.post_copy_hook is not None:
                options.post_copy_hook()
            if not final_progress_emitted:
                # Guarantee final 100% progress line even if last callback was time-throttled skip.
                _emit_progress(
                    options,
                    remaining=0,
                    pagecount=int(source_meta["page_count"]),
                    started=t0,
                    page_size=page_size,
                )
                progress_events += 1

            dest_conn.commit()
            dest_conn.close()
            dest_conn = None
            src_conn.close()
            src_conn = None

            # Mandatory file fsync of partial DB before verification/publish.
            fsync_file(partial)

            # --- Pre-publication: verify, fingerprint policy, build+scan manifest ---
            dest_meta = verify_destination_cheap(partial)

            source_fp_after = fingerprint_file(source)
            source_changed = source_fp_before != source_fp_after
            if options.fail_if_source_fingerprint_changes and source_changed:
                raise BackupError(
                    "source fingerprint changed during backup while "
                    "fail_if_source_fingerprint_changes=True"
                )

            elapsed = options.clock() - t0
            assert source_meta is not None
            warnings: list[str] = []
            # Directory fsync of parent (may be unsupported on 9p) — recorded, not fatal.
            dir_fsync_info = fsync_directory(
                partial.parent, dir_fsync=options.dir_fsync
            )
            if dir_fsync_info.get("directory_fsync_warning"):
                warnings.append(str(dir_fsync_info["directory_fsync_warning"]))

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
                "destination_size_bytes": partial.stat().st_size,
                "pages_per_batch": options.pages_per_batch,
                "progress_events": progress_events,
                "allow_same_filesystem": options.allow_same_filesystem,
                "python_version": sys.version.split()[0],
                "sqlite_version": sqlite3.sqlite_version,
                "directory_fsync_supported": dir_fsync_info.get("directory_fsync_supported"),
                "warnings": warnings,
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
                "completion_marker": "final_manifest",
                "notes": [
                    "Online Backup API snapshot; not a plain cp/rsync of a live WAL database.",
                    "Completed only when final DB and completed final manifest both exist.",
                    "Crash window: final DB without final manifest is an orphan requiring explicit cleanup.",
                    "Cheap verification only; integrity_check/dbstat/deep-audit not run.",
                    "Utility opens source with URI mode=ro; concurrent writers may still change source.",
                ],
            }
            privacy = scan_manifest_privacy(manifest)
            if privacy:
                raise BackupError(f"manifest privacy violation: {privacy[:3]}")

            # Write+fsync manifest.partial BEFORE entering finalization rename sequence.
            payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            if options.manifest_write_hook is not None:
                options.manifest_write_hook(manifest_partial, payload)
            else:
                manifest_partial.write_text(payload, encoding="utf-8")
            fsync_file(manifest_partial)

            # --- Finalization: DB rename then manifest rename (completion marker) ---
            os.rename(partial, destination)
            published = True  # DB published; crash before manifest rename => orphan
            os.rename(manifest_partial, manifest_path)

            # Best-effort directory fsync after renames; unsupported FS => warning only.
            post_rename = fsync_directory(destination.parent, dir_fsync=options.dir_fsync)
            if post_rename.get("directory_fsync_warning"):
                # Manifest already published; do not rewrite it. Warning already possible earlier.
                pass

            return manifest
        finally:
            lock.release()
    except Exception as exc:
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
        # Never delete a published orphan final DB; refuse overwrite on next run.
        if not published:
            cleanup_script_owned_artifacts(destination)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(sanitize_error_message(exc)) from None
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if previous_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, previous_sigterm)
            except (ValueError, OSError):
                pass
