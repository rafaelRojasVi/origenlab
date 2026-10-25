"""Model spend: prices, cost, month window and the cap, with a fake cursor. Figures are invented."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from origenlab_api.v2.model_spend import (
    BudgetReached, UnpricedModel, cost_usd, ensure_within_cap, month_window, record_usage,
    spend_summary, with_cap_lock, worst_case_usd)

UTC = timezone.utc
NOW = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)


class Cur:
    def __init__(self, spent="0"):
        self.spent, self.inserted, self.sql, self.inserts = spent, None, [], []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if sql.lstrip().startswith("insert"):
            self.inserted = params
            self.inserts.append(params)

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


@pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-opus-5"])
def test_every_permitted_fallback_target_is_priced(model):
    assert cost_usd(model, input_tokens=1_000_000, output_tokens=1_000_000) == Decimal("30")


def test_the_worst_usage_puts_the_input_at_its_dearer_rate():
    from origenlab_api.v2.model_spend import worst_usage

    usage = worst_usage("claude-opus-5-5", input_tokens_estimate=1000, max_tokens=500)
    assert cost_usd("claude-opus-5-5", input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    cache_write_tokens=usage.cache_creation_input_tokens) == worst_case_usd(
        "claude-opus-5-5", input_tokens_estimate=1000, max_tokens=500)


def test_worst_case_counts_every_attempt_a_fallback_may_bill():
    """A declined attempt bills at its model's rates and the fallback attempt at its own (claude-api
    skill, server-side fallbacks → Billing): the worst case is their sum, not the dearest one."""
    cheap = worst_case_usd("claude-haiku-5-5", input_tokens_estimate=1000, max_tokens=1000)
    dear = worst_case_usd("claude-opus-4-8", input_tokens_estimate=1000, max_tokens=1000)
    covered = worst_case_usd("claude-haiku-5-5", input_tokens_estimate=1000, max_tokens=1000,
                             fallback_models=("claude-opus-4-8",))
    assert covered == cheap + dear


def test_record_usage_records_each_billed_attempt_at_its_own_model():
    cur = Cur()
    usage = SimpleNamespace(
        input_tokens=1_000_000, output_tokens=1_000_000,  # top level: only the returned attempt
        iterations=[
            SimpleNamespace(type="message", model=None, input_tokens=1_000_000, output_tokens=500_000,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0),
            SimpleNamespace(type="fallback_message", model="claude-opus-4-8", input_tokens=1_000_000,
                            output_tokens=1_000_000, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        ])
    cost = record_usage(cur, feature="licitaciones", step="read", model="claude-opus-5-5", usage=usage,
                        bid_preparation_id=None, served_model="claude-opus-4-8")
    assert cost == Decimal("14") + Decimal("30")  # opus 5.5: 4 + 10; opus 4.8: 5 + 25
    assert [(row[1], row[9]) for row in cur.inserts] == [("claude-opus-5-5", Decimal("14")),
                                                          ("claude-opus-4-8", Decimal("30"))]


def test_record_usage_without_iterations_uses_the_top_level_usage():
    cur = Cur()
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, iterations=None)
    assert record_usage(cur, feature="licitaciones", step="read", model="claude-opus-5-5", usage=usage,
                        bid_preparation_id=None) == Decimal("4")
    assert len(cur.inserts) == 1


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
    assert cost == Decimal("4") and cur.inserted[1] == "claude-opus-5-5" and cur.inserted[6] == 0


def test_spend_summary():
    assert spend_summary(Cur("60"), "licitaciones", NOW) == {
        "spent_usd": "60.000000", "cap_usd": "60", "month": "2026-10", "reached": True}


def test_worst_case_uses_cache_write_when_dearer_than_input():
    got = worst_case_usd("claude-opus-5-5", input_tokens_estimate=1_000_000, max_tokens=0)
    assert got == Decimal("5")


def test_dated_alias_priced_as_base_model():
    assert cost_usd("claude-opus-5-5-20261001", input_tokens=1_000_000, output_tokens=0) == Decimal("4")


def test_record_usage_unknown_served_model_still_inserted_at_dearest_price():
    cur = Cur()
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = record_usage(cur, feature="licitaciones", step="read", model="claude-haiku-5-5", usage=usage,
                        bid_preparation_id=None, served_model="claude-mystery-9")
    assert cost == Decimal("30") and cur.inserted[1] == "claude-mystery-9"  # claude-opus-4-8: 5 + 25


def test_record_usage_dated_alias_priced_by_base():
    cur = Cur()
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0)
    assert record_usage(cur, feature="licitaciones", step="read", model="claude-haiku-5-5", usage=usage,
                        bid_preparation_id=None, served_model="claude-sonnet-5-5-20261001") == Decimal("2")


def test_with_cap_lock_takes_advisory_xact_lock():
    cur = Cur()
    with_cap_lock(cur, "licitaciones")
    assert "pg_advisory_xact_lock" in cur.sql[0]


def test_month_window_rejects_naive_datetime():
    with pytest.raises(ValueError):
        month_window(datetime(2026, 10, 19))
