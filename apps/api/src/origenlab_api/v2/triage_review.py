"""Mail triage review — a person approves, corrects or rejects what the triage suggested.

The worker's mail triage (`apps/worker` triage.py; ARCHITECTURE.md §8, D2) records one
`evidence.assertion` of kind `message_triage` per email it read, as a suggestion: the class, the
products, the case stage, a summary. Nothing acts on it. This module is the human half:

| Surface | What it does |
|---|---|
| `GET /v2/workspace/triage-readings?status=pending` | the suggestions waiting for a person, newest first, each with its email, its products, the cases its thread is on and their current stage, and the latest verdict if any |
| `POST /v2/commands/review-triage` | records one verdict — `approved`, `corrected` (with the corrected fields) or `rejected`, an optional note — as an `evidence.triage_review` row and one `assertion.triage_reviewed` event |

**Append-only.** A verdict is never rewritten: a second review of the same suggestion adds a row,
and the latest is the current one. The full history is what the triage is measured against
(`apps/worker/scripts/triage_eval.py --reviews`).

**Moving the case is not this command.** «Aprobar» on a stage suggestion is this command followed,
in the dashboard, by the existing `advance-case-stage` steps (`stagePath`), each with its own
receipt and event — the same path as «Cambiar estado». A verdict alone moves nothing.

The vocabularies below are the triage's own; `apps/worker/tests/test_triage_vocabulary.py` fails
if the worker's and these ever differ.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.case_commands import CASE_STAGES
from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused, as_uuid
from origenlab_api.v2.identity import OperatorIdentity

REVIEW_TRIAGE = "review_triage"
VERDICTS: tuple[str, ...] = ("approved", "corrected", "rejected")
TRIAGE_CLASSES: tuple[str, ...] = (
    "outbound", "bounce", "auto_reply", "calendar", "unsubscribe", "bulk", "notification", "empty",
    "tender_notice", "purchase_order", "lost", "quote_followup", "quote_request", "business_other",
)
TRIAGE_STAGES: tuple[str, ...] = (*CASE_STAGES, "not_a_case", "unclear")
TRIAGE_INTENTS: tuple[str, ...] = (
    "quote_request", "purchase_order", "quote_followup", "negotiation", "lost", "technical_question",
    "service_or_repair", "supplier_offer", "logistics", "administrative", "not_commercial",
)
LIST_STATUSES: tuple[str, ...] = ("pending", "reviewed", "all")
MAX_LIST = 200


class CorrectedProduct(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: Annotated[str, Field(min_length=1, max_length=300)]
    model: Annotated[str | None, Field(max_length=120)] = None
    quantity: Annotated[int | None, Field(gt=0, lt=1_000_000)] = None
    catalog_product_id: str | None = None


class TriageCorrection(BaseModel):
    """What the person says the reading should have been. Only the fields they changed."""

    model_config = ConfigDict(extra="forbid")

    triage_class: Literal[TRIAGE_CLASSES] | None = Field(default=None, alias="class")  # type: ignore[valid-type]
    stage: Literal[TRIAGE_STAGES] | None = None  # type: ignore[valid-type]
    intent: Literal[TRIAGE_INTENTS] | None = None  # type: ignore[valid-type]
    products: Annotated[list[CorrectedProduct] | None, Field(max_length=20)] = None

    def as_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class ReviewTriageBody(BaseModel):
    """One verdict on one suggestion. `corrected` only with `corrected`; `note` optional."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    assertion_id: str
    verdict: Literal["approved", "corrected", "rejected"]
    corrected: TriageCorrection | None = None
    note: Annotated[str | None, Field(max_length=2000)] = None

    @field_validator("note")
    @classmethod
    def _blank_note_is_none(cls, value: str | None) -> str | None:
        return value if value and value.strip() else None


def validated_review(body: ReviewTriageBody) -> dict[str, Any]:
    corrected = body.corrected.as_json() if body.corrected else {}
    if body.verdict == "corrected" and not corrected:
        raise CommandRefused(422, "correction_missing", "a correction must say what the right answer is")
    if body.verdict != "corrected" and corrected:
        raise CommandRefused(422, "correction_not_allowed", "only a correction carries corrected fields")
    for product in corrected.get("products") or []:
        if product.get("catalog_product_id"):
            product["catalog_product_id"] = as_uuid(product["catalog_product_id"], "catalog_product_id")
    return {"assertion_id": as_uuid(body.assertion_id, "assertion_id"), "verdict": body.verdict,
            "corrected": corrected, "note": body.note}


_LIST_SQL = """
with readings as (
  select a.id, a.source_record_id, a.value_norm, a.value, a.created_at,
         (select json_build_object('verdict', r.verdict, 'corrected', r.corrected, 'note', r.note,
                                   'reviewed_at', r.reviewed_at, 'reviewed_by', o.display_name)
            from evidence.triage_review r join platform.operator o on o.id = r.reviewed_by_operator_id
           where r.assertion_id = a.id order by r.reviewed_at desc, r.id desc limit 1) as review
    from evidence.assertion a
   where a.kind = 'message_triage'
     and coalesce((a.value ->> 'needs_model')::boolean, false)
)
select r.id::text as assertion_id, r.source_record_id::text as source_record_id, r.value_norm as version,
       r.value, r.created_at, r.review,
       m.subject, m.internal_date as sent_at, m.direction, m.provider_thread_id as thread_id,
       snd.address_norm as sender,
       -- The sender's domain belongs to a registered supplier or manufacturer.
       exists (select 1 from crm.organization_domain d join crm.organization o on o.id = d.organization_id
                where d.removed_at is null and d.domain_norm = split_part(snd.address_norm, '@', 2)
                  and (o.kind in ('supplier', 'manufacturer')
                       or exists (select 1 from crm.organization_relationship rel
                                   where rel.organization_id = o.id and rel.role in ('supplier', 'manufacturer')
                                     and (rel.valid_to is null or rel.valid_to > current_date)))) as sender_is_supplier,
       coalesce((select json_agg(json_build_object('opportunity_id', o.id, 'title', o.title, 'stage', o.stage,
                                                   'version', o.version, 'closed_at', o.closed_at,
                                                   'close_reason', o.close_reason) order by o.id)
                   from (select distinct o.id, o.title, o.stage, o.version, o.closed_at, o.close_reason
                           from crm.opportunity_evidence oe
                           join evidence.source_record s2 on s2.id = oe.source_record_id
                           join crm.opportunity o on o.id = oe.opportunity_id
                          where oe.unlinked_at is null
                            and s2.payload ->> 'gmail_thread_id' = m.provider_thread_id) o), '[]') as cases
  from readings r
  join evidence.source_record sr on sr.id = r.source_record_id
  join comms.message m on 'gmail_message:' || m.provider_message_id = sr.dedupe_key
  left join lateral (select p.address_norm from comms.message_participant p
                      where p.message_id = m.id and p.role = 'from' limit 1) snd on true
 where (%(status)s = 'all' or (%(status)s = 'pending') = (r.review is null))
 order by m.internal_date desc, r.id
 limit %(limit)s
"""


class TriageReviewRepository(CommandTransaction):
    """The read queue and the `review_triage` command, on one database as `origenlab_api`."""

    @contextmanager
    def _read(self) -> Iterator[Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    def readings(self, *, status: str = "pending", limit: int = 100) -> dict[str, Any]:
        if status not in LIST_STATUSES:
            raise CommandRefused(422, "status_invalid", f"status is one of {', '.join(LIST_STATUSES)}")
        with self._read() as cur:
            cur.execute(_LIST_SQL, {"status": status, "limit": max(1, min(int(limit), MAX_LIST))})
            names = [d[0] for d in cur.description]
            rows = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
        items = []
        for row in rows:
            value = row["value"] or {}
            reading = value.get("reading") or {}
            items.append({
                "assertion_id": row["assertion_id"],
                "source_record_id": row["source_record_id"],
                "version": row["version"],
                "subject": row["subject"],
                "sender": row["sender"],
                "sender_is_supplier": bool(row["sender_is_supplier"]),
                "sent_at": row["sent_at"].isoformat() if row["sent_at"] else None,
                "thread_id": row["thread_id"],
                "class": value.get("class"),
                "reasons": value.get("reasons") or [],
                "model_state": value.get("model_state"),
                "stage": reading.get("stage"),
                "intent": reading.get("intent"),
                "urgency": reading.get("urgency"),
                "summary_es": reading.get("summary_es"),
                "needs_reply": reading.get("needs_reply"),
                "requester_organization": reading.get("requester_organization"),
                "products": reading.get("products") or [],
                "candidates": value.get("candidates") or [],
                "cases": row["cases"] or [],
                "transitions": reading.get("linked_cases") or [],
                "review": row["review"],
            })
        return {"status": status, "items": items,
                "vocabulary": {"classes": list(TRIAGE_CLASSES), "stages": list(TRIAGE_STAGES),
                               "intents": list(TRIAGE_INTENTS)}}

    def _review_triage(self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                       receipt_id: str) -> dict[str, Any]:
        cur.execute(
            "select id::text as id, kind, source_record_id::text as source_record_id from evidence.assertion "
            "where id = %s for share",
            (fields["assertion_id"],),
        )
        assertion = self._row(cur)
        if assertion is None:
            raise CommandRefused(404, "suggestion_not_found", "no such triage suggestion")
        if assertion["kind"] != "message_triage":
            raise CommandRefused(409, "not_a_triage_suggestion", "only a mail-triage suggestion is reviewed here")
        from psycopg.types.json import Jsonb

        cur.execute(
            """
            insert into evidence.triage_review (assertion_id, verdict, corrected, note, reviewed_by_operator_id)
            values (%s, %s, %s, %s, %s)
            returning id::text as id, reviewed_at
            """,
            (assertion["id"], fields["verdict"], Jsonb(fields["corrected"]), fields["note"], operator.operator_id),
        )
        review = self._row(cur)
        assert review is not None  # noqa: S101 - `returning` on a successful insert
        event = self._append_event(
            cur,
            aggregate_kind="assertion",
            aggregate_id=assertion["id"],
            event_type="assertion.triage_reviewed",
            payload={"review_id": review["id"], "source_record_id": assertion["source_record_id"],
                     "verdict": fields["verdict"], "corrected": fields["corrected"], "note": fields["note"]},
            operator=operator,
            receipt_id=receipt_id,
        )
        return {"command": REVIEW_TRIAGE, "review_id": review["id"], "assertion_id": assertion["id"],
                "verdict": fields["verdict"], "reviewed_at": review["reviewed_at"].isoformat(),
                "event_ids": [event]}

    _HANDLERS = {REVIEW_TRIAGE: _review_triage}


__all__ = ["LIST_STATUSES", "REVIEW_TRIAGE", "TRIAGE_CLASSES", "TRIAGE_INTENTS", "TRIAGE_STAGES", "VERDICTS",
           "ReviewTriageBody", "TriageCorrection", "TriageReviewRepository", "validated_review"]
