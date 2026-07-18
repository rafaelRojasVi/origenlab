"""Centralized read-only SQLite opens for the operator API and recovery drills.

Default opens use ``mode=ro`` only (no ``immutable=1``).

Immutable recovery mode is **fail-closed**:
``ORIGENLAB_SQLITE_IMMUTABLE_RO=1`` alone is never enough. Admission also
requires explicit offline confirmation, a completed compaction manifest,
non-production path (including aliases/samefile/device+inode), no sidecars,
and a frozen candidate fingerprint.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from origenlab_email_pipeline.qa.sqlite_deep_audit import is_configured_production_db


class SqliteAdmissionError(Exception):
    """Fail-closed recovery/admission admission failure (sanitized message)."""


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class SqliteOpenPolicy:
    """Resolved per-request/open policy (never captured only at import time)."""

    immutable: bool
    recovery_mode: bool
    query_only: bool = True


def companion_paths(db: Path) -> dict[str, Path]:
    return {
        "wal": Path(str(db) + "-wal"),
        "shm": Path(str(db) + "-shm"),
        "journal": Path(str(db) + "-journal"),
    }


def existing_companions(db: Path) -> list[str]:
    """Return companion kinds that exist, including zero-byte files."""
    found: list[str] = []
    for kind, path in companion_paths(db).items():
        try:
            if path.exists():
                found.append(kind)
        except OSError:
            found.append(kind)
    return found


def fingerprint_path(path: Path) -> FileFingerprint:
    st = path.stat()
    return FileFingerprint(
        size_bytes=int(st.st_size),
        mtime_ns=int(st.st_mtime_ns),
        device=int(st.st_dev),
        inode=int(st.st_ino),
    )


def sanitize_db_basename(path: Path) -> str:
    return path.name


def sqlite_readonly_uri(sqlite_path: Path, *, immutable: bool) -> str:
    """Build a SQLite URI with percent-encoded path (spaces, #, ?, quotes, Unicode)."""
    resolved = sqlite_path.expanduser().resolve()
    # Keep path separators; encode everything else that breaks URI parsing.
    encoded = quote(resolved.as_posix(), safe="/")
    if immutable:
        return f"file:{encoded}?mode=ro&immutable=1"
    return f"file:{encoded}?mode=ro"


def _ep_settings_for_production_exclusion(*, recovery_mode: bool) -> Any:
    """
    Resolve email-pipeline settings used only for production-path exclusion.

    In recovery mode the process ``ORIGENLAB_SQLITE_PATH`` is the *candidate*, so it
    must not be treated as the production path. Temporarily unset it while loading
    dotenv-disabled EP settings so exclusion compares against default/DATA_ROOT
    production locations only.
    """
    from origenlab_email_pipeline.config import load_settings as load_ep_settings

    if not recovery_mode:
        return load_ep_settings()

    saved = os.environ.pop("ORIGENLAB_SQLITE_PATH", None)
    try:
        return load_ep_settings(enable_dotenv=False)
    finally:
        if saved is not None:
            os.environ["ORIGENLAB_SQLITE_PATH"] = saved


def assert_not_production_sqlite(db: Path, *, settings: Any | None = None) -> None:
    """Refuse configured/default production path and aliases/samefile/device+inode."""
    recovery_mode = False
    if settings is not None:
        try:
            recovery_mode = bool(resolve_open_policy(settings).recovery_mode)
        except SqliteAdmissionError:
            recovery_mode = False
    if not recovery_mode:
        from origenlab_api.settings import recovery_mode_requested_from_environ

        recovery_mode = recovery_mode_requested_from_environ()

    try:
        ep_settings = _ep_settings_for_production_exclusion(recovery_mode=recovery_mode)
    except Exception as exc:
        raise SqliteAdmissionError(
            f"unable to load settings for production exclusion ({type(exc).__name__})"
        ) from None

    try:
        if is_configured_production_db(db, ep_settings):
            raise SqliteAdmissionError(
                "refusing configured/default production SQLite path "
                "(including symlink/hardlink/samefile/device+inode aliases)"
            )
    except SqliteAdmissionError:
        raise
    except Exception as exc:
        raise SqliteAdmissionError(
            f"unable to verify production path exclusion ({type(exc).__name__})"
        ) from None


def assert_no_sidecars(db: Path, *, label: str = "database") -> None:
    companions = existing_companions(db)
    if companions:
        raise SqliteAdmissionError(
            f"{label} has companion file(s) present: {', '.join(companions)} "
            "(including zero-byte); immutable recovery requires none"
        )


def load_compaction_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise SqliteAdmissionError("compaction manifest not found")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SqliteAdmissionError(
            f"compaction manifest unreadable ({type(exc).__name__})"
        ) from None
    if not isinstance(payload, dict):
        raise SqliteAdmissionError("compaction manifest must be a JSON object")
    return payload


def validate_compaction_manifest_for_candidate(
    manifest: dict[str, Any],
    candidate: Path,
    *,
    fingerprint: FileFingerprint | None = None,
) -> dict[str, Any]:
    """Require completed compaction success and candidate agreement."""
    if manifest.get("completed") is not True:
        raise SqliteAdmissionError("compaction manifest completed!=true")

    dest_basename = manifest.get("destination_basename")
    if not dest_basename or str(dest_basename) != candidate.name:
        raise SqliteAdmissionError(
            "compaction manifest destination_basename does not match candidate"
        )

    dest_size = manifest.get("destination_size_bytes")
    try:
        dest_size_i = int(dest_size)
    except (TypeError, ValueError):
        raise SqliteAdmissionError(
            "compaction manifest missing destination_size_bytes"
        ) from None

    fp = fingerprint or fingerprint_path(candidate)
    if fp.size_bytes != dest_size_i:
        raise SqliteAdmissionError(
            "candidate size does not match compaction manifest destination_size_bytes"
        )

    verification = manifest.get("verification")
    if not isinstance(verification, dict):
        raise SqliteAdmissionError("compaction manifest missing verification object")
    if verification.get("quick_check") != "ok":
        raise SqliteAdmissionError("compaction manifest verification.quick_check!=ok")
    fk = verification.get("foreign_key_violation_count")
    try:
        if int(fk) != 0:
            raise SqliteAdmissionError(
                "compaction manifest reports foreign_key violations"
            )
    except (TypeError, ValueError):
        raise SqliteAdmissionError(
            "compaction manifest missing foreign_key_violation_count"
        ) from None

    counts = verification.get("critical_table_counts")
    if not isinstance(counts, dict) or "emails" not in counts:
        raise SqliteAdmissionError(
            "compaction manifest missing verification.critical_table_counts.emails"
        )

    method = str(manifest.get("method") or "")
    if method and method != "VACUUM INTO":
        raise SqliteAdmissionError("compaction manifest method is not VACUUM INTO")

    return {
        "destination_basename": str(dest_basename),
        "destination_size_bytes": dest_size_i,
        "quick_check": "ok",
        "foreign_key_violation_count": 0,
        "critical_table_counts": {
            k: (int(v) if v is not None else None) for k, v in counts.items()
        },
        "schema_fingerprint": verification.get("schema_fingerprint"),
    }


def admit_immutable_candidate(
    candidate: Path,
    *,
    confirm_offline_copy: bool,
    manifest_path: Path,
    settings: Any | None = None,
) -> dict[str, Any]:
    """
    Fail-closed gate for immutable opens.

    Requires confirm_offline_copy, completed compaction manifest, non-production
    path, regular file, and zero companions. Does not infer safety from filenames.
    """
    if not confirm_offline_copy:
        raise SqliteAdmissionError(
            "immutable mode requires explicit offline confirmation "
            "(--confirm-offline-copy or ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY=1)"
        )
    try:
        if candidate.is_symlink():
            raise SqliteAdmissionError(
                "refusing symlink candidate path for immutable mode "
                "(provide the real offline file path)"
            )
        if not candidate.is_file():
            raise SqliteAdmissionError("candidate must be a regular file")
    except SqliteAdmissionError:
        raise
    except OSError as exc:
        raise SqliteAdmissionError(
            f"unable to inspect candidate ({type(exc).__name__})"
        ) from None

    assert_not_production_sqlite(candidate, settings=settings)
    assert_no_sidecars(candidate, label="candidate")

    fp = fingerprint_path(candidate)
    manifest = load_compaction_manifest(manifest_path)
    verified = validate_compaction_manifest_for_candidate(
        manifest, candidate, fingerprint=fp
    )

    return {
        "admitted": True,
        "database_basename": sanitize_db_basename(candidate),
        "fingerprint": fp.to_dict(),
        "manifest": verified,
        "companions": [],
    }


def resolve_open_policy(settings: Any) -> SqliteOpenPolicy:
    """Resolve open policy from settings. Immutable requires full admission elsewhere."""
    immutable_flag = bool(getattr(settings, "sqlite_immutable_ro", False))
    confirm = bool(getattr(settings, "sqlite_confirm_offline_copy", False))
    if immutable_flag and not confirm:
        raise SqliteAdmissionError(
            "ORIGENLAB_SQLITE_IMMUTABLE_RO=1 requires "
            "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY=1"
        )
    if confirm and not immutable_flag:
        raise SqliteAdmissionError(
            "ORIGENLAB_SQLITE_CONFIRM_OFFLINE_COPY=1 requires "
            "ORIGENLAB_SQLITE_IMMUTABLE_RO=1"
        )
    recovery = immutable_flag and confirm
    return SqliteOpenPolicy(
        immutable=recovery,
        recovery_mode=recovery,
        query_only=True,
    )


def admit_settings_for_immutable_api(settings: Any) -> dict[str, Any] | None:
    """
    API startup admission. Returns admission evidence when recovery mode is on.
    Returns None for ordinary mode=ro. Raises SqliteAdmissionError on failure.
    """
    policy = resolve_open_policy(settings)
    if not policy.recovery_mode:
        return None

    if settings.resolved_api_backend() != "sqlite":
        raise SqliteAdmissionError("recovery mode requires ORIGENLAB_API_BACKEND=sqlite")
    if settings.postgres_configured():
        raise SqliteAdmissionError(
            "recovery mode refuses ORIGENLAB_POSTGRES_URL (no Postgres in recovery)"
        )

    manifest = getattr(settings, "sqlite_compaction_manifest", None)
    if manifest is None:
        raise SqliteAdmissionError(
            "recovery mode requires ORIGENLAB_SQLITE_COMPACTION_MANIFEST"
        )
    candidate = settings.resolved_sqlite_path()
    return admit_immutable_candidate(
        candidate,
        confirm_offline_copy=True,
        manifest_path=Path(manifest).expanduser(),
        settings=settings,
    )


def open_sqlite_readonly(
    sqlite_path: Path,
    *,
    immutable: bool = False,
    query_only: bool = True,
) -> sqlite3.Connection:
    """
    Low-level open. Callers enabling immutable MUST have already passed admission.

    Prefer :func:`open_operator_sqlite` for API repositories.
    """
    uri = sqlite_readonly_uri(sqlite_path, immutable=immutable)
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    if query_only:
        conn.execute("PRAGMA query_only=ON")
        active = conn.execute("PRAGMA query_only").fetchone()[0]
        if int(active) != 1:
            conn.close()
            raise SqliteAdmissionError("PRAGMA query_only failed to activate")
    return conn


def open_operator_sqlite(
    sqlite_path: Path,
    *,
    settings: Any | None = None,
) -> sqlite3.Connection:
    """
    Central API/repository connection factory.

    Policy is resolved from settings at call time (not import time). Recovery mode
    re-checks sidecars and fingerprint size before every open.
    """
    if settings is None:
        from origenlab_api.settings import get_settings

        settings = get_settings()

    policy = resolve_open_policy(settings)
    path = sqlite_path.expanduser()

    if policy.recovery_mode:
        assert_not_production_sqlite(path, settings=settings)
        assert_no_sidecars(path, label="candidate")
        manifest = getattr(settings, "sqlite_compaction_manifest", None)
        if manifest is None:
            raise SqliteAdmissionError(
                "recovery mode requires ORIGENLAB_SQLITE_COMPACTION_MANIFEST"
            )
        fp = fingerprint_path(path)
        manifest_payload = load_compaction_manifest(Path(manifest).expanduser())
        validate_compaction_manifest_for_candidate(
            manifest_payload, path, fingerprint=fp
        )

    return open_sqlite_readonly(
        path,
        immutable=policy.immutable,
        query_only=policy.query_only,
    )


def connection_mode_flags(settings: Any) -> dict[str, bool]:
    """Sanitized booleans for health (never include absolute paths)."""
    try:
        policy = resolve_open_policy(settings)
    except SqliteAdmissionError:
        return {
            "sqlite_immutable": False,
            "sqlite_query_only": False,
            "recovery_mode": False,
        }
    return {
        "sqlite_immutable": bool(policy.immutable),
        "sqlite_query_only": bool(policy.query_only),
        "recovery_mode": bool(policy.recovery_mode),
    }
