"""The one writer of a won case: `negotiating` → `won`, against a sent, current quote revision.

Two callers, one implementation:

* **A person** — `POST /v2/commands/record-case-won` (`case_command_routes.py`), the
  dashboard's «Marcar ganada». The operator comes from the verified identity, the request
  names the revision as `quote_id` + `revision_no`, and every event is the operator's.
* **The email → cases rules** — R5, a purchase order on the thread (`mail_rules_repository.py`).
  The step names the revision by `quote_revision_id`, may carry the purchase-order file name,
  and runs under an identity marked `acts_as_system`, so its events are the machine's.

Who acted is therefore never decided here: it is the identity the caller passes, and
`CommandTransaction._append_event` turns it into `actor_kind`. What a win *is* — which stage it
follows, which revision may be named, the three columns that move together — is decided only
here, so a person and a rule cannot record two different kinds of win.

This module reads `crm.quote` and `crm.quote_revision` to check the named revision and writes
neither. The only row it writes is `crm.opportunity` (plus its two events), on the handler's
transaction and under the handler's receipt.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.case_commands import RECORD_CASE_WON, WON_FROM_STAGE
from origenlab_api.v2.commands import CommandRefused, as_uuid
from origenlab_api.v2.identity import OperatorIdentity

_REVISION_BY_ID = """
    select r.id::text as id, r.quote_id::text as quote_id, r.revision_no, r.status,
           r.superseded_by_revision_no, q.opportunity_id::text as opportunity_id, q.quote_number
      from crm.quote_revision r join crm.quote q on q.id = r.quote_id
     where r.id = %s
"""

_REVISION_BY_NUMBER = """
    select r.id::text as id, r.quote_id::text as quote_id, r.revision_no, r.status,
           r.superseded_by_revision_no, q.opportunity_id::text as opportunity_id, q.quote_number
      from crm.quote_revision r join crm.quote q on q.id = r.quote_id
     where r.quote_id = %s and r.revision_no = %s
"""


def _named_revision(repo: Any, cur: Any, fields: dict[str, Any]) -> dict[str, Any] | None:
    """The revision the caller named: by its id (a rule), or by quote and number (a person)."""
    if fields.get("quote_revision_id"):
        cur.execute(_REVISION_BY_ID, (as_uuid(fields["quote_revision_id"], "quote_revision_id"),))
    else:
        cur.execute(
            _REVISION_BY_NUMBER,
            (as_uuid(fields["quote_id"], "quote_id"), int(fields["revision_no"])),
        )
    return repo._row(cur)


def record_case_won(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """`negotiating` → `won`, against one sent, current revision of a quote on this case.

    `repo` is the `V2CaseCommandRepository` (or a subclass) whose transaction this runs on:
    the case is read through its `_live_case`, so the same compare-and-set, the same «this case
    changed since you saw it» refusal and the same closed-case refusal apply to a win as to
    every other case command.

    Refusals, in the order an operator meets them: the case is not at `negotiating`
    (`case_not_negotiating`, 409); the revision is not on this case (`quote_revision_not_on_case`,
    404); it is not `sent`, or a later revision replaced it (`quote_revision_not_current`, 409).
    """
    case = repo._live_case(
        cur, as_uuid(fields["opportunity_id"], "opportunity_id"), int(fields["opportunity_version"])
    )
    if case["stage"] != WON_FROM_STAGE:
        raise CommandRefused(
            409,
            "case_not_negotiating",
            f"a case is won from '{WON_FROM_STAGE}'; this one is at '{case['stage']}'",
        )
    rev = _named_revision(repo, cur, fields)
    if rev is None or rev["opportunity_id"] != case["id"]:
        raise CommandRefused(404, "quote_revision_not_on_case", "that quote revision is not on this case")
    if rev["status"] != "sent" or rev["superseded_by_revision_no"] is not None:
        raise CommandRefused(409, "quote_revision_not_current", "only a sent, current revision is won")
    cur.execute(
        """
        update crm.opportunity
           set stage = 'won', won_quote_id = %s, won_revision_no = %s, closed_at = now(),
               version = version + 1, updated_at = now()
         where id = %s and version = %s
        """,
        (rev["quote_id"], rev["revision_no"], case["id"], int(case["version"])),
    )
    if cur.rowcount != 1:
        raise CommandRefused(409, "case_version_conflict", "this case changed while the command ran")
    cur.execute("set constraints all immediate")
    po = fields.get("purchase_order")
    events = [
        repo._append_event(
            cur, aggregate_kind="opportunity", aggregate_id=case["id"], event_type="opportunity.staged",
            payload={"from_stage": WON_FROM_STAGE, "to_stage": "won", "note": fields["note"]},
            operator=operator, receipt_id=receipt_id,
        ),
        repo._append_event(
            cur, aggregate_kind="opportunity", aggregate_id=case["id"], event_type="opportunity.closed",
            payload={"stage": "won", "won_quote_id": rev["quote_id"],
                     "won_revision_no": int(rev["revision_no"]),
                     "quote_number": rev["quote_number"], "purchase_order": po,
                     "note": fields["note"]},
            operator=operator, receipt_id=receipt_id,
        ),
    ]
    return {
        "command": RECORD_CASE_WON,
        "opportunity_id": case["id"],
        "opportunity_version": int(case["version"]) + 1,
        "stage": "won",
        "previous_stage": WON_FROM_STAGE,
        "closed": True,
        "won_quote_id": rev["quote_id"],
        "won_revision_no": int(rev["revision_no"]),
        "quote_number": rev["quote_number"],
        "created": [],
        "event_ids": events,
    }
