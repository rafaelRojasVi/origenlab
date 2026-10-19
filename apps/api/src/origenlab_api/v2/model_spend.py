"""Model spend for the «Licitaciones» feature: prices, cost of a call, and the monthly hard cap.

Spec §6.4: every model call is recorded in `platform.model_usage` (append-only); before each call
the worst-case cost of that call is added to the month's spend and, if the sum would pass the cap,
the call is refused (`BudgetReached`). A call that lands exactly on the cap is allowed.

Money is `Decimal` throughout, quantised to six places (the column is numeric(12, 6)). A model
with no price is refused (`UnpricedModel`) rather than guessed.

Server-side fallbacks (claude-api skill, «Billing»): a declined attempt is billed at its own
model's rates and the fallback attempt at the fallback's, and the top-level `usage` covers only the
attempt that produced the message. So `worst_case_usd` adds every attempt the chain may make
(`fallback_models`), and `record_usage` records each entry of `usage.iterations` at that entry's
model, falling back to the top-level usage (priced at `served_model`) only when there are none.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

FEATURE_LICITACIONES = "licitaciones"
MONTHLY_CAP_USD: dict[str, Decimal] = {FEATURE_LICITACIONES: Decimal("60")}
SANTIAGO = ZoneInfo("America/Santiago")
_PLACES = Decimal("0.000001")
_MTOK = Decimal(1_000_000)


class Price(NamedTuple):
    """US$ per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


def _p(i: str, o: str, r: str, w: str) -> Price:
    return Price(Decimal(i), Decimal(o), Decimal(r), Decimal(w))


PRICES_PER_MTOK: dict[str, Price] = {
    "claude-opus-5-5": _p("4", "20", "0.20", "5"),
    "claude-sonnet-5-5": _p("2", "10", "0.20", "2.5"),
    "claude-haiku-5-5": _p("0.10", "0.50", "0.01", "0.125"),
    # The permitted server-side fallback targets (claude-api skill; `fallbacks: "default"` sends a
    # cyber-category decline to Opus 4.8): the tender reader's cap check prices them (ruling R15).
    "claude-opus-4-8": _p("5", "25", "0.50", "6.25"),
    # The other permitted fallback target (claude-api skill: Claude Opus 5 at Opus 4.8's pricing).
    "claude-opus-5": _p("5", "25", "0.50", "6.25"),
}


class UnpricedModel(ValueError):
    """The model has no entry in `PRICES_PER_MTOK`."""


class BudgetReached(Exception):
    def __init__(self, spent: Decimal, cap: Decimal) -> None:
        super().__init__(f"monthly model budget reached: spent {spent} of {cap}")
        self.spent = spent
        self.cap = cap


def _resolve(model: str) -> str | None:
    """The priced model for `model`: exact, or a dated alias (`claude-opus-5-5-20261001`) by prefix."""
    if model in PRICES_PER_MTOK:
        return model
    matches = [k for k in PRICES_PER_MTOK if model.startswith(k + "-")]
    return max(matches, key=len) if matches else None


def _price(model: str) -> Price:
    base = _resolve(model)
    if base is None:
        raise UnpricedModel(f"no price for model {model!r}")
    return PRICES_PER_MTOK[base]


def _dearest() -> Price:
    ps = PRICES_PER_MTOK.values()
    return Price(*(max(getattr(p, f) for p in ps) for f in Price._fields))


def _q(value: Decimal) -> Decimal:
    return value.quantize(_PLACES, rounding=ROUND_HALF_UP)


def cost_usd(model: str, *, input_tokens: int, output_tokens: int,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0, _price_override: Price | None = None) -> Decimal:
    p = _price_override or _price(model)
    total = (Decimal(input_tokens) * p.input + Decimal(output_tokens) * p.output
             + Decimal(cache_read_tokens) * p.cache_read + Decimal(cache_write_tokens) * p.cache_write) / _MTOK
    return _q(total)


def worst_case_usd(model: str, *, input_tokens_estimate: int, max_tokens: int,
                   fallback_models: tuple[str, ...] = ()) -> Decimal:
    """Every input token at the dearer of the input and cache-write rates plus every output token
    allowed, for `model` and for each fallback attempt after it, summed: each attempt of a fallback
    chain may be billed. Raises `UnpricedModel` (before any paid call)."""
    total = Decimal(0)
    for m in (model, *fallback_models):
        p = _price(m)
        rate = Price(max(p.input, p.cache_write), p.output, p.cache_read, p.cache_write)
        total += cost_usd(m, input_tokens=input_tokens_estimate, output_tokens=max_tokens, _price_override=rate)
    return _q(total)


def worst_usage(model: str, *, input_tokens_estimate: int, max_tokens: int) -> Any:
    """A usage object whose cost is `worst_case_usd` for one attempt on `model` (no fallback): every
    input token at the dearer of the input and cache-write rates, every output token allowed. For
    recording a call that was sent but whose real usage is unknown."""
    p = _price(model)
    as_write = p.cache_write > p.input
    return SimpleNamespace(input_tokens=0 if as_write else input_tokens_estimate, output_tokens=max_tokens,
                           cache_read_input_tokens=0,
                           cache_creation_input_tokens=input_tokens_estimate if as_write else 0, model=model)


def dearest_model(models: tuple[str, ...]) -> str:
    """The model of `models` whose worst case per token is highest (for a conservative record)."""
    return max(models, key=lambda m: (_price(m).output, max(_price(m).input, _price(m).cache_write)))


def month_window(now: datetime) -> tuple[datetime, datetime]:
    """The calendar month (America/Santiago) containing `now`, as [start, end) in Santiago time."""
    if now.tzinfo is None:
        raise ValueError("month_window needs a timezone-aware datetime")
    local = now.astimezone(SANTIAGO)
    start = datetime(local.year, local.month, 1, tzinfo=SANTIAGO)
    end = (datetime(local.year + 1, 1, 1, tzinfo=SANTIAGO) if local.month == 12
           else datetime(local.year, local.month + 1, 1, tzinfo=SANTIAGO))
    return start, end


def month_spend(cur: Any, feature: str, now: datetime) -> Decimal:
    start, end = month_window(now)
    cur.execute("select coalesce(sum(usd_cost), 0) from platform.model_usage "
                "where feature = %s and created_at >= %s and created_at < %s", (feature, start, end))
    return _q(Decimal(cur.fetchone()[0]))


def with_cap_lock(cur: Any, feature: str) -> None:
    """Take a transaction-scoped advisory lock on `feature` for a SHORT check section.

    `ensure_within_cap` is check-then-act, and this lock guards only the check itself. Never hold a
    database transaction across a model API call. The documented pattern is:
      1. short transaction: `with_cap_lock` + `ensure_within_cap`, then commit;
      2. the model call, with no transaction open;
      3. short transaction: `record_usage`.
    The real guarantee that the cap holds is that the tender worker is single-flight (one
    preparation at a time, in its own child process): no second spender can run between 1 and 3.
    On an autocommit connection the lock is released at once and protects nothing.
    """
    cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"model_spend:{feature}",))


def ensure_within_cap(cur: Any, feature: str, now: datetime, next_call_worst_usd: Decimal) -> None:
    cap = MONTHLY_CAP_USD[feature]
    spent = month_spend(cur, feature, now)
    if spent + next_call_worst_usd > cap:
        raise BudgetReached(spent, cap)


def record_usage(cur: Any, *, feature: str, step: str, model: str, usage: Any,
                 bid_preparation_id: Any, served_model: str | None = None) -> Decimal:
    """Insert the `platform.model_usage` rows of one call and return their total cost.

    With `usage.iterations` (server-side fallbacks, tool loops): one row per attempt, priced at the
    attempt's own model (an entry without one is the requested `model`). Without: one row from the
    top-level usage, priced at `served_model` (the response's `model`) when given, else `model`.
    """
    iterations = getattr(usage, "iterations", None)
    if isinstance(iterations, list | tuple) and iterations:
        total = Decimal(0)
        for entry in iterations:
            entry_model = getattr(entry, "model", None)
            total += _record_one(cur, feature=feature, step=step,
                                 model=entry_model if isinstance(entry_model, str) and entry_model else model,
                                 usage=entry, bid_preparation_id=bid_preparation_id)
        return _q(total)
    return _record_one(cur, feature=feature, step=step, model=served_model or model, usage=usage,
                       bid_preparation_id=bid_preparation_id)


def _record_one(cur: Any, *, feature: str, step: str, model: str, usage: Any, bid_preparation_id: Any) -> Decimal:
    tokens = {
        "input_tokens": getattr(usage, "input_tokens", None) or 0,
        "output_tokens": getattr(usage, "output_tokens", None) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", None) or 0,
    }
    # The call is already paid: never raise here. An unknown model is priced at the dearest known rate.
    if _resolve(model) is None:
        _log.error("model_spend: unpriced served model %r; recorded at the dearest known price", model)
        cost = cost_usd(model, _price_override=_dearest(), **tokens)
    else:
        cost = cost_usd(model, **tokens)
    cur.execute(
        "insert into platform.model_usage (feature, model, step, bid_preparation_id, input_tokens, "
        "output_tokens, cache_read_tokens, cache_write_tokens, usd_cost) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (feature, model, step, bid_preparation_id, tokens["input_tokens"], tokens["output_tokens"],
         tokens["cache_read_tokens"], tokens["cache_write_tokens"], cost))
    return cost


def spend_summary(cur: Any, feature: str, now: datetime) -> dict[str, Any]:
    spent = month_spend(cur, feature, now)
    cap = MONTHLY_CAP_USD[feature]
    start, _ = month_window(now)
    return {"spent_usd": str(spent), "cap_usd": str(cap), "month": f"{start.year:04d}-{start.month:02d}",
            "reached": spent >= cap}
