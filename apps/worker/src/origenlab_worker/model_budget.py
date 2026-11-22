"""The monthly spend cap and the usage record for the mail worker's model calls.

Triage, case pick, subject memory and quote reading call the model on every email or PDF. They
had no cap and recorded nothing; a loop or a retry storm could spend without limit and unseen. Each
call now goes through :func:`paid_call`, the same pattern as the tender worker
(`tender/job.py._call_model`) and the prospect scorer (`prospects/fit.py`):

  1. a short transaction: `with_cap_lock`, then the month's spend plus this call's worst case
     against the feature's cap (`BudgetReached` when it would pass it);
  2. the model call, with no transaction open;
  3. right after it returns, before the answer is parsed, a short transaction: `record_usage`.
     A paid answer that fails to parse is still recorded.

Features (`platform.model_usage.feature`, widened by migration 20261122100000): `triage`,
`case_pick`, `subject_memory`, `quote_read`. Steps are the table's existing ones (`read`, `match`).

Caps: US$ per calendar month (America/Santiago), from the environment with these defaults
(docs/OPERATIONS.md, «Model spend caps»). The worker keeps its caps here, not in
`origenlab_api.v2.model_spend.MONTHLY_CAP_USD`, so each can be changed on Render alone; the
spend itself is read with that module's `month_spend`, and refused with its `BudgetReached`.

Triage runs two jobs at once, so the cap check is not single-flight: two calls may both pass a
cap that only one fits. The overshoot is at most one call's worst case — cents.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from origenlab_api.v2.model_spend import (
    BudgetReached,
    UnpricedModel,
    dearest_model,
    month_spend,
    record_usage,
    with_cap_lock,
    worst_case_usd,
)

_log = logging.getLogger(__name__)

FEATURE_TRIAGE = "triage"
FEATURE_CASE_PICK = "case_pick"
FEATURE_SUBJECT_MEMORY = "subject_memory"
FEATURE_QUOTE_READ = "quote_read"

#: Defaults, US$ a month. Triage reads every inbound email with Opus; the others are smaller.
DEFAULT_CAP_USD: dict[str, Decimal] = {
    FEATURE_TRIAGE: Decimal("60"),
    FEATURE_CASE_PICK: Decimal("20"),
    FEATURE_SUBJECT_MEMORY: Decimal("10"),
    FEATURE_QUOTE_READ: Decimal("20"),
}
CAP_ENV: dict[str, str] = {
    FEATURE_TRIAGE: "ORIGENLAB_WORKER_TRIAGE_CAP_USD",
    FEATURE_CASE_PICK: "ORIGENLAB_WORKER_CASE_PICK_CAP_USD",
    FEATURE_SUBJECT_MEMORY: "ORIGENLAB_WORKER_SUBJECT_MEMORY_CAP_USD",
    FEATURE_QUOTE_READ: "ORIGENLAB_WORKER_QUOTE_READ_CAP_USD",
}
STEP_READ = "read"
STEP_MATCH = "match"

#: The server-side fallback targets a request with `fallbacks` may bill (tender/read_model.py).
FALLBACK_TARGETS: tuple[str, ...] = ("claude-opus-4-8", "claude-opus-5")
#: A PDF or image block: billed by page, not by its base64 length. Quote PDFs are one to three pages.
DOCUMENT_TOKENS_ESTIMATE = 20_000
DEFAULT_MAX_TOKENS = 4096


def monthly_cap(feature: str, env: Mapping[str, str] | None = None) -> Decimal:
    """The feature's cap: its environment variable when it is a non-negative number, else the
    default (an unreadable value is logged, never silently widened)."""
    env = os.environ if env is None else env
    raw = (env.get(CAP_ENV[feature]) or "").strip()
    if not raw:
        return DEFAULT_CAP_USD[feature]
    try:
        value = Decimal(raw)
    except InvalidOperation:
        value = Decimal(-1)
    if not value.is_finite() or value < 0:
        _log.error("model_budget: %s is not a non-negative number; using the default", CAP_ENV[feature])
        return DEFAULT_CAP_USD[feature]
    return value


def input_tokens_estimate(request: Mapping[str, Any]) -> int:
    """A generous input count for the cap check: one token per two characters of every text in the
    request (prompt, schema, tools), plus a fixed amount per document or image block."""
    chars = 0
    documents = 0

    def walk(value: Any) -> None:
        nonlocal chars, documents
        if isinstance(value, Mapping):
            if value.get("type") in ("document", "image"):
                documents += 1
                return
            for key, item in value.items():
                if key not in ("model", "max_tokens", "betas", "fallbacks"):
                    walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            chars += len(value)
        elif value is not None and not isinstance(value, bool | int | float):
            chars += len(json.dumps(value, default=str))

    walk(request)
    return chars // 2 + 1000 + documents * DOCUMENT_TOKENS_ESTIMATE


def request_worst_usd(request: Mapping[str, Any]) -> Decimal:
    """What one call may bill: every attempt's worst case (a fallback attempt at the dearest target).
    Raises `UnpricedModel` before any paid call."""
    model = str(request["model"])
    fallbacks = (dearest_model(FALLBACK_TARGETS),) if request.get("fallbacks") else ()
    return worst_case_usd(model, input_tokens_estimate=input_tokens_estimate(request),
                          max_tokens=int(request.get("max_tokens") or DEFAULT_MAX_TOKENS),
                          fallback_models=fallbacks)


class Spend(Protocol):
    """Where the cap is checked and the usage recorded (`TriageDb`, `ConnectionSpend`, test fakes)."""

    def spend_check(self, feature: str, worst_usd: Decimal) -> None: ...

    def spend_record(self, feature: str, step: str, model: str, message: Any) -> None: ...


class ConnectionSpend:
    """:class:`Spend` on one autocommit `origenlab_worker` connection (each step its own transaction)."""

    def __init__(self, conn: Any, *, env: Mapping[str, str] | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._conn = conn
        self._env = env
        self._now = now

    def spend_check(self, feature: str, worst_usd: Decimal) -> None:
        cap = monthly_cap(feature, self._env)
        with self._conn.transaction(), self._conn.cursor() as cur:
            with_cap_lock(cur, feature)
            spent = month_spend(cur, feature, self._now())
        if spent + worst_usd > cap:
            raise BudgetReached(spent, cap)

    def spend_record(self, feature: str, step: str, model: str, message: Any) -> None:
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        served = getattr(message, "model", None)
        with self._conn.transaction(), self._conn.cursor() as cur:
            record_usage(cur, feature=feature, step=step, model=model, usage=usage, bid_preparation_id=None,
                         served_model=served if isinstance(served, str) and served else None)


def paid_call(spend: Spend, feature: str, step: str, reader: Callable[[dict[str, Any]], Any],
              request: dict[str, Any]) -> Any:
    """Cap check, the call, and its usage recorded before anyone parses it. Raises `BudgetReached`
    or `UnpricedModel` before the call; whatever the reader raises, after it."""
    spend.spend_check(feature, request_worst_usd(request))
    message = reader(request)
    spend.spend_record(feature, step, str(request["model"]), message)
    return message


def metered(reader: Callable[[dict[str, Any]], Any] | None, spend: Spend, feature: str,
            step: str = STEP_READ) -> Callable[[dict[str, Any]], Any] | None:
    """`reader`, every call of it through :func:`paid_call`; None stays None (the model is off)."""
    if reader is None:
        return None

    def read(request: dict[str, Any]) -> Any:
        return paid_call(spend, feature, step, reader, request)

    return read


__all__ = ["BudgetReached", "CAP_ENV", "ConnectionSpend", "DEFAULT_CAP_USD", "FEATURE_CASE_PICK",
           "FEATURE_QUOTE_READ", "FEATURE_SUBJECT_MEMORY", "FEATURE_TRIAGE", "STEP_MATCH", "STEP_READ", "Spend",
           "UnpricedModel", "input_tokens_estimate", "metered", "monthly_cap", "paid_call", "request_worst_usd"]
