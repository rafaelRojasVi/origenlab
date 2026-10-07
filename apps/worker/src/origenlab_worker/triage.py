"""Mail triage of one captured message, end to end: read its `.eml` from Storage, run the rules,
look up product candidates in the catalog, ask the model only when the rules say a person wrote
something that matters, and record the reading as `evidence.assertion` proposals.

Writes, all `on conflict do nothing`, in one transaction per message:

* one `message_triage` assertion — `value_norm = triage:v<TRIAGE_VERSION>`, the whole reading in
  `value` (class, reasons, signals, candidates, the model's answer or why it did not run);
* one `product_mention` assertion per product named — by the model when it ran, otherwise by an
  exact model-number match in the catalog.

The worker never writes `crm.*` or `outbound.*` directly. For a bounce it also records a versioned
`delivery_failure` assertion and only a conservative Batch-A single-recipient no-such-user result
may call the closed-list contact-control writer. Ordinary already-triaged mail is not re-read; old
bounce readings are backfilled independently.

The body is read for this one call and never stored. What is stored are short machine reasons,
the catalog ids, and the model's own summary and product descriptions.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from origenlab_api.v2.mail_rules import fold
from origenlab_worker.bounce import BounceAnalysis, DELIVERY_FAILURE_VALUE_NORM, analyze_bounce
from origenlab_worker.catalog_match import ProductCandidate, match_products, model_key
from origenlab_worker.drive_filing import storage_key
from origenlab_worker.mail_text import MailText, parse_mail
from origenlab_worker.storage import StorageError
from origenlab_worker.triage_model import (
    LinkedCase,
    ModelReader,
    ModelReading,
    ModelRefused,
    build_request,
    parse_reading,
)
from origenlab_worker.triage_rules import TRIAGE_VERSION, RuleVerdict, TriageInput, classify

VALUE_NORM = f"triage:v{TRIAGE_VERSION}"
#: The periodic sweep looks this far back; `triage-once --since-days` reaches further on purpose.
DEFAULT_SINCE_DAYS = 14

OUTCOMES: tuple[str, ...] = ("recorded", "already_triaged", "not_found", "no_eml", "storage_error",
                             "parse_error")


class EmlStore(Protocol):
    def get(self, key: str) -> bytes: ...


@dataclass(frozen=True)
class PendingMessage:
    source_record_id: str
    message_id: str
    direction: str
    labels: tuple[str, ...]
    eml_storage_path: str | None
    thread_id: str | None = None


@dataclass(frozen=True)
class ModelSettings:
    reader: ModelReader | None  # None: the model stage is off; the rules still run
    model: str


@dataclass
class TriageResult:
    outcome: str
    triage_class: str | None = None
    model: str | None = None  # 'ran' | 'off' | 'not_needed' | a ModelRefused code | 'api_error'
    mentions: int = 0


_PENDING = """
select sr.id::text
  from comms.message m
  join evidence.source_record sr on sr.dedupe_key = 'gmail_message:' || m.provider_message_id
 where sr.kind = 'gmail_message'
   and m.parse_status = 'parsed'
   and m.eml_storage_path is not null
   and m.internal_date >= now() - make_interval(days => %s)
   and (
     not exists (select 1 from evidence.assertion a
                  where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s)
     or (
       exists (select 1 from evidence.assertion a
                where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s
                  and a.value->>'class' = 'bounce')
       and not exists (select 1 from evidence.assertion d
                        where d.source_record_id = sr.id and d.kind = 'delivery_failure'
                          and d.value_norm = %s)
     )
   )
 order by m.internal_date desc
 limit %s
"""
_ONE = """
select sr.id::text, m.id::text, m.direction, m.labels, m.eml_storage_path, m.provider_thread_id,
       exists (select 1 from evidence.assertion a
                where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s),
       (select a.value->>'class' from evidence.assertion a
         where a.source_record_id = sr.id and a.kind = 'message_triage' and a.value_norm = %s
         limit 1),
       exists (select 1 from evidence.assertion d
                where d.source_record_id = sr.id and d.kind = 'delivery_failure'
                  and d.value_norm = %s)
  from evidence.source_record sr
  join comms.message m on 'gmail_message:' || m.provider_message_id = sr.dedupe_key
 where sr.id = %s and sr.kind = 'gmail_message'
"""
_LINKED_CASES = """
select distinct o.id::text, o.title, o.stage
  from crm.opportunity_evidence oe
  join evidence.source_record sr on sr.id = oe.source_record_id
  join crm.opportunity o on o.id = oe.opportunity_id
 where oe.unlinked_at is null and sr.payload ->> 'gmail_thread_id' = %s
 order by 1
 limit 5
"""
_INSERT_ASSERTION = """
insert into evidence.assertion (source_record_id, kind, value_norm, value)
values (%s, %s, %s, %s)
on conflict (source_record_id, kind, value_norm) do nothing
"""


class TriageDb:
    """The triage's reads and writes on one verified `origenlab_worker` connection."""

    def __init__(self, conn: Any, statement_timeout_ms: int = 30_000) -> None:
        self._conn = conn
        self._timeout = int(statement_timeout_ms)

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(f"set local statement_timeout = {self._timeout}")
            yield cur

    def pending(self, *, since_days: int, limit: int) -> list[str]:
        with self._tx() as cur:
            cur.execute(_PENDING, (since_days, VALUE_NORM, VALUE_NORM, DELIVERY_FAILURE_VALUE_NORM, limit))
            return [row[0] for row in cur.fetchall()]

    def load(self, source_record_id: str) -> tuple[PendingMessage, bool, str | None, bool] | None:
        with self._tx() as cur:
            cur.execute(_ONE, (VALUE_NORM, VALUE_NORM, DELIVERY_FAILURE_VALUE_NORM, source_record_id))
            row = cur.fetchone()
        if row is None:
            return None
        sr, mid, direction, labels, path, thread, done, triage_class, delivery_done = row
        return (PendingMessage(sr, mid, direction, tuple(labels or ()), path, thread), bool(done),
                triage_class, bool(delivery_done))

    def linked_cases(self, thread_id: str | None) -> list[LinkedCase]:
        """The open and closed cases any email of this Gmail thread is linked to — the API's own
        thread rule (R1, `mail_rules_repository._LINKS_SQL`), read-only."""
        if not thread_id:
            return []
        with self._tx() as cur:
            cur.execute(_LINKED_CASES, (thread_id,))
            return [LinkedCase(oid, title, stage) for oid, title, stage in cur.fetchall()]

    def candidates(self, mail: MailText) -> list[ProductCandidate]:
        with self._tx() as cur:
            return match_products(cur, mail.subject, mail.body)

    def record(self, source_record_id: str, reading: dict[str, Any],
               mentions: Sequence[tuple[str, dict[str, Any]]]) -> int:
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(_INSERT_ASSERTION, (source_record_id, "message_triage", VALUE_NORM, Jsonb(reading)))
            for norm, value in mentions:
                cur.execute(_INSERT_ASSERTION, (source_record_id, "product_mention", norm, Jsonb(value)))
            return len(mentions)

    def record_delivery_failure(self, source_record_id: str, analysis: BounceAnalysis) -> int:
        """Record the versioned NDR observation and apply only its exact Batch-A hard block.

        The assertion and privileged call share one transaction. If the database boundary refuses
        the proposed block, the assertion rolls back too and the sweep retries instead of silently
        recording a half-applied safety decision.
        """
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(
                _INSERT_ASSERTION,
                (source_record_id, "delivery_failure", DELIVERY_FAILURE_VALUE_NORM, Jsonb(analysis.as_json())),
            )
            for address in analysis.auto_block_addresses:
                cur.execute(
                    """select outbound.add_contact_control(
                           'block', 'all', %s, 'invalid_address', null, null, %s
                       )""",
                    (
                        address,
                        Jsonb({
                            "source_record_id": source_record_id,
                            "triage_value_norm": VALUE_NORM,
                            "delivery_value_norm": DELIVERY_FAILURE_VALUE_NORM,
                            "reason_code": analysis.reason_code,
                        }),
                    ),
                )
                cur.fetchone()
        return len(analysis.auto_block_addresses)


def _candidate_json(c: ProductCandidate) -> dict[str, Any]:
    return {"product_id": c.product_id, "model_number": c.model_number, "model_key": c.model_key,
            "how": c.how, "evidence": c.evidence}


def mentions_for(reading: ModelReading | None, candidates: Sequence[ProductCandidate]) -> list[tuple[str, dict[str, Any]]]:
    """`(value_norm, value)` for each product the message names, deduplicated by value_norm."""
    out: dict[str, dict[str, Any]] = {}
    base = {"triage_version": TRIAGE_VERSION}
    if reading is not None:
        by_id = {c.product_id: c for c in candidates}
        for p in reading.products:
            matched = by_id.get(p.catalog_product_id or "")
            norm = (matched.model_key if matched and matched.model_key
                    else model_key(p.model) if p.model else fold(p.description))[:200]
            if norm:
                out.setdefault(norm, {**base, "source": "model", "description": p.description, "brand": p.brand,
                                      "model": p.model, "quantity": p.quantity,
                                      "catalog_product_id": p.catalog_product_id})
    else:
        for c in candidates:
            if c.how == "model_key" and c.model_key:
                out.setdefault(c.model_key, {**base, "source": "catalog_model_key", "description": c.name,
                                             "model": c.model_number, "quantity": None,
                                             "catalog_product_id": c.product_id})
    return list(out.items())


def reading_value(verdict: RuleVerdict, mail: MailText, candidates: Sequence[ProductCandidate],
                  model_state: str, reading: ModelReading | None, elapsed_ms: int,
                  cases: Sequence[LinkedCase] = ()) -> dict[str, Any]:
    value: dict[str, Any] = {
        "triage_version": TRIAGE_VERSION,
        "class": verdict.triage_class,
        "needs_model": verdict.needs_model,
        "reasons": list(verdict.reasons),
        "signals": verdict.signals,
        "quote_numbers": list(verdict.quote_numbers),
        "purchase_order_numbers": list(verdict.purchase_order_numbers),
        "sender_domain": verdict.sender_domain,
        "free_mail": verdict.free_mail,
        "attachment_count": len(mail.attachment_names),
        "body_chars": len(mail.body),
        "candidates": [_candidate_json(c) for c in candidates],
        "linked_cases": [{"opportunity_id": c.opportunity_id, "stage": c.stage} for c in cases],
        "model_state": model_state,
        "elapsed_ms": elapsed_ms,
    }
    if reading is not None:
        value["reading"] = reading.as_json()
    return value


def triage_one(db: TriageDb, store: EmlStore, source_record_id: str, settings: ModelSettings, *,
               clock: Callable[[], float] = time.monotonic) -> TriageResult:
    started = clock()
    loaded = db.load(source_record_id)
    if loaded is None:
        return TriageResult("not_found")
    message, done, existing_class, delivery_done = loaded
    if done and not (existing_class == "bounce" and not delivery_done):
        return TriageResult("already_triaged")
    if not message.eml_storage_path:
        return TriageResult("no_eml")
    try:
        raw = store.get(storage_key(message.eml_storage_path))
    except StorageError:
        return TriageResult("storage_error")
    try:
        mail = parse_mail(raw)
    except Exception:  # noqa: BLE001 - a malformed message is recorded as such by the capture; skip it
        return TriageResult("parse_error")

    # Backfill a delivery analysis for a bounce that was already triaged before this slice. Ordinary
    # messages never re-run, so enabling automatic bounce blocking does not re-spend model calls.
    if done:
        analysis = analyze_bounce(raw)
        db.record_delivery_failure(message.source_record_id, analysis)
        return TriageResult("recorded", "bounce", "not_needed", 0)

    verdict = classify(TriageInput(direction=message.direction, labels=message.labels, mail=mail))
    candidates = db.candidates(mail) if verdict.needs_model or verdict.signals.get("quote_word") else []
    cases = db.linked_cases(message.thread_id) if verdict.needs_model else []

    reading: ModelReading | None = None
    if not verdict.needs_model:
        state = "not_needed"
    elif settings.reader is None:
        state = "off"
    else:
        request = build_request(mail, verdict, candidates, model=settings.model, cases=cases)
        try:
            answer = settings.reader(request)
        except Exception as exc:  # noqa: BLE001 - reported by class and status only, never message text
            if not is_permanent_api_error(exc):
                raise ModelUnavailable(type(exc).__name__) from None
            answer, state = None, f"api_error_{getattr(exc, 'status_code', 0)}"
        if answer is not None:
            try:
                reading = parse_reading(answer, model=settings.model, candidates=candidates, cases=cases)
                state = "ran"
            except ModelRefused as exc:
                state = str(exc)

    mentions = mentions_for(reading, candidates)
    value = reading_value(verdict, mail, candidates, state, reading, int((clock() - started) * 1000), cases)
    written = db.record(message.source_record_id, value, mentions)
    if verdict.triage_class == "bounce":
        db.record_delivery_failure(message.source_record_id, analyze_bounce(raw))
    return TriageResult("recorded", verdict.triage_class, state, written)


class ModelUnavailable(RuntimeError):
    """The model call failed (network, rate limit, 5xx): nothing was recorded; the job retries."""


def is_permanent_api_error(exc: BaseException) -> bool:
    """A 4xx the same request will always get again (bad request, auth, not found): recorded as the
    reading's `model_state` so the sweep does not re-enqueue it forever. 408, 409 and 429 are
    transient, like 5xx and connection errors, and retried by the queue."""
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status not in (408, 409, 429)


@dataclass
class SweepCounts:
    found: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    classes: dict[str, int] = field(default_factory=dict)
    model: dict[str, int] = field(default_factory=dict)

    def add(self, result: TriageResult) -> None:
        self.outcomes[result.outcome] = self.outcomes.get(result.outcome, 0) + 1
        if result.triage_class:
            self.classes[result.triage_class] = self.classes.get(result.triage_class, 0) + 1
        if result.model:
            self.model[result.model] = self.model.get(result.model, 0) + 1


__all__ = ["DEFAULT_SINCE_DAYS", "OUTCOMES", "VALUE_NORM", "ModelSettings", "ModelUnavailable", "is_permanent_api_error", "PendingMessage",
           "SweepCounts", "TriageDb", "TriageResult", "mentions_for", "reading_value", "triage_one"]
