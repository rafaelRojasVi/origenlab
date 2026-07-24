"""Transactional publication tests for outbound + mart Postgres loaders.

Unit tests use mocks. Integration / MVCC / sequence tests require:

  ORIGENLAB_POSTGRES_TEST_URL=postgresql://…@127.0.0.1/origenlab_pg_test

The URL is structurally validated (PostgreSQL scheme, loopback host, test-only
database name). Production / remote / substring-heuristic URLs are refused.
Schema must already be migrated (alembic upgrade head).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from origenlab_email_pipeline import mart_core_postgres_migrate as mart
from test_sqlite_mart_core_to_postgres_migrate import _make_mart_sqlite

REPO = Path(__file__).resolve().parents[1]
OUTBOUND_SCRIPT = REPO / "scripts" / "migrate" / "sqlite_outbound_sidecars_to_postgres.py"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_PG_SCHEMES = frozenset(
    {
        "postgresql",
        "postgres",
        "postgresql+psycopg",
        "postgres+psycopg",
    }
)
_EXACT_TEST_DBS = frozenset({"origenlab_pg_test"})
_TEST_DB_SUFFIXES = ("_test", "_scratch")
_PROD_DB_TOKENS = ("prod", "production", "live")


def validate_disposable_postgres_test_url(url: str) -> str:
    """Refuse non-disposable ORIGENLAB_POSTGRES_TEST_URL values before any SQL.

    Safety is structural (scheme / hostname / database name only). A password or
    username containing ``test`` must never make a remote URL acceptable.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("ORIGENLAB_POSTGRES_TEST_URL is empty")
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _PG_SCHEMES:
        raise ValueError(
            f"unsupported scheme for disposable Postgres test URL: {scheme!r}"
        )
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"disposable Postgres test URL hostname must be loopback, got {host!r}"
        )
    db = (parsed.path or "").lstrip("/")
    if not db or "/" in db:
        raise ValueError(
            "disposable Postgres test URL must include an explicit database name"
        )
    lowered = db.lower()
    if any(tok in lowered for tok in _PROD_DB_TOKENS):
        raise ValueError(f"production-looking database name refused: {db!r}")
    if db in _EXACT_TEST_DBS:
        return raw
    if any(lowered.endswith(suffix) for suffix in _TEST_DB_SUFFIXES):
        return raw
    raise ValueError(
        "database name must be origenlab_pg_test or end with _test/_scratch, "
        f"got {db!r}"
    )


def _pg_url() -> str | None:
    """Return validated disposable URL, or None when unset.

    When set but invalid, raise so integration tests fail closed before SQL
    (never fall back to ORIGENLAB_POSTGRES_URL / ALEMBIC_DATABASE_URL /
    ORIGENLAB_CLOUD_POSTGRES_URL).
    """
    raw = (os.environ.get("ORIGENLAB_POSTGRES_TEST_URL") or "").strip()
    if not raw:
        return None
    return validate_disposable_postgres_test_url(raw)


requires_pg = pytest.mark.skipif(
    _pg_url() is None,
    reason="Set ORIGENLAB_POSTGRES_TEST_URL for disposable Postgres integration tests.",
)


def _load_outbound():
    spec = importlib.util.spec_from_file_location(
        "sqlite_outbound_sidecars_to_postgres", OUTBOUND_SCRIPT
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ob = _load_outbound()


def test_validate_disposable_postgres_test_url_accepts_ci_url() -> None:
    assert (
        validate_disposable_postgres_test_url(
            "postgresql://postgres:postgres@127.0.0.1:5432/origenlab_pg_test"
        )
        == "postgresql://postgres:postgres@127.0.0.1:5432/origenlab_pg_test"
    )


@pytest.mark.parametrize(
    "url,match",
    [
        (
            "postgresql://postgres:postgres@db.example.com:5432/origenlab_pg_test",
            "loopback",
        ),
        (
            "postgresql://postgres:postgres@127.0.0.1:5432/origenlab_dashboard_prod",
            "production-looking",
        ),
        (
            "postgresql://user:test_password@db.example.com:5432/origenlab_pg_test",
            "loopback",
        ),
        ("postgresql://postgres:postgres@127.0.0.1:5432/", "database name"),
        ("mysql://postgres:postgres@127.0.0.1:5432/origenlab_pg_test", "unsupported scheme"),
    ],
)
def test_validate_disposable_postgres_test_url_rejects(url: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_disposable_postgres_test_url(url)


def test_pg_url_never_falls_back_to_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIGENLAB_POSTGRES_TEST_URL", raising=False)
    monkeypatch.setenv(
        "ORIGENLAB_POSTGRES_URL",
        "postgresql://u:p@127.0.0.1:5432/origenlab_pg_test",
    )
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL",
        "postgresql://u:p@127.0.0.1:5432/origenlab_pg_test",
    )
    monkeypatch.setenv(
        "ORIGENLAB_CLOUD_POSTGRES_URL",
        "postgresql://u:p@127.0.0.1:5432/origenlab_pg_test",
    )
    assert _pg_url() is None


def _make_outbound_sqlite(path: Path, *, email: str = "a@x.com") -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        f"""
        CREATE TABLE contact_email_suppression (
          email TEXT PRIMARY KEY,
          suppression_reason_code TEXT NOT NULL,
          suppression_reason_text TEXT,
          suppression_source TEXT,
          last_bounced_at TEXT,
          updated_at TEXT NOT NULL,
          updated_by TEXT
        );
        CREATE TABLE contact_domain_suppression (
          domain_norm TEXT PRIMARY KEY,
          suppression_reason_text TEXT,
          updated_at TEXT NOT NULL,
          updated_by TEXT
        );
        CREATE TABLE outreach_contact_state (
          contact_email_norm TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          first_contacted_at TEXT,
          last_contacted_at TEXT,
          source TEXT,
          notes TEXT,
          updated_at TEXT NOT NULL,
          updated_by TEXT,
          lead_id INTEGER
        );
        INSERT INTO contact_email_suppression (email, suppression_reason_code, updated_at)
          VALUES ('{email}', 'manual_do_not_contact', '2024-01-01T00:00:00Z');
        INSERT INTO contact_domain_suppression (domain_norm, updated_at)
          VALUES ('x.com', '2024-01-01T00:00:00Z');
        INSERT INTO outreach_contact_state (contact_email_norm, state, updated_at, lead_id)
          VALUES ('{email}', 'contacted', '2024-01-01T00:00:00Z', 1);
        """
    )
    conn.close()


def test_outbound_load_sidecar_table_never_commits() -> None:
    sconn = MagicMock()
    sconn.execute.return_value.fetchone.return_value = (1,)
    scur = MagicMock()
    sconn.cursor.return_value = scur
    scur.fetchmany.side_effect = [
        [("a@x.com", "manual_do_not_contact", None, None, None, "2024-01-01T00:00:00Z", None)],
        [],
    ]
    pconn = MagicMock()
    pcur = MagicMock()
    pconn.cursor.return_value.__enter__.return_value = pcur
    loaded = ob.load_sidecar_table(
        sconn,
        pconn,
        source="contact_email_suppression",
        target="outbound.contact_email_suppression",
        pk="email",
        columns=tuple(ob.TABLE_SPECS[0]["columns"]),
        timestamp_columns=frozenset(ob.TABLE_SPECS[0]["timestamp_columns"]),
        source_exists=True,
        t_start=time.monotonic(),
        fetch_batch=10,
    )
    assert loaded == 1
    pconn.commit.assert_not_called()
    pconn.rollback.assert_not_called()


def test_mart_load_table_never_commits() -> None:
    sconn = MagicMock()
    pconn = MagicMock()
    loaded = mart.load_table(
        sconn,
        pconn,
        spec=mart.TABLE_SPECS[0],
        source_exists=False,
        t_start=time.monotonic(),
    )
    assert loaded == 0
    pconn.commit.assert_not_called()


def test_json_result_includes_transaction_committed() -> None:
    assert "transaction_committed" in ob._empty_result()
    assert "transaction_committed" in mart._empty_result()
    assert "attempted_loaded" in ob._empty_result()
    assert "attempted_loaded" in mart._empty_result()


def test_plan_loader_steps_still_pass_replace() -> None:
    from origenlab_email_pipeline.dashboard_postgres_sync import plan_loader_steps

    steps = plan_loader_steps(only=None, skip_outbound=False, skip_mart=False)
    assert steps[0].name == "outbound_sidecars"
    assert "--replace" in steps[0].argv
    assert steps[1].name == "mart_core"
    assert "--replace" in steps[1].argv
    assert "--tables" in steps[1].argv
    assert "all" in steps[1].argv


@requires_pg
def test_outbound_replace_commits_once_and_is_visible(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db = tmp_path / "out.sqlite"
    _make_outbound_sqlite(db, email="old@x.com")
    assert ob.main(["--sqlite-db", str(db), "--postgres-url", pg, "--replace"]) == 0
    db2 = tmp_path / "out2.sqlite"
    _make_outbound_sqlite(db2, email="new@x.com")
    json_out = tmp_path / "out.json"
    rc = ob.main(
        [
            "--sqlite-db",
            str(db2),
            "--postgres-url",
            pg,
            "--replace",
            "--json-out",
            str(json_out),
        ]
    )
    assert rc == 0
    doc = json.loads(json_out.read_text(encoding="utf-8"))
    assert doc["ok"] is True
    assert doc["transaction_committed"] is True
    assert doc["loaded"]["contact_email_suppression"] == 1
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM outbound.contact_email_suppression")
        assert {r[0] for r in cur.fetchall()} == {"new@x.com"}


@requires_pg
def test_outbound_mvcc_readers_see_old_until_commit(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db_old = tmp_path / "old.sqlite"
    _make_outbound_sqlite(db_old, email="old@x.com")
    assert ob.main(["--sqlite-db", str(db_old), "--postgres-url", pg, "--replace"]) == 0

    writer = psycopg.connect(pg, autocommit=False)
    reader = psycopg.connect(pg, autocommit=True)
    try:
        with writer.cursor() as cur:
            ob.lock_outbound_sidecar_targets(cur)
            ob.delete_outbound_sidecar_targets(cur)
            cur.execute(
                """
                INSERT INTO outbound.contact_email_suppression
                  (email, suppression_reason_code, updated_at)
                VALUES ('partial@x.com', 'manual_do_not_contact', now())
                """
            )
        with reader.cursor() as rcur:
            rcur.execute("SELECT email FROM outbound.contact_email_suppression ORDER BY email")
            emails = [r[0] for r in rcur.fetchall()]
            assert emails == ["old@x.com"]
            rcur.execute("SELECT COUNT(*) FROM outbound.outreach_contact_state")
            assert int(rcur.fetchone()[0]) == 1
        writer.commit()
        with reader.cursor() as rcur:
            rcur.execute("SELECT email FROM outbound.contact_email_suppression")
            assert [r[0] for r in rcur.fetchall()] == ["partial@x.com"]
    finally:
        writer.close()
        reader.close()
    assert ob.main(["--sqlite-db", str(db_old), "--postgres-url", pg, "--replace"]) == 0


@requires_pg
def test_outbound_failure_after_delete_rolls_back(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db = tmp_path / "out.sqlite"
    _make_outbound_sqlite(db, email="keep@x.com")
    assert ob.main(["--sqlite-db", str(db), "--postgres-url", pg, "--replace"]) == 0

    def boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("injected insert failure")

    with patch.object(ob, "load_sidecar_table", side_effect=boom):
        rc = ob.main(["--sqlite-db", str(db), "--postgres-url", pg, "--replace"])
    assert rc == 1
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM outbound.contact_email_suppression")
        assert [r[0] for r in cur.fetchall()] == ["keep@x.com"]
        cur.execute("SELECT COUNT(*) FROM outbound.outreach_contact_state")
        assert int(cur.fetchone()[0]) == 1


@requires_pg
def test_outbound_validation_failure_rolls_back(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db = tmp_path / "out.sqlite"
    _make_outbound_sqlite(db, email="keep2@x.com")
    assert ob.main(["--sqlite-db", str(db), "--postgres-url", pg, "--replace"]) == 0

    def bad_validate(**_k: Any) -> None:
        raise ob.ValidationError(
            "Post-load validation failed for outbound sidecar migration."
        )

    with patch.object(ob, "_validate_outbound_publication", side_effect=bad_validate):
        rc = ob.main(["--sqlite-db", str(db), "--postgres-url", pg, "--replace"])
    assert rc == 1
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM outbound.contact_email_suppression")
        assert [r[0] for r in cur.fetchall()] == ["keep2@x.com"]


@requires_pg
def test_outbound_concurrent_writer_waits_on_lock(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db = tmp_path / "out.sqlite"
    _make_outbound_sqlite(db)
    assert ob.main(["--sqlite-db", str(db), "--postgres-url", pg, "--replace"]) == 0

    holder = psycopg.connect(pg, autocommit=False)
    waiter_result: dict[str, Any] = {}

    def wait_for_lock() -> None:
        try:
            w = psycopg.connect(pg, autocommit=False)
            with w.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '2s'")
                t0 = time.monotonic()
                try:
                    cur.execute(
                        "LOCK TABLE outbound.contact_email_suppression "
                        "IN SHARE ROW EXCLUSIVE MODE"
                    )
                    waiter_result["acquired"] = True
                except Exception as exc:  # noqa: BLE001
                    waiter_result["error"] = type(exc).__name__
                    waiter_result["msg"] = str(exc)
                waiter_result["waited"] = time.monotonic() - t0
            w.rollback()
            w.close()
        except Exception as exc:  # noqa: BLE001
            waiter_result["boot_error"] = str(exc)

    with holder.cursor() as cur:
        ob.lock_outbound_sidecar_targets(cur)
    thr = threading.Thread(target=wait_for_lock)
    thr.start()
    time.sleep(0.5)
    holder.rollback()
    holder.close()
    thr.join(timeout=10)
    assert "boot_error" not in waiter_result
    assert waiter_result.get("waited", 0) >= 0.4 or "error" in waiter_result


@requires_pg
def test_mart_archive_replace_and_mvcc(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db = tmp_path / "mart.sqlite"
    _make_mart_sqlite(db)
    rc = mart.run_migration(
        [
            "--sqlite-db",
            str(db),
            "--postgres-url",
            pg,
            "--replace",
            "--tables",
            "archive",
        ]
    )
    assert rc == 0
    with psycopg.connect(pg) as c, c.cursor() as cur:
        cur.execute("SELECT email FROM mart.contact_master ORDER BY email")
        emails = [r[0] for r in cur.fetchall()]
        assert emails
        old_email = emails[0]
        cur.execute("SELECT COUNT(*) FROM mart.opportunity_signals")
        opp_before = int(cur.fetchone()[0])

    writer = psycopg.connect(pg, autocommit=False)
    reader = psycopg.connect(pg, autocommit=True)
    try:
        with writer.cursor() as cur:
            mart.lock_mart_targets(cur, mart.TABLE_SPECS)
            mart.delete_targets_for_specs(cur, mart.TABLE_SPECS)
            # Partial insert only — should remain invisible to readers.
            cur.execute(
                """
                INSERT INTO mart.contact_master (email, domain, first_seen_at, last_seen_at, total_emails)
                VALUES ('partial@lab.cl', 'lab.cl', now(), now(), 1)
                """
            )
        with reader.cursor() as rcur:
            rcur.execute("SELECT email FROM mart.contact_master ORDER BY email")
            assert [r[0] for r in rcur.fetchall()] == [old_email]
            rcur.execute("SELECT COUNT(*) FROM mart.opportunity_signals")
            assert int(rcur.fetchone()[0]) == opp_before
        writer.rollback()
    finally:
        writer.close()
        reader.close()


@requires_pg
def test_mart_failure_after_delete_restores_selected(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db = tmp_path / "mart.sqlite"
    _make_mart_sqlite(db)
    assert (
        mart.run_migration(
            ["--sqlite-db", str(db), "--postgres-url", pg, "--replace", "--tables", "archive"]
        )
        == 0
    )
    with psycopg.connect(pg) as c, c.cursor() as cur:
        cur.execute("SELECT email FROM mart.contact_master ORDER BY email")
        emails_before = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM mart.opportunity_signals")
        opp_before = int(cur.fetchone()[0])

    def boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("injected mart load failure")

    with patch.object(mart, "load_table", side_effect=boom):
        rc = mart.run_migration(
            ["--sqlite-db", str(db), "--postgres-url", pg, "--replace", "--tables", "archive"]
        )
    assert rc == 1
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT email FROM mart.contact_master ORDER BY email")
        assert [r[0] for r in cur.fetchall()] == emails_before
        cur.execute("SELECT COUNT(*) FROM mart.opportunity_signals")
        assert int(cur.fetchone()[0]) == opp_before


@requires_pg
def test_mart_canonical_does_not_mutate_archive(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM mart.contact_master")
        archive_before = int(cur.fetchone()[0])

    db = tmp_path / "empty_canon.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY, source_file TEXT, message_id TEXT, date_iso TEXT,
          folder TEXT, sender TEXT, recipients TEXT, subject TEXT, body TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    rc = mart.run_migration(
        [
            "--sqlite-db",
            str(db),
            "--postgres-url",
            pg,
            "--replace",
            "--tables",
            "canonical",
        ]
    )
    assert rc in (0, 1)
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM mart.contact_master")
        assert int(cur.fetchone()[0]) == archive_before


def _opp_seq_state(cur: Any, table: str) -> tuple[str, int, bool]:
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    seq = cur.fetchone()[0]
    assert seq
    schema, name = seq.split(".", 1)
    cur.execute(
        mart.pg_sql.SQL("SELECT last_value, is_called FROM {}.{}").format(
            mart.pg_sql.Identifier(schema),
            mart.pg_sql.Identifier(name),
        )
    )
    last_value, is_called = cur.fetchone()
    return str(seq), int(last_value), bool(is_called)


def _seed_opp_table(cur: Any, table: str, row_id: int) -> None:
    cur.execute(f"DELETE FROM {table}")
    cur.execute(
        f"""
        INSERT INTO {table}
          (id, signal_type, entity_kind, entity_key, score, created_at)
        VALUES (%s, 'seed', 'contact', 'old@x.cl', 1.0, now())
        """,
        (row_id,),
    )
    schema, name = table.split(".", 1)
    seq = f"{schema}.{name}_id_seq"
    # Establish a known non-default sequence state without relying on publication code.
    cur.execute(
        mart.pg_sql.SQL("ALTER SEQUENCE {}.{} RESTART WITH {}").format(
            mart.pg_sql.Identifier(schema),
            mart.pg_sql.Identifier(f"{name}_id_seq"),
            mart.pg_sql.Literal(row_id + 1),
        )
    )
    # Advance once so is_called is true and last_value is row_id+1.
    cur.execute("SELECT nextval(%s)", (seq,))
    assert int(cur.fetchone()[0]) == row_id + 1


@pytest.mark.parametrize(
    "table",
    ["mart.opportunity_signals", "mart.opportunity_signals_canonical"],
)
@requires_pg
def test_opp_sequence_restart_rolls_back_with_rows(table: str) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    specs = tuple(s for s in mart.ALL_TABLE_SPECS if s["target"] == table)
    assert len(specs) == 1

    with psycopg.connect(pg, autocommit=False) as conn, conn.cursor() as cur:
        _seed_opp_table(cur, table, row_id=9001)
        conn.commit()

    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {table} ORDER BY id")
        ids_before = [int(r[0]) for r in cur.fetchall()]
        assert ids_before == [9001]
        seq_name, last_before, called_before = _opp_seq_state(cur, table)
        assert last_before == 9002
        assert called_before is True

    writer = psycopg.connect(pg, autocommit=False)
    try:
        with writer.cursor() as cur:
            mart.lock_mart_targets(cur, specs)
            mart.assert_opp_sequence_restart_authority(cur, specs)
            mart.delete_targets_for_specs(cur, specs)
            cur.execute(
                f"""
                INSERT INTO {table}
                  (id, signal_type, entity_kind, entity_key, score, created_at)
                VALUES (42, 'new', 'contact', 'new@x.cl', 2.0, now())
                """
            )
            mart.restart_opp_sequences(cur, specs)
            _, last_mid, called_mid = _opp_seq_state(cur, table)
            assert last_mid == 43
            assert called_mid is False
            raise RuntimeError("injected failure after sequence restart")
    except RuntimeError:
        writer.rollback()
    finally:
        writer.close()

    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM {table} ORDER BY id")
        assert [int(r[0]) for r in cur.fetchall()] == ids_before
        _, last_after, called_after = _opp_seq_state(cur, table)
        assert last_after == last_before
        assert called_after == called_before
        # nextval() is non-transactional: it permanently advances the sequence even
        # if this connection later rolls back. Use it only for the collision check,
        # then restore a known disposable state with transactional ALTER SEQUENCE.
        cur.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", (table,))
        nxt = int(cur.fetchone()[0])
        assert nxt == 9003
        assert nxt not in ids_before
        schema, name = table.split(".", 1)
        cur.execute(
            mart.pg_sql.SQL("ALTER SEQUENCE {}.{} RESTART WITH {}").format(
                mart.pg_sql.Identifier(schema),
                mart.pg_sql.Identifier(f"{name}_id_seq"),
                mart.pg_sql.Literal(int(max(ids_before)) + 1),
            )
        )
        conn.commit()


@pytest.mark.parametrize(
    "table",
    ["mart.opportunity_signals", "mart.opportunity_signals_canonical"],
)
@requires_pg
def test_opp_sequence_restart_commits_nextval_is_max_plus_one(table: str) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    specs = tuple(s for s in mart.ALL_TABLE_SPECS if s["target"] == table)
    assert len(specs) == 1

    with psycopg.connect(pg, autocommit=False) as conn, conn.cursor() as cur:
        mart.lock_mart_targets(cur, specs)
        mart.assert_opp_sequence_restart_authority(cur, specs)
        mart.delete_targets_for_specs(cur, specs)
        cur.execute(
            f"""
            INSERT INTO {table}
              (id, signal_type, entity_kind, entity_key, score, created_at)
            VALUES
              (10, 'a', 'contact', 'a@x.cl', 1.0, now()),
              (25, 'b', 'contact', 'b@x.cl', 1.0, now())
            """
        )
        mart.restart_opp_sequences(cur, specs)
        conn.commit()

    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", (table,))
        assert int(cur.fetchone()[0]) == 26
        conn.rollback()


@pytest.mark.parametrize(
    "table",
    ["mart.opportunity_signals", "mart.opportunity_signals_canonical"],
)
@requires_pg
def test_opp_sequence_empty_table_restarts_at_one(table: str) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    specs = tuple(s for s in mart.ALL_TABLE_SPECS if s["target"] == table)
    with psycopg.connect(pg, autocommit=False) as conn, conn.cursor() as cur:
        mart.lock_mart_targets(cur, specs)
        mart.delete_targets_for_specs(cur, specs)
        mart.restart_opp_sequences(cur, specs)
        conn.commit()
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        cur.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", (table,))
        assert int(cur.fetchone()[0]) == 1
        conn.rollback()


@requires_pg
def test_archive_replace_does_not_modify_canonical_sequence(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    canon = "mart.opportunity_signals_canonical"
    with psycopg.connect(pg, autocommit=False) as conn, conn.cursor() as cur:
        _seed_opp_table(cur, canon, row_id=7001)
        conn.commit()
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        _, last_before, called_before = _opp_seq_state(cur, canon)

    db = tmp_path / "mart.sqlite"
    _make_mart_sqlite(db)
    assert (
        mart.run_migration(
            ["--sqlite-db", str(db), "--postgres-url", pg, "--replace", "--tables", "archive"]
        )
        == 0
    )
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        _, last_after, called_after = _opp_seq_state(cur, canon)
        assert last_after == last_before
        assert called_after == called_before
        cur.execute(f"SELECT id FROM {canon}")
        assert [int(r[0]) for r in cur.fetchall()] == [7001]


@requires_pg
def test_canonical_replace_does_not_modify_archive_sequence(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    archive = "mart.opportunity_signals"
    with psycopg.connect(pg, autocommit=False) as conn, conn.cursor() as cur:
        _seed_opp_table(cur, archive, row_id=8001)
        conn.commit()
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        _, last_before, called_before = _opp_seq_state(cur, archive)

    db = tmp_path / "empty_canon.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY, source_file TEXT, message_id TEXT, date_iso TEXT,
          folder TEXT, sender TEXT, recipients TEXT, subject TEXT, body TEXT
        );
        CREATE TABLE contact_master (email TEXT PRIMARY KEY, domain TEXT);
        CREATE TABLE organization_master (domain TEXT PRIMARY KEY);
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY, signal_type TEXT, entity_kind TEXT, entity_key TEXT,
          email_id INTEGER, attachment_id INTEGER, score REAL, details_json TEXT,
          created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    mart.run_migration(
        ["--sqlite-db", str(db), "--postgres-url", pg, "--replace", "--tables", "canonical"]
    )
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        _, last_after, called_after = _opp_seq_state(cur, archive)
        assert last_after == last_before
        assert called_after == called_before
        cur.execute(f"SELECT id FROM {archive}")
        assert [int(r[0]) for r in cur.fetchall()] == [8001]


@requires_pg
def test_tables_all_repairs_both_opp_sequences(tmp_path: Path) -> None:
    import psycopg

    pg = _pg_url()
    assert pg
    db = tmp_path / "mart.sqlite"
    _make_mart_sqlite(db)
    # Enrich sqlite so both archive opportunity + empty canonical path still run.
    assert (
        mart.run_migration(
            ["--sqlite-db", str(db), "--postgres-url", pg, "--replace", "--tables", "all"]
        )
        == 0
    )
    with psycopg.connect(pg) as conn, conn.cursor() as cur:
        for table in mart._OPP_SEQ_TABLES:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
            max_id = int(cur.fetchone()[0])
            cur.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", (table,))
            nxt = int(cur.fetchone()[0])
            assert nxt == (max_id + 1 if max_id > 0 else 1)
        conn.rollback()


@requires_pg
def test_mart_publication_source_has_no_setval() -> None:
    src = Path(mart.__file__).read_text(encoding="utf-8")
    assert "setval(" not in src
    assert "ALTER SEQUENCE" in src or "RESTART WITH" in src
    assert "pg_has_role(current_user, c.relowner, 'USAGE')" in src
    assert "pg_has_role(current_user, c.relowner, 'MEMBER')" not in src


def _drop_temp_roles(cur: Any, roles: tuple[str, ...]) -> None:
    from psycopg import sql as pg_sql

    for role in roles:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        if cur.fetchone() is None:
            continue
        cur.execute(
            pg_sql.SQL("REVOKE ALL ON SCHEMA mart FROM {}").format(
                pg_sql.Identifier(role)
            )
        )
        cur.execute(
            pg_sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA mart FROM {}").format(
                pg_sql.Identifier(role)
            )
        )
        cur.execute(
            pg_sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA mart FROM {}").format(
                pg_sql.Identifier(role)
            )
        )
        cur.execute(
            pg_sql.SQL("DROP ROLE IF EXISTS {}").format(pg_sql.Identifier(role))
        )


def _table_and_sequence_owner(cur: Any, table: str) -> tuple[str, str, str]:
    """Return (table_owner, sequence_identity, sequence_owner)."""
    schema, name = table.split(".", 1)
    cur.execute(
        """
        SELECT pg_catalog.pg_get_userbyid(c.relowner)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'
        """,
        (schema, name),
    )
    table_owner = str(cur.fetchone()[0])
    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
    seq_identity = str(cur.fetchone()[0])
    seq_schema, seq_name = seq_identity.split(".", 1)
    cur.execute(
        """
        SELECT pg_catalog.pg_get_userbyid(c.relowner)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'S'
        """,
        (seq_schema, seq_name),
    )
    seq_owner = str(cur.fetchone()[0])
    return table_owner, seq_identity, seq_owner


@requires_pg
def test_opp_sequence_authority_exact_owner_superuser_and_inherit() -> None:
    """Exact owner, superuser, and INHERIT membership pass; NOINHERIT MEMBER rejects."""
    import uuid

    import psycopg
    from psycopg import sql as pg_sql

    pg = _pg_url()
    assert pg
    table = "mart.opportunity_signals"
    specs = tuple(s for s in mart.ALL_TABLE_SPECS if s["target"] == table)
    suffix = uuid.uuid4().hex[:12]
    owner_role = f"ol_txpub_owner_{suffix}"
    inherit_role = f"ol_txpub_inherit_{suffix}"
    noinher_role = f"ol_txpub_noinh_{suffix}"
    unrelated_role = f"ol_txpub_other_{suffix}"
    roles = (owner_role, inherit_role, noinher_role, unrelated_role)

    admin = psycopg.connect(pg, autocommit=True)
    original_table_owner: str | None = None
    try:
        with admin.cursor() as cur:
            original_table_owner, _seq, original_seq_owner = _table_and_sequence_owner(
                cur, table
            )
            assert original_table_owner == original_seq_owner

            _drop_temp_roles(cur, roles)
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(pg_sql.Identifier(owner_role))
            )
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN INHERIT").format(
                    pg_sql.Identifier(inherit_role)
                )
            )
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(
                    pg_sql.Identifier(noinher_role)
                )
            )
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(
                    pg_sql.Identifier(unrelated_role)
                )
            )
            cur.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(owner_role),
                    pg_sql.Identifier(inherit_role),
                )
            )
            cur.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(owner_role),
                    pg_sql.Identifier(noinher_role),
                )
            )
            for role in roles:
                cur.execute(
                    pg_sql.SQL("GRANT USAGE ON SCHEMA mart TO {}").format(
                        pg_sql.Identifier(role)
                    )
                )
            # Linked BIGSERIAL ownership follows the table owner.
            t_schema, t_name = table.split(".", 1)
            cur.execute(
                pg_sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                    pg_sql.Identifier(t_schema),
                    pg_sql.Identifier(t_name),
                    pg_sql.Identifier(owner_role),
                )
            )
            _t_owner, _seq, seq_owner = _table_and_sequence_owner(cur, table)
            assert seq_owner == owner_role

            # 1) Exact sequence owner passes.
            cur.execute(pg_sql.SQL("SET ROLE {}").format(pg_sql.Identifier(owner_role)))
            mart.assert_opp_sequence_restart_authority(cur, specs)
            cur.execute("RESET ROLE")

            # 2) Superuser passes (admin connection role).
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            assert bool(cur.fetchone()[0])
            mart.assert_opp_sequence_restart_authority(cur, specs)

            # 3) INHERIT member with immediately usable owner privileges passes.
            cur.execute(pg_sql.SQL("SET ROLE {}").format(pg_sql.Identifier(inherit_role)))
            cur.execute(
                "SELECT pg_has_role(current_user, %s::regrole, 'USAGE'), "
                "pg_has_role(current_user, %s::regrole, 'MEMBER')",
                (owner_role, owner_role),
            )
            usage, member = cur.fetchone()
            assert bool(usage) is True
            assert bool(member) is True
            mart.assert_opp_sequence_restart_authority(cur, specs)
            cur.execute("RESET ROLE")

            # 4) NOINHERIT MEMBER-only path: MEMBER true, USAGE false → reject.
            cur.execute(pg_sql.SQL("SET ROLE {}").format(pg_sql.Identifier(noinher_role)))
            cur.execute(
                "SELECT pg_has_role(current_user, %s::regrole, 'USAGE'), "
                "pg_has_role(current_user, %s::regrole, 'MEMBER')",
                (owner_role, owner_role),
            )
            usage, member = cur.fetchone()
            assert bool(usage) is False
            assert bool(member) is True
            with pytest.raises(PermissionError, match="immediately available owner privileges"):
                mart.assert_opp_sequence_restart_authority(cur, specs)
            cur.execute("RESET ROLE")

            # 5) Unrelated role rejects.
            cur.execute(
                pg_sql.SQL("SET ROLE {}").format(pg_sql.Identifier(unrelated_role))
            )
            with pytest.raises(PermissionError, match="cannot ALTER SEQUENCE"):
                mart.assert_opp_sequence_restart_authority(cur, specs)
            cur.execute("RESET ROLE")
    finally:
        try:
            with admin.cursor() as cur:
                cur.execute("RESET ROLE")
                if original_table_owner:
                    t_schema, t_name = table.split(".", 1)
                    cur.execute(
                        pg_sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                            pg_sql.Identifier(t_schema),
                            pg_sql.Identifier(t_name),
                            pg_sql.Identifier(original_table_owner),
                        )
                    )
                _drop_temp_roles(cur, roles)
        finally:
            admin.close()


@requires_pg
def test_opp_sequence_authority_rejects_before_delete_and_load() -> None:
    """NOINHERIT membership fails closed before delete/load/restart mutation."""
    import uuid

    import psycopg
    from psycopg import sql as pg_sql

    pg = _pg_url()
    assert pg
    table = "mart.opportunity_signals"
    specs = tuple(s for s in mart.ALL_TABLE_SPECS if s["target"] == table)
    suffix = uuid.uuid4().hex[:12]
    owner_role = f"ol_txpub_own2_{suffix}"
    noinher_role = f"ol_txpub_ni2_{suffix}"
    roles = (owner_role, noinher_role)

    admin = psycopg.connect(pg, autocommit=True)
    original_table_owner: str | None = None
    try:
        with admin.cursor() as cur:
            original_table_owner, _seq, _seq_owner = _table_and_sequence_owner(cur, table)
            _drop_temp_roles(cur, roles)
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN").format(pg_sql.Identifier(owner_role))
            )
            cur.execute(
                pg_sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(
                    pg_sql.Identifier(noinher_role)
                )
            )
            cur.execute(
                pg_sql.SQL("GRANT {} TO {}").format(
                    pg_sql.Identifier(owner_role),
                    pg_sql.Identifier(noinher_role),
                )
            )
            # Table privileges so LOCK can succeed; ALTER SEQUENCE still needs ownership.
            cur.execute(
                pg_sql.SQL("GRANT USAGE ON SCHEMA mart TO {}").format(
                    pg_sql.Identifier(noinher_role)
                )
            )
            for target in (str(s["target"]) for s in specs):
                t_schema, t_name = target.split(".", 1)
                cur.execute(
                    pg_sql.SQL(
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON {}.{} TO {}"
                    ).format(
                        pg_sql.Identifier(t_schema),
                        pg_sql.Identifier(t_name),
                        pg_sql.Identifier(noinher_role),
                    )
                )
            t_schema, t_name = table.split(".", 1)
            cur.execute(
                pg_sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                    pg_sql.Identifier(t_schema),
                    pg_sql.Identifier(t_name),
                    pg_sql.Identifier(owner_role),
                )
            )

        mutation_calls: list[str] = []

        def track_delete(*_a: Any, **_k: Any) -> None:
            mutation_calls.append("delete")
            raise AssertionError("delete_targets_for_specs must not run")

        def track_load(*_a: Any, **_k: Any) -> int:
            mutation_calls.append("load")
            raise AssertionError("load_table must not run")

        def track_restart(*_a: Any, **_k: Any) -> None:
            mutation_calls.append("restart")
            raise AssertionError("restart_opp_sequences must not run")

        session = psycopg.connect(pg, autocommit=False)
        try:
            with session.cursor() as cur:
                cur.execute(
                    pg_sql.SQL("SET ROLE {}").format(pg_sql.Identifier(noinher_role))
                )
                cur.execute(
                    "SELECT pg_has_role(current_user, %s::regrole, 'USAGE'), "
                    "pg_has_role(current_user, %s::regrole, 'MEMBER')",
                    (owner_role, owner_role),
                )
                usage, member = cur.fetchone()
                assert bool(usage) is False
                assert bool(member) is True

                # Mirror run_migration ordering: lock → authority → delete → load → restart.
                mart.lock_mart_targets(cur, specs)
                with (
                    patch.object(mart, "delete_targets_for_specs", side_effect=track_delete),
                    patch.object(mart, "load_table", side_effect=track_load),
                    patch.object(mart, "restart_opp_sequences", side_effect=track_restart),
                ):
                    with pytest.raises(PermissionError) as raised:
                        mart.assert_opp_sequence_restart_authority(cur, specs)
                        mart.delete_targets_for_specs(cur, specs)
                        mart.load_table(
                            MagicMock(),
                            session,
                            spec=specs[0],
                            source_exists=False,
                            t_start=time.monotonic(),
                        )
                        mart.restart_opp_sequences(cur, specs)
            assert mutation_calls == []
            err = mart.sanitize_migrate_error(raised.value, postgres_url=pg)
            assert noinher_role in err
            assert owner_role in err
            assert "USAGE" in err or "immediately available" in err
            assert "postgres:postgres@" not in err
            assert "postgresql://" not in err.lower()
            assert pg not in err
        finally:
            session.rollback()
            session.close()
    finally:
        try:
            with admin.cursor() as cur:
                cur.execute("RESET ROLE")
                if original_table_owner:
                    t_schema, t_name = table.split(".", 1)
                    cur.execute(
                        pg_sql.SQL("ALTER TABLE {}.{} OWNER TO {}").format(
                            pg_sql.Identifier(t_schema),
                            pg_sql.Identifier(t_name),
                            pg_sql.Identifier(original_table_owner),
                        )
                    )
                _drop_temp_roles(cur, roles)
        finally:
            admin.close()
