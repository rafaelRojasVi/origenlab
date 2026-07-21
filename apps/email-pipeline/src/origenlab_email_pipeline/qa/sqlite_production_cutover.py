"""Fail-closed, resumable SQLite production cutover orchestrator.

Staged state machine only. Default is zero-write preflight. Never runs the
entire production workflow from one command.

Access inventory is split into SQLite writers, SQLite readers, and unrelated
external writers (see ``CUTOVER_ACCESS_INVENTORY``). ChileCompra does not
mutate SQLite and must not block RPO=0. An OS-level chmod write barrier
mitigates accidental ad-hoc SQLite writes; malicious/root bypass is outside
the operator threat model.

Production apply readiness is derived dynamically (pause + stop + flock +
write barrier + clean FDs + WAL quiesce + valid plan), not a hard-coded True.

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
from typing import Any, Callable, Generator, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from origenlab_email_pipeline.config import Settings, canonical_production_sqlite_path, load_settings
from origenlab_email_pipeline.qa.dashboard_api_readiness import (
    API_AUTH_TOKEN_HEADER,
    resolve_api_auth_token,
)
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

CUTOVER_SCHEMA_VERSION = 3
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
# Read-only smoke may only ever talk to the local loopback operator API.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}
# Short bound for /health and /operator/automation-status.
DEFAULT_SMOKE_TIMEOUT_SECONDS = 15.0
# /operator/status has been observed to take ~57s under real load; allow a
# generous but bounded ceiling so a slow-but-healthy status cannot false-fail.
STATUS_SMOKE_TIMEOUT_SECONDS = 180.0
DEFAULT_SMOKE_MAX_RESPONSE_BYTES = 1_000_000
_STATUS_PATH_SUFFIX = "/operator/status"
# API process activity classification (SIGTERM/143 bookkeeping, A3).
API_ACTIVITY_RUNNING = "running"
API_ACTIVITY_STOPPED = "stopped"
# systemd ActiveState / SubState buckets. "unknown"/unlisted => ambiguous.
_ACTIVE_STATE_RUNNING = frozenset({"active", "activating", "reloading", "deactivating"})
_ACTIVE_STATE_STOPPED = frozenset({"inactive", "failed", "dead"})
# Linux open(2) access-mode bits (from /proc/<pid>/fdinfo/<fd> flags).
_O_ACCMODE = 0o3
_O_RDONLY = 0o0
_O_WRONLY = 0o1
_O_RDWR = 0o2

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

# Verified from source: mail writes SQLite; dashboard reads SQLite + writes Postgres;
# API reads SQLite; chilecompra writes CSV/Postgres/external only (no SQLite).
CUTOVER_ACCESS_INVENTORY: dict[str, tuple[dict[str, str], ...]] = {
    "sqlite_writers": (
        {
            "name": "mail_auto_refresh",
            "also_known_as": "daily-core",
            "barrier": "auto_refresh_paused",
            "lock": MAIL_LOCK_FILENAME,
            "status": "must_pause",
            "access": "sqlite_writer",
        },
        {
            "name": "ad_hoc_operator_scripts",
            "barrier": "os_write_barrier_chmod",
            "lock": "none",
            "status": "mitigated_by_write_barrier",
            "access": "potential_sqlite_writer",
            "reason": (
                "manual sqlite3 / scripts may open production RW without pause "
                "markers; chmod write barrier blocks ordinary accidental opens. "
                "Malicious/root bypass is outside the operator threat model."
            ),
        },
    ),
    "sqlite_readers": (
        {
            "name": "dashboard_auto_mirror",
            "barrier": "dashboard_auto_mirror_paused",
            "lock": DASHBOARD_LOCK_FILENAME,
            "status": "must_pause",
            "access": "sqlite_reader_postgres_writer",
            "reason": (
                "pause to release SQLite reads and prevent stale mirror publication"
            ),
        },
        {
            "name": "origenlab-api.service",
            "barrier": "systemctl stop API + health timer",
            "lock": "n/a",
            "status": "must_stop",
            "access": "sqlite_reader",
        },
    ),
    "unrelated_external_writers": (
        {
            "name": "chilecompra_equipment_auto_refresh",
            "barrier": "optional_operational_quiet",
            "lock": CHILECOMPRA_LOCK_FILENAME,
            "status": "not_sqlite",
            "access": "csv_postgres_external_writer",
            "reason": (
                "does not mutate SQLite; must not block RPO=0. Optional quiet "
                "for operational clarity only."
            ),
        },
    ),
}

# Backward-compatible aliases used by docs/tests.
REAL_SQLITE_WRITER_ENTRY_POINTS: tuple[dict[str, str], ...] = (
    CUTOVER_ACCESS_INVENTORY["sqlite_writers"]
)
REAL_SQLITE_READER_ENTRY_POINTS: tuple[dict[str, str], ...] = (
    CUTOVER_ACCESS_INVENTORY["sqlite_readers"]
)
UNRELATED_EXTERNAL_WRITER_ENTRY_POINTS: tuple[dict[str, str], ...] = (
    CUTOVER_ACCESS_INVENTORY["unrelated_external_writers"]
)

# Deprecated name — no longer a hard True; use evaluate_production_apply_readiness.
REAL_PRODUCTION_APPLY_BLOCKED = False
REAL_PRODUCTION_APPLY_BLOCK_REASON = (
    "Production apply readiness is derived: SQLite automation writers paused, "
    "readers stopped, exclusive flock held, OS write barrier active, FD scan "
    "clean, WAL quiesced, and approved plan/journal valid. ChileCompra is not "
    "a SQLite writer and does not block RPO=0."
)


class CutoverStage(enum.Enum):
    PLAN_PREFLIGHT = "plan_preflight"
    PAUSE_WRITERS = "pause_writers"
    STOP_READERS = "stop_readers"
    QUIESCE_WAL = "quiesce_wal"
    APPLY_OS_WRITE_BARRIER = "apply_os_write_barrier"
    CREATE_CURRENT_BACKUP = "create_current_backup"
    COMPACT_TO_PRODUCTION_FS_STAGING = "compact_to_production_fs_staging"
    VERIFY_CANDIDATE = "verify_candidate"
    APPROVE_SWAP = "approve_swap"
    ATOMIC_SWAP = "atomic_swap"
    READONLY_SMOKE = "readonly_smoke"
    RESUME_SERVICES = "resume_services"
    RESUME_WRITERS_PONR = "resume_writers_ponr"
    RESUME_WRITERS_RESTORE_MODE = "resume_writers_restore_mode"
    RESUME_WRITERS_MAIL = "resume_writers_mail"
    RESUME_WRITERS_OBSERVE_MAIL = "resume_writers_observe_mail"
    RESUME_WRITERS_MIRROR = "resume_writers_mirror"
    RESUME_WRITERS_OBSERVE_MIRROR = "resume_writers_observe_mirror"
    RESUME_WRITERS_COMMIT = "resume_writers_commit"
    COMPLETED = "completed"
    # abort_before_swap is an operation, not a forward stage.


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


class _RedirectBlocked(Exception):
    """Internal sentinel: a smoke request attempted an HTTP redirect."""


class _LoopbackOnlyRedirectHandler(HTTPRedirectHandler):
    """Refuse every redirect so smoke can never leave the approved origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise _RedirectBlocked()


def _origin_tuple(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    port = parts.port or _DEFAULT_PORT_BY_SCHEME.get(scheme, 0)
    return scheme, host, port


def make_loopback_json_getter(
    api_base_url: str,
    *,
    token_provider: Callable[[], str | None] = resolve_api_auth_token,
    timeout: float = DEFAULT_SMOKE_TIMEOUT_SECONDS,
    status_timeout: float = STATUS_SMOKE_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_SMOKE_MAX_RESPONSE_BYTES,
) -> Callable[[str], dict[str, Any]]:
    """Build a bounded, GET-only, loopback-restricted JSON getter.

    The returned callable is the production default for ``FilesystemAdapters.http_get``:
    no dependency injection required. It matches the real API auth contract
    (``apps/api`` accepts both ``Authorization: Bearer`` and ``X-OriginLab-API-Key``;
    token from ``ORIGENLAB_API_AUTH_TOKEN``) and never logs the token, response
    bodies, secrets, or absolute paths. ``/operator/status`` gets a longer bounded
    timeout than ``/health`` and ``/operator/automation-status``.
    """
    approved_scheme, approved_host, approved_port = _origin_tuple(api_base_url)
    if approved_scheme != "http" or approved_host not in _LOOPBACK_HOSTS:
        _fail(
            "readonly smoke base URL is not an approved loopback http origin",
            category=CutoverFailureCategory.SAFETY,
        )

    def _assert_same_origin(url: str, *, where: str) -> None:
        parts = urlsplit(url)
        # Reject userinfo and fragments outright — never present on a clean
        # loopback smoke URL, and both are classic SSRF/ambiguity vectors.
        if parts.username or parts.password or parts.fragment:
            _fail(
                f"readonly smoke {where} contained userinfo or a fragment",
                category=CutoverFailureCategory.VERIFY,
            )
        scheme, host, port = _origin_tuple(url)
        if (
            scheme != approved_scheme
            or host not in _LOOPBACK_HOSTS
            or host != approved_host
            or port != approved_port
        ):
            _fail(
                f"readonly smoke {where} left the approved loopback origin",
                category=CutoverFailureCategory.VERIFY,
            )

    def _timeout_for(url: str) -> float:
        path = urlsplit(url).path.rstrip("/")
        return status_timeout if path.endswith(_STATUS_PATH_SUFFIX) else timeout

    def get(url: str) -> dict[str, Any]:
        _assert_same_origin(url, where="request URL")
        headers = {"Accept": "application/json"}
        token = (token_provider() or "").strip()
        if token:
            # Match apps/api: it checks Authorization: Bearer first, then the
            # X-OriginLab-API-Key fallback. Send both for maximum compatibility.
            headers["Authorization"] = f"Bearer {token}"
            headers[API_AUTH_TOKEN_HEADER] = token
        request = Request(url, method="GET", headers=headers)
        opener = build_opener(_LoopbackOnlyRedirectHandler())
        try:
            with opener.open(request, timeout=_timeout_for(url)) as response:
                status = int(getattr(response, "status", 0) or 0)
                final_url = response.geturl()
                raw = response.read(max_bytes + 1)
        except _RedirectBlocked:
            _fail(
                "readonly smoke rejected an HTTP redirect",
                category=CutoverFailureCategory.VERIFY,
            )
        except HTTPError:
            # Never surface the response body or headers.
            _fail(
                "readonly smoke received a non-success HTTP status",
                category=CutoverFailureCategory.VERIFY,
            )
        except (URLError, TimeoutError, OSError):
            _fail(
                "readonly smoke request failed or timed out",
                category=CutoverFailureCategory.VERIFY,
            )
        _assert_same_origin(final_url, where="final URL")
        if len(raw) > max_bytes:
            _fail(
                "readonly smoke response exceeded byte bound",
                category=CutoverFailureCategory.VERIFY,
            )
        if status != 200:
            _fail(
                "readonly smoke response was not HTTP 200",
                category=CutoverFailureCategory.VERIFY,
            )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            _fail(
                "readonly smoke response was not valid JSON",
                category=CutoverFailureCategory.VERIFY,
            )
        if not isinstance(parsed, dict):
            _fail(
                "readonly smoke response JSON was not an object",
                category=CutoverFailureCategory.VERIFY,
            )
        return parsed

    return get


def classify_api_activity(
    *,
    is_active_text: str | None,
    main_pid: int | None,
    listener_present: bool,
    sub_state: str | None = None,
) -> dict[str, Any]:
    """Classify whether the API process is actually running (A3, SIGTERM/143).

    A process is treated as *stopped* only on a *known* stopped state
    (``inactive`` / ``failed`` / ``dead``) with no live PID and no loopback
    listener — this deliberately includes the ``failed`` state that an intentional
    ``systemctl stop`` (SIGTERM, ExecMainStatus 143) leaves behind. A process that
    is in a known running state, or that still has a PID *and* a listener, is
    *running*. Any PID/listener presence, or an *unknown/unrecognized* activity
    text, is **ambiguous** and fails closed as running so "must be stopped" gates
    never proceed on uncertainty. Genuine failure sub-states (auto-restart, oom,
    restart loops) are surfaced via ``genuine_failure_signal`` so callers never
    silently mask them.
    """
    text = (is_active_text or "").strip().lower()
    pid = int(main_pid or 0)
    listener = bool(listener_present)
    sub = (sub_state or "").strip().lower()
    has_process = pid > 0 or listener
    if text in _ACTIVE_STATE_RUNNING or has_process:
        running, ambiguous = True, False
    elif text in _ACTIVE_STATE_STOPPED:
        running, ambiguous = False, False
    else:
        # Unknown/unreachable activity text with no PID/listener: fail closed.
        running, ambiguous = True, True
    genuine_failure = sub in {
        "auto-restart",
        "oom-kill",
        "oom-killed",
        "restart",
        "start-limit-hit",
    }
    return {
        "state": API_ACTIVITY_RUNNING if running else API_ACTIVITY_STOPPED,
        "running": running,
        "stopped": not running,
        "ambiguous": ambiguous,
        "genuine_failure_signal": genuine_failure,
        "is_active_text": text or None,
        "main_pid": pid,
        "listener_present": listener,
        "sub_state": sub or None,
    }


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
            if r.classification == "live"
            and r.pid is not None
            and r.basename != CHILECOMPRA_LOCK_FILENAME
        ]

    @property
    def lock_basenames(self) -> list[str]:
        return [r.basename for r in self.locks if r.classification != "absent"]

    @property
    def writers_quiesced(self) -> bool:
        if not self.mail_pause_present or not self.mirror_pause_present:
            return False
        if any(
            r.classification in {"live", "malformed"}
            and r.basename != CHILECOMPRA_LOCK_FILENAME
            for r in self.locks
        ):
            return False
        # Foreign FD hits (not our pid) block quiesce.
        for hit in self.fd_hits:
            if int(hit.get("pid") or -1) != self.orchestrator_pid:
                access = str(hit.get("access") or "unknown")
                if access != "read_only":
                    return False
                return False  # foreign RO also blocks until readers stopped
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
    smoke_started_api: bool = False
    swap_direction: str | None = None
    swap_intent: dict[str, Any] | None = None
    exchange_completed: bool = False
    old_production_retained: bool = False
    rollback_intent: dict[str, Any] | None = None
    original_mode: int | None = None
    original_uid: int | None = None
    original_gid: int | None = None
    production_write_barrier_active: bool = False
    staging_write_barrier_active: bool = False
    writable_mode_restored: bool = False
    permission_intent: dict[str, Any] | None = None
    abort_before_swap_completed: bool = False
    post_mail_fingerprint: str | None = None
    mail_observe_ok: bool = False
    mail_observe_failure: str | None = None
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
    original_mode: int | None = None
    original_uid: int | None = None
    original_gid: int | None = None
    production_device: int | None = None
    production_inode: int | None = None


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
    def api_activity(self, *, api_base_url: str | None = None) -> dict[str, Any]: ...
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
    def fsync_file(self, path: Path) -> None: ...
    def get_file_mode_owner(self, path: Path) -> dict[str, int]: ...
    def chmod_path(self, path: Path, mode: int) -> None: ...
    def chmod_verified_inode(
        self,
        path: Path,
        mode: int,
        *,
        expected_device: int,
        expected_inode: int,
    ) -> None: ...
    def chown_path(self, path: Path, uid: int, gid: int) -> None: ...
    def quick_check_ok(self, path: Path) -> bool: ...
    def try_open_writable(self, path: Path) -> bool: ...


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


def parse_fdinfo_access_mode(flags_value: int | str) -> str:
    """Classify Linux open flags into read_only / writable / unknown."""
    try:
        if isinstance(flags_value, str):
            text = flags_value.strip().lower()
            flags = int(text, 8) if text.startswith("0") else int(text, 0)
        else:
            flags = int(flags_value)
    except (TypeError, ValueError):
        return "unknown"
    mode = flags & _O_ACCMODE
    if mode == _O_RDONLY:
        return "read_only"
    if mode in {_O_WRONLY, _O_RDWR}:
        return "writable"
    return "unknown"


def read_fdinfo_flags(pid: int, fd: str) -> str:
    path = Path(f"/proc/{pid}/fdinfo/{fd}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    for line in text.splitlines():
        if line.startswith("flags:"):
            return parse_fdinfo_access_mode(line.split(":", 1)[1].strip())
    return "unknown"


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
                    access = read_fdinfo_flags(pid, fd.name)
                    path_label = "unknown"
                    try:
                        link = os.readlink(fd)
                        if link.endswith(" (deleted)"):
                            path_label = "deleted"
                        elif link.endswith("-wal"):
                            path_label = "wal"
                        elif link.endswith("-shm"):
                            path_label = "shm"
                        elif link.endswith("-journal"):
                            path_label = "journal"
                        else:
                            path_label = "db_or_alias"
                    except OSError:
                        path_label = "unreadable_link"
                    hits.append(
                        {
                            "pid": pid,
                            "fd": fd.name,
                            "device": key[0],
                            "inode": key[1],
                            "kind": "production" if key == (device, inode) else "sidecar",
                            "access": access,
                            "path_label": path_label,
                        }
                    )
        except OSError:
            continue
    return hits


class CutoverExclusiveLock:
    """Exclusive flock keyed only by canonical production device/inode.

    Maintenance ID is stored inside the lock file for diagnostics but must not
    create independent lock namespaces for the same production inode.
    """

    def __init__(
        self,
        production: Path,
        maintenance_id: str,
        lock_dir: Path | None = None,
        *,
        device: int | None = None,
        inode: int | None = None,
    ) -> None:
        if device is None or inode is None:
            st = production.stat()
            device = int(st.st_dev)
            inode = int(st.st_ino)
        self.device = int(device)
        self.inode = int(inode)
        self.maintenance_id = maintenance_id
        self.key = f"dev{self.device}_ino{self.inode}.lock"
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
                recovery=(
                    "Another cutover stage holds the exclusive lock for this "
                    "production inode; wait or inspect."
                ),
            )
        payload = (
            f"pid={os.getpid()} maintenance_id={self.maintenance_id} "
            f"started_at={_iso_now()}\n"
        )
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


def _default_systemctl_text(args: list[str]) -> tuple[int, str]:
    """Run systemctl and return (returncode, trimmed stdout). 127 on failure."""
    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        return int(proc.returncode), (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def _loopback_listener_present(host: str, port: int, *, timeout: float = 1.5) -> bool:
    """Best-effort read-only TCP connect probe to a loopback API listener."""
    import socket

    if not host or port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _default_api_activity_inputs(api_base_url: str | None) -> dict[str, Any]:
    """Gather real systemd + listener evidence for ``classify_api_activity``."""
    _, is_active_text = _default_systemctl_text(
        ["--user", "is-active", API_SERVICE]
    )
    _, show = _default_systemctl_text(
        ["--user", "show", API_SERVICE, "-p", "MainPID", "-p", "SubState"]
    )
    main_pid = 0
    sub_state: str | None = None
    for line in show.splitlines():
        if line.startswith("MainPID="):
            try:
                main_pid = int(line.split("=", 1)[1].strip() or "0")
            except ValueError:
                main_pid = 0
        elif line.startswith("SubState="):
            sub_state = line.split("=", 1)[1].strip() or None
    listener = False
    if api_base_url:
        _, host, port = _origin_tuple(api_base_url)
        listener = _loopback_listener_present(host, port)
    return {
        "is_active_text": is_active_text or None,
        "main_pid": main_pid,
        "listener_present": listener,
        "sub_state": sub_state,
    }


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
    api_activity_probe: Callable[[str | None], dict[str, Any]] | None = None
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

    def api_activity(self, *, api_base_url: str | None = None) -> dict[str, Any]:
        """Classify real API process activity (systemd state + PID + listener)."""
        if self.api_activity_probe is not None:
            inputs = self.api_activity_probe(api_base_url)
        else:
            inputs = _default_api_activity_inputs(api_base_url)
        return classify_api_activity(
            is_active_text=inputs.get("is_active_text"),
            main_pid=inputs.get("main_pid"),
            listener_present=bool(inputs.get("listener_present")),
            sub_state=inputs.get("sub_state"),
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
        # Dependency injection stays available for tests; production needs none.
        getter = self.http_get or make_loopback_json_getter(base_url)
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

    def fsync_file(self, path: Path) -> None:
        fsync_file(path)

    def get_file_mode_owner(self, path: Path) -> dict[str, int]:
        st = path.stat()
        return {
            "mode": int(st.st_mode) & 0o7777,
            "uid": int(st.st_uid),
            "gid": int(st.st_gid),
        }

    def chmod_path(self, path: Path, mode: int) -> None:
        os.chmod(path, mode)

    def chmod_verified_inode(
        self,
        path: Path,
        mode: int,
        *,
        expected_device: int,
        expected_inode: int,
    ) -> None:
        """Open path, fstat-match device+inode, then fchmod (not path chmod)."""
        fd = os.open(str(path), os.O_RDONLY)
        try:
            st = os.fstat(fd)
            if int(st.st_dev) != int(expected_device) or int(st.st_ino) != int(
                expected_inode
            ):
                _fail(
                    "refusing chmod: opened FD device/inode mismatch vs journal",
                    category=CutoverFailureCategory.SAFETY,
                    recovery=(
                        "Do not chmod by path alone. Re-resolve canonical production "
                        "and verify fingerprints before retrying the write barrier."
                    ),
                )
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)

    def chown_path(self, path: Path, uid: int, gid: int) -> None:
        os.chown(path, uid, gid)

    def quick_check_ok(self, path: Path) -> bool:
        conn = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30.0
        )
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row and row[0] == "ok")
        finally:
            conn.close()

    def try_open_writable(self, path: Path) -> bool:
        try:
            fd = os.open(str(path), os.O_RDWR)
            os.close(fd)
            return True
        except OSError:
            return False


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
    known = {f.name for f in PrivatePlanPaths.__dataclass_fields__.values()}
    return PrivatePlanPaths(**{k: data[k] for k in data if k in known})


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
    allow_readonly_foreign: bool = False,
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
        # ChileCompra lock is unrelated to SQLite; ignore if present as live
        # only when basename is chilecompra — still fail for mail/dashboard locks.
        live_sqlite = [
            r
            for r in writers.locks
            if r.classification == "live"
            and r.basename != CHILECOMPRA_LOCK_FILENAME
        ]
        if live_sqlite:
            _fail(
                "live writer lock(s) present",
                category=CutoverFailureCategory.SAFETY,
                evidence={"locks": [asdict(r) for r in live_sqlite]},
            )
    bad_fds: list[dict[str, Any]] = []
    for hit in writers.fd_hits:
        pid = int(hit.get("pid") or -1)
        access = str(hit.get("access") or "unknown")
        if pid == writers.orchestrator_pid and allow_own_fd:
            continue
        if access == "writable":
            bad_fds.append(hit)
        elif access == "unknown":
            bad_fds.append(hit)
        elif access == "read_only":
            if not allow_readonly_foreign and pid != writers.orchestrator_pid:
                bad_fds.append(hit)
        else:
            bad_fds.append(hit)
    if bad_fds:
        _fail(
            "unexpected or writable/ambiguous FD holds production/sidecar",
            category=CutoverFailureCategory.SAFETY,
            evidence={"fd_hits": bad_fds[:8]},
        )
    if writers.unreadable:
        _fail(
            "unreadable writer evidence",
            category=CutoverFailureCategory.AMBIGUOUS,
            evidence={"unreadable": writers.unreadable},
        )
    return writers


def evaluate_production_apply_readiness(
    *,
    stage: CutoverStage,
    journal: CutoverJournal | None,
    writers: WriterInventory | None,
    services: ServiceState | None,
    flock_held: bool,
    synthetic: bool,
) -> dict[str, Any]:
    """Derive whether a mutating stage may proceed (not a hard-coded True)."""
    blockers: list[str] = []
    if synthetic:
        return {
            "ready": True,
            "blockers": [],
            "reason": "synthetic_world",
            "access_inventory": CUTOVER_ACCESS_INVENTORY,
        }
    # Early stages establish barriers; later stages require them.
    needs_barrier = stage in {
        CutoverStage.CREATE_CURRENT_BACKUP,
        CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING,
        CutoverStage.VERIFY_CANDIDATE,
        CutoverStage.APPROVE_SWAP,
        CutoverStage.ATOMIC_SWAP,
        CutoverStage.READONLY_SMOKE,
    }
    if writers is not None:
        if not writers.mail_pause_present:
            blockers.append("mail_pause_absent")
        if stage not in {CutoverStage.PAUSE_WRITERS} and not writers.mirror_pause_present:
            blockers.append("mirror_pause_absent")
        live = [
            r
            for r in writers.locks
            if r.classification == "live" and r.basename != CHILECOMPRA_LOCK_FILENAME
        ]
        if live and stage not in {CutoverStage.PAUSE_WRITERS}:
            blockers.append("live_sqlite_automation_locks")
    if services is not None and stage not in {
        CutoverStage.PAUSE_WRITERS,
        CutoverStage.STOP_READERS,
    }:
        if services.api_active or services.health_timer_active:
            if stage not in {
                CutoverStage.READONLY_SMOKE,
                CutoverStage.RESUME_SERVICES,
                CutoverStage.RESUME_WRITERS_PONR,
                CutoverStage.RESUME_WRITERS_RESTORE_MODE,
                CutoverStage.RESUME_WRITERS_MAIL,
                CutoverStage.RESUME_WRITERS_OBSERVE_MAIL,
                CutoverStage.RESUME_WRITERS_MIRROR,
                CutoverStage.RESUME_WRITERS_OBSERVE_MIRROR,
                CutoverStage.RESUME_WRITERS_COMMIT,
                CutoverStage.COMPLETED,
            }:
                blockers.append("readers_still_active")
    if not flock_held and stage != CutoverStage.PLAN_PREFLIGHT:
        blockers.append("exclusive_flock_not_held")
    if needs_barrier:
        if journal is None or not journal.production_write_barrier_active:
            blockers.append("os_write_barrier_inactive")
        if journal is None or not journal.wal_quiesced:
            blockers.append("wal_not_quiesced")
        if journal is None or not journal.approved_plan:
            blockers.append("approved_plan_missing")
    chilecompra_names = {e["name"] for e in UNRELATED_EXTERNAL_WRITER_ENTRY_POINTS}
    return {
        "ready": not blockers,
        "blockers": blockers,
        "reason": REAL_PRODUCTION_APPLY_BLOCK_REASON,
        "chilecompra_blocks_rpo0": False,
        "chilecompra_entry_points": sorted(chilecompra_names),
        "access_inventory": {
            k: list(v) for k, v in CUTOVER_ACCESS_INVENTORY.items()
        },
    }


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


def _failsafe_stop_smoke_api(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
    *,
    api_base_url: str | None = None,
) -> dict[str, Any]:
    """Best-effort cleanup when readonly smoke fails after starting the API.

    Stops only the API this stage started and keeps the health timer stopped,
    then *verifies* the API is actually stopped (no PID / no listener) before
    clearing ownership. Never advances the journal past ``atomic_swap`` and never
    raises, so the original smoke failure propagates unchanged with
    rollback-before-writers availability preserved.

    Returns a sanitized status dict: on a confirmed stop ``api_stopped=True`` and
    ownership is cleared; if the stop failed or the API is still running/ambiguous
    it keeps ``smoke_started_api=True`` and surfaces ``manual_stop_required=True``.
    """
    result: dict[str, Any] = {"api_stopped": False, "manual_stop_required": False}
    try:
        if adapters.service_state().health_timer_active:
            try:
                adapters.stop_health_timer()
            except Exception:  # noqa: BLE001
                pass
        try:
            adapters.stop_api()
        except Exception:  # noqa: BLE001
            pass
        activity = adapters.api_activity(api_base_url=api_base_url)
        journal.smoke_ok = False
        journal.stage = CutoverStage.ATOMIC_SWAP.value
        if activity.get("stopped"):
            result["api_stopped"] = True
            journal.smoke_started_api = False
            journal.notes.append("readonly_smoke_failed_started_api_stopped")
        else:
            # Still running/ambiguous: preserve ownership, require manual stop.
            result["manual_stop_required"] = True
            journal.smoke_started_api = True
            journal.notes.append("readonly_smoke_failed_manual_stop_required")
        try:
            write_journal(adapters, journal_path, journal)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        # Even the probe failed: fail safe, keep ownership, require manual stop.
        result["api_stopped"] = False
        result["manual_stop_required"] = True
        try:
            journal.smoke_started_api = True
            journal.smoke_ok = False
            journal.stage = CutoverStage.ATOMIC_SWAP.value
            journal.notes.append(
                "readonly_smoke_cleanup_indeterminate_manual_stop_required"
            )
            write_journal(adapters, journal_path, journal)
        except Exception:  # noqa: BLE001
            pass
    return result


def _attach_cleanup_evidence(original: BaseException, cleanup: dict[str, Any]) -> None:
    """Attach sanitized cleanup state to the original smoke error (never replaces it)."""
    if not isinstance(original, CutoverError):
        return
    try:
        merged = {
            **(original.evidence or {}),
            "smoke_cleanup": {
                "api_stopped": bool(cleanup.get("api_stopped")),
                "manual_stop_required": bool(cleanup.get("manual_stop_required")),
            },
        }
        original.evidence = sanitize_evidence(merged)
    except Exception:  # noqa: BLE001
        pass


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
    live_sqlite_locks = [
        r
        for r in writers.locks
        if r.classification in {"live", "malformed"}
        and r.basename != CHILECOMPRA_LOCK_FILENAME
    ]
    if writers.live_writer_pids or live_sqlite_locks:
        blockers.append("active_sqlite_writers_or_locks")
    if services.api_active or services.health_timer_active:
        blockers.append("api_or_health_still_active")
    if not topology.get("recommended_topology_ok"):
        blockers.append("capacity_topology_fail_closed")
    if opts.staging_dest and not same_fs_ok:
        blockers.append("staging_not_same_filesystem_as_production")
    readiness = evaluate_production_apply_readiness(
        stage=CutoverStage.CREATE_CURRENT_BACKUP,
        journal=None,
        writers=writers,
        services=services,
        flock_held=False,
        synthetic=_is_synthetic(opts, adapters),
    )
    if not readiness["ready"] and not _is_synthetic(opts, adapters):
        blockers.extend(f"readiness:{b}" for b in readiness["blockers"])

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
        "access_inventory": readiness["access_inventory"],
        "chilecompra_blocks_rpo0": False,
        "production_apply_readiness": {
            "ready": readiness["ready"],
            "blockers": readiness["blockers"],
        },
        "blockers": blockers,
        "notes": [
            "Zero-write plan only; no pause markers, journals, locks, backups, or swaps created.",
            "July compact candidate is evidence-only and must never be a cutover source.",
            "Each stage requires a separate --apply invocation.",
            "RPO=0 requires SQLite writers stopped from backup through post-swap smoke.",
            "ChileCompra does not mutate SQLite and does not block RPO=0.",
            "OS write barrier mitigates accidental ad-hoc SQLite writes; root/malicious bypass is out of scope.",
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
        readiness = evaluate_production_apply_readiness(
            stage=opts.stage,
            journal=journal,
            writers=adapters.list_writers(production),
            services=adapters.service_state(),
            flock_held=True,
            synthetic=_is_synthetic(opts, adapters),
        )
        if not readiness["ready"] and opts.stage not in {
            CutoverStage.PAUSE_WRITERS,
            CutoverStage.STOP_READERS,
            CutoverStage.QUIESCE_WAL,
            CutoverStage.APPLY_OS_WRITE_BARRIER,
            CutoverStage.RESUME_SERVICES,
            CutoverStage.RESUME_WRITERS_PONR,
            CutoverStage.RESUME_WRITERS_RESTORE_MODE,
            CutoverStage.RESUME_WRITERS_MAIL,
            CutoverStage.RESUME_WRITERS_OBSERVE_MAIL,
            CutoverStage.RESUME_WRITERS_MIRROR,
            CutoverStage.RESUME_WRITERS_OBSERVE_MIRROR,
            CutoverStage.RESUME_WRITERS_COMMIT,
            CutoverStage.COMPLETED,
        }:
            # Early bootstrap stages allowed; barrier-required stages fail closed.
            if opts.stage in {
                CutoverStage.CREATE_CURRENT_BACKUP,
                CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING,
                CutoverStage.VERIFY_CANDIDATE,
                CutoverStage.APPROVE_SWAP,
                CutoverStage.ATOMIC_SWAP,
                CutoverStage.READONLY_SMOKE,
            }:
                _fail(
                    "production apply readiness not met",
                    category=CutoverFailureCategory.SAFETY,
                    evidence={"blockers": readiness["blockers"]},
                    recovery=REAL_PRODUCTION_APPLY_BLOCK_REASON,
                )

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
                    CutoverStage.RESUME_WRITERS_PONR,
                    CutoverStage.RESUME_WRITERS_RESTORE_MODE,
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

        if stage == CutoverStage.APPLY_OS_WRITE_BARRIER:
            _expect_journal_stage(journal, CutoverStage.QUIESCE_WAL)
            if not journal.wal_quiesced:
                _fail("WAL not quiesced", category=CutoverFailureCategory.SAFETY)
            _assert_writers_quiesced(adapters, production)
            services = adapters.service_state()
            if services.api_active or services.health_timer_active:
                _fail(
                    "API/health must be stopped before write barrier",
                    category=CutoverFailureCategory.SAFETY,
                )
            owner = adapters.get_file_mode_owner(production)
            original_mode = int(owner["mode"])
            original_uid = int(owner["uid"])
            original_gid = int(owner["gid"])
            barrier_mode = original_mode & ~0o222
            ident = adapters.path_identity(production)
            expected_dev = int(journal.production_device or ident["device"])
            expected_ino = int(journal.production_inode or ident["inode"])
            if int(ident["device"]) != expected_dev or int(ident["inode"]) != expected_ino:
                _fail(
                    "production device/inode drift before write barrier",
                    category=CutoverFailureCategory.SAFETY,
                )
            journal.original_mode = original_mode
            journal.original_uid = original_uid
            journal.original_gid = original_gid
            journal.permission_intent = {
                "action": "apply_production_write_barrier",
                "original_mode": original_mode,
                "barrier_mode": barrier_mode,
                "expected_device": expected_dev,
                "expected_inode": expected_ino,
            }
            write_journal(adapters, journal_path, journal)
            _inject("permission_intent_barrier")
            private = load_private_paths(adapters, journal_path)
            if private is None:
                _fail(
                    "missing private plan for permission record",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            assert private is not None
            private.original_mode = original_mode
            private.original_uid = original_uid
            private.original_gid = original_gid
            private.production_device = expected_dev
            private.production_inode = expected_ino
            write_private_paths(adapters, journal_path, private)
            adapters.chmod_verified_inode(
                production,
                barrier_mode,
                expected_device=expected_dev,
                expected_inode=expected_ino,
            )
            adapters.fsync_file(production)
            adapters.fsync_dir(production.parent)
            _inject("after_production_chmod_barrier")
            verified = adapters.get_file_mode_owner(production)
            if int(verified["mode"]) != barrier_mode:
                _fail(
                    "write barrier mode verification failed",
                    category=CutoverFailureCategory.VERIFY,
                    recovery=(
                        "Production may be read-only. Run abort_before_swap or "
                        "reconcile_permission_barrier to restore original_mode from "
                        "journal/private plan, then inspect."
                    ),
                )
            if adapters.try_open_writable(production):
                _fail(
                    "write barrier failed: production still opens writable",
                    category=CutoverFailureCategory.VERIFY,
                    recovery=(
                        "Barrier incomplete. Restore via abort_before_swap / "
                        "reconcile_permission_barrier before any writer resume."
                    ),
                )
            post_ident = adapters.path_identity(production)
            if (
                int(post_ident["device"]) != expected_dev
                or int(post_ident["inode"]) != expected_ino
            ):
                _fail(
                    "production device/inode changed during write barrier",
                    category=CutoverFailureCategory.SAFETY,
                )
            _assert_writers_quiesced(adapters, production)
            journal.production_write_barrier_active = True
            journal.writable_mode_restored = False
            journal.permission_intent = None
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("apply_os_write_barrier")
            return _stage_report(
                opts,
                journal,
                {
                    "production_write_barrier_active": True,
                    "barrier_mode": barrier_mode,
                    "barrier_device": expected_dev,
                    "barrier_inode": expected_ino,
                    "threat_model_note": (
                        "Ordinary accidental ad-hoc SQLite writes are blocked; "
                        "malicious/root bypass is outside operator threat model."
                    ),
                },
            )

        if stage == CutoverStage.CREATE_CURRENT_BACKUP:
            _expect_journal_stage(journal, CutoverStage.APPLY_OS_WRITE_BARRIER)
            if not journal.production_write_barrier_active:
                _fail(
                    "OS write barrier must be active before backup",
                    category=CutoverFailureCategory.SAFETY,
                )
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
            if opts.staging_dest is None:
                _fail("--staging-dest required", category=CutoverFailureCategory.PREFLIGHT)
            staged = opts.staging_dest.expanduser()
            # Staging must be RO before swap; bind owner/mode into journal.
            if journal.original_uid is None or journal.original_gid is None:
                _fail(
                    "original ownership missing for staging barrier",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            journal.permission_intent = {
                "action": "apply_staging_write_barrier",
                "staging_basename": staged.name,
            }
            write_journal(adapters, journal_path, journal)
            _inject("staging_barrier_intent")
            adapters.chown_path(staged, int(journal.original_uid), int(journal.original_gid))
            st_mode = int(adapters.get_file_mode_owner(staged)["mode"]) & ~0o222
            adapters.chmod_path(staged, st_mode)
            adapters.fsync_file(staged)
            adapters.fsync_dir(staged.parent)
            verified = adapters.get_file_mode_owner(staged)
            if int(verified["mode"]) & 0o222:
                _fail(
                    "staging still has write bits",
                    category=CutoverFailureCategory.VERIFY,
                )
            journal.staging_write_barrier_active = True
            journal.permission_intent = None
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
            entry_activity = adapters.api_activity(api_base_url=opts.api_base_url)
            # Ownership: this stage may only stop an API it started itself.
            owns_api = journal.smoke_started_api
            if entry_activity["running"] and not owns_api:
                _fail(
                    "API already active/ambiguous before readonly smoke; refusing "
                    "to claim ownership or stop an unrelated process",
                    category=CutoverFailureCategory.SAFETY,
                )
            try:
                _inject("readonly_smoke_before_ownership_write")
                if not entry_activity["running"]:
                    # Persist durable ownership intent BEFORE start_api so a crash
                    # can never orphan an unowned running API; a resumed run sees
                    # smoke_started_api=True and re-drives or safely stops it.
                    journal.smoke_started_api = True
                    owns_api = True
                    write_journal(adapters, journal_path, journal)
                    _inject("readonly_smoke_ownership_written")
                    adapters.start_api()
                    _inject("readonly_smoke_after_api_start")
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
            except BaseException as original:
                # Fail-safe: stop only the API we started; keep timer stopped;
                # leave smoke_ok=False; do not advance the journal from
                # atomic_swap; preserve rollback-before-writers availability.
                # Preserve the original error; attach sanitized cleanup state.
                if owns_api:
                    cleanup = _failsafe_stop_smoke_api(
                        adapters,
                        journal,
                        journal_path,
                        api_base_url=opts.api_base_url,
                    )
                    _attach_cleanup_evidence(original, cleanup)
                raise

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

        if stage == CutoverStage.RESUME_WRITERS_PONR:
            _expect_journal_stage(journal, CutoverStage.RESUME_SERVICES)
            if journal.writable_mode_restored:
                _fail(
                    "writable mode already restored while advertising rollback safety",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            # Point of no return BEFORE restoring writable permissions.
            journal.writer_resume_started = True
            write_journal(adapters, journal_path, journal)
            _inject("writer_resume_started")
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_ponr")
            return _stage_report(
                opts,
                journal,
                {
                    "writer_resume_started": True,
                    "rollback_allowed": False,
                },
            )

        if stage == CutoverStage.RESUME_WRITERS_RESTORE_MODE:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_PONR)
            if not journal.writer_resume_started:
                _fail(
                    "writer_resume_started required before restoring writable mode",
                    category=CutoverFailureCategory.SAFETY,
                )
            if journal.original_mode is None:
                _fail(
                    "original_mode missing from journal",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            journal.permission_intent = {
                "action": "restore_production_writable_mode",
                "target_mode": journal.original_mode,
            }
            write_journal(adapters, journal_path, journal)
            _inject("restore_mode_intent")
            adapters.chmod_verified_inode(
                production,
                int(journal.original_mode),
                expected_device=int(journal.production_device or 0),
                expected_inode=int(journal.production_inode or 0),
            )
            adapters.fsync_file(production)
            adapters.fsync_dir(production.parent)
            _inject("after_restore_chmod")
            verified = adapters.get_file_mode_owner(production)
            if int(verified["mode"]) != int(journal.original_mode):
                _fail(
                    "writable mode restore verification failed",
                    category=CutoverFailureCategory.VERIFY,
                )
            ident = adapters.path_identity(production)
            if (
                journal.production_device is not None
                and int(ident["device"]) != int(journal.production_device)
            ) or (
                journal.production_inode is not None
                and int(ident["inode"]) != int(journal.production_inode)
            ):
                _fail(
                    "production device/inode changed before writer resume",
                    category=CutoverFailureCategory.SAFETY,
                )
            journal.writable_mode_restored = True
            journal.production_write_barrier_active = False
            journal.permission_intent = None
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_restore_mode")
            return _stage_report(opts, journal, {"writable_mode_restored": True})

        if stage == CutoverStage.RESUME_WRITERS_MAIL:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_RESTORE_MODE)
            if not journal.writer_resume_started or not journal.writable_mode_restored:
                _fail(
                    "PoNR and writable restore required before removing mail pause",
                    category=CutoverFailureCategory.SAFETY,
                )
            path = mail_pause_path(reports)
            if adapters.path_exists(path):
                adapters.unlink(path)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_mail")
            return _stage_report(opts, journal, {"mail_pause_removed": True})

        if stage == CutoverStage.RESUME_WRITERS_OBSERVE_MAIL:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_MAIL)
            # Legitimate ingestion may change size/mtime fingerprint.
            failure: str | None = None
            ident = adapters.path_identity(production)
            if (
                journal.production_device is not None
                and int(ident["device"]) != int(journal.production_device)
            ) or (
                journal.production_inode is not None
                and int(ident["inode"]) != int(journal.production_inode)
            ):
                failure = "production_device_inode_replacement"
            mode = adapters.get_file_mode_owner(production)
            if failure is None and journal.original_mode is not None and int(
                mode["mode"]
            ) != int(journal.original_mode):
                failure = "production_mode_unexpected"
            if failure is None and int(mode["mode"]) & 0o222 == 0:
                failure = "production_not_writable"
            if failure is None and not adapters.quick_check_ok(production):
                failure = "quick_check_failed"
            if failure is not None:
                # Re-pause mail; do not advance to mirror. Recoverable stopped state.
                adapters.touch(mail_pause_path(reports))
                journal.mail_observe_ok = False
                journal.mail_observe_failure = failure
                journal.notes.append(f"mail_observe_failed:{failure}")
                write_journal(adapters, journal_path, journal)
                _fail(
                    f"mail observe failed ({failure}); mail re-paused; "
                    "mirror must not proceed",
                    category=CutoverFailureCategory.VERIFY,
                    recovery=(
                        "Inspect production SQLite and mail automation. "
                        "Journal stage remains resume_writers_mail / observe "
                        "incomplete. Do not resume_writers_mirror until observe "
                        "succeeds. Rollback remains forbidden after "
                        "writer_resume_started."
                    ),
                    evidence={"mail_observe_failure": failure},
                )
            live_fp = adapters.fingerprint(production)
            journal.post_mail_fingerprint = live_fp
            journal.production_fingerprint = live_fp
            journal.mail_observe_ok = True
            journal.mail_observe_failure = None
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_observe_mail")
            return _stage_report(
                opts,
                journal,
                {
                    "post_mail_fingerprint": live_fp,
                    "device": ident["device"],
                    "inode": ident["inode"],
                    "mail_observe_ok": True,
                },
            )

        if stage == CutoverStage.RESUME_WRITERS_MIRROR:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_OBSERVE_MAIL)
            if not journal.writer_resume_started:
                _fail(
                    "writer_resume_started missing",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            if not journal.mail_observe_ok:
                _fail(
                    "refusing mirror resume: mail observe not ok",
                    category=CutoverFailureCategory.SAFETY,
                    recovery=(
                        "Re-run resume_writers_observe_mail after fixing mail/SQLite. "
                        "Mail should remain paused if observe previously failed."
                    ),
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
            # Mirror must not mutate SQLite; identity changes attributed to mail.
            ident = adapters.path_identity(production)
            if (
                journal.production_device is not None
                and int(ident["device"]) != int(journal.production_device)
            ) or (
                journal.production_inode is not None
                and int(ident["inode"]) != int(journal.production_inode)
            ):
                _fail(
                    "production device/inode replacement after mirror resume refused",
                    category=CutoverFailureCategory.SAFETY,
                )
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_observe_mirror")
            return _stage_report(
                opts,
                journal,
                {"observed_fingerprint": adapters.fingerprint(production)},
            )

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


def _resolve_original_mode_for_restore(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
) -> int | None:
    if journal.original_mode is not None:
        return int(journal.original_mode)
    intent = journal.permission_intent or {}
    if intent.get("original_mode") is not None:
        return int(intent["original_mode"])
    if intent.get("target_mode") is not None and str(intent.get("action", "")).endswith(
        "restore_writable_mode"
    ):
        return int(intent["target_mode"])
    private = load_private_paths(adapters, journal_path)
    if private is not None and private.original_mode is not None:
        return int(private.original_mode)
    return None


def reconcile_permission_barrier(
    adapters: CutoverAdapters,
    *,
    production: Path,
    journal: CutoverJournal,
    journal_path: Path,
) -> dict[str, Any]:
    """Inspect mode vs journal after chmod crash; never silently leave writers unpaused."""
    mode = int(adapters.get_file_mode_owner(production)["mode"])
    write_bits = bool(mode & 0o222)
    original = _resolve_original_mode_for_restore(adapters, journal, journal_path)
    state = {
        "current_mode": mode,
        "write_bits_present": write_bits,
        "barrier_active_flag": journal.production_write_barrier_active,
        "permission_intent": journal.permission_intent,
        "original_mode_known": original is not None,
        "recognized": None,
        "safe_action": None,
    }
    if journal.writer_resume_started:
        state["recognized"] = "ponr_reached"
        state["safe_action"] = "never_auto_rollback_inspect_manually"
        return state
    if not write_bits and original is not None:
        if journal.production_write_barrier_active:
            state["recognized"] = "barrier_active_ro"
            state["safe_action"] = "continue_or_abort_before_swap"
        elif journal.permission_intent:
            state["recognized"] = "chmod_applied_journal_incomplete"
            state["safe_action"] = "abort_before_swap_to_restore_or_complete_barrier_stage"
        else:
            state["recognized"] = "unexpected_readonly"
            state["safe_action"] = "abort_before_swap_or_manual_chmod_restore"
        return state
    if write_bits and journal.production_write_barrier_active:
        state["recognized"] = "barrier_flag_stale_file_writable"
        state["safe_action"] = "manual_inspect_refuse_blind_retry"
        return state
    state["recognized"] = "writable_no_barrier"
    state["safe_action"] = "continue_if_pre_barrier_else_inspect"
    return state


def abort_before_swap(opts: CutoverOptions) -> dict[str, Any]:
    """Safe abort before exchange / writer resume: restore perms, services, markers."""
    _require_auth(opts, for_swap=False)
    adapters = opts.adapters or FilesystemAdapters(settings=opts.settings)
    assert opts.expected_production_path is not None
    production = opts.expected_production_path.expanduser()
    reports = opts.reports_dir or (
        opts.settings or load_settings(enable_dotenv=False)
    ).resolved_reports_dir()
    journal_path = journal_path_for(opts, production)
    with adapters.acquire_exclusive_lock(production, opts.maintenance_id):
        journal = load_journal(adapters, journal_path)
        if journal is None:
            _fail("missing journal for abort", category=CutoverFailureCategory.AMBIGUOUS)
        assert journal is not None
        if journal.writer_resume_started or journal.writers_resumed:
            _fail(
                "abort_before_swap refused after writer_resume_started",
                category=CutoverFailureCategory.SAFETY,
            )
        if journal.swap_intent or journal.exchange_completed:
            _fail(
                "abort_before_swap refused after swap intent/exchange",
                category=CutoverFailureCategory.SAFETY,
            )
        fp = adapters.fingerprint(production)
        if journal.production_fingerprint and fp != journal.production_fingerprint:
            _fail(
                "production fingerprint mismatch for abort",
                category=CutoverFailureCategory.VERIFY,
            )
        ident = adapters.path_identity(production)
        if (
            journal.production_device is not None
            and int(ident["device"]) != int(journal.production_device)
        ) or (
            journal.production_inode is not None
            and int(ident["inode"]) != int(journal.production_inode)
        ):
            _fail(
                "production device/inode mismatch for abort",
                category=CutoverFailureCategory.SAFETY,
            )

        # Stop readers (idempotent).
        adapters.stop_health_timer()
        adapters.stop_api()

        original = _resolve_original_mode_for_restore(adapters, journal, journal_path)
        current_mode = int(adapters.get_file_mode_owner(production)["mode"])
        needs_restore = (
            original is not None
            and (
                journal.production_write_barrier_active
                or bool(journal.permission_intent)
                or (current_mode & 0o222) == 0
            )
        )
        if needs_restore:
            journal.permission_intent = {
                "action": "abort_restore_writable_mode",
                "target_mode": original,
            }
            write_journal(adapters, journal_path, journal)
            adapters.chmod_verified_inode(
                production,
                int(original),
                expected_device=int(journal.production_device or ident["device"]),
                expected_inode=int(journal.production_inode or ident["inode"]),
            )
            adapters.fsync_file(production)
            adapters.fsync_dir(production.parent)
            verified = adapters.get_file_mode_owner(production)
            if int(verified["mode"]) != int(original):
                _fail(
                    "abort failed to restore original mode",
                    category=CutoverFailureCategory.VERIFY,
                    recovery=(
                        "Manual chmod may be required using original_mode from "
                        "journal or private plan. Do not resume writers until "
                        "writable mode is verified."
                    ),
                )
            journal.production_write_barrier_active = False
            journal.writable_mode_restored = True
            journal.permission_intent = None
            write_journal(adapters, journal_path, journal)

        adapters.start_api()
        smoke = adapters.http_smoke(
            opts.api_base_url, expected_fingerprint=adapters.fingerprint(production)
        )
        adapters.start_health_timer()

        for path in (mail_pause_path(reports), mirror_pause_path(reports)):
            if adapters.path_exists(path):
                adapters.unlink(path)

        journal.abort_before_swap_completed = True
        journal.services_stopped = False
        journal.notes.append("aborted_before_swap")
        # Leave stage at last completed forward stage for audit; mark abort note.
        # Do not silently claim COMPLETED cutover success.
        if journal.stage not in {
            CutoverStage.COMPLETED.value,
            CutoverStage.PLAN_PREFLIGHT.value,
        }:
            journal.notes.append(f"abort_from_stage={journal.stage}")
        write_journal(adapters, journal_path, journal)
        return sanitize_evidence(
            {
                "aborted_before_swap": True,
                "smoke": smoke,
                "production_write_barrier_active": False,
                "stage": journal.stage,
                "permission_reconcile": reconcile_permission_barrier(
                    adapters,
                    production=production,
                    journal=journal,
                    journal_path=journal_path,
                ),
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
    # Test knobs: force a stop to be ignored (API keeps running) and/or override
    # the activity classification inputs independent of ``services``.
    refuse_stop_api: bool = False
    api_activity_override: dict[str, Any] | None = None
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
    _held_lock_keys: set[str] = field(default_factory=set)
    same_fs_pairs: set[tuple[str, str]] | None = None
    free_bytes: int = 300 * 1024**3
    modes: dict[str, int] = field(default_factory=dict)
    owners: dict[str, tuple[int, int]] = field(default_factory=dict)
    quick_check_fail: bool = False
    # Stable inode overrides for tests that need fixed device/inode.
    inode_overrides: dict[str, int] = field(default_factory=dict)
    device_overrides: dict[str, int] = field(default_factory=dict)

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
        digest = int(hashlib.sha256(self.key(path).encode()).hexdigest()[:8], 16)
        inode = self.inode_overrides.get(self.key(path), digest or 1)
        device = self.device_overrides.get(self.key(path), 1)
        return {
            "basename": path.name,
            "size_bytes": len(data),
            "mtime_ns": 0,
            "device": device,
            "inode": inode,
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

    def api_activity(self, *, api_base_url: str | None = None) -> dict[str, Any]:
        if self.api_activity_override is not None:
            inputs = dict(self.api_activity_override)
        else:
            active = self.services.api_active
            inputs = {
                "is_active_text": "active" if active else "inactive",
                "main_pid": 4321 if active else 0,
                "listener_present": active,
                "sub_state": "running" if active else "dead",
            }
        return classify_api_activity(
            is_active_text=inputs.get("is_active_text"),
            main_pid=inputs.get("main_pid"),
            listener_present=bool(inputs.get("listener_present")),
            sub_state=inputs.get("sub_state"),
        )

    def stop_api(self) -> None:
        # Simulate a stop that does not take effect (API keeps listening).
        if self.refuse_stop_api:
            return
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
        self.modes[self.key(dest)] = 0o444
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
        self.modes[self.key(dest)] = 0o644
        self.owners[self.key(dest)] = self.owners.get(self.key(source), (1000, 1000))
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
        ident = self.path_identity(production)
        lock_key = f"dev{ident['device']}_ino{ident['inode']}"
        if lock_key in self._held_lock_keys or self.lock_held:
            _fail(
                "cutover lock contention (Busy)",
                category=CutoverFailureCategory.SAFETY,
            )
        self._held_lock_keys.add(lock_key)
        self.lock_held = True
        self._lock_owner = os.getpid()
        try:
            yield None
        finally:
            self._held_lock_keys.discard(lock_key)
            self.lock_held = False
            self._lock_owner = None

    def fsync_dir(self, path: Path) -> None:
        return

    def fsync_file(self, path: Path) -> None:
        return

    def get_file_mode_owner(self, path: Path) -> dict[str, int]:
        k = self.key(path)
        mode = self.modes.get(k, 0o644)
        uid, gid = self.owners.get(k, (1000, 1000))
        return {"mode": mode, "uid": uid, "gid": gid}

    def chmod_path(self, path: Path, mode: int) -> None:
        self.modes[self.key(path)] = int(mode) & 0o7777

    def chmod_verified_inode(
        self,
        path: Path,
        mode: int,
        *,
        expected_device: int,
        expected_inode: int,
    ) -> None:
        ident = self.path_identity(path)
        if int(ident["device"]) != int(expected_device) or int(ident["inode"]) != int(
            expected_inode
        ):
            _fail(
                "refusing chmod: opened FD device/inode mismatch vs journal",
                category=CutoverFailureCategory.SAFETY,
            )
        self.chmod_path(path, mode)

    def chown_path(self, path: Path, uid: int, gid: int) -> None:
        self.owners[self.key(path)] = (int(uid), int(gid))

    def quick_check_ok(self, path: Path) -> bool:
        if self.quick_check_fail:
            return False
        return self.is_file(path)

    def try_open_writable(self, path: Path) -> bool:
        mode = self.modes.get(self.key(path), 0o644)
        return bool(mode & 0o222)


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
