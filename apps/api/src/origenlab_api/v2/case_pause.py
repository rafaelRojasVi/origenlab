"""«En pausa hasta…» as a state of the case (owner decision 2026-11-10; DOMAIN.md §7.18).

`set_case_pause` is the one writer of `crm.opportunity.paused_until` / `pause_reason`: a day and a
reason, or both null to resume, with one `opportunity.pause_set` event. It also retires the legacy
representation of a pause (W11): the case's open «Retomar: …» tasks, cancelled here with their own
`task.cancelled` events, so the case never says two different things. That task SQL is why this
lives outside `case_command_repository.py`, like the quote acts and the win.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.case_commands import SET_CASE_PAUSE
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

#: The cancel reason written on a legacy «Retomar: …» task this command retires.
LEGACY_TASK_RETIRED = "La pausa del caso se lleva ahora en el caso (set-case-pause)."


def set_case_pause(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    """Pause an open case until a day for a reason, or resume it, with one `opportunity.pause_set`
    event; any legacy open «Retomar: …» task of the case is cancelled in the same transaction.

    `repo` is the `V2CaseCommandRepository` whose transaction this runs on: the case is read through
    its `_live_case` (compare-and-set on the version, closed-case refusal). Setting exactly the pause
    the case already has is refused: there is no change to record.
    """
    case = repo._live_case(cur, fields["opportunity_id"], fields["opportunity_version"])
    previous = (case["paused_until"], case["pause_reason"])
    until = fields["paused_until"]
    target = (until.isoformat() if until is not None else None, fields["pause_reason"])
    if target == previous:
        raise CommandRefused(
            409,
            "case_pause_unchanged",
            "this case is already paused until that day for that reason" if until else "this case is not paused",
        )
    cur.execute(
        """
        update crm.opportunity
           set paused_until = %s, pause_reason = %s, version = version + 1, updated_at = now()
         where id = %s and version = %s
        """,
        (until, fields["pause_reason"], case["id"], int(case["version"])),
    )
    if cur.rowcount != 1:
        raise CommandRefused(
            409,
            "case_version_conflict",
            "this case changed while the command ran; re-read it and decide again",
        )
    events = [repo._append_event(
        cur,
        aggregate_kind="opportunity",
        aggregate_id=case["id"],
        event_type="opportunity.pause_set",
        payload={
            "from_paused_until": previous[0], "from_reason": previous[1],
            "to_paused_until": target[0], "to_reason": target[1], "note": fields["note"],
        },
        operator=operator,
        receipt_id=receipt_id,
    )]
    # The legacy representation of a pause (W11): the open «Retomar: …» task. Cancelled here so the
    # case never says two different things; its event names this command as the reason.
    cur.execute(
        """
        update crm.task
           set status = 'cancelled', cancel_reason = %s, version = version + 1, updated_at = now()
         where opportunity_id = %s and status = 'open' and title like 'Retomar:%%'
        returning id::text as id, version
        """,
        (LEGACY_TASK_RETIRED, case["id"]),
    )
    for task in cur.fetchall():
        events.append(repo._append_event(
            cur,
            aggregate_kind="task",
            aggregate_id=task[0],
            event_type="task.cancelled",
            payload={"status": "cancelled", "note": fields["note"], "replaced_by": SET_CASE_PAUSE},
            operator=operator,
            receipt_id=receipt_id,
        ))
    return {
        "command": SET_CASE_PAUSE,
        "opportunity_id": case["id"],
        "opportunity_version": int(case["version"]) + 1,
        "paused_until": target[0],
        "pause_reason": target[1],
        "previous_paused_until": previous[0],
        "created": [],
        "event_ids": events,
    }
