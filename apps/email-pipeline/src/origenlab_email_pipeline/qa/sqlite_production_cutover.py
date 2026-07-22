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
import stat as stat_mod
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Generator, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from origenlab_email_pipeline.config import Settings, canonical_production_sqlite_path, load_settings
from origenlab_email_pipeline.qa.dashboard_api_readiness import (
    API_AUTH_TOKEN_HEADER,
    resolve_api_auth_token,
)
from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
    ROLE_MAIN,
    ROLE_SHM,
    ROLE_WAL,
    ArtifactPermissionError,
    ArtifactPermissions,
    apply_write_barrier,
    capture_artifact,
    dump_artifact_permissions,
    filesystem_capture_member_identity,
    legacy_artifact_from_original_mode,
    load_artifact_permissions,
    member_path,
    recapture_post_swap_companions,
    reconcile_artifact_barrier_state,
    record_post_swap_main,
    restore_post_swap_for_writer_resume,
    restore_pre_swap_changed_members,
    verify_artifact_writable_for_resume,
)
from origenlab_email_pipeline.qa.sqlite_cutover_maintenance_boot_policy import (
    BOOT_POLICY_RESTORE_ACTION,
    BOOT_POLICY_SUPPRESS_ACTION,
    BOOT_POLICY_UNITS,
    LIFECYCLE_PRISTINE,
    LIFECYCLE_RESTORED,
    LIFECYCLE_SUPPRESSION_ACTIVE,
    boot_policy_intent_problem,
    boot_policy_journal_problem,
    boot_policy_lifecycle,
    boot_policy_requires_restoration,
    classify_unit_enablement,
    sanitized_boot_policy_status,
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
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    OPERR_BUSY_OR_LOCKED,
    OPERR_CANNOT_OPEN,
    OPERR_CAPACITY,
    OPERR_INTERRUPTED,
    OPERR_IO_ERROR,
    OPERR_READONLY_WAL_LOCKING,
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
    # Distinct terminal reached ONLY via rollback_finalize (PR-C). Mutually
    # exclusive with COMPLETED; never a successful cutover; never soak-eligible.
    # Kept out of the linear STAGE_ORDER so ordinary next/previous progression
    # can never advance a rolled-back MID toward COMPLETED.
    ABANDONED = "abandoned"
    # abort_before_swap and rollback_finalize are operations, not forward stages.


# Linear forward progression excludes ABANDONED (out-of-band terminal).
STAGE_ORDER: tuple[CutoverStage, ...] = tuple(
    s for s in CutoverStage if s is not CutoverStage.ABANDONED
)
READ_ONLY_STAGES = frozenset({CutoverStage.PLAN_PREFLIGHT})

# The July 19 incident MID is permanently abandoned: never resumable as a normal
# cutover and ineligible for rollback_finalize. Do not retrofit its artifacts.
PERMANENTLY_ABANDONED_MIDS: frozenset[str] = frozenset({"cutover20260719T163633Z"})


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
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
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


def _rollback_finalize_locked(journal: "CutoverJournal | None") -> str | None:
    """Return a reason string if the journal is owned by the rollback-finalize
    lifecycle (any marker present), else None.

    Once any of these is recorded, ONLY rollback_finalize status/reconciliation
    may proceed — every other mutating route (ordinary stages, a second physical
    rollback, abort_before_swap) must refuse. This is what prevents a second
    rollback call from ever exchanging the original and candidate again.

    Presence semantics (``is not None``), not truthiness: an empty dict, list,
    string, or otherwise malformed ``rollback_intent`` / finalize intent still
    locks ordinary progression. Malformed PR-C safety booleans also lock.
    """
    if journal is None:
        return None
    bool_problem = _prc_safety_boolean_problem(journal)
    if bool_problem is not None:
        return f"malformed safety boolean ({bool_problem})"
    if journal.abandoned is True or journal.stage == CutoverStage.ABANDONED.value:
        return "journal is terminal ABANDONED"
    if journal.rollback_finalized is True:
        return "rollback already finalized"
    if journal.rollback_verified is True:
        return "verified rollback recorded"
    if journal.rollback_identity_capture_failed is True:
        return "post-rollback identity capture failed (manual recovery required)"
    if journal.rollback_finalize_intent is not None:
        return "rollback_finalize intent recorded"
    # Any durable rollback_intent (including malformed {}) is itself a lifecycle
    # lock — even if the later post-exchange journal write failed.
    if journal.rollback_intent is not None:
        return "rollback_intent recorded (pre-PoNR rollback lifecycle owns journal)"
    if journal.rollback_finalize_service_intent is not None:
        return "rollback_finalize service intent recorded"
    return None


def companion_paths(path: Path) -> list[Path]:
    return [Path(str(path) + s) for s in ("-wal", "-shm", "-journal")]


def wal_path(path: Path) -> Path:
    return Path(str(path) + "-wal")


def shm_path(path: Path) -> Path:
    return Path(str(path) + "-shm")


def _sidecar_identity_snapshot(
    adapters: "CutoverAdapters", path: Path
) -> dict[str, Any]:
    """Authoritative-identity snapshot of a WAL/SHM sidecar (never size/mtime).

    The presence gate is a pathname existence check only; the authoritative
    identity/type comes from ``capture_member_identity`` — non-following,
    non-blocking ``open`` + ``fstat`` (the same PR-B primitive that closes the
    FIFO/symlink/TOCTOU hole). No pathname-following ``stat`` is used for
    identity. Returns ``{"present": False}`` for an absent sidecar; any capture
    failure (symlink, FIFO/device, lstat→open replacement, fstat identity
    replacement, unreadable) is recorded as ``ambiguous`` so finalize fails
    closed rather than trusting it.
    """
    if not adapters.path_exists(path):
        return {"present": False}
    try:
        ident = adapters.capture_member_identity(path)
    except (ArtifactPermissionError, CutoverError):
        return {"present": True, "ambiguous": True}
    except Exception:  # noqa: BLE001
        return {"present": True, "ambiguous": True}
    return {
        "present": True,
        "device": int(ident["device"]),
        "inode": int(ident["inode"]),
        "mode": int(ident.get("mode", 0)),
        "nlink": int(ident.get("nlink", 1)),
    }


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


# Structured health-timer activity buckets. Anything not a clean active/inactive
# text (or a systemctl invocation failure) is AMBIGUOUS and must fail closed to
# manual recovery — never silently treated as "inactive".
_TIMER_KNOWN_ACTIVE = frozenset({"active"})
_TIMER_KNOWN_INACTIVE = frozenset({"inactive", "dead"})


def classify_health_timer_activity(
    *, returncode: int, is_active_text: str | None
) -> dict[str, Any]:
    """Classify the API health TIMER into known-active / known-inactive / ambiguous.

    ``systemctl is-active`` returns the state text and a return code. A 127
    sentinel (our ``_default_systemctl_text`` OS-error/timeout marker) is an
    invocation failure and is ambiguous. Any unexpected text — ``failed``,
    ``activating``, ``deactivating``, ``reloading``, unknown, or empty — is
    ambiguous. Only the exact known texts map to a definite state.
    """
    text = (is_active_text or "").strip().lower()
    if int(returncode) == 127:
        active = inactive = False
        ambiguous = True
    elif text in _TIMER_KNOWN_ACTIVE:
        active, inactive, ambiguous = True, False, False
    elif text in _TIMER_KNOWN_INACTIVE:
        active, inactive, ambiguous = False, True, False
    else:
        active, inactive, ambiguous = False, False, True
    return {
        "state": "active" if active else "inactive" if inactive else "ambiguous",
        "active": active,
        "inactive": inactive,
        "ambiguous": ambiguous,
        "is_active_text": text or None,
        "returncode": int(returncode),
    }


def _boot_policy_journal_fields_problem(journal: CutoverJournal) -> str | None:
    return boot_policy_journal_problem(
        maintenance_id=journal.maintenance_id,
        api_enabled=journal.pre_maintenance_api_enabled,
        timer_enabled=journal.pre_maintenance_health_timer_enabled,
        intent=journal.maintenance_boot_policy_intent,
        active=journal.maintenance_boot_policy_active,
        restored=journal.maintenance_boot_policy_restored,
    )


def _boot_policy_lifecycle_for(journal: CutoverJournal) -> tuple[str | None, str | None]:
    return boot_policy_lifecycle(
        maintenance_id=journal.maintenance_id,
        api_enabled=journal.pre_maintenance_api_enabled,
        timer_enabled=journal.pre_maintenance_health_timer_enabled,
        intent=journal.maintenance_boot_policy_intent,
        active=journal.maintenance_boot_policy_active,
        restored=journal.maintenance_boot_policy_restored,
    )


def _require_coherent_boot_policy(
    journal: CutoverJournal,
    *,
    allow_pristine: bool = False,
    require_restored: bool = False,
    require_suppression_active: bool = False,
) -> str:
    """Validate coherent PR-D lifecycle; return the lifecycle label or fail.

    ``allow_pristine``: abort before STOP_READERS may see pristine state.
    ``require_restored``: terminal COMPLETED / late abort / ABANDONED.
    ``require_suppression_active``: hazardous maintenance stages.
    """
    lifecycle, problem = _boot_policy_lifecycle_for(journal)
    if problem is not None or lifecycle is None:
        _fail(
            f"malformed maintenance boot policy ({problem or 'unknown'})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"boot_policy_problem": problem},
        )
        raise AssertionError("unreachable")
    if lifecycle == LIFECYCLE_PRISTINE:
        if allow_pristine and not require_restored and not require_suppression_active:
            return lifecycle
        _fail(
            "maintenance boot policy looks pristine after STOP_READERS began",
            category=CutoverFailureCategory.SAFETY,
            evidence={"boot_policy_lifecycle": lifecycle},
        )
    if require_suppression_active and lifecycle != LIFECYCLE_SUPPRESSION_ACTIVE:
        _fail(
            "maintenance boot policy not in coherent suppression_active state",
            category=CutoverFailureCategory.SAFETY,
            evidence={"boot_policy_lifecycle": lifecycle},
        )
    if require_restored and lifecycle != LIFECYCLE_RESTORED:
        _fail(
            "terminal cutover exit requires coherent verified boot-policy restoration",
            category=CutoverFailureCategory.SAFETY,
            evidence={"boot_policy_lifecycle": lifecycle},
        )
    return lifecycle


def _require_readers_unambiguously_inactive(
    adapters: CutoverAdapters,
    *,
    api_base_url: str | None = None,
    context: str,
) -> None:
    """Fail-closed reader inactivity via structured classifiers (never binary).

    API must be unambiguously stopped through ``api_activity()``; timer must be
    unambiguously inactive through ``health_timer_activity()``. Rejects
    ambiguity, running/listener/PID presence, genuine failure/restart signals,
    rc=127, empty/unknown, failed/activating/deactivating/reloading timer text.
    """
    api_act = adapters.api_activity(api_base_url=api_base_url)
    if not isinstance(api_act, dict) or api_act.get("ambiguous"):
        _fail(
            f"API activity ambiguous ({context})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"context": context, "api_state": (
                api_act.get("state") if isinstance(api_act, dict) else None
            )},
        )
    if api_act.get("running") or not api_act.get("stopped"):
        _fail(
            f"API not unambiguously stopped ({context})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"context": context, "api_state": api_act.get("state")},
        )
    if api_act.get("genuine_failure_signal"):
        _fail(
            f"API genuine failure/restart signal ({context})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"context": context, "sub_state": api_act.get("sub_state")},
        )
    if int(api_act.get("main_pid") or 0) > 0 or bool(api_act.get("listener_present")):
        _fail(
            f"API PID/listener still present ({context})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"context": context},
        )
    timer_act = adapters.health_timer_activity()
    if not isinstance(timer_act, dict) or timer_act.get("ambiguous"):
        _fail(
            f"health timer activity ambiguous ({context})",
            category=CutoverFailureCategory.SAFETY,
            evidence={
                "context": context,
                "timer_state": (
                    timer_act.get("state") if isinstance(timer_act, dict) else None
                ),
                "returncode": (
                    timer_act.get("returncode") if isinstance(timer_act, dict) else None
                ),
            },
        )
    if not timer_act.get("inactive") or timer_act.get("active"):
        _fail(
            f"health timer not unambiguously inactive ({context})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"context": context, "timer_state": timer_act.get("state")},
        )


def _probe_unit_enablement(adapters: CutoverAdapters, unit: str) -> dict[str, Any]:
    if unit not in BOOT_POLICY_UNITS:
        _fail(
            f"refusing enablement probe for unrelated unit {unit}",
            category=CutoverFailureCategory.SAFETY,
        )
    classified = adapters.unit_enablement(unit)
    if not isinstance(classified, dict) or classified.get("ambiguous"):
        _fail(
            f"ambiguous systemd enablement for {unit}",
            category=CutoverFailureCategory.SAFETY,
            evidence={
                "unit": unit,
                "enablement_state": (
                    classified.get("state") if isinstance(classified, dict) else None
                ),
            },
            recovery=(
                "Inspect systemctl --user is-enabled for the unit; only exact "
                "enabled/disabled results are accepted. Manual recovery required."
            ),
        )
    if classified.get("enabled") is True and classified.get("disabled") is False:
        return classified
    if classified.get("disabled") is True and classified.get("enabled") is False:
        return classified
    _fail(
        f"inconsistent enablement classification for {unit}",
        category=CutoverFailureCategory.SAFETY,
        evidence={"unit": unit},
    )
    raise AssertionError("unreachable")


def _require_boot_policy_suppression_active(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
) -> None:
    """Fail closed unless persistent disablement is verified for both units."""
    _require_coherent_boot_policy(journal, require_suppression_active=True)
    if journal.maintenance_boot_policy_active is not True:
        _fail(
            "maintenance boot policy not active",
            category=CutoverFailureCategory.SAFETY,
            recovery=(
                "STOP_READERS must establish persistent disablement before "
                "hazardous maintenance stages proceed."
            ),
        )
    if journal.maintenance_boot_policy_restored is True:
        _fail(
            "maintenance boot policy already restored during maintenance",
            category=CutoverFailureCategory.SAFETY,
        )
    for unit in (API_HEALTH_TIMER, API_SERVICE):
        classified = _probe_unit_enablement(adapters, unit)
        if classified.get("disabled") is not True:
            _fail(
                f"boot suppression lost or reversed for {unit}",
                category=CutoverFailureCategory.SAFETY,
                evidence={
                    "unit": unit,
                    "enablement_state": classified.get("state"),
                },
                recovery=(
                    "An external enable during maintenance reopens production SQLite "
                    "on WSL/user-manager restart. Re-disable and retry, or abort."
                ),
            )


def _should_restore_boot_policy(journal: CutoverJournal) -> bool:
    """True when coherent non-pristine lifecycle requires restore/verify.

    Validates lifecycle **before** deciding to skip. Malformed / empty /
    unknown intents fail closed rather than being treated as pristine.
    """
    lifecycle, problem = _boot_policy_lifecycle_for(journal)
    if problem is not None or lifecycle is None:
        _fail(
            f"malformed maintenance boot policy ({problem or 'unknown'})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"boot_policy_problem": problem},
        )
    return boot_policy_requires_restoration(lifecycle)


def _establish_boot_policy_suppression(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
    *,
    inject: Callable[[str], None],
    api_base_url: str | None = None,
) -> None:
    """Capture enablement, disable+verify timer then API, then stop both units.

    Crash after durable intent (or after either disable) leaves reconcilable
    journal state. Retry observes live enablement and continues; it never
    overwrites a malformed or unrelated intent. Inactivity verification uses
    structured ``api_activity`` / ``health_timer_activity`` classifiers — never
    binary ``service_state()``.
    """
    problem = _boot_policy_journal_fields_problem(journal)
    if problem is not None:
        _fail(
            f"malformed maintenance boot policy before suppress ({problem})",
            category=CutoverFailureCategory.SAFETY,
            evidence={"boot_policy_problem": problem},
        )
    if journal.maintenance_boot_policy_restored is True:
        _fail(
            "refusing STOP_READERS after boot policy already restored",
            category=CutoverFailureCategory.SAFETY,
        )

    if journal.maintenance_boot_policy_active is True:
        _require_boot_policy_suppression_active(adapters, journal)
    else:
        intent = journal.maintenance_boot_policy_intent
        if intent is not None:
            ip = boot_policy_intent_problem(
                intent, maintenance_id=journal.maintenance_id
            )
            if ip is not None:
                _fail(
                    f"malformed boot policy intent ({ip}); refusing overwrite",
                    category=CutoverFailureCategory.SAFETY,
                    evidence={"boot_policy_intent_problem": ip},
                )
            if intent.get("action") != BOOT_POLICY_SUPPRESS_ACTION:
                _fail(
                    "unrelated boot policy intent present; refusing overwrite",
                    category=CutoverFailureCategory.SAFETY,
                )
            # Reconcile captured booleans from intent; never rewrite them.
            if journal.pre_maintenance_api_enabled is None:
                journal.pre_maintenance_api_enabled = bool(intent["api_was_enabled"])
            if journal.pre_maintenance_health_timer_enabled is None:
                journal.pre_maintenance_health_timer_enabled = bool(
                    intent["timer_was_enabled"]
                )
            if (
                journal.pre_maintenance_api_enabled != intent.get("api_was_enabled")
                or journal.pre_maintenance_health_timer_enabled
                != intent.get("timer_was_enabled")
            ):
                _fail(
                    "boot policy intent disagrees with journal pre-enablement",
                    category=CutoverFailureCategory.SAFETY,
                )
        else:
            # Fresh capture — probe live enablement before any mutation.
            timer_cls = _probe_unit_enablement(adapters, API_HEALTH_TIMER)
            api_cls = _probe_unit_enablement(adapters, API_SERVICE)
            timer_enabled = bool(timer_cls.get("enabled"))
            api_enabled = bool(api_cls.get("enabled"))
            journal.pre_maintenance_api_enabled = api_enabled
            journal.pre_maintenance_health_timer_enabled = timer_enabled
            journal.maintenance_boot_policy_intent = {
                "action": BOOT_POLICY_SUPPRESS_ACTION,
                "maintenance_id": journal.maintenance_id,
                "started_at_utc": _iso_now(),
                "api_was_enabled": api_enabled,
                "timer_was_enabled": timer_enabled,
                "phase": "capture",
            }
            write_journal(adapters, journal_path, journal)
            inject("boot_policy_intent")

        # Disable + verify health timer first, then API.
        intent = journal.maintenance_boot_policy_intent
        assert isinstance(intent, dict)
        timer_live = _probe_unit_enablement(adapters, API_HEALTH_TIMER)
        if timer_live.get("enabled") is True:
            intent["phase"] = "disable_timer"
            journal.maintenance_boot_policy_intent = intent
            write_journal(adapters, journal_path, journal)
            adapters.disable_unit(API_HEALTH_TIMER)
            inject("boot_policy_timer_disabled")
            after = _probe_unit_enablement(adapters, API_HEALTH_TIMER)
            if after.get("disabled") is not True:
                _fail(
                    f"disable unverified for {API_HEALTH_TIMER}",
                    category=CutoverFailureCategory.APPLY,
                )
        elif timer_live.get("disabled") is not True:
            _fail(
                f"timer enablement not disabled after suppress path for {API_HEALTH_TIMER}",
                category=CutoverFailureCategory.SAFETY,
            )

        api_live = _probe_unit_enablement(adapters, API_SERVICE)
        if api_live.get("enabled") is True:
            intent["phase"] = "disable_api"
            journal.maintenance_boot_policy_intent = intent
            write_journal(adapters, journal_path, journal)
            adapters.disable_unit(API_SERVICE)
            inject("boot_policy_api_disabled")
            after_api = _probe_unit_enablement(adapters, API_SERVICE)
            if after_api.get("disabled") is not True:
                _fail(
                    f"disable unverified for {API_SERVICE}",
                    category=CutoverFailureCategory.APPLY,
                )
        elif api_live.get("disabled") is not True:
            _fail(
                f"API enablement not disabled after suppress path for {API_SERVICE}",
                category=CutoverFailureCategory.SAFETY,
            )

        intent["phase"] = "stop_units"
        journal.maintenance_boot_policy_intent = intent
        write_journal(adapters, journal_path, journal)

    # Stop and unambiguously verify inactive via structured classifiers.
    adapters.stop_health_timer()
    adapters.stop_api()
    _require_readers_unambiguously_inactive(
        adapters,
        api_base_url=api_base_url,
        context="stop_readers_after_stop",
    )
    # Final enablement gate — both probes go through consistency checks.
    timer_final = _probe_unit_enablement(adapters, API_HEALTH_TIMER)
    api_final = _probe_unit_enablement(adapters, API_SERVICE)
    if timer_final.get("disabled") is not True or api_final.get("disabled") is not True:
        _fail(
            "units not persistently disabled before STOP_READERS completion",
            category=CutoverFailureCategory.SAFETY,
        )
    intent = journal.maintenance_boot_policy_intent
    if isinstance(intent, dict):
        intent["phase"] = "active"
        journal.maintenance_boot_policy_intent = intent
    journal.maintenance_boot_policy_active = True
    journal.services_stopped = True


def _restore_one_unit_enablement(
    adapters: CutoverAdapters,
    unit: str,
    *,
    want_enabled: bool,
) -> None:
    live = _probe_unit_enablement(adapters, unit)
    if want_enabled:
        if live.get("enabled") is True:
            return
        adapters.enable_unit(unit)
        after = _probe_unit_enablement(adapters, unit)
        if after.get("enabled") is not True:
            _fail(
                f"enable unverified for {unit}",
                category=CutoverFailureCategory.APPLY,
            )
        return
    if live.get("disabled") is True:
        return
    adapters.disable_unit(unit)
    after_dis = _probe_unit_enablement(adapters, unit)
    if after_dis.get("disabled") is not True:
        _fail(
            f"disable unverified for {unit} during restore",
            category=CutoverFailureCategory.APPLY,
        )


def _restore_boot_policy(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
    *,
    inject: Callable[[str], None],
) -> dict[str, Any]:
    """Exact enablement restoration (intent-before / verified-success-after).

    Validates coherent lifecycle **before** deciding to skip. Restores only the
    two maintenance units. Partial restoration retains typed intent and must not
    claim COMPLETED / ABANDONED / successful abort.
    """
    # Lifecycle validation precedes any skip decision (empty/unknown intents
    # fail closed rather than looking pristine).
    if not _should_restore_boot_policy(journal):
        return {"boot_policy_restored": False, "boot_policy_skipped": True}

    want_api = bool(journal.pre_maintenance_api_enabled)
    want_timer = bool(journal.pre_maintenance_health_timer_enabled)

    if journal.maintenance_boot_policy_restored is True:
        # Idempotent verify: live state must already match captured policy.
        _restore_one_unit_enablement(
            adapters, API_HEALTH_TIMER, want_enabled=want_timer
        )
        _restore_one_unit_enablement(adapters, API_SERVICE, want_enabled=want_api)
        _require_coherent_boot_policy(journal, require_restored=True)
        return {
            "boot_policy_restored": True,
            "pre_maintenance_api_enabled": want_api,
            "pre_maintenance_health_timer_enabled": want_timer,
        }

    intent = journal.maintenance_boot_policy_intent
    assert isinstance(intent, dict)

    # Intent-before: typed restore intent (or upgrade suppress → restore).
    # Keep active=True throughout restoration_in_progress (even if suppress
    # crashed before the active flag was set).
    if intent.get("action") != BOOT_POLICY_RESTORE_ACTION:
        journal.maintenance_boot_policy_active = True
        journal.maintenance_boot_policy_intent = {
            "action": BOOT_POLICY_RESTORE_ACTION,
            "maintenance_id": journal.maintenance_id,
            "started_at_utc": _iso_now(),
            "api_was_enabled": want_api,
            "timer_was_enabled": want_timer,
            "phase": "restore_timer",
        }
        write_journal(adapters, journal_path, journal)
        inject("boot_policy_restore_intent")

    intent = journal.maintenance_boot_policy_intent
    assert isinstance(intent, dict)
    if journal.maintenance_boot_policy_active is not True:
        journal.maintenance_boot_policy_active = True
        write_journal(adapters, journal_path, journal)

    # Restore timer first, then API (same order as suppress).
    if intent.get("phase") != "verified":
        intent["phase"] = "restore_timer"
        journal.maintenance_boot_policy_intent = intent
        write_journal(adapters, journal_path, journal)
        _restore_one_unit_enablement(
            adapters, API_HEALTH_TIMER, want_enabled=want_timer
        )
        inject("boot_policy_timer_restored")

        intent["phase"] = "restore_api"
        journal.maintenance_boot_policy_intent = intent
        write_journal(adapters, journal_path, journal)
        _restore_one_unit_enablement(adapters, API_SERVICE, want_enabled=want_api)
        inject("boot_policy_api_restored")

        # Verified-success-after.
        timer_v = _probe_unit_enablement(adapters, API_HEALTH_TIMER)
        api_v = _probe_unit_enablement(adapters, API_SERVICE)
        if bool(timer_v.get("enabled")) != want_timer:
            _fail(
                f"timer enablement restore mismatch for {API_HEALTH_TIMER}",
                category=CutoverFailureCategory.VERIFY,
            )
        if bool(api_v.get("enabled")) != want_api:
            _fail(
                f"API enablement restore mismatch for {API_SERVICE}",
                category=CutoverFailureCategory.VERIFY,
            )
        intent["phase"] = "verified"
        journal.maintenance_boot_policy_intent = intent
        journal.maintenance_boot_policy_restored = True
        journal.maintenance_boot_policy_active = False
        write_journal(adapters, journal_path, journal)
        inject("boot_policy_restored")

    _require_coherent_boot_policy(journal, require_restored=True)
    return {
        "boot_policy_restored": True,
        "pre_maintenance_api_enabled": want_api,
        "pre_maintenance_health_timer_enabled": want_timer,
    }


def _boot_policy_status_fragment(journal: CutoverJournal) -> dict[str, Any]:
    return sanitized_boot_policy_status(
        api_enabled=journal.pre_maintenance_api_enabled,
        timer_enabled=journal.pre_maintenance_health_timer_enabled,
        intent=journal.maintenance_boot_policy_intent,
        active=journal.maintenance_boot_policy_active,
        restored=journal.maintenance_boot_policy_restored,
    )


def _default_api_process_identity(api_base_url: str | None) -> dict[str, Any]:
    """Best-effort durable process identity for the API unit (MainPID + generation).

    ``InvocationID`` is a per-start systemd generation identifier; combined with
    ``MainPID`` it lets rollback-finalize prove that a still-running API is the
    exact process it started (verified-success-after ownership), rather than a
    later externally started process at the same PID.
    """
    _, show = _default_systemctl_text(
        ["--user", "show", API_SERVICE, "-p", "MainPID", "-p", "InvocationID"]
    )
    main_pid = 0
    invocation_id: str | None = None
    for line in show.splitlines():
        if line.startswith("MainPID="):
            try:
                main_pid = int(line.split("=", 1)[1].strip() or "0")
            except ValueError:
                main_pid = 0
        elif line.startswith("InvocationID="):
            invocation_id = line.split("=", 1)[1].strip() or None
    return {"main_pid": main_pid, "invocation_id": invocation_id}


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
    # --- PR-C: rollback finalize (abandoned != completed). All additive. ---
    # Structured proof that a verified pre-PoNR physical rollback restored the
    # ORIGINAL database at the production path. Never inferred from stage/notes/
    # swap_direction/pathnames alone.
    rollback_verified: bool = False
    rollback_proof: dict[str, Any] | None = None
    # Set True only when the authoritative fstat identity capture of the restored
    # main FAILED after the physical exchange. It is a durable, conservative lock:
    # normal progression is refused and rollback_finalize routes to manual
    # recovery — never verified rollback, never normal cutover.
    rollback_identity_capture_failed: bool = False
    rollback_finalize_intent: dict[str, Any] | None = None
    # Terminal ABANDONED bookkeeping (distinct from normal completion).
    abandoned: bool = False
    rollback_finalized: bool = False
    rollback_finalized_at_utc: str | None = None
    # Writers resumed on the RESTORED ORIGINAL — deliberately separate from the
    # normal-cutover writer_resume_started / writers_resumed (new-DB) semantics.
    rollback_original_writers_resumed: bool = False
    # HISTORICAL truth: a pause-removal mutation happened at least once. These are
    # never reset to False just because writers were later re-paused.
    rollback_finalize_mail_resumed: bool = False
    rollback_finalize_mirror_resumed: bool = False
    # Durable intent-before / success-after for each pause removal.
    rollback_finalize_mail_resume_intent: bool = False
    rollback_finalize_mirror_resume_intent: bool = False
    rollback_finalize_mail_observed_ok: bool = False
    rollback_finalize_mirror_observed_ok: bool = False
    # CURRENT state (separate from historical truth): are the pauses currently
    # absent (writers live) and were they re-paused during a recovery?
    rollback_finalize_mail_pause_absent: bool = False
    rollback_finalize_mirror_pause_absent: bool = False
    rollback_finalize_writers_repaused: bool = False
    # Explicit post-mail fingerprint recorded once legitimate writes may begin, so
    # a retry after real mail activity does not demand the pristine original fp.
    rollback_finalize_post_mail_fingerprint: str | None = None
    # Ownership of any API this finalize started (separate from smoke ownership).
    rollback_finalize_started_api: bool = False
    # An API this finalize started ONLY to verify health when it was inactive
    # pre-maintenance; it must be owned-stopped again before terminal.
    rollback_finalize_owned_temp_api: bool = False
    rollback_finalize_service_intent: dict[str, Any] | None = None
    # T3: durable process/unit evidence (MainPID + systemd InvocationID generation)
    # captured ONLY after a start was externally issued and verified running, so a
    # crash after start-intent but before verified ownership can never authorize
    # stopping a later, different API process at the same PID.
    rollback_finalize_api_owner: dict[str, Any] | None = None
    # T6: last CURRENT pause observation was KNOWN (list_writers succeeded). A
    # failed observation sets these False so terminal coherence can require known,
    # verified pause state rather than trusting a stale absent/present boolean.
    rollback_finalize_mail_pause_known: bool = True
    rollback_finalize_mirror_pause_known: bool = True
    # Sidecar (WAL/SHM) lifecycle proof captured immediately after the physical
    # rollback, used to fail closed on unexplained candidate sidecars.
    rollback_sidecar_proof: dict[str, Any] | None = None
    # Pre-maintenance service policy captured before STOP_READERS stops them.
    pre_maintenance_api_active: bool | None = None
    pre_maintenance_health_timer_active: bool | None = None
    # PR-D: persistent systemd enablement policy (boot auto-start suppression).
    # Exact pre-maintenance is-enabled states for the two maintenance units only.
    pre_maintenance_api_enabled: bool | None = None
    pre_maintenance_health_timer_enabled: bool | None = None
    maintenance_boot_policy_intent: dict[str, Any] | None = None
    maintenance_boot_policy_active: bool = False
    maintenance_boot_policy_restored: bool = False
    original_mode: int | None = None
    original_uid: int | None = None
    original_gid: int | None = None
    # Canonical main/WAL/SHM permission capture (PR-B). Absent on legacy journals.
    artifact_permissions: dict[str, Any] | None = None
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
    artifact_permissions: dict[str, Any] | None = None


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
    def list_writers(
        self,
        production: Path | None = None,
        *,
        reports_dir: Path | None = None,
    ) -> WriterInventory: ...
    def service_state(self) -> ServiceState: ...
    def api_activity(self, *, api_base_url: str | None = None) -> dict[str, Any]: ...
    def health_timer_activity(self) -> dict[str, Any]: ...
    def api_process_identity(
        self, *, api_base_url: str | None = None
    ) -> dict[str, Any]: ...
    def stop_api(self) -> None: ...
    def start_api(self) -> None: ...
    def stop_health_timer(self) -> None: ...
    def start_health_timer(self) -> None: ...
    def unit_enablement(self, unit: str) -> dict[str, Any]: ...
    def disable_unit(self, unit: str) -> None: ...
    def enable_unit(self, unit: str) -> None: ...
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
    def capture_member_identity(self, path: Path) -> dict[str, Any]: ...
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
    systemctl_text: Callable[[list[str]], tuple[int, str]] | None = None
    api_activity_probe: Callable[[str | None], dict[str, Any]] | None = None
    api_process_probe: Callable[[str | None], dict[str, Any]] | None = None
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
        # Preserve the previous version WITHOUT ever moving the live journal away
        # before the replacement. A hardlink keeps the canonical path present at
        # ALL times, so the path is always the OLD complete journal (before the
        # replace) or the NEW complete journal (after it) — never a path-missing
        # window merely to retain ``.prev``. Previous-version preservation is
        # best-effort evidence and must never block the atomic replace.
        if path.is_file() and not path.is_symlink():
            try:
                if prev.exists() or prev.is_symlink():
                    prev.unlink()
                os.link(path, prev)
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

    def list_writers(
        self,
        production: Path | None = None,
        *,
        reports_dir: Path | None = None,
    ) -> WriterInventory:
        # When a sealed reports directory is supplied (rollback_finalize /
        # attempt_rollback), it is authoritative for pause/lock observation —
        # never touch pauses in one directory and verify another via settings.
        if reports_dir is not None:
            reports = reports_dir
        else:
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

    def health_timer_activity(self) -> dict[str, Any]:
        """Structured (active/inactive/ambiguous) health-timer classification."""
        fn = self.systemctl_text or _default_systemctl_text
        rc, text = fn(["--user", "is-active", API_HEALTH_TIMER])
        return classify_health_timer_activity(returncode=rc, is_active_text=text)

    def api_process_identity(
        self, *, api_base_url: str | None = None
    ) -> dict[str, Any]:
        if self.api_process_probe is not None:
            return self.api_process_probe(api_base_url)
        return _default_api_process_identity(api_base_url)

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

    def unit_enablement(self, unit: str) -> dict[str, Any]:
        if unit not in BOOT_POLICY_UNITS:
            _fail(
                f"refusing enablement probe for unrelated unit {unit}",
                category=CutoverFailureCategory.SAFETY,
            )
        fn = self.systemctl_text or _default_systemctl_text
        rc, text = fn(["--user", "is-enabled", unit])
        return classify_unit_enablement(returncode=rc, is_enabled_text=text)

    def disable_unit(self, unit: str) -> None:
        if unit not in BOOT_POLICY_UNITS:
            _fail(
                f"refusing to disable unrelated unit {unit}",
                category=CutoverFailureCategory.SAFETY,
            )
        fn = self.systemctl or _default_systemctl
        if fn(["--user", "disable", unit]) != 0:
            _fail(
                f"failed to disable {unit}",
                category=CutoverFailureCategory.APPLY,
            )

    def enable_unit(self, unit: str) -> None:
        if unit not in BOOT_POLICY_UNITS:
            _fail(
                f"refusing to enable unrelated unit {unit}",
                category=CutoverFailureCategory.SAFETY,
            )
        fn = self.systemctl or _default_systemctl
        if fn(["--user", "enable", unit]) != 0:
            _fail(
                f"failed to enable {unit}",
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
        try:
            result = run_online_backup(
                BackupOptions(
                    source=source,
                    destination=dest,
                    apply=True,
                    allow_same_filesystem=False,
                    fail_if_source_fingerprint_changes=True,
                    # PR-E: mandatory FD observation while source connection is open.
                    require_fd_observation=True,
                )
            )
        except BackupError as exc:
            _raise_cutover_from_backup_error(exc)
            raise  # pragma: no cover — _raise always raises
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
        out: dict[str, Any] = {
            "completed": True,
            "destination_basename": dest.name,
            "destination_fingerprint": dest_fp,
            "source_fingerprint_before": source_fp_before,
            "source_fingerprint_after": source_fp_after,
            "manifest_completed": True,
            "method": man.get("method"),
            "raw_keys": sorted(result.keys())[:20],
        }
        # Sanitized FD aggregate only (never raw FD/error evidence).
        fd_obs = result.get("fd_observation")
        if isinstance(fd_obs, dict):
            out["fd_observation"] = {
                k: fd_obs[k]
                for k in (
                    "schema_version",
                    "observation_phases",
                    "member_roles_present",
                    "member_roles_observed",
                    "trusted_locking_count",
                    "blocker_count",
                    "ambiguous_count",
                    "verdict",
                )
                if k in fd_obs
            }
        return out

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

    def capture_member_identity(self, path: Path) -> dict[str, Any]:
        try:
            return filesystem_capture_member_identity(path)
        except ArtifactPermissionError as exc:
            _map_artifact_error(exc)
            raise AssertionError("unreachable") from exc

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
        """Open path, fstat-match device+inode, then fchmod (not path chmod).

        O_NOFOLLOW refuses a symlink swapped at the path; O_NONBLOCK prevents a
        FIFO/device swapped in from blocking open(); O_CLOEXEC avoids FD leaks.
        A post-open fstat proves the opened FD is a regular file before fchmod.
        """
        flags = os.O_RDONLY
        for _flag_name in ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"):
            flags |= getattr(os, _flag_name, 0)
        fd = os.open(str(path), flags)
        try:
            st = os.fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode):
                _fail(
                    "refusing chmod: opened member is not a regular file",
                    category=CutoverFailureCategory.SAFETY,
                    recovery=(
                        "A non-regular file occupies the artifact path. Inspect "
                        "before retrying the write barrier."
                    ),
                )
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
        flags = os.O_RDWR
        for _flag_name in ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"):
            flags |= getattr(os, _flag_name, 0)
        try:
            fd = os.open(str(path), flags)
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


def _map_artifact_error(exc: ArtifactPermissionError) -> None:
    category = {
        "preflight": CutoverFailureCategory.PREFLIGHT,
        "safety": CutoverFailureCategory.SAFETY,
        "apply": CutoverFailureCategory.APPLY,
        "verify": CutoverFailureCategory.VERIFY,
        "ambiguous": CutoverFailureCategory.AMBIGUOUS,
    }.get(exc.category, CutoverFailureCategory.SAFETY)
    _fail(
        str(exc),
        category=category,
        recovery=exc.recovery,
        evidence=sanitize_evidence(exc.evidence) if exc.evidence else None,
    )


def _journal_artifact(journal: CutoverJournal) -> ArtifactPermissions | None:
    try:
        return load_artifact_permissions(journal.artifact_permissions)
    except ArtifactPermissionError as exc:
        _map_artifact_error(exc)
        raise AssertionError("unreachable") from exc


def _persist_artifact(
    adapters: CutoverAdapters,
    journal_path: Path,
    journal: CutoverJournal,
    artifact: ArtifactPermissions,
    *,
    private: PrivatePlanPaths | None = None,
) -> None:
    journal.artifact_permissions = dump_artifact_permissions(artifact)
    if artifact.main.present and artifact.main.mode is not None:
        journal.original_mode = int(artifact.main.mode)
    if artifact.main.uid is not None:
        journal.original_uid = int(artifact.main.uid)
    if artifact.main.gid is not None:
        journal.original_gid = int(artifact.main.gid)
    write_journal(adapters, journal_path, journal)
    if private is not None:
        private.artifact_permissions = dump_artifact_permissions(artifact)
        private.original_mode = journal.original_mode
        private.original_uid = journal.original_uid
        private.original_gid = journal.original_gid
        write_private_paths(adapters, journal_path, private)


def _resolve_artifact_for_barrier_ops(
    journal: CutoverJournal,
) -> ArtifactPermissions | None:
    """Load artifact_permissions, or main-only legacy view. Never invent companions."""
    art = _journal_artifact(journal)
    if art is not None:
        return art
    if journal.original_mode is None:
        return None
    return legacy_artifact_from_original_mode(
        original_mode=int(journal.original_mode),
        production_device=journal.production_device,
        production_inode=journal.production_inode,
        original_uid=journal.original_uid,
        original_gid=journal.original_gid,
    )


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
    if opts.maintenance_id in PERMANENTLY_ABANDONED_MIDS:
        _fail(
            "maintenance ID is permanently abandoned and must never be resumed "
            "or finalized",
            category=CutoverFailureCategory.SAFETY,
            recovery=(
                "This incident MID is closed forever. Use a fresh maintenance ID "
                "for any future cutover; do not operate on its artifacts."
            ),
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


def _validate_production_path_binding(
    opts: CutoverOptions, adapters: CutoverAdapters, production: Path
) -> None:
    """Canonical production path / type / symlink / samefile binding.

    Everything in ``_validate_production_identity`` EXCEPT the (mutable) live
    fingerprint compare, so callers whose fingerprint contract differs (e.g.
    ``rollback_finalize``, where content may legitimately change after writers
    resume) can still enforce the exact same non-synthetic canonical binding.
    """
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


def _validate_production_identity(
    opts: CutoverOptions, adapters: CutoverAdapters, production: Path
) -> str:
    _validate_production_path_binding(opts, adapters, production)
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


def _sanitized_backup_failure_evidence(
    detail: dict[str, Any] | None,
) -> dict[str, Any]:
    """Allowlisted BackupError.detail fragments for cutover evidence (no paths/PIDs)."""
    out: dict[str, Any] = {}
    if not isinstance(detail, dict):
        return out
    op = detail.get("operational_error")
    if isinstance(op, dict):
        safe_op: dict[str, Any] = {}
        for key in (
            "schema",
            "phase",
            "category",
            "sqlite_errorcode",
            "sqlite_errorname",
            "retryable",
            "recovery",
        ):
            if key in op:
                safe_op[key] = op[key]
        if safe_op:
            out["operational_error"] = safe_op
    fd = detail.get("fd_observation")
    if isinstance(fd, dict):
        safe_fd: dict[str, Any] = {}
        for key in (
            "schema_version",
            "phase",
            "observation_phases",
            "member_roles_present",
            "member_roles_observed",
            "trusted_locking_count",
            "blocker_count",
            "ambiguous_count",
            "verdict",
            "reason",
        ):
            if key in fd:
                safe_fd[key] = fd[key]
        if safe_fd:
            out["fd_observation"] = safe_fd
    return out


def _cutover_category_for_backup_error(
    exc: BackupError,
) -> CutoverFailureCategory:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    fd = detail.get("fd_observation") if isinstance(detail, dict) else None
    if isinstance(fd, dict):
        verdict = fd.get("verdict")
        if verdict == "ambiguous":
            return CutoverFailureCategory.AMBIGUOUS
        if verdict == "blocked":
            return CutoverFailureCategory.SAFETY
    op = detail.get("operational_error") if isinstance(detail, dict) else None
    if isinstance(op, dict):
        cat = op.get("category")
        if cat in {OPERR_BUSY_OR_LOCKED, OPERR_READONLY_WAL_LOCKING}:
            return CutoverFailureCategory.SAFETY
        if cat == OPERR_CANNOT_OPEN:
            return CutoverFailureCategory.APPLY
        if cat == OPERR_IO_ERROR:
            return CutoverFailureCategory.APPLY
        if cat == OPERR_CAPACITY:
            return CutoverFailureCategory.PREFLIGHT
        if cat == OPERR_INTERRUPTED:
            return CutoverFailureCategory.APPLY
    return CutoverFailureCategory.APPLY


def _raise_cutover_from_backup_error(exc: BackupError) -> None:
    """Convert a sanitized BackupError into CutoverError (never 'unexpected')."""
    if isinstance(exc, CutoverError):
        raise exc
    evidence = _sanitized_backup_failure_evidence(exc.detail)
    category = _cutover_category_for_backup_error(exc)
    recovery: str | None = None
    op = evidence.get("operational_error")
    if isinstance(op, dict) and isinstance(op.get("recovery"), str):
        recovery = str(op["recovery"])
    elif isinstance(evidence.get("fd_observation"), dict):
        recovery = "verify_backup_source_fd_identity_and_wal_shm_locking"
    raise CutoverError(
        str(exc),
        category=category,
        recovery=recovery,
        evidence=evidence,
        detail=dict(exc.detail) if isinstance(exc.detail, dict) else None,
    )


def _assert_writers_quiesced(
    adapters: CutoverAdapters,
    production: Path,
    *,
    allow_readonly_foreign: bool = False,
    reports_dir: Path | None = None,
) -> WriterInventory:
    writers = adapters.list_writers(production, reports_dir=reports_dir)
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
        # Own-PID alone is never a bypass: matching FDs must still satisfy
        # access rules. Trusted backup WAL/SHM locking is classified only
        # inside run_online_backup's active observation capability (PR-E).
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

        # PR-C lockout: once the rollback-finalize lifecycle owns the journal, the
        # ordinary cutover path is closed. A crash must never leave a rolled-back
        # MID able to continue toward COMPLETED. Only the dedicated
        # rollback_finalize operation (a separate entrypoint) may proceed.
        locked = _rollback_finalize_locked(journal)
        if locked is not None:
            _fail(
                f"ordinary execute/resume/apply refused: {locked}",
                category=CutoverFailureCategory.SAFETY,
                recovery=(
                    "Only --rollback-finalize may proceed (status or "
                    "reconciliation). The normal cutover path is permanently "
                    "locked for this MID."
                ),
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
            # Capture the pre-maintenance service *activity* policy BEFORE stopping
            # anything so a later rollback_finalize restores exactly the services
            # that were active (and never enables one that was disabled before
            # maintenance). Enablement (boot auto-start) is handled separately by
            # the PR-D boot-policy suppress path below.
            if (
                journal.pre_maintenance_api_active is None
                or journal.pre_maintenance_health_timer_active is None
            ):
                pre_state = adapters.service_state()
                journal.pre_maintenance_api_active = bool(pre_state.api_active)
                journal.pre_maintenance_health_timer_active = bool(
                    pre_state.health_timer_active
                )
                write_journal(adapters, journal_path, journal)
            _establish_boot_policy_suppression(
                adapters,
                journal,
                journal_path,
                inject=_inject,
                api_base_url=opts.api_base_url,
            )
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("stop_readers")
            return _stage_report(
                opts,
                journal,
                {
                    "services": asdict(adapters.service_state()),
                    "boot_policy": _boot_policy_status_fragment(journal),
                },
            )

        if stage == CutoverStage.QUIESCE_WAL:
            _expect_journal_stage(journal, CutoverStage.STOP_READERS)
            _require_boot_policy_suppression_active(adapters, journal)
            _assert_writers_quiesced(adapters, production)
            _require_readers_unambiguously_inactive(
                adapters,
                api_base_url=opts.api_base_url,
                context="quiesce_wal_before_checkpoint",
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
            _require_readers_unambiguously_inactive(
                adapters,
                api_base_url=opts.api_base_url,
                context="quiesce_wal_after_checkpoint",
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
            _require_boot_policy_suppression_active(adapters, journal)
            if not journal.wal_quiesced:
                _fail("WAL not quiesced", category=CutoverFailureCategory.SAFETY)
            _assert_writers_quiesced(adapters, production)
            services = adapters.service_state()
            if services.api_active or services.health_timer_active:
                _fail(
                    "API/health must be stopped before write barrier",
                    category=CutoverFailureCategory.SAFETY,
                )
            ident = adapters.path_identity(production)
            expected_dev = int(journal.production_device or ident["device"])
            expected_ino = int(journal.production_inode or ident["inode"])
            if int(ident["device"]) != expected_dev or int(ident["inode"]) != expected_ino:
                _fail(
                    "production device/inode drift before write barrier",
                    category=CutoverFailureCategory.SAFETY,
                )
            try:
                artifact = capture_artifact(adapters, production, phase="pre_swap")
            except ArtifactPermissionError as exc:
                _map_artifact_error(exc)
                raise AssertionError("unreachable") from exc
            if (
                int(artifact.main.device or -1) != expected_dev
                or int(artifact.main.inode or -1) != expected_ino
            ):
                _fail(
                    "captured main identity disagrees with journal production identity",
                    category=CutoverFailureCategory.SAFETY,
                )
            journal.original_mode = int(artifact.main.mode or 0)
            journal.original_uid = int(artifact.main.uid or 0)
            journal.original_gid = int(artifact.main.gid or 0)
            journal.permission_intent = {
                "action": "apply_production_write_barrier",
                "original_mode": journal.original_mode,
                "barrier_mode": int(journal.original_mode) & ~0o222,
                "expected_device": expected_dev,
                "expected_inode": expected_ino,
                "members_present": {
                    ROLE_MAIN: True,
                    ROLE_WAL: bool(artifact.wal.present),
                    ROLE_SHM: bool(artifact.shm.present),
                },
            }
            private = load_private_paths(adapters, journal_path)
            if private is None:
                _fail(
                    "missing private plan for permission record",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            assert private is not None
            private.original_mode = journal.original_mode
            private.original_uid = journal.original_uid
            private.original_gid = journal.original_gid
            private.production_device = expected_dev
            private.production_inode = expected_ino
            # Durable capture BEFORE first chmod.
            _persist_artifact(
                adapters, journal_path, journal, artifact, private=private
            )
            _inject("permission_intent_barrier")

            def _persist(art: ArtifactPermissions) -> None:
                _persist_artifact(
                    adapters, journal_path, journal, art, private=private
                )

            try:
                apply_write_barrier(
                    adapters,
                    production,
                    artifact,
                    persist=_persist,
                    inject=_inject,
                )
            except ArtifactPermissionError as exc:
                _map_artifact_error(exc)
                raise AssertionError("unreachable") from exc

            if adapters.try_open_writable(production):
                _fail(
                    "write barrier failed: production still opens writable",
                    category=CutoverFailureCategory.VERIFY,
                    recovery=(
                        "Barrier incomplete. Restore via abort_before_swap / "
                        "reconcile_permission_barrier before any writer resume."
                    ),
                )
            for role in (ROLE_WAL, ROLE_SHM):
                path = member_path(production, role)
                if adapters.path_exists(path) and adapters.try_open_writable(path):
                    _fail(
                        f"write barrier failed: {role} still opens writable",
                        category=CutoverFailureCategory.VERIFY,
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
            _persist_artifact(
                adapters, journal_path, journal, artifact, private=private
            )
            _inject("apply_os_write_barrier")
            return _stage_report(
                opts,
                journal,
                {
                    "production_write_barrier_active": True,
                    "barrier_mode": int(journal.original_mode) & ~0o222,
                    "barrier_device": expected_dev,
                    "barrier_inode": expected_ino,
                    "artifact_members_changed": list(artifact.members_changed),
                    "threat_model_note": (
                        "Ordinary accidental ad-hoc SQLite writes are blocked; "
                        "malicious/root bypass is outside operator threat model."
                    ),
                },
            )

        if stage == CutoverStage.CREATE_CURRENT_BACKUP:
            _expect_journal_stage(journal, CutoverStage.APPLY_OS_WRITE_BARRIER)
            _require_boot_policy_suppression_active(adapters, journal)
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
            _require_boot_policy_suppression_active(adapters, journal)
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
            _require_boot_policy_suppression_active(adapters, journal)
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
            _require_boot_policy_suppression_active(adapters, journal)
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
            _require_boot_policy_suppression_active(adapters, journal)
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
                art = _resolve_artifact_for_barrier_ops(journal)
                if art is not None:
                    try:
                        if not art.post_swap_main:
                            record_post_swap_main(art, adapters, production)
                        recapture_post_swap_companions(
                            art, adapters, production, source="readonly_smoke"
                        )
                    except ArtifactPermissionError as exc:
                        _map_artifact_error(exc)
                        raise AssertionError("unreachable") from exc
                    journal.artifact_permissions = dump_artifact_permissions(art)
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
            _require_boot_policy_suppression_active(adapters, journal)
            if not journal.smoke_ok:
                _fail("smoke not ok", category=CutoverFailureCategory.SAFETY)
            if not adapters.service_state().api_active:
                adapters.start_api()
            adapters.start_health_timer()
            # RESUME_SERVICES may start the units but must NOT re-enable boot
            # auto-start; verify persistent disablement still holds.
            _require_boot_policy_suppression_active(adapters, journal)
            journal.services_stopped = False
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_services")
            return _stage_report(
                opts,
                journal,
                {
                    "services": asdict(adapters.service_state()),
                    "boot_policy": _boot_policy_status_fragment(journal),
                },
            )

        if stage == CutoverStage.RESUME_WRITERS_PONR:
            _expect_journal_stage(journal, CutoverStage.RESUME_SERVICES)
            _require_boot_policy_suppression_active(adapters, journal)
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
            _require_boot_policy_suppression_active(adapters, journal)
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
            artifact = _resolve_artifact_for_barrier_ops(journal)
            if artifact is None:
                _fail(
                    "artifact permission state missing",
                    category=CutoverFailureCategory.AMBIGUOUS,
                )
            assert artifact is not None
            if not artifact.post_swap_main:
                try:
                    record_post_swap_main(artifact, adapters, production)
                except ArtifactPermissionError as exc:
                    _map_artifact_error(exc)
                    raise AssertionError("unreachable") from exc
            journal.permission_intent = {
                "action": "restore_production_writable_mode",
                "target_mode": journal.original_mode,
            }
            write_journal(adapters, journal_path, journal)
            _inject("restore_mode_intent")
            try:
                restore_evidence = restore_post_swap_for_writer_resume(
                    adapters,
                    production,
                    artifact,
                    original_mode=int(journal.original_mode),
                    inject=_inject,
                )
            except ArtifactPermissionError as exc:
                # Preserve barrier ownership / pauses; do not clear flags.
                journal.artifact_permissions = dump_artifact_permissions(artifact)
                write_journal(adapters, journal_path, journal)
                _map_artifact_error(exc)
                raise AssertionError("unreachable") from exc
            journal.writable_mode_restored = True
            journal.production_write_barrier_active = False
            journal.permission_intent = None
            journal.artifact_permissions = dump_artifact_permissions(artifact)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_restore_mode")
            return _stage_report(
                opts,
                journal,
                {
                    "writable_mode_restored": True,
                    "artifact_writable": restore_evidence,
                },
            )

        if stage == CutoverStage.RESUME_WRITERS_MAIL:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_RESTORE_MODE)
            _require_boot_policy_suppression_active(adapters, journal)
            if not journal.writer_resume_started or not journal.writable_mode_restored:
                _fail(
                    "PoNR and writable restore required before removing mail pause",
                    category=CutoverFailureCategory.SAFETY,
                )
            try:
                verify_artifact_writable_for_resume(
                    adapters,
                    production,
                    _journal_artifact(journal),
                    legacy_original_mode=journal.original_mode,
                )
            except ArtifactPermissionError as exc:
                _map_artifact_error(exc)
                raise AssertionError("unreachable") from exc
            path = mail_pause_path(reports)
            if adapters.path_exists(path):
                adapters.unlink(path)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_mail")
            return _stage_report(opts, journal, {"mail_pause_removed": True})

        if stage == CutoverStage.RESUME_WRITERS_OBSERVE_MAIL:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_MAIL)
            _require_boot_policy_suppression_active(adapters, journal)
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
            if failure is None:
                try:
                    verify_artifact_writable_for_resume(
                        adapters,
                        production,
                        _journal_artifact(journal),
                        legacy_original_mode=journal.original_mode,
                    )
                except ArtifactPermissionError as exc:
                    msg = str(exc).lower()
                    if "wal" in msg:
                        failure = "wal_not_writable"
                    elif "shm" in msg:
                        failure = "shm_not_writable"
                    else:
                        failure = "artifact_not_writable"
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
            _require_boot_policy_suppression_active(adapters, journal)
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
            _require_boot_policy_suppression_active(adapters, journal)
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
            _require_boot_policy_suppression_active(adapters, journal)
            journal.writers_resumed = True
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            _inject("resume_writers_commit")
            return _stage_report(opts, journal, {"writers_resumed": True})

        if stage == CutoverStage.COMPLETED:
            _expect_journal_stage(journal, CutoverStage.RESUME_WRITERS_COMMIT)
            if journal.abandoned or journal.rollback_verified:
                _fail(
                    "refusing COMPLETED on an abandoned/rolled-back MID "
                    "(abandoned != completed)",
                    category=CutoverFailureCategory.SAFETY,
                )
            # Exact enablement restoration immediately BEFORE committing COMPLETED.
            boot = _restore_boot_policy(
                adapters, journal, journal_path, inject=_inject
            )
            if boot.get("boot_policy_skipped"):
                _fail(
                    "refusing COMPLETED with pristine boot policy "
                    "(STOP_READERS never established suppression)",
                    category=CutoverFailureCategory.SAFETY,
                    evidence={"boot_policy": boot},
                )
            _require_coherent_boot_policy(journal, require_restored=True)
            journal.stage = stage.value
            write_journal(adapters, journal_path, journal)
            return _stage_report(
                opts,
                journal,
                {
                    "completed": True,
                    "boot_policy": _boot_policy_status_fragment(journal),
                },
            )

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
    _require_boot_policy_suppression_active(adapters, journal)
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
    # Post-swap: production path now holds the former staging inode. Bind
    # subsequent chmod/restore to the live identity, not the pre-swap main.
    live_ident = adapters.path_identity(production)
    journal.production_device = int(live_ident["device"])
    journal.production_inode = int(live_ident["inode"])
    art = _resolve_artifact_for_barrier_ops(journal)
    if art is not None:
        try:
            record_post_swap_main(art, adapters, production)
            # Companions at production-wal/shm are path-bound, not exchanged with
            # the main. Capture what is actually present without transferring
            # pre-swap companion metadata onto a different inode.
            recapture_post_swap_companions(
                art, adapters, production, source="atomic_swap"
            )
        except ArtifactPermissionError as exc:
            _map_artifact_error(exc)
            raise AssertionError("unreachable") from exc
        journal.artifact_permissions = dump_artifact_permissions(art)
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
            "post_swap_production_device": journal.production_device,
            "post_swap_production_inode": journal.production_inode,
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
    # T5: enforce git, canonical production path/type/symlink, and confined
    # journal-location bindings BEFORE any rollback intent or physical exchange.
    _require_git_match(opts, adapters)
    _validate_production_path_binding(opts, adapters, production)
    _validate_journal_location(adapters, journal_path, production)
    with adapters.acquire_exclusive_lock(production, opts.maintenance_id):
        journal = load_journal(adapters, journal_path)
        if journal is None:
            _fail(
                "missing journal for rollback",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        assert journal is not None
        # Never exchange twice: if the rollback-finalize lifecycle already owns the
        # journal, refuse a second physical rollback outright.
        locked = _rollback_finalize_locked(journal)
        if locked is not None:
            _fail(
                f"second rollback refused: {locked}; only --rollback-finalize may "
                "proceed",
                category=CutoverFailureCategory.SAFETY,
                recovery=(
                    "The original has already been restored and recorded. Use "
                    "--rollback-finalize to reconcile/finish; do not exchange again."
                ),
            )
        if journal.writer_resume_started is not False or journal.writers_resumed is not False:
            _fail(
                "automatic rollback refused after writer_resume_started; "
                "use incident reconciliation",
                category=CutoverFailureCategory.SAFETY,
            )
        bool_problem = _prc_safety_boolean_problem(journal)
        if bool_problem is not None:
            _fail(
                f"malformed safety boolean before rollback ({bool_problem})",
                category=CutoverFailureCategory.SAFETY,
                evidence={"boolean_problem": bool_problem},
            )
        # T5: bind the sealed private plan (production/reports/journal) BEFORE any
        # rollback intent or exchange; a bad plan is a sanitized HARD refusal.
        sealed_problem, sealed = _sealed_paths_problem(
            adapters, opts, production, journal_path
        )
        if sealed_problem is not None or sealed is None:
            _fail_sealed_binding(sealed_problem or "private_plan_unbound")
        assert sealed is not None
        reports = sealed["reports"]
        if not journal.pre_cutover_basename:
            _fail(
                "journal missing pre_cutover_basename",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        # T5: require a SAFE single-component pre_cutover_basename BEFORE deriving
        # the sibling path with ``Path.with_name`` (guards traversal/absolute).
        if not _safe_basename(journal.pre_cutover_basename):
            _fail(
                "journal pre_cutover_basename is not a safe single component",
                category=CutoverFailureCategory.SAFETY,
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

        # Pause observation uses the SEALED reports directory (never settings).
        _assert_writers_quiesced(adapters, production, reports_dir=reports)
        # Fail-closed structured service classifiers — command failure, ambiguity,
        # reloading/activating/deactivating means NO exchange (never binary
        # service_state which maps every nonzero systemctl result to inactive).
        api_act = adapters.api_activity(api_base_url=opts.api_base_url)
        if api_act.get("ambiguous") or not api_act.get("stopped"):
            _fail(
                "API must be unambiguously stopped before rollback",
                category=CutoverFailureCategory.SAFETY,
            )
        timer_act = adapters.health_timer_activity()
        if timer_act.get("ambiguous") or not timer_act.get("inactive"):
            _fail(
                "health timer must be unambiguously inactive before rollback",
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

        # T5: the retained original must be a REGULAR, NON-SYMLINK file whose
        # authoritative identity is capturable BEFORE the exchange. Retain that
        # capture and compare its device/inode with the approved-plan original
        # identity so a replacement/drift is refused BEFORE exchange.
        if adapters.is_symlink(pre):
            _fail(
                "retained pre_cutover original is not a regular capturable file",
                category=CutoverFailureCategory.SAFETY,
            )
        if adapters.is_symlink(production):
            _fail(
                "current production candidate is not a regular capturable file",
                category=CutoverFailureCategory.SAFETY,
            )
        pre_ident = _authoritative_identity(adapters, pre)
        prod_ident = _authoritative_identity(adapters, production)
        if pre_ident is None:
            _fail(
                "retained pre_cutover original is not a regular capturable file",
                category=CutoverFailureCategory.SAFETY,
            )
        if prod_ident is None:
            _fail(
                "current production candidate is not a regular capturable file",
                category=CutoverFailureCategory.SAFETY,
            )
        assert pre_ident is not None and prod_ident is not None
        # Complete ApprovedPlan validator (sanitized) before intent/exchange —
        # no dataclass construction / int conversion / missing-key exception may
        # escape unsanitized.
        plan_problem = _approved_plan_problem(
            journal,
            opts,
            adapters=adapters,
            sealed=sealed,
            production=production,
        )
        if plan_problem is not None:
            _fail(
                f"approved_plan invalid before rollback ({plan_problem})",
                category=CutoverFailureCategory.SAFETY,
                evidence={"approved_plan_problem": plan_problem},
            )
        plan = journal.approved_plan
        assert isinstance(plan, dict)
        if int(pre_ident["device"]) != int(plan["production_device"]) or int(
            pre_ident["inode"]
        ) != int(plan["production_inode"]):
            _fail(
                "retained pre_cutover original identity does not match "
                "approved-plan production identity (replacement/drift)",
                category=CutoverFailureCategory.VERIFY,
            )
        # Bind the current candidate (production path) to the ATOMIC_SWAP journal
        # identity written at swap time.
        if not _is_exact_int(journal.production_device) or not _is_exact_int(
            journal.production_inode
        ):
            _fail(
                "ATOMIC_SWAP journal candidate identity missing before rollback",
                category=CutoverFailureCategory.VERIFY,
            )
        if int(prod_ident["device"]) != int(journal.production_device) or int(
            prod_ident["inode"]
        ) != int(journal.production_inode):
            _fail(
                "current candidate identity does not match ATOMIC_SWAP journal "
                "identity (replacement/drift)",
                category=CutoverFailureCategory.VERIFY,
            )
        if not _safe_basename(journal.staging_basename):
            _fail(
                "journal staging_basename is not a safe single component",
                category=CutoverFailureCategory.SAFETY,
            )
        if not _is_nonempty_str(journal.candidate_fingerprint):
            _fail(
                "journal candidate_fingerprint missing before rollback",
                category=CutoverFailureCategory.VERIFY,
            )
        if not (
            _is_nonempty_str(expected_old_fingerprint)
            and _is_nonempty_str(expected_new_fingerprint)
        ):
            _fail(
                "rollback fingerprints must be non-empty",
                category=CutoverFailureCategory.VERIFY,
            )
        if expected_old_fingerprint == expected_new_fingerprint:
            _fail(
                "rollback old/new fingerprints must differ",
                category=CutoverFailureCategory.VERIFY,
            )
        # Bind operator approval to the CURRENT/NEW side; plan + candidate to the
        # original/new sides respectively (mirrors finalize proof invariants).
        if opts.expected_production_fingerprint != expected_new_fingerprint:
            _fail(
                "operator expected fingerprint must equal current/new production",
                category=CutoverFailureCategory.SAFETY,
            )
        if plan["production_fingerprint"] != expected_old_fingerprint:
            _fail(
                "approved-plan production fingerprint must equal restored original",
                category=CutoverFailureCategory.VERIFY,
            )
        if journal.candidate_fingerprint != expected_new_fingerprint:
            _fail(
                "journal candidate fingerprint must equal current/new production",
                category=CutoverFailureCategory.VERIFY,
            )

        journal.rollback_intent = {
            "old_fingerprint": expected_old_fingerprint,
            "new_fingerprint": expected_new_fingerprint,
            "pre_cutover_basename": pre.name,
            "retained_device": int(pre_ident["device"]),
            "retained_inode": int(pre_ident["inode"]),
            "candidate_device": int(prod_ident["device"]),
            "candidate_inode": int(prod_ident["inode"]),
        }
        write_journal(adapters, journal_path, journal)
        # Inject point for crash-after-intent tests. Intent remains durable.
        if opts.fail_after == "rollback_intent_written":
            _fail(
                "injected failure after rollback_intent_written",
                category=CutoverFailureCategory.APPLY,
            )
        # Synthetic/test hook: mutate after the durable intent write but before
        # the immediate pre-exchange recapture (models TOCTOU replacement).
        hook = getattr(adapters, "after_rollback_intent_hook", None)
        if callable(hook):
            hook(adapters)

        # Close the pre-exchange identity window: immediately recapture BOTH
        # exchange members authoritatively and revalidate against the already
        # approved values. Drift/replacement/symlink/FIFO/capture failure leaves
        # the durable lifecycle lock in place, performs no exchange, and requires
        # manual recovery. Do NOT clear the durable intent to permit a retry.
        if adapters.is_symlink(pre) or adapters.is_symlink(production):
            _fail(
                "exchange member became a symlink after rollback_intent; "
                "manual recovery required (intent retained)",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        pre_ident2 = _authoritative_identity(adapters, pre)
        prod_ident2 = _authoritative_identity(adapters, production)
        if pre_ident2 is None or prod_ident2 is None:
            _fail(
                "authoritative recapture of exchange members failed after "
                "rollback_intent; manual recovery required (intent retained)",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        assert pre_ident2 is not None and prod_ident2 is not None
        intent = journal.rollback_intent
        assert isinstance(intent, dict)
        if int(pre_ident2["device"]) != int(plan["production_device"]) or int(
            pre_ident2["inode"]
        ) != int(plan["production_inode"]):
            _fail(
                "retained original identity drifted after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )
        if int(pre_ident2["device"]) != int(intent["retained_device"]) or int(
            pre_ident2["inode"]
        ) != int(intent["retained_inode"]):
            _fail(
                "retained original identity drifted after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )
        if int(pre_ident2["device"]) != int(pre_ident["device"]) or int(
            pre_ident2["inode"]
        ) != int(pre_ident["inode"]):
            _fail(
                "retained original identity replaced after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )
        if int(prod_ident2["device"]) != int(journal.production_device) or int(
            prod_ident2["inode"]
        ) != int(journal.production_inode):
            _fail(
                "current candidate identity drifted after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )
        if int(prod_ident2["device"]) != int(intent["candidate_device"]) or int(
            prod_ident2["inode"]
        ) != int(intent["candidate_inode"]):
            _fail(
                "current candidate identity drifted after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )
        if int(prod_ident2["device"]) != int(prod_ident["device"]) or int(
            prod_ident2["inode"]
        ) != int(prod_ident["inode"]):
            _fail(
                "current candidate identity replaced after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )
        if adapters.fingerprint(pre) != expected_old_fingerprint:
            _fail(
                "retained original fingerprint drifted after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )
        if adapters.fingerprint(production) != expected_new_fingerprint:
            _fail(
                "current production fingerprint drifted after rollback_intent; "
                "manual recovery required (intent retained, no exchange)",
                category=CutoverFailureCategory.VERIFY,
            )

        # PR-D: verified persistent disablement before any physical exchange.
        _require_boot_policy_suppression_active(adapters, journal)

        adapters.rename_exchange(production, pre)
        post_hook = getattr(adapters, "after_rename_exchange_hook", None)
        if callable(post_hook):
            post_hook(adapters)
        adapters.fsync_dir(production.parent)
        journal.swap_direction = "rollback_pre_cutover_to_production"
        restored_fp = adapters.fingerprint(production)
        journal.production_fingerprint = restored_fp
        journal.smoke_ok = False
        journal.exchange_completed = True
        journal.old_production_retained = True
        journal.stage = CutoverStage.ATOMIC_SWAP.value
        journal.notes.append("rolled_back_before_writers_resumed")

        # The original database is now back at the production path. Bind future
        # rollback_finalize to its live device/inode and record STRUCTURED proof
        # (never inferred from stage/notes/swap_direction/pathnames alone).
        #
        # T3: capture the restored main through the authoritative non-following
        # open+fstat identity (exactly like WAL/SHM), never pathname stat. If the
        # capture fails AFTER the physical exchange we must not fabricate a proof
        # identity; instead record a durable conservative lock so normal
        # progression is refused and finalize routes to manual recovery.
        restored_ident = _authoritative_identity(adapters, production)
        if restored_ident is None:
            journal.rollback_identity_capture_failed = True
            journal.notes.append("rollback_identity_capture_failed_post_exchange")
            try:
                write_journal(adapters, journal_path, journal)
            except Exception:  # noqa: BLE001
                pass
            _fail(
                "authoritative identity capture of the restored main failed "
                "after the rollback exchange; normal progression is locked and "
                "manual reconciliation is required",
                category=CutoverFailureCategory.AMBIGUOUS,
                recovery=(
                    "The physical rollback exchange completed but the restored "
                    "database identity could not be authoritatively captured. "
                    "Inspect the production path (regular file, no FIFO/symlink) "
                    "and reconcile manually; do not resume normal cutover."
                ),
            )
            raise AssertionError("unreachable")
        journal.production_device = int(restored_ident["device"])
        journal.production_inode = int(restored_ident["inode"])
        # All plan/proof source fields were fully validated BEFORE the exchange,
        # so this proof build is pure serialization (no int()/schema failure can
        # first occur here).
        plan = journal.approved_plan
        assert isinstance(plan, dict)
        proof = {
            "schema": 1,
            "maintenance_id": journal.maintenance_id,
            "old_fingerprint": expected_old_fingerprint,
            "new_fingerprint": expected_new_fingerprint,
            "restored_fingerprint": restored_fp,
            "restored_device": int(restored_ident["device"]),
            "restored_inode": int(restored_ident["inode"]),
            "plan_production_device": int(plan["production_device"]),
            "plan_production_inode": int(plan["production_inode"]),
            "candidate_basename": journal.staging_basename,
            "retained_candidate_basename": pre.name,
            "candidate_not_production": restored_fp == expected_old_fingerprint,
            "before_writer_resume": True,
            "verified_at_utc": _iso_now(),
        }
        journal.rollback_proof = proof
        # Sidecar lifecycle proof: RENAME_EXCHANGE moves only main, so whatever
        # WAL/SHM sit at the production paths immediately after rollback are the
        # authoritative "expected" state finalize must reconcile against. Record
        # exact identity or explicit absence — never invent or delete.
        journal.rollback_sidecar_proof = {
            "schema": 1,
            "captured_at_utc": _iso_now(),
            "wal": _sidecar_identity_snapshot(adapters, wal_path(production)),
            "shm": _sidecar_identity_snapshot(adapters, shm_path(production)),
        }
        # T5: set rollback_verified ONLY if the restored fingerprint, authoritative
        # identity, and approved-plan identity invariants ALL pass post-exchange.
        # Otherwise persist a conservative post-exchange manual-recovery lock so
        # finalize can never treat an inconsistent rollback as verified.
        post_ok = (
            restored_fp == expected_old_fingerprint
            and int(restored_ident["device"]) == int(plan["production_device"])
            and int(restored_ident["inode"]) == int(plan["production_inode"])
            and expected_old_fingerprint != expected_new_fingerprint
        )
        if not post_ok:
            journal.rollback_verified = False
            journal.rollback_identity_capture_failed = True
            journal.notes.append("post_exchange_rollback_invariant_failed")
            try:
                write_journal(adapters, journal_path, journal)
            except Exception:  # noqa: BLE001
                pass
            _fail(
                "post-exchange rollback invariants failed; a conservative "
                "manual-recovery lock was set and normal progression is refused",
                category=CutoverFailureCategory.AMBIGUOUS,
                recovery=(
                    "The rollback exchange completed but the restored identity/"
                    "fingerprint did not match the approved plan. Reconcile "
                    "manually via --rollback-finalize; do not resume normal cutover."
                ),
            )
            raise AssertionError("unreachable")
        journal.rollback_verified = True
        # Rollback-finalize is now the only supported forward operation.
        write_journal(adapters, journal_path, journal)
        return sanitize_evidence(
            {
                "rolled_back": True,
                "rollback_verified": True,
                "production_fingerprint": journal.production_fingerprint,
                "writer_resume_started": False,
                "writers_resumed": False,
                "next_required": "rollback_finalize",
            }
        )


def _rollback_finalize_status(
    opts: CutoverOptions,
    journal: CutoverJournal,
    *,
    already: bool,
    smoke: dict[str, Any] | None = None,
    restore: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured ABANDONED status — never a success/soak signal."""
    return sanitize_evidence(
        {
            "schema_version": CUTOVER_SCHEMA_VERSION,
            "mode": "sqlite_production_cutover_rollback_finalize",
            "maintenance_id": opts.maintenance_id,
            "stage": journal.stage,
            "abandoned": bool(journal.abandoned),
            "rollback_finalized": bool(journal.rollback_finalized),
            "rollback_finalized_at_utc": journal.rollback_finalized_at_utc,
            "already_finalized": bool(already),
            # Explicit non-success semantics.
            "cutover_succeeded": False,
            "completed": journal.stage == CutoverStage.COMPLETED.value,
            "soak_eligible": False,
            "waves_unblocked": False,
            # Writers were resumed against the RESTORED ORIGINAL, not the new DB.
            "writers_resumed_against": "restored_original",
            "rollback_original_writers_resumed": bool(
                journal.rollback_original_writers_resumed
            ),
            "normal_writers_resumed": bool(journal.writers_resumed),
            "smoke": smoke,
            "artifact_writable": restore,
            "journal": sanitize_evidence(journal.to_dict()),
            "captured_at_utc": _iso_now(),
        }
    )


def _rollback_finalize_manual_recovery(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
    production: Path,
    reports: Path,
    reason: str,
    *,
    opts: CutoverOptions | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    """Fail safe: reassert both pauses + prove quiescence, retain ownership.

    Preserves ``rollback_finalize_intent`` and all HISTORICAL mutation truth so a
    later retry reconciles reality. Records CURRENT mail/mirror pause presence
    INDEPENDENTLY (a partial success where one re-pause worked and the other
    failed is persisted exactly), never resets historical ``*_resumed`` fields,
    and stops a demonstrably OWNED temporary API BEFORE the final quiescence
    assessment (so the evidence describes the post-cleanup state). Triggering,
    cleanup, and journal-write failures are preserved separately and sanitized.
    Never records ABANDONED/COMPLETED/success.
    """
    cleanup_errors: list[str] = []
    # T5: reassert each pause INDEPENDENTLY so a single-file failure is exact.
    mail_touch_failed = False
    mirror_touch_failed = False
    try:
        adapters.touch(mail_pause_path(reports))
    except Exception as exc:  # noqa: BLE001
        mail_touch_failed = True
        cleanup_errors.append(f"touch_mail:{type(exc).__name__}")
    try:
        adapters.touch(mirror_pause_path(reports))
    except Exception as exc:  # noqa: BLE001
        mirror_touch_failed = True
        cleanup_errors.append(f"touch_mirror:{type(exc).__name__}")
    # T5: stop a demonstrably OWNED temporary API BEFORE the final quiescence
    # assessment, then rerun quiescence so evidence reflects post-cleanup state.
    temp_api_stopped: bool | None = None
    if opts is not None and journal.rollback_finalize_owned_temp_api is True:
        # Temporary stops require the exact temp flag plus matching live
        # MainPID/InvocationID. Never stop on a truthy non-boolean flag or on
        # started/smoke ownership substituting for temp.
        try:
            act = adapters.api_activity(api_base_url=opts.api_base_url)
            if act.get("stopped"):
                temp_api_stopped = True
                _clear_api_ownership(journal)
                journal.rollback_finalize_service_intent = None
            elif act.get("ambiguous"):
                cleanup_errors.append("temp_api_ambiguous_not_stopped")
            elif not _api_ownership_proven(
                adapters, journal, opts, roles=frozenset({"temp"})
            ):
                cleanup_errors.append("temp_api_ownership_unproven_not_stopped")
            else:
                conflict = _replace_service_intent(
                    journal, {"action": "cleanup_stop_owned_temp_api"}
                )
                if conflict is not None:
                    cleanup_errors.append(conflict)
                else:
                    write_journal(adapters, journal_path, journal)
                    adapters.stop_api()
                    temp_api_stopped = _rf_api_stopped(adapters, opts)
                    if temp_api_stopped:
                        _clear_api_ownership(journal)
                        journal.rollback_finalize_service_intent = None
                    # else retain ownership + intent — stopped state unverified
        except Exception as exc:  # noqa: BLE001
            cleanup_errors.append(f"temp_api_stop:{type(exc).__name__}")
    # T5/T6: record mail/mirror CURRENT presence/absence INDEPENDENTLY. If the
    # observation is unavailable (list_writers failed) the state is UNKNOWN — we
    # do NOT leave a stale absent/present boolean pretending to be current truth.
    mail_present: bool | None = None
    mirror_present: bool | None = None
    mail_known = False
    mirror_known = False
    quiesced = False
    try:
        writers = adapters.list_writers(production, reports_dir=reports)
        mail_present = bool(writers.mail_pause_present)
        mirror_present = bool(writers.mirror_pause_present)
        mail_known = True
        mirror_known = True
    except Exception as exc:  # noqa: BLE001
        cleanup_errors.append(f"list_writers:{type(exc).__name__}")
    repaused = bool(mail_present) and bool(mirror_present) and mail_known and mirror_known
    if repaused:
        try:
            _assert_writers_quiesced(adapters, production, reports_dir=reports)
            quiesced = True
        except CutoverError:
            quiesced = False
        except Exception as exc:  # noqa: BLE001
            cleanup_errors.append(f"quiesce:{type(exc).__name__}")
    # CURRENT state truth only — HISTORICAL *_resumed truth is preserved. Each
    # pause-absence flag is set ONLY from a KNOWN independent observation; an
    # UNKNOWN observation marks the pause state not-known (terminal coherence
    # then refuses to treat it as verified) instead of trusting a stale boolean.
    journal.rollback_finalize_mail_pause_known = mail_known
    journal.rollback_finalize_mirror_pause_known = mirror_known
    if mail_known and mail_present is not None:
        journal.rollback_finalize_mail_pause_absent = not mail_present
    if mirror_known and mirror_present is not None:
        journal.rollback_finalize_mirror_pause_absent = not mirror_present
    if (mail_present or mirror_present) and (
        journal.rollback_finalize_mail_resumed
        or journal.rollback_finalize_mirror_resumed
    ):
        journal.rollback_finalize_writers_repaused = True
    journal.rollback_original_writers_resumed = False
    journal.notes.append(f"rollback_finalize_manual_recovery:{reason}")
    journal_write_failed = False
    try:
        write_journal(adapters, journal_path, journal)
    except Exception as exc:  # noqa: BLE001
        journal_write_failed = True
        cleanup_errors.append(f"journal_write:{type(exc).__name__}")
    ok = repaused and quiesced and not journal_write_failed

    def _pause_state(known: bool, present: bool | None) -> str:
        if not known or present is None:
            return "unknown"
        return "present" if present else "absent"

    ev = dict(evidence or {})
    ev.update(
        {
            "reason": reason,
            "repaused": repaused,
            "mail_pause_present": mail_present,
            "mirror_pause_present": mirror_present,
            "mail_pause_state": _pause_state(mail_known, mail_present),
            "mirror_pause_state": _pause_state(mirror_known, mirror_present),
            "mail_touch_failed": mail_touch_failed,
            "mirror_touch_failed": mirror_touch_failed,
            "writers_quiesced": quiesced,
            "temp_api_stopped": temp_api_stopped,
            "journal_write_failed": journal_write_failed,
            "cleanup_errors": cleanup_errors,
        }
    )
    recovery = (
        "Both writer pauses were reasserted and verified quiescent; "
        if ok
        else "Re-pause/quiescence/journal state is NOT fully verified — manual "
        "inspection required before any resume; "
    )
    _fail(
        "MANUAL_RECOVERY_REQUIRED during rollback_finalize",
        category=CutoverFailureCategory.AMBIGUOUS,
        recovery=recovery
        + "inspect service/identity state and re-run --rollback-finalize. "
        "ABANDONED was not recorded.",
        evidence=sanitize_evidence(ev),
    )


def _is_exact_int(value: Any) -> bool:
    """True only for a genuine int (excludes bool, str, float)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_exact_bool(value: Any) -> bool:
    """True only for a genuine bool (rejects 0/1/\"false\"/[]/{})."""
    return type(value) is bool


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _path_canonically_equal(left: Path, right: Path | str) -> bool:
    """Exact string equality or resolve-equal; never raises."""
    lp = left.expanduser()
    rp = Path(right).expanduser()
    if str(lp) == str(rp):
        return True
    try:
        return lp.resolve() == rp.resolve()
    except OSError:
        return False


# PR-C booleans used as locking / ownership / writer-PoNR / recovery / terminal
# decision gates. Malformed values must never bool-coerce into a successful path.
_PRC_ALWAYS_BOOL_FIELDS: tuple[str, ...] = (
    "abandoned",
    "rollback_finalized",
    "rollback_verified",
    "rollback_identity_capture_failed",
    "writer_resume_started",
    "writers_resumed",
    "rollback_original_writers_resumed",
    "rollback_finalize_mail_resumed",
    "rollback_finalize_mirror_resumed",
    "rollback_finalize_mail_observed_ok",
    "rollback_finalize_mirror_observed_ok",
    "rollback_finalize_mail_pause_known",
    "rollback_finalize_mirror_pause_known",
    "rollback_finalize_mail_pause_absent",
    "rollback_finalize_mirror_pause_absent",
    "rollback_finalize_mail_resume_intent",
    "rollback_finalize_mirror_resume_intent",
    "rollback_finalize_writers_repaused",
    "production_write_barrier_active",
    "smoke_started_api",
    "rollback_finalize_started_api",
    "rollback_finalize_owned_temp_api",
    "exchange_completed",
    "old_production_retained",
    "maintenance_boot_policy_active",
    "maintenance_boot_policy_restored",
)

_PRC_OPTIONAL_BOOL_FIELDS: tuple[str, ...] = (
    "pre_maintenance_api_active",
    "pre_maintenance_health_timer_active",
    "pre_maintenance_api_enabled",
    "pre_maintenance_health_timer_enabled",
)

# Stages after STOP_READERS through RESUME_WRITERS_COMMIT must keep boot
# suppression verified (persistently disabled) for both maintenance units.
_BOOT_POLICY_GATED_STAGES: frozenset[CutoverStage] = frozenset(
    {
        CutoverStage.QUIESCE_WAL,
        CutoverStage.APPLY_OS_WRITE_BARRIER,
        CutoverStage.CREATE_CURRENT_BACKUP,
        CutoverStage.COMPACT_TO_PRODUCTION_FS_STAGING,
        CutoverStage.VERIFY_CANDIDATE,
        CutoverStage.APPROVE_SWAP,
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
    }
)


def _prc_safety_boolean_problem(journal: "CutoverJournal") -> str | None:
    """Return a field name if any PR-C safety boolean is not an exact bool."""
    for name in _PRC_ALWAYS_BOOL_FIELDS:
        if not _is_exact_bool(getattr(journal, name, None)):
            return name
    for name in _PRC_OPTIONAL_BOOL_FIELDS:
        val = getattr(journal, name, None)
        if val is not None and not _is_exact_bool(val):
            return name
    return None


_SERVICE_INTENT_TIMER_PHASES = frozenset({"pre_api", "pre_health", "final"})
_SERVICE_INTENT_ACTIONS = frozenset(
    {
        "stop_timer",
        "start_timer",
        "stop_owned_api",
        "stop_owned_temp_api",
        "cleanup_stop_owned_temp_api",
        "start_api",
    }
)


def _finalize_intent_problem(
    intent: Any, journal: "CutoverJournal", opts: "CutoverOptions"
) -> str | None:
    """Validate rollback_finalize_intent as an exact typed object."""
    if not isinstance(intent, dict) or not intent:
        return "finalize_intent_empty_or_not_object"
    if intent.get("action") != "finalize_abandoned":
        return "finalize_intent_action"
    if intent.get("maintenance_id") != journal.maintenance_id:
        return "finalize_intent_mid_ne_journal"
    if intent.get("maintenance_id") != opts.maintenance_id:
        return "finalize_intent_mid_ne_opts"
    if not _is_nonempty_str(intent.get("started_at_utc")):
        return "finalize_intent_timestamp"
    return None


def _service_intent_problem(intent: Any) -> str | None:
    """Validate rollback_finalize_service_intent against an explicit allowlist."""
    if not isinstance(intent, dict) or not intent:
        return "service_intent_empty_or_not_object"
    action = intent.get("action")
    if action not in _SERVICE_INTENT_ACTIONS:
        return "service_intent_unknown_action"
    if action == "stop_timer":
        if intent.get("phase") not in _SERVICE_INTENT_TIMER_PHASES:
            return "service_intent_timer_phase"
    if action == "start_api":
        if not _is_exact_bool(intent.get("owned_temp")):
            return "service_intent_owned_temp"
    return None


def _replace_service_intent(
    journal: "CutoverJournal", intent: dict[str, Any]
) -> str | None:
    """Assign a service intent without overwriting an unrelated unresolved one."""
    problem = _service_intent_problem(intent)
    if problem is not None:
        return problem
    existing = journal.rollback_finalize_service_intent
    if existing is None:
        journal.rollback_finalize_service_intent = intent
        return None
    existing_problem = _service_intent_problem(existing)
    if existing_problem is not None:
        return existing_problem
    if existing.get("action") != intent.get("action"):
        return "unresolved_service_intent_conflict"
    # Same action: refresh typed fields (e.g. phase) in place.
    journal.rollback_finalize_service_intent = intent
    return None


def _safe_basename(name: Any) -> bool:
    """Reject anything that is not a single safe path component.

    Guards ``Path.with_name(...)`` against separators, traversal, absolute
    paths, NUL, and non-string values before it is ever used to derive a
    sibling artifact path.
    """
    if not isinstance(name, str) or name in ("", ".", ".."):
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    if name.startswith("/") or name.startswith("~"):
        return False
    try:
        return PurePosixPath(name).name == name and Path(name).name == name
    except (ValueError, OSError):
        return False


def _rollback_finalize_terminal_state(journal: CutoverJournal) -> str:
    """Classify terminal coherence: not_terminal | coherent_abandoned | mixed.

    ``coherent_abandoned`` demands a fully self-consistent terminal record — the
    terminal flags, the rollback-original writer completion, both mail/mirror
    observations, current pause-absence, resolved (no leftover) temporary API
    ownership + service intent, and both normal writer-resume fields false. Any
    contradictory partial/current state is ``mixed`` (→ MANUAL_RECOVERY). Proof
    typing and live identity are validated separately by the caller (needs
    adapters); this function only inspects journal-internal coherence.
    """
    has_marker = (
        journal.abandoned is True
        or journal.rollback_finalized is True
        or journal.stage == CutoverStage.ABANDONED.value
    )
    if not has_marker:
        return "not_terminal"
    if _prc_safety_boolean_problem(journal) is not None:
        return "mixed"
    coherent = (
        journal.stage == CutoverStage.ABANDONED.value
        and journal.abandoned is True
        and journal.rollback_finalized is True
        and _is_nonempty_str(journal.rollback_finalized_at_utc)
        and journal.rollback_verified is True
        and isinstance(journal.rollback_proof, dict)
        and journal.stage != CutoverStage.COMPLETED.value
        # Capture-failure lock must be exactly cleared for coherent ABANDONED.
        and journal.rollback_identity_capture_failed is False
        # Normal-cutover writer semantics must remain false.
        and journal.writer_resume_started is False
        and journal.writers_resumed is False
        # No unresolved finalize/service intent may remain.
        and journal.rollback_finalize_intent is None
        and journal.rollback_finalize_service_intent is None
        and journal.rollback_finalize_owned_temp_api is False
        # Rollback-original writers were resumed and both observations passed.
        and journal.rollback_original_writers_resumed is True
        and journal.rollback_finalize_mail_resumed is True
        and journal.rollback_finalize_mirror_resumed is True
        and journal.rollback_finalize_mail_observed_ok is True
        and journal.rollback_finalize_mirror_observed_ok is True
        # Current pause state must reflect resumed (pauses absent) AND be a
        # KNOWN, verified observation (T6): a stale/unknown pause state can never
        # qualify as coherent terminal ABANDONED.
        and journal.rollback_finalize_mail_pause_known is True
        and journal.rollback_finalize_mirror_pause_known is True
        and journal.rollback_finalize_mail_pause_absent is True
        and journal.rollback_finalize_mirror_pause_absent is True
        # Every historical resume implies its durable intent was recorded first.
        and journal.rollback_finalize_mail_resume_intent is True
        and journal.rollback_finalize_mirror_resume_intent is True
        # Post-mail fingerprint recorded at the instant writes could begin.
        and _is_nonempty_str(journal.rollback_finalize_post_mail_fingerprint)
        # Permission barrier fully resolved (no leftover intent / active barrier).
        and journal.permission_intent is None
        and journal.production_write_barrier_active is False
        # Typed sidecar proof must be structurally valid (exact schema/timestamp/
        # presence/ambiguity/device/inode). An ambiguous captured member or a
        # list/string/non-empty malformed object can never qualify as coherent.
        and _rollback_sidecar_proof_problem(journal.rollback_sidecar_proof) is None
        and _is_exact_bool(journal.pre_maintenance_api_active)
        and _is_exact_bool(journal.pre_maintenance_health_timer_active)
        # PR-D: terminal ABANDONED requires coherent verified restoration.
        and _boot_policy_lifecycle_for(journal) == (LIFECYCLE_RESTORED, None)
    )
    return "coherent_abandoned" if coherent else "mixed"


def _rollback_proof_problem(
    journal: CutoverJournal,
    proof: dict[str, Any],
    opts: CutoverOptions,
    *,
    adapters: CutoverAdapters | None = None,
    sealed: dict[str, Any] | None = None,
    production: Path | None = None,
) -> str | None:
    """Return a short reason if the structured proof is missing/invalid, else None.

    Non-raising so callers can route to either a hard refusal (fresh finalize)
    or sanitized MANUAL_RECOVERY_REQUIRED (idempotent-terminal validation). The
    proof is treated as fully untrusted input: type, schema, timestamp, safe
    basenames, approved-plan identity equality, fingerprint relationships,
    rollback stage/direction consistency, and the operator's approval
    fingerprint are ALL enforced. An arbitrary non-empty value can never pass.
    """
    if journal.rollback_verified is not True:
        return "no_verified_rollback"
    if not isinstance(proof, dict):
        return "proof_not_object"
    # Exact schema integer (reject bool/str/float coercion).
    if not _is_exact_int(proof.get("schema")) or int(proof["schema"]) != 1:
        return "proof_schema"
    if not _is_nonempty_str(proof.get("verified_at_utc")):
        return "proof_timestamp"
    for key in (
        "old_fingerprint",
        "new_fingerprint",
        "restored_fingerprint",
        "candidate_basename",
        "retained_candidate_basename",
    ):
        if not _is_nonempty_str(proof.get(key)):
            return f"proof_field_{key}"
    for key in (
        "restored_device",
        "restored_inode",
        "plan_production_device",
        "plan_production_inode",
    ):
        if not _is_exact_int(proof.get(key)):
            return f"proof_identity_{key}"
    # Exact booleans (reject truthy strings/ints).
    if proof.get("before_writer_resume") is not True:
        return "proof_before_writer_resume"
    if proof.get("candidate_not_production") is not True:
        return "proof_candidate_not_production"
    if proof.get("maintenance_id") != opts.maintenance_id:
        return "proof_maintenance_id_mismatch"

    # Safe single-component basenames before any Path.with_name(...) use.
    if not _safe_basename(proof.get("candidate_basename")):
        return "proof_unsafe_candidate_basename"
    if not _safe_basename(proof.get("retained_candidate_basename")):
        return "proof_unsafe_retained_basename"

    # Fingerprint relationships: the restored DB is the ORIGINAL (old), and the
    # candidate (new) must differ. The operator's approval fingerprint is bound
    # to that same original/restored fingerprint (never merely non-empty).
    if proof["old_fingerprint"] != proof["restored_fingerprint"]:
        return "proof_old_ne_restored"
    if proof["old_fingerprint"] == proof["new_fingerprint"]:
        return "proof_old_eq_new"
    if opts.expected_production_fingerprint != proof["old_fingerprint"]:
        return "expected_fingerprint_mismatch"

    # Complete ApprovedPlan schema with exact types; bind MID / SHA / production
    # identity/basename / staging basename / pause basenames to journal + opts
    # (+ sealed/live parent captures when supplied).
    plan_problem = _approved_plan_problem(
        journal,
        opts,
        adapters=adapters,
        sealed=sealed,
        production=production,
    )
    if plan_problem is not None:
        return plan_problem
    plan = journal.approved_plan
    assert isinstance(plan, dict)
    plan_dev = plan["production_device"]
    plan_ino = plan["production_inode"]
    plan_fp = plan["production_fingerprint"]
    if int(proof["plan_production_device"]) != int(plan_dev) or int(
        proof["plan_production_inode"]
    ) != int(plan_ino):
        return "proof_plan_identity_mismatch"

    # ``rollback_intent`` is an UNTRUSTED typed object recorded before the
    # exchange. Bind the proof fingerprints AND pre_cutover_basename to it.
    intent = journal.rollback_intent
    if not isinstance(intent, dict):
        return "rollback_intent_not_object"
    intent_old = intent.get("old_fingerprint")
    intent_new = intent.get("new_fingerprint")
    intent_pre = intent.get("pre_cutover_basename")
    if not _is_nonempty_str(intent_old) or not _is_nonempty_str(intent_new):
        return "rollback_intent_fingerprints_missing"
    if not _safe_basename(intent_pre):
        return "rollback_intent_pre_cutover_unsafe"
    for key in (
        "retained_device",
        "retained_inode",
        "candidate_device",
        "candidate_inode",
    ):
        if not _is_exact_int(intent.get(key)):
            return f"rollback_intent_{key}"
    if proof["old_fingerprint"] != plan_fp:
        return "proof_old_ne_approved_plan_fp"
    if proof["old_fingerprint"] != intent_old:
        return "proof_old_ne_intent_old"
    if proof["new_fingerprint"] != intent_new:
        return "proof_new_ne_intent_new"
    if intent_pre != journal.pre_cutover_basename:
        return "rollback_intent_pre_cutover_mismatch"
    if intent_pre != proof["retained_candidate_basename"]:
        return "rollback_intent_pre_ne_proof_retained"
    if int(proof["restored_device"]) != int(intent["retained_device"]) or int(
        proof["restored_inode"]
    ) != int(intent["retained_inode"]):
        return "proof_restored_ne_intent_retained"

    # Candidate / staging / pre-cutover journal state is REQUIRED, safe,
    # non-empty and EXACTLY equal to the proof values — never a conditional
    # "only if present" check that a missing field could silently satisfy.
    if not _safe_basename(journal.staging_basename):
        return "journal_staging_basename_unsafe"
    if not _safe_basename(journal.pre_cutover_basename):
        return "journal_pre_cutover_basename_unsafe"
    if not _is_nonempty_str(journal.candidate_fingerprint):
        return "journal_candidate_fingerprint_missing"
    if proof["candidate_basename"] != journal.staging_basename:
        return "proof_candidate_basename_mismatch"
    if proof["retained_candidate_basename"] != journal.pre_cutover_basename:
        return "proof_retained_basename_mismatch"
    if proof["new_fingerprint"] != journal.candidate_fingerprint:
        return "proof_new_fingerprint_mismatch"

    # Rollback stage/direction consistency (never inferred, but must agree).
    # Exchange/retained flags are exact booleans (reject truthy strings/ints).
    if journal.swap_direction != "rollback_pre_cutover_to_production":
        return "proof_swap_direction"
    if journal.exchange_completed is not True:
        return "proof_exchange_not_exact_true"
    if journal.old_production_retained is not True:
        return "proof_retained_not_exact_true"
    return None


def _approved_plan_problem(
    journal: CutoverJournal,
    opts: CutoverOptions,
    *,
    adapters: CutoverAdapters | None = None,
    sealed: dict[str, Any] | None = None,
    production: Path | None = None,
) -> str | None:
    """Validate the complete ApprovedPlan schema with exact typed bindings.

    When ``adapters`` / ``sealed`` / ``production`` are supplied (finalize and
    pre-exchange), also bind basenames and parent identities to the sealed
    private plan and live authoritative captures. Never raises on malformed
    input — returns a short path-free problem token.
    """
    plan = journal.approved_plan
    if not isinstance(plan, dict):
        return "approved_plan_not_object"
    required_str = (
        "maintenance_id",
        "expected_main_sha",
        "production_basename",
        "production_fingerprint",
        "backup_dest_basename",
        "staging_dest_basename",
        "reports_active_basename",
        "mail_pause_basename",
        "mirror_pause_basename",
    )
    required_int = (
        "production_device",
        "production_inode",
        "backup_parent_device",
        "backup_parent_inode",
        "staging_parent_device",
        "staging_parent_inode",
        "capacity_backup_required_bytes",
        "capacity_staging_required_bytes",
    )
    for key in required_str:
        if not _is_nonempty_str(plan.get(key)):
            return f"approved_plan_field_{key}"
    for key in required_int:
        if not _is_exact_int(plan.get(key)):
            return f"approved_plan_field_{key}"
    if not _safe_basename(plan.get("production_basename")):
        return "approved_plan_production_basename_unsafe"
    if not _safe_basename(plan.get("staging_dest_basename")):
        return "approved_plan_staging_basename_unsafe"
    if not _safe_basename(plan.get("backup_dest_basename")):
        return "approved_plan_backup_basename_unsafe"
    if plan["maintenance_id"] != journal.maintenance_id:
        return "approved_plan_mid_ne_journal"
    if plan["maintenance_id"] != opts.maintenance_id:
        return "approved_plan_mid_ne_opts"
    if plan["expected_main_sha"] != journal.expected_main_sha:
        return "approved_plan_sha_ne_journal"
    if plan["expected_main_sha"] != opts.expected_main_sha:
        return "approved_plan_sha_ne_opts"
    if plan["production_basename"] != journal.expected_production_basename:
        return "approved_plan_basename_ne_journal"
    if plan["staging_dest_basename"] != journal.staging_basename:
        return "approved_plan_staging_ne_journal"
    # Journal backup basename is REQUIRED (safe, non-empty) and must bind exactly.
    if not _safe_basename(journal.backup_basename):
        return "journal_backup_basename_missing_or_unsafe"
    if plan["backup_dest_basename"] != journal.backup_basename:
        return "approved_plan_backup_ne_journal"
    if plan["mail_pause_basename"] != MAIL_PAUSE_FILENAME:
        return "approved_plan_mail_pause_basename"
    if plan["mirror_pause_basename"] != DASHBOARD_PAUSE_FILENAME:
        return "approved_plan_mirror_pause_basename"
    if plan["reports_active_basename"] != "current":
        return "approved_plan_reports_active_ne_current"
    # Capacity fields: exact non-negative integers.
    if int(plan["capacity_backup_required_bytes"]) < 0:
        return "approved_plan_capacity_backup_negative"
    if int(plan["capacity_staging_required_bytes"]) < 0:
        return "approved_plan_capacity_staging_negative"

    # Bind production basename to the actual expected/canonical production path.
    prod = production
    if prod is None and opts.expected_production_path is not None:
        prod = opts.expected_production_path.expanduser()
    if prod is not None and plan["production_basename"] != prod.name:
        return "approved_plan_basename_ne_production"
    if (
        opts.expected_production_path is not None
        and plan["production_basename"] != opts.expected_production_path.expanduser().name
    ):
        return "approved_plan_basename_ne_opts"

    if sealed is not None:
        sealed_prod = sealed.get("production_path")
        if _is_nonempty_str(sealed_prod) and Path(sealed_prod).name != plan[
            "production_basename"
        ]:
            return "approved_plan_basename_ne_sealed"
        sealed_backup = sealed.get("backup_dest")
        sealed_staging = sealed.get("staging_dest")
        if not _is_nonempty_str(sealed_backup):
            return "sealed_backup_dest_missing"
        if not _is_nonempty_str(sealed_staging):
            return "sealed_staging_dest_missing"
        if Path(sealed_backup).name != plan["backup_dest_basename"]:
            return "approved_plan_backup_ne_sealed"
        if Path(sealed_staging).name != plan["staging_dest_basename"]:
            return "approved_plan_staging_ne_sealed"
        if journal.backup_basename != Path(sealed_backup).name:
            return "journal_backup_ne_sealed"
        if journal.staging_basename != Path(sealed_staging).name:
            return "journal_staging_ne_sealed"
        # Exact (or canonical-equivalent) destination path equality — basename-
        # only matches in a different parent must never pass.
        if opts.backup_dest is not None:
            if Path(opts.backup_dest.expanduser()).name != plan["backup_dest_basename"]:
                return "approved_plan_backup_basename_ne_opts"
            if not _path_canonically_equal(opts.backup_dest, sealed_backup):
                return "approved_plan_backup_ne_opts"
        if opts.staging_dest is not None:
            if Path(opts.staging_dest.expanduser()).name != plan["staging_dest_basename"]:
                return "approved_plan_staging_basename_ne_opts"
            if not _path_canonically_equal(opts.staging_dest, sealed_staging):
                return "approved_plan_staging_ne_opts"
        # Bind mail/mirror basenames to the actual sealed pause paths.
        sealed_reports = sealed.get("reports")
        if sealed_reports is not None:
            mail_p = mail_pause_path(Path(sealed_reports))
            mirror_p = mirror_pause_path(Path(sealed_reports))
            if mail_p.name != plan["mail_pause_basename"]:
                return "approved_plan_mail_ne_sealed_path"
            if mirror_p.name != plan["mirror_pause_basename"]:
                return "approved_plan_mirror_ne_sealed_path"
        # Bind approved production device/inode to sealed private-plan identity.
        sealed_dev = sealed.get("production_device")
        sealed_ino = sealed.get("production_inode")
        if not _is_exact_int(sealed_dev) or not _is_exact_int(sealed_ino):
            return "sealed_production_identity_missing"
        if int(plan["production_device"]) != int(sealed_dev) or int(
            plan["production_inode"]
        ) != int(sealed_ino):
            return "approved_plan_identity_ne_sealed"

        # Compare backup/staging parent device/inode with live parent captures.
        if adapters is not None:
            try:
                backup_parent = adapters.parent_identity(Path(sealed_backup))
                staging_parent = adapters.parent_identity(Path(sealed_staging))
            except Exception:  # noqa: BLE001
                return "approved_plan_parent_identity_uncapturable"
            if not isinstance(backup_parent, dict) or not isinstance(
                staging_parent, dict
            ):
                return "approved_plan_parent_identity_uncapturable"
            if not _is_exact_int(backup_parent.get("device")) or not _is_exact_int(
                backup_parent.get("inode")
            ):
                return "approved_plan_backup_parent_live_invalid"
            if not _is_exact_int(staging_parent.get("device")) or not _is_exact_int(
                staging_parent.get("inode")
            ):
                return "approved_plan_staging_parent_live_invalid"
            if int(backup_parent["device"]) != int(plan["backup_parent_device"]) or int(
                backup_parent["inode"]
            ) != int(plan["backup_parent_inode"]):
                return "approved_plan_backup_parent_drift"
            if int(staging_parent["device"]) != int(plan["staging_parent_device"]) or int(
                staging_parent["inode"]
            ) != int(plan["staging_parent_inode"]):
                return "approved_plan_staging_parent_drift"
    return None


def _validate_rollback_proof(
    journal: CutoverJournal,
    proof: dict[str, Any],
    opts: CutoverOptions,
    *,
    adapters: CutoverAdapters | None = None,
    sealed: dict[str, Any] | None = None,
    production: Path | None = None,
) -> None:
    """Fail closed unless the structured proof has every required typed field."""
    problem = _rollback_proof_problem(
        journal,
        proof,
        opts,
        adapters=adapters,
        sealed=sealed,
        production=production,
    )
    if problem is not None:
        _fail(
            f"rollback proof invalid ({problem}); manual reconciliation required",
            category=(
                CutoverFailureCategory.SAFETY
                if problem == "proof_maintenance_id_mismatch"
                else CutoverFailureCategory.AMBIGUOUS
            ),
            recovery=(
                "rollback_finalize requires a durable, structured rollback proof "
                "from attempt_rollback_before_writers. Reconcile manually."
            ),
        )


def _authoritative_identity(
    adapters: CutoverAdapters, path: Path
) -> dict[str, Any] | None:
    """Non-following open+fstat identity, or None if uncapturable (fail closed)."""
    try:
        return adapters.capture_member_identity(path)
    except (ArtifactPermissionError, CutoverError):
        return None
    except Exception:  # noqa: BLE001
        return None


def _rollback_identity_problem(
    adapters: CutoverAdapters,
    production: Path,
    proof: dict[str, Any],
) -> str | None:
    """Return a reason if live production identity/candidate state is wrong.

    Uses the authoritative fstat-based capture for the live main identity;
    immutable device/inode + approved-plan identity + candidate-not-production +
    retained-candidate presence are all fail-closed.
    """
    live = _authoritative_identity(adapters, production)
    if live is None:
        return "live_identity_uncapturable"
    if int(live["device"]) != int(proof["restored_device"]) or int(
        live["inode"]
    ) != int(proof["restored_inode"]):
        return "restored_identity_drift"
    if int(live["device"]) != int(proof["plan_production_device"]) or int(
        live["inode"]
    ) != int(proof["plan_production_inode"]):
        return "plan_identity_mismatch"
    live_fp = adapters.fingerprint(production)
    if live_fp == proof["new_fingerprint"]:
        return "candidate_at_production"
    retained = production.with_name(str(proof["retained_candidate_basename"]))
    if not adapters.path_exists(retained):
        return "retained_candidate_missing"
    if adapters.fingerprint(retained) != proof["new_fingerprint"]:
        return "retained_candidate_drift"
    return None


def _rollback_sidecar_proof_problem(proof: dict[str, Any] | None) -> str | None:
    """Return a reason if the typed rollback_sidecar_proof is missing/malformed.

    A missing proof is NOT treated as "both companions absent"; it fails closed.
    An ambiguous captured member never qualifies as a coherent terminal proof.
    """
    if not isinstance(proof, dict) or not proof:
        return "missing_sidecar_proof"
    if not _is_exact_int(proof.get("schema")) or int(proof["schema"]) != 1:
        return "sidecar_proof_schema"
    if not _is_nonempty_str(proof.get("captured_at_utc")):
        return "sidecar_proof_timestamp"
    for role in ("wal", "shm"):
        record = proof.get(role)
        if not isinstance(record, dict) or "present" not in record:
            return f"sidecar_proof_{role}_record"
        present = record.get("present")
        if present is not True and present is not False:
            return f"sidecar_proof_{role}_present"
        # Ambiguous members (FIFO/symlink/capture failure) never qualify.
        if record.get("ambiguous") is True:
            return f"sidecar_proof_{role}_ambiguous"
        if "ambiguous" in record and record.get("ambiguous") is not False:
            return f"sidecar_proof_{role}_ambiguous"
        if present is True:
            for key in ("device", "inode"):
                if not _is_exact_int(record.get(key)):
                    return f"sidecar_proof_{role}_{key}"
    return None


def _rf_api_running(adapters: CutoverAdapters, opts: CutoverOptions) -> bool:
    return bool(
        adapters.api_activity(api_base_url=opts.api_base_url).get("running")
    )


def _rf_api_stopped(adapters: CutoverAdapters, opts: CutoverOptions) -> bool:
    return bool(
        adapters.api_activity(api_base_url=opts.api_base_url).get("stopped")
    )


def _rf_api_owned(journal: CutoverJournal) -> bool:
    """True only when an exact-boolean ownership flag is set (never truthy coerce)."""
    return (
        journal.smoke_started_api is True
        or journal.rollback_finalize_started_api is True
        or journal.rollback_finalize_owned_temp_api is True
    )


_OWNERSHIP_ROLES = frozenset({"temp", "started", "smoke"})


def _ownership_flag_exact(journal: CutoverJournal, role: str) -> bool:
    """Exact-boolean ownership flag for one role; roles never substitute."""
    if role == "temp":
        return journal.rollback_finalize_owned_temp_api is True
    if role == "started":
        return journal.rollback_finalize_started_api is True
    if role == "smoke":
        return journal.smoke_started_api is True
    return False


def _ownership_roles_exact_true(journal: CutoverJournal) -> frozenset[str]:
    """Exact-True ownership roles currently asserted on the journal."""
    return frozenset(
        role for role in _OWNERSHIP_ROLES if _ownership_flag_exact(journal, role)
    )


def _api_owner_record(
    *,
    role: str,
    main_pid: int,
    invocation_id: str,
    maintenance_id: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "main_pid": int(main_pid),
        "invocation_id": invocation_id,
        "maintenance_id": maintenance_id,
    }


def _api_process_matches_owner(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    opts: CutoverOptions,
    *,
    role: str,
) -> bool:
    """True when owner role+MID+MainPID+InvocationID all agree with live process.

    Legacy owner records lacking an exact role (or MID) are never proven.
    """
    owner = journal.rollback_finalize_api_owner
    if not isinstance(owner, dict):
        return False
    if owner.get("role") != role:
        return False
    if owner.get("maintenance_id") != journal.maintenance_id:
        return False
    if owner.get("maintenance_id") != opts.maintenance_id:
        return False
    owner_pid = owner.get("main_pid")
    owner_inv = owner.get("invocation_id")
    if not _is_exact_int(owner_pid) or int(owner_pid) <= 0:
        return False
    if not _is_nonempty_str(owner_inv):
        return False
    try:
        cur = adapters.api_process_identity(api_base_url=opts.api_base_url)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(cur, dict):
        return False
    cur_pid = cur.get("main_pid")
    cur_inv = cur.get("invocation_id")
    if not _is_exact_int(cur_pid) or int(cur_pid) <= 0:
        return False
    if not _is_nonempty_str(cur_inv):
        return False
    return int(cur_pid) == int(owner_pid) and cur_inv == owner_inv


def _api_ownership_proven(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    opts: CutoverOptions,
    *,
    roles: frozenset[str],
) -> bool:
    """True only if exactly one role flag is True, it is requested, and owner matches.

    Mixed/contradictory role flags are ambiguous and never authorize a stop.
    Temporary stops must pass ``roles={"temp"}``. One role never substitutes.
    """
    if not roles or not roles <= _OWNERSHIP_ROLES:
        return False
    active = _ownership_roles_exact_true(journal)
    if len(active) != 1:
        return False
    role = next(iter(active))
    if role not in roles:
        return False
    return _api_process_matches_owner(adapters, journal, opts, role=role)


def _api_ownership_proven_any_reconcile(
    adapters: CutoverAdapters, journal: CutoverJournal, opts: CutoverOptions
) -> bool:
    """Reconcile may stop only when exactly one appropriate role is proven."""
    active = _ownership_roles_exact_true(journal)
    if len(active) != 1:
        return False
    role = next(iter(active))
    return _api_process_matches_owner(adapters, journal, opts, role=role)


def _clear_api_ownership(journal: CutoverJournal) -> None:
    """Clear ownership flags + owner evidence after an unambiguously stopped API."""
    journal.smoke_started_api = False
    journal.rollback_finalize_started_api = False
    journal.rollback_finalize_owned_temp_api = False
    journal.rollback_finalize_api_owner = None


def _sealed_paths_problem(
    adapters: CutoverAdapters,
    opts: CutoverOptions,
    production: Path,
    journal_path: Path,
) -> tuple[str | None, dict[str, Any] | None]:
    """Load + type-validate the sealed PrivatePlanPaths and bind every path.

    Returns ``(problem, bound)``. ``problem`` is a short, path-free reason on any
    missing / malformed / symlinked / inconsistent private-plan data; ``bound``
    carries the authoritative reports/journal Paths taken from the sealed plan
    (never re-derived from opts/settings). Absolute paths are NEVER placed in the
    returned problem string so callers can surface it in public evidence safely.
    """
    priv_path = private_journal_path(journal_path)
    if not adapters.path_exists(priv_path):
        return ("private_plan_missing", None)
    if adapters.is_symlink(priv_path) or not adapters.is_file(priv_path):
        return ("private_plan_not_regular", None)
    try:
        data = json.loads(adapters.read_text(priv_path))
    except (OSError, ValueError):
        return ("private_plan_unreadable", None)
    if not isinstance(data, dict):
        return ("private_plan_not_object", None)
    for key in ("production_path", "reports_dir", "journal_path", "journal_dir"):
        if not _is_nonempty_str(data.get(key)):
            return (f"private_plan_field_{key}", None)
    if data["journal_path"] != str(journal_path):
        return ("private_plan_journal_path_drift", None)
    if data["journal_dir"] != str(journal_path.parent):
        return ("private_plan_journal_dir_drift", None)
    # Production must equal the sealed production (exact or resolve-equal).
    if data["production_path"] != str(production):
        try:
            if Path(data["production_path"]).resolve() != production.resolve():
                return ("private_plan_production_drift", None)
        except OSError:
            return ("private_plan_production_drift", None)
    reports = Path(data["reports_dir"])
    # The operator-supplied reports dir (opts) must equal the sealed reports dir;
    # we never operate on an operator-chosen directory that the plan did not seal.
    operator_reports = opts.reports_dir
    if operator_reports is not None:
        op = operator_reports.expanduser()
        if str(op) != data["reports_dir"]:
            try:
                if op.resolve() != reports.resolve():
                    return ("reports_dir_mismatch", None)
            except OSError:
                return ("reports_dir_mismatch", None)
    # Require the REAL reports directory (not merely matching pause basenames)
    # and a non-symlinked journal dir on real filesystems.
    if not _is_synthetic(opts, adapters):
        if adapters.is_symlink(reports) or not adapters.is_dir(reports):
            return ("reports_dir_not_directory", None)
        if adapters.is_symlink(journal_path.parent):
            return ("journal_dir_symlink", None)
    return (
        None,
        {
            "reports": reports,
            "private": data,
            "production_path": data["production_path"],
            "backup_dest": data.get("backup_dest"),
            "staging_dest": data.get("staging_dest"),
            "production_device": data.get("production_device"),
            "production_inode": data.get("production_inode"),
        },
    )


def _fail_sealed_binding(problem: str) -> None:
    """Sanitized MANUAL_RECOVERY_REQUIRED for a sealed-path binding failure.

    Uses a HARD fail (never touches pause files/services), so a wrong reports_dir
    or malformed private plan performs NO file or service mutation. The problem
    token is path-free.
    """
    _fail(
        "MANUAL_RECOVERY_REQUIRED: sealed private-plan path binding failed; "
        "no file or service mutation was performed",
        category=CutoverFailureCategory.AMBIGUOUS,
        recovery=(
            "The sealed private plan is missing, malformed, symlinked, or "
            "inconsistent with the supplied paths. Reconcile manually; do not "
            "resume writers or services."
        ),
        evidence={"sealed_plan_problem": problem},
    )


def _rollback_finalize_stop_timer(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
    production: Path,
    reports: Path,
    opts: CutoverOptions,
    inject,
    *,
    phase: str,
) -> None:
    """Durably stop + verify the health timer, with intent-before/success-after.

    Called BEFORE reconciling the API (so the timer can never reopen/retrigger
    API work between our API stop and the quiescence gate) and again at final
    convergence. Every late/final timer stop carries durable journal intent and
    verified-success persistence; no externally visible transition without it.
    """
    # T4: structured (fail-closed) timer classification. Unknown / unreachable /
    # activating / deactivating / command-failure is AMBIGUOUS → manual recovery,
    # never silently treated as "inactive".
    activity = adapters.health_timer_activity()
    if activity.get("inactive"):
        return
    if activity.get("ambiguous"):
        _rollback_finalize_manual_recovery(
            adapters, journal, journal_path, production, reports,
            f"timer_activity_ambiguous_{phase}", opts=opts,
        )
    conflict = _replace_service_intent(
        journal, {"action": "stop_timer", "phase": phase}
    )
    if conflict is not None:
        _rollback_finalize_manual_recovery(
            adapters, journal, journal_path, production, reports,
            conflict, opts=opts,
        )
    write_journal(adapters, journal_path, journal)
    inject(f"rollback_finalize_timer_stop_intent_{phase}")
    adapters.stop_health_timer()
    inject(f"rollback_finalize_timer_stopped_{phase}")
    after = adapters.health_timer_activity()
    if not after.get("inactive"):
        _rollback_finalize_manual_recovery(
            adapters, journal, journal_path, production, reports,
            f"timer_stop_unverified_{phase}", opts=opts,
        )
    journal.rollback_finalize_service_intent = None
    write_journal(adapters, journal_path, journal)


def _rollback_finalize_reconcile_active_api(
    adapters: CutoverAdapters,
    journal: CutoverJournal,
    journal_path: Path,
    production: Path,
    reports: Path,
    opts: CutoverOptions,
    inject,
) -> None:
    """Reconcile any active API BEFORE demanding writer/FD quiescence.

    The activity is classified EXACTLY ONCE and ``ambiguous`` is never
    discarded. An ambiguous or unproven active API is manual recovery WITHOUT
    stopping it (never touch a foreign service, never act on uncertainty). Only
    a demonstrably owned API (exact-boolean flag AND live MainPID+InvocationID
    equality) that is unambiguously running is stopped with durable intent,
    activity-classifier verification, and success persistence.
    """
    act = adapters.api_activity(api_base_url=opts.api_base_url)
    if act.get("stopped"):
        return
    if act.get("ambiguous"):
        _rollback_finalize_manual_recovery(
            adapters, journal, journal_path, production, reports,
            "api_activity_ambiguous", opts=opts,
        )
    # Every stop requires proven ownership for an enumerated appropriate role.
    # A stale owned flag against a foreign process must never authorize a stop.
    if not _api_ownership_proven_any_reconcile(adapters, journal, opts):
        _rollback_finalize_manual_recovery(
            adapters, journal, journal_path, production, reports,
            "unowned_api_active", opts=opts,
        )
    conflict = _replace_service_intent(journal, {"action": "stop_owned_api"})
    if conflict is not None:
        _rollback_finalize_manual_recovery(
            adapters, journal, journal_path, production, reports,
            conflict, opts=opts,
        )
    write_journal(adapters, journal_path, journal)
    inject("rollback_finalize_api_stop_intent")
    try:
        adapters.stop_api()
    except Exception:  # noqa: BLE001
        pass
    inject("rollback_finalize_api_stopped")
    if not _rf_api_stopped(adapters, opts):
        # Never clear ownership when the stopped state is unverified.
        _rollback_finalize_manual_recovery(
            adapters, journal, journal_path, production, reports,
            "owned_api_stop_unverified", opts=opts,
        )
    _clear_api_ownership(journal)
    journal.rollback_finalize_service_intent = None
    write_journal(adapters, journal_path, journal)


def _rollback_finalize_check_pre_api_sidecars(
    adapters: CutoverAdapters,
    production: Path,
    journal: CutoverJournal,
    artifact: ArtifactPermissions | None,
) -> dict[str, Any]:
    """Fail closed on any unexplained WAL/SHM before starting the API.

    Requires a typed ``rollback_sidecar_proof`` (schema + capture timestamp +
    explicit WAL/SHM presence records); a MISSING proof fails closed even when
    both pathname sidecars are currently absent. A present pre-API companion is
    legit ONLY when it matches the proof identity AND an ORIGINAL artifact member
    that is present, identity-matched, ``barrier_applied=true``, and recorded in
    PR-B's ``members_changed``. Candidate-created / appeared / disappeared /
    drifted / ambiguous / not-barriered sidecars all fail closed. Never deletes
    or chmods a sidecar to make the state pass.
    """
    proof = journal.rollback_sidecar_proof
    proof_problem = _rollback_sidecar_proof_problem(proof)
    if proof_problem is not None:
        return {"ok": False, "role": "proof", "reason": proof_problem}
    assert proof is not None
    changed = set(artifact.members_changed) if artifact is not None else set()
    detail: dict[str, str] = {}
    for role, path in (("wal", wal_path(production)), ("shm", shm_path(production))):
        expected = proof.get(role) or {"present": False}
        live = _sidecar_identity_snapshot(adapters, path)
        if live.get("ambiguous") or expected.get("ambiguous"):
            return {"ok": False, "role": role, "reason": "ambiguous_sidecar"}
        exp_present = bool(expected.get("present"))
        live_present = bool(live.get("present"))
        if not exp_present and not live_present:
            detail[role] = "absent"
            continue
        if exp_present != live_present:
            return {
                "ok": False,
                "role": role,
                "reason": "sidecar_appeared" if live_present else "sidecar_disappeared",
            }
        if int(live["device"]) != int(expected["device"]) or int(
            live["inode"]
        ) != int(expected["inode"]):
            return {"ok": False, "role": role, "reason": "sidecar_identity_drift"}
        member = artifact.member(role) if artifact is not None else None
        if (
            member is None
            or not member.present
            or member.device is None
            or member.inode is None
            or int(member.device) != int(live["device"])
            or int(member.inode) != int(live["inode"])
        ):
            return {
                "ok": False,
                "role": role,
                "reason": "unexplained_candidate_sidecar",
            }
        if not member.barrier_applied or role not in changed:
            return {
                "ok": False,
                "role": role,
                "reason": "sidecar_not_barrier_applied",
            }
        detail[role] = "original_companion"
    return {"ok": True, "detail": detail}


def rollback_finalize(opts: CutoverOptions) -> dict[str, Any]:
    """Finalize a verified pre-PoNR rollback into the terminal ABANDONED state.

    A dedicated mutating operation: it never runs through ordinary ``--stage``
    cutover application, requires ``--apply``, and enforces the full production
    approval contract (``--confirm-production-cutover``, exact MID/path/SHA/
    fingerprint, ``--approve-swap``) plus an exact repository HEAD match. The
    July 19 MID is rejected by ``_require_auth``. abandoned != completed;
    ABANDONED is never a successful cutover and never soak-eligible.
    """
    if not opts.apply:
        _fail(
            "rollback_finalize requires --apply",
            category=CutoverFailureCategory.PREFLIGHT,
        )
    _require_auth(opts, for_swap=True)
    adapters = opts.adapters or FilesystemAdapters(settings=opts.settings)
    _require_git_match(opts, adapters)
    assert opts.expected_production_path is not None
    production = opts.expected_production_path.expanduser()
    reports = opts.reports_dir or (
        opts.settings or load_settings(enable_dotenv=False)
    ).resolved_reports_dir()
    journal_path = journal_path_for(opts, production)

    # T1: enforce the SAME canonical production path/type/symlink/samefile
    # binding and confined journal-location rules as apply_stage BEFORE any
    # mutation. The mutable production fingerprint is NOT compared here: the
    # restored original content may legitimately change once writers resume, so
    # the operator's approval fingerprint is bound to the rollback proof's
    # original/restored fingerprint (see ``_rollback_proof_problem``) and the
    # live content is checked separately via authoritative identity + quick_check
    # after writer control is regained.
    _validate_production_path_binding(opts, adapters, production)
    _validate_journal_location(adapters, journal_path, production)

    def _inject(phase: str) -> None:
        if opts.fail_after == phase:
            _fail(
                f"injected failure after {phase}",
                category=CutoverFailureCategory.APPLY,
            )

    with adapters.acquire_exclusive_lock(production, opts.maintenance_id):
        journal = load_journal(adapters, journal_path)
        if journal is None:
            _fail(
                "missing journal for rollback_finalize",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        assert journal is not None
        if journal.maintenance_id != opts.maintenance_id:
            _fail(
                "journal maintenance_id mismatch",
                category=CutoverFailureCategory.AMBIGUOUS,
            )

        # T1: bind EVERY sealed path from the private plan BEFORE any finalize
        # intent, pause touch, or service mutation. A missing/malformed/symlinked/
        # inconsistent private plan — or an operator reports_dir that does not
        # match the sealed plan — is a sanitized HARD refusal that performs NO
        # file or service mutation. ``reports`` is rebound to the sealed reports
        # directory so all pause operations use the authoritative sealed path.
        sealed_problem, sealed = _sealed_paths_problem(
            adapters, opts, production, journal_path
        )
        if sealed_problem is not None or sealed is None:
            _fail_sealed_binding(sealed_problem or "private_plan_unbound")
        assert sealed is not None
        reports = sealed["reports"]

        # Local manual-recovery shim: always carries production/reports/opts so
        # owned temporary-API cleanup and re-pause are attempted consistently.
        def _manual(reason: str, **kw: Any) -> None:
            _rollback_finalize_manual_recovery(
                adapters, journal, journal_path, production, reports, reason,
                opts=opts, **kw,
            )

        proof = journal.rollback_proof if isinstance(journal.rollback_proof, dict) else {}

        # Fail closed on malformed PR-C safety booleans before any mutation.
        bool_problem = _prc_safety_boolean_problem(journal)
        if bool_problem is not None:
            _manual(
                "malformed_safety_boolean",
                evidence={"boolean_problem": bool_problem},
            )

        # PR-D: validate coherent boot-policy lifecycle BEFORE any stop/start /
        # service-intent reconciliation / enable / disable. Malformed state is
        # a sanitized hard refusal with zero service/enablement mutation.
        bp_lifecycle, bp_problem = _boot_policy_lifecycle_for(journal)
        if bp_problem is not None or bp_lifecycle is None:
            _fail(
                f"malformed maintenance boot policy ({bp_problem or 'unknown'})",
                category=CutoverFailureCategory.SAFETY,
                evidence={"boot_policy_problem": bp_problem},
            )
        if bp_lifecycle == LIFECYCLE_PRISTINE:
            _fail(
                "maintenance boot policy looks pristine after STOP_READERS began",
                category=CutoverFailureCategory.SAFETY,
                evidence={"boot_policy_lifecycle": bp_lifecycle},
            )

        # Malformed rollback_intent (present but not a typed object) routes to
        # sanitized manual recovery; presence alone already locks other entrypoints.
        if journal.rollback_intent is not None and not isinstance(
            journal.rollback_intent, dict
        ):
            _manual(
                "malformed_rollback_intent",
                evidence={"intent_type": type(journal.rollback_intent).__name__},
            )
        if journal.rollback_finalize_intent is not None:
            fi_problem = _finalize_intent_problem(
                journal.rollback_finalize_intent, journal, opts
            )
            if fi_problem is not None:
                _manual(
                    "malformed_rollback_finalize_intent",
                    evidence={"finalize_intent_problem": fi_problem},
                )
        if journal.rollback_finalize_service_intent is not None:
            si_problem = _service_intent_problem(
                journal.rollback_finalize_service_intent
            )
            if si_problem is not None:
                _manual(
                    "malformed_rollback_finalize_service_intent",
                    evidence={"service_intent_problem": si_problem},
                )
            # Known crash intents may proceed only via action-specific observed-
            # state reconciliation (never overwrite with an unrelated action).
            si = journal.rollback_finalize_service_intent
            assert isinstance(si, dict)
            action = si.get("action")
            if action == "stop_timer":
                t_act = adapters.health_timer_activity()
                if t_act.get("inactive"):
                    journal.rollback_finalize_service_intent = None
                    write_journal(adapters, journal_path, journal)
                elif t_act.get("ambiguous"):
                    _manual("service_intent_timer_ambiguous")
            elif action in {
                "stop_owned_api",
                "stop_owned_temp_api",
                "cleanup_stop_owned_temp_api",
            }:
                if _rf_api_stopped(adapters, opts):
                    _clear_api_ownership(journal)
                    journal.rollback_finalize_service_intent = None
                    write_journal(adapters, journal_path, journal)
            elif action == "start_api":
                a_act = adapters.api_activity(api_base_url=opts.api_base_url)
                owned_temp = si.get("owned_temp")
                role = "temp" if owned_temp is True else "started"
                if a_act.get("ambiguous"):
                    _manual("service_intent_api_start_ambiguous")
                elif a_act.get("running"):
                    if _api_ownership_proven(
                        adapters, journal, opts, roles=frozenset({role})
                    ):
                        journal.rollback_finalize_service_intent = None
                        write_journal(adapters, journal_path, journal)
                    else:
                        _manual("service_intent_api_start_unowned_running")
                # stopped: leave intent for the start path (same action only)
            elif action == "start_timer":
                t_act = adapters.health_timer_activity()
                if t_act.get("ambiguous"):
                    _manual("service_intent_timer_start_ambiguous")
                # active: start already took effect. inactive: start never took
                # effect (crash after intent). Either way clear so a later
                # unrelated stop cannot conflict; an inactive start is re-issued
                # with the same action when the normal path reaches it.
                journal.rollback_finalize_service_intent = None
                write_journal(adapters, journal_path, journal)

        # T3: a post-exchange authoritative-capture failure is a durable
        # conservative lock. Never verified rollback, never normal progression —
        # route straight to sanitized manual recovery.
        if journal.rollback_identity_capture_failed is True:
            _manual("post_rollback_identity_capture_failed")
        if journal.rollback_identity_capture_failed is not False:
            _manual("malformed_rollback_identity_capture_failed")

        # ---- R1: strict terminal consistency / idempotency. Idempotent success
        # requires BOTH a self-consistent terminal record AND full typed-proof +
        # typed sidecar proof + live-identity validation. A malformed / incomplete
        # / contradictory terminal record is sanitized MANUAL_RECOVERY_REQUIRED,
        # never already_finalized=true. ----
        terminal = _rollback_finalize_terminal_state(journal)
        if terminal != "not_terminal":
            proof_problem = _rollback_proof_problem(
                journal,
                proof if isinstance(proof, dict) else {},
                opts,
                adapters=adapters,
                sealed=sealed,
                production=production,
            )
            sidecar_problem = _rollback_sidecar_proof_problem(
                journal.rollback_sidecar_proof
            )
            identity_problem = (
                _rollback_identity_problem(adapters, production, proof)
                if proof_problem is None and isinstance(proof, dict)
                else "proof_invalid"
            )
            if (
                terminal == "coherent_abandoned"
                and proof_problem is None
                and sidecar_problem is None
                and identity_problem is None
            ):
                return _rollback_finalize_status(opts, journal, already=True)
            _manual(
                "incoherent_terminal_record",
                evidence={
                    "terminal": terminal,
                    "proof_problem": proof_problem,
                    "sidecar_problem": sidecar_problem,
                    "identity_problem": identity_problem,
                },
            )

        # Refuse COMPLETED / post-PoNR / normal writers resumed.
        if journal.stage == CutoverStage.COMPLETED.value:
            _fail(
                "cutover already COMPLETED; refuse rollback_finalize",
                category=CutoverFailureCategory.SAFETY,
            )
        if journal.writer_resume_started is not False or journal.writers_resumed is not False:
            _fail(
                "normal writer resume already started/completed; "
                "rollback_finalize refused (post point-of-no-return)",
                category=CutoverFailureCategory.SAFETY,
            )

        # PR-D: verified persistent disablement before finalize mutations.
        # Skipped for coherent already-ABANDONED idempotent returns above, and
        # when enablement was already restored (crash after restore intent /
        # verified restore but before terminal ABANDONED).
        if journal.maintenance_boot_policy_restored is not True:
            try:
                _require_boot_policy_suppression_active(adapters, journal)
            except CutoverError:
                _manual("boot_policy_suppression_inactive")

        # ---- structured proof schema validation (typed fields + approved plan
        # device/inode required; legacy/insufficient → manual). ----
        _validate_rollback_proof(
            journal,
            proof,
            opts,
            adapters=adapters,
            sealed=sealed,
            production=production,
        )

        # Fresh finalize requires stage ATOMIC_SWAP (terminal ABANDONED is handled
        # by the idempotency gate above). Any other stage after a verified
        # rollback is incoherent and fails closed to manual recovery.
        if journal.stage != CutoverStage.ATOMIC_SWAP.value:
            _manual(
                "fresh_finalize_requires_atomic_swap_stage",
                evidence={"stage": journal.stage},
            )

        # ---- ownership: persist finalize intent BEFORE any mutation. ----
        # Never overwrite an empty/malformed object via truthiness; validate
        # existing typed intents and only create when absent.
        if journal.rollback_finalize_intent is None:
            journal.rollback_finalize_intent = {
                "action": "finalize_abandoned",
                "maintenance_id": journal.maintenance_id,
                "started_at_utc": _iso_now(),
            }
            write_journal(adapters, journal_path, journal)
        else:
            fi_problem = _finalize_intent_problem(
                journal.rollback_finalize_intent, journal, opts
            )
            if fi_problem is not None:
                _manual(
                    "malformed_rollback_finalize_intent",
                    evidence={"finalize_intent_problem": fi_problem},
                )
        _inject("rollback_finalize_intent")

        # ---- R1/F1: reassert BOTH pauses BEFORE any mutable-fingerprint gate. ----
        _inject("rollback_finalize_before_pause_touch")
        adapters.touch(mail_pause_path(reports))
        adapters.touch(mirror_pause_path(reports))
        if (
            journal.rollback_finalize_mail_pause_absent
            or journal.rollback_finalize_mirror_pause_absent
        ):
            journal.rollback_finalize_writers_repaused = True
        journal.rollback_finalize_mail_pause_absent = False
        journal.rollback_finalize_mirror_pause_absent = False
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_after_pause_touch")

        # ---- T4: durably stop + verify the health timer FIRST, before the API
        # is classified/reconciled, so the timer can never reopen or retrigger
        # API work in the window between our API stop and the quiescence gate. ----
        _rollback_finalize_stop_timer(
            adapters, journal, journal_path, production, reports, opts, _inject,
            phase="pre_api",
        )

        # ---- T4/R5: classify the API activity ONCE (ambiguous preserved) and
        # reconcile it (owned→verified stop; ambiguous/unowned→manual, never
        # stopped) BEFORE demanding writer/FD quiescence, so a real API-held
        # read-only FD cannot force manual recovery before the owned-stop logic
        # runs. ----
        _rollback_finalize_reconcile_active_api(
            adapters, journal, journal_path, production, reports, opts, _inject
        )
        try:
            _assert_writers_quiesced(adapters, production, reports_dir=reports)
        except CutoverError:
            _manual("writers_not_quiesced")
        _inject("rollback_finalize_pauses_reasserted")

        # ---- immutable identity bindings (authoritative fstat: device/inode +
        # approved plan) and candidate-not-production / retained-candidate. ----
        identity_problem = _rollback_identity_problem(adapters, production, proof)
        if identity_problem is not None:
            category = CutoverFailureCategory.SAFETY
            if identity_problem in {"retained_candidate_drift"}:
                category = CutoverFailureCategory.VERIFY
            elif identity_problem in {
                "live_identity_uncapturable",
                "retained_candidate_missing",
            }:
                category = CutoverFailureCategory.AMBIGUOUS
            _fail(
                f"rollback identity problem ({identity_problem}); refuse finalize",
                category=category,
            )
        live_fp = adapters.fingerprint(production)

        # ---- F1: mutable-content gate. The pristine original size/mtime
        # fingerprint is required ONLY while no writer-pause removal was ever
        # intended or recorded. Once mail resume was durably intended, content may
        # differ; immutable device/inode identity above plus quick_check carry
        # safety (durable intent + live pause observation reconcile a crash that
        # occurred after unlink but before success persistence). ----
        writers_ever_resumed = (
            journal.rollback_finalize_mail_resumed
            or journal.rollback_finalize_mirror_resumed
            or journal.rollback_finalize_mail_resume_intent
            or journal.rollback_finalize_mirror_resume_intent
        )
        if not writers_ever_resumed:
            if live_fp != proof["old_fingerprint"] or live_fp != proof[
                "restored_fingerprint"
            ]:
                _fail(
                    "production fingerprint does not match restored original",
                    category=CutoverFailureCategory.VERIFY,
                )
        elif not adapters.quick_check_ok(production):
            _manual("post_resume_quick_check_failed")

        # ---- F5: verify WAL/SHM lifecycle BEFORE touching services/API. A
        # candidate-created / appeared / disappeared / drifted / ambiguous
        # sidecar fails closed to manual reconciliation (never delete/chmod). ----
        artifact = _resolve_artifact_for_barrier_ops(journal)
        if artifact is None:
            _fail(
                "artifact permission state missing for rollback_finalize",
                category=CutoverFailureCategory.AMBIGUOUS,
            )
        assert artifact is not None
        side = _rollback_finalize_check_pre_api_sidecars(
            adapters, production, journal, artifact
        )
        if not side.get("ok"):
            _manual(
                f"sidecar_{side.get('role')}_{side.get('reason')}",
                evidence={"sidecar": side},
            )
        _inject("rollback_finalize_sidecars_verified")

        # ---- F6: require the captured pre-maintenance service policy. Legacy
        # journals lacking it fail closed (backward-compatible refusal). ----
        if (
            journal.pre_maintenance_api_active is None
            or journal.pre_maintenance_health_timer_active is None
        ):
            _manual("pre_maintenance_service_state_missing")
        # T2: the pre-maintenance service policy fields must be EXACT booleans
        # (a truthy string / int could silently invert the intended end-state).
        if not isinstance(journal.pre_maintenance_api_active, bool) or not isinstance(
            journal.pre_maintenance_health_timer_active, bool
        ):
            _manual("pre_maintenance_service_state_non_bool")
        want_api = bool(journal.pre_maintenance_api_active)
        want_timer = bool(journal.pre_maintenance_health_timer_active)

        # (5) restore ORIGINAL main/WAL/SHM via PR-B device/inode-bound logic.
        journal.permission_intent = {
            "action": "rollback_finalize_restore_original_writable",
            "target_mode": journal.original_mode,
        }
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_restore_intent")
        try:
            # Rebind restore target to the ORIGINAL (now-live) production identity,
            # not the candidate inode captured during the abandoned swap.
            record_post_swap_main(artifact, adapters, production)
            restore_evidence = restore_post_swap_for_writer_resume(
                adapters,
                production,
                artifact,
                original_mode=int(journal.original_mode or artifact.main.mode or 0),
                inject=_inject,
            )
        except ArtifactPermissionError as exc:
            journal.artifact_permissions = dump_artifact_permissions(artifact)
            write_journal(adapters, journal_path, journal)
            _map_artifact_error(exc)
            raise AssertionError("unreachable") from exc
        journal.permission_intent = None
        journal.production_write_barrier_active = False
        journal.artifact_permissions = dump_artifact_permissions(artifact)
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_permissions_restored")

        # ---- T4/F6/R5: the API was already reconciled (owned→stopped) before
        # the quiescence gate. Re-stop + verify the health timer here too (it may
        # have reappeared/restarted since the pre-API stop) with durable intent
        # + verified success before any health-check API work. ----
        _rollback_finalize_stop_timer(
            adapters, journal, journal_path, production, reports, opts, _inject,
            phase="pre_health",
        )
        _inject("rollback_finalize_services_reconciled")

        # (6) Recheck API activity IMMEDIATELY before using/starting the
        # health-check API. Ambiguity is tested FIRST. Start only from an
        # unambiguously stopped state; ambiguous/unknown activity is manual
        # recovery. Late unproven activity is never adopted, reused, or stopped.
        act = adapters.api_activity(api_base_url=opts.api_base_url)
        if act.get("ambiguous"):
            _manual("api_ambiguous_before_health")
        if act.get("running"):
            # Reuse ONLY a PROVEN-owned running API (enumerated role + live
            # MainPID/InvocationID match). Never adopt/stop late unproven activity.
            if not _api_ownership_proven_any_reconcile(adapters, journal, opts):
                _manual("api_appeared_unowned_before_health")
            # Proven owned + unambiguously running: reuse it, do not start again.
        elif act.get("stopped"):
            # Verified-success-AFTER ownership. Persist a durable start intent
            # WITHOUT any ownership flag, start externally, verify UNAMBIGUOUS
            # running, and ONLY THEN capture durable process/unit evidence
            # (exact positive MainPID + non-empty InvocationID) and persist
            # ownership. A crash after this intent but before verified ownership
            # leaves no owner record, so a later externally started API can never
            # be proven-owned (and thus never stopped) on retry.
            conflict = _replace_service_intent(
                journal,
                {"action": "start_api", "owned_temp": not want_api},
            )
            if conflict is not None:
                _manual(conflict)
            write_journal(adapters, journal_path, journal)
            _inject("rollback_finalize_api_start_intent")
            adapters.start_api()
            _inject("rollback_finalize_api_started")
            act2 = adapters.api_activity(api_base_url=opts.api_base_url)
            if act2.get("ambiguous") or not act2.get("running"):
                _manual("api_start_unverified")
            owner = adapters.api_process_identity(api_base_url=opts.api_base_url)
            owner_pid = owner.get("main_pid") if isinstance(owner, dict) else None
            owner_inv = owner.get("invocation_id") if isinstance(owner, dict) else None
            if not _is_exact_int(owner_pid) or int(owner_pid) <= 0:
                _manual("api_owner_evidence_uncapturable")
            if not _is_nonempty_str(owner_inv):
                _manual("api_owner_invocation_missing")
            role = "started" if want_api else "temp"
            journal.rollback_finalize_api_owner = _api_owner_record(
                role=role,
                main_pid=int(owner_pid),
                invocation_id=str(owner_inv),
                maintenance_id=journal.maintenance_id,
            )
            if want_api:
                journal.rollback_finalize_started_api = True
                journal.rollback_finalize_owned_temp_api = False
                journal.smoke_started_api = False
            else:
                journal.rollback_finalize_owned_temp_api = True
                journal.rollback_finalize_started_api = False
                journal.smoke_started_api = False
            # Clear the current service intent now that ownership is verified; the
            # separate ownership flags + owner evidence retain the history.
            journal.rollback_finalize_service_intent = None
            write_journal(adapters, journal_path, journal)
        else:
            _manual("api_activity_unknown_before_health")

        # (7) verify health against the restored original (bounded loopback).
        live_fp = adapters.fingerprint(production)
        smoke = adapters.http_smoke(opts.api_base_url, expected_fingerprint=live_fp)
        _inject("rollback_finalize_health_verified")

        # (8) recapture sidecars created at the API-open boundary (hardened).
        try:
            recapture_post_swap_companions(
                artifact, adapters, production, source="rollback_finalize"
            )
        except ArtifactPermissionError as exc:
            _map_artifact_error(exc)
            raise AssertionError("unreachable") from exc
        journal.artifact_permissions = dump_artifact_permissions(artifact)
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_sidecars_recaptured")

        # ---- F6: converge to the captured pre-maintenance service policy and
        # verify the outcomes. A temporary owned API is stopped again. ----
        if journal.rollback_finalize_owned_temp_api is True:
            # Temporary stops require the exact temp flag + proven process match.
            # started/smoke ownership must never authorize a temporary stop.
            if not _api_ownership_proven(
                adapters, journal, opts, roles=frozenset({"temp"})
            ):
                _manual("temp_api_ownership_unproven")
            conflict = _replace_service_intent(
                journal, {"action": "stop_owned_temp_api"}
            )
            if conflict is not None:
                _manual(conflict)
            write_journal(adapters, journal_path, journal)
            _inject("rollback_finalize_temp_api_stop_intent")
            try:
                adapters.stop_api()
            except Exception:  # noqa: BLE001
                pass
            _inject("rollback_finalize_temp_api_stopped")
            if not _rf_api_stopped(adapters, opts):
                _manual("temp_api_stop_unverified")
            _clear_api_ownership(journal)
            journal.rollback_finalize_service_intent = None
            write_journal(adapters, journal_path, journal)
        if want_timer:
            # T4: fail-closed structured timer classification for the start path.
            start_activity = adapters.health_timer_activity()
            if start_activity.get("ambiguous"):
                _manual("timer_start_activity_ambiguous")
            if not start_activity.get("active"):
                conflict = _replace_service_intent(
                    journal, {"action": "start_timer"}
                )
                if conflict is not None:
                    _manual(conflict)
                write_journal(adapters, journal_path, journal)
                _inject("rollback_finalize_timer_start_intent")
                adapters.start_health_timer()
                _inject("rollback_finalize_timer_started")
                after_start = adapters.health_timer_activity()
                if not after_start.get("active"):
                    _manual("timer_start_unverified")
                journal.rollback_finalize_service_intent = None
                write_journal(adapters, journal_path, journal)
        else:
            _rollback_finalize_stop_timer(
                adapters, journal, journal_path, production, reports, opts,
                _inject, phase="final",
            )
        # T4: final policy convergence uses api_activity for unambiguous API
        # running/stopped truth and structured timer activity for unambiguous
        # timer truth. Any ambiguity (unknown / unreachable / activating /
        # command-failure) fails closed to manual recovery, never a silent pass.
        final_api = adapters.api_activity(api_base_url=opts.api_base_url)
        if final_api.get("ambiguous"):
            _manual("final_api_activity_ambiguous")
        if bool(final_api.get("running")) != want_api:
            _manual("final_service_state_mismatch")
        final_timer = adapters.health_timer_activity()
        if final_timer.get("ambiguous"):
            _manual("final_timer_activity_ambiguous")
        if bool(final_timer.get("active")) != want_timer:
            _manual("final_service_state_mismatch")
        _inject("rollback_finalize_service_policy_applied")

        # PR-D: exact enablement restoration after service/health convergence and
        # BEFORE writer-pause removal / terminal ABANDONED.
        boot = _restore_boot_policy(
            adapters, journal, journal_path, inject=_inject
        )
        if boot.get("boot_policy_skipped") or (
            journal.maintenance_boot_policy_restored is not True
        ):
            _manual(
                "boot_policy_restore_incomplete",
                evidence={"boot_policy": boot},
            )
        try:
            _require_coherent_boot_policy(journal, require_restored=True)
        except CutoverError:
            _manual("boot_policy_restore_incoherent")
        _inject("rollback_finalize_boot_policy_restored")

        # (9) verify main + present required sidecars writable before any resume.
        try:
            verify_artifact_writable_for_resume(
                adapters,
                production,
                artifact,
                legacy_original_mode=journal.original_mode,
            )
        except ArtifactPermissionError as exc:
            _map_artifact_error(exc)
            raise AssertionError("unreachable") from exc

        # (10) resume mail then mirror on the RESTORED ORIGINAL. Each pause
        # removal is genuinely intent-before / verified-success-after:
        #   1. persist ONLY *_resume_intent;
        #   2. unlink the pause;
        #   3. authoritatively observe that the pause is absent;
        #   4. only then persist historical *_resumed + current *_pause_absent.
        # A crash after unlink but before success leaves durable intent, and the
        # retry reconciles from intent + live pause observation. If unlink fails
        # before mutation we never claim resume occurred. Historical *_resumed is
        # set once and preserved across any later re-pause.
        journal.rollback_finalize_mail_resume_intent = True
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_mail_intent")
        adapters.unlink(mail_pause_path(reports))
        _inject("rollback_finalize_mail_unlinked")
        # Observe pause absence against the SEALED reports directory. Any
        # observation failure (list_writers raise or pause still present) enters
        # manual recovery immediately — reassert both sealed pauses, preserve
        # historical resume intent, never claim success / ABANDONED.
        try:
            mail_writers = adapters.list_writers(production, reports_dir=reports)
        except Exception:  # noqa: BLE001
            _manual("mail_pause_observation_failed")
        if mail_writers.mail_pause_present:
            _manual("mail_pause_unexpectedly_present")
        journal.rollback_finalize_mail_resumed = True
        journal.rollback_finalize_mail_pause_absent = True
        journal.rollback_finalize_mail_pause_known = True
        # Record the fingerprint at the instant writes may legitimately begin so
        # a crash-and-retry after real mail activity is recognizable and does not
        # demand the pristine original fingerprint.
        journal.rollback_finalize_post_mail_fingerprint = adapters.fingerprint(
            production
        )
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_mail_resumed")
        mail_failure: str | None = None
        ident_mail = _authoritative_identity(adapters, production)
        if ident_mail is None:
            mail_failure = "identity_uncapturable"
        elif int(ident_mail["device"]) != int(proof["restored_device"]) or int(
            ident_mail["inode"]
        ) != int(proof["restored_inode"]):
            mail_failure = "production_identity_drift"
        elif not adapters.quick_check_ok(production):
            mail_failure = "quick_check_failed"
        else:
            try:
                verify_artifact_writable_for_resume(
                    adapters,
                    production,
                    artifact,
                    legacy_original_mode=journal.original_mode,
                )
            except ArtifactPermissionError:
                mail_failure = "artifact_not_writable"
        if mail_failure is not None:
            _manual(f"mail_observe_failed:{mail_failure}")
        journal.rollback_finalize_mail_observed_ok = True
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_mail_observed")

        journal.rollback_finalize_mirror_resume_intent = True
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_mirror_intent")
        adapters.unlink(mirror_pause_path(reports))
        _inject("rollback_finalize_mirror_unlinked")
        try:
            mirror_writers = adapters.list_writers(production, reports_dir=reports)
        except Exception:  # noqa: BLE001
            _manual("mirror_pause_observation_failed")
        if mirror_writers.mirror_pause_present:
            _manual("mirror_pause_unexpectedly_present")
        journal.rollback_finalize_mirror_resumed = True
        journal.rollback_finalize_mirror_pause_absent = True
        journal.rollback_finalize_mirror_pause_known = True
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_mirror_resumed")
        ident_mirror = _authoritative_identity(adapters, production)
        if ident_mirror is None or int(ident_mirror["device"]) != int(
            proof["restored_device"]
        ) or int(ident_mirror["inode"]) != int(proof["restored_inode"]):
            _manual("mirror_observe_identity_drift")
        journal.rollback_finalize_mirror_observed_ok = True
        journal.rollback_original_writers_resumed = True
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_mirror_observed")
        _inject("rollback_finalize_before_terminal")

        # (11) terminal ABANDONED — only after every step above verified.
        # Validate coherence before committing so a successful ABANDONED return
        # never bool-coerces malformed state.
        prior_finalize_intent = journal.rollback_finalize_intent
        journal.abandoned = True
        journal.rollback_finalized = True
        journal.rollback_finalized_at_utc = _iso_now()
        journal.rollback_finalize_intent = None
        journal.stage = CutoverStage.ABANDONED.value
        journal.notes.append("rollback_finalized_abandoned")
        if _rollback_finalize_terminal_state(journal) != "coherent_abandoned":
            journal.abandoned = False
            journal.rollback_finalized = False
            journal.rollback_finalized_at_utc = None
            journal.rollback_finalize_intent = prior_finalize_intent
            journal.stage = CutoverStage.ATOMIC_SWAP.value
            if journal.notes and journal.notes[-1] == "rollback_finalized_abandoned":
                journal.notes.pop()
            _manual("terminal_coherence_failed_before_commit")
        write_journal(adapters, journal_path, journal)
        _inject("rollback_finalize_after_terminal_replace")
        adapters.fsync_dir(journal_path.parent)
        _inject("rollback_finalize_abandoned")
        return _rollback_finalize_status(
            opts, journal, already=False, smoke=smoke, restore=restore_evidence
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
    artifact = _resolve_artifact_for_barrier_ops(journal)
    try:
        state = reconcile_artifact_barrier_state(
            adapters,
            production,
            artifact,
            barrier_active_flag=journal.production_write_barrier_active,
            permission_intent=journal.permission_intent,
            writer_resume_started=journal.writer_resume_started,
            legacy_original_mode=journal.original_mode,
        )
    except ArtifactPermissionError as exc:
        _map_artifact_error(exc)
        raise AssertionError("unreachable") from exc
    return sanitize_evidence(state)


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

    def _inject(phase: str) -> None:
        if opts.fail_after == phase:
            _fail(
                f"injected failure after {phase}",
                category=CutoverFailureCategory.APPLY,
            )

    with adapters.acquire_exclusive_lock(production, opts.maintenance_id):
        journal = load_journal(adapters, journal_path)
        if journal is None:
            _fail("missing journal for abort", category=CutoverFailureCategory.AMBIGUOUS)
        assert journal is not None
        locked = _rollback_finalize_locked(journal)
        if locked is not None:
            _fail(
                f"abort_before_swap refused: {locked}; only --rollback-finalize "
                "may proceed",
                category=CutoverFailureCategory.SAFETY,
            )
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

        # PR-D: validate coherent boot-policy lifecycle BEFORE any stop/start /
        # enable/disable. Pristine is allowed only when STOP_READERS never began.
        lifecycle = _require_coherent_boot_policy(journal, allow_pristine=True)

        # Stop readers (idempotent).
        adapters.stop_health_timer()
        adapters.stop_api()

        artifact = _resolve_artifact_for_barrier_ops(journal)
        current_mode = int(adapters.get_file_mode_owner(production)["mode"])
        needs_restore = artifact is not None and (
            journal.production_write_barrier_active
            or bool(journal.permission_intent)
            or bool(artifact.barrier_partial)
            or bool(artifact.members_changed)
            or (current_mode & 0o222) == 0
        )
        if needs_restore:
            assert artifact is not None
            journal.permission_intent = {
                "action": "abort_restore_writable_mode",
                "target_mode": journal.original_mode or artifact.main.mode,
                "members_changed": list(artifact.members_changed),
            }
            write_journal(adapters, journal_path, journal)

            def _persist(art: ArtifactPermissions) -> None:
                journal.artifact_permissions = dump_artifact_permissions(art)
                write_journal(adapters, journal_path, journal)

            try:
                # Mark barrier_applied for any present member that is currently RO
                # so restore covers partial chmod crashes.
                for role in (ROLE_MAIN, ROLE_WAL, ROLE_SHM):
                    member = artifact.member(role)
                    path = member_path(production, role)
                    if not member.present or not adapters.path_exists(path):
                        continue
                    live_mode = int(adapters.get_file_mode_owner(path)["mode"])
                    if live_mode & 0o222 == 0:
                        member.barrier_applied = True
                        if role not in artifact.members_changed:
                            artifact.members_changed.append(role)
                        artifact.set_member(member)
                restore_pre_swap_changed_members(
                    adapters,
                    production,
                    artifact,
                    persist=_persist,
                )
            except ArtifactPermissionError as exc:
                journal.artifact_permissions = dump_artifact_permissions(artifact)
                write_journal(adapters, journal_path, journal)
                _map_artifact_error(exc)
                raise AssertionError("unreachable") from exc
            journal.production_write_barrier_active = False
            journal.writable_mode_restored = True
            journal.permission_intent = None
            journal.artifact_permissions = dump_artifact_permissions(artifact)
            write_journal(adapters, journal_path, journal)

        adapters.start_api()
        smoke = adapters.http_smoke(
            opts.api_base_url, expected_fingerprint=adapters.fingerprint(production)
        )
        adapters.start_health_timer()

        # Exact enablement restoration after restored-original health verification
        # and BEFORE removing pauses / claiming abort complete. An abort that
        # never established STOP_READERS suppression must not invent restoration.
        boot = _restore_boot_policy(
            adapters, journal, journal_path, inject=_inject
        )
        if boot.get("boot_policy_skipped"):
            if lifecycle != LIFECYCLE_PRISTINE:
                _fail(
                    "abort boot-policy skip refused for non-pristine lifecycle",
                    category=CutoverFailureCategory.SAFETY,
                    evidence={
                        "boot_policy": boot,
                        "boot_policy_lifecycle": lifecycle,
                    },
                )
        else:
            _require_coherent_boot_policy(journal, require_restored=True)

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
                "boot_policy": _boot_policy_status_fragment(journal),
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
    # PR-D: persistent systemd enablement (boot auto-start). Defaults match a
    # typical enabled local API stack. ``simulate_user_manager_restart`` models
    # WSL/user-manager reboot: enabled units auto-start; disabled stay stopped.
    api_enabled: bool = True
    health_timer_enabled: bool = True
    unit_enablement_override: dict[str, dict[str, Any]] | None = None
    systemctl_calls: list[list[str]] = field(default_factory=list)
    lock_records: list[LockRecord] = field(default_factory=list)
    fd_hits: list[dict[str, Any]] = field(default_factory=list)
    # ---- R6: FD hits attributable to the API process. These are visible to
    # ``list_writers`` (and therefore the quiescence gate) ONLY while the API is
    # active, so stopping an owned API clears them — modeling a real API-held
    # read-only production/sidecar FD that closes when the API stops. ----
    api_fd_hits: list[dict[str, Any]] = field(default_factory=list)
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
    # ---- R6: test-only atomic-journal write fault injection. ``(substring,
    # phase)`` fails the FIRST atomic write whose serialized text contains
    # ``substring``, at one of the modeled durable-write phases:
    #   before_temp_write / after_temp_fsync / before_replace -> new content NOT
    #     applied (prior journal on disk); simulates a crash before os.replace.
    #   after_replace_before_dir_fsync / dir_fsync_failure -> new content IS
    #     applied (replace happened) then the fault raises; simulates a replace
    #     that succeeded but whose parent-directory fsync did not.
    # One-shot: fires once, then clears so the retry can proceed. ----
    journal_write_fault: tuple[str, str] | None = None
    _journal_fault_fired: bool = False
    # ---- R2/R3: force ``capture_member_identity`` to fail (ambiguous) for the
    # given path keys — the synthetic analog of a FIFO/symlink/non-regular
    # sidecar that the authoritative fstat capture refuses. ----
    capture_raise_paths: set[str] = field(default_factory=set)
    # ---- R4: pause-unlink fault knobs. ``refuse_unlink_mail/mirror`` makes the
    # unlink raise BEFORE any mutation (resume must not be claimed). ``sticky_*``
    # makes the unlink a no-op so the live pause is still present at the post-
    # unlink verification (unlink ineffective / pause recreated). ----
    refuse_unlink_mail: bool = False
    refuse_unlink_mirror: bool = False
    sticky_mail_pause: bool = False
    sticky_mirror_pause: bool = False
    # ---- T5: manual-recovery cleanup fault knobs. ``refuse_touch_mail/mirror``
    # make the re-pause touch raise for exactly one pause (partial re-pause), and
    # ``raise_list_writers`` makes the post-cleanup quiescence probe fail. ----
    refuse_touch_mail: bool = False
    refuse_touch_mirror: bool = False
    raise_list_writers: bool = False
    # Fail list_writers only when the sealed mail (or mirror) pause is currently
    # absent — models observation failure immediately after an unlink.
    raise_list_writers_when_mail_absent: bool = False
    raise_list_writers_when_mirror_absent: bool = False
    # When set, ``list_writers`` without an explicit ``reports_dir`` observes this
    # directory (models FilesystemAdapters deriving reports from settings). Used
    # to prove sealed reports remain authoritative when settings drift.
    settings_reports_dir: Path | None = None
    # ---- P4 (fourth pass) service knobs. ``health_timer_activity_override``
    # feeds raw (returncode, is_active_text) into the structured timer classifier
    # so tests can model systemctl failure/127, unknown unit state, activating,
    # etc. ``api_process_identity_override`` forces a specific MainPID/InvocationID
    # so tests can model a later externally started API at the same PID (ownership
    # cannot be proven). ``_api_invocation`` increments on every start_api to model
    # a fresh systemd generation. ----
    health_timer_activity_override: dict[str, Any] | None = None
    api_process_identity_override: dict[str, Any] | None = None
    _api_invocation: int = 0
    # ---- P6: callable invoked AFTER durable rollback_intent is written and
    # BEFORE the immediate pre-exchange member recapture. Models TOCTOU
    # replacement / content drift / capture failure in that window. ----
    after_rollback_intent_hook: Any | None = None
    # Callable invoked immediately AFTER rename_exchange in rollback (test-only)
    # so post-exchange identity-capture failure can be scripted without breaking
    # the pre-exchange recapture gate.
    after_rename_exchange_hook: Any | None = None

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
        # Faithful model of the fixed real writer: the canonical path is ALWAYS
        # the OLD complete journal (pre-replace faults) or the NEW complete
        # journal (at/after the atomic replace) — never a path-missing window.
        # ``.prev`` preserves the previous version via a mechanism that does not
        # move the live journal away before the replace.
        fault: str | None = None
        if self.journal_write_fault is not None and not self._journal_fault_fired:
            sub, phase = self.journal_write_fault
            if sub in text:
                fault = phase
        key = self.key(path)
        prev_key = self.key(path.with_name(path.name + ".prev"))
        old = self.files.get(key)
        # Phases strictly BEFORE the atomic replace: canonical stays OLD.
        if fault in ("before_temp_write", "after_temp_fsync", "before_replace"):
            self._journal_fault_fired = True
            raise OSError(errno.EIO, f"injected journal fault:{fault}")
        # Preserve previous version without moving the live journal away.
        if old is not None:
            self.files[prev_key] = old
        # Atomic replace: canonical becomes NEW.
        self.files[key] = text.encode("utf-8")
        # Phases AT/AFTER the replace: canonical is NEW; durability indeterminate.
        if fault in ("after_replace_before_dir_fsync", "dir_fsync_failure"):
            self._journal_fault_fired = True
            raise OSError(errno.EIO, f"injected journal fault:{fault}")

    def touch(self, path: Path) -> None:
        if path.name == MAIL_PAUSE_FILENAME and self.refuse_touch_mail:
            raise OSError(errno.EACCES, "injected mail-pause touch failure")
        if path.name == DASHBOARD_PAUSE_FILENAME and self.refuse_touch_mirror:
            raise OSError(errno.EACCES, "injected mirror-pause touch failure")
        self.files[self.key(path)] = b""
        if path.name == MAIL_PAUSE_FILENAME:
            self.mail_pause = True
        if path.name == DASHBOARD_PAUSE_FILENAME:
            self.mirror_pause = True

    def unlink(self, path: Path) -> None:
        if path.name == MAIL_PAUSE_FILENAME and self.refuse_unlink_mail:
            raise OSError(errno.EACCES, "injected mail-pause unlink failure")
        if path.name == DASHBOARD_PAUSE_FILENAME and self.refuse_unlink_mirror:
            raise OSError(errno.EACCES, "injected mirror-pause unlink failure")
        if path.name == MAIL_PAUSE_FILENAME and self.sticky_mail_pause:
            return
        if path.name == DASHBOARD_PAUSE_FILENAME and self.sticky_mirror_pause:
            return
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

    def list_writers(
        self,
        production: Path | None = None,
        *,
        reports_dir: Path | None = None,
    ) -> WriterInventory:
        if self.raise_list_writers:
            raise OSError(errno.EIO, "injected list_writers failure")
        hits = list(self.fd_hits)
        if self.services.api_active:
            hits.extend(self.api_fd_hits)
        # When a sealed reports directory is supplied, observe pause files there
        # (authoritative). Else if settings_reports_dir is set, observe that
        # (models FilesystemAdapters settings derivation). Otherwise fall back to
        # the in-memory pause flags used by ordinary cutover stages.
        if reports_dir is not None:
            mail = self.path_exists(mail_pause_path(reports_dir))
            mirror = self.path_exists(mirror_pause_path(reports_dir))
        elif self.settings_reports_dir is not None:
            mail = self.path_exists(mail_pause_path(self.settings_reports_dir))
            mirror = self.path_exists(mirror_pause_path(self.settings_reports_dir))
        else:
            mail = self.mail_pause
            mirror = self.mirror_pause
        if self.raise_list_writers_when_mail_absent and not mail:
            raise OSError(errno.EIO, "injected list_writers failure (mail absent)")
        if self.raise_list_writers_when_mirror_absent and not mirror:
            raise OSError(errno.EIO, "injected list_writers failure (mirror absent)")
        return WriterInventory(
            mail_pause_present=mail,
            mirror_pause_present=mirror,
            locks=list(self.lock_records),
            fd_hits=hits,
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

    def health_timer_activity(self) -> dict[str, Any]:
        override = self.health_timer_activity_override
        if isinstance(override, list):
            # Scripted transitions: consume one entry per call, keep the last.
            o = dict(override[0]) if override else {}
            if len(override) > 1:
                override.pop(0)
        elif override is not None:
            o = dict(override)
        else:
            o = None
        if o is not None:
            return classify_health_timer_activity(
                returncode=int(o.get("returncode", 0)),
                is_active_text=o.get("is_active_text"),
            )
        text = "active" if self.services.health_timer_active else "inactive"
        return classify_health_timer_activity(returncode=0, is_active_text=text)

    def api_process_identity(
        self, *, api_base_url: str | None = None
    ) -> dict[str, Any]:
        if self.api_process_identity_override is not None:
            return dict(self.api_process_identity_override)
        if self.services.api_active:
            return {"main_pid": 4321, "invocation_id": f"inv-{self._api_invocation}"}
        return {"main_pid": 0, "invocation_id": None}

    def stop_api(self) -> None:
        # Simulate a stop that does not take effect (API keeps listening).
        if self.refuse_stop_api:
            return
        self.services.api_active = False

    def start_api(self) -> None:
        # Each start models a fresh systemd generation (new InvocationID).
        self._api_invocation += 1
        self.services.api_active = True

    def stop_health_timer(self) -> None:
        self.services.health_timer_active = False

    def start_health_timer(self) -> None:
        self.services.health_timer_active = True

    def unit_enablement(self, unit: str) -> dict[str, Any]:
        if unit not in BOOT_POLICY_UNITS:
            _fail(
                f"refusing enablement probe for unrelated unit {unit}",
                category=CutoverFailureCategory.SAFETY,
            )
        self.systemctl_calls.append(["--user", "is-enabled", unit])
        override = self.unit_enablement_override
        if isinstance(override, dict) and unit in override:
            o = dict(override[unit])
            return classify_unit_enablement(
                returncode=int(o.get("returncode", 0)),
                is_enabled_text=o.get("is_enabled_text"),
            )
        enabled = (
            self.api_enabled if unit == API_SERVICE else self.health_timer_enabled
        )
        if enabled:
            return classify_unit_enablement(
                returncode=0, is_enabled_text="enabled"
            )
        return classify_unit_enablement(
            returncode=1, is_enabled_text="disabled"
        )

    def disable_unit(self, unit: str) -> None:
        if unit not in BOOT_POLICY_UNITS:
            _fail(
                f"refusing to disable unrelated unit {unit}",
                category=CutoverFailureCategory.SAFETY,
            )
        self.systemctl_calls.append(["--user", "disable", unit])
        if unit == API_SERVICE:
            self.api_enabled = False
        else:
            self.health_timer_enabled = False

    def enable_unit(self, unit: str) -> None:
        if unit not in BOOT_POLICY_UNITS:
            _fail(
                f"refusing to enable unrelated unit {unit}",
                category=CutoverFailureCategory.SAFETY,
            )
        self.systemctl_calls.append(["--user", "enable", unit])
        if unit == API_SERVICE:
            self.api_enabled = True
        else:
            self.health_timer_enabled = True

    def simulate_user_manager_restart(self) -> None:
        """Model WSL / user systemd manager restart.

        Persistently enabled units auto-start; disabled units remain stopped and
        do not regain activity.
        """
        if self.api_enabled:
            self._api_invocation += 1
            self.services.api_active = True
        else:
            self.services.api_active = False
        if self.health_timer_enabled:
            self.services.health_timer_active = True
        else:
            self.services.health_timer_active = False

    def wal_state(self, db: Path) -> dict[str, Any]:
        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        wal_present = self.path_exists(wal) or self.wal_size > 0
        shm_present = self.path_exists(shm) or self.shm_size > 0
        wal_size = (
            int(self.path_identity(wal)["size_bytes"])
            if self.path_exists(wal)
            else int(self.wal_size)
        )
        shm_size = (
            int(self.path_identity(shm)["size_bytes"])
            if self.path_exists(shm)
            else int(self.shm_size)
        )
        return {
            "wal_present": wal_present,
            "wal_size": wal_size,
            "shm_present": shm_present,
            "shm_size": shm_size,
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
        wal = Path(str(db) + "-wal")
        if self.path_exists(wal):
            # Truncate companion in place (identity preserved).
            self.files[self.key(wal)] = b""
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
            # Sanitized FD aggregate shape (PR-E); synthetic path has no /proc scan.
            "fd_observation": {
                "schema_version": 1,
                "observation_phases": ["pre_copy", "post_copy"],
                "member_roles_present": ["main"],
                "member_roles_observed": ["main"],
                "trusted_locking_count": 1,
                "blocker_count": 0,
                "ambiguous_count": 0,
                "verdict": "ok",
            },
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
        self.modes[ka], self.modes[kb] = (
            self.modes.get(kb, 0o644),
            self.modes.get(ka, 0o644),
        )
        self.owners[ka], self.owners[kb] = (
            self.owners.get(kb, (1000, 1000)),
            self.owners.get(ka, (1000, 1000)),
        )
        ia, ib = self.inode_overrides.get(ka), self.inode_overrides.get(kb)
        da, db = self.device_overrides.get(ka), self.device_overrides.get(kb)
        if ib is not None:
            self.inode_overrides[ka] = ib
        elif ka in self.inode_overrides:
            self.inode_overrides.pop(ka, None)
        if ia is not None:
            self.inode_overrides[kb] = ia
        elif kb in self.inode_overrides:
            self.inode_overrides.pop(kb, None)
        if db is not None:
            self.device_overrides[ka] = db
        elif ka in self.device_overrides:
            self.device_overrides.pop(ka, None)
        if da is not None:
            self.device_overrides[kb] = da
        elif kb in self.device_overrides:
            self.device_overrides.pop(kb, None)

    def rename_noreplace(self, src: Path, dest: Path) -> None:
        if self.path_exists(dest):
            raise OSError(errno.EEXIST, "RENAME_NOREPLACE dest exists")
        ks, kd = self.key(src), self.key(dest)
        data = self.files.pop(ks)
        self.files[kd] = data
        if ks in self.modes:
            self.modes[kd] = self.modes.pop(ks)
        if ks in self.owners:
            self.owners[kd] = self.owners.pop(ks)
        if ks in self.inode_overrides:
            self.inode_overrides[kd] = self.inode_overrides.pop(ks)
        if ks in self.device_overrides:
            self.device_overrides[kd] = self.device_overrides.pop(ks)

    def materialize_companion(
        self,
        db: Path,
        role: str,
        *,
        size: int,
        mode: int = 0o644,
        inode: int | None = None,
        device: int | None = None,
    ) -> Path:
        """Create an exact -wal/-shm companion file for permission tests."""
        suffix = {"wal": "-wal", "shm": "-shm"}[role]
        path = Path(str(db) + suffix)
        self.files[self.key(path)] = b"\0" * int(size)
        self.modes[self.key(path)] = int(mode) & 0o7777
        self.owners[self.key(path)] = self.owners.get(self.key(db), (1000, 1000))
        if inode is not None:
            self.inode_overrides[self.key(path)] = int(inode)
        if device is not None:
            self.device_overrides[self.key(path)] = int(device)
        if role == "wal":
            self.wal_size = int(size)
        if role == "shm":
            self.shm_size = int(size)
        return path

    def capture_member_identity(self, path: Path) -> dict[str, Any]:
        if self.key(path) in self.capture_raise_paths:
            from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
                ArtifactPermissionError as _APE,
            )

            raise _APE(
                "refusing non-regular artifact member (fstat)", category="safety"
            )
        if self.is_symlink(path):
            from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
                ArtifactPermissionError as _APE,
            )

            raise _APE("refusing symlink artifact member (fstat)", category="safety")
        if not self.is_file(path):
            from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import (
                ArtifactPermissionError as _APE,
            )

            raise _APE(
                "refusing non-regular artifact member (fstat)",
                category="safety",
            )
        ident = self.path_identity(path)
        owner = self.get_file_mode_owner(path)
        return {
            "basename": path.name,
            "device": int(ident["device"]),
            "inode": int(ident["inode"]),
            "mode": int(owner["mode"]),
            "uid": int(owner["uid"]),
            "gid": int(owner["gid"]),
            "nlink": 1,
            "size": int(ident["size_bytes"]),
        }

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
