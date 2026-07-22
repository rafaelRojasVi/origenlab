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
from typing import Any, Callable, Mapping

from origenlab_email_pipeline.qa.sqlite_cutover_artifact_permissions import ROLE_MAIN

BACKUP_SCHEMA_VERSION = 1
DEFAULT_PAGES_PER_BATCH = 4096
DEFAULT_CAPACITY_MARGIN_RATIO = 0.05
DEFAULT_CAPACITY_MARGIN_MIN_BYTES = 256 * 1024 * 1024  # 256 MiB
DEFAULT_BUSY_TIMEOUT_MS = 30_000
DEFAULT_PROGRESS_INTERVAL_SECONDS = 2.0
DEFAULT_FD_OBSERVATION_INTERVAL_SECONDS = 2.0
LOCK_DIR_NAME = ".origenlab_sqlite_online_backup_locks"

# Closed CLI --json projection (stdout boundary; not the on-disk manifest).
CLI_JSON_SCHEMA_VERSION = 1
CLI_MODE_PREFLIGHT = "preflight"
CLI_MODE_COMPLETED = "completed"
CLI_METHOD_BACKUP = "sqlite3.Connection.backup"
CLI_PUBLICATION_HARDLINK = "hardlink_no_clobber"
CLI_DEST_VERIFY_IMMUTABLE = "mode=ro&immutable=1"
CLI_VERIFICATION_CHEAP = "header+query_only+cheap_pragmas+schema_inventory"
CLI_COMPLETION_FINAL_MANIFEST = "final_manifest"
CLI_FD_PHASE_PRE_COPY = "pre_copy"
CLI_FD_PHASE_DURING_COPY = "during_copy"
CLI_FD_PHASE_POST_COPY = "post_copy"
CLI_FD_ROLE_MAIN = "main"
CLI_FD_ROLE_WAL = "wal"
CLI_FD_ROLE_SHM = "shm"
CLI_FD_VERDICT_OK = "ok"
CLI_FD_VERDICT_BLOCKED = "blocked"
CLI_FD_VERDICT_AMBIGUOUS = "ambiguous"
_CLI_FD_PHASE_EMIT: dict[str, str] = {
    CLI_FD_PHASE_PRE_COPY: CLI_FD_PHASE_PRE_COPY,
    CLI_FD_PHASE_DURING_COPY: CLI_FD_PHASE_DURING_COPY,
    CLI_FD_PHASE_POST_COPY: CLI_FD_PHASE_POST_COPY,
}
_CLI_FD_ROLE_EMIT: dict[str, str] = {
    CLI_FD_ROLE_MAIN: CLI_FD_ROLE_MAIN,
    CLI_FD_ROLE_WAL: CLI_FD_ROLE_WAL,
    CLI_FD_ROLE_SHM: CLI_FD_ROLE_SHM,
}
_CLI_FD_VERDICT_EMIT: dict[str, str] = {
    CLI_FD_VERDICT_OK: CLI_FD_VERDICT_OK,
    CLI_FD_VERDICT_BLOCKED: CLI_FD_VERDICT_BLOCKED,
    CLI_FD_VERDICT_AMBIGUOUS: CLI_FD_VERDICT_AMBIGUOUS,
}
_CLI_MAX_ELAPSED_SECONDS = 31_536_000.0  # 365d bound for CLI float field
_CLI_JSON_REJECT = "cli json report rejected: malformed field"

# PR-E: allowlisted backup phases for OperationalError classification.
BACKUP_PHASE_SOURCE_CONNECT = "source_connect"
BACKUP_PHASE_SOURCE_INVENTORY = "source_inventory"
BACKUP_PHASE_DESTINATION_CONNECT = "destination_connect"
BACKUP_PHASE_COPY = "copy"
BACKUP_PHASE_DESTINATION_COMMIT = "destination_commit"
BACKUP_PHASE_DESTINATION_VERIFY = "destination_verify"
BACKUP_PHASE_PUBLISH = "publish"
BACKUP_PHASES: frozenset[str] = frozenset(
    {
        BACKUP_PHASE_SOURCE_CONNECT,
        BACKUP_PHASE_SOURCE_INVENTORY,
        BACKUP_PHASE_DESTINATION_CONNECT,
        BACKUP_PHASE_COPY,
        BACKUP_PHASE_DESTINATION_COMMIT,
        BACKUP_PHASE_DESTINATION_VERIFY,
        BACKUP_PHASE_PUBLISH,
    }
)

OPERR_SCHEMA_VERSION = 1
OPERR_BUSY_OR_LOCKED = "busy_or_locked"
OPERR_READONLY_WAL_LOCKING = "readonly_wal_locking"
OPERR_CANNOT_OPEN = "cannot_open"
OPERR_IO_ERROR = "io_error"
OPERR_CAPACITY = "capacity"
OPERR_INTERRUPTED = "interrupted"
OPERR_OTHER = "other_operational"
OPERR_CATEGORIES: frozenset[str] = frozenset(
    {
        OPERR_BUSY_OR_LOCKED,
        OPERR_READONLY_WAL_LOCKING,
        OPERR_CANNOT_OPEN,
        OPERR_IO_ERROR,
        OPERR_CAPACITY,
        OPERR_INTERRUPTED,
        OPERR_OTHER,
    }
)
OPERR_RECOVERY_WAL_SHM = "verify_wal_shm_permissions_and_identity"
OPERR_RECOVERY_BUSY = "retry_after_writers_quiesced"
OPERR_RECOVERY_CANTOPEN = "verify_source_path_and_permissions"
OPERR_RECOVERY_IO = "inspect_destination_storage_health"
OPERR_RECOVERY_FULL = "free_destination_capacity"
OPERR_RECOVERY_INTERRUPT = "retry_backup_after_interrupt"
OPERR_RECOVERY_OTHER = "inspect_sqlite_operational_failure"
OPERR_UNKNOWN_NAME = "UNKNOWN"

# Primary/extended result codes (sqlite3 exposes these on modern Python).
_SQLITE_BUSY = int(getattr(sqlite3, "SQLITE_BUSY", 5))
_SQLITE_LOCKED = int(getattr(sqlite3, "SQLITE_LOCKED", 6))
_SQLITE_READONLY = int(getattr(sqlite3, "SQLITE_READONLY", 8))
_SQLITE_INTERRUPT = int(getattr(sqlite3, "SQLITE_INTERRUPT", 9))
_SQLITE_IOERR = int(getattr(sqlite3, "SQLITE_IOERR", 10))
_SQLITE_FULL = int(getattr(sqlite3, "SQLITE_FULL", 13))
_SQLITE_CANTOPEN = int(getattr(sqlite3, "SQLITE_CANTOPEN", 14))
_SQLITE_READONLY_CANTLOCK = int(getattr(sqlite3, "SQLITE_READONLY_CANTLOCK", 520))
_SQLITE_READONLY_CANTINIT = int(getattr(sqlite3, "SQLITE_READONLY_CANTINIT", 1288))

_VALID_SQLITE_NAME = re.compile(r"^SQLITE_[A-Z0-9_]+$")


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
    """Operator-facing backup failure (already sanitized).

    May carry additive ``detail`` with a structured OperationalError schema or
    FD-observation aggregate. Never put raw exception text, paths, SQL, or
    tokens into ``detail``.
    """

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] | None = (
            dict(detail) if isinstance(detail, dict) else None
        )


def _safe_int_code(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _safe_sqlite_name(value: Any) -> str:
    if isinstance(value, str) and _VALID_SQLITE_NAME.fullmatch(value):
        return value
    return OPERR_UNKNOWN_NAME


def classify_sqlite_operational_error(
    exc: BaseException,
    *,
    phase: str,
) -> dict[str, Any]:
    """Derive a sanitized OperationalError detail object (never raises).

    Uses exact integer ``sqlite_errorcode`` and validated symbolic
    ``sqlite_errorname`` when present. Never includes ``str(exc)``, SQL, URI,
    paths, emails, tokens, or arbitrary exception attributes.
    """
    safe_phase = phase if phase in BACKUP_PHASES else "unknown"
    code: int | None = None
    name = OPERR_UNKNOWN_NAME
    try:
        code = _safe_int_code(getattr(exc, "sqlite_errorcode", None))
        name = _safe_sqlite_name(getattr(exc, "sqlite_errorname", None))
    except Exception:  # noqa: BLE001
        code = None
        name = OPERR_UNKNOWN_NAME

    category = OPERR_OTHER
    retryable = False
    recovery = OPERR_RECOVERY_OTHER

    primary = (code % 256) if isinstance(code, int) else None
    try:
        if code in {_SQLITE_BUSY, _SQLITE_LOCKED} or primary in {
            _SQLITE_BUSY,
            _SQLITE_LOCKED,
        }:
            category = OPERR_BUSY_OR_LOCKED
            retryable = True
            recovery = OPERR_RECOVERY_BUSY
        elif code in {_SQLITE_READONLY_CANTLOCK, _SQLITE_READONLY_CANTINIT} or (
            name in {"SQLITE_READONLY_CANTLOCK", "SQLITE_READONLY_CANTINIT"}
        ):
            category = OPERR_READONLY_WAL_LOCKING
            retryable = False
            recovery = OPERR_RECOVERY_WAL_SHM
        elif primary == _SQLITE_READONLY or name.startswith("SQLITE_READONLY"):
            # Other readonly family without CANTLOCK/CANTINIT.
            category = OPERR_READONLY_WAL_LOCKING
            retryable = False
            recovery = OPERR_RECOVERY_WAL_SHM
        elif code == _SQLITE_CANTOPEN or primary == _SQLITE_CANTOPEN or name.startswith(
            "SQLITE_CANTOPEN"
        ):
            category = OPERR_CANNOT_OPEN
            retryable = False
            recovery = OPERR_RECOVERY_CANTOPEN
        elif code == _SQLITE_IOERR or primary == _SQLITE_IOERR or name.startswith(
            "SQLITE_IOERR"
        ):
            category = OPERR_IO_ERROR
            retryable = True
            recovery = OPERR_RECOVERY_IO
        elif code == _SQLITE_FULL or primary == _SQLITE_FULL or name == "SQLITE_FULL":
            category = OPERR_CAPACITY
            retryable = False
            recovery = OPERR_RECOVERY_FULL
        elif (
            code == _SQLITE_INTERRUPT
            or primary == _SQLITE_INTERRUPT
            or name == "SQLITE_INTERRUPT"
        ):
            category = OPERR_INTERRUPTED
            retryable = True
            recovery = OPERR_RECOVERY_INTERRUPT
    except Exception:  # noqa: BLE001
        category = OPERR_OTHER
        retryable = False
        recovery = OPERR_RECOVERY_OTHER
        code = None
        name = OPERR_UNKNOWN_NAME

    if category not in OPERR_CATEGORIES:
        category = OPERR_OTHER

    return {
        "schema": OPERR_SCHEMA_VERSION,
        "phase": safe_phase,
        "category": category,
        "sqlite_errorcode": code,
        "sqlite_errorname": name,
        "retryable": bool(retryable) if type(retryable) is bool else False,
        "recovery": recovery,
    }


def operational_error_to_backup_error(
    exc: BaseException,
    *,
    phase: str,
    message: str | None = None,
) -> BackupError:
    """Convert sqlite3.OperationalError (or similar) into a safe BackupError."""
    detail = classify_sqlite_operational_error(exc, phase=phase)
    fixed = message or (
        f"sqlite operational failure category={detail['category']} "
        f"phase={detail['phase']}"
    )
    return BackupError(fixed, detail={"operational_error": detail})


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
    # PR-E: optional FD observation while the source connection is open.
    # Cutover CREATE_CURRENT_BACKUP sets require_fd_observation=True.
    require_fd_observation: bool = False
    fd_observation_interval_seconds: float = DEFAULT_FD_OBSERVATION_INTERVAL_SECONDS
    # Test / DI hooks for observation (injected scanners, capture, etc.).
    fd_observe_hook: Callable[[Any, str], dict[str, Any]] | None = None
    fd_capture_hook: Callable[[Path], dict[str, Any]] | None = None
    # Test hooks
    dir_fsync: Callable[[int], None] | None = None
    manifest_write_hook: Callable[[Path, str], None] | None = None
    post_copy_hook: Callable[[], None] | None = None
    # Inject OperationalError / observation faults by phase name (tests).
    fail_phase: str | None = None
    fail_phase_exc: BaseException | None = None


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


def _cli_reject() -> None:
    raise BackupError(_CLI_JSON_REJECT)


def _cli_exact_bool(value: Any) -> bool:
    if type(value) is not bool:
        _cli_reject()
    return value


def _cli_nonneg_int(value: Any) -> int:
    if type(value) is not int or value < 0:
        _cli_reject()
    return value


def _cli_positive_int(value: Any) -> int:
    if type(value) is not int or value <= 0:
        _cli_reject()
    return value


def _cli_nonneg_float(value: Any, *, max_value: float) -> float:
    if type(value) is bool:
        _cli_reject()
    if type(value) is int:
        number = float(value)
    elif type(value) is float:
        number = value
    else:
        _cli_reject()
        raise AssertionError("unreachable")
    if number < 0.0 or number > max_value:
        _cli_reject()
    return number


def _cli_emit_fixed_literal(value: Any, emit: Mapping[str, str]) -> str:
    if type(value) is not str:
        _cli_reject()
    if value not in emit:
        _cli_reject()
    return emit[value]


def _cli_emit_fixed_literal_list(value: Any, emit: Mapping[str, str]) -> list[str]:
    if type(value) is not list:
        _cli_reject()
    out: list[str] = []
    for item in value:
        out.append(_cli_emit_fixed_literal(item, emit))
    return out


def _build_safe_cli_fd_observation(raw: Any) -> dict[str, Any]:
    """Rebuild fd_observation from an explicit allowlist (no key copy)."""
    if type(raw) is not dict:
        _cli_reject()
    schema = raw.get("schema_version")
    if type(schema) is not int or schema != CLI_JSON_SCHEMA_VERSION:
        _cli_reject()
    verdict = _cli_emit_fixed_literal(raw.get("verdict"), _CLI_FD_VERDICT_EMIT)
    phases_raw = raw.get("observation_phases")
    if phases_raw is None:
        phase_one = raw.get("phase")
        if phase_one is None:
            phases: list[str] = []
        else:
            phases = [_cli_emit_fixed_literal(phase_one, _CLI_FD_PHASE_EMIT)]
    else:
        phases = _cli_emit_fixed_literal_list(phases_raw, _CLI_FD_PHASE_EMIT)
    present = raw.get("member_roles_present")
    observed = raw.get("member_roles_observed")
    if present is None:
        present_roles: list[str] = []
    else:
        present_roles = _cli_emit_fixed_literal_list(present, _CLI_FD_ROLE_EMIT)
    if observed is None:
        observed_roles: list[str] = []
    else:
        observed_roles = _cli_emit_fixed_literal_list(observed, _CLI_FD_ROLE_EMIT)
    trusted = _cli_nonneg_int(raw.get("trusted_locking_count"))
    blockers = _cli_nonneg_int(raw.get("blocker_count"))
    ambiguous = _cli_nonneg_int(raw.get("ambiguous_count"))
    return {
        "schema_version": CLI_JSON_SCHEMA_VERSION,
        "observation_phases": phases,
        "member_roles_present": present_roles,
        "member_roles_observed": observed_roles,
        "trusted_locking_count": trusted,
        "blocker_count": blockers,
        "ambiguous_count": ambiguous,
        "verdict": verdict,
    }


def build_safe_cli_json_report(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``run_online_backup`` results to a closed CLI stdout schema.

    Copies only allowlisted fields with exact type checks. Emits fixed string
    literals for accepted states. Never copies unknown keys, fingerprints,
    basenames, meta blobs, warnings, notes, or raw exception text.
    """
    if not isinstance(result, Mapping) or isinstance(result, (str, bytes, bytearray)):
        _cli_reject()

    mode_raw = result.get("mode")
    completed_raw = result.get("completed")

    if mode_raw == CLI_MODE_PREFLIGHT:
        _cli_emit_fixed_literal(mode_raw, {CLI_MODE_PREFLIGHT: CLI_MODE_PREFLIGHT})
        completed = _cli_exact_bool(completed_raw)
        writes = _cli_exact_bool(result.get("writes_performed"))
        if completed is not False or writes is not False:
            _cli_reject()
        return {
            "schema_version": CLI_JSON_SCHEMA_VERSION,
            "mode": CLI_MODE_PREFLIGHT,
            "apply": _cli_exact_bool(result.get("apply")),
            "completed": False,
            "writes_performed": False,
            "source_opened_with_sqlite3": _cli_exact_bool(
                result.get("source_opened_with_sqlite3")
            ),
            "source_size_bytes": _cli_nonneg_int(result.get("source_size_bytes")),
            "estimated_output_bytes": _cli_nonneg_int(result.get("estimated_output_bytes")),
            "destination_free_bytes": _cli_nonneg_int(result.get("destination_free_bytes")),
            "capacity_required_bytes": _cli_nonneg_int(result.get("capacity_required_bytes")),
            "filesystem_separated": _cli_exact_bool(result.get("filesystem_separated")),
            "allow_same_filesystem": _cli_exact_bool(result.get("allow_same_filesystem")),
            "pages_per_batch": _cli_positive_int(result.get("pages_per_batch")),
            "busy_timeout_ms": _cli_nonneg_int(result.get("busy_timeout_ms")),
        }

    if completed_raw is True:
        out: dict[str, Any] = {
            "schema_version": CLI_JSON_SCHEMA_VERSION,
            "mode": CLI_MODE_COMPLETED,
            "completed": True,
            "method": _cli_emit_fixed_literal(
                result.get("method"),
                {CLI_METHOD_BACKUP: CLI_METHOD_BACKUP},
            ),
            "elapsed_seconds": _cli_nonneg_float(
                result.get("elapsed_seconds"),
                max_value=_CLI_MAX_ELAPSED_SECONDS,
            ),
            "source_size_bytes": _cli_nonneg_int(result.get("source_size_bytes")),
            "destination_size_bytes": _cli_nonneg_int(result.get("destination_size_bytes")),
            "pages_per_batch": _cli_positive_int(result.get("pages_per_batch")),
            "progress_events": _cli_nonneg_int(result.get("progress_events")),
            "allow_same_filesystem": _cli_exact_bool(result.get("allow_same_filesystem")),
            "publication_method": _cli_emit_fixed_literal(
                result.get("publication_method"),
                {CLI_PUBLICATION_HARDLINK: CLI_PUBLICATION_HARDLINK},
            ),
            "destination_verification": _cli_emit_fixed_literal(
                result.get("destination_verification"),
                {CLI_DEST_VERIFY_IMMUTABLE: CLI_DEST_VERIFY_IMMUTABLE},
            ),
            "directory_fsync_supported": _cli_exact_bool(
                result.get("directory_fsync_supported")
            ),
            "source_opened_readonly": _cli_exact_bool(result.get("source_opened_readonly")),
            "source_mutated_by_utility": _cli_exact_bool(
                result.get("source_mutated_by_utility")
            ),
            "source_fingerprint_changed_during_backup": _cli_exact_bool(
                result.get("source_fingerprint_changed_during_backup")
            ),
            "verification": _cli_emit_fixed_literal(
                result.get("verification"),
                {CLI_VERIFICATION_CHEAP: CLI_VERIFICATION_CHEAP},
            ),
            "completion_marker": _cli_emit_fixed_literal(
                result.get("completion_marker"),
                {CLI_COMPLETION_FINAL_MANIFEST: CLI_COMPLETION_FINAL_MANIFEST},
            ),
        }
        if "fd_observation" in result:
            out["fd_observation"] = _build_safe_cli_fd_observation(
                result.get("fd_observation")
            )
        return out

    _cli_reject()
    raise AssertionError("unreachable")


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
    parse_sqlite_header_meta(path)  # raises BackupError if invalid


def parse_sqlite_header_meta(path: Path) -> dict[str, Any]:
    """
    Read-only SQLite header parse (no sqlite3.connect).

    Page size / page count come from the DB header. Freelist/schema/table
    fields are marked not_assessed_until_apply.
    """
    if not path.is_file() or path.stat().st_size < 100:
        raise BackupError(
            f"source is not a readable SQLite file: {sanitize_path_for_log(path)}"
        )
    with path.open("rb") as handle:
        header = handle.read(100)
    if not header.startswith(b"SQLite format 3\x00"):
        raise BackupError(
            f"source header is not SQLite format 3: {sanitize_path_for_log(path)}"
        )
    page_size_raw = int.from_bytes(header[16:18], "big")
    page_size = 65536 if page_size_raw == 1 else page_size_raw
    if page_size < 512 or (page_size & (page_size - 1)) != 0:
        raise BackupError(
            f"invalid SQLite header page_size for {sanitize_path_for_log(path)}"
        )
    page_count = int.from_bytes(header[28:32], "big")
    journal_fields = header_journal_format_fields(header)
    return {
        "page_size": page_size,
        "page_count": page_count,
        "allocated_bytes_estimate": page_size * page_count,
        "freelist_count": "not_assessed_until_apply",
        "schema_version": "not_assessed_until_apply",
        "user_version": "not_assessed_until_apply",
        "journal_mode": "not_assessed_until_apply",
        "table_count": "not_assessed_until_apply",
        "tables": "not_assessed_until_apply",
        "assessment": "sqlite_header_only",
        **journal_fields,
    }


def header_journal_format_fields(header: bytes) -> dict[str, Any]:
    """
    Journal format from SQLite header bytes 18/19 (not PRAGMA journal_mode).

    1 = legacy rollback (delete) journal; 2 = WAL format.
    Under immutable opens, PRAGMA journal_mode may falsely report ``delete``.
    """
    write_version = int(header[18])
    read_version = int(header[19])
    if write_version == 2:
        fmt = "wal"
    elif write_version == 1:
        fmt = "delete"
    else:
        fmt = f"unknown_{write_version}"
    return {
        "header_write_version": write_version,
        "header_read_version": read_version,
        "header_journal_format": fmt,
    }


def read_header_journal_format(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        header = handle.read(100)
    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        raise BackupError(
            f"cannot parse journal header for {sanitize_path_for_log(path)}"
        )
    return header_journal_format_fields(header)


def connect_source_readonly(path: Path, *, busy_timeout_ms: int) -> sqlite3.Connection:
    """Live source: mode=ro only — never immutable=1 (must observe live WAL)."""
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=max(1.0, busy_timeout_ms / 1000.0))
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    conn.execute("PRAGMA query_only=ON")
    return conn


def connect_destination(path: Path, *, busy_timeout_ms: int) -> sqlite3.Connection:
    """Writable destination for Online Backup API (script-owned partial)."""
    conn = sqlite3.connect(str(path), timeout=max(1.0, busy_timeout_ms / 1000.0))
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    # Keep partial companions out of the publish path: prefer rollback journal deleted on close.
    conn.execute("PRAGMA journal_mode=DELETE")
    return conn


def connect_destination_immutable_readonly(
    path: Path, *, busy_timeout_ms: int = 5_000
) -> sqlite3.Connection:
    """
    Verify a closed, fsynced, script-owned partial with no sidecar creation.

    Uses mode=ro&immutable=1. Never use immutable=1 on the live production source.
    """
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=max(1.0, busy_timeout_ms / 1000.0))
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    conn.execute("PRAGMA query_only=ON")
    return conn


def read_cheap_sqlite_meta(
    conn: sqlite3.Connection,
    *,
    path: Path | None = None,
    journal_reporting: str = "pragma",
) -> dict[str, Any]:
    """
    Cheap inventory. ``journal_reporting``:
      - ``pragma``: live source — PRAGMA journal_mode is authoritative operational mode
      - ``header``: immutable destination — use header bytes 18/19; do not trust pragma
    """
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    pragma_journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    tables = [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    ]
    meta: dict[str, Any] = {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "allocated_bytes": page_size * page_count,
        "freelist_bytes": page_size * freelist_count,
        "schema_version": schema_version,
        "user_version": user_version,
        "table_count": len(tables),
        "tables": tables,
        "journal_mode_pragma": pragma_journal,
    }
    if journal_reporting == "header":
        if path is None:
            raise BackupError("header journal reporting requires path")
        journal_fields = read_header_journal_format(path)
        meta.update(journal_fields)
        # Authoritative for static/immutable verification: header format, not pragma.
        meta["journal_mode"] = journal_fields["header_journal_format"]
        meta["journal_mode_source"] = "sqlite_header"
        meta["journal_mode_pragma_non_authoritative_under_immutable"] = pragma_journal
    else:
        if path is not None:
            meta.update(read_header_journal_format(path))
        meta["journal_mode"] = pragma_journal
        meta["journal_mode_source"] = "pragma"
    return meta


def verify_destination_cheap(path: Path) -> dict[str, Any]:
    """Immutable RO verification of a closed partial — must not create WAL/SHM/journal."""
    assert_sqlite_header(path)
    conn = connect_destination_immutable_readonly(path, busy_timeout_ms=5_000)
    try:
        meta = read_cheap_sqlite_meta(conn, path=path, journal_reporting="header")
    finally:
        conn.close()
    return meta


def partial_sidecar_paths(partial: Path) -> list[Path]:
    return [
        Path(str(partial) + "-journal"),
        Path(str(partial) + "-wal"),
        Path(str(partial) + "-shm"),
    ]


def ensure_no_partial_sidecars_before_publish(partial: Path) -> list[str]:
    """
    Before final publication, assert no ``-wal``/``-shm``/``-journal`` companions.

    Non-empty WAL is a verification failure. Zero-byte script-owned verification
    artifacts may be removed when demonstrably empty/safe; non-empty leftovers fail.
    """
    cleaned: list[str] = []
    failures: list[str] = []
    for path in partial_sidecar_paths(partial):
        if not path.exists():
            continue
        try:
            size = int(path.stat().st_size)
        except OSError as exc:
            raise BackupError(
                f"cannot stat unexpected partial sidecar "
                f"{sanitize_path_for_log(path)} (errno={getattr(exc, 'errno', None)})"
            ) from None
        label = sanitize_path_for_log(path)
        if size == 0:
            try:
                path.unlink()
                cleaned.append(label)
            except OSError as exc:
                failures.append(f"{label}: empty but unlink failed errno={getattr(exc, 'errno', None)}")
            continue
        if path.name.endswith("-wal"):
            failures.append(f"{label}: non-empty WAL ({size} bytes) — verification failure")
        else:
            failures.append(f"{label}: unexpected non-empty sidecar ({size} bytes)")
    remaining = [sanitize_path_for_log(p) for p in partial_sidecar_paths(partial) if p.exists()]
    if failures or remaining:
        raise BackupError(
            "unexpected partial sidecars before publish; refusing publication until "
            f"understood: failures={failures or remaining}"
        )
    return cleaned


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
        except OSError as exc:
            # Close handle for every flock OSError (Busy + other failures).
            try:
                self._fh.close()
            finally:
                self._fh = None
            if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {
                errno.EWOULDBLOCK,
                errno.EAGAIN,
            }:
                raise BackupError(
                    "another sqlite online backup appears to be running "
                    f"(lock={sanitize_path_for_log(self.path)})"
                ) from None
            raise BackupError(
                f"failed to acquire backup lock (errno={getattr(exc, 'errno', None)})"
            ) from None
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
    Attempt directory fsync. Unsupported open/fsync (DrvFS/9p EINVAL/ENOTSUP) → warning.
    """
    fn = dir_fsync or os.fsync
    unsupported = {
        errno.EINVAL,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }
    try:
        dir_fd = os.open(str(path), os.O_RDONLY)
    except OSError as exc:
        if exc.errno in unsupported:
            return {
                "directory_fsync_supported": False,
                "directory_fsync_warning": (
                    f"directory open/fsync unsupported on this filesystem "
                    f"(errno={exc.errno}); file fsync still required and performed"
                ),
            }
        raise BackupError(f"directory open for fsync failed: errno={exc.errno}") from None
    try:
        fn(dir_fd)
        return {"directory_fsync_supported": True, "directory_fsync_warning": None}
    except OSError as exc:
        if exc.errno in unsupported:
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


def probe_hardlink_no_clobber_supported(dest_parent: Path) -> None:
    """Fail before a long backup if destination FS cannot hard-link publish."""
    stamp = f"{os.getpid()}_{time.time_ns()}"
    probe_a = dest_parent / f".origenlab_hl_probe_{stamp}.a"
    probe_b = dest_parent / f".origenlab_hl_probe_{stamp}.b"
    try:
        fd = os.open(str(probe_a), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, b"x")
        finally:
            os.close(fd)
        os.link(probe_a, probe_b)
    except OSError as exc:
        raise BackupError(
            "destination filesystem does not support atomic hard-link no-clobber "
            f"publication (errno={getattr(exc, 'errno', None)}); refusing to weaken "
            "overwrite protection"
        ) from None
    finally:
        for path in (probe_b, probe_a):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass


def publish_no_clobber(src: Path, dest: Path) -> None:
    """
    Atomically publish ``src`` to ``dest`` without replacing an existing destination.

    Same-filesystem hard-link: os.link(src, dest) then unlink(src).
    Fails safely with EEXIST if ``dest`` already exists.
    """
    try:
        os.link(src, dest)
    except FileExistsError:
        raise BackupError(
            f"no-clobber publication refused: destination already exists "
            f"({sanitize_path_for_log(dest)})"
        ) from None
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise BackupError(
                f"no-clobber publication refused: destination already exists "
                f"({sanitize_path_for_log(dest)})"
            ) from None
        if exc.errno in {
            errno.EPERM,
            errno.EACCES,
            errno.EXDEV,
            errno.ENOSYS,
            errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
        }:
            raise BackupError(
                "destination filesystem rejected hard-link no-clobber publication "
                f"(errno={exc.errno})"
            ) from None
        raise BackupError(
            f"hard-link publication failed (errno={getattr(exc, 'errno', None)})"
        ) from None
    try:
        src.unlink()
    except OSError as exc:
        # Dest is published (extra link). Leaving src name is undesirable but dest is safe.
        raise BackupError(
            f"published destination but failed to unlink partial name "
            f"(errno={getattr(exc, 'errno', None)}); remove leftover partial explicitly"
        ) from None


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
    """
    Truly zero-write preflight: stat + SQLite header parse only.

    Does not call sqlite3.connect(), create locks, or write destination artifacts.
    Must not create source WAL/SHM or mutate source metadata.
    """
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
    source_meta = parse_sqlite_header_meta(source)
    return {
        "mode": "preflight",
        "apply": False,
        "completed": False,
        "writes_performed": False,
        "source_opened_with_sqlite3": False,
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
        "source_meta": source_meta,
        "notes": [
            "Preflight only: no lock, partial, backup, or manifest created.",
            "Preflight does not call sqlite3.connect(); header parse + filesystem checks only.",
            "Freelist/schema/table metadata are not_assessed_until_apply.",
            "Re-run with --apply to execute the Online Backup API snapshot.",
            "Completion requires both final DB and completed final manifest.",
            "Publication uses hard-link no-clobber (EEXIST-safe); never rewrite-replace.",
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

    When ``require_fd_observation=True`` (cutover CREATE_CURRENT_BACKUP), observes
    source FDs while the source connection is open (pre-copy, periodically during
    copy, post-copy) via the trusted backup FD taxonomy.
    """
    if not options.apply:
        return build_preflight_report(options)

    from origenlab_email_pipeline.qa.sqlite_backup_fd_observability import (
        PHASE_DURING_COPY,
        PHASE_POST_COPY,
        PHASE_PRE_COPY,
        ActiveBackupObservationCapability,
        BackupFdObservationError,
        capture_backup_source_members,
        compare_source_member_sets,
        merge_observation_aggregates,
        observe_backup_source_fds,
    )

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
    capability: ActiveBackupObservationCapability | None = None
    fd_observations: list[dict[str, Any]] = []
    members_before = None
    members_before_connect = None

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ARG001
        nonlocal interrupted
        interrupted = True

    def _raise_phase(phase: str) -> None:
        if options.fail_phase == phase:
            injected = options.fail_phase_exc
            if isinstance(injected, sqlite3.OperationalError):
                raise operational_error_to_backup_error(injected, phase=phase)
            if isinstance(injected, BaseException):
                if isinstance(injected, BackupError):
                    raise injected
                raise BackupError(sanitize_error_message(injected))
            raise BackupError(f"injected failure at phase={phase}")

    def _observe(phase: str) -> None:
        if not options.require_fd_observation and options.fd_observe_hook is None:
            return
        if capability is None or not capability.active:
            raise BackupError(
                "backup FD observation capability inactive",
                detail={"fd_observation": {"verdict": "ambiguous", "phase": phase}},
            )
        try:
            if options.fd_observe_hook is not None:
                agg = options.fd_observe_hook(capability, phase)
            else:
                agg = observe_backup_source_fds(capability, phase=phase)
        except BackupFdObservationError as exc:
            raise BackupError(
                str(exc),
                detail={"fd_observation": exc.aggregate or {"verdict": exc.verdict}},
            ) from None
        if not isinstance(agg, dict) or agg.get("verdict") != "ok":
            raise BackupError(
                "backup FD observation blocked or ambiguous",
                detail={"fd_observation": agg if isinstance(agg, dict) else {}},
            )
        fd_observations.append(agg)

    signal.signal(signal.SIGINT, _on_signal)
    try:
        signal.signal(signal.SIGTERM, _on_signal)
    except (ValueError, OSError):
        previous_sigterm = None

    published = False
    try:
        lock.acquire()
        try:
            probe_hardlink_no_clobber_supported(destination.parent)

            # Capture source identities before connect (cutover / optional observe).
            # Main must remain stable across open; WAL/SHM often appear only after
            # the readonly source connection opens WAL locking — re-baseline then.
            members_before_connect = None
            if options.require_fd_observation or options.fd_observe_hook is not None:
                try:
                    members_before_connect = capture_backup_source_members(
                        source,
                        capture_fn=options.fd_capture_hook,
                    )
                except BackupFdObservationError as exc:
                    raise BackupError(str(exc), detail={"fd_observation": exc.aggregate}) from None
                capability = ActiveBackupObservationCapability(
                    owner_pid=os.getpid(),
                    source_basename=source.name,
                    members=members_before_connect,
                    active=True,
                )

            _raise_phase(BACKUP_PHASE_SOURCE_CONNECT)
            try:
                src_conn = connect_source_readonly(
                    source, busy_timeout_ms=options.busy_timeout_ms
                )
            except sqlite3.OperationalError as exc:
                raise operational_error_to_backup_error(
                    exc, phase=BACKUP_PHASE_SOURCE_CONNECT
                ) from None

            _raise_phase(BACKUP_PHASE_SOURCE_INVENTORY)
            try:
                source_meta = read_cheap_sqlite_meta(
                    src_conn, path=source, journal_reporting="pragma"
                )
            except sqlite3.OperationalError as exc:
                raise operational_error_to_backup_error(
                    exc, phase=BACKUP_PHASE_SOURCE_INVENTORY
                ) from None
            page_size = int(source_meta["page_size"])

            # Re-baseline after WAL locking state is open; refuse main identity drift.
            if members_before_connect is not None and capability is not None:
                try:
                    members_before = capture_backup_source_members(
                        source,
                        capture_fn=options.fd_capture_hook,
                    )
                except BackupFdObservationError as exc:
                    raise BackupError(
                        str(exc), detail={"fd_observation": exc.aggregate}
                    ) from None
                main_before = members_before_connect.get(ROLE_MAIN)
                main_open = members_before.get(ROLE_MAIN)
                if (
                    main_before is None
                    or main_open is None
                    or not main_before.present
                    or not main_open.present
                    or main_before.key() != main_open.key()
                ):
                    raise BackupError(
                        "source main identity changed across backup connect",
                        detail={
                            "fd_observation": {
                                "verdict": "ambiguous",
                                "reason": "member_identity_drift_main",
                            }
                        },
                    )
                capability.members = members_before

            # Observe after WAL state is opened and before first backup() copy.
            _observe(PHASE_PRE_COPY)

            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            fd = os.open(str(partial), flags, 0o600)
            os.close(fd)

            _raise_phase(BACKUP_PHASE_DESTINATION_CONNECT)
            try:
                dest_conn = connect_destination(
                    partial, busy_timeout_ms=options.busy_timeout_ms
                )
            except sqlite3.OperationalError as exc:
                raise operational_error_to_backup_error(
                    exc, phase=BACKUP_PHASE_DESTINATION_CONNECT
                ) from None

            last_progress = t0
            last_fd_obs = t0
            progress_events = 0
            final_progress_emitted = False
            obs_interval = float(options.fd_observation_interval_seconds)

            def progress_callback(status: int, remaining: int, pagecount: int) -> None:  # noqa: ARG001
                nonlocal last_progress, last_fd_obs, progress_events, final_progress_emitted, interrupted
                if interrupted or (options.should_abort and options.should_abort()):
                    raise BackupError("backup interrupted; partial file not published")
                now = options.clock()
                remaining_i = int(remaining)
                pagecount_i = int(pagecount)
                # Bounded-interval FD observation during long copies (not per page).
                if (
                    (options.require_fd_observation or options.fd_observe_hook is not None)
                    and (now - last_fd_obs) >= obs_interval
                ):
                    _observe(PHASE_DURING_COPY)
                    last_fd_obs = now
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
            _raise_phase(BACKUP_PHASE_COPY)
            try:
                src_conn.backup(
                    dest_conn,
                    pages=options.pages_per_batch,
                    progress=progress_callback,
                    sleep=sleep_s,
                )
            except sqlite3.OperationalError as exc:
                raise operational_error_to_backup_error(
                    exc, phase=BACKUP_PHASE_COPY
                ) from None
            if options.post_copy_hook is not None:
                options.post_copy_hook()
            if not final_progress_emitted:
                _emit_progress(
                    options,
                    remaining=0,
                    pagecount=int(source_meta["page_count"]),
                    started=t0,
                    page_size=page_size,
                )
                progress_events += 1

            # Observe once more after copy and before closing the source.
            _observe(PHASE_POST_COPY)

            _raise_phase(BACKUP_PHASE_DESTINATION_COMMIT)
            try:
                dest_conn.commit()
            except sqlite3.OperationalError as exc:
                raise operational_error_to_backup_error(
                    exc, phase=BACKUP_PHASE_DESTINATION_COMMIT
                ) from None
            dest_conn.close()
            dest_conn = None
            src_conn.close()
            src_conn = None
            if capability is not None:
                capability.deactivate()

            # Recapture identities at the boundary; fail on drift.
            if members_before is not None:
                try:
                    members_after = capture_backup_source_members(
                        source,
                        capture_fn=options.fd_capture_hook,
                    )
                except BackupFdObservationError as exc:
                    raise BackupError(
                        str(exc), detail={"fd_observation": exc.aggregate}
                    ) from None
                drift = compare_source_member_sets(members_before, members_after)
                if drift is not None:
                    raise BackupError(
                        f"source artifact identity changed during backup ({drift})",
                        detail={
                            "fd_observation": {
                                "verdict": "ambiguous",
                                "reason": drift,
                            }
                        },
                    )

            fsync_file(partial)

            _raise_phase(BACKUP_PHASE_DESTINATION_VERIFY)
            try:
                dest_meta = verify_destination_cheap(partial)
            except sqlite3.OperationalError as exc:
                raise operational_error_to_backup_error(
                    exc, phase=BACKUP_PHASE_DESTINATION_VERIFY
                ) from None
            ensure_no_partial_sidecars_before_publish(partial)

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
            dir_fsync_info = fsync_directory(
                partial.parent, dir_fsync=options.dir_fsync
            )
            if dir_fsync_info.get("directory_fsync_warning"):
                warnings.append(str(dir_fsync_info["directory_fsync_warning"]))

            fd_obs_summary = (
                merge_observation_aggregates(fd_observations)
                if fd_observations
                else None
            )

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
                "publication_method": "hardlink_no_clobber",
                "destination_verification": "mode=ro&immutable=1",
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
                        "journal_mode_source",
                        "header_journal_format",
                        "header_write_version",
                        "header_read_version",
                        "table_count",
                    )
                    if k in source_meta
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
                        "journal_mode_source",
                        "header_journal_format",
                        "header_write_version",
                        "header_read_version",
                        "journal_mode_pragma_non_authoritative_under_immutable",
                        "table_count",
                    )
                    if k in dest_meta
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
                    "Publication uses hard-link no-clobber (fails safely on EEXIST).",
                    "Destination verification uses mode=ro&immutable=1 (no sidecar creation).",
                    "Destination journal_mode comes from header bytes 18/19; pragma under immutable is non-authoritative.",
                    "Crash window: final DB without final manifest is an orphan requiring explicit cleanup.",
                    "Cheap verification only; integrity_check/dbstat/deep-audit not run.",
                    "Utility opens source with URI mode=ro (not immutable); concurrent writers may still change source.",
                ],
            }
            if fd_obs_summary is not None:
                manifest["fd_observation"] = fd_obs_summary
            privacy = scan_manifest_privacy(manifest)
            if privacy:
                raise BackupError(f"manifest privacy violation: {privacy[:3]}")

            payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            if options.manifest_write_hook is not None:
                options.manifest_write_hook(manifest_partial, payload)
            else:
                manifest_partial.write_text(payload, encoding="utf-8")
            fsync_file(manifest_partial)

            _raise_phase(BACKUP_PHASE_PUBLISH)
            publish_no_clobber(partial, destination)
            published = True
            publish_no_clobber(manifest_partial, manifest_path)

            post_publish = fsync_directory(destination.parent, dir_fsync=options.dir_fsync)
            post_publish_warnings: list[str] = []
            if post_publish.get("directory_fsync_warning"):
                post_publish_warnings.append(str(post_publish["directory_fsync_warning"]))
            if post_publish_warnings:
                for warning in post_publish_warnings:
                    sink = options.progress_sink or (lambda s: print(s, file=sys.stderr))
                    sink(f"post-publish durability warning: {warning}")
                manifest = {
                    **manifest,
                    "warnings": [*warnings, *post_publish_warnings],
                    "post_publish_warnings": post_publish_warnings,
                    "directory_fsync_supported": (
                        bool(dir_fsync_info.get("directory_fsync_supported"))
                        and bool(post_publish.get("directory_fsync_supported"))
                    ),
                }

            return manifest
        finally:
            lock.release()
    except Exception as exc:
        if capability is not None:
            try:
                capability.deactivate()
            except Exception:  # noqa: BLE001
                pass
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
        if not published:
            cleanup_script_owned_artifacts(destination)
        if isinstance(exc, BackupError):
            raise
        if isinstance(exc, sqlite3.OperationalError):
            raise operational_error_to_backup_error(
                exc, phase=BACKUP_PHASE_COPY
            ) from None
        raise BackupError(sanitize_error_message(exc)) from None
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if previous_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, previous_sigterm)
            except (ValueError, OSError):
                pass
