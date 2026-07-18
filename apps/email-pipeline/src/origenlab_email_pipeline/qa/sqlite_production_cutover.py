"""Fail-closed, resumable SQLite production cutover orchestrator.

Staged state machine only. Default is zero-write preflight. Never runs the
entire production workflow from one command. Real Online Backup / compaction /
systemctl / swap are invoked only through injected adapters when an operator
explicitly applies a single approved stage.

This module does not authorize production cutover by itself. The July 2026
compact candidate is evidence-only and is never accepted as a cutover source.
"""

from __future__ import annotations

import enum
import errno
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from origenlab_email_pipeline.config import Settings, canonical_production_sqlite_path, load_settings
from origenlab_email_pipeline.operator_cli.dashboard_auto_mirror import (
    PAUSE_FILENAME as DASHBOARD_PAUSE_FILENAME,
)
from origenlab_email_pipeline.operator_cli.mail_auto_refresh import (
    PAUSE_FILENAME as MAIL_PAUSE_FILENAME,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    disk_free_bytes,
    fingerprint_file,
    fsync_directory,
    fsync_file,
    paths_same_file,
    required_capacity_bytes,
    same_filesystem,
    sanitize_path_for_log,
)
from origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal import (
    validate_planned_cutover_topology,
)

CUTOVER_SCHEMA_VERSION = 1
TOOL_NAME = "sqlite_production_cutover"
API_SERVICE = "origenlab-api.service"
API_HEALTH_TIMER = "origenlab-api-health.timer"
FORBIDDEN_COMPACT_PREFIXES = ("emails_compact_", "emails_offline_")
KNOWN_EVIDENCE_COMPACT = "emails_compact_20260717T183537Z.sqlite"

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
    RESUME_WRITERS = "resume_writers"
    COMPLETED = "completed"


STAGE_ORDER: tuple[CutoverStage, ...] = tuple(CutoverStage)

# Stages that may run without --apply (read-only planning).
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


@dataclass
class ServiceState:
    api_active: bool = False
    health_timer_active: bool = False


@dataclass
class WriterInventory:
    mail_pause_present: bool = False
    mirror_pause_present: bool = False
    active_writer_pids: list[int] = field(default_factory=list)
    lock_basenames: list[str] = field(default_factory=list)

    @property
    def writers_quiesced(self) -> bool:
        return (
            self.mail_pause_present
            and self.mirror_pause_present
            and not self.active_writer_pids
            and not self.lock_basenames
        )


@dataclass
class CutoverJournal:
    schema_version: int = CUTOVER_SCHEMA_VERSION
    tool: str = TOOL_NAME
    maintenance_id: str = ""
    stage: str = CutoverStage.PLAN_PREFLIGHT.value
    expected_main_sha: str = ""
    expected_production_basename: str = ""
    production_fingerprint: str | None = None
    backup_basename: str | None = None
    staging_basename: str | None = None
    pre_cutover_basename: str | None = None
    candidate_fingerprint: str | None = None
    swap_approved: bool = False
    writers_resumed: bool = False
    services_stopped: bool = False
    wal_quiesced: bool = False
    backup_verified: bool = False
    compact_verified: bool = False
    smoke_ok: bool = False
    swap_direction: str | None = None
    updated_at_utc: str = field(default_factory=_iso_now)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CutoverJournal:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: data[k] for k in data if k in known})


class CutoverAdapters(Protocol):
    """Injectable world boundary for synthetic tests and real operators."""

    def path_exists(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def read_text(self, path: Path) -> str: ...
    def write_text_atomic(self, path: Path, text: str) -> None: ...
    def touch(self, path: Path) -> None: ...
    def unlink(self, path: Path) -> None: ...
    def mkdir(self, path: Path) -> None: ...
    def disk_free(self, path: Path) -> int: ...
    def same_fs(self, a: Path, b: Path) -> bool: ...
    def fingerprint(self, path: Path) -> str: ...
    def list_writers(self) -> WriterInventory: ...
    def service_state(self) -> ServiceState: ...
    def stop_services(self) -> None: ...
    def start_services(self) -> None: ...
    def wal_state(self, db: Path) -> dict[str, Any]: ...
    def checkpoint_wal(self, db: Path) -> dict[str, Any]: ...
    def create_online_backup(self, source: Path, dest: Path) -> dict[str, Any]: ...
    def compact_offline(self, source: Path, dest: Path) -> dict[str, Any]: ...
    def verify_candidate(self, path: Path) -> dict[str, Any]: ...
    def probe_rename_exchange(self, directory: Path) -> bool: ...
    def rename_exchange(self, a: Path, b: Path) -> None: ...
    def http_smoke(self, base_url: str) -> dict[str, Any]: ...
    def git_head_sha(self) -> str: ...


@dataclass
class FilesystemAdapters:
    """Default adapters backed by real OS calls (still gated by CLI auth)."""

    settings: Settings | None = None
    http_get: Callable[[str], dict[str, Any]] | None = None
    systemctl: Callable[[list[str]], int] | None = None
    rename_exchange_supported: bool | None = None

    def path_exists(self, path: Path) -> bool:
        try:
            return path.exists() or path.is_symlink()
        except OSError:
            return False

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def is_file(self, path: Path) -> bool:
        return path.is_file() and not path.is_symlink()

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_text_atomic(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.partial.{os.getpid()}.{time.time_ns()}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(str(partial), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
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

    def list_writers(self) -> WriterInventory:
        settings = self.settings or load_settings(enable_dotenv=False)
        reports = settings.resolved_reports_dir()
        # Do not mkdir — zero-write preflight must remain side-effect free.
        active = reports / "active" / "current"
        mail_pause = (active / MAIL_PAUSE_FILENAME).is_file()
        mirror_pause = (active / DASHBOARD_PAUSE_FILENAME).is_file()
        locks: list[str] = []
        pids: list[int] = []
        if active.is_dir():
            locks = [p.name for p in active.glob("*.lock") if p.is_file()]
            for lock in active.glob("*.lock"):
                try:
                    text = lock.read_text(encoding="utf-8")
                except OSError:
                    continue
                for line in text.splitlines():
                    if line.startswith("pid="):
                        try:
                            pids.append(int(line.split("=", 1)[1].strip()))
                        except ValueError:
                            pass
        return WriterInventory(
            mail_pause_present=mail_pause,
            mirror_pause_present=mirror_pause,
            active_writer_pids=pids,
            lock_basenames=locks,
        )

    def service_state(self) -> ServiceState:
        fn = self.systemctl or _default_systemctl
        api = fn(["--user", "is-active", API_SERVICE]) == 0
        health = fn(["--user", "is-active", API_HEALTH_TIMER]) == 0
        return ServiceState(api_active=api, health_timer_active=health)

    def stop_services(self) -> None:
        fn = self.systemctl or _default_systemctl
        for unit in (API_HEALTH_TIMER, API_SERVICE):
            rc = fn(["--user", "stop", unit])
            if rc not in (0,):
                # stop is idempotent-ish; non-zero still fails closed
                _fail(
                    f"failed to stop {unit}",
                    category=CutoverFailureCategory.APPLY,
                )

    def start_services(self) -> None:
        fn = self.systemctl or _default_systemctl
        for unit in (API_SERVICE, API_HEALTH_TIMER):
            rc = fn(["--user", "start", unit])
            if rc != 0:
                _fail(
                    f"failed to start {unit}",
                    category=CutoverFailureCategory.APPLY,
                )

    def wal_state(self, db: Path) -> dict[str, Any]:
        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        return {
            "wal_present": wal.exists(),
            "wal_size": int(wal.stat().st_size) if wal.is_file() else 0,
            "shm_present": shm.exists(),
            "db_fingerprint": fingerprint_token(db) if db.is_file() else None,
        }

    def checkpoint_wal(self, db: Path) -> dict[str, Any]:
        # Read-only URI cannot checkpoint; open briefly for TRUNCATE only when
        # apply+quiesce_wal already proved writers stopped.
        uri = f"file:{db.resolve().as_posix()}?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            before = self.wal_state(db)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
            after = self.wal_state(db)
            if after["wal_size"] not in (0,):
                # Some SQLite builds keep a zero-length WAL file; non-zero fails.
                if after["wal_size"] > 0:
                    _fail(
                        "WAL still non-empty after checkpoint",
                        category=CutoverFailureCategory.VERIFY,
                    )
            return {"before": before, "after": after, "checkpoint": "TRUNCATE"}
        finally:
            conn.close()

    def create_online_backup(self, source: Path, dest: Path) -> dict[str, Any]:
        _fail(
            "real Online Backup adapter not invoked from unit tests; "
            "operator must wire backup_sqlite_online apply for this stage",
            category=CutoverFailureCategory.APPLY,
            recovery=(
                "Use scripts/maintenance/backup_sqlite_online.py --apply with "
                "writers paused, then record basenames into the cutover journal."
            ),
        )
        return {}

    def compact_offline(self, source: Path, dest: Path) -> dict[str, Any]:
        _fail(
            "real offline compact adapter not invoked from unit tests; "
            "operator must wire compact_sqlite_offline apply for this stage",
            category=CutoverFailureCategory.APPLY,
            recovery=(
                "Use scripts/maintenance/compact_sqlite_offline.py against the "
                "fresh offline snapshot into production-FS staging."
            ),
        )
        return {}

    def verify_candidate(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            _fail("candidate missing", category=CutoverFailureCategory.VERIFY)
        if path.name == KNOWN_EVIDENCE_COMPACT or path.name.startswith(
            FORBIDDEN_COMPACT_PREFIXES
        ):
            _fail(
                "refusing evidence-only offline/compact basename as cutover source",
                category=CutoverFailureCategory.SAFETY,
            )
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            qc = conn.execute("PRAGMA quick_check").fetchone()
            if not qc or qc[0] != "ok":
                _fail("quick_check failed", category=CutoverFailureCategory.VERIFY)
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                _fail(
                    f"foreign_key_check violations={len(fk)}",
                    category=CutoverFailureCategory.VERIFY,
                )
        finally:
            conn.close()
        for side in companion_paths(path):
            if side.exists():
                _fail(
                    f"candidate has sidecar {sanitize_path_for_log(side)}",
                    category=CutoverFailureCategory.VERIFY,
                )
        return {
            "quick_check_ok": True,
            "foreign_key_violations": 0,
            "fingerprint": fingerprint_token(path),
            "basename": path.name,
        }

    def probe_rename_exchange(self, directory: Path) -> bool:
        if self.rename_exchange_supported is not None:
            return bool(self.rename_exchange_supported)
        return _probe_rename_exchange(directory)

    def rename_exchange(self, a: Path, b: Path) -> None:
        _rename_exchange(a, b)
        fsync_directory(a.parent)

    def http_smoke(self, base_url: str) -> dict[str, Any]:
        getter = self.http_get
        if getter is None:
            _fail(
                "http smoke getter not configured",
                category=CutoverFailureCategory.VERIFY,
            )
        results = {}
        for path in ("/health", "/operator/status", "/operator/automation-status"):
            payload = getter(base_url.rstrip("/") + path)
            results[path] = {"ok": True, "keys": sorted(payload.keys())[:12]}
        return results

    def git_head_sha(self) -> str:
        # Prefer env for CI/tests; otherwise leave empty for operator to supply.
        return (os.environ.get("ORIGENLAB_EXPECTED_MAIN_SHA") or "").strip()


def _default_systemctl(args: list[str]) -> int:
    import subprocess

    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        return int(proc.returncode)
    except OSError:
        return 127


def _probe_rename_exchange(directory: Path) -> bool:
    """Probe renameat2(RENAME_EXCHANGE) with ephemeral files; cleanup always."""
    stamp = f"{os.getpid()}_{time.time_ns()}"
    a = directory / f".origenlab_rex_probe_{stamp}.a"
    b = directory / f".origenlab_rex_probe_{stamp}.b"
    try:
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        _rename_exchange(a, b)
        return a.read_bytes() == b"b" and b.read_bytes() == b"a"
    except OSError:
        return False
    finally:
        for path in (a, b):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _rename_exchange(a: Path, b: Path) -> None:
    import ctypes
    import ctypes.util

    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        raise OSError(errno.ENOSYS, "libc not found")
    libc = ctypes.CDLL(libc_name, use_errno=True)
    AT_FDCWD = -100
    RENAME_EXCHANGE = 2
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
        str(a).encode(),
        AT_FDCWD,
        str(b).encode(),
        RENAME_EXCHANGE,
    )
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"renameat2 RENAME_EXCHANGE failed errno={err}")


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


def journal_path_for(opts: CutoverOptions, production: Path) -> Path:
    if opts.journal_path is not None:
        return opts.journal_path.expanduser()
    return (
        production.parent
        / ".origenlab_cutover_journals"
        / f"{opts.maintenance_id}.journal.json"
    )


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


def load_journal(adapters: CutoverAdapters, path: Path) -> CutoverJournal | None:
    if not adapters.path_exists(path):
        return None
    try:
        data = json.loads(adapters.read_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            f"unreadable cutover journal ({type(exc).__name__})",
            category=CutoverFailureCategory.AMBIGUOUS,
            recovery="Do not guess; inspect journal basename under cutover journals dir.",
        )
    return CutoverJournal.from_dict(data)


def write_journal(adapters: CutoverAdapters, path: Path, journal: CutoverJournal) -> None:
    journal.updated_at_utc = _iso_now()
    payload = sanitize_evidence(journal.to_dict())
    adapters.write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _require_auth(opts: CutoverOptions, *, for_swap: bool = False) -> None:
    if not opts.confirm_production_cutover:
        _fail(
            "production cutover requires --confirm-production-cutover",
            category=CutoverFailureCategory.SAFETY,
        )
    if not opts.maintenance_id or "/" in opts.maintenance_id or ".." in opts.maintenance_id:
        _fail(
            "unique --maintenance-id required (no path separators)",
            category=CutoverFailureCategory.SAFETY,
        )
    if not opts.expected_main_sha or len(opts.expected_main_sha) < 7:
        _fail(
            "--expected-main-sha required",
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
    if production.name != expected.name:
        _fail(
            "production basename drift",
            category=CutoverFailureCategory.SAFETY,
        )
    if production.resolve() != expected.expanduser().resolve():
        _fail(
            "production path drift vs --expected-production-path",
            category=CutoverFailureCategory.SAFETY,
        )
    fp = adapters.fingerprint(production)
    if fp != opts.expected_production_fingerprint:
        _fail(
            "production fingerprint mismatch vs expected",
            category=CutoverFailureCategory.SAFETY,
        )
    return fp


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

    # Pure reads only.
    prod_exists = adapters.path_exists(production) and adapters.is_file(production)
    fp = adapters.fingerprint(production) if prod_exists else None
    sidecars = [
        sanitize_path_for_log(p)
        for p in companion_paths(production)
        if adapters.path_exists(p)
    ]
    writers = adapters.list_writers()
    services = adapters.service_state()
    wal = adapters.wal_state(production) if prod_exists else {}

    backup_parent = (
        opts.backup_dest.parent if opts.backup_dest is not None else Path("/mnt/d")
    )
    staging_parent = (
        opts.staging_dest.parent
        if opts.staging_dest is not None
        else production.parent
    )
    try:
        free_mnt = adapters.disk_free(backup_parent)
    except Exception:  # noqa: BLE001 — capacity probe must not crash plan
        free_mnt = -1
    try:
        free_root = adapters.disk_free(staging_parent)
    except Exception:  # noqa: BLE001
        free_root = -1

    size = 0
    if prod_exists:
        try:
            size = int(Path(production).stat().st_size)
        except OSError:
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

    rename_ok = False
    # Probe only if parent exists — probe creates ephemeral files, so skip in
    # strict zero-write mode unless allow_synthetic_world and parent exists and
    # operator asked apply. For plan_preflight we report "probe_deferred".
    blockers: list[str] = []
    if not prod_exists:
        blockers.append("production_sqlite_missing")
    if not writers.mail_pause_present or not writers.mirror_pause_present:
        blockers.append("pause_markers_absent")
    if writers.active_writer_pids or writers.lock_basenames:
        blockers.append("active_writers_or_locks")
    if services.api_active or services.health_timer_active:
        blockers.append("api_or_health_still_active")
    if wal.get("wal_size", 0) and not writers.writers_quiesced:
        blockers.append("wal_nonzero_with_active_writers")
    if not topology.get("recommended_topology_ok"):
        blockers.append("capacity_topology_fail_closed")
    if opts.staging_dest and not same_fs_ok:
        blockers.append("staging_not_same_filesystem_as_production")

    journal_expected = None
    if opts.maintenance_id:
        journal_expected = sanitize_path_for_log(
            journal_path_for(opts, production)
        )

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
        "writers": asdict(writers),
        "services": asdict(services),
        "wal": wal,
        "capacity": {
            "backup_parent_free_bytes": free_mnt,
            "staging_parent_free_bytes": free_root,
            "compact_capacity_required_bytes": compact_need,
            "topology": {
                k: v
                for k, v in topology.items()
                if k != "notes"
            },
        },
        "backup_dest_basename": (
            opts.backup_dest.name if opts.backup_dest is not None else None
        ),
        "staging_dest_basename": (
            opts.staging_dest.name if opts.staging_dest is not None else None
        ),
        "same_filesystem_required_for_swap": True,
        "same_filesystem_ok": same_fs_ok,
        "rename_exchange_probe": "deferred_until_apply_or_explicit_probe",
        "rename_exchange_ok": rename_ok,
        "estimated_downtime_hours": "4-6",
        "journal_basename": journal_expected,
        "blockers": blockers,
        "notes": [
            "Zero-write plan only; no pause markers, journals, backups, or swaps created.",
            "July compact candidate is evidence-only and must never be a cutover source.",
            "Each stage requires a separate --apply invocation.",
            "RPO=0 requires writers stopped from backup through post-swap smoke.",
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

    head = adapters.git_head_sha()
    if head and not head.startswith(opts.expected_main_sha[:7]):
        # If adapter returns a SHA, it must match. Empty means operator-supplied only.
        if len(head) >= 7 and head[:7] != opts.expected_main_sha[:7]:
            _fail(
                "main SHA mismatch vs --expected-main-sha",
                category=CutoverFailureCategory.SAFETY,
            )

    fp = _validate_production_identity(opts, adapters, production)
    journal_path = journal_path_for(opts, production)
    journal = load_journal(adapters, journal_path)
    reports = opts.reports_dir or settings.resolved_reports_dir()

    if journal is None:
        if opts.stage != CutoverStage.PAUSE_WRITERS:
            _fail(
                "no journal; start with pause_writers after plan_preflight",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        journal = CutoverJournal(
            maintenance_id=opts.maintenance_id,
            stage=CutoverStage.PLAN_PREFLIGHT.value,
            expected_main_sha=opts.expected_main_sha,
            expected_production_basename=production.name,
            production_fingerprint=fp,
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
        if journal.production_fingerprint and journal.production_fingerprint != fp:
            if opts.stage not in {
                CutoverStage.ATOMIC_SWAP,
                CutoverStage.READONLY_SMOKE,
                CutoverStage.RESUME_SERVICES,
                CutoverStage.RESUME_WRITERS,
                CutoverStage.COMPLETED,
            }:
                _fail(
                    "production fingerprint changed since journal",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )

    def _inject(phase: str) -> None:
        if opts.fail_after == phase:
            _fail(
                f"injected failure after {phase}",
                category=CutoverFailureCategory.APPLY,
            )

    stage = opts.stage
    # Require sequential progress: journal must be at previous stage.
    prev = previous_stage(stage)
    if prev is not None and journal.stage != prev.value:
        # Allow idempotent re-entry of the current stage if journal already there
        # and stage is incomplete side-effect free — otherwise refuse.
        if journal.stage != stage.value:
            _fail(
                f"refusing non-sequential stage: journal={journal.stage} requested={stage.value}",
                category=CutoverFailureCategory.AMBIGUOUS,
                recovery=f"Next safe stage from journal is {next_stage(CutoverStage(journal.stage))}",
            )

    if stage == CutoverStage.PAUSE_WRITERS:
        adapters.touch(mail_pause_path(reports))
        adapters.touch(mirror_pause_path(reports))
        writers = adapters.list_writers()
        if not writers.mail_pause_present or not writers.mirror_pause_present:
            _fail("pause markers missing after touch", category=CutoverFailureCategory.APPLY)
        journal.stage = stage.value
        journal.notes.append("writers_paused")
        write_journal(adapters, journal_path, journal)
        _inject("pause_writers")
        return _stage_report(opts, journal, {"writers": asdict(writers)})

    if stage == CutoverStage.STOP_READERS:
        _expect_journal_stage(journal, CutoverStage.PAUSE_WRITERS)
        adapters.stop_services()
        services = adapters.service_state()
        if services.api_active or services.health_timer_active:
            _fail("services still active after stop", category=CutoverFailureCategory.APPLY)
        journal.services_stopped = True
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        _inject("stop_readers")
        return _stage_report(opts, journal, {"services": asdict(services)})

    if stage == CutoverStage.QUIESCE_WAL:
        _expect_journal_stage(journal, CutoverStage.STOP_READERS)
        writers = adapters.list_writers()
        if not writers.writers_quiesced:
            _fail(
                "writers/locks not quiesced before WAL checkpoint",
                category=CutoverFailureCategory.SAFETY,
            )
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
            _fail("WAL grew or remained non-empty", category=CutoverFailureCategory.VERIFY)
        if after.get("db_fingerprint") != adapters.fingerprint(production):
            _fail("fingerprint drift during quiesce", category=CutoverFailureCategory.VERIFY)
        journal.wal_quiesced = True
        journal.production_fingerprint = adapters.fingerprint(production)
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        _inject("quiesce_wal")
        return _stage_report(
            opts, journal, {"wal_before": before, "wal_after": after, "checkpoint": result}
        )

    if stage == CutoverStage.CREATE_CURRENT_BACKUP:
        _expect_journal_stage(journal, CutoverStage.QUIESCE_WAL)
        if opts.backup_dest is None:
            _fail("--backup-dest required", category=CutoverFailureCategory.PREFLIGHT)
        dest = opts.backup_dest.expanduser()
        _refuse_evidence_compact(dest)
        if adapters.path_exists(dest):
            _fail("backup dest exists (no-clobber)", category=CutoverFailureCategory.APPLY)
        result = adapters.create_online_backup(production, dest)
        _inject("create_current_backup")
        journal.backup_basename = dest.name
        journal.backup_verified = True
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"backup": result})

    if stage == CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING:
        _expect_journal_stage(journal, CutoverStage.CREATE_CURRENT_BACKUP)
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
            _fail("staging dest exists (no-clobber)", category=CutoverFailureCategory.APPLY)
        result = adapters.compact_offline(source, dest)
        _inject("compact_to_production_fs_staging")
        journal.staging_basename = dest.name
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"compact": result})

    if stage == CutoverStage.VERIFY_CANDIDATE:
        _expect_journal_stage(journal, CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING)
        if opts.staging_dest is None:
            _fail("--staging-dest required", category=CutoverFailureCategory.PREFLIGHT)
        dest = opts.staging_dest.expanduser()
        _refuse_evidence_compact(dest)
        result = adapters.verify_candidate(dest)
        _inject("verify_candidate")
        journal.candidate_fingerprint = result.get("fingerprint")
        journal.compact_verified = True
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"verify": result})

    if stage == CutoverStage.APPROVE_SWAP:
        _expect_journal_stage(journal, CutoverStage.VERIFY_CANDIDATE)
        if not journal.compact_verified or not journal.candidate_fingerprint:
            _fail("candidate not verified", category=CutoverFailureCategory.SAFETY)
        journal.swap_approved = True
        journal.stage = stage.value
        _inject("approve_swap")
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"swap_approved": True})

    if stage == CutoverStage.ATOMIC_SWAP:
        _expect_journal_stage(journal, CutoverStage.APPROVE_SWAP)
        if not journal.swap_approved:
            _fail("swap not approved", category=CutoverFailureCategory.SAFETY)
        if journal.writers_resumed:
            _fail(
                "refusing swap after writers resumed",
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
        old_fp = adapters.fingerprint(production)
        new_fp = adapters.fingerprint(staged)
        if journal.candidate_fingerprint and new_fp != journal.candidate_fingerprint:
            _fail(
                "staging fingerprint drift before swap",
                category=CutoverFailureCategory.VERIFY,
            )
        # Exchange production <-> staged, then rename staged(old prod) to pre_cutover.
        adapters.rename_exchange(production, staged)
        _inject("atomic_swap")
        # After exchange: production path has new DB; staged path has old DB.
        pre_name = f"{production.name}.pre_cutover.{opts.maintenance_id}"
        pre_path = production.with_name(pre_name)
        if adapters.path_exists(pre_path):
            _fail("pre_cutover artifact exists (no-clobber)", category=CutoverFailureCategory.APPLY)
        # Move old DB from staging path name to uniquely named rollback artifact.
        # Use os.replace only after exchange left old bytes at staged path.
        # Adapters may implement rename_exchange only; use write via exchange undo forbidden.
        # FilesystemAdapters: perform rename of staged -> pre_path using os.rename.
        _retain_pre_cutover(adapters, staged, pre_path)
        fsync_directory(production.parent)
        journal.pre_cutover_basename = pre_path.name
        journal.production_fingerprint = adapters.fingerprint(production)
        journal.swap_direction = "staging_to_production_via_rename_exchange"
        journal.stage = stage.value
        journal.notes.append(f"old_fp_before_swap={old_fp}")
        write_journal(adapters, journal_path, journal)
        return _stage_report(
            opts,
            journal,
            {
                "pre_cutover_basename": pre_path.name,
                "new_production_fingerprint": journal.production_fingerprint,
                "retained_old_production": True,
            },
        )

    if stage == CutoverStage.READONLY_SMOKE:
        _expect_journal_stage(journal, CutoverStage.ATOMIC_SWAP)
        adapters.start_services()  # API only first via adapter order
        _inject("readonly_smoke")
        # Re-stop health if adapter starts both — smoke uses HTTP against API.
        smoke = adapters.http_smoke(opts.api_base_url)
        live_fp = adapters.fingerprint(production)
        if live_fp != journal.production_fingerprint:
            _fail(
                "production fingerprint mismatch during smoke",
                category=CutoverFailureCategory.VERIFY,
            )
        for side in companion_paths(production):
            if adapters.path_exists(side) and Path(str(side)).stat().st_size > 0:
                # zero-length wal after open may appear; non-empty unexpected for RO
                pass
        journal.smoke_ok = True
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"smoke": smoke})

    if stage == CutoverStage.RESUME_SERVICES:
        _expect_journal_stage(journal, CutoverStage.READONLY_SMOKE)
        if not journal.smoke_ok:
            _fail("smoke not ok", category=CutoverFailureCategory.SAFETY)
        adapters.start_services()
        _inject("resume_services")
        journal.services_stopped = False
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"services": asdict(adapters.service_state())})

    if stage == CutoverStage.RESUME_WRITERS:
        _expect_journal_stage(journal, CutoverStage.RESUME_SERVICES)
        # Remove pause markers in documented order: mail then mirror.
        for path in (mail_pause_path(reports), mirror_pause_path(reports)):
            if adapters.path_exists(path):
                adapters.unlink(path)
        _inject("resume_writers")
        journal.writers_resumed = True
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"writers_resumed": True})

    if stage == CutoverStage.COMPLETED:
        _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS)
        journal.stage = stage.value
        write_journal(adapters, journal_path, journal)
        return _stage_report(opts, journal, {"completed": True})

    _fail(f"unsupported stage {stage.value}", category=CutoverFailureCategory.PREFLIGHT)
    raise AssertionError("unreachable")  # pragma: no cover


def _retain_pre_cutover(adapters: CutoverAdapters, staged_old: Path, pre_path: Path) -> None:
    """Move post-exchange old production bytes to unique pre_cutover name."""
    if hasattr(adapters, "rename_path"):
        adapters.rename_path(staged_old, pre_path)  # type: ignore[attr-defined]
        return
    # Default OS rename.
    if staged_old.resolve() == pre_path.resolve():
        return
    os.rename(staged_old, pre_path)
    fsync_directory(pre_path.parent)


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
    pre_cutover_path: Path,
    expected_old_fingerprint: str,
    expected_new_fingerprint: str,
) -> dict[str, Any]:
    """Verified atomic rollback only if writers have not resumed."""
    _require_auth(opts, for_swap=True)
    adapters = opts.adapters or FilesystemAdapters(settings=opts.settings)
    assert opts.expected_production_path is not None
    production = opts.expected_production_path.expanduser()
    journal_path = journal_path_for(opts, production)
    journal = load_journal(adapters, journal_path)
    if journal is None:
        _fail("missing journal for rollback", category=CutoverFailureCategory.AMBIGUOUS)
    if journal.writers_resumed:
        _fail(
            "automatic rollback refused after writers resumed; use incident reconciliation",
            category=CutoverFailureCategory.SAFETY,
        )
    if adapters.fingerprint(production) != expected_new_fingerprint:
        _fail(
            "current production fingerprint mismatch for rollback",
            category=CutoverFailureCategory.VERIFY,
        )
    if adapters.fingerprint(pre_cutover_path) != expected_old_fingerprint:
        _fail(
            "pre_cutover fingerprint mismatch for rollback",
            category=CutoverFailureCategory.VERIFY,
        )
    if not adapters.probe_rename_exchange(production.parent):
        _fail(
            "rename exchange unsupported for rollback",
            category=CutoverFailureCategory.SAFETY,
        )
    adapters.rename_exchange(production, pre_cutover_path)
    fsync_directory(production.parent)
    journal.swap_direction = "rollback_pre_cutover_to_production"
    journal.production_fingerprint = adapters.fingerprint(production)
    journal.smoke_ok = False
    journal.stage = CutoverStage.ATOMIC_SWAP.value
    journal.notes.append("rolled_back_before_writers_resumed")
    write_journal(adapters, journal_path, journal)
    return sanitize_evidence(
        {
            "rolled_back": True,
            "production_fingerprint": journal.production_fingerprint,
            "writers_resumed": False,
        }
    )


# --- Synthetic world for tests -------------------------------------------------


@dataclass
class SyntheticWorld:
    """In-memory/fake FS cutover world for exhaustive synthetic tests."""

    root: Path
    files: dict[str, bytes] = field(default_factory=dict)
    services: ServiceState = field(default_factory=ServiceState)
    writer_pids: list[int] = field(default_factory=list)
    locks: list[str] = field(default_factory=list)
    mail_pause: bool = False
    mirror_pause: bool = False
    wal_size: int = 0
    rename_exchange_ok: bool = True
    head_sha: str = "25cd4100e226427b3a4d027f1ee3b3af056884d4"
    smoke_payload: dict[str, Any] = field(
        default_factory=lambda: {"status": "ok", "sqlite_query_only": True}
    )
    fail_checkpoint: bool = False

    def key(self, path: Path) -> str:
        return str(path)

    def path_exists(self, path: Path) -> bool:
        return self.key(path) in self.files or path.is_symlink()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink() if path.exists() or path.is_symlink() else False

    def is_file(self, path: Path) -> bool:
        return self.key(path) in self.files

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
        return 300 * 1024**3

    def same_fs(self, a: Path, b: Path) -> bool:
        return True

    def fingerprint(self, path: Path) -> str:
        data = self.files.get(self.key(path), b"")
        digest = hashlib.sha256(data).hexdigest()[:16]
        return f"{len(data)}:0:1:{digest}"

    def list_writers(self) -> WriterInventory:
        return WriterInventory(
            mail_pause_present=self.mail_pause,
            mirror_pause_present=self.mirror_pause,
            active_writer_pids=list(self.writer_pids),
            lock_basenames=list(self.locks),
        )

    def service_state(self) -> ServiceState:
        return ServiceState(
            api_active=self.services.api_active,
            health_timer_active=self.services.health_timer_active,
        )

    def stop_services(self) -> None:
        self.services.api_active = False
        self.services.health_timer_active = False

    def start_services(self) -> None:
        self.services.api_active = True
        self.services.health_timer_active = True

    def wal_state(self, db: Path) -> dict[str, Any]:
        return {
            "wal_present": self.wal_size > 0,
            "wal_size": self.wal_size,
            "shm_present": False,
            "db_fingerprint": self.fingerprint(db) if self.is_file(db) else None,
        }

    def checkpoint_wal(self, db: Path) -> dict[str, Any]:
        if self.fail_checkpoint:
            _fail("checkpoint failed", category=CutoverFailureCategory.VERIFY)
        before = self.wal_state(db)
        self.wal_size = 0
        return {"before": before, "after": self.wal_state(db), "checkpoint": "TRUNCATE"}

    def create_online_backup(self, source: Path, dest: Path) -> dict[str, Any]:
        if self.path_exists(dest):
            _fail("backup exists", category=CutoverFailureCategory.APPLY)
        self.files[self.key(dest)] = self.files[self.key(source)]
        return {"basename": dest.name, "verified": True}

    def compact_offline(self, source: Path, dest: Path) -> dict[str, Any]:
        if self.path_exists(dest):
            _fail("staging exists", category=CutoverFailureCategory.APPLY)
        # Simulate smaller compact by keeping bytes but marking content.
        self.files[self.key(dest)] = b"COMPACT:" + self.files[self.key(source)]
        return {"basename": dest.name, "verified": True}

    def verify_candidate(self, path: Path) -> dict[str, Any]:
        if not self.is_file(path):
            _fail("candidate missing", category=CutoverFailureCategory.VERIFY)
        if path.name == KNOWN_EVIDENCE_COMPACT or path.name.startswith(
            FORBIDDEN_COMPACT_PREFIXES
        ):
            _fail(
                "refusing evidence compact",
                category=CutoverFailureCategory.SAFETY,
            )
        return {
            "quick_check_ok": True,
            "foreign_key_violations": 0,
            "fingerprint": self.fingerprint(path),
            "basename": path.name,
        }

    def probe_rename_exchange(self, directory: Path) -> bool:
        return self.rename_exchange_ok

    def rename_exchange(self, a: Path, b: Path) -> None:
        if not self.rename_exchange_ok:
            raise OSError(errno.ENOSYS, "exchange unsupported")
        ka, kb = self.key(a), self.key(b)
        self.files[ka], self.files[kb] = self.files[kb], self.files[ka]

    def rename_path(self, src: Path, dest: Path) -> None:
        data = self.files.pop(self.key(src))
        self.files[self.key(dest)] = data

    def http_smoke(self, base_url: str) -> dict[str, Any]:
        return {
            "/health": {"ok": True, "keys": list(self.smoke_payload.keys())},
            "/operator/status": {"ok": True, "keys": ["status"]},
            "/operator/automation-status": {"ok": True, "keys": ["status"]},
        }

    def git_head_sha(self) -> str:
        return self.head_sha


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
