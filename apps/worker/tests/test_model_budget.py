"""The mail worker's model spend: a monthly cap per feature, checked before each call, and the call's
usage recorded before its answer is parsed. No network; the reader is a fake. Every figure is invented."""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from dbhelp import owner_rows
from origenlab_worker import model_budget
from origenlab_worker.database import local_test_target, open_verified_connection
from origenlab_worker.model_budget import (
    BudgetReached,
    ConnectionSpend,
    input_tokens_estimate,
    monthly_cap,
    paid_call,
    request_worst_usd,
)
from v2_command_harness import build_disposable_database, needs_worker_db, worker_dsn

REQUEST = {"model": "claude-haiku-5-5", "max_tokens": 1000, "system": "x" * 2000,
           "messages": [{"role": "user", "content": "hola"}]}


def _answer(text: str = "{}") -> SimpleNamespace:
    return SimpleNamespace(model="claude-haiku-5-5", usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
                           content=[SimpleNamespace(type="text", text=text)])


class Spend:
    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.log: list[tuple] = []

    def spend_check(self, feature, worst_usd):
        self.log.append(("check", feature, worst_usd))
        if self.refuse:
            raise BudgetReached(Decimal("60"), Decimal("60"))

    def spend_record(self, feature, step, model, message):
        self.log.append(("record", feature, step, model))


@pytest.mark.parametrize("feature,env,default", [
    ("triage", "ORIGENLAB_WORKER_TRIAGE_CAP_USD", "60"),
    ("case_pick", "ORIGENLAB_WORKER_CASE_PICK_CAP_USD", "20"),
    ("subject_memory", "ORIGENLAB_WORKER_SUBJECT_MEMORY_CAP_USD", "10"),
    ("quote_read", "ORIGENLAB_WORKER_QUOTE_READ_CAP_USD", "20"),
])
def test_each_feature_has_a_default_cap_its_env_can_change(feature, env, default) -> None:
    assert monthly_cap(feature, {}) == Decimal(default)
    assert monthly_cap(feature, {env: "12.5"}) == Decimal("12.5")
    assert monthly_cap(feature, {env: "0"}) == Decimal("0")  # zero switches the feature's spend off
    for bad in ("-1", "lots", "NaN", "inf"):
        assert monthly_cap(feature, {env: bad}) == Decimal(default)


def test_a_pdf_is_estimated_by_document_not_by_its_base64_length() -> None:
    pdf = {"model": "claude-opus-5-5", "max_tokens": 4000, "messages": [{"role": "user", "content": [
        {"type": "document", "source": {"type": "base64", "data": "A" * 4_000_000}},
        {"type": "text", "text": "Transcribe this quotation."}]}]}
    assert input_tokens_estimate(pdf) < 25_000
    assert input_tokens_estimate(REQUEST) >= 1000 + 1000
    assert request_worst_usd({**REQUEST, "fallbacks": "default"}) > request_worst_usd(REQUEST)


def test_the_cap_is_checked_before_the_call_and_usage_recorded_before_parsing() -> None:
    spend = Spend()
    calls = []

    def reader(request):
        calls.append(request)
        spend.log.append(("call",))
        return _answer("not json at all")

    message = paid_call(spend, "triage", "read", reader, REQUEST)
    assert [e[0] for e in spend.log] == ["check", "call", "record"]
    assert spend.log[-1] == ("record", "triage", "read", "claude-haiku-5-5")
    assert message.content[0].text == "not json at all"  # parsing is the caller's, after the record


def test_over_the_cap_the_model_is_never_called() -> None:
    def reader(request):
        raise AssertionError("no call over the cap")

    with pytest.raises(BudgetReached):
        paid_call(Spend(refuse=True), "triage", "read", reader, REQUEST)
    assert model_budget.metered(None, Spend(), "triage") is None


def test_the_worker_builds_anthropic_clients_without_sdk_retries(monkeypatch) -> None:
    """The queue retries; SDK retries multiplied every queue attempt by three."""
    built = []
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: built.append(kw) or SimpleNamespace(beta=None)
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    from origenlab_worker.triage_model import anthropic_reader

    anthropic_reader("k")
    assert built[-1]["max_retries"] == 0


# --- PostgreSQL, as the real origenlab_worker login ------------------------------------------------
# Needs migration 20261122100000 (the API/DB team's): it widens platform.model_usage's feature check
# to 'triage', 'case_pick', 'subject_memory' and 'quote_read'.

@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


@needs_worker_db
@pytest.mark.parametrize("feature", ["triage", "case_pick", "subject_memory", "quote_read"])
def test_a_paid_call_is_recorded_and_counts_against_its_own_cap(dsn, feature) -> None:
    env = {model_budget.CAP_ENV[feature]: "0.05"}
    with open_verified_connection(local_test_target(worker_dsn(dsn)), application_name="test-model-budget",
                                  privileges=(("platform.model_usage", "INSERT"),), policies=frozenset()) as conn:
        spend = ConnectionSpend(conn, env=env, now=lambda: datetime.now(timezone.utc))
        paid_call(spend, feature, "read", lambda r: _answer(), REQUEST)
        rows = owner_rows(dsn, "select model, step, input_tokens, output_tokens from platform.model_usage "
                               "where feature = %s", (feature,))
        assert rows == [("claude-haiku-5-5", "read", 1000, 200)]
        # Spent so far + the next call's worst case passes the 5-cent cap: refused before the call.
        spend_big = ConnectionSpend(conn, env={model_budget.CAP_ENV[feature]: "0.0002"})
        with pytest.raises(BudgetReached):
            paid_call(spend_big, feature, "read", lambda r: pytest.fail("called over the cap"), REQUEST)
