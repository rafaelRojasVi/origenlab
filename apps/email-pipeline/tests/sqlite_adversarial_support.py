"""Shared synthetic SQLite corpus + cutover helpers for post-closure adversarial tests.

Temporary directories only. Never touches production paths.
Not part of the A–F roadmap (post-closure verification only).
"""

from __future__ import annotations

import os
import sqlite3
import struct
from pathlib import Path
from typing import Any, Literal

from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverOptions,
    CutoverStage,
    SyntheticWorld,
)

MID = "cutover20260722T180000Z"
MAIN_SHA = "25cd4100e226427b3a4d027f1ee3b3af056884d4"
JULY19_MID = "cutover20260719T163633Z"

HOSTILE_TEXT = (
    "disk I/O error at /home/rafael/secret/db.sqlite "
    "file:///tmp/x.db?mode=rw "
    "operator@origenlab.example "
    "SELECT * FROM secrets WHERE token='origenlab_test_token_ABCDEF123456' "
    "pid=12345 fd=42 device=99 inode=77"
)

FORBIDDEN_EVIDENCE = (
    "/home/",
    "/proc/",
    "file://",
    "SELECT ",
    "@origenlab.example",
    "origenlab_test_token",
    "pid=",
    "inode=",
    "device=",
)

CorpusKind = Literal[
    "empty_valid",
    "one_table",
    "many_tables",
    "indexes_unique",
    "triggers_views_fk",
    "generated_column",
    "large_blob_text",
    "unicode_quoted",
    "page_size_1024",
    "page_size_8192",
    "wal_mode",
    "delete_journal_mode",
    "zero_length_main",
    "truncated_header",
    "valid_header_impossible_page_count",
    "unsupported_page_size_header",
]


def make_world(tmp_path: Path) -> tuple[SyntheticWorld, Path, Path, Path, str]:
    root = tmp_path / "cutover_world"
    root.mkdir(parents=True, exist_ok=True)
    prod = root / "emails.sqlite"
    reports = root / "reports" / "out"
    (reports / "active" / "current").mkdir(parents=True)
    world = SyntheticWorld(root=root, head_sha=MAIN_SHA)
    world.files[str(prod)] = b"SYNTHETIC-PROD-DB-v1"
    world.modes[str(prod)] = 0o644
    world.owners[str(prod)] = (1000, 1000)
    world.inode_overrides[str(prod)] = 4242
    world.device_overrides[str(prod)] = 7
    world.services.api_active = True
    world.services.health_timer_active = True
    world.wal_size = 4096
    fp = world.fingerprint(prod)
    return world, prod, reports, root, fp


def make_opts(
    world: SyntheticWorld,
    prod: Path,
    reports: Path,
    fp: str,
    *,
    stage: CutoverStage,
    apply: bool = False,
    approve_swap: bool = False,
    backup: Path | None = None,
    staging: Path | None = None,
    fail_after: str | None = None,
    maintenance_id: str = MID,
    main_sha: str = MAIN_SHA,
) -> CutoverOptions:
    return CutoverOptions(
        stage=stage,
        apply=apply,
        confirm_production_cutover=True,
        maintenance_id=maintenance_id,
        expected_main_sha=main_sha,
        expected_production_path=prod,
        expected_production_fingerprint=fp,
        approve_swap=approve_swap,
        journal_path=prod.parent
        / ".origenlab_cutover_journals"
        / f"{maintenance_id}.journal.json",
        backup_dest=backup,
        staging_dest=staging,
        reports_dir=reports,
        adapters=world,
        fail_after=fail_after,
        allow_synthetic_world=True,
    )


def backup_staging(root: Path) -> tuple[Path, Path]:
    return (
        root / "emails_online_backup_cutover_fresh.sqlite",
        root / "emails.sqlite.staged.cutover",
    )


def assert_no_forbidden(blob: str) -> None:
    lower = blob.lower()
    for needle in FORBIDDEN_EVIDENCE:
        assert needle.lower() not in lower, f"leaked {needle!r}"


def _write_minimal_sqlite_header(
    path: Path,
    *,
    page_size: int = 4096,
    page_count: int = 1,
    write_version: int = 1,
    read_version: int = 1,
) -> None:
    """Write a 100-byte SQLite header + zero-padded page (no sqlite3 required)."""
    header = bytearray(100)
    header[0:16] = b"SQLite format 3\x00"
    raw_ps = 1 if page_size == 65536 else page_size
    header[16:18] = struct.pack(">H", raw_ps)
    header[18] = write_version
    header[19] = read_version
    header[20:24] = b"\x00\x00\x00\x01"  # unused bytes reserved area size etc.
    header[28:32] = struct.pack(">I", page_count)
    # Fill one page so some parsers accept the file as non-empty.
    path.write_bytes(bytes(header) + b"\x00" * max(0, page_size - 100))


def create_sqlite_corpus(tmp_path: Path, kind: CorpusKind) -> Path:
    """Create a small synthetic SQLite (or malformed) file under tmp_path."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / f"corpus_{kind}.sqlite"

    if kind == "zero_length_main":
        db.write_bytes(b"")
        return db
    if kind == "truncated_header":
        db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 20)
        return db
    if kind == "valid_header_impossible_page_count":
        _write_minimal_sqlite_header(db, page_size=4096, page_count=2**31 - 1)
        return db
    if kind == "unsupported_page_size_header":
        # page_size 513 is not power-of-two → header parser must reject
        header = bytearray(100)
        header[0:16] = b"SQLite format 3\x00"
        header[16:18] = struct.pack(">H", 513)
        header[18] = 1
        header[19] = 1
        header[28:32] = struct.pack(">I", 1)
        db.write_bytes(bytes(header) + b"\x00" * 413)
        return db

    page_size = 4096
    if kind == "page_size_1024":
        page_size = 1024
    elif kind == "page_size_8192":
        page_size = 8192

    uri = f"file:{db}?mode=rwc"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute(f"PRAGMA page_size={page_size}")
        if kind in {"wal_mode", "empty_valid", "one_table", "many_tables"}:
            conn.execute("PRAGMA journal_mode=WAL")
        elif kind == "delete_journal_mode":
            conn.execute("PRAGMA journal_mode=DELETE")

        if kind == "empty_valid":
            conn.execute("SELECT 1")
        elif kind == "one_table":
            conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t(v) VALUES ('a')")
        elif kind == "many_tables":
            for i in range(12):
                conn.execute(f"CREATE TABLE t{i}(id INTEGER PRIMARY KEY, v TEXT)")
                conn.execute(f"INSERT INTO t{i}(v) VALUES ('row{i}')")
        elif kind == "indexes_unique":
            conn.execute("CREATE TABLE u(id INTEGER PRIMARY KEY, email TEXT UNIQUE)")
            conn.execute("CREATE INDEX ix_u_email ON u(email)")
            conn.execute("INSERT INTO u(email) VALUES ('a@example.test')")
        elif kind == "triggers_views_fk":
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE child(id INTEGER PRIMARY KEY, pid INTEGER "
                "REFERENCES parent(id))"
            )
            conn.execute("CREATE VIEW v_child AS SELECT * FROM child")
            conn.execute(
                "CREATE TRIGGER trg_parent AFTER INSERT ON parent BEGIN "
                "INSERT INTO child(pid) VALUES (NEW.id); END"
            )
            conn.execute("INSERT INTO parent(id) VALUES (1)")
        elif kind == "generated_column":
            conn.execute(
                "CREATE TABLE g(id INTEGER PRIMARY KEY, a INT, b INT, "
                "c INT GENERATED ALWAYS AS (a+b) VIRTUAL)"
            )
            conn.execute("INSERT INTO g(a,b) VALUES (2,3)")
        elif kind == "large_blob_text":
            conn.execute("CREATE TABLE big(id INTEGER PRIMARY KEY, b BLOB, t TEXT)")
            conn.execute(
                "INSERT INTO big(b,t) VALUES (?, ?)",
                (os.urandom(64 * 1024), "üñîçødé-" * 2000),
            )
        elif kind == "unicode_quoted":
            conn.execute('CREATE TABLE "表"("列" TEXT PRIMARY KEY)')
            conn.execute('INSERT INTO "表"("列") VALUES (?)', ("値",))
        elif kind in {"page_size_1024", "page_size_8192", "wal_mode", "delete_journal_mode"}:
            conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO t DEFAULT VALUES")
        conn.commit()
    finally:
        conn.close()
    return db


def attach_sidecar(
    main: Path,
    *,
    wal: bytes | None = None,
    shm: bytes | None = None,
    journal: bytes | None = None,
) -> dict[str, Path]:
    """Create companion files next to a synthetic main DB."""
    out: dict[str, Path] = {}
    if wal is not None:
        p = Path(str(main) + "-wal")
        p.write_bytes(wal)
        out["wal"] = p
    if shm is not None:
        p = Path(str(main) + "-shm")
        p.write_bytes(shm)
        out["shm"] = p
    if journal is not None:
        p = Path(str(main) + "-journal")
        p.write_bytes(journal)
        out["journal"] = p
    return out


def chmod_path(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def journal_dict_skeleton(
    *,
    stage: str = "pause_writers",
    maintenance_id: str = MID,
    schema_version: int = 3,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": schema_version,
        "tool": "orchestrate_sqlite_production_cutover",
        "maintenance_id": maintenance_id,
        "stage": stage,
        "created_at_utc": "2026-07-22T00:00:00+00:00",
        "updated_at_utc": "2026-07-22T00:00:00+00:00",
    }
    if extra:
        base.update(extra)
    return base
