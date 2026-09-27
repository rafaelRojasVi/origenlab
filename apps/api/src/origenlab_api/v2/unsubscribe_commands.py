"""W10 unsubscribe — preview and apply a batch of already-fetched «BAJA» replies.

Four operations, deliberately separate:

* **Preview** (:meth:`V2UnsubscribeRepository.preview`) — a read-only transaction. It reads the
  facts the plan needs (which senders OrigenLab knows, which are already under a marketing
  block, which messages are already recorded) and answers :func:`plan_batch` with the batch's
  ``input_sha256``. It writes nothing, not even a receipt.
* **Apply** (``apply-unsubscribe-replies``) — the same `CommandTransaction` as every V2 command:
  one transaction, one receipt per ``Idempotency-Key``. It recomputes the fingerprint and refuses
  a batch that differs from the one previewed (``input_hash_mismatch``), refuses a batch with any
  malformed record, and then calls ``outbound.add_contact_control`` once per applicable record.
  That function — the closed-list privileged writer (docs/ARCHITECTURE.md §6.2), which owns the
  whole unsubscribe transaction — records the reply as evidence and then either the permanent
  marketing suppression (or links the evidence to the one that exists), or, for a sender it
  cannot prove, an unresolved request that holds the address for review; and exactly one
  ``crm.domain_event``. It re-proves the basis the plan claims (a known address, or outbound
  lineage to exactly this recipient) and refuses a false one. This module writes no table itself
  except the receipt.
* **Resolve** (``resolve-unsubscribe-review``) — an operator confirms one held request: the same
  function creates or links the permanent suppression and resolves the request to it.
  Confirming again answers ``already_resolved`` and writes nothing but the receipt.
* **Dismiss** (``dismiss-unsubscribe-review``) — an *admin* rules one held request a false
  positive. It quotes the request's ``review_sha256`` (served by the suppressions read) and a
  non-blank explanation; the same function rejects the request, marks its evidence reviewed and
  records one ``assertion.unsubscribe_review_dismissed`` event with the explanation. Only a
  pending hold can be dismissed: a confirmed request, a request already decided or a stale
  ``review_sha256`` is refused, and no contact control is ever touched. The reply evidence and
  the request stay. Replaying the same ``Idempotency-Key`` returns the stored answer.

All run as ``origenlab_api`` and read or write nothing in Gmail: there is no mail client here.
The apply, resolve and dismiss routes exist only with ``ORIGENLAB_V2_UNSUBSCRIBE_APPLY_ENABLED``
on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Callable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from origenlab_api.v2.command_core import CommandTransaction, json_payload
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.unsubscribe_replies import (
    APPLIED_OUTCOMES,
    APPLY_UNSUBSCRIBE_REPLIES,
    DISMISS_UNSUBSCRIBE_REVIEW,
    MAX_RECORDS,
    RESOLVE_UNSUBSCRIBE_REVIEW,
    UNSUBSCRIBE_POLICY_VERSION,
    ReplyFacts,
    batch_addresses_and_keys,
    input_sha256,
    plan_batch,
)

_SHA256 = r"^[0-9a-f]{64}$"


class PreviewUnsubscribeBody(BaseModel):
    """Already-fetched reply records. Each is validated on its own so a bad one is named, not fatal."""

    model_config = ConfigDict(extra="forbid")

    records: Annotated[list[Any], Field(min_length=1, max_length=MAX_RECORDS)]


class ApplyUnsubscribeBody(PreviewUnsubscribeBody):
    """The records previewed, and the fingerprint the preview answered for them."""

    expected_input_sha256: Annotated[str, Field(pattern=_SHA256)]
    #: The preview's `plan_sha256`: the outcomes the operator confirmed.
    expected_plan_sha256: Annotated[str, Field(pattern=_SHA256)]


class ResolveUnsubscribeReviewBody(BaseModel):
    """Confirm one held request (an unresolved `unsubscribe_request`) as a permanent suppression."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: UUID
    #: The address the operator saw; must be the request's own, or nothing happens.
    expected_address: Annotated[str, Field(min_length=3, max_length=320)]
    note: Annotated[str, Field(min_length=1, max_length=500)]


class DismissUnsubscribeReviewBody(BaseModel):
    """Dismiss one held request as a false positive. Admin only; never a confirmed unsubscribe."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: UUID
    #: The address the admin saw; must be the request's own, or nothing happens.
    expected_address: Annotated[str, Field(min_length=3, max_length=320)]
    #: The request's version as the suppressions read served it; a changed request is refused.
    expected_review_sha256: Annotated[str, Field(pattern=_SHA256)]
    explanation: Annotated[str, Field(min_length=1, max_length=1000)]

    @field_validator("explanation")
    @classmethod
    def _explanation_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("the explanation must not be blank")
        return value


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


class V2UnsubscribeRepository(CommandTransaction):
    """Preview (read-only) and apply (one command) for staged «BAJA» replies."""

    def __init__(self, *args: Any, clock: Callable[[], datetime] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------ facts

    def _facts(self, cur: Any, records: list[Any]) -> ReplyFacts:
        now = self._clock()
        addresses, keys, parents = batch_addresses_and_keys(records, now)
        facts = ReplyFacts(now=now)
        cur.execute(
            """
            select a.addr
              from unnest(%s::text[]) as a(addr)
             where exists (select 1 from outbound.contact_control c
                            where c.scope = 'address' and c.value_norm = a.addr)
                or exists (select 1 from outbound.campaign_recipient r where r.address_norm = a.addr)
                or exists (select 1 from crm.contact_point p where p.kind = 'email' and p.value_norm = a.addr)
            """,
            (addresses,),
        )
        facts.known_addresses = {r[0] for r in cur.fetchall()}
        cur.execute(
            """
            select value_norm, reason from outbound.contact_control
             where scope = 'address' and kind = 'block' and purpose = 'marketing' and value_norm = any(%s::text[])
            """,
            (addresses,),
        )
        facts.marketing_blocked = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            """
            select s.dedupe_key, bool_or(a.resolution = 'unresolved')
              from evidence.source_record s
              left join evidence.assertion a on a.source_record_id = s.id and a.kind = 'unsubscribe_request'
             where s.dedupe_key = any(%s::text[])
             group by s.dedupe_key
            """,
            (keys,),
        )
        for key, pending in cur.fetchall():
            facts.recorded_keys.add(key)
            if pending:
                facts.pending_keys.add(key)
        # Outbound lineage: the recorded outbound messages these replies answer, and to whom each
        # was sent. The same rule `outbound.add_contact_control` re-checks before it suppresses.
        cur.execute(
            """
            select p.parent, mp.address_norm
              from unnest(%s::text[]) as p(parent)
              join comms.message m on m.direction = 'outbound'
                                  and m.rfc822_message_id_norm in (p.parent, '<' || p.parent || '>')
              left join comms.message_participant mp on mp.message_id = m.id and mp.role in ('to', 'cc', 'bcc')
            """,
            (parents,),
        )
        for parent, address in cur.fetchall():
            recipients = facts.outbound_recipients.setdefault(parent, set())
            if address:
                recipients.add(address)
        cur.execute("select address_norm from comms.mailbox")
        facts.own_addresses = {r[0] for r in cur.fetchall() if r[0]}
        return facts

    # ------------------------------------------------------------------ preview

    def preview(self, records: list[Any]) -> dict[str, Any]:
        """The plan, from a read-only transaction. Writes nothing."""
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                cur.execute("set transaction read only")
                try:
                    plan = plan_batch(records, self._facts(cur, records))
                finally:
                    conn.rollback()
        return public_plan(plan)

    # ------------------------------------------------------------------ apply

    def _apply(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        records = f["records"]
        batch = input_sha256(records)
        if batch != f["expected_input_sha256"]:
            raise CommandRefused(
                409, "input_hash_mismatch",
                "these records are not the batch that was previewed (input_sha256 differs); preview again",
            )
        plan = plan_batch(records, self._facts(cur, records))
        if plan["blocked"]:
            raise CommandRefused(
                422, "malformed_records",
                f"{plan['counts']['malformed']} record(s) are malformed; nothing was applied",
            )
        if plan["plan_sha256"] != f["expected_plan_sha256"]:
            raise CommandRefused(
                409, "plan_changed",
                "the outcome of these records changed since the preview (plan_sha256 differs); preview again",
            )
        results: list[dict[str, Any]] = []
        for row, parsed in zip(plan["rows"], plan["_parsed"], strict=True):
            applied: dict[str, Any] | None = None
            if row["outcome"] in APPLIED_OUTCOMES:
                evidence = parsed.evidence(batch, basis=row["basis"], review_reason=row["review_reason"])
                cur.execute(
                    "select outbound.add_contact_control('block', 'marketing', %s, 'unsubscribe', %s, %s, %s::jsonb)",
                    (parsed.address, operator.operator_id, receipt_id, json_payload(evidence)),
                )
                applied = cur.fetchone()[0]
            results.append({
                "index": row["index"],
                "planned": row["outcome"],
                "applied": (applied or {}).get("outcome"),
                "address": row.get("address"),
                "contact_control_id": (applied or {}).get("contact_control_id"),
                "assertion_id": (applied or {}).get("assertion_id"),
                "basis": row.get("basis"),
                "review_reason": row.get("review_reason"),
                "verdict": row.get("verdict"),
            })
        tally = {"added": 0, "evidence_linked": 0, "pending_review": 0, "already_recorded": 0, "already_pending": 0}
        for r in results:
            if r["applied"] in tally:
                tally[r["applied"]] += 1
        return {
            "command": APPLY_UNSUBSCRIBE_REPLIES,
            "input_sha256": batch,
            "plan_sha256": plan["plan_sha256"],
            "grammar_version": plan["grammar_version"],
            "policy_version": plan["policy_version"],
            "records": plan["records"],
            "planned": plan["counts"],
            "applied": tally,
            "not_applied": sum(1 for r in results if r["applied"] is None),
            "rows": results,
            "suppression": {"kind": "block", "purpose": "marketing", "reason": "unsubscribe", "permanent": True},
            "sends_email": False,
            "reads_mailbox": False,
        }

    # ------------------------------------------------------------------ resolve

    def _resolve(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        cur.execute(
            "select value_norm, resolution from evidence.assertion where id = %s and kind = 'unsubscribe_request'",
            (f["assertion_id"],),
        )
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "review_not_found", "no unsubscribe request with that id")
        address, resolution = row
        if address != f["expected_address"].strip().lower():
            raise CommandRefused(409, "review_address_mismatch",
                                 "the request is for another address than the one confirmed; reload the list")
        cur.execute(
            "select outbound.add_contact_control('block', 'marketing', %s, 'unsubscribe', %s, %s, %s::jsonb)",
            (address, operator.operator_id, receipt_id, json_payload({
                "basis": "review_confirmed", "assertion_id": str(f["assertion_id"]), "note": f["note"],
                "policy_version": UNSUBSCRIBE_POLICY_VERSION,
            })),
        )
        applied = cur.fetchone()[0]
        return {
            "command": RESOLVE_UNSUBSCRIBE_REVIEW,
            "assertion_id": str(f["assertion_id"]),
            "address": address,
            "was": resolution,
            "outcome": applied["outcome"],
            "contact_control_id": applied.get("contact_control_id"),
            "suppression": {"kind": "block", "purpose": "marketing", "reason": "unsubscribe", "permanent": True},
            "sends_email": False,
            "reads_mailbox": False,
        }

    # ------------------------------------------------------------------ dismiss

    def _dismiss(self, cur: Any, operator: OperatorIdentity, f: dict[str, Any], receipt_id: str) -> dict[str, Any]:
        if operator.role != "admin":  # the route checks it too; so does the database
            raise CommandRefused(403, "role_may_not_dismiss", "only an admin may dismiss a held unsubscribe")
        cur.execute(
            "select value_norm, resolution from evidence.assertion where id = %s and kind = 'unsubscribe_request'",
            (f["assertion_id"],),
        )
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "review_not_found", "no unsubscribe request with that id")
        address, resolution = row
        if address != f["expected_address"].strip().lower():
            raise CommandRefused(409, "review_address_mismatch",
                                 "the request is for another address than the one dismissed; reload the list")
        if resolution in ("promoted", "linked"):
            raise CommandRefused(409, "review_already_confirmed",
                                 "this request is a confirmed unsubscribe; it can never be dismissed")
        if resolution != "unresolved":
            raise CommandRefused(409, "review_not_pending", f"this request is {resolution}, not pending; reload the list")
        cur.execute(
            "select outbound.add_contact_control('block', 'marketing', %s, 'unsubscribe', %s, %s, %s::jsonb)",
            (address, operator.operator_id, receipt_id, json_payload({
                "basis": "review_dismissed", "assertion_id": str(f["assertion_id"]),
                "review_sha256": f["expected_review_sha256"], "explanation": f["explanation"],
                "policy_version": UNSUBSCRIBE_POLICY_VERSION,
            })),
        )
        applied = cur.fetchone()[0]
        return {
            "command": DISMISS_UNSUBSCRIBE_REVIEW,
            "assertion_id": str(f["assertion_id"]),
            "address": address,
            "was": resolution,
            "outcome": applied["outcome"],
            "review_sha256": applied["review_sha256"],
            "event_id": applied["event_id"],
            "suppression": None,
            "sends_email": False,
            "reads_mailbox": False,
        }

    _HANDLERS = {APPLY_UNSUBSCRIBE_REPLIES: _apply, RESOLVE_UNSUBSCRIBE_REVIEW: _resolve,
                 DISMISS_UNSUBSCRIBE_REVIEW: _dismiss}
