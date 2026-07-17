"""Safe offline SQLite compaction via VACUUM INTO (never in-place VACUUM).

Creates a compacted candidate from a verified offline snapshot. Default is
zero-write preflight. Writes require ``--confirm-offline-copy`` and ``--apply``.

Never swaps the candidate into production, never deletes the source snapshot,
and never treats historical/referenced rows as deletable.
"""

from __future__ import annotations

import hashlib
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

from origenlab_email_pipeline.config import Settings, load_settings
from origenlab_email_pipeline.qa.sqlite_deep_audit import (
    is_configured_production_db,
    quote_identifier,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    BackupLock,
    assert_sqlite_header,
    disk_free_bytes,
    fsync_directory,
    fsync_file,
    fingerprint_file,
    lock_path_for,
    parse_sqlite_header_meta,
    paths_same_file,
    probe_hardlink_no_clobber_supported,
    publish_no_clobber,
    required_capacity_bytes,
    sanitize_path_for_log,
    scan_manifest_privacy,
)

COMPACTION_SCHEMA_VERSION = 1
DEFAULT_CAPACITY_MARGIN_RATIO = 0.05
DEFAULT_CAPACITY_MARGIN_MIN_BYTES = 256 * 1024 * 1024
LOCK_DIR_NAME = ".origenlab_sqlite_offline_compaction_locks"
CRITICAL_TABLES: tuple[str, ...] = (
    "emails",
    "attachments",
    "email_mart_features",
    "conversations",
)
_WS_RE = re.compile(r"\s+")


class CompactionError(BackupError):
    """Operator-facing compaction failure (already sanitized)."""


@dataclass
class CompactionOptions:
    source: Path
    destination: Path
    confirm_offline_copy: bool = False
    apply: bool = False
    allow_same_filesystem: bool = False
    capacity_margin_ratio: float = DEFAULT_CAPACITY_MARGIN_RATIO
    capacity_margin_min_bytes: int = DEFAULT_CAPACITY_MARGIN_MIN_BYTES
    lock_dir: Path | None = None
    settings: Settings | None = None
    progress_sink: Callable[[str], None] | None = None
    should_abort: Callable[[], bool] | None = None
    clock: Callable[[], float] = field(default=time.perf_counter)
    dir_fsync: Callable[[int], None] | None = None
    vacuum_hook: Callable[[sqlite3.Connection, Path], None] | None = None
    post_vacuum_hook: Callable[[], None] | None = None
    verify_hook: Callable[[Path], dict[str, Any]] | None = None
    manifest_write_hook: Callable[[Path, str], None] | None = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _emit(options: CompactionOptions, message: str) -> None:
    sink = options.progress_sink or (lambda s: print(s, file=sys.stderr))
    sink(message)


def partial_path_for(destination: Path, *, unique: bool = True) -> Path:
    if unique:
        return destination.with_name(
            f"{destination.name}.partial.{os.getpid()}.{time.time_ns()}"
        )
    return destination.with_name(destination.name + ".partial")


def existing_partial_artifacts(destination: Path) -> list[Path]:
    parent = destination.parent
    prefix = destination.name + ".partial"
    found: list[Path] = []
    if not parent.is_dir():
        return found
    for path in parent.iterdir():
        name = path.name
        if name == prefix or name.startswith(prefix + "."):
            found.append(path)
            for companion in companion_paths(path).values():
                if companion.exists():
                    found.append(companion)
    return found


def manifest_path_for(destination: Path) -> Path:
    return destination.with_name(destination.name + ".compaction.manifest.json")


def manifest_partial_path_for(destination: Path) -> Path:
    return destination.with_name(
        destination.name + f".compaction.manifest.json.partial.{os.getpid()}.{time.time_ns()}"
    )


def companion_paths(db: Path) -> dict[str, Path]:
    return {
        "wal": Path(str(db) + "-wal"),
        "shm": Path(str(db) + "-shm"),
        "journal": Path(str(db) + "-journal"),
    }


def assert_no_sidecars(db: Path, *, label: str = "database") -> None:
    for kind, path in companion_paths(db).items():
        if path.exists():
            raise CompactionError(
                f"{label} has companion {kind} beside {sanitize_path_for_log(db)}; "
                "offline compaction requires a closed copy with no -wal/-shm/-journal"
            )


def compaction_is_completed(destination: Path) -> bool:
    man = manifest_path_for(destination)
    if not (destination.is_file() and man.is_file()):
        return False
    try:
        payload = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("completed") is True


def detect_orphan_destination(destination: Path) -> str | None:
    """Detect final DB without a completed compaction manifest."""
    man = manifest_path_for(destination)
    if destination.exists() and not man.exists():
        return (
            f"orphaned/uncommitted destination detected: "
            f"{sanitize_path_for_log(destination)} exists without completed manifest "
            f"{sanitize_path_for_log(man)}. Explicit cleanup required; refusing overwrite."
        )
    if destination.exists() and man.exists():
        try:
            payload = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return (
                f"destination exists with unreadable manifest "
                f"{sanitize_path_for_log(man)}. Explicit cleanup required."
            )
        if payload.get("completed") is not True:
            return (
                f"destination exists with incomplete manifest completed!=true: "
                f"{sanitize_path_for_log(destination)}. Explicit cleanup required."
            )
    return None


def connect_source_immutable(db: Path) -> sqlite3.Connection:
    """Open confirmed offline source with mode=ro&immutable=1 (no query_only; VACUUM INTO)."""
    uri = f"file:{db.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=120.0)
    conn.row_factory = sqlite3.Row
    return conn


def connect_candidate_readonly(db: Path) -> sqlite3.Connection:
    uri = f"file:{db.resolve().as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, timeout=120.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def storage_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    allocated = page_size * page_count
    freelist_bytes = page_size * freelist
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
        "allocated_bytes": allocated,
        "freelist_bytes": freelist_bytes,
        "active_bytes_estimate": max(0, allocated - freelist_bytes),
    }


def database_identity_props(conn: sqlite3.Connection) -> dict[str, Any]:
    """Stable DB identity attributes that must survive VACUUM INTO."""
    encoding = conn.execute("PRAGMA encoding").fetchone()[0]
    return {
        "page_size": int(conn.execute("PRAGMA page_size").fetchone()[0]),
        "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
        "application_id": int(conn.execute("PRAGMA application_id").fetchone()[0]),
        "encoding": str(encoding),
        "schema_version": int(conn.execute("PRAGMA schema_version").fetchone()[0]),
    }


def _normalize_sql(sql: str | None) -> str:
    if not sql:
        return ""
    collapsed = _WS_RE.sub(" ", sql).strip()
    # Ignore insignificant spacing around SQL punctuation so VACUUM INTO / pretty
    # CREATE text does not look like schema drift.
    return re.sub(r"\s*([(),;])\s*", r"\1", collapsed)


def schema_fingerprint(conn: sqlite3.Connection) -> str:
    """
    Stable schema fingerprint ignoring root-page layout changes from compaction.

    Uses object type/name/table name plus whitespace-normalized CREATE SQL only.
    """
    rows = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        ORDER BY type, name, tbl_name, sql
        """
    ).fetchall()
    payload = "\n".join(
        f"{r[0]}|{r[1]}|{r[2]}|{_normalize_sql(r[3])}"
        for r in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def schema_object_inventory(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT type, name, tbl_name
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return [
        {"type": str(r[0]), "name": str(r[1]), "tbl_name": str(r[2])}
        for r in rows
    ]


def critical_table_counts(conn: sqlite3.Connection) -> dict[str, int | None]:
    existing = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    out: dict[str, int | None] = {}
    for table in CRITICAL_TABLES:
        if table not in existing:
            out[table] = None
            continue
        q = quote_identifier(table)
        out[table] = int(conn.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0])
    return out


def _assert_paths_distinct(source: Path, *others: Path) -> None:
    for other in others:
        try:
            if source.resolve() == other.resolve():
                raise CompactionError(
                    "source and destination/partial resolve to the same path"
                )
        except OSError:
            pass
        if other.exists() and paths_same_file(source, other):
            raise CompactionError(
                "source and destination/partial resolve to the same file"
            )


def _read_destination_free_bytes(destination_parent: Path) -> int:
    try:
        return int(disk_free_bytes(destination_parent))
    except OSError as exc:
        raise CompactionError(
            f"unable to read destination filesystem capacity "
            f"(errno={getattr(exc, 'errno', None)})"
        ) from None


def validate_compaction_options(options: CompactionOptions) -> None:
    source = options.source.expanduser()
    destination = options.destination.expanduser()
    options.source = source
    options.destination = destination
    settings = options.settings or load_settings()

    if not source.is_file():
        raise CompactionError(f"source database not found: {sanitize_path_for_log(source)}")
    assert_sqlite_header(source)

    if is_configured_production_db(source, settings):
        raise CompactionError(
            "refusing configured production SQLite path (including samefile/device+inode "
            "aliases). Compaction is offline-snapshot only."
        )
    if is_configured_production_db(destination, settings):
        raise CompactionError(
            "refusing destination that resolves to the configured production SQLite path"
        )

    assert_no_sidecars(source, label="source")
    _assert_paths_distinct(
        source,
        destination,
        partial_path_for(destination, unique=False),
        manifest_path_for(destination),
    )

    orphan = detect_orphan_destination(destination)
    if orphan:
        raise CompactionError(orphan)

    if compaction_is_completed(destination):
        raise CompactionError(
            f"destination already completed (refusing overwrite): "
            f"{sanitize_path_for_log(destination)}"
        )
    if destination.exists():
        raise CompactionError(
            f"destination already exists (refusing overwrite): "
            f"{sanitize_path_for_log(destination)}"
        )

    leftovers = existing_partial_artifacts(destination)
    if leftovers:
        raise CompactionError(
            "conflicting partial artifact already exists: "
            f"{sanitize_path_for_log(leftovers[0])}"
        )
    man = manifest_path_for(destination)
    if man.exists() and not destination.exists():
        raise CompactionError(
            f"conflicting final manifest without destination DB: "
            f"{sanitize_path_for_log(man)}"
        )
    if not destination.parent.is_dir():
        raise CompactionError(
            f"destination parent directory missing: "
            f"{sanitize_path_for_log(destination.parent)}"
        )

    if options.allow_same_filesystem:
        if not options.confirm_offline_copy:
            raise CompactionError(
                "--allow-same-filesystem requires --confirm-offline-copy on a "
                "verified non-production offline snapshot"
            )
        if is_configured_production_db(source, settings):
            raise CompactionError(
                "--allow-same-filesystem refused for production SQLite paths"
            )
    else:
        try:
            src_dev = os.stat(source).st_dev
            dst_dev = os.stat(destination.parent).st_dev
        except OSError as exc:
            raise CompactionError(
                f"unable to compare filesystems (errno={getattr(exc, 'errno', None)})"
            ) from None
        if src_dev == dst_dev:
            raise CompactionError(
                "destination is on the same filesystem as source; refuse by default. "
                "Hard-link no-clobber publication requires same-FS between partial and "
                "final destination names. Use --allow-same-filesystem only for "
                "confirmed offline non-production snapshots / synthetic tests."
            )

    try:
        source_size = int(source.stat().st_size)
    except OSError as exc:
        raise CompactionError(
            f"unable to stat source (errno={getattr(exc, 'errno', None)})"
        ) from None
    needed = required_capacity_bytes(
        source_size,
        margin_ratio=options.capacity_margin_ratio,
        margin_min_bytes=options.capacity_margin_min_bytes,
    )
    free = _read_destination_free_bytes(destination.parent)
    if free < needed:
        raise CompactionError(
            "insufficient destination capacity: "
            f"free={free} needed>={needed} (source={source_size} + margin)"
        )


def build_preflight_report(options: CompactionOptions) -> dict[str, Any]:
    if not options.confirm_offline_copy:
        raise CompactionError(
            "offline compaction requires --confirm-offline-copy acknowledging a "
            "verified frozen offline/backup copy"
        )
    validate_compaction_options(options)
    source = options.source
    destination = options.destination
    source_size = int(source.stat().st_size)
    free = _read_destination_free_bytes(destination.parent)
    needed = required_capacity_bytes(
        source_size,
        margin_ratio=options.capacity_margin_ratio,
        margin_min_bytes=options.capacity_margin_min_bytes,
    )
    header = parse_sqlite_header_meta(source)
    return {
        "schema_version": COMPACTION_SCHEMA_VERSION,
        "mode": "preflight",
        "apply": False,
        "completed": False,
        "writes_performed": False,
        "method": "VACUUM INTO",
        "confirm_offline_copy": True,
        "source_basename": source.name,
        "destination_basename": destination.name,
        "source_size_bytes": source_size,
        "destination_free_bytes": free,
        "capacity_required_bytes": needed,
        "source_header": header,
        "notes": [
            "Preflight only: no lock, partial, VACUUM INTO, or manifest created.",
            "Never in-place VACUUM; never swaps candidate into production.",
            "Never deletes source snapshot or historical clones.",
            "Within-row body redundancy is not deletion approval.",
            "Hard-link no-clobber requires same filesystem between .partial and final names.",
            "Re-run with --confirm-offline-copy --apply to create a compacted candidate.",
        ],
    }


def verify_compact_candidate(
    candidate: Path,
    *,
    source_counts: dict[str, int | None],
    source_schema: str,
    source_identity: dict[str, Any],
    source_storage: dict[str, Any],
) -> dict[str, Any]:
    assert_no_sidecars(candidate, label="candidate")
    conn = connect_candidate_readonly(candidate)
    try:
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        if str(quick) != "ok":
            raise CompactionError(f"candidate quick_check failed: {quick}")
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_rows:
            raise CompactionError(
                f"candidate foreign_key_check reported {len(fk_rows)} violation(s)"
            )
        cand_schema = schema_fingerprint(conn)
        if cand_schema != source_schema:
            raise CompactionError("candidate schema fingerprint disagrees with source")
        cand_identity = database_identity_props(conn)
        for key in ("page_size", "user_version", "application_id", "encoding"):
            if cand_identity.get(key) != source_identity.get(key):
                raise CompactionError(
                    f"candidate {key} mismatch: "
                    f"source={source_identity.get(key)!r} candidate={cand_identity.get(key)!r}"
                )
        cand_counts = critical_table_counts(conn)
        for table, expected in source_counts.items():
            actual = cand_counts.get(table)
            if expected != actual:
                raise CompactionError(
                    f"candidate row-count mismatch for {table}: "
                    f"source={expected} candidate={actual}"
                )
        stats = storage_stats(conn)
        src_freelist = int(source_storage.get("freelist_count") or 0)
        cand_freelist = int(stats["freelist_count"])
        src_allocated = int(source_storage.get("allocated_bytes") or 0)
        if src_freelist > 0 and not (cand_freelist < src_freelist):
            raise CompactionError(
                "candidate freelist was not reduced relative to source "
                f"(source_freelist={src_freelist} candidate_freelist={cand_freelist})"
            )
        if src_allocated > 0 and not (int(stats["allocated_bytes"]) < src_allocated):
            raise CompactionError(
                "candidate allocated size was not smaller than source "
                f"(source={src_allocated} candidate={stats['allocated_bytes']})"
            )
        inventory = schema_object_inventory(conn)
        return {
            "quick_check": "ok",
            "foreign_key_violation_count": 0,
            "schema_fingerprint": cand_schema,
            "schema_object_count": len(inventory),
            "database_identity": cand_identity,
            "critical_table_counts": cand_counts,
            "storage": stats,
            "freelist_reduced": cand_freelist < src_freelist if src_freelist > 0 else True,
            "companions_present": False,
        }
    finally:
        conn.close()


def cleanup_script_owned_artifacts(
    destination: Path, *, partial: Path | None = None, manifest_partial: Path | None = None
) -> list[str]:
    """Remove only this run's partial artifacts. Never deletes final DB/manifest/source."""
    removed: list[str] = []
    targets: list[Path] = []
    if partial is not None:
        targets.extend([partial, *companion_paths(partial).values()])
    if manifest_partial is not None:
        targets.append(manifest_partial)
    for path in targets:
        if path.exists():
            try:
                path.unlink()
                removed.append(sanitize_path_for_log(path))
            except OSError:
                pass
    return removed


def _run_vacuum_into(conn: sqlite3.Connection, partial: Path) -> None:
    # Quote path for SQLite string literal; never interpolate unsanitized SQL.
    literal = str(partial.resolve()).replace("'", "''")
    conn.execute(f"VACUUM INTO '{literal}'")


def run_offline_compaction(options: CompactionOptions) -> dict[str, Any]:
    """
    Preflight by default. With apply+confirm, create compacted candidate via VACUUM INTO.
    """
    if options.allow_same_filesystem and not options.confirm_offline_copy:
        raise CompactionError(
            "--allow-same-filesystem requires --confirm-offline-copy on a "
            "verified non-production offline snapshot"
        )

    if not options.apply:
        return build_preflight_report(options)

    if not options.confirm_offline_copy:
        raise CompactionError(
            "writes require both --confirm-offline-copy and --apply"
        )

    validate_compaction_options(options)
    source = options.source
    destination = options.destination
    partial = partial_path_for(destination, unique=True)
    manifest_path = manifest_path_for(destination)
    manifest_partial = destination.with_name(
        destination.name
        + f".compaction.manifest.json.partial.{os.getpid()}.{time.time_ns()}"
    )
    _assert_paths_distinct(source, destination, partial, manifest_path, manifest_partial)

    default_lock_dir = Path.home() / ".cache" / "origenlab" / LOCK_DIR_NAME
    lock = BackupLock(lock_path_for(source, options.lock_dir or default_lock_dir))

    source_fp_before = fingerprint_file(source)
    started = options.clock()
    warnings: list[str] = []
    published_db = False
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    abort_flag = {"value": False}

    def _handle_signal(signum: int, _frame: Any) -> None:
        abort_flag["value"] = True
        _emit(options, f"compaction received signal={signum}; aborting safely")

    try:
        lock.acquire()
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        # Probe hard-link support BEFORE the expensive VACUUM INTO.
        probe_hardlink_no_clobber_supported(destination.parent)
        assert_no_sidecars(source, label="source")

        source_conn = connect_source_immutable(source)
        try:
            assert_no_sidecars(source, label="source")
            source_stats = storage_stats(source_conn)
            source_schema = schema_fingerprint(source_conn)
            source_identity = database_identity_props(source_conn)
            source_counts = critical_table_counts(source_conn)
            source_inventory = schema_object_inventory(source_conn)
            _emit(
                options,
                "compaction start: method=VACUUM INTO "
                f"source_active_est={source_stats['active_bytes_estimate']}",
            )
            if options.should_abort and options.should_abort():
                raise CompactionError("compaction aborted before VACUUM INTO")
            if abort_flag["value"]:
                raise CompactionError("compaction aborted before VACUUM INTO")

            if options.vacuum_hook is not None:
                options.vacuum_hook(source_conn, partial)
            else:
                _run_vacuum_into(source_conn, partial)
        finally:
            source_conn.close()

        if options.post_vacuum_hook is not None:
            options.post_vacuum_hook()
        if options.should_abort and options.should_abort():
            raise CompactionError("compaction aborted after VACUUM INTO")
        if abort_flag["value"]:
            raise CompactionError("compaction aborted after VACUUM INTO")

        source_fp_after = fingerprint_file(source)
        if source_fp_after != source_fp_before:
            raise CompactionError(
                "source fingerprint changed during compaction; refusing success"
            )
        assert_no_sidecars(source, label="source")
        assert_no_sidecars(partial, label="partial candidate")

        if options.verify_hook is not None:
            verification = options.verify_hook(partial)
        else:
            verification = verify_compact_candidate(
                partial,
                source_counts=source_counts,
                source_schema=source_schema,
                source_identity=source_identity,
                source_storage=source_stats,
            )

        fsync_file(partial)
        dir_info = fsync_directory(partial.parent, dir_fsync=options.dir_fsync)
        if dir_info.get("directory_fsync_warning"):
            warnings.append(str(dir_info["directory_fsync_warning"]))

        dest_size = int(partial.stat().st_size)
        source_size = int(source_fp_before.size_bytes)
        reclaimed = max(0, source_size - dest_size)
        elapsed = round(options.clock() - started, 3)

        manifest: dict[str, Any] = {
            "schema_version": COMPACTION_SCHEMA_VERSION,
            "completed": True,
            "method": "VACUUM INTO",
            "confirm_offline_copy": True,
            "apply": True,
            "captured_at_utc": _iso_now(),
            "elapsed_seconds": elapsed,
            "source_basename": source.name,
            "destination_basename": destination.name,
            "source_size_bytes": source_size,
            "destination_size_bytes": dest_size,
            "estimated_reclaimed_bytes": reclaimed,
            "source_fingerprint_before": source_fp_before.to_dict(),
            "source_fingerprint_after": source_fp_after.to_dict(),
            "source_fingerprint_unchanged": True,
            "source_storage": source_stats,
            "source_database_identity": source_identity,
            "source_schema_object_count": len(source_inventory),
            "source_critical_table_counts": source_counts,
            "verification": verification,
            "directory_fsync_supported": dir_info.get("directory_fsync_supported"),
            "warnings": warnings,
            "publication_method": "hardlink_no_clobber",
            "completion_marker": "final_compaction_manifest",
            "notes": [
                "Offline VACUUM INTO candidate only; not swapped into production.",
                "Source snapshot was not deleted or modified.",
                "In-place VACUUM was not used.",
                "Historical/referenced rows are not treated as deletable.",
                "Within-row body redundancy remains an engineering opportunity only.",
                "Completed only when final DB and completed final manifest both exist.",
            ],
        }
        privacy = scan_manifest_privacy(manifest)
        if privacy:
            raise CompactionError(f"manifest privacy violation: {privacy[:3]}")

        payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        if options.manifest_write_hook is not None:
            options.manifest_write_hook(manifest_partial, payload)
        else:
            manifest_partial.write_text(payload, encoding="utf-8")
        fsync_file(manifest_partial)

        try:
            publish_no_clobber(partial, destination)
            published_db = True
            publish_no_clobber(manifest_partial, manifest_path)
        except BackupError as exc:
            # If DB published but manifest did not, leave orphan for explicit cleanup.
            raise CompactionError(str(exc)) from None

        post = fsync_directory(destination.parent, dir_fsync=options.dir_fsync)
        if post.get("directory_fsync_warning"):
            warning = str(post["directory_fsync_warning"])
            warnings.append(warning)
            _emit(options, f"post-publish durability warning: {warning}")
            manifest = {
                **manifest,
                "warnings": warnings,
                "directory_fsync_supported": bool(dir_info.get("directory_fsync_supported"))
                and bool(post.get("directory_fsync_supported")),
            }

        assert_no_sidecars(destination, label="published candidate")
        source_fp_final = fingerprint_file(source)
        if source_fp_final != source_fp_before:
            raise CompactionError(
                "source fingerprint changed after publication; refusing success"
            )
        assert_no_sidecars(source, label="source")
        _emit(
            options,
            "compaction completed: "
            f"dest_bytes={dest_size} reclaimed_est={reclaimed} elapsed_s={elapsed}",
        )
        return manifest
    except Exception as exc:
        if not published_db:
            cleanup_script_owned_artifacts(
                destination, partial=partial, manifest_partial=manifest_partial
            )
        else:
            # Orphan window: final DB may exist without completed manifest.
            cleanup_script_owned_artifacts(
                destination, partial=None, manifest_partial=manifest_partial
            )
        if isinstance(exc, CompactionError):
            raise
        if isinstance(exc, BackupError):
            raise CompactionError(str(exc)) from None
        raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        lock.release()
