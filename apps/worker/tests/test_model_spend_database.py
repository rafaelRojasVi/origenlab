"""`record_usage` and the cap against PostgreSQL as the real `origenlab_worker` login (the API has
no INSERT on `platform.model_usage`). Every figure is invented."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from dbhelp import owner_rows
from origenlab_api.v2.model_spend import BudgetReached, ensure_within_cap, month_spend, record_usage
from origenlab_worker.database import local_test_target, open_verified_connection
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

pytestmark = needs_worker_db
NOW = datetime.now(timezone.utc)  # record_usage stamps the real clock, so the cap window must too


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


def _conn(dsn):
    return open_verified_connection(local_test_target(worker_dsn(dsn)), application_name="test-model-spend",
                                    privileges=(("platform.model_usage", "INSERT"),), policies=frozenset())


def test_record_usage_reads_cache_tokens_returns_cost_and_feeds_the_cap(dsn) -> None:
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=500_000,
                            cache_read_input_tokens=1_000_000, cache_creation_input_tokens=None)
    with _conn(dsn) as conn, conn.cursor() as cur:
        cost = record_usage(cur, feature="licitaciones", step="read", model="claude-opus-5-5", usage=usage,
                            bid_preparation_id=None)
        assert cost == Decimal("14.2")                       # 4 + 10 + 0.20
        assert month_spend(cur, "licitaciones", NOW) == Decimal("14.2")
        ensure_within_cap(cur, "licitaciones", NOW, Decimal("45.8"))
        with pytest.raises(BudgetReached):
            ensure_within_cap(cur, "licitaciones", NOW, Decimal("45.81"))
    [row] = owner_rows(dsn, "select input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
                            "usd_cost::text from platform.model_usage")
    assert row == (1_000_000, 500_000, 1_000_000, 0, "14.200000")
