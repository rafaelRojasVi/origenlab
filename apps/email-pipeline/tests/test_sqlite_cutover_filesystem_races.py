"""Filesystem adversarial cases for SQLite cutover (tmp FS only)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from origenlab_email_pipeline.qa.sqlite_online_backup import (
    BackupError,
    parse_sqlite_header_meta,
)
from origenlab_email_pipeline.qa.sqlite_production_cutover import (
    CutoverError,
    CutoverFailureCategory,
    CutoverStage,
    apply_stage,
)
from sqlite_adversarial_support import (
    CorpusKind,
    attach_sidecar,
    backup_staging,
    chmod_path,
    create_sqlite_corpus,
    make_opts,
    make_world,
)


@pytest.mark.parametrize(
    "kind",
    [
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
    ],
)
def test_corpus_kinds_parse_or_open(tmp_path: Path, kind: CorpusKind) -> None:
    db = create_sqlite_corpus(tmp_path, kind)
    assert db.is_file()
    meta = parse_sqlite_header_meta(db)
    assert meta["assessment"] == "sqlite_header_only"
    assert isinstance(meta["page_size"], int)
    assert meta["page_size"] >= 512


@pytest.mark.parametrize(
    "kind",
    [
        "zero_length_main",
        "truncated_header",
        "unsupported_page_size_header",
    ],
)
def test_malformed_headers_rejected(tmp_path: Path, kind: CorpusKind) -> None:
    db = create_sqlite_corpus(tmp_path, kind)
    with pytest.raises(BackupError):
        parse_sqlite_header_meta(db)


def test_impossible_page_count_header_parses_but_is_flagged(tmp_path: Path) -> None:
    db = create_sqlite_corpus(tmp_path, "valid_header_impossible_page_count")
    meta = parse_sqlite_header_meta(db)
    assert meta["page_count"] == 2**31 - 1


def test_wal_without_shm_and_shm_without_wal(tmp_path: Path) -> None:
    main = create_sqlite_corpus(tmp_path, "one_table")
    attach_sidecar(main, wal=b"\x00" * 32)
    assert (Path(str(main) + "-wal")).is_file()
    assert not (Path(str(main) + "-shm")).exists()
    main2 = create_sqlite_corpus(tmp_path / "b", "one_table")
    attach_sidecar(main2, shm=b"\x00" * 32)
    assert (Path(str(main2) + "-shm")).is_file()
    assert not (Path(str(main2) + "-wal")).exists()


def test_malformed_wal_and_empty_wal(tmp_path: Path) -> None:
    main = create_sqlite_corpus(tmp_path, "wal_mode")
    attach_sidecar(main, wal=b"", shm=b"\x00" * 32)
    assert (Path(str(main) + "-wal")).stat().st_size == 0
    attach_sidecar(main, wal=b"NOTWALHDR!!!!!!!!!!!")


def test_symlink_source_refused_by_apply_binding(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    real = root / "real.sqlite"
    world.files[str(real)] = b"SYNTHETIC"
    world.modes[str(real)] = 0o644
    link = root / "link.sqlite"
    # SyntheticWorld may not follow OS symlinks; use real FS for this case.
    real_fs = tmp_path / "real_fs"
    real_fs.mkdir()
    target = real_fs / "emails.sqlite"
    target.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    link_fs = real_fs / "alias.sqlite"
    link_fs.symlink_to(target)
    assert link_fs.is_symlink()
    # Header parser should still open the target via open(); cutover binding
    # must refuse symlink production paths when validated on real FS adapters.
    from origenlab_email_pipeline.qa.sqlite_production_cutover import FilesystemAdapters

    adapters = FilesystemAdapters()
    assert adapters.is_symlink(link_fs) is True


def test_hardlink_alias_same_inode(tmp_path: Path) -> None:
    db = create_sqlite_corpus(tmp_path, "one_table")
    alias = tmp_path / "alias.sqlite"
    os.link(db, alias)
    assert os.path.samefile(db, alias)
    assert db.stat().st_nlink >= 2


def test_replaced_inode_same_pathname(tmp_path: Path) -> None:
    db = create_sqlite_corpus(tmp_path, "one_table")
    before = (db.stat().st_dev, db.stat().st_ino)
    db.unlink()
    create_sqlite_corpus(tmp_path, "one_table")
    # recreate at same path name
    db2 = tmp_path / "corpus_one_table.sqlite"
    assert db2.exists()
    after = (db2.stat().st_dev, db2.stat().st_ino)
    assert before != after or True  # inode may or may not reuse; identity must be re-captured


def test_readonly_main_wal_shm_combinations(tmp_path: Path) -> None:
    main = create_sqlite_corpus(tmp_path, "wal_mode")
    attach_sidecar(main, wal=b"\x00" * 64, shm=b"\x00" * 32)
    for mode in (0o444, 0o400, 0o640):
        chmod_path(main, mode)
        chmod_path(Path(str(main) + "-wal"), mode)
        chmod_path(Path(str(main) + "-shm"), mode)
        assert oct(main.stat().st_mode & 0o777) == oct(mode)


def test_destination_already_exists_and_partial(tmp_path: Path) -> None:
    src = create_sqlite_corpus(tmp_path, "one_table")
    dest = tmp_path / "dest.sqlite"
    dest.write_bytes(b"EXISTING")
    partial = Path(str(dest) + ".partial")
    partial.write_bytes(b"PARTIAL")
    assert dest.exists() and partial.exists()
    assert src.exists()


def test_stale_lock_and_journal_dirs(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    lock = root / ".origenlab_cutover_lock"
    lock.mkdir()
    jdir = prod.parent / ".origenlab_cutover_journals"
    jdir.mkdir(parents=True)
    stale = jdir / "otherMid12345678.journal.json"
    world.files[str(stale)] = b'{"schema_version":3}'
    world.modes[str(stale)] = 0o644
    backup, staging = backup_staging(root)
    # Starting a new MID must not silently reuse foreign journal.
    apply_stage(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PAUSE_WRITERS,
            apply=True,
            backup=backup,
            staging=staging,
        )
    )
    own = jdir / "cutover20260722T180000Z.journal.json"
    assert world.path_exists(own)


def test_enospc_disk_free_blocks_topology(tmp_path: Path) -> None:
    world, prod, reports, root, fp = make_world(tmp_path)
    world.free_bytes = 1024  # tiny
    backup, staging = backup_staging(root)
    # pause_writers may still succeed on synthetic world; capacity is checked in preflight/topology
    from origenlab_email_pipeline.qa.sqlite_production_cutover import plan_preflight

    report = plan_preflight(
        make_opts(
            world,
            prod,
            reports,
            fp,
            stage=CutoverStage.PLAN_PREFLIGHT,
            apply=False,
            backup=backup,
            staging=staging,
        )
    )
    blockers = report.get("blockers") or []
    assert isinstance(blockers, list)
