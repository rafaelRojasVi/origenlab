"""Tests for commercial purchase order promotion (SQLite) and Postgres mirror."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from origenlab_email_pipeline.commercial.ceaf_oc_26172 import CEAF_OC_NUMBER, CEAF_SUBJECT_FRAGMENT
from origenlab_email_pipeline.commercial.commercial_purchase_promotion import (
    apply_promotion_plan,
    build_ceaf_oc_26172_plan,
    connect_sqlite_rw,
    find_source_email,
)
from origenlab_email_pipeline.commercial.commercial_purchase_schema import (
    commercial_purchase_tables_exist,
    ensure_commercial_purchase_tables,
)
import origenlab_email_pipeline.commercial_purchase_postgres_mirror as commercial_pg_mirror
from origenlab_email_pipeline.commercial_purchase_postgres_mirror import (
    sync_commercial_purchase_events,
)


def _seed_ceaf_email(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          source_file TEXT NOT NULL,
          folder TEXT,
          message_id TEXT,
          subject TEXT,
          sender TEXT,
          recipients TEXT,
          date_iso TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE attachments (
          id INTEGER PRIMARY KEY,
          email_id INTEGER NOT NULL,
          part_index INTEGER,
          filename TEXT,
          content_type TEXT,
          size_bytes INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO emails (id, source_file, folder, message_id, subject, sender, recipients, date_iso)
        VALUES (710387, 'gmail:contacto@origenlab.cl/INBOX', 'INBOX',
                '<test@mail.gmail.com>', ?, 'Carlos Garay <cgaray@ceaf.cl>',
                'CONTACTO@origenlab.cl', '2026-05-14T12:30:18-04:00')
        """,
        (CEAF_SUBJECT_FRAGMENT,),
    )
    conn.execute(
        "INSERT INTO attachments (id, email_id, part_index, filename, content_type, size_bytes) "
        "VALUES (1, 710387, 0, 'OC N º 26172.pdf', 'application/pdf', 1000)"
    )
    conn.execute(
        "INSERT INTO attachments (id, email_id, part_index, filename, content_type, size_bytes) "
        "VALUES (2, 710387, 1, 'CN011728A-Verónica Guajardo – Tatiana Vivanco.pdf', 'application/pdf', 2000)"
    )
    conn.commit()
    conn.close()
    return 710387


def test_find_source_email_by_subject_and_oc(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _seed_ceaf_email(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    found = find_source_email(
        conn,
        subject=CEAF_SUBJECT_FRAGMENT,
        oc_number=CEAF_OC_NUMBER,
        buyer_domain="ceaf.cl",
    )
    conn.close()
    assert found is not None
    assert found.id == 710387
    assert "26172" in (found.subject or "")


def test_promotion_insert_and_idempotent_update(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _seed_ceaf_email(db)
    conn = connect_sqlite_rw(db)
    plan1 = build_ceaf_oc_26172_plan(conn)
    assert plan1.action == "insert"
    event_id = apply_promotion_plan(conn, plan1)
    conn.close()

    conn = connect_sqlite_rw(db)
    assert commercial_purchase_tables_exist(conn)
    row = conn.execute(
        "SELECT buyer_org_name, oc_number, net_amount_clp, purchase_status FROM commercial_purchase_events WHERE id=?",
        (event_id,),
    ).fetchone()
    assert row is not None
    assert "CEAF" in row[0]
    assert row[1] == CEAF_OC_NUMBER
    assert row[2] == 1_260_000
    assert row[3] == "purchase_order_received"
    items = conn.execute(
        "SELECT COUNT(*) FROM commercial_purchase_event_items WHERE purchase_event_id=?",
        (event_id,),
    ).fetchone()[0]
    assert items == 3

    plan2 = build_ceaf_oc_26172_plan(conn)
    assert plan2.action == "update"
    assert plan2.existing_event_id == event_id
    apply_promotion_plan(conn, plan2)
    count = conn.execute("SELECT COUNT(*) FROM commercial_purchase_events").fetchone()[0]
    conn.close()
    assert count == 1


def test_postgres_mirror_sync_from_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "emails.sqlite"
    _seed_ceaf_email(db)
    conn = connect_sqlite_rw(db)
    plan = build_ceaf_oc_26172_plan(conn)
    apply_promotion_plan(conn, plan)
    conn.close()


    class FakeCursor:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def execute(self, sql: str, params=None) -> None:
            self.commands.append(sql.lower())

        def fetchone(self):
            if any("information_schema" in c for c in self.commands[-1:]):
                return (1,)
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr(
        commercial_pg_mirror,
        "psycopg",
        type("P", (), {"connect": lambda *a, **k: FakeConn()}),
    )

    class _FakeJson:
        def __init__(self, obj: object) -> None:
            self.obj = obj

    monkeypatch.setattr(commercial_pg_mirror, "Json", _FakeJson)
    monkeypatch.setattr(commercial_pg_mirror, "pg_table_exists", lambda *a, **k: True)
    result = sync_commercial_purchase_events("postgresql://u:p@127.0.0.1/scratch", db)
    assert result["events_built"] == 1
    assert result["items_built"] == 3
    assert result["events_written"] == 1


def test_promote_script_dry_run_exits_zero(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    _seed_ceaf_email(db)
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "commercial" / "promote_purchase_order_event.py"
    r = subprocess.run(
        [sys.executable, str(script), "--sqlite-db", str(db), "--dry-run"],
        cwd=str(script.parents[2]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0
    assert "26172" in r.stdout


def test_promote_script_fails_without_email(tmp_path: Path) -> None:
    db = tmp_path / "emails.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE emails (id INTEGER PRIMARY KEY, source_file TEXT NOT NULL, subject TEXT, sender TEXT, recipients TEXT, date_iso TEXT, message_id TEXT, folder TEXT)"
    )
    conn.commit()
    conn.close()
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "commercial" / "promote_purchase_order_event.py"
    r = subprocess.run(
        [sys.executable, str(script), "--sqlite-db", str(db), "--dry-run"],
        cwd=str(script.parents[2]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 1
