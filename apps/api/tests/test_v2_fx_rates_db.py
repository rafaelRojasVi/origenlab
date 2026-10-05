"""The Resumen's stored-rate fallback and write-through against PostgreSQL as `origenlab_api`.

Rows are inserted as the owner (the migrator's role), then read and written through the real
runtime login, the way `build_fx_rates` wires them in the API. Every figure is invented.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal as D

import psycopg
import pytest

from origenlab_api.v2.crm_workspace_routes import build_fx_rates
from origenlab_api.v2.fx_rates import FxRates, FxUnavailable
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

#: 2026-10-05 15:00 UTC — noon in Santiago.
_WALL = dt.datetime(2026, 10, 5, 15, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _down():
    raise OSError("connection refused")


def _fx(dsn: str, sources) -> FxRates:
    built = build_fx_rates(psycopg.connect, runtime_dsn(dsn), statement_timeout_ms=5_000)
    # The real reader and writer, with invented sources and a fixed clock.
    return FxRates(sources=sources, wall_clock=lambda: _WALL,
                   store_reader=built._store_reader, store_writer=built._store_writer)


@needs_db
def test_empty_store_then_write_through_then_stored_fallback(disposable_database) -> None:
    dsn = disposable_database
    with pytest.raises(FxUnavailable):
        _fx(dsn, (("mindicador.cl", _down),)).current()

    body = {
        "dolar": {"fecha": "2026-10-03T03:00:00.000Z", "valor": 951.25},
        "euro": {"fecha": "2026-10-03T03:00:00.000Z", "valor": 1100.5},
        "uf": {"fecha": "2026-10-03T03:00:00.000Z", "valor": 40000.0},
    }
    for _ in range(2):  # the same day twice: the second is a conflict, nothing new
        fx = _fx(dsn, (("mindicador.cl", lambda: body),))
        fx.current()
        assert fx.wait_for_write_through(10)
    rows = _owner(dsn, "select currency, rate_date, clp_per_unit, source, provider from catalog.fx_rate "
                       "order by currency")
    assert rows == [("EUR", dt.date(2026, 10, 3), D("1100.500000"), "bcentral", "mindicador"),
                    ("USD", dt.date(2026, 10, 3), D("951.250000"), "bcentral", "mindicador")]

    out = _fx(dsn, (("mindicador.cl", _down),)).current()
    assert out["source"] == "catalog.fx_rate" and out["stale"] is True
    assert [(r["code"], r["clp"], r["as_of"]) for r in out["rates"]] == [
        ("USD", 951.25, "2026-10-03"), ("EUR", 1100.5, "2026-10-03")]


@needs_db
def test_newest_manual_row_wins_its_day_and_future_rows_are_ignored(disposable_database) -> None:
    dsn = disposable_database
    op = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                     "values (gen_random_uuid(), %s, 'Operadora Prueba', 'sales', 'active') returning id",
                (f"op-{uuid.uuid4().hex[:8]}@example.test",))[0][0]
    _owner(dsn, "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider) values "
                "('2026-10-04', 'USD', 960, 'bcentral', 'mindicador'), "
                "('2026-10-09', 'USD', 999, 'bcentral', 'mindicador')")
    for clp, at in (("961", "2026-10-04T13:00:00+00"), ("962", "2026-10-04T14:00:00+00")):
        _owner(dsn, "insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider, reason, "
                    "recorded_by_operator_id, created_at) values "
                    "('2026-10-04', 'USD', %s, 'manual', 'operator', 'corrección sintética', %s, %s)",
               (clp, op, at))
    out = _fx(dsn, (("mindicador.cl", _down),)).current()
    usd = next(r for r in out["rates"] if r["code"] == "USD")
    assert (usd["clp"], usd["as_of"]) == (962.0, "2026-10-04")
