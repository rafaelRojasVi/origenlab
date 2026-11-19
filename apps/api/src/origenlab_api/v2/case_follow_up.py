"""The follow-up of a case with a delivered quote: who follows it up (the encargado) and by which
channel («Ventas › Seguimiento»).

`set_case_follow_up` is the one writer of `crm.opportunity.follow_up_operator_id` / `follow_up_channel`:
the full pair, with one `opportunity.follow_up_set` event carrying the new and the previous values.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.case_commands import SET_CASE_FOLLOW_UP
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity


def set_case_follow_up(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Set the encargado and the channel of an open case together, with one event.

    `repo` is the `V2CaseCommandRepository` whose transaction this runs on: the case is read through
    its `_live_case` (compare-and-set on the version, closed-case refusal). The encargado must be a
    row of `platform.operator` or null.
    """
    case = repo._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])
    operator_id, channel = fields["operator_id"], fields["channel"]
    if operator_id is not None:
        cur.execute("select 1 from platform.operator where id = %s", (operator_id,))
        if cur.fetchone() is None:
            raise CommandRefused(422, "follow_up_operator_not_found", "no such operator to follow the case up")
    cur.execute(
        "select follow_up_operator_id::text, follow_up_channel from crm.opportunity where id = %s",
        (case["id"],),
    )
    previous_operator_id, previous_channel = cur.fetchone()
    cur.execute(
        """
        update crm.opportunity
           set follow_up_operator_id = %s, follow_up_channel = %s, version = version + 1, updated_at = now()
         where id = %s and version = %s
        """,
        (operator_id, channel, case["id"], int(case["version"])),
    )
    if cur.rowcount != 1:
        raise CommandRefused(
            409,
            "case_version_conflict",
            "this case changed while the command ran; re-read it and decide again",
        )
    event = repo._append_event(
        cur,
        aggregate_kind="opportunity",
        aggregate_id=case["id"],
        event_type="opportunity.follow_up_set",
        payload={
            "operator_id": operator_id, "channel": channel,
            "previous_operator_id": previous_operator_id, "previous_channel": previous_channel,
        },
        operator=operator,
        receipt_id=receipt_id,
    )
    return {
        "command": SET_CASE_FOLLOW_UP,
        "opportunity_id": case["id"],
        "opportunity_version": int(case["version"]) + 1,
        "operator_id": operator_id,
        "channel": channel,
        "created": [],
        "event_ids": [event],
    }
