"""Read-only source guarantees for the Wave 1B V1 safety extractor.

Every test here uses a disposable synthetic SQLite fixture. Nothing in this file
may name a production path or a real address; synthetic addresses use the
reserved ``example.invalid`` domain.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.migration.readonly_source import (
    ReadOnlyProofError,
    assert_query_only_active,
    open_source_readonly,
    read_only_uri,
    single_read_transaction,
)


def _fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "synthetic_source.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, email TEXT NOT NULL)")
    conn.execute("INSERT INTO t (id, email) VALUES (1, 'a@example.invalid')")
    conn.commit()
    conn.close()
    return db


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_only_uri_uses_mode_ro_and_percent_encodes(tmp_path: Path) -> None:
    db = tmp_path / "a b#c.sqlite"
    uri = read_only_uri(db)
    assert uri.startswith("file:")
    assert uri.endswith("?mode=ro")
    assert "immutable" not in uri
    assert " " not in uri and "#" not in uri


def test_open_source_readonly_activates_and_proves_query_only(tmp_path: Path) -> None:
    db = _fixture_db(tmp_path)
    conn = open_source_readonly(db)
    try:
        assert int(conn.execute("PRAGMA query_only").fetchone()[0]) == 1
    finally:
        conn.close()


def test_open_source_readonly_refuses_a_writable_connection(tmp_path: Path) -> None:
    db = _fixture_db(tmp_path)
    conn = open_source_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t (id, email) VALUES (2, 'b@example.invalid')")
    finally:
        conn.close()


def test_assert_query_only_active_refuses_when_it_cannot_be_proven(tmp_path: Path) -> None:
    db = _fixture_db(tmp_path)
    conn = sqlite3.connect(db)  # deliberately writable, query_only never set
    try:
        with pytest.raises(ReadOnlyProofError):
            assert_query_only_active(conn)
    finally:
        conn.close()


def test_open_source_readonly_refuses_a_missing_database(tmp_path: Path) -> None:
    with pytest.raises(ReadOnlyProofError):
        open_source_readonly(tmp_path / "absent.sqlite")


def test_single_read_transaction_leaves_the_fixture_byte_identical(tmp_path: Path) -> None:
    db = _fixture_db(tmp_path)
    before = _digest(db)
    before_size = db.stat().st_size

    conn = open_source_readonly(db)
    try:
        with single_read_transaction(conn) as txn:
            assert txn.data_version_start == txn.data_version_now()
            rows = conn.execute("SELECT id, email FROM t ORDER BY id").fetchall()
            assert [tuple(r) for r in rows] == [(1, "a@example.invalid")]
        assert conn.total_changes == 0
    finally:
        conn.close()

    assert _digest(db) == before
    assert db.stat().st_size == before_size
    assert not (tmp_path / "synthetic_source.sqlite-wal").exists()


def test_single_read_transaction_refuses_a_connection_without_query_only(tmp_path: Path) -> None:
    db = _fixture_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(ReadOnlyProofError):
            with single_read_transaction(conn):
                pass
    finally:
        conn.close()
