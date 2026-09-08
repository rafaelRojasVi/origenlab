from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_TDIR = Path(__file__).resolve().parent.parent
if str(_TDIR) not in sys.path:
    sys.path.insert(0, str(_TDIR))

from origenlab_email_pipeline.historical_quote_register.db_readonly import open_readonly
from historical_quote_register.sqlite_fixture import build_fixture_db, open_writable_fixture


def test_open_readonly_allows_select(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_readonly(db_path)
    rows = conn.execute("SELECT COUNT(*) FROM emails").fetchall()
    assert rows == [(0,)]


def test_open_readonly_rejects_write(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_readonly(db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO emails (source_file) VALUES ('x')")


def test_open_readonly_missing_file_raises(tmp_path):
    missing = tmp_path / "does-not-exist.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        open_readonly(missing)
