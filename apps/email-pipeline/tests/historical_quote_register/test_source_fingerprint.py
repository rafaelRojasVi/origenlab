from __future__ import annotations

import sys
from pathlib import Path

_TDIR = Path(__file__).resolve().parent.parent
if str(_TDIR) not in sys.path:
    sys.path.insert(0, str(_TDIR))

from origenlab_email_pipeline.historical_quote_register.db_readonly import open_readonly
from origenlab_email_pipeline.historical_quote_register.source_fingerprint import (
    fingerprint_source_db,
)
from historical_quote_register.sqlite_fixture import build_fixture_db, open_writable_fixture


def test_fingerprint_stable_when_untouched(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_readonly(db_path)
    first = fingerprint_source_db(db_path, conn)
    second = fingerprint_source_db(db_path, conn)
    assert first == second
    assert first.quick_check == "ok"


def test_fingerprint_changes_after_external_write(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_readonly(db_path)
    before = fingerprint_source_db(db_path, conn)

    writer = open_writable_fixture(db_path)
    writer.execute(
        "INSERT INTO emails (source_file, folder) VALUES ('x', '[Gmail]/Enviados')"
    )
    writer.commit()
    writer.close()

    after = fingerprint_source_db(db_path, open_readonly(db_path))
    assert after.size_bytes != before.size_bytes or after.mtime_ns != before.mtime_ns
