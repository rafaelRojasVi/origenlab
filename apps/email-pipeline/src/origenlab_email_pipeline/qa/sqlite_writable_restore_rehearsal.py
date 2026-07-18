"""Synthetic writable-restore rehearsal (throwaway fixtures only).

Models a controlled writable restore against *small synthetic* SQLite files
that this tool itself created. Never opens production, never opens the real
~61 GiB compact candidate, and never performs cutover/swap.

Default is **zero-write** preflight (no mkdir, no lock files, no fixtures).
Writes require ``--confirm-throwaway`` and ``--apply``.

Fixture creation (``build_fixture``) and rehearsal (``rehearse``) are
**separate** explicit apply operations.
"""

from __future__ import annotations

import enum
import errno
import fcntl
import hashlib
import json
import os
import re
import signal
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn

from origenlab_email_pipeline.config import Settings, canonical_production_sqlite_path, load_settings
from origenlab_email_pipeline.qa.sqlite_deep_audit import is_configured_production_db
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    BackupLock,
    assert_sqlite_header,
    disk_free_bytes,
    fingerprint_file,
    fsync_directory,
    fsync_file,
    paths_same_file,
    publish_no_clobber,
    sanitize_path_for_log,
)

REHEARSAL_SCHEMA_VERSION = 1
DEFAULT_SCRATCH_ROOT = Path("/tmp/origenlab_sqlite_rehearsal")
DEFAULT_CAPACITY_MARGIN_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024  # 64 MiB hard cap
COPY_CHUNK_BYTES = 1024 * 1024  # 1 MiB fixed streaming buffer
FIXTURE_IDENTITY = "origenlab-sqlite-writable-restore-rehearsal"
MARKER_TABLE = "origenlab_sqlite_rehearsal_meta"
APPROVED_BASENAME_TOKENS = ("throwaway", "rehearsal", "synthetic")
LOCK_DIR_NAME = ".origenlab_sqlite_rehearsal_locks"
TOOL_NAME = "sqlite_writable_restore_rehearsal"

# Path/basename shapes that must never be accepted (including the known offline
# snapshot / compact candidate), checked before any payload read.
FORBIDDEN_PATH_SUBSTRINGS = (
    "origenlab-sqlite-offline",
    "/emails_compact_",
    "/emails_offline_",
)
FORBIDDEN_BASENAME_PREFIXES = (
    "emails_compact_",
    "emails_offline_",
)
FORBIDDEN_BASENAMES = frozenset(
    {
        "emails.sqlite",
        "emails.sqlite-wal",
        "emails.sqlite-shm",
        "emails_compact_20260717T183537Z.sqlite",
        "emails_offline_20260716T023339Z.sqlite",
    }
)
FORBIDDEN_BASENAME_SUFFIXES = (
    ".compaction.manifest.json",
    ".manifest.json",
    ".backup.manifest.json",
)

EXIT_OK = 0
EXIT_PREFLIGHT = 2
EXIT_APPLY = 3
EXIT_VERIFY = 4
EXIT_SAFETY = 5

_ABS_PATH = re.compile(
    r"(?:/home|/mnt|/var|/tmp|/Users|/opt)/[^\s\"']+|[A-Za-z]:\\[^\s\"']+"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
# Fixture payload tokens that must not appear in sanitized evidence.
_FIXTURE_PAYLOAD_TOKENS = (
    "pre-restore",
    "post-restore-write",
    "rehearsal-insert",
    "subj-",
)


class RehearsalFailureCategory(enum.Enum):
    PREFLIGHT = "preflight"
    SAFETY = "safety"
    APPLY = "apply"
    VERIFY = "verify"


class RehearsalApplyState(enum.Enum):
    """Deterministic apply state machine (synthetic rehearsal only).

    After ``TARGET_PRESERVED``, ``preserved`` is recovery-critical while
    ``target`` is absent. Cleanup must never delete that last recoverable copy.
    Ephemeral ``partial`` / ``republish`` artifacts may always be removed.
    """

    START = "start"
    PARTIAL_COPIED = "partial_copied"
    TARGET_PUBLISHED = "target_published"
    TARGET_WRITTEN = "target_written"
    TARGET_PRESERVED = "target_preserved"
    REPUBLISH_PARTIAL = "republish_partial"
    TARGET_RESTORED = "target_restored"
    COMPLETE = "complete"


class RehearsalError(BackupError):
    """Operator-facing rehearsal failure with typed category / exit code."""

    def __init__(
        self,
        message: str,
        *,
        category: RehearsalFailureCategory,
        exit_code: int | None = None,
        recovery: str | None = None,
        recovery_evidence: dict[str, Any] | None = None,
        apply_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.exit_code = (
            exit_code
            if exit_code is not None
            else {
                RehearsalFailureCategory.PREFLIGHT: EXIT_PREFLIGHT,
                RehearsalFailureCategory.SAFETY: EXIT_SAFETY,
                RehearsalFailureCategory.APPLY: EXIT_APPLY,
                RehearsalFailureCategory.VERIFY: EXIT_VERIFY,
            }[category]
        )
        self.recovery = recovery
        self.recovery_evidence = recovery_evidence
        self.apply_state = apply_state


@dataclass
class RehearsalOptions:
    source: Path
    restore_target: Path
    scratch_root: Path = DEFAULT_SCRATCH_ROOT
    confirm_throwaway: bool = False
    apply: bool = False
    capacity_margin_bytes: int = DEFAULT_CAPACITY_MARGIN_BYTES
    max_source_bytes: int = MAX_SOURCE_BYTES
    copy_chunk_bytes: int = COPY_CHUNK_BYTES
    settings: Settings | None = None
    progress_sink: Callable[[str], None] | None = None
    clock: Callable[[], float] = time.perf_counter
    # Test-only failure injection after named phase boundaries.
    fail_after: str | None = None
    lock_dir: Path | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _progress(opts: RehearsalOptions, msg: str) -> None:
    if opts.progress_sink is not None:
        opts.progress_sink(msg)


def _fail(
    message: str,
    *,
    category: RehearsalFailureCategory,
    recovery: str | None = None,
) -> NoReturn:
    raise RehearsalError(message, category=category, recovery=recovery)


def _maybe_inject(opts: RehearsalOptions, phase: str) -> None:
    if opts.fail_after == phase:
        _fail(
            f"injected failure after {phase}",
            category=RehearsalFailureCategory.APPLY,
            recovery=(
                "Remove only script-owned *.partial.* / *.republish.* artifacts under "
                "the rehearsal scratch root after operator review; never touch production."
            ),
        )


def resolve_scratch_root(scratch_root: Path | None) -> Path:
    root = (scratch_root or DEFAULT_SCRATCH_ROOT).expanduser()
    try:
        return root.resolve(strict=False)
    except OSError as exc:
        _fail(
            f"unable to resolve scratch root ({type(exc).__name__})",
            category=RehearsalFailureCategory.SAFETY,
        )
        raise  # pragma: no cover


def path_is_under_scratch(path: Path, scratch_root: Path) -> bool:
    try:
        resolved = path.expanduser().resolve(strict=False)
        root = scratch_root.resolve(strict=False)
        return resolved == root or root in resolved.parents
    except OSError:
        return False


def _nearest_existing_dir(path: Path) -> Path | None:
    cur = path.expanduser()
    for _ in range(64):
        try:
            if cur.exists() and cur.is_dir():
                return cur
        except OSError:
            return None
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _assert_approved_basename(path: Path, *, role: str) -> None:
    name = path.name.lower()
    if not any(token in name for token in APPROVED_BASENAME_TOKENS):
        _fail(
            f"{role} basename must include one of: {', '.join(APPROVED_BASENAME_TOKENS)}",
            category=RehearsalFailureCategory.SAFETY,
        )
    if name in FORBIDDEN_BASENAMES or name in {
        "emails.sqlite",
        "emails.sqlite-wal",
        "emails.sqlite-shm",
    }:
        _fail(
            f"refusing production-like or known offline {role} basename",
            category=RehearsalFailureCategory.SAFETY,
        )
    for prefix in FORBIDDEN_BASENAME_PREFIXES:
        if name.startswith(prefix):
            _fail(
                f"refusing known offline/compact {role} basename prefix",
                category=RehearsalFailureCategory.SAFETY,
            )
    for suffix in FORBIDDEN_BASENAME_SUFFIXES:
        if name.endswith(suffix):
            _fail(
                f"refusing manifest/companion {role} basename",
                category=RehearsalFailureCategory.SAFETY,
            )


def _assert_not_forbidden_shape(path: Path, *, role: str) -> None:
    """Refuse known offline/compact/production shapes before payload reads."""
    name = path.name
    lowered = name.lower()
    text = str(path)
    # Prefer as_posix for stable substring checks without requiring existence.
    try:
        text = path.expanduser().as_posix()
    except OSError:
        pass

    if lowered in {b.lower() for b in FORBIDDEN_BASENAMES}:
        _fail(
            f"refusing forbidden {role} basename",
            category=RehearsalFailureCategory.SAFETY,
        )
    for prefix in FORBIDDEN_BASENAME_PREFIXES:
        if lowered.startswith(prefix):
            _fail(
                f"refusing offline/compact {role} path shape",
                category=RehearsalFailureCategory.SAFETY,
            )
    for marker in FORBIDDEN_PATH_SUBSTRINGS:
        if marker in text or marker in text.lower():
            _fail(
                f"refusing offline snapshot/compact {role} path",
                category=RehearsalFailureCategory.SAFETY,
            )
    for suffix in FORBIDDEN_BASENAME_SUFFIXES:
        if lowered.endswith(suffix):
            _fail(
                f"refusing manifest {role} path",
                category=RehearsalFailureCategory.SAFETY,
            )


def _refuse_production_and_aliases(path: Path, *, settings: Settings) -> None:
    try:
        if is_configured_production_db(path, settings):
            _fail(
                "refusing production SQLite path (including aliases/samefile/device+inode)",
                category=RehearsalFailureCategory.SAFETY,
            )
        canonical = canonical_production_sqlite_path()
        if paths_same_file(path, canonical):
            _fail(
                "refusing canonical production SQLite path",
                category=RehearsalFailureCategory.SAFETY,
            )
        try:
            resolved = path.expanduser().resolve(strict=False)
            if resolved == canonical.resolve(strict=False):
                _fail(
                    "refusing canonical production SQLite path",
                    category=RehearsalFailureCategory.SAFETY,
                )
            prod_dir = canonical.parent.resolve(strict=False)
            if resolved == prod_dir or prod_dir in resolved.parents:
                _fail(
                    "refusing path underneath the production SQLite directory",
                    category=RehearsalFailureCategory.SAFETY,
                )
        except OSError as exc:
            _fail(
                f"unable to verify production directory exclusion ({type(exc).__name__})",
                category=RehearsalFailureCategory.SAFETY,
            )
    except RehearsalError:
        raise
    except OSError as exc:
        _fail(
            f"unable to verify production exclusion ({type(exc).__name__})",
            category=RehearsalFailureCategory.SAFETY,
        )


def companion_paths(path: Path) -> list[Path]:
    return [Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")]


def target_collision_paths(target: Path) -> list[Path]:
    """Pre-existing companions / partials / rollback / manifest that block apply."""
    paths = list(companion_paths(target))
    parent = target.parent
    name = target.name
    # Exact known suffixes plus glob-like collision names we own.
    for suffix in (
        f"{name}.partial",
        f"{name}.manifest.json",
        f"{name}.rehearsal.manifest.json",
        f"{name}.rolled",
        f"{name}.republish",
        f"{name}.preserved",
    ):
        paths.append(parent / suffix)
    return paths


def list_existing_collisions(target: Path) -> list[str]:
    found: list[str] = []
    for path in target_collision_paths(target):
        if _path_lexists(path):
            found.append(path.name)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                found.append(f"{path.name}:non_regular")
    # Script-owned unique partials/republish leftovers with pid/ns suffixes.
    if target.parent.is_dir():
        for child in target.parent.iterdir():
            n = child.name
            if n.startswith(f"{target.name}.partial.") or n.startswith(
                f"{target.name}.republish."
            ):
                found.append(n)
            if n.startswith(f"{target.name}.rolled.") or n.startswith(
                f"{target.name}.preserved."
            ):
                found.append(n)
            if child.is_symlink() and (
                n == target.name
                or n.startswith(f"{target.name}.")
            ):
                found.append(f"{n}:symlink")
    return sorted(set(found))


def _path_lexists(path: Path) -> bool:
    """True if the path name exists, including broken symlinks."""
    try:
        return path.is_symlink() or path.exists()
    except OSError:
        return False


def stream_copy_file(
    source: Path,
    destination: Path,
    *,
    chunk_bytes: int = COPY_CHUNK_BYTES,
    progress_sink: Callable[[str], None] | None = None,
) -> int:
    """Bounded streaming copy with exclusive destination create (O_EXCL).

    Never loads the whole file into memory. Refuses if ``destination`` already
    exists (including races after a prior existence check).
    """
    if chunk_bytes <= 0:
        _fail(
            "copy chunk size must be positive",
            category=RehearsalFailureCategory.APPLY,
        )
    if _path_lexists(destination):
        _fail(
            "exclusive create refused: destination already exists",
            category=RehearsalFailureCategory.APPLY,
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(destination), flags, 0o600)
    except FileExistsError:
        _fail(
            "exclusive create refused: destination already exists",
            category=RehearsalFailureCategory.APPLY,
        )
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            _fail(
                "exclusive create refused: destination already exists",
                category=RehearsalFailureCategory.APPLY,
            )
        _fail(
            f"exclusive create failed (errno={getattr(exc, 'errno', None)})",
            category=RehearsalFailureCategory.APPLY,
        )
    copied = 0
    with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
        while True:
            buf = src.read(chunk_bytes)
            if not buf:
                break
            dst.write(buf)
            copied += len(buf)
            if progress_sink is not None and copied % (chunk_bytes * 8) == 0:
                progress_sink(f"stream_copy_bytes={copied}")
        dst.flush()
        os.fsync(dst.fileno())
    return copied


def content_sha256(path: Path, *, chunk_bytes: int = COPY_CHUNK_BYTES) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            buf = handle.read(chunk_bytes)
            if not buf:
                break
            digest.update(buf)
    return digest.hexdigest()


def _open_marker_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only=ON")
    return conn


def read_fixture_marker(path: Path) -> dict[str, Any]:
    """Require the dedicated rehearsal metadata marker; refuse unmarked DBs."""
    try:
        conn = _open_marker_readonly(path)
    except sqlite3.Error as exc:
        _fail(
            f"unable to open source for marker check ({type(exc).__name__})",
            category=RehearsalFailureCategory.SAFETY,
        )
        raise  # pragma: no cover
    try:
        row = conn.execute(
            f"SELECT schema_version, fixture_identity, created_at_utc, tool "
            f"FROM {MARKER_TABLE} WHERE id = 1"
        ).fetchone()
    except sqlite3.Error:
        _fail(
            "source missing dedicated rehearsal metadata marker table",
            category=RehearsalFailureCategory.SAFETY,
        )
        raise  # pragma: no cover
    finally:
        conn.close()
    if row is None:
        _fail(
            "source missing rehearsal metadata marker row",
            category=RehearsalFailureCategory.SAFETY,
        )
    schema_version, fixture_identity, created_at_utc, tool = row
    if int(schema_version) != REHEARSAL_SCHEMA_VERSION:
        _fail(
            "rehearsal marker schema_version mismatch",
            category=RehearsalFailureCategory.SAFETY,
        )
    if str(fixture_identity) != FIXTURE_IDENTITY:
        _fail(
            "rehearsal marker fixture_identity mismatch",
            category=RehearsalFailureCategory.SAFETY,
        )
    if str(tool) != TOOL_NAME:
        _fail(
            "rehearsal marker tool mismatch",
            category=RehearsalFailureCategory.SAFETY,
        )
    return {
        "schema_version": int(schema_version),
        "fixture_identity": str(fixture_identity),
        "created_at_utc": str(created_at_utc),
        "tool": str(tool),
    }


def schema_digest(path: Path) -> str:
    conn = _open_marker_readonly(path)
    try:
        rows = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "ORDER BY type, name, tbl_name"
        ).fetchall()
    finally:
        conn.close()
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def table_row_counts(path: Path) -> dict[str, int]:
    conn = _open_marker_readonly(path)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        counts: dict[str, int] = {}
        for name in names:
            # Identifiers come only from sqlite_master of our synthetic fixture.
            counts[name] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        return counts
    finally:
        conn.close()


def run_quick_check(path: Path) -> str:
    conn = _open_marker_readonly(path)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()
        status = str(result[0]) if result else "missing"
        if status != "ok":
            _fail(
                f"quick_check failed: {status}",
                category=RehearsalFailureCategory.VERIFY,
            )
        return status
    finally:
        conn.close()


def run_fk_check(path: Path) -> int:
    conn = _open_marker_readonly(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        n = len(rows)
        if n:
            _fail(
                f"foreign_key_check reported {n} violation(s)",
                category=RehearsalFailureCategory.VERIFY,
            )
        return n
    finally:
        conn.close()


def reopen_sqlite_readonly_verified(path: Path) -> dict[str, Any]:
    """Prove RO reopen + query_only (not a full apps/api process reopen)."""
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        active = conn.execute("PRAGMA query_only").fetchone()[0]
        if not active:
            _fail(
                "PRAGMA query_only failed to activate on reopen",
                category=RehearsalFailureCategory.VERIFY,
            )
        marker = conn.execute(
            f"SELECT fixture_identity FROM {MARKER_TABLE} WHERE id = 1"
        ).fetchone()
        if marker is None or marker[0] != FIXTURE_IDENTITY:
            _fail(
                "readonly reopen marker mismatch",
                category=RehearsalFailureCategory.VERIFY,
            )
        return {
            "sqlite_readonly_reopen_verified": True,
            "query_only": True,
            "marker_ok": True,
        }
    finally:
        conn.close()


def assert_report_privacy(report: dict[str, Any]) -> None:
    blob = json.dumps(report, sort_keys=True)
    if _ABS_PATH.search(blob):
        _fail(
            "evidence contains absolute path",
            category=RehearsalFailureCategory.VERIFY,
        )
    if _EMAIL.search(blob):
        _fail(
            "evidence contains email-like text",
            category=RehearsalFailureCategory.VERIFY,
        )
    low = blob.lower()
    for token in _FIXTURE_PAYLOAD_TOKENS:
        if token.lower() in low:
            _fail(
                "evidence contains fixture payload value",
                category=RehearsalFailureCategory.VERIFY,
            )


def build_synthetic_source(
    path: Path,
    *,
    scratch_root: Path | None = None,
    confirm_throwaway: bool = False,
    apply: bool = False,
    rows: int = 8,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Create a tiny marked throwaway SQLite fixture (explicit apply write)."""
    if not confirm_throwaway:
        _fail(
            "fixture build requires --confirm-throwaway",
            category=RehearsalFailureCategory.SAFETY,
        )
    if not apply:
        _fail(
            "fixture build is a write and requires --apply",
            category=RehearsalFailureCategory.SAFETY,
        )

    settings = settings or load_settings(enable_dotenv=False)
    root = resolve_scratch_root(scratch_root)
    dest = path.expanduser()

    _assert_not_forbidden_shape(dest, role="fixture")
    _assert_approved_basename(dest, role="fixture")
    _refuse_production_and_aliases(dest, settings=settings)
    if not path_is_under_scratch(dest, root):
        _fail(
            "fixture path must resolve under the rehearsal scratch root",
            category=RehearsalFailureCategory.SAFETY,
        )
    if dest.exists():
        _fail(
            "fixture path already exists (no-clobber)",
            category=RehearsalFailureCategory.SAFETY,
        )
    if dest.is_symlink():
        _fail("refusing symlink fixture path", category=RehearsalFailureCategory.SAFETY)

    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(dest)
    try:
        conn.execute(
            f"CREATE TABLE {MARKER_TABLE} ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "schema_version INTEGER NOT NULL, "
            "fixture_identity TEXT NOT NULL, "
            "created_at_utc TEXT NOT NULL, "
            "tool TEXT NOT NULL"
            ")"
        )
        conn.execute(
            f"INSERT INTO {MARKER_TABLE} "
            "(id, schema_version, fixture_identity, created_at_utc, tool) "
            "VALUES (1, ?, ?, ?, ?)",
            (REHEARSAL_SCHEMA_VERSION, FIXTURE_IDENTITY, _iso_now(), TOOL_NAME),
        )
        conn.execute(
            "CREATE TABLE emails ("
            "id INTEGER PRIMARY KEY, subject TEXT, marker TEXT)"
        )
        conn.execute(
            "CREATE TABLE attachments ("
            "id INTEGER PRIMARY KEY, email_id INTEGER, "
            "FOREIGN KEY (email_id) REFERENCES emails(id))"
        )
        for i in range(1, rows + 1):
            conn.execute(
                "INSERT INTO emails (id, subject, marker) VALUES (?,?,?)",
                (i, f"subj-{i}", "pre-restore"),
            )
            if i % 2 == 0:
                conn.execute(
                    "INSERT INTO attachments (id, email_id) VALUES (?,?)",
                    (i, i),
                )
        conn.commit()
    finally:
        conn.close()
    fsync_file(dest)
    fsync_directory(dest.parent)
    return {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "mode": "writable_restore_build_fixture",
        "apply": True,
        "completed": True,
        "fixture_basename": dest.name,
        "fixture_identity": FIXTURE_IDENTITY,
        "size_bytes": int(dest.stat().st_size),
        "captured_at_utc": _iso_now(),
    }


def _validate_common_paths(opts: RehearsalOptions, *, require_source_file: bool) -> tuple[Path, Path, Path, Settings]:
    settings = opts.settings or load_settings(enable_dotenv=False)
    root = resolve_scratch_root(opts.scratch_root)
    source = opts.source.expanduser()
    target = opts.restore_target.expanduser()

    if not opts.confirm_throwaway:
        _fail(
            "rehearsal requires --confirm-throwaway acknowledging synthetic/throwaway paths only",
            category=RehearsalFailureCategory.SAFETY,
        )

    for role, path in (("source", source), ("target", target)):
        _assert_not_forbidden_shape(path, role=role)
        _assert_approved_basename(path, role=role)
        _refuse_production_and_aliases(path, settings=settings)
        if not path_is_under_scratch(path, root):
            _fail(
                f"{role} must resolve under the rehearsal scratch root",
                category=RehearsalFailureCategory.SAFETY,
            )
        try:
            # Refuse symlinks including broken ones (exists() is false for broken links).
            if path.is_symlink():
                _fail(
                    f"refusing symlink {role}",
                    category=RehearsalFailureCategory.SAFETY,
                )
        except OSError as exc:
            _fail(
                f"unable to inspect {role} ({type(exc).__name__})",
                category=RehearsalFailureCategory.SAFETY,
            )

    if paths_same_file(source, target):
        _fail(
            "source and restore target must differ",
            category=RehearsalFailureCategory.SAFETY,
        )
    try:
        if source.resolve(strict=False) == target.resolve(strict=False):
            _fail(
                "source and restore target resolve to the same path",
                category=RehearsalFailureCategory.SAFETY,
            )
    except OSError:
        pass

    if require_source_file:
        if source.is_symlink() or not source.is_file():
            _fail(
                "source must be an existing regular non-symlink file",
                category=RehearsalFailureCategory.PREFLIGHT,
            )
        size = int(source.stat().st_size)
        if size > int(opts.max_source_bytes):
            _fail(
                f"source exceeds hard maximum size "
                f"({size} > {int(opts.max_source_bytes)})",
                category=RehearsalFailureCategory.SAFETY,
            )
        # Size gate happens before header/marker/payload reads of large bodies.
        assert_sqlite_header(source)
        for suffix_path in companion_paths(source):
            if _path_lexists(suffix_path):
                _fail(
                    f"source has companion file(s): {suffix_path.name}",
                    category=RehearsalFailureCategory.PREFLIGHT,
                )
        read_fixture_marker(source)

    # Target must not lexist (covers broken symlinks and non-regular names).
    if target.is_symlink():
        _fail(
            "refusing symlink target (including broken symlinks)",
            category=RehearsalFailureCategory.SAFETY,
        )
    if _path_lexists(target):
        if target.exists() and not target.is_file():
            _fail(
                "refusing non-regular target path",
                category=RehearsalFailureCategory.SAFETY,
            )
        _fail(
            "restore target already exists (no-clobber)",
            category=RehearsalFailureCategory.PREFLIGHT,
        )
    collisions = list_existing_collisions(target)
    if collisions:
        _fail(
            f"pre-existing target companion/partial/rollback/manifest: "
            f"{', '.join(collisions[:8])}",
            category=RehearsalFailureCategory.PREFLIGHT,
            recovery=(
                "Remove only script-owned rehearsal artifacts under the scratch "
                "root after review; do not delete production or offline clones."
            ),
        )

    return source, target, root, settings


def preflight(opts: RehearsalOptions) -> dict[str, Any]:
    """Zero-write preflight: never creates dirs, files, locks, fixtures, or sidecars."""
    source, target, root, _settings = _validate_common_paths(
        opts, require_source_file=True
    )

    parent = target.parent
    parent_exists = parent.is_dir()
    apply_would_create_parent = not parent_exists
    capacity_probe = parent if parent_exists else _nearest_existing_dir(parent)
    if capacity_probe is None:
        _fail(
            "cannot assess free space: no existing ancestor directory for target",
            category=RehearsalFailureCategory.PREFLIGHT,
        )
        raise  # pragma: no cover

    free = disk_free_bytes(capacity_probe)
    need = int(source.stat().st_size) + int(opts.capacity_margin_bytes)
    if free < need:
        _fail(
            f"insufficient free space for throwaway restore target "
            f"(need>={need} free={free})",
            category=RehearsalFailureCategory.PREFLIGHT,
        )

    fp = fingerprint_file(source)
    marker = read_fixture_marker(source)
    report = {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "mode": "writable_restore_rehearsal_preflight",
        "confirm_throwaway": True,
        "apply": False,
        "completed": True,
        "source_basename": source.name,
        "target_basename": target.name,
        "scratch_root_basename": root.name,
        "source_fingerprint": fp.to_dict(),
        "fixture_marker_present": True,
        "fixture_identity_ok": marker["fixture_identity"] == FIXTURE_IDENTITY,
        "companions": [],
        "target_parent_exists": parent_exists,
        "apply_would_create_parent": apply_would_create_parent,
        "capacity": {
            "free_bytes": free,
            "required_bytes": need,
            "max_source_bytes": int(opts.max_source_bytes),
            "copy_chunk_bytes": int(opts.copy_chunk_bytes),
        },
        "production_excluded": True,
        "zero_write_preflight": True,
        "captured_at_utc": _iso_now(),
        "notes": [
            "Synthetic/throwaway rehearsal only; not a real 61 GiB restore.",
            "Not production cutover.",
            "Fixture build and rehearsal are separate --apply operations.",
        ],
    }
    assert_report_privacy(report)
    return report


def _lock_path(opts: RehearsalOptions, scratch_root: Path, source: Path) -> Path:
    base = opts.lock_dir or (scratch_root / LOCK_DIR_NAME)
    try:
        fp = fingerprint_file(source)
        key = f"dev{fp.device}_ino{fp.inode}.lock"
    except OSError:
        key = f"name_{source.name}.lock"
    return base / key


def _owned_partial_name(target: Path, kind: str) -> Path:
    return target.with_name(f"{target.name}.{kind}.{os.getpid()}.{time.time_ns()}")


def _unlink_ephemeral(paths: list[Path]) -> list[str]:
    removed: list[str] = []
    for path in list(paths):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
                removed.append(path.name)
        except OSError:
            pass
    return removed


def finalize_failure_artifacts(
    *,
    target: Path,
    ephemeral: list[Path],
    preserved: Path | None,
    apply_state: RehearsalApplyState,
) -> dict[str, Any]:
    """Handle failure without destroying the last recoverable DB copy.

    - Always remove ephemeral partial/republish files.
    - Never delete ``preserved`` while ``target`` is absent.
    - If target is absent and preserved exists: restore preserved→target
      (no-clobber) with directory fsync, or retain preserved with evidence.
    """
    removed = _unlink_ephemeral(ephemeral)
    evidence: dict[str, Any] = {
        "apply_state": apply_state.value,
        "target_present": False,
        "preserved_present": bool(preserved is not None and _path_lexists(preserved)),
        "preserved_basename": (
            sanitize_path_for_log(preserved) if preserved is not None else None
        ),
        "preserved_restored_to_target": False,
        "preserved_retained_for_recovery": False,
        "removed_ephemeral": removed,
        "last_recoverable_deleted": False,
    }

    target_present = target.exists() and target.is_file() and not target.is_symlink()
    preserved_present = (
        preserved is not None
        and preserved.exists()
        and preserved.is_file()
        and not preserved.is_symlink()
    )
    evidence["target_present"] = target_present
    evidence["preserved_present"] = preserved_present

    if preserved_present and not target_present:
        # Recovery-critical: restore or retain — never unlink.
        try:
            if _path_lexists(target):
                evidence["preserved_retained_for_recovery"] = True
            else:
                assert preserved is not None
                os.rename(preserved, target)
                fsync_directory(target.parent)
                evidence["preserved_restored_to_target"] = True
                evidence["target_present"] = True
                evidence["preserved_present"] = False
        except OSError:
            evidence["preserved_retained_for_recovery"] = True
            evidence["preserved_present"] = bool(
                preserved is not None and preserved.exists()
            )
    elif preserved_present and target_present:
        # Target is authoritative again; retain preserved for operator inspection
        # on failure (do not auto-delete the post-write copy on crash paths).
        evidence["preserved_retained_for_recovery"] = True

    # Invariant: never both absent after we had entered TARGET_PRESERVED+.
    if apply_state in {
        RehearsalApplyState.TARGET_PRESERVED,
        RehearsalApplyState.REPUBLISH_PARTIAL,
        RehearsalApplyState.TARGET_RESTORED,
    }:
        if not evidence["target_present"] and not evidence["preserved_present"]:
            evidence["last_recoverable_deleted"] = True

    return evidence


def validate_planned_cutover_topology(
    *,
    mnt_d_free_bytes: int,
    root_free_bytes: int,
    snapshot_size_bytes: int,
    compact_capacity_required_bytes: int,
    staged_compact_size_bytes: int,
) -> dict[str, Any]:
    """Synthetic capacity plan for documented topology (no I/O, no real paths).

    Recommended plan: Online Backup snapshot on /mnt/d, compact staging on /.
    Does **not** require a second ~127 GiB artifact on /mnt/d.
    """
    dual_on_mnt_d_need = int(snapshot_size_bytes) + int(compact_capacity_required_bytes)
    snapshot_ok = int(mnt_d_free_bytes) >= int(snapshot_size_bytes)
    compact_on_root_ok = int(root_free_bytes) >= int(compact_capacity_required_bytes)
    dual_on_mnt_d_ok = int(mnt_d_free_bytes) >= dual_on_mnt_d_need
    root_after_stage = int(root_free_bytes) - int(staged_compact_size_bytes)
    recommended_ok = snapshot_ok and compact_on_root_ok
    return {
        "schema_version": REHEARSAL_SCHEMA_VERSION,
        "mode": "planned_cutover_topology_validation",
        "snapshot_on_mnt_d_ok": snapshot_ok,
        "compact_staging_on_root_ok": compact_on_root_ok,
        "dual_artifacts_on_mnt_d_ok": dual_on_mnt_d_ok,
        "second_full_snapshot_on_mnt_d_required": False,
        "dual_on_mnt_d_required_bytes": dual_on_mnt_d_need,
        "root_free_after_staged_compact_bytes": root_after_stage,
        "recommended_topology": "online_snapshot_mnt_d__compact_staging_root",
        "recommended_topology_ok": recommended_ok,
        "fail_closed": not recommended_ok,
        "notes": [
            "Synthetic capacity math only; opens no mounts or databases.",
            "Dual snapshot+compact on /mnt/d fails closed at current free space.",
        ],
    }


def apply_rehearsal(opts: RehearsalOptions) -> dict[str, Any]:
    report = preflight(opts)
    if not opts.apply:
        return report

    source = opts.source.expanduser()
    target = opts.restore_target.expanduser()
    scratch_root = resolve_scratch_root(opts.scratch_root)
    fp_before = fingerprint_file(source)
    source_hash_before = content_sha256(source, chunk_bytes=opts.copy_chunk_bytes)
    schema_before = schema_digest(source)
    counts_before = table_row_counts(source)
    t0 = opts.clock()

    state = RehearsalApplyState.START
    ephemeral: list[Path] = []
    preserved: Path | None = None
    lock: BackupLock | None = None
    previous_handlers: dict[int, Any] = {}

    def _handle_failure(exc: RehearsalError) -> RehearsalError:
        evidence = finalize_failure_artifacts(
            target=target,
            ephemeral=ephemeral,
            preserved=preserved,
            apply_state=state,
        )
        ephemeral.clear()
        exc.recovery_evidence = evidence
        exc.apply_state = state.value
        if evidence.get("last_recoverable_deleted"):
            exc.recovery = (
                "CRITICAL: last recoverable rehearsal copy missing under scratch root; "
                "do not touch production or offline clones."
            )
        elif evidence.get("preserved_restored_to_target"):
            exc.recovery = (
                "Restored preserved→target under scratch root; remove target after review "
                "before retrying rehearsal."
            )
        elif evidence.get("preserved_retained_for_recovery"):
            exc.recovery = (
                "Retained script-owned *.preserved.* under scratch root; restore or remove "
                "only that artifact after review — never touch production."
            )
        elif evidence.get("removed_ephemeral"):
            exc.recovery = (
                "Removed script-owned ephemeral partial/republish files under scratch root only."
            )
        return exc

    def _on_signal(signum: int, _frame: Any) -> None:
        err = RehearsalError(
            f"interrupted by signal {signum}",
            category=RehearsalFailureCategory.APPLY,
        )
        raise _handle_failure(err)

    try:
        # Apply may create the target parent under the scratch root only.
        if not target.parent.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)

        lock_path = _lock_path(opts, scratch_root, source)
        lock = BackupLock(lock_path)
        try:
            lock.acquire()
        except BackupError as exc:
            raise RehearsalError(
                str(exc),
                category=RehearsalFailureCategory.APPLY,
            ) from None

        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.signal(sig, _on_signal)

        _progress(opts, "stream_copy_source_to_partial")
        if fingerprint_file(source) != fp_before:
            _fail(
                "source fingerprint changed before copy",
                category=RehearsalFailureCategory.VERIFY,
            )

        partial = _owned_partial_name(target, "partial")
        if _path_lexists(target):
            _fail(
                "partial/target exists (no-clobber)",
                category=RehearsalFailureCategory.APPLY,
            )
        # Track before exclusive create so a raced name is cleaned on failure.
        ephemeral.append(partial)
        if _path_lexists(partial):
            _fail(
                "partial exists (no-clobber)",
                category=RehearsalFailureCategory.APPLY,
            )
        stream_copy_file(
            source,
            partial,
            chunk_bytes=opts.copy_chunk_bytes,
            progress_sink=opts.progress_sink,
        )
        fsync_file(partial)
        state = RehearsalApplyState.PARTIAL_COPIED
        _maybe_inject(opts, "initial_copy")

        _progress(opts, "publish_no_clobber")
        try:
            publish_no_clobber(partial, target)
        except BackupError as exc:
            raise RehearsalError(
                str(exc),
                category=RehearsalFailureCategory.APPLY,
            ) from None
        if partial in ephemeral:
            ephemeral.remove(partial)
        fsync_file(target)
        fsync_directory(target.parent)
        state = RehearsalApplyState.TARGET_PUBLISHED
        _maybe_inject(opts, "publication")

        if fingerprint_file(source) != fp_before:
            _fail(
                "source fingerprint changed after publication",
                category=RehearsalFailureCategory.VERIFY,
            )

        _progress(opts, "writable_transaction")
        conn = sqlite3.connect(target)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE emails SET marker = ? WHERE id = ?",
                ("post-restore-write", 1),
            )
            conn.execute(
                "INSERT INTO emails (id, subject, marker) VALUES (?,?,?)",
                (9001, "rehearsal-insert", "post-restore-write"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT marker FROM emails WHERE id = 1"
            ).fetchone()
            count = int(conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
            if row is None or row[0] != "post-restore-write":
                _fail(
                    "writable transaction commit verification failed",
                    category=RehearsalFailureCategory.VERIFY,
                )
            if count < 2:
                _fail(
                    "unexpected email count after writable transaction",
                    category=RehearsalFailureCategory.VERIFY,
                )
        except RehearsalError:
            conn.rollback()
            raise
        except sqlite3.Error as exc:
            conn.rollback()
            _fail(
                f"writable transaction failed ({type(exc).__name__})",
                category=RehearsalFailureCategory.APPLY,
            )
        finally:
            conn.close()
        state = RehearsalApplyState.TARGET_WRITTEN
        _maybe_inject(opts, "writable_commit")

        # Two-phase preserve + republish (crash-recoverable — not one atomic swap).
        _progress(opts, "preserve_target_then_republish_source")
        preserved = _owned_partial_name(target, "preserved")
        if _path_lexists(preserved):
            _fail(
                "preserve path exists (no-clobber)",
                category=RehearsalFailureCategory.APPLY,
            )
        os.rename(target, preserved)
        state = RehearsalApplyState.TARGET_PRESERVED
        _maybe_inject(opts, "target_preservation")

        republish = _owned_partial_name(target, "republish")
        if _path_lexists(target):
            _fail(
                "republish/target exists (no-clobber)",
                category=RehearsalFailureCategory.APPLY,
            )
        ephemeral.append(republish)
        if _path_lexists(republish):
            _fail(
                "republish exists (no-clobber)",
                category=RehearsalFailureCategory.APPLY,
            )
        stream_copy_file(
            source,
            republish,
            chunk_bytes=opts.copy_chunk_bytes,
            progress_sink=opts.progress_sink,
        )
        fsync_file(republish)
        state = RehearsalApplyState.REPUBLISH_PARTIAL
        try:
            publish_no_clobber(republish, target)
        except BackupError as exc:
            raise RehearsalError(
                str(exc),
                category=RehearsalFailureCategory.APPLY,
            ) from None
        if republish in ephemeral:
            ephemeral.remove(republish)
        fsync_file(target)
        fsync_directory(target.parent)
        state = RehearsalApplyState.TARGET_RESTORED
        _maybe_inject(opts, "rollback_republish")

        # Safe to remove preserved only while target is present again.
        if not (target.exists() and target.is_file()):
            _fail(
                "target missing after republish; refusing to delete preserved",
                category=RehearsalFailureCategory.APPLY,
            )
        try:
            assert preserved is not None
            preserved.unlink(missing_ok=True)
            preserved = None
        except OSError:
            _fail(
                "rollback republish succeeded but preserved artifact unlink failed; "
                "remove the script-owned preserved file after review",
                category=RehearsalFailureCategory.APPLY,
                recovery="Remove only the rehearsal *.preserved.* artifact under scratch root.",
            )

        # Full fixture verification after rollback.
        run_quick_check(target)
        run_fk_check(target)
        if schema_digest(target) != schema_before:
            _fail(
                "schema digest mismatch after rollback",
                category=RehearsalFailureCategory.VERIFY,
            )
        if table_row_counts(target) != counts_before:
            _fail(
                "row counts mismatch after rollback",
                category=RehearsalFailureCategory.VERIFY,
            )
        if content_sha256(target, chunk_bytes=opts.copy_chunk_bytes) != source_hash_before:
            _fail(
                "content hash mismatch after rollback",
                category=RehearsalFailureCategory.VERIFY,
            )
        marker_after = read_fixture_marker(target)
        if marker_after["fixture_identity"] != FIXTURE_IDENTITY:
            _fail(
                "marker mismatch after rollback",
                category=RehearsalFailureCategory.VERIFY,
            )
        reopen_info = reopen_sqlite_readonly_verified(target)

        fp_after = fingerprint_file(source)
        if fp_after != fp_before:
            _fail(
                "source fingerprint changed during rehearsal",
                category=RehearsalFailureCategory.VERIFY,
            )
        if content_sha256(source, chunk_bytes=opts.copy_chunk_bytes) != source_hash_before:
            _fail(
                "source content hash changed during rehearsal",
                category=RehearsalFailureCategory.VERIFY,
            )
        if any(_path_lexists(p) for p in companion_paths(source)) or any(
            _path_lexists(p) for p in companion_paths(target)
        ):
            _fail(
                "unexpected sidecars after rehearsal",
                category=RehearsalFailureCategory.VERIFY,
            )

        state = RehearsalApplyState.COMPLETE
        elapsed = opts.clock() - t0
        report.update(
            {
                "mode": "writable_restore_rehearsal_apply",
                "apply": True,
                "completed": True,
                "captured_at_utc": _iso_now(),
                "elapsed_seconds": round(float(elapsed), 6),
                "apply_state": state.value,
                "source_fingerprint_before": fp_before.to_dict(),
                "source_fingerprint_after": fp_after.to_dict(),
                "source_fingerprint_unchanged": True,
                "source_content_hash_unchanged": True,
                "writable_transaction_commit_verified": True,
                "cutover_rollback_verified": True,
                "quick_check_ok": True,
                "foreign_key_check_violations": 0,
                "schema_digest_matched": True,
                "row_counts_matched": True,
                "content_hash_matched": True,
                "marker_values_matched": True,
                "zero_sidecars": True,
                "exclusive_temp_create": True,
                "copy_chunk_bytes": int(opts.copy_chunk_bytes),
                "max_source_bytes": int(opts.max_source_bytes),
                "streaming_copy_used": True,
                **reopen_info,
                "target_basename_after": target.name,
                "notes": [
                    "Synthetic/throwaway rehearsal only; not a real 61 GiB restore.",
                    "Source fingerprint unchanged; preserved removed only after target restored.",
                    "Two-phase preserve+republish is crash-recoverable, not a single atomic swap.",
                    "Not production cutover.",
                ],
            }
        )
        report["source_path_sanitized"] = sanitize_path_for_log(source)
        report["target_path_sanitized"] = sanitize_path_for_log(target)
        assert_report_privacy(report)
        return report
    except RehearsalError as exc:
        raise _handle_failure(exc) from None
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if lock is not None:
            lock.release()


def run_rehearsal(opts: RehearsalOptions) -> dict[str, Any]:
    return apply_rehearsal(opts)


def tree_snapshot(root: Path) -> dict[str, tuple[int, int] | str]:
    """Deterministic directory tree fingerprint for zero-write proofs."""
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
