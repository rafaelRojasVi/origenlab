"""Fail-closed, resumable SQLite production cutover orchestrator.

Staged state machine only. Default is zero-write preflight. Never runs the
entire production workflow from one command.

Real production apply is currently blocked because not every SQLite writer
entry point has a cutover-linked pause barrier (see
``REAL_SQLITE_WRITER_ENTRY_POINTS`` / ``REAL_PRODUCTION_APPLY_BLOCKED``).
SyntheticWorld remains available for exhaustive tests.

The July 2026 compact candidate is evidence-only and is never accepted as a
cutover source. Environment variables are never accepted as proof of git HEAD.
"""

from __future__ import annotations

import enum
import errno
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator, Iterator, Protocol

from origenlab_email_pipeline.config import Settings, canonical_production_sqlite_path, load_settings
from origenlab_email_pipeline.operator_cli.chilecompra_auto_refresh import (
    LOCK_FILENAME as CHILECOMPRA_LOCK_FILENAME,
)
from origenlab_email_pipeline.operator_cli.dashboard_auto_mirror import (
    LOCK_FILENAME as DASHBOARD_LOCK_FILENAME,
    PAUSE_FILENAME as DASHBOARD_PAUSE_FILENAME,
)
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import (
    LOCK_FILENAME as MAIL_LOCK_FILENAME,
    PAUSE_FILENAME as MAIL_PAUSE_FILENAME,
)
from origenlab_email_pipeline.qa.sqlite_offline_compaction import (
    CompactionOptions,
    assert_no_sidecars,
    compaction_is_completed,
    connect_candidate_readonly,
    critical_table_counts,
    database_identity_props,
    manifest_path_for as compaction_manifest_path_for,
    run_offline_compaction,
    schema_fingerprint,
    storage_stats,
    verify_compact_candidate,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    BackupOptions,
    backup_is_completed,
    disk_free_bytes,
    fingerprint_file,
    fsync_directory,
    fsync_file,
    manifest_path_for as backup_manifest_path_for,
    paths_same_file,
    required_capacity_bytes,
    run_online_backup,
    same_filesystem,
    sanitize_path_for_log,
)
from origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal import (
    validate_planned_cutover_topology,
)

CUTOVER_SCHEMA_VERSION = 2
TOOL_NAME = "sqlite_production_cutover"
API_SERVICE = "origenlab-api.service"
API_HEALTH_TIMER = "origenlab-api-health.timer"
FORBIDDEN_COMPACT_PREFIXES = ("emails_compact_", "emails_offline_")
KNOWN_EVIDENCE_COMPACT = "emails_compact_20260717T183537Z.sqlite"
MAINTENANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
JOURNAL_DIR_NAME = ".origenlab_cutover_journals"
LOCK_DIR_NAME = ".origenlab_sqlite_cutover_locks"
# SHM may appear after API open; only these sizes are acceptable.
ALLOWED_SHM_SIZES = frozenset({0, 32768})

EXIT_OK = 0
EXIT_PREFLIGHT = 2
EXIT_APPLY = 3
EXIT_VERIFY = 4
EXIT_SAFETY = 5
EXIT_AMBIGUOUS = 6

_ABS_PATH = re.compile(
    r"(?:/home|/mnt|/var|/tmp|/Users|/opt)/[^\s\"']+|[A-Za-z]:\\[^\s\"']+"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

# Inventory of known production SQLite writers / holders. Keep exact.
REAL_SQLITE_WRITER_ENTRY_POINTS: tuple[dict[str, str], ...] = (
    {
        "name": "mail_auto_refresh",
        "barrier": "auto_refresh_paused",
        "lock": MAIL_LOCK_FILENAME,
        "status": "guarded",
    },
    {
        "name": "dashboard_auto_mirror",
        "barrier": "dashboard_auto_mirror_paused",
        "lock": DASHBOARD_LOCK_FILENAME,
        "status": "guarded",
    },
    {
        "name": "chilecompra_equipment_auto_refresh",
        "barrier": "none",
        "lock": CHILECOMPRA_LOCK_FILENAME,
        "status": "unguarded",
        "reason": "has concurrency lock but no cutover-linked pause marker",
    },
    {
        "name": "origenlab-api.service",
        "barrier": "systemctl stop (reader; must stop before WAL quiesce/swap)",
        "lock": "n/a",
        "status": "guarded_via_stop_readers",
    },
    {
        "name": "ad_hoc_operator_scripts",
        "barrier": "none",
        "lock": "none",
        "status": "unguarded",
        "reason": "manual sqlite3 / scripts may open production RW without markers",
    },
)

UNGUARDED_WRITER_ENTRY_POINTS: tuple[str, ...] = tuple(
    e["name"]
    for e in REAL_SQLITE_WRITER_ENTRY_POINTS
    if e.get("status") == "unguarded"
)

# Real FilesystemAdapters mutating apply remains blocked until every writer
# has a reliable maintenance barrier.
REAL_PRODUCTION_APPLY_BLOCKED = True
REAL_PRODUCTION_APPLY_BLOCK_REASON = (
    "Real production cutover apply is blocked: unguarded SQLite writer entry "
    f"points remain: {', '.join(UNGUARDED_WRITER_ENTRY_POINTS)}. "
    "SyntheticWorld tests exercise the state machine without touching production."
)


class CutoverStage(enum.Enum):
    PLAN_PREFLIGHT = "plan_preflight"
    PAUSE_WRITERS = "pause_writers"
    STOP_READERS = "stop_readers"
    QUIESCE_WAL = "quiesce_wal"
    CREATE_CURRENT_BACKUP = "create_current_backup"
    COMPACT_TO_PRODUCTION_FS_STAGING = "compact_to_production_fs_staging"
    VERIFY_CANDIDATE = "verify_candidate"
    APPROVE_SWAP = "approve_swap"
    ATOMIC_SWAP = "atomic_swap"
    READONLY_SMOKE = "readonly_smoke"
    RESUME_SERVICES = "resume_services"
    RESUME_WRITERS_MAIL = "resume_writers_mail"
    RESUME_WRITERS_OBSERVE_MAIL = "resume_writers_observe_mail"
    RESUME_WRITERS_MIRROR = "resume_writers_mirror"
    RESUME_WRITERS_OBSERVE_MIRROR = "resume_writers_observe_mirror"
    RESUME_WRITERS_COMMIT = "resume_writers_commit"
    COMPLETED = "completed"


STAGE_ORDER: tuple[CutoverStage, ...] = tuple(CutoverStage)
READ_ONLY_STAGES = frozenset({CutoverStage.PLAN_PREFLIGHT})


class CutoverFailureCategory(enum.Enum):
    PREFLIGHT = "preflight"
    SAFETY = "safety"
    APPLY = "apply"
    VERIFY = "verify"
    AMBIGUOUS = "ambiguous"


class CutoverError(BackupError):
    def __init__(
        self,
        message: str,
        *,
        category: CutoverFailureCategory,
        exit_code: int | None = None,
        recovery: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.exit_code = exit_code or {
            CutoverFailureCategory.PREFLIGHT: EXIT_PREFLIGHT,
            CutoverFailureCategory.SAFETY: EXIT_SAFETY,
            CutoverFailureCategory.APPLY: EXIT_APPLY,
            CutoverFailureCategory.VERIFY: EXIT_VERIFY,
            CutoverFailureCategory.AMBIGUOUS: EXIT_AMBIGUOUS,
        }[category]
        self.recovery = recovery
        self.evidence = evidence or {}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fail(
    message: str,
    *,
    category: CutoverFailureCategory,
    recovery: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    raise CutoverError(
        message, category=category, recovery=recovery, evidence=evidence
    )


def next_stage(current: CutoverStage) -> CutoverStage | None:
    idx = STAGE_ORDER.index(current)
    if idx + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[idx + 1]


def previous_stage(current: CutoverStage) -> CutoverStage | None:
    idx = STAGE_ORDER.index(current)
    if idx == 0:
        return None
    return STAGE_ORDER[idx - 1]


def companion_paths(path: Path) -> list[Path]:
    return [Path(str(path) + s) for s in ("-wal", "-shm", "-journal")]


def sanitize_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    blob = json.dumps(payload, sort_keys=True, default=str)
    if _ABS_PATH.search(blob) or _EMAIL.search(blob):
        _fail(
            "evidence contains absolute path or email-like text",
            category=CutoverFailureCategory.VERIFY,
        )
    return payload


def fingerprint_token(path: Path) -> str:
    fp = fingerprint_file(path)
    return f"{fp.size_bytes}:{fp.mtime_ns}:{fp.device}:{fp.inode}"


def path_identity_token(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "basename": path.name,
        "size_bytes": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "device": int(st.st_dev),
        "inode": int(st.st_ino),
    }


def parent_identity_token(path: Path) -> dict[str, Any]:
    parent = path if path.is_dir() else path.parent
    st = parent.stat()
    return {"basename": parent.name, "device": int(st.st_dev), "inode": int(st.st_ino)}


@dataclass
class GitIdentity:
    head_sha: str
    branch: str
    clean_worktree: bool
    local_main_sha: str
    origin_main_sha: str

    @property
    def local_main_matches_origin(self) -> bool:
        return bool(self.local_main_sha) and self.local_main_sha == self.origin_main_sha


@dataclass
class ServiceState:
    api_active: bool = False
    health_timer_active: bool = False


@dataclass
class LockRecord:
    basename: str
    classification: str  # live | stale | malformed | absent
    pid: int | None = None
    detail: str = ""


@dataclass
class WriterInventory:
    mail_pause_present: bool = False
    mirror_pause_present: bool = False
    locks: list[LockRecord] = field(default_factory=list)
    fd_hits: list[dict[str, Any]] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    orchestrator_pid: int = field(default_factory=os.getpid)

    @property
    def live_writer_pids(self) -> list[int]:
        return [
            int(r.pid)
            for r in self.locks
            if r.classification == "live" and r.pid is not None
        ]

    @property
    def lock_basenames(self) -> list[str]:
        return [r.basename for r in self.locks if r.classification != "absent"]

    @property
    def writers_quiesced(self) -> bool:
        if not self.mail_pause_present or not self.mirror_pause_present:
            return False
        if any(r.classification in {"live", "malformed"} for r in self.locks):
            return False
        # Foreign FD hits (not our pid) block quiesce.
        for hit in self.fd_hits:
            if int(hit.get("pid") or -1) != self.orchestrator_pid:
                return False
        if self.unreadable:
            return False
        return True


@dataclass
class ApprovedPlan:
    maintenance_id: str
    expected_main_sha: str
    production_basename: str
    production_fingerprint: str
    production_device: int
    production_inode: int
    backup_dest_basename: str
    staging_dest_basename: str
    backup_parent_device: int
    backup_parent_inode: int
    staging_parent_device: int
    staging_parent_inode: int
    reports_active_basename: str
    mail_pause_basename: str
    mirror_pause_basename: str
    capacity_backup_required_bytes: int
    capacity_staging_required_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovedPlan:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: data[k] for k in data if k in known})


@dataclass
class CutoverJournal:
    schema_version: int = CUTOVER_SCHEMA_VERSION
    tool: str = TOOL_NAME
    maintenance_id: str = ""
    stage: str = CutoverStage.PLAN_PREFLIGHT.value
    expected_main_sha: str = ""
    expected_production_basename: str = ""
    production_fingerprint: str | None = None
    production_device: int | None = None
    production_inode: int | None = None
    approved_plan: dict[str, Any] | None = None
    backup_basename: str | None = None
    staging_basename: str | None = None
    pre_cutover_basename: str | None = None
    candidate_fingerprint: str | None = None
    backup_fingerprint: str | None = None
    backup_source_fingerprint_before: str | None = None
    backup_source_fingerprint_after: str | None = None
    compact_source_fingerprint: str | None = None
    compact_dest_fingerprint: str | None = None
    swap_approved: bool = False
    writers_resumed: bool = False
    writer_resume_started: bool = False
    services_stopped: bool = False
    wal_quiesced: bool = False
    backup_verified: bool = False
    compact_verified: bool = False
    smoke_ok: bool = False
    swap_direction: str | None = None
    swap_intent: dict[str, Any] | None = None
    exchange_completed: bool = False
    old_production_retained: bool = False
    rollback_intent: dict[str, Any] | None = None
    updated_at_utc: str = field(default_factory=_iso_now)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CutoverJournal:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: data[k] for k in data if k in known})


@dataclass
class PrivatePlanPaths:
    """Exact local paths — never emitted in public evidence."""

    production_path: str
    backup_dest: str
    staging_dest: str
    reports_dir: str
    journal_path: str
    journal_dir: str


class CutoverAdapters(Protocol):
    def path_exists(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def read_text(self, path: Path) -> str: ...
    def write_text_atomic(self, path: Path, text: str) -> None: ...
    def touch(self, path: Path) -> None: ...
    def unlink(self, path: Path) -> None: ...
    def mkdir(self, path: Path) -> None: ...
    def disk_free(self, path: Path) -> int: ...
    def same_fs(self, a: Path, b: Path) -> bool: ...
    def fingerprint(self, path: Path) -> str: ...
    def path_identity(self, path: Path) -> dict[str, Any]: ...
    def parent_identity(self, path: Path) -> dict[str, Any]: ...
    def list_writers(self, production: Path | None = None) -> WriterInventory: ...
    def service_state(self) -> ServiceState: ...
    def stop_api(self) -> None: ...
    def start_api(self) -> None: ...
    def stop_health_timer(self) -> None: ...
    def start_health_timer(self) -> None: ...
    def wal_state(self, db: Path) -> dict[str, Any]: ...
    def checkpoint_wal(self, db: Path) -> dict[str, Any]: ...
    def create_online_backup(self, source: Path, dest: Path) -> dict[str, Any]: ...
    def compact_offline(self, source: Path, dest: Path) -> dict[str, Any]: ...
    def verify_candidate(
        self, path: Path, *, backup_fingerprint: str | None = None
    ) -> dict[str, Any]: ...
    def probe_rename_exchange(self, directory: Path) -> bool: ...
    def rename_exchange(self, a: Path, b: Path) -> None: ...
    def rename_noreplace(self, src: Path, dest: Path) -> None: ...
    def http_smoke(self, base_url: str, *, expected_fingerprint: str) -> dict[str, Any]: ...
    def git_identity(self) -> GitIdentity: ...
    def acquire_exclusive_lock(
        self, production: Path, maintenance_id: str
    ) -> Any: ...
    def fsync_dir(self, path: Path) -> None: ...


def find_repository_root(start: Path | None = None) -> Path:
    cur = (start or Path(__file__).resolve()).resolve()
    if cur.is_file():
        cur = cur.parent
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    _fail(
        "unable to locate git repository root",
        category=CutoverFailureCategory.SAFETY,
    )
    raise AssertionError("unreachable")


def _run_git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(
            f"git invocation failed ({type(exc).__name__})",
            category=CutoverFailureCategory.SAFETY,
        )
        raise AssertionError("unreachable") from exc
    if proc.returncode != 0:
        _fail(
            f"git {' '.join(args)} failed rc={proc.returncode}",
            category=CutoverFailureCategory.SAFETY,
        )
    return (proc.stdout or "").strip()


def resolve_repository_git_identity(repo_root: Path | None = None) -> GitIdentity:
    """Bounded non-interactive git identity. Never consults environment SHA vars."""
    repo = find_repository_root(repo_root)
    head = _run_git(repo, "rev-parse", "HEAD")
    if not FULL_SHA_RE.fullmatch(head):
        _fail(
            "git HEAD is not a complete 40-character SHA",
            category=CutoverFailureCategory.SAFETY,
        )
    branch = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    porcelain = _run_git(repo, "status", "--porcelain")
    local_main = _run_git(repo, "rev-parse", "main")
    origin_main = _run_git(repo, "rev-parse", "origin/main")
    if not FULL_SHA_RE.fullmatch(local_main) or not FULL_SHA_RE.fullmatch(origin_main):
        _fail(
            "main/origin/main are not complete 40-character SHAs",
            category=CutoverFailureCategory.SAFETY,
        )
    return GitIdentity(
        head_sha=head,
        branch=branch,
        clean_worktree=porcelain == "",
        local_main_sha=local_main,
        origin_main_sha=origin_main,
    )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _parse_lock_payload(text: str) -> tuple[int | None, str]:
    text = text.strip()
    if not text:
        return None, "empty"
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "pid" in data:
            return int(data["pid"]), "json"
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    for line in text.splitlines():
        if line.startswith("pid="):
            try:
                return int(line.split("=", 1)[1].strip()), "pid_line"
            except ValueError:
                return None, "malformed_pid_line"
    return None, "unrecognized"


def classify_lock_file(path: Path) -> LockRecord:
    if not path.is_file():
        return LockRecord(basename=path.name, classification="absent")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return LockRecord(
            basename=path.name, classification="malformed", detail="unreadable"
        )
    pid, kind = _parse_lock_payload(text)
    if pid is None:
        return LockRecord(
            basename=path.name, classification="malformed", detail=kind
        )
    if _pid_alive(pid):
        return LockRecord(
            basename=path.name, classification="live", pid=pid, detail=kind
        )
    return LockRecord(
        basename=path.name, classification="stale", pid=pid, detail=kind
    )


def scan_proc_fds_for_inode(
    *,
    device: int,
    inode: int,
    sidecar_inodes: set[tuple[int, int]],
    uid: int | None = None,
) -> list[dict[str, Any]]:
    """Scan same-user /proc/*/fd for production DB or sidecar inodes."""
    hits: list[dict[str, Any]] = []
    want_uid = os.getuid() if uid is None else uid
    proc = Path("/proc")
    if not proc.is_dir():
        return hits
    try:
        entries = list(proc.iterdir())
    except OSError:
        _fail(
            "unable to list /proc for FD inventory",
            category=CutoverFailureCategory.AMBIGUOUS,
        )
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            st = entry.stat()
            if st.st_uid != want_uid:
                continue
            fd_dir = entry / "fd"
            for fd in fd_dir.iterdir():
                try:
                    target = os.stat(fd)
                except OSError:
                    continue
                key = (int(target.st_dev), int(target.st_ino))
                if key == (device, inode) or key in sidecar_inodes:
                    hits.append(
                        {
                            "pid": pid,
                            "fd": fd.name,
                            "device": key[0],
                            "inode": key[1],
                            "kind": "production" if key == (device, inode) else "sidecar",
                        }
                    )
        except OSError:
            continue
    return hits


class CutoverExclusiveLock:
    """Exclusive flock keyed by production device/inode + maintenance id."""

    def __init__(self, production: Path, maintenance_id: str, lock_dir: Path | None = None) -> None:
        st = production.stat()
        self.key = f"dev{st.st_dev}_ino{st.st_ino}_{maintenance_id}.lock"
        base = lock_dir or (Path.home() / ".cache" / "origenlab" / LOCK_DIR_NAME)
        self.path = base / self.key
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            _fail(
                f"cutover lock contention ({type(exc).__name__})",
                category=CutoverFailureCategory.SAFETY,
                recovery="Another cutover stage holds the exclusive lock; wait or inspect.",
            )
        payload = f"pid={os.getpid()} started_at={_iso_now()}\n"
        os.ftruncate(self._fd, 0)
        os.write(self._fd, payload.encode())
        os.fsync(self._fd)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> CutoverExclusiveLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


def _default_systemctl(args: list[str]) -> int:
    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return int(proc.returncode)
    except (OSError, subprocess.TimeoutExpired):
        return 127


def _probe_rename_exchange(directory: Path) -> bool:
    stamp = f"{os.getpid()}_{time.time_ns()}"
    a = directory / f".origenlab_rex_probe_{stamp}.a"
    b = directory / f".origenlab_rex_probe_{stamp}.b"
    try:
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        _renameat2(a, b, flags=2)  # RENAME_EXCHANGE
        return a.read_bytes() == b"b" and b.read_bytes() == b"a"
    except OSError:
        return False
    finally:
        for path in (a, b):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _renameat2(a: Path, b: Path, *, flags: int) -> None:
    import ctypes
    import ctypes.util

    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        raise OSError(errno.ENOSYS, "libc not found")
    libc = ctypes.CDLL(libc_name, use_errno=True)
    AT_FDCWD = -100
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rc = renameat2(
        AT_FDCWD,
        os.fsencode(str(a)),
        AT_FDCWD,
        os.fsencode(str(b)),
        flags,
    )
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"renameat2 flags={flags} failed errno={err}")


def _rename_exchange(a: Path, b: Path) -> None:
    _renameat2(a, b, flags=2)  # RENAME_EXCHANGE


def _rename_noreplace(src: Path, dest: Path) -> None:
    # RENAME_NOREPLACE = 1
    _renameat2(src, dest, flags=1)


@dataclass
class FilesystemAdapters:
    """Real OS adapters. Mutating apply is blocked while writers remain unguarded."""

    settings: Settings | None = None
    http_get: Callable[[str], dict[str, Any]] | None = None
    systemctl: Callable[[list[str]], int] | None = None
    rename_exchange_supported: bool | None = None
    repo_root: Path | None = None
    lock_dir: Path | None = None
    held_locks: dict[str, CutoverExclusiveLock] = field(default_factory=dict)

    def path_exists(self, path: Path) -> bool:
        try:
            return path.exists() or path.is_symlink()
        except OSError:
            return False

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def is_file(self, path: Path) -> bool:
        return path.is_file() and not path.is_symlink()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir() and not path.is_symlink()

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_text_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        prev = path.with_name(path.name + ".prev")
        partial = path.with_name(f"{path.name}.partial.{os.getpid()}.{time.time_ns()}")
        fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and path.is_file():
            try:
                os.replace(path, prev)
            except OSError:
                pass
        os.replace(partial, path)
        fsync_file(path)
        fsync_directory(path.parent)

    def touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        fsync_file(path)
        fsync_directory(path.parent)

    def unlink(self, path: Path) -> None:
        path.unlink()

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def disk_free(self, path: Path) -> int:
        return disk_free_bytes(path)

    def same_fs(self, a: Path, b: Path) -> bool:
        return same_filesystem(a, b)

    def fingerprint(self, path: Path) -> str:
        return fingerprint_token(path)

    def path_identity(self, path: Path) -> dict[str, Any]:
        return path_identity_token(path)

    def parent_identity(self, path: Path) -> dict[str, Any]:
        return parent_identity_token(path)

    def list_writers(self, production: Path | None = None) -> WriterInventory:
        settings = self.settings or load_settings(enable_dotenv=False)
        reports = settings.resolved_reports_dir()
        active = reports / "active" / "current"
        mail_pause = (active / MAIL_PAUSE_FILENAME).is_file()
        mirror_pause = (active / DASHBOARD_PAUSE_FILENAME).is_file()
        locks: list[LockRecord] = []
        unreadable: list[str] = []
        if active.is_dir():
            for name in (
                MAIL_LOCK_FILENAME,
                DASHBOARD_LOCK_FILENAME,
                CHILECOMPRA_LOCK_FILENAME,
            ):
                locks.append(classify_lock_file(active / name))
            for lock in sorted(active.glob("*.lock")):
                if lock.name in {
                    MAIL_LOCK_FILENAME,
                    DASHBOARD_LOCK_FILENAME,
                    CHILECOMPRA_LOCK_FILENAME,
                }:
                    continue
                locks.append(classify_lock_file(lock))
        fd_hits: list[dict[str, Any]] = []
        if production is not None and production.is_file():
            try:
                st = production.stat()
                side_inos: set[tuple[int, int]] = set()
                for side in companion_paths(production):
                    if side.is_file():
                        sst = side.stat()
                        side_inos.add((int(sst.st_dev), int(sst.st_ino)))
                fd_hits = scan_proc_fds_for_inode(
                    device=int(st.st_dev),
                    inode=int(st.st_ino),
                    sidecar_inodes=side_inos,
                )
            except OSError:
                unreadable.append("production_stat_or_proc")
        return WriterInventory(
            mail_pause_present=mail_pause,
            mirror_pause_present=mirror_pause,
            locks=locks,
            fd_hits=fd_hits,
            unreadable=unreadable,
        )

    def service_state(self) -> ServiceState:
        fn = self.systemctl or _default_systemctl
        return ServiceState(
            api_active=fn(["--user", "is-active", API_SERVICE]) == 0,
            health_timer_active=fn(["--user", "is-active", API_HEALTH_TIMER]) == 0,
        )

    def stop_api(self) -> None:
        fn = self.systemctl or _default_systemctl
        if fn(["--user", "stop", API_SERVICE]) != 0:
            _fail(f"failed to stop {API_SERVICE}", category=CutoverFailureCategory.APPLY)

    def start_api(self) -> None:
        fn = self.systemctl or _default_systemctl
        if fn(["--user", "start", API_SERVICE]) != 0:
            _fail(f"failed to start {API_SERVICE}", category=CutoverFailureCategory.APPLY)

    def stop_health_timer(self) -> None:
        fn = self.systemctl or _default_systemctl
        if fn(["--user", "stop", API_HEALTH_TIMER]) != 0:
            _fail(
                f"failed to stop {API_HEALTH_TIMER}",
                category=CutoverFailureCategory.APPLY,
            )

    def start_health_timer(self) -> None:
        fn = self.systemctl or _default_systemctl
        if fn(["--user", "start", API_HEALTH_TIMER]) != 0:
            _fail(
                f"failed to start {API_HEALTH_TIMER}",
                category=CutoverFailureCategory.APPLY,
            )

    def wal_state(self, db: Path) -> dict[str, Any]:
        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        journal = Path(str(db) + "-journal")
        return {
            "wal_present": wal.exists(),
            "wal_size": int(wal.stat().st_size) if wal.is_file() else 0,
            "shm_present": shm.exists(),
            "shm_size": int(shm.stat().st_size) if shm.is_file() else 0,
            "journal_present": journal.exists(),
            "db_fingerprint": fingerprint_token(db) if db.is_file() else None,
        }

    def checkpoint_wal(self, db: Path) -> dict[str, Any]:
        uri = f"file:{db.resolve().as_posix()}?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            before = self.wal_state(db)
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row is None or len(row) < 3:
                _fail(
                    "wal_checkpoint returned unexpected row",
                    category=CutoverFailureCategory.VERIFY,
                )
            busy, log_frames, checkpointed = int(row[0]), int(row[1]), int(row[2])
            if busy != 0:
                _fail(
                    f"wal_checkpoint busy={busy}",
                    category=CutoverFailureCategory.VERIFY,
                    evidence={"busy": busy, "log": log_frames, "checkpointed": checkpointed},
                )
            conn.commit()
            after = self.wal_state(db)
            if after["wal_size"] > 0:
                _fail(
                    "WAL still non-empty after checkpoint",
                    category=CutoverFailureCategory.VERIFY,
                    evidence={"after": after},
                )
            return {
                "before": before,
                "after": after,
                "checkpoint": {
                    "busy": busy,
                    "log": log_frames,
                    "checkpointed": checkpointed,
                    "mode": "TRUNCATE",
                },
            }
        finally:
            conn.close()

    def create_online_backup(self, source: Path, dest: Path) -> dict[str, Any]:
        source_fp_before = fingerprint_token(source)
        result = run_online_backup(
            BackupOptions(
                source=source,
                destination=dest,
                apply=True,
                allow_same_filesystem=False,
                fail_if_source_fingerprint_changes=True,
            )
        )
        if not backup_is_completed(dest):
            _fail(
                "online backup did not complete with final DB+manifest",
                category=CutoverFailureCategory.VERIFY,
            )
        man = json.loads(backup_manifest_path_for(dest).read_text(encoding="utf-8"))
        if man.get("completed") is not True:
            _fail("backup manifest completed!=true", category=CutoverFailureCategory.VERIFY)
        dest_fp = fingerprint_token(dest)
        source_fp_after = fingerprint_token(source)
        if source_fp_after != source_fp_before:
            _fail(
                "source fingerprint changed across backup",
                category=CutoverFailureCategory.VERIFY,
            )
        for side in companion_paths(dest):
            if side.exists():
                _fail(
                    f"backup has sidecar {sanitize_path_for_log(side)}",
                    category=CutoverFailureCategory.VERIFY,
                )
        return {
            "completed": True,
            "destination_basename": dest.name,
            "destination_fingerprint": dest_fp,
            "source_fingerprint_before": source_fp_before,
            "source_fingerprint_after": source_fp_after,
            "manifest_completed": True,
            "method": man.get("method"),
            "raw_keys": sorted(result.keys())[:20],
        }

    def compact_offline(self, source: Path, dest: Path) -> dict[str, Any]:
        source_fp = fingerprint_token(source)
        result = run_offline_compaction(
            CompactionOptions(
                source=source,
                destination=dest,
                confirm_offline_copy=True,
                apply=True,
                allow_same_filesystem=True,
                settings=self.settings,
            )
        )
        if not compaction_is_completed(dest):
            _fail(
                "compaction did not complete with final DB+manifest",
                category=CutoverFailureCategory.VERIFY,
            )
        man = json.loads(compaction_manifest_path_for(dest).read_text(encoding="utf-8"))
        if man.get("completed") is not True:
            _fail(
                "compaction manifest completed!=true",
                category=CutoverFailureCategory.VERIFY,
            )
        if fingerprint_token(source) != source_fp:
            _fail(
                "compact source fingerprint changed",
                category=CutoverFailureCategory.VERIFY,
            )
        dest_fp = fingerprint_token(dest)
        return {
            "completed": True,
            "destination_basename": dest.name,
            "destination_fingerprint": dest_fp,
            "source_fingerprint": source_fp,
            "manifest_completed": True,
            "method": man.get("method"),
            "raw_keys": sorted(result.keys())[:20],
        }

    def verify_candidate(
        self, path: Path, *, backup_fingerprint: str | None = None
    ) -> dict[str, Any]:
        _refuse_evidence_compact(path)
        assert_no_sidecars(path, label="candidate")
        man_path = compaction_manifest_path_for(path)
        if not man_path.is_file():
            _fail("compaction manifest missing", category=CutoverFailureCategory.VERIFY)
        man = json.loads(man_path.read_text(encoding="utf-8"))
        if man.get("completed") is not True:
            _fail(
                "compaction manifest incomplete",
                category=CutoverFailureCategory.VERIFY,
            )
        # Lineage: compaction source should be the fresh backup basename.
        src_base = man.get("source_basename")
        if backup_fingerprint is None and not src_base:
            _fail(
                "compaction manifest missing source lineage",
                category=CutoverFailureCategory.VERIFY,
            )
        conn = connect_candidate_readonly(path)
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()
            if not quick or quick[0] != "ok":
                _fail("quick_check failed", category=CutoverFailureCategory.VERIFY)
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                _fail(
                    f"foreign_key_check violations={len(fk)}",
                    category=CutoverFailureCategory.VERIFY,
                )
            schema = schema_fingerprint(conn)
            identity = database_identity_props(conn)
            counts = critical_table_counts(conn)
            stats = storage_stats(conn)
        finally:
            conn.close()
        fp1 = fingerprint_token(path)
        fp2 = fingerprint_token(path)
        if fp1 != fp2:
            _fail(
                "candidate fingerprint unstable",
                category=CutoverFailureCategory.VERIFY,
            )
        return {
            "quick_check_ok": True,
            "foreign_key_violations": 0,
            "schema_fingerprint": schema,
            "database_identity": identity,
            "critical_table_counts": counts,
            "storage": {
                k: stats[k]
                for k in ("page_count", "page_size", "freelist_count", "allocated_bytes")
                if k in stats
            },
            "fingerprint": fp1,
            "basename": path.name,
            "manifest_completed": True,
            "source_basename": src_base,
        }

    def probe_rename_exchange(self, directory: Path) -> bool:
        if self.rename_exchange_supported is not None:
            return bool(self.rename_exchange_supported)
        return _probe_rename_exchange(directory)

    def rename_exchange(self, a: Path, b: Path) -> None:
        _rename_exchange(a, b)

    def rename_noreplace(self, src: Path, dest: Path) -> None:
        _rename_noreplace(src, dest)

    def http_smoke(self, base_url: str, *, expected_fingerprint: str) -> dict[str, Any]:
        getter = self.http_get
        if getter is None:
            _fail(
                "http smoke getter not configured",
                category=CutoverFailureCategory.VERIFY,
            )
        results: dict[str, Any] = {}
        health = getter(base_url.rstrip("/") + "/health")
        if health.get("status") not in {"ok", "healthy", True} and "ok" not in str(
            health.get("status", "")
        ).lower():
            # Accept common shapes: {"status":"ok"} or sqlite readiness fields.
            if not health.get("sqlite_ok", health.get("ok")):
                _fail(
                    "health semantic check failed",
                    category=CutoverFailureCategory.VERIFY,
                    evidence={"keys": sorted(health.keys())[:12]},
                )
        results["/health"] = {"ok": True, "status": health.get("status")}
        status = getter(base_url.rstrip("/") + "/operator/status")
        if "sqlite" not in json.dumps(status).lower() and "status" not in status:
            _fail(
                "operator/status semantic check failed",
                category=CutoverFailureCategory.VERIFY,
            )
        results["/operator/status"] = {"ok": True}
        auto = getter(base_url.rstrip("/") + "/operator/automation-status")
        if "mail" not in json.dumps(auto).lower() and "automation" not in json.dumps(
            auto
        ).lower() and "status" not in auto:
            _fail(
                "automation-status semantic check failed",
                category=CutoverFailureCategory.VERIFY,
            )
        results["/operator/automation-status"] = {"ok": True}
        # Optional explicit fingerprint field from recovery-aware health.
        opened = health.get("production_fingerprint") or status.get(
            "production_fingerprint"
        )
        if opened is not None and opened != expected_fingerprint:
            _fail(
                "API production fingerprint mismatch",
                category=CutoverFailureCategory.VERIFY,
            )
        results["expected_fingerprint"] = expected_fingerprint
        results["reported_fingerprint"] = opened
        return results

    def git_identity(self) -> GitIdentity:
        return resolve_repository_git_identity(self.repo_root)

    @contextmanager
    def acquire_exclusive_lock(
        self, production: Path, maintenance_id: str
    ) -> Generator[CutoverExclusiveLock, None, None]:
        lock = CutoverExclusiveLock(production, maintenance_id, lock_dir=self.lock_dir)
        lock.acquire()
        try:
            yield lock
        finally:
            lock.release()

    def fsync_dir(self, path: Path) -> None:
        fsync_directory(path)


@dataclass
class CutoverOptions:
    stage: CutoverStage = CutoverStage.PLAN_PREFLIGHT
    apply: bool = False
    confirm_production_cutover: bool = False
    maintenance_id: str = ""
    expected_main_sha: str = ""
    expected_production_path: Path | None = None
    expected_production_fingerprint: str = ""
    approve_swap: bool = False
    journal_path: Path | None = None
    backup_dest: Path | None = None
    staging_dest: Path | None = None
    reports_dir: Path | None = None
    api_base_url: str = "http://127.0.0.1:8001"
    adapters: CutoverAdapters | None = None
    settings: Settings | None = None
    fail_after: str | None = None
    allow_synthetic_world: bool = False


def canonical_journal_dir(production: Path) -> Path:
    return production.parent / JOURNAL_DIR_NAME


def journal_path_for(opts: CutoverOptions, production: Path) -> Path:
    canonical = canonical_journal_dir(production) / f"{opts.maintenance_id}.journal.json"
    if opts.journal_path is None:
        return canonical
    requested = opts.journal_path.expanduser()
    # Constrain: must resolve under canonical journal dir (no path escape).
    try:
        req_res = requested.resolve()
        can_res = canonical_journal_dir(production).resolve()
    except OSError:
        _fail(
            "unable to resolve journal path",
            category=CutoverFailureCategory.SAFETY,
        )
        raise AssertionError("unreachable")
    if can_res not in req_res.parents and req_res.parent != can_res:
        _fail(
            "refusing --journal-path outside approved cutover journals directory",
            category=CutoverFailureCategory.SAFETY,
        )
    if req_res.name != canonical.name:
        _fail(
            "journal basename must match maintenance_id.journal.json",
            category=CutoverFailureCategory.SAFETY,
        )
    return requested


def private_journal_path(journal: Path) -> Path:
    return journal.with_name(journal.name + ".private.json")


def mail_pause_path(reports_dir: Path) -> Path:
    return reports_dir / "active" / "current" / MAIL_PAUSE_FILENAME


def mirror_pause_path(reports_dir: Path) -> Path:
    return reports_dir / "active" / "current" / DASHBOARD_PAUSE_FILENAME


def _refuse_evidence_compact(path: Path) -> None:
    name = path.name
    if name == KNOWN_EVIDENCE_COMPACT or name.startswith(FORBIDDEN_COMPACT_PREFIXES):
        _fail(
            "refusing July/offline compact evidence artifact as cutover source",
            category=CutoverFailureCategory.SAFETY,
        )


def _validate_journal_location(adapters: CutoverAdapters, path: Path, production: Path) -> None:
    parent = path.parent
    if adapters.is_symlink(parent) or adapters.is_symlink(path):
        _fail(
            "refusing symlinked journal path",
            category=CutoverFailureCategory.SAFETY,
        )
    expected_dir = canonical_journal_dir(production)
    if path.name != f"{path.name}" or not path.name.endswith(".journal.json"):
        _fail("invalid journal basename", category=CutoverFailureCategory.SAFETY)
    # Parent basename must be journals dir.
    if parent.name != JOURNAL_DIR_NAME:
        _fail(
            "journal parent must be .origenlab_cutover_journals",
            category=CutoverFailureCategory.SAFETY,
        )


def load_journal(adapters: CutoverAdapters, path: Path) -> CutoverJournal | None:
    if not adapters.path_exists(path):
        return None
    if adapters.is_symlink(path) or not adapters.is_file(path):
        _fail(
            "journal must be a regular non-symlink file",
            category=CutoverFailureCategory.AMBIGUOUS,
        )
    try:
        data = json.loads(adapters.read_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            f"unreadable cutover journal ({type(exc).__name__})",
            category=CutoverFailureCategory.AMBIGUOUS,
            recovery="Do not guess; inspect journal under cutover journals dir.",
        )
    if data.get("schema_version") != CUTOVER_SCHEMA_VERSION:
        _fail(
            "journal schema_version mismatch",
            category=CutoverFailureCategory.AMBIGUOUS,
        )
    if data.get("tool") != TOOL_NAME:
        _fail("journal tool mismatch", category=CutoverFailureCategory.AMBIGUOUS)
    return CutoverJournal.from_dict(data)


def write_journal(adapters: CutoverAdapters, path: Path, journal: CutoverJournal) -> None:
    journal.updated_at_utc = _iso_now()
    payload = sanitize_evidence(journal.to_dict())
    adapters.write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_private_paths(adapters: CutoverAdapters, journal: Path, paths: PrivatePlanPaths) -> None:
    # Private file intentionally contains absolute paths; never sanitize_evidence.
    dest = private_journal_path(journal)
    adapters.write_text_atomic(dest, json.dumps(asdict(paths), indent=2, sort_keys=True) + "\n")


def load_private_paths(adapters: CutoverAdapters, journal: Path) -> PrivatePlanPaths | None:
    path = private_journal_path(journal)
    if not adapters.path_exists(path):
        return None
    data = json.loads(adapters.read_text(path))
    return PrivatePlanPaths(**{k: data[k] for k in PrivatePlanPaths.__dataclass_fields__})


def _require_auth(opts: CutoverOptions, *, for_swap: bool = False) -> None:
    if not opts.confirm_production_cutover:
        _fail(
            "production cutover requires --confirm-production-cutover",
            category=CutoverFailureCategory.SAFETY,
        )
    if not MAINTENANCE_ID_RE.fullmatch(opts.maintenance_id or ""):
        _fail(
            "invalid --maintenance-id "
            f"(require {MAINTENANCE_ID_RE.pattern})",
            category=CutoverFailureCategory.SAFETY,
        )
    if not FULL_SHA_RE.fullmatch(opts.expected_main_sha or ""):
        _fail(
            "--expected-main-sha must be the complete 40-character SHA",
            category=CutoverFailureCategory.SAFETY,
        )
    if opts.expected_production_path is None:
        _fail(
            "--expected-production-path required",
            category=CutoverFailureCategory.SAFETY,
        )
    if not opts.expected_production_fingerprint:
        _fail(
            "--expected-production-fingerprint required",
            category=CutoverFailureCategory.SAFETY,
        )
    if for_swap and not opts.approve_swap:
        _fail(
            "swap stages require separate --approve-swap",
            category=CutoverFailureCategory.SAFETY,
        )


def _is_synthetic(opts: CutoverOptions, adapters: CutoverAdapters) -> bool:
    return bool(opts.allow_synthetic_world) and isinstance(adapters, SyntheticWorld)


def _validate_production_identity(
    opts: CutoverOptions, adapters: CutoverAdapters, production: Path
) -> str:
    if adapters.is_symlink(production):
        _fail("refusing symlink production path", category=CutoverFailureCategory.SAFETY)
    if not adapters.is_file(production):
        _fail(
            "expected production path is not a regular file",
            category=CutoverFailureCategory.SAFETY,
        )
    expected = opts.expected_production_path
    assert expected is not None
    if production.name != "emails.sqlite" and production.name != expected.name:
        _fail("production basename drift", category=CutoverFailureCategory.SAFETY)
    if production.name != expected.name:
        _fail("production basename drift", category=CutoverFailureCategory.SAFETY)

    if not _is_synthetic(opts, adapters):
        canonical = canonical_production_sqlite_path()
        try:
            if production.resolve() != canonical.resolve():
                _fail(
                    "refusing non-canonical production path "
                    "(arbitrary emails.sqlite is not enough)",
                    category=CutoverFailureCategory.SAFETY,
                )
            if not paths_same_file(production, canonical):
                _fail(
                    "production path is not samefile as canonical production",
                    category=CutoverFailureCategory.SAFETY,
                )
        except OSError:
            _fail(
                "unable to verify canonical production samefile",
                category=CutoverFailureCategory.SAFETY,
            )
        if expected.expanduser().resolve() != canonical.resolve():
            _fail(
                "--expected-production-path must be canonical production",
                category=CutoverFailureCategory.SAFETY,
            )
    else:
        # Synthetic: exact path match against expected only.
        if str(production) != str(expected.expanduser()):
            # Also allow resolve equality within temp world.
            try:
                if production.resolve() != expected.expanduser().resolve():
                    _fail(
                        "synthetic production path drift",
                        category=CutoverFailureCategory.SAFETY,
                    )
            except OSError:
                _fail(
                    "synthetic production path drift",
                    category=CutoverFailureCategory.SAFETY,
                )

    fp = adapters.fingerprint(production)
    if fp != opts.expected_production_fingerprint:
        _fail(
            "production fingerprint mismatch vs expected",
            category=CutoverFailureCategory.SAFETY,
        )
    return fp


def _require_git_match(opts: CutoverOptions, adapters: CutoverAdapters) -> GitIdentity:
    identity = adapters.git_identity()
    if identity.head_sha != opts.expected_main_sha:
        _fail(
            "main SHA mismatch vs --expected-main-sha (exact 40-char required)",
            category=CutoverFailureCategory.SAFETY,
        )
    if not _is_synthetic(opts, adapters):
        if identity.branch != "main":
            _fail(
                "production apply requires branch main",
                category=CutoverFailureCategory.SAFETY,
            )
        if not identity.clean_worktree:
            _fail(
                "production apply requires clean worktree",
                category=CutoverFailureCategory.SAFETY,
            )
        if not identity.local_main_matches_origin:
            _fail(
                "production apply requires local main == origin/main",
                category=CutoverFailureCategory.SAFETY,
            )
    return identity


def _assert_writers_quiesced(
    adapters: CutoverAdapters,
    production: Path,
    *,
    allow_own_fd: bool = False,
) -> WriterInventory:
    writers = adapters.list_writers(production)
    if not writers.mail_pause_present or not writers.mirror_pause_present:
        _fail(
            "pause markers missing",
            category=CutoverFailureCategory.SAFETY,
        )
    if any(r.classification == "malformed" for r in writers.locks):
        _fail(
            "malformed writer lock(s) present",
            category=CutoverFailureCategory.SAFETY,
            evidence={"locks": [asdict(r) for r in writers.locks]},
        )
    if any(r.classification == "live" for r in writers.locks):
        _fail(
            "live writer lock(s) present",
            category=CutoverFailureCategory.SAFETY,
            evidence={"locks": [asdict(r) for r in writers.locks]},
        )
    foreign = [
        h
        for h in writers.fd_hits
        if int(h.get("pid") or -1) != writers.orchestrator_pid
    ]
    if foreign and not allow_own_fd:
        _fail(
            "foreign process FD holds production/sidecar inode",
            category=CutoverFailureCategory.SAFETY,
            evidence={"fd_hits": foreign[:8]},
        )
    if writers.unreadable:
        _fail(
            "unreadable writer evidence",
            category=CutoverFailureCategory.AMBIGUOUS,
            evidence={"unreadable": writers.unreadable},
        )
    return writers


def _assert_sidecars_smoke_policy(adapters: CutoverAdapters, production: Path) -> dict[str, Any]:
    wal = Path(str(production) + "-wal")
    shm = Path(str(production) + "-shm")
    journal = Path(str(production) + "-journal")
    evidence: dict[str, Any] = {}
    if adapters.path_exists(journal):
        _fail(
            "unexpected -journal sidecar during smoke",
            category=CutoverFailureCategory.VERIFY,
        )
    if adapters.path_exists(wal):
        # Prefer size via fingerprint/path_identity when file-backed.
        try:
            size = int(adapters.path_identity(wal)["size_bytes"])
        except Exception:  # noqa: BLE001
            size = -1
        evidence["wal_size"] = size
        if size > 0:
            _fail(
                "non-empty WAL sidecar during smoke",
                category=CutoverFailureCategory.VERIFY,
                evidence=evidence,
            )
    if adapters.path_exists(shm):
        try:
            size = int(adapters.path_identity(shm)["size_bytes"])
        except Exception:  # noqa: BLE001
            size = -1
        evidence["shm_size"] = size
        if size not in ALLOWED_SHM_SIZES:
            _fail(
                "unexpected SHM size during smoke",
                category=CutoverFailureCategory.VERIFY,
                evidence=evidence,
            )
    return evidence


def plan_preflight(opts: CutoverOptions) -> dict[str, Any]:
    """Zero-write planning report. Never creates files/dirs/locks/pause markers."""
    adapters = opts.adapters or FilesystemAdapters(settings=opts.settings)
    settings = opts.settings or load_settings(enable_dotenv=False)
    production = (
        opts.expected_production_path.expanduser()
        if opts.expected_production_path is not None
        else canonical_production_sqlite_path()
    )
    reports = opts.reports_dir or settings.resolved_reports_dir()

    prod_exists = adapters.path_exists(production) and adapters.is_file(production)
    fp = adapters.fingerprint(production) if prod_exists else None
    sidecars = [
        sanitize_path_for_log(p)
        for p in companion_paths(production)
        if adapters.path_exists(p)
    ]
    writers = adapters.list_writers(production if prod_exists else None)
    services = adapters.service_state()
    wal = adapters.wal_state(production) if prod_exists else {}

    backup_parent = (
        opts.backup_dest.parent if opts.backup_dest is not None else Path("/mnt/d")
    )
    staging_parent = (
        opts.staging_dest.parent if opts.staging_dest is not None else production.parent
    )
    try:
        free_mnt = adapters.disk_free(backup_parent)
    except Exception:  # noqa: BLE001
        free_mnt = -1
    try:
        free_root = adapters.disk_free(staging_parent)
    except Exception:  # noqa: BLE001
        free_root = -1

    size = 0
    if prod_exists:
        try:
            size = int(adapters.path_identity(production)["size_bytes"])
        except Exception:  # noqa: BLE001
            size = 0
    compact_need = required_capacity_bytes(size) if size else 0
    topology = validate_planned_cutover_topology(
        mnt_d_free_bytes=max(free_mnt, 0),
        root_free_bytes=max(free_root, 0),
        snapshot_size_bytes=size,
        compact_capacity_required_bytes=compact_need,
        staged_compact_size_bytes=max(size // 2, 1) if size else 0,
    )

    same_fs_ok = False
    if opts.staging_dest is not None and prod_exists:
        try:
            same_fs_ok = adapters.same_fs(production, opts.staging_dest)
        except Exception:  # noqa: BLE001
            same_fs_ok = False

    blockers: list[str] = []
    if not prod_exists:
        blockers.append("production_sqlite_missing")
    if not writers.mail_pause_present or not writers.mirror_pause_present:
        blockers.append("pause_markers_absent")
    if writers.live_writer_pids or any(
        r.classification in {"live", "malformed"} for r in writers.locks
    ):
        blockers.append("active_writers_or_locks")
    if services.api_active or services.health_timer_active:
        blockers.append("api_or_health_still_active")
    if not topology.get("recommended_topology_ok"):
        blockers.append("capacity_topology_fail_closed")
    if opts.staging_dest and not same_fs_ok:
        blockers.append("staging_not_same_filesystem_as_production")
    if REAL_PRODUCTION_APPLY_BLOCKED and not _is_synthetic(opts, adapters):
        blockers.append("real_production_apply_blocked_unguarded_writers")

    journal_expected = None
    if opts.maintenance_id and MAINTENANCE_ID_RE.fullmatch(opts.maintenance_id):
        journal_expected = f"{opts.maintenance_id}.journal.json"

    report = {
        "schema_version": CUTOVER_SCHEMA_VERSION,
        "mode": "sqlite_production_cutover_plan_preflight",
        "apply": False,
        "zero_write_preflight": True,
        "tool": TOOL_NAME,
        "stage": CutoverStage.PLAN_PREFLIGHT.value,
        "next_approved_stage": CutoverStage.PAUSE_WRITERS.value,
        "production_basename": production.name,
        "production_exists": prod_exists,
        "production_fingerprint": fp,
        "sidecars": sidecars,
        "mail_pause_basename": MAIL_PAUSE_FILENAME,
        "mirror_pause_basename": DASHBOARD_PAUSE_FILENAME,
        "pause_paths_relative": [
            f"active/current/{MAIL_PAUSE_FILENAME}",
            f"active/current/{DASHBOARD_PAUSE_FILENAME}",
        ],
        "writers": {
            "mail_pause_present": writers.mail_pause_present,
            "mirror_pause_present": writers.mirror_pause_present,
            "locks": [asdict(r) for r in writers.locks],
            "fd_hit_count": len(writers.fd_hits),
            "unreadable": writers.unreadable,
        },
        "services": asdict(services),
        "wal": wal,
        "capacity": {
            "backup_parent_free_bytes": free_mnt,
            "staging_parent_free_bytes": free_root,
            "compact_capacity_required_bytes": compact_need,
            "topology": {k: v for k, v in topology.items() if k != "notes"},
        },
        "backup_dest_basename": (
            opts.backup_dest.name if opts.backup_dest is not None else None
        ),
        "staging_dest_basename": (
            opts.staging_dest.name if opts.staging_dest is not None else None
        ),
        "same_filesystem_required_for_swap": True,
        "same_filesystem_ok": same_fs_ok,
        "rename_exchange_probe": "deferred_until_apply",
        "estimated_downtime_hours": "4-6",
        "journal_basename": journal_expected,
        "real_production_apply_blocked": REAL_PRODUCTION_APPLY_BLOCKED,
        "unguarded_writer_entry_points": list(UNGUARDED_WRITER_ENTRY_POINTS),
        "writer_entry_points": list(REAL_SQLITE_WRITER_ENTRY_POINTS),
        "blockers": blockers,
        "notes": [
            "Zero-write plan only; no pause markers, journals, locks, backups, or swaps created.",
            "July compact candidate is evidence-only and must never be a cutover source.",
            "Each stage requires a separate --apply invocation.",
            "RPO=0 requires writers stopped from backup through post-swap smoke.",
            REAL_PRODUCTION_APPLY_BLOCK_REASON,
        ],
        "captured_at_utc": _iso_now(),
    }
    return sanitize_evidence(report)


def _expect_journal_stage(journal: CutoverJournal, expected: CutoverStage) -> None:
    if journal.stage != expected.value:
        _fail(
            f"journal stage mismatch: have={journal.stage} need={expected.value}",
            category=CutoverFailureCategory.AMBIGUOUS,
            recovery="Inspect journal; resume only the documented next stage.",
        )


def _match_approved_plan(
    opts: CutoverOptions,
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    production: Path,
    reports: Path,
) -> None:
    if not journal.approved_plan:
        return
    plan = ApprovedPlan.from_dict(journal.approved_plan)
    private = load_private_paths(adapters, journal_path_for(opts, production))
    if private is None:
        _fail(
            "missing private plan paths journal",
            category=CutoverFailureCategory.AMBIGUOUS,
        )
    assert private is not None
    if opts.backup_dest is None or opts.staging_dest is None:
        _fail(
            "approved plan requires --backup-dest and --staging-dest",
            category=CutoverFailureCategory.PREFLIGHT,
        )
    if opts.backup_dest.expanduser().name != plan.backup_dest_basename:
        _fail("backup-dest drift vs approved plan", category=CutoverFailureCategory.SAFETY)
    if opts.staging_dest.expanduser().name != plan.staging_dest_basename:
        _fail("staging-dest drift vs approved plan", category=CutoverFailureCategory.SAFETY)
    if str(opts.backup_dest.expanduser()) != private.backup_dest:
        _fail(
            "backup-dest path drift vs approved private plan",
            category=CutoverFailureCategory.SAFETY,
        )
    if str(opts.staging_dest.expanduser()) != private.staging_dest:
        _fail(
            "staging-dest path drift vs approved private plan",
            category=CutoverFailureCategory.SAFETY,
        )
    if str(reports) != private.reports_dir and str(reports.resolve()) != private.reports_dir:
        # Synthetic may not resolve; compare string forms.
        if str(reports) != private.reports_dir:
            _fail(
                "reports-dir drift vs approved plan",
                category=CutoverFailureCategory.SAFETY,
            )
    if str(production) != private.production_path:
        try:
            if str(production.resolve()) != private.production_path:
                _fail(
                    "production path drift vs approved plan",
                    category=CutoverFailureCategory.SAFETY,
                )
        except OSError:
            _fail(
                "production path drift vs approved plan",
                category=CutoverFailureCategory.SAFETY,
            )


def reconcile_atomic_swap_state(
    adapters: CutoverAdapters,
    *,
    production: Path,
    staging: Path,
    pre_cutover: Path,
    journal: CutoverJournal,
) -> dict[str, Any]:
    """Inspect reality after a crash; never blindly repeat exchange."""
    prod_exists = adapters.path_exists(production) and adapters.is_file(production)
    stage_exists = adapters.path_exists(staging) and adapters.is_file(staging)
    pre_exists = adapters.path_exists(pre_cutover) and adapters.is_file(pre_cutover)
    state = {
        "production_present": prod_exists,
        "staging_present": stage_exists,
        "pre_cutover_present": pre_exists,
        "exchange_completed": journal.exchange_completed,
        "old_production_retained": journal.old_production_retained,
        "recognized": None,
        "safe_action": None,
    }
    intent = journal.swap_intent or {}
    old_fp = intent.get("old_fingerprint")
    new_fp = intent.get("new_fingerprint")

    if not journal.exchange_completed and prod_exists and stage_exists and not pre_exists:
        # Pre-exchange or failed before exchange.
        if (
            old_fp
            and new_fp
            and adapters.fingerprint(production) == old_fp
            and adapters.fingerprint(staging) == new_fp
        ):
            state["recognized"] = "pre_exchange_ready"
            state["safe_action"] = "retry_atomic_swap_from_intent"
        else:
            state["recognized"] = "ambiguous_pre_exchange"
            state["safe_action"] = "manual_inspect"
        return state

    if journal.exchange_completed and not journal.old_production_retained:
        # After exchange: production should hold new; staging holds old.
        if prod_exists and stage_exists and not pre_exists:
            if (
                new_fp
                and old_fp
                and adapters.fingerprint(production) == new_fp
                and adapters.fingerprint(staging) == old_fp
            ):
                state["recognized"] = "exchange_done_retain_pending"
                state["safe_action"] = "retry_pre_cutover_retain_noreplace"
            else:
                state["recognized"] = "ambiguous_post_exchange"
                state["safe_action"] = "manual_inspect"
            return state

    if journal.old_production_retained and prod_exists and pre_exists and not stage_exists:
        if (
            new_fp
            and old_fp
            and adapters.fingerprint(production) == new_fp
            and adapters.fingerprint(pre_cutover) == old_fp
        ):
            state["recognized"] = "swap_complete"
            state["safe_action"] = "continue_readonly_smoke"
            return state

    state["recognized"] = "ambiguous_filesystem_state"
    state["safe_action"] = "manual_inspect_refuse_repeat_exchange"
    return state


def apply_stage(opts: CutoverOptions) -> dict[str, Any]:
    if opts.stage == CutoverStage.PLAN_PREFLIGHT and not opts.apply:
        return plan_preflight(opts)

    if opts.stage == CutoverStage.PLAN_PREFLIGHT and opts.apply:
        _fail(
            "plan_preflight is zero-write; omit --apply",
            category=CutoverFailureCategory.PREFLIGHT,
        )

    if not opts.apply:
        _fail(
            f"stage {opts.stage.value} requires --apply",
            category=CutoverFailureCategory.PREFLIGHT,
        )

    for_swap = opts.stage in {
        CutoverStage.APPROVE_SWAP,
        CutoverStage.ATOMIC_SWAP,
    }
    _require_auth(opts, for_swap=for_swap)

    adapters = opts.adapters or FilesystemAdapters(settings=opts.settings)
    settings = opts.settings or load_settings(enable_dotenv=False)
    assert opts.expected_production_path is not None
    production = opts.expected_production_path.expanduser()
    _refuse_evidence_compact(production)

    if not _is_synthetic(opts, adapters) and REAL_PRODUCTION_APPLY_BLOCKED:
        _fail(
            REAL_PRODUCTION_APPLY_BLOCK_REASON,
            category=CutoverFailureCategory.SAFETY,
            evidence={
                "unguarded": list(UNGUARDED_WRITER_ENTRY_POINTS),
                "entry_points": list(REAL_SQLITE_WRITER_ENTRY_POINTS),
            },
        )

    _require_git_match(opts, adapters)
    fp = _validate_production_identity(opts, adapters, production)
    journal_path = journal_path_for(opts, production)
    _validate_journal_location(adapters, journal_path, production)
    reports = opts.reports_dir or settings.resolved_reports_dir()

    def _inject(phase: str) -> None:
        if opts.fail_after == phase:
            _fail(
                f"injected failure after {phase}",
                category=CutoverFailureCategory.APPLY,
            )

    # Exclusive lock for entire mutating stage (not for plan_preflight).
    with adapters.acquire_exclusive_lock(production, opts.maintenance_id):
        journal = load_journal(adapters, journal_path)

        if journal is None:
            if opts.stage != CutoverStage.PAUSE_WRITERS:
                _fail(
                    "no journal; start with pause_writers after plan_preflight",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            if opts.backup_dest is None or opts.staging_dest is None:
                _fail(
                    "pause_writers requires --backup-dest and --staging-dest "
                    "to seal the approved plan",
                    category=CutoverFailureCategory.PREFLIGHT,
                )
            ident = adapters.path_identity(production)
            backup_parent = adapters.parent_identity(opts.backup_dest.expanduser())
            staging_parent = adapters.parent_identity(opts.staging_dest.expanduser())
            size = int(ident["size_bytes"])
            plan = ApprovedPlan(
                maintenance_id=opts.maintenance_id,
                expected_main_sha=opts.expected_main_sha,
                production_basename=production.name,
                production_fingerprint=fp,
                production_device=int(ident["device"]),
                production_inode=int(ident["inode"]),
                backup_dest_basename=opts.backup_dest.expanduser().name,
                staging_dest_basename=opts.staging_dest.expanduser().name,
                backup_parent_device=int(backup_parent["device"]),
                backup_parent_inode=int(backup_parent["inode"]),
                staging_parent_device=int(staging_parent["device"]),
                staging_parent_inode=int(staging_parent["inode"]),
                reports_active_basename="current",
                mail_pause_basename=MAIL_PAUSE_FILENAME,
                mirror_pause_basename=DASHBOARD_PAUSE_FILENAME,
                capacity_backup_required_bytes=required_capacity_bytes(size),
                capacity_staging_required_bytes=required_capacity_bytes(size),
            )
            journal = CutoverJournal(
                maintenance_id=opts.maintenance_id,
                stage=CutoverStage.PLAN_PREFLIGHT.value,
                expected_main_sha=opts.expected_main_sha,
                expected_production_basename=production.name,
                production_fingerprint=fp,
                production_device=int(ident["device"]),
                production_inode=int(ident["inode"]),
                approved_plan=plan.to_dict(),
                backup_basename=plan.backup_dest_basename,
                staging_basename=plan.staging_dest_basename,
            )
            adapters.mkdir(journal_path.parent)
            write_private_paths(
                adapters,
                journal_path,
                PrivatePlanPaths(
                    production_path=str(production),
                    backup_dest=str(opts.backup_dest.expanduser()),
                    staging_dest=str(opts.staging_dest.expanduser()),
                    reports_dir=str(reports),
                    journal_path=str(journal_path),
                    journal_dir=str(journal_path.parent),
                ),
            )
        else:
            if journal.maintenance_id != opts.maintenance_id:
                _fail(
                    "journal maintenance_id mismatch",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            if journal.expected_main_sha != opts.expected_main_sha:
                _fail(
                    "journal main SHA mismatch",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            if journal.expected_production_basename != production.name:
                _fail(
                    "journal production basename mismatch",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            _match_approved_plan(opts, adapters, journal, production, reports)
            if journal.production_fingerprint and journal.production_fingerprint != fp:
                if opts.stage not in {
                    CutoverStage.ATOMIC_SWAP,
                    CutoverStage.READONLY_SMOKE,
                    CutoverStage.RESUME_SERVICES,
                    CutoverStage.RESUME_WRITERS_MAIL,
                    CutoverStage.RESUME_WRITERS_OBSERVE_MAIL,
                    CutoverStage.RESUME_WRITERS_MIRROR,
                    CutoverStage.RESUME_WRITERS_OBSERVE_MIRROR,
                    CutoverStage.RESUME_WRITERS_COMMIT,
                    CutoverStage.COMPLETED,
                }:
                    _fail(
                        "production fingerprint changed since journal",
                        category=CutoverFailureCategory.AMBIGUOUS,
                    )

        stage = opts.stage
        prev = previous_stage(stage)
        if prev is not None and journal.stage != prev.value:
            if journal.stage != stage.value:
                _fail(
                    f"refusing non-sequential stage: journal={journal.stage} "
                    f"requested={stage.value}",
                    category=CutoverFailureCategory.AMBIGUOUS,
                    recovery=(
                        f"Next safe stage from journal is "
                        f"{next_stage(CutoverStage(journal.stage))}"
                    ),
                )

        if stage == CutoverStage.PAUSE_WRITERS:
            adapters.touch(mail_pause_path(reports))
            adapters.touch(mirror_pause_path(reports))
            writers = adapters.list_writers(production)
            if not writers.mail_pause_present or not writers.mirror_pause_present:
                _fail(
                    "pause markers missing after touch",
                    category=CutoverFailureCategory.APPLY,
                )
            journal.stage = stage.value
            journal.notes.append("writers_paused")
            write_journal(adapters, journal_path, journal)
            _inject("pause_writers")
            return _stage_report(opts, journal, {"writers_paused": True})

        if stage == CutoverStage.STOP_READERS:
            _expect_journal_stage(journal, CutoverStage.PAUSE_WRITERS)
            adapters.stop_health_timer()
            adapters.stop_api()
            services = adapters.service_state()
            if services.api_active or services.health_timer_active:
                _fail(
                    "services still active after stop",
                    category=CutoverFailureCategory.APPLY,
                )
            journal.services_stopped = True
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("stop_readers")
            return _stage_report(opts, journal, {"services": asdict(services)})

        if stage == CutoverStage.QUIESCE_WAL:
            _expect_journal_stage(journal, CutoverStage.STOP_READERS)
            _assert_writers_quiesced(adapters, production)
            services = adapters.service_state()
            if services.api_active or services.health_timer_active:
                _fail(
                    "API/health must be stopped before WAL quiesce",
                    category=CutoverFailureCategory.SAFETY,
                )
            before = adapters.wal_state(production)
            result = adapters.checkpoint_wal(production)
            after = adapters.wal_state(production)
            if after.get("wal_size", 0) > 0:
                _fail(
                    "WAL grew or remained non-empty",
                    category=CutoverFailureCategory.VERIFY,
                )
            # Recheck barriers after checkpoint.
            _assert_writers_quiesced(adapters, production)
            services = adapters.service_state()
            if services.api_active or services.health_timer_active:
                _fail(
                    "services reappeared after checkpoint",
                    category=CutoverFailureCategory.SAFETY,
                )
            journal.wal_quiesced = True
            journal.production_fingerprint = adapters.fingerprint(production)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("quiesce_wal")
            return _stage_report(
                opts,
                journal,
                {"wal_before": before, "wal_after": after, "checkpoint": result},
            )

        if stage == CutoverStage.CREATE_CURRENT_BACKUP:
            _expect_journal_stage(journal, CutoverStage.QUIESCE_WAL)
            _assert_writers_quiesced(adapters, production)
            if opts.backup_dest is None:
                _fail("--backup-dest required", category=CutoverFailureCategory.PREFLIGHT)
            dest = opts.backup_dest.expanduser()
            _refuse_evidence_compact(dest)
            if adapters.path_exists(dest):
                _fail(
                    "backup dest exists (no-clobber)",
                    category=CutoverFailureCategory.APPLY,
                )
            # Separate filesystem from production required for real topology;
            # synthetic may share FS.
            if not _is_synthetic(opts, adapters) and adapters.same_fs(production, dest):
                _fail(
                    "backup dest must be on a separate filesystem from production",
                    category=CutoverFailureCategory.SAFETY,
                )
            result = adapters.create_online_backup(production, dest)
            if not result.get("completed") or not result.get("manifest_completed"):
                _fail(
                    "refusing to advance: backup incomplete",
                    category=CutoverFailureCategory.VERIFY,
                )
            _assert_writers_quiesced(adapters, production)
            journal.backup_basename = dest.name
            journal.backup_fingerprint = result["destination_fingerprint"]
            journal.backup_source_fingerprint_before = result[
                "source_fingerprint_before"
            ]
            journal.backup_source_fingerprint_after = result[
                "source_fingerprint_after"
            ]
            journal.backup_verified = True
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("create_current_backup")
            return _stage_report(opts, journal, {"backup": result})

        if stage == CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING:
            _expect_journal_stage(journal, CutoverStage.CREATE_CURRENT_BACKUP)
            _assert_writers_quiesced(adapters, production)
            if opts.backup_dest is None or opts.staging_dest is None:
                _fail(
                    "--backup-dest and --staging-dest required",
                    category=CutoverFailureCategory.PREFLIGHT,
                )
            source = opts.backup_dest.expanduser()
            dest = opts.staging_dest.expanduser()
            _refuse_evidence_compact(source)
            _refuse_evidence_compact(dest)
            if not adapters.same_fs(production, dest):
                _fail(
                    "staging must be on the same filesystem as production",
                    category=CutoverFailureCategory.SAFETY,
                )
            if adapters.path_exists(dest):
                _fail(
                    "staging dest exists (no-clobber)",
                    category=CutoverFailureCategory.APPLY,
                )
            if not journal.backup_verified or not journal.backup_fingerprint:
                _fail(
                    "backup not verified in journal",
                    category=CutoverFailureCategory.SAFETY,
                )
            if adapters.fingerprint(source) != journal.backup_fingerprint:
                _fail(
                    "backup fingerprint drift before compact",
                    category=CutoverFailureCategory.VERIFY,
                )
            result = adapters.compact_offline(source, dest)
            if not result.get("completed") or not result.get("manifest_completed"):
                _fail(
                    "refusing to advance: compaction incomplete",
                    category=CutoverFailureCategory.VERIFY,
                )
            journal.staging_basename = dest.name
            journal.compact_source_fingerprint = result["source_fingerprint"]
            journal.compact_dest_fingerprint = result["destination_fingerprint"]
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("compact_to_production_fs_staging")
            return _stage_report(opts, journal, {"compact": result})

        if stage == CutoverStage.VERIFY_CANDIDATE:
            _expect_journal_stage(
                journal, CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING
            )
            _assert_writers_quiesced(adapters, production)
            if opts.staging_dest is None:
                _fail("--staging-dest required", category=CutoverFailureCategory.PREFLIGHT)
            dest = opts.staging_dest.expanduser()
            _refuse_evidence_compact(dest)
            result = adapters.verify_candidate(
                dest, backup_fingerprint=journal.backup_fingerprint
            )
            if journal.compact_dest_fingerprint and result.get("fingerprint") != (
                journal.compact_dest_fingerprint
            ):
                _fail(
                    "candidate fingerprint disagrees with compact journal binding",
                    category=CutoverFailureCategory.VERIFY,
                )
            journal.candidate_fingerprint = result.get("fingerprint")
            journal.compact_verified = True
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("verify_candidate")
            return _stage_report(opts, journal, {"verify": result})

        if stage == CutoverStage.APPROVE_SWAP:
            _expect_journal_stage(journal, CutoverStage.VERIFY_CANDIDATE)
            if not journal.compact_verified or not journal.candidate_fingerprint:
                _fail("candidate not verified", category=CutoverFailureCategory.SAFETY)
            journal.swap_approved = True
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("approve_swap")
            return _stage_report(opts, journal, {"swap_approved": True})

        if stage == CutoverStage.ATOMIC_SWAP:
            return _run_atomic_swap(
                opts, adapters, journal, journal_path, production, _inject
            )

        if stage == CutoverStage.READONLY_SMOKE:
            _expect_journal_stage(journal, CutoverStage.ATOMIC_SWAP)
            if not journal.old_production_retained:
                _fail(
                    "swap retention incomplete; refuse smoke",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            adapters.start_api()
            # Health timer must remain stopped.
            if adapters.service_state().health_timer_active:
                adapters.stop_health_timer()
            if adapters.service_state().health_timer_active:
                _fail(
                    "health timer active during readonly smoke",
                    category=CutoverFailureCategory.SAFETY,
                )
            live_fp = adapters.fingerprint(production)
            if live_fp != journal.production_fingerprint:
                _fail(
                    "production fingerprint mismatch during smoke",
                    category=CutoverFailureCategory.VERIFY,
                )
            smoke = adapters.http_smoke(
                opts.api_base_url, expected_fingerprint=live_fp
            )
            _assert_sidecars_smoke_policy(adapters, production)
            journal.smoke_ok = True
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("readonly_smoke")
            return _stage_report(opts, journal, {"smoke": smoke})

        if stage == CutoverStage.RESUME_SERVICES:
            _expect_journal_stage(journal, CutoverStage.READONLY_SMOKE)
            if not journal.smoke_ok:
                _fail("smoke not ok", category=CutoverFailureCategory.SAFETY)
            if not adapters.service_state().api_active:
                adapters.start_api()
            adapters.start_health_timer()
            journal.services_stopped = False
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_services")
            return _stage_report(
                opts, journal, {"services": asdict(adapters.service_state())}
            )

        if stage == CutoverStage.RESUME_WRITERS_MAIL:
            _expect_journal_stage(journal, CutoverStage.RESUME_SERVICES)
            # Point of no return BEFORE removing first pause marker.
            journal.writer_resume_started = True
            write_journal(adapters, journal_path, journal)
            _inject("writer_resume_started")
            path = mail_pause_path(reports)
            if adapters.path_exists(path):
                adapters.unlink(path)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_mail")
            return _stage_report(opts, journal, {"mail_pause_removed": True})

        if stage == CutoverStage.RESUME_WRITERS_OBSERVE_MAIL:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_MAIL)
            live_fp = adapters.fingerprint(production)
            if live_fp != journal.production_fingerprint:
                _fail(
                    "fingerprint drift after mail unpause",
                    category=CutoverFailureCategory.VERIFY,
                )
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_observe_mail")
            return _stage_report(opts, journal, {"observed_fingerprint": live_fp})

        if stage == CutoverStage.RESUME_WRITERS_MIRROR:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_OBSERVE_MAIL)
            if not journal.writer_resume_started:
                _fail(
                    "writer_resume_started missing",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            path = mirror_pause_path(reports)
            if adapters.path_exists(path):
                adapters.unlink(path)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_mirror")
            return _stage_report(opts, journal, {"mirror_pause_removed": True})

        if stage == CutoverStage.RESUME_WRITERS_OBSERVE_MIRROR:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_MIRROR)
            live_fp = adapters.fingerprint(production)
            if live_fp != journal.production_fingerprint:
                _fail(
                    "fingerprint drift after mirror unpause",
                    category=CutoverFailureCategory.VERIFY,
                )
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_observe_mirror")
            return _stage_report(opts, journal, {"observed_fingerprint": live_fp})

        if stage == CutoverStage.RESUME_WRITERS_COMMIT:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_OBSERVE_MIRROR)
            journal.writers_resumed = True
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_commit")
            return _stage_report(opts, journal, {"writers_resumed": True})

        if stage == CutoverStage.COMPLETED:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_COMMIT)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            return _stage_report(opts, journal, {"completed": True})

        _fail(f"unsupported stage {stage.value}", category=CutoverFailureCategory.PREFLIGHT)
        raise AssertionError("unreachable")


def _run_atomic_swap(
    opts: CutoverOptions,
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
    production: Path,
    inject: Callable[[str], None],
) -> dict[str, Any]:
    # Allow idempotent re-entry when journal already at atomic_swap (crash resume).
    if journal.stage == CutoverStage.ATOMIC_SWAP.value:
        if not journal.swap_approved:
            _fail("swap not approved", category=CutoverFailureCategory.SAFETY)
    else:
        _expect_journal_stage(journal, CutoverStage.APPROVE_SWAP)
    if not journal.swap_approved:
        _fail("swap not approved", category=CutoverFailureCategory.SAFETY)
    if journal.writers_resumed or journal.writer_resume_started:
        _fail(
            "refusing swap after writer resume started",
            category=CutoverFailureCategory.SAFETY,
        )
    if opts.staging_dest is None:
        _fail("--staging-dest required", category=CutoverFailureCategory.PREFLIGHT)
    staged = opts.staging_dest.expanduser()
    if not adapters.same_fs(production, staged):
        _fail(
            "cross-filesystem swap refused",
            category=CutoverFailureCategory.SAFETY,
        )
    if not adapters.probe_rename_exchange(production.parent):
        _fail(
            "renameat2(RENAME_EXCHANGE) unsupported; refusing dual-mv fallback",
            category=CutoverFailureCategory.SAFETY,
            recovery=(
                "Do not use two ordinary mv commands. A separately reviewed "
                "two-phase protocol is not enabled in this orchestrator."
            ),
        )
    _assert_writers_quiesced(adapters, production)
    services = adapters.service_state()
    if services.api_active or services.health_timer_active:
        _fail(
            "API/health must be stopped before swap",
            category=CutoverFailureCategory.SAFETY,
        )

    pre_name = f"{production.name}.pre_cutover.{opts.maintenance_id}"
    pre_path = production.with_name(pre_name)

    # Idempotent resume via reconciliation if prior crash / completed swap.
    if journal.exchange_completed or journal.swap_intent or journal.stage == (
        CutoverStage.ATOMIC_SWAP.value
    ):
        recon = reconcile_atomic_swap_state(
            adapters,
            production=production,
            staging=staged,
            pre_cutover=pre_path,
            journal=journal,
        )
        if recon["recognized"] == "swap_complete":
            journal.stage = CutoverStage.ATOMIC_SWAP.value
            write_journal(adapters, journal_path, journal)
            return _stage_report(opts, journal, {"reconciled": recon})
        if recon["recognized"] == "exchange_done_retain_pending":
            inject("before_retain")
            adapters.rename_noreplace(staged, pre_path)
            adapters.fsync_dir(production.parent)
            journal.old_production_retained = True
            journal.pre_cutover_basename = pre_path.name
            journal.production_fingerprint = adapters.fingerprint(production)
            journal.swap_direction = "staging_to_production_via_rename_exchange"
            journal.stage = CutoverStage.ATOMIC_SWAP.value
            write_journal(adapters, journal_path, journal)
            inject("after_retain")
            return _stage_report(
                opts,
                journal,
                {"reconciled": recon, "pre_cutover_basename": pre_path.name},
            )
        if recon["recognized"] not in {"pre_exchange_ready", None}:
            # None only when first entry without intent — fall through.
            if recon["recognized"] is not None:
                _fail(
                    f"ambiguous swap state: {recon['recognized']}",
                    category=CutoverFailureCategory.AMBIGUOUS,
                    evidence=recon,
                    recovery=str(recon.get("safe_action")),
                )

    if adapters.path_exists(pre_path):
        _fail(
            "pre_cutover artifact exists (no-clobber)",
            category=CutoverFailureCategory.APPLY,
        )

    old_fp = adapters.fingerprint(production)
    new_fp = adapters.fingerprint(staged)
    if journal.candidate_fingerprint and new_fp != journal.candidate_fingerprint:
        _fail(
            "staging fingerprint drift before swap",
            category=CutoverFailureCategory.VERIFY,
        )

    journal.swap_intent = {
        "old_fingerprint": old_fp,
        "new_fingerprint": new_fp,
        "production_basename": production.name,
        "staging_basename": staged.name,
        "pre_cutover_basename": pre_name,
    }
    write_journal(adapters, journal_path, journal)
    inject("swap_intent")
    inject("before_exchange")

    adapters.rename_exchange(production, staged)
    inject("after_exchange")
    adapters.fsync_dir(production.parent)
    inject("after_dir_fsync")
    journal.exchange_completed = True
    write_journal(adapters, journal_path, journal)
    inject("exchange_completed")

    inject("before_retain")
    adapters.rename_noreplace(staged, pre_path)
    adapters.fsync_dir(production.parent)
    inject("after_retain")
    journal.old_production_retained = True
    journal.pre_cutover_basename = pre_path.name
    journal.production_fingerprint = adapters.fingerprint(production)
    journal.swap_direction = "staging_to_production_via_rename_exchange"
    journal.stage = CutoverStage.ATOMIC_SWAP.value
    journal.notes.append(f"old_fp_before_swap={old_fp}")
    write_journal(adapters, journal_path, journal)
    inject("atomic_swap")
    return _stage_report(
        opts,
        journal,
        {
            "pre_cutover_basename": pre_path.name,
            "new_production_fingerprint": journal.production_fingerprint,
            "retained_old_production": True,
        },
    )


def _stage_report(
    opts: CutoverOptions, journal: CutoverJournal, extra: dict[str, Any]
) -> dict[str, Any]:
    nxt = next_stage(opts.stage)
    report = {
        "schema_version": CUTOVER_SCHEMA_VERSION,
        "mode": "sqlite_production_cutover_stage",
        "apply": True,
        "stage": opts.stage.value,
        "next_stage": nxt.value if nxt else None,
        "maintenance_id": opts.maintenance_id,
        "journal": sanitize_evidence(journal.to_dict()),
        "captured_at_utc": _iso_now(),
        **extra,
    }
    return sanitize_evidence(report)


def attempt_rollback_before_writers(
    opts: CutoverOptions,
    *,
    pre_cutover_path: Path | None = None,
    expected_old_fingerprint: str,
    expected_new_fingerprint: str,
) -> dict[str, Any]:
    """Verified atomic rollback only if writer resume has not started."""
    _require_auth(opts, for_swap=True)
    adapters = opts.adapters or FilesystemAdapters(settings=opts.settings)
    assert opts.expected_production_path is not None
    production = opts.expected_production_path.expanduser()

    if not _is_synthetic(opts, adapters) and REAL_PRODUCTION_APPLY_BLOCKED:
        _fail(
            REAL_PRODUCTION_APPLY_BLOCK_REASON,
            category=CutoverFailureCategory.SAFETY,
        )

    journal_path = journal_path_for(opts, production)
    with adapters.acquire_exclusive_lock(production, opts.maintenance_id):
        journal = load_journal(adapters, journal_path)
        if journal is None:
            _fail(
                "missing journal for rollback",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        assert journal is not None
        if journal.writer_resume_started or journal.writers_resumed:
            _fail(
                "automatic rollback refused after writer_resume_started; "
                "use incident reconciliation",
                category=CutoverFailureCategory.SAFETY,
            )
        if not journal.pre_cutover_basename:
            _fail(
                "journal missing pre_cutover_basename",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        approved_pre = production.with_name(journal.pre_cutover_basename)
        if pre_cutover_path is not None:
            if pre_cutover_path.expanduser().name != journal.pre_cutover_basename:
                _fail(
                    "refusing arbitrary --pre-cutover-path; must match journal basename",
                    category=CutoverFailureCategory.SAFETY,
                )
            if str(pre_cutover_path.expanduser()) != str(approved_pre):
                try:
                    if (
                        pre_cutover_path.expanduser().resolve()
                        != approved_pre.resolve()
                    ):
                        _fail(
                            "refusing arbitrary --pre-cutover-path; must match journal path",
                            category=CutoverFailureCategory.SAFETY,
                        )
                except OSError:
                    _fail(
                        "refusing arbitrary --pre-cutover-path",
                        category=CutoverFailureCategory.SAFETY,
                    )
        pre = approved_pre

        _assert_writers_quiesced(adapters, production)
        services = adapters.service_state()
        if services.api_active or services.health_timer_active:
            _fail(
                "API/health must be stopped before rollback",
                category=CutoverFailureCategory.SAFETY,
            )
        for side in companion_paths(production) + companion_paths(pre):
            if adapters.path_exists(side):
                # Allow zero-length wal/shm only on production? Fail any journal.
                if str(side).endswith("-journal"):
                    _fail(
                        "unexpected journal sidecar before rollback",
                        category=CutoverFailureCategory.VERIFY,
                    )

        if adapters.fingerprint(production) != expected_new_fingerprint:
            _fail(
                "current production fingerprint mismatch for rollback",
                category=CutoverFailureCategory.VERIFY,
            )
        if adapters.fingerprint(pre) != expected_old_fingerprint:
            _fail(
                "pre_cutover fingerprint mismatch for rollback",
                category=CutoverFailureCategory.VERIFY,
            )
        if not adapters.same_fs(production, pre):
            _fail(
                "rollback requires same filesystem",
                category=CutoverFailureCategory.SAFETY,
            )
        if not adapters.probe_rename_exchange(production.parent):
            _fail(
                "rename exchange unsupported for rollback",
                category=CutoverFailureCategory.SAFETY,
            )

        journal.rollback_intent = {
            "old_fingerprint": expected_old_fingerprint,
            "new_fingerprint": expected_new_fingerprint,
            "pre_cutover_basename": pre.name,
        }
        write_journal(adapters, journal_path, journal)

        adapters.rename_exchange(production, pre)
        adapters.fsync_dir(production.parent)
        journal.swap_direction = "rollback_pre_cutover_to_production"
        journal.production_fingerprint = adapters.fingerprint(production)
        journal.smoke_ok = False
        journal.exchange_completed = True
        journal.old_production_retained = True
        journal.stage = CutoverStage.ATOMIC_SWAP.value
        journal.notes.append("rolled_back_before_writers_resumed")
        write_journal(adapters, journal_path, journal)
        return sanitize_evidence(
            {
                "rolled_back": True,
                "production_fingerprint": journal.production_fingerprint,
                "writer_resume_started": False,
                "writers_resumed": False,
                "next_required": CutoverStage.READONLY_SMOKE.value,
            }
        )


# --- Synthetic world for tests -------------------------------------------------


@dataclass
class SyntheticWorld:
    """In-memory/fake FS cutover world for exhaustive synthetic tests."""

    root: Path
    files: dict[str, bytes] = field(default_factory=dict)
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    services: ServiceState = field(default_factory=ServiceState)
    lock_records: list[LockRecord] = field(default_factory=list)
    fd_hits: list[dict[str, Any]] = field(default_factory=list)
    mail_pause: bool = False
    mirror_pause: bool = False
    wal_size: int = 0
    shm_size: int = 0
    journal_present: bool = False
    rename_exchange_ok: bool = True
    head_sha: str = "25cd4100e226427b3a4d027f1ee3b3af056884d4"
    branch: str = "feat/sqlite-production-cutover-orchestrator"
    clean_worktree: bool = True
    local_main_sha: str = "25cd4100e226427b3a4d027f1ee3b3af056884d4"
    origin_main_sha: str = "25cd4100e226427b3a4d027f1ee3b3af056884d4"
    smoke_payload: dict[str, Any] = field(
        default_factory=lambda: {
            "status": "ok",
            "sqlite_query_only": True,
            "production_fingerprint": None,
        }
    )
    fail_checkpoint: bool = False
    checkpoint_busy: int = 0
    backup_incomplete: bool = False
    compact_incomplete: bool = False
    lock_held: bool = False
    _lock_owner: int | None = None
    same_fs_pairs: set[tuple[str, str]] | None = None
    free_bytes: int = 300 * 1024**3

    def key(self, path: Path) -> str:
        return str(path)

    def path_exists(self, path: Path) -> bool:
        return self.key(path) in self.files

    def is_symlink(self, path: Path) -> bool:
        return False

    def is_file(self, path: Path) -> bool:
        return self.key(path) in self.files

    def is_dir(self, path: Path) -> bool:
        return False

    def read_text(self, path: Path) -> str:
        return self.files[self.key(path)].decode("utf-8")

    def write_text_atomic(self, path: Path, text: str) -> None:
        self.files[self.key(path)] = text.encode("utf-8")

    def touch(self, path: Path) -> None:
        self.files[self.key(path)] = b""
        if path.name == MAIL_PAUSE_FILENAME:
            self.mail_pause = True
        if path.name == DASHBOARD_PAUSE_FILENAME:
            self.mirror_pause = True

    def unlink(self, path: Path) -> None:
        self.files.pop(self.key(path), None)
        if path.name == MAIL_PAUSE_FILENAME:
            self.mail_pause = False
        if path.name == DASHBOARD_PAUSE_FILENAME:
            self.mirror_pause = False

    def mkdir(self, path: Path) -> None:
        return

    def disk_free(self, path: Path) -> int:
        return self.free_bytes

    def same_fs(self, a: Path, b: Path) -> bool:
        if self.same_fs_pairs is not None:
            return (self.key(a), self.key(b)) in self.same_fs_pairs or (
                self.key(b),
                self.key(a),
            ) in self.same_fs_pairs
        return True

    def fingerprint(self, path: Path) -> str:
        data = self.files.get(self.key(path), b"")
        digest = hashlib.sha256(data).hexdigest()[:16]
        return f"{len(data)}:0:1:{digest}"

    def path_identity(self, path: Path) -> dict[str, Any]:
        data = self.files.get(self.key(path), b"")
        digest = int(hashlib.sha256(data).hexdigest()[:8], 16)
        return {
            "basename": path.name,
            "size_bytes": len(data),
            "mtime_ns": 0,
            "device": 1,
            "inode": digest or 1,
        }

    def parent_identity(self, path: Path) -> dict[str, Any]:
        parent = path.parent
        return {"basename": parent.name, "device": 1, "inode": 99}

    def list_writers(self, production: Path | None = None) -> WriterInventory:
        return WriterInventory(
            mail_pause_present=self.mail_pause,
            mirror_pause_present=self.mirror_pause,
            locks=list(self.lock_records),
            fd_hits=list(self.fd_hits),
            unreadable=[],
        )

    def service_state(self) -> ServiceState:
        return ServiceState(
            api_active=self.services.api_active,
            health_timer_active=self.services.health_timer_active,
        )

    def stop_api(self) -> None:
        self.services.api_active = False

    def start_api(self) -> None:
        self.services.api_active = True

    def stop_health_timer(self) -> None:
        self.services.health_timer_active = False

    def start_health_timer(self) -> None:
        self.services.health_timer_active = True

    def wal_state(self, db: Path) -> dict[str, Any]:
        return {
            "wal_present": self.wal_size > 0,
            "wal_size": self.wal_size,
            "shm_present": self.shm_size > 0,
            "shm_size": self.shm_size,
            "journal_present": self.journal_present,
            "db_fingerprint": self.fingerprint(db) if self.is_file(db) else None,
        }

    def checkpoint_wal(self, db: Path) -> dict[str, Any]:
        if self.fail_checkpoint:
            _fail("checkpoint failed", category=CutoverFailureCategory.VERIFY)
        if self.checkpoint_busy != 0:
            _fail(
                f"wal_checkpoint busy={self.checkpoint_busy}",
                category=CutoverFailureCategory.VERIFY,
            )
        before = self.wal_state(db)
        self.wal_size = 0
        return {
            "before": before,
            "after": self.wal_state(db),
            "checkpoint": {
                "busy": 0,
                "log": 0,
                "checkpointed": 0,
                "mode": "TRUNCATE",
            },
        }

    def create_online_backup(self, source: Path, dest: Path) -> dict[str, Any]:
        if self.path_exists(dest):
            _fail("backup exists", category=CutoverFailureCategory.APPLY)
        if self.backup_incomplete:
            return {"completed": False, "manifest_completed": False}
        before = self.fingerprint(source)
        self.files[self.key(dest)] = self.files[self.key(source)]
        # Write synthetic completed manifest
        man = dest.with_name(dest.name + ".manifest.json")
        self.files[self.key(man)] = json.dumps({"completed": True}).encode()
        after = self.fingerprint(source)
        return {
            "completed": True,
            "destination_basename": dest.name,
            "destination_fingerprint": self.fingerprint(dest),
            "source_fingerprint_before": before,
            "source_fingerprint_after": after,
            "manifest_completed": True,
            "method": "synthetic",
        }

    def compact_offline(self, source: Path, dest: Path) -> dict[str, Any]:
        if self.path_exists(dest):
            _fail("staging exists", category=CutoverFailureCategory.APPLY)
        if self.compact_incomplete:
            return {"completed": False, "manifest_completed": False}
        src_fp = self.fingerprint(source)
        self.files[self.key(dest)] = b"COMPACT:" + self.files[self.key(source)]
        man = dest.with_name(dest.name + ".compaction.manifest.json")
        self.files[self.key(man)] = json.dumps(
            {
                "completed": True,
                "source_basename": source.name,
                "method": "VACUUM INTO",
            }
        ).encode()
        return {
            "completed": True,
            "destination_basename": dest.name,
            "destination_fingerprint": self.fingerprint(dest),
            "source_fingerprint": src_fp,
            "manifest_completed": True,
            "method": "VACUUM INTO",
        }

    def verify_candidate(
        self, path: Path, *, backup_fingerprint: str | None = None
    ) -> dict[str, Any]:
        if not self.is_file(path):
            _fail("candidate missing", category=CutoverFailureCategory.VERIFY)
        _refuse_evidence_compact(path)
        man = path.with_name(path.name + ".compaction.manifest.json")
        if not self.is_file(man):
            _fail("compaction manifest missing", category=CutoverFailureCategory.VERIFY)
        payload = json.loads(self.read_text(man))
        if payload.get("completed") is not True:
            _fail(
                "compaction manifest incomplete",
                category=CutoverFailureCategory.VERIFY,
            )
        return {
            "quick_check_ok": True,
            "foreign_key_violations": 0,
            "schema_fingerprint": "synth",
            "database_identity": {
                "page_size": 4096,
                "encoding": "UTF-8",
                "application_id": 0,
                "user_version": 0,
            },
            "critical_table_counts": {"emails": 1},
            "storage": {
                "page_count": 1,
                "page_size": 4096,
                "freelist_count": 0,
                "allocated_bytes": 4096,
            },
            "fingerprint": self.fingerprint(path),
            "basename": path.name,
            "manifest_completed": True,
            "source_basename": payload.get("source_basename"),
        }

    def probe_rename_exchange(self, directory: Path) -> bool:
        return self.rename_exchange_ok

    def rename_exchange(self, a: Path, b: Path) -> None:
        if not self.rename_exchange_ok:
            raise OSError(errno.ENOSYS, "exchange unsupported")
        ka, kb = self.key(a), self.key(b)
        self.files[ka], self.files[kb] = self.files[kb], self.files[ka]

    def rename_noreplace(self, src: Path, dest: Path) -> None:
        if self.path_exists(dest):
            raise OSError(errno.EEXIST, "RENAME_NOREPLACE dest exists")
        data = self.files.pop(self.key(src))
        self.files[self.key(dest)] = data

    def http_smoke(self, base_url: str, *, expected_fingerprint: str) -> dict[str, Any]:
        payload = dict(self.smoke_payload)
        payload["production_fingerprint"] = expected_fingerprint
        if payload.get("status") != "ok":
            _fail("health semantic check failed", category=CutoverFailureCategory.VERIFY)
        return {
            "/health": {"ok": True, "status": "ok"},
            "/operator/status": {"ok": True},
            "/operator/automation-status": {"ok": True},
            "expected_fingerprint": expected_fingerprint,
            "reported_fingerprint": expected_fingerprint,
        }

    def git_identity(self) -> GitIdentity:
        return GitIdentity(
            head_sha=self.head_sha,
            branch=self.branch,
            clean_worktree=self.clean_worktree,
            local_main_sha=self.local_main_sha,
            origin_main_sha=self.origin_main_sha,
        )

    @contextmanager
    def acquire_exclusive_lock(
        self, production: Path, maintenance_id: str
    ) -> Generator[None, None, None]:
        if self.lock_held:
            _fail(
                "cutover lock contention (Busy)",
                category=CutoverFailureCategory.SAFETY,
            )
        self.lock_held = True
        self._lock_owner = os.getpid()
        try:
            yield None
        finally:
            self.lock_held = False
            self._lock_owner = None

    def fsync_dir(self, path: Path) -> None:
        return


def tree_snapshot(root: Path) -> dict[str, tuple[int, int] | str]:
    if not root.exists():
        return {}
    out: dict[str, tuple[int, int] | str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = "symlink"
        elif path.is_dir():
            out[rel] = "dir"
        elif path.is_file():
            st = path.stat()
            out[rel] = (int(st.st_size), int(st.st_mtime_ns))
        else:
            out[rel] = "other"
    return out
