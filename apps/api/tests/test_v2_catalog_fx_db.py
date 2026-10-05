"""PricingFx cache fill against PostgreSQL as the real `origenlab_api` role."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import psycopg
import pytest

from origenlab_api.v2.catalog.fx import FxCacheWriter, PricingFx
from origenlab_api.v2.catalog.reads import V2CatalogReads
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


class _P:
    name = "mindicador"

    def observed(self, currency, start, end):
        return {dt.date(2026, 10, 1): D("951.25"), dt.date(2026, 10, 2): D("952.5")}


@needs_db
def test_miss_fills_cache_idempotently_and_second_call_is_stored(disposable_database):
    dsn = runtime_dsn(disposable_database)
    writer = FxCacheWriter(psycopg.connect, dsn)
    reads = V2CatalogReads(psycopg.connect, dsn)
    fx = PricingFx(reads, writer, [_P()], lambda: dt.date(2026, 10, 5))
    assert fx.rate("USD", dt.date(2026, 10, 2))["clp_per_unit"] == D("952.5")
    writer([{"rate_date": dt.date(2026, 10, 2), "currency": "USD", "clp_per_unit": D("1"), "provider": "mindicador"}])
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from catalog.fx_rate where currency = 'USD'")
        assert cur.fetchone()[0] == 2
    fx2 = PricingFx(reads, writer, [], lambda: dt.date(2026, 10, 5))
    q = fx2.rate("USD", dt.date(2026, 10, 3))
    assert q["clp_per_unit"] == D("952.500000") and q["as_of"] == dt.date(2026, 10, 2) and q["source"] == "bcentral"
