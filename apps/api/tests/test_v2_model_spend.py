"""Model spend: prices, cost, month window and the cap, with a fake cursor. Figures are invented."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from origenlab_api.v2.model_spend import (
    BudgetReached, UnpricedModel, cost_usd, ensure_within_cap, month_window, record_usage,
    spend_summary, worst_case_usd)

UTC = timezone.utc
NOW = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)


class Cur:
    def __init__(self, spent="0"):
        self.spent, self.inserted = spent, None

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("insert"):
            self.inserted = params

    def fetchone(self):
        return (Decimal(self.spent),)


def test_cost_opus_one_million_each():
    assert cost_usd("claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000) == Decimal("24")


def test_cost_counts_cache_tokens_and_quantises():
    got = cost_usd("claude-sonnet-5-5", input_tokens=1, output_tokens=0, cache_read_tokens=1_000_000,
                   cache_write_tokens=1_000_000)
    assert got == Decimal("2.700002")


def test_unpriced_model_refused():
    with pytest.raises(UnpricedModel):
        cost_usd("claude-x", input_tokens=1, output_tokens=1)


def test_worst_case_covers_dearest_fallback():
    cheap = worst_case_usd("claude-haiku-5-5", input_tokens_estimate=1000, max_tokens=1000)
    covered = worst_case_usd("claude-haiku-5-5", input_tokens_estimate=1000, max_tokens=1000,
                             fallback_models=("claude-opus-5-5",))
    assert covered == cost_usd("claude-opus-5-5", input_tokens=1000, output_tokens=1000) > cheap


def test_month_window_santiago_boundary():
    start, end = month_window(datetime(2026, 11, 1, 2, 0, tzinfo=UTC))   # 30 Oct 23:00 in Santiago
    assert start.month == 10 and end.month == 11


def test_month_window_december_rolls_year():
    start, end = month_window(datetime(2026, 12, 15, tzinfo=UTC))
    assert (start.month, end.year, end.month) == (12, 2027, 1)


def test_ensure_within_cap_refuses_when_next_call_would_cross():
    with pytest.raises(BudgetReached) as e:
        ensure_within_cap(Cur("59.50"), "licitaciones", NOW, Decimal("0.60"))
    assert e.value.spent == Decimal("59.50") and e.value.cap == Decimal("60")


def test_ensure_within_cap_allows_exactly_cap():
    ensure_within_cap(Cur("59.40"), "licitaciones", NOW, Decimal("0.60"))


def test_record_usage_missing_tokens_count_zero_and_served_model_priced():
    cur = Cur()
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=None)
    cost = record_usage(cur, feature="licitaciones", step="read", model="claude-haiku-5-5", usage=usage,
                        bid_preparation_id=None, served_model="claude-opus-5-5")
    assert cost == Decimal("4") and cur.inserted[1] == "claude-opus-5-5" and cur.inserted[5] == 0


def test_spend_summary():
    assert spend_summary(Cur("60"), "licitaciones", NOW) == {
        "spent_usd": "60.000000", "cap_usd": "60", "month": "2026-10", "reached": True}
