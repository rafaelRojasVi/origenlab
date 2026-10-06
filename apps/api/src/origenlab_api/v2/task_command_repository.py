"""The transactional half of the task commands (`task_commands.py`, `WORKFLOWS.md` W11).

The same `CommandTransaction` as every other V2 command: one transaction, one receipt per
`(operator, Idempotency-Key)`, one `crm.domain_event` per change, and a refusal rolls back all
three.

What it writes is `crm.task` and its `task.*` events — nothing else. The case a task belongs to
is read (it must exist and be open) and never written: a task does not move a stage and does not
spend the version an operator was shown, so creating a pause on a case does not make a colleague
who has the same case open re-read it.

A task changes `where version = %s`, and only while it is `open`: completing or cancelling one
twice is refused by name rather than reported as success.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.task_commands import CANCEL_TASK, COMPLETE_TASK, CREATE_TASK


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


class V2TaskCommandRepository(CommandTransaction):
    """`create_task`, `complete_task`, `cancel_task` — each in one transaction."""

    def _create_task(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        cur.execute(
            "select id::text as id, stage, closed_at from crm.opportunity where id = %s for share",
            (fields["opportunity_id"],),
        )
        case = self._row(cur)
        if case is None:
            raise CommandRefused(404, "case_not_found", "no such commercial case")
        if case["closed_at"] is not None:
            raise CommandRefused(
                409,
                "case_is_closed",
                f"this case ended at stage '{case['stage']}'; a closed case has nothing to follow up",
            )
        cur.execute(
            """
            insert into crm.task
                (opportunity_id, owner_operator_id, title, due_at, created_by_operator_id)
            values (%s, %s, %s, %s, %s)
            returning id::text as id, version, due_at
            """,
            (case["id"], operator.operator_id, fields["title"], fields["due_at"], operator.operator_id),
        )
        task = self._row(cur)
        assert task is not None  # noqa: S101 - `returning` on a successful insert
        due_at = _iso(task["due_at"])
        event = self._append_event(
            cur,
            aggregate_kind="task",
            aggregate_id=task["id"],
            event_type="task.created",
            payload={
                "opportunity_id": case["id"],
                "title": fields["title"],
                "due_at": due_at,
                "owner_operator_id": operator.operator_id,
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "command": CREATE_TASK,
            "task_id": task["id"],
            "task_version": int(task["version"]),
            "opportunity_id": case["id"],
            "status": "open",
            "due_at": due_at,
            "event_ids": [event],
        }

    def _close_task(
        self,
        cur: Any,
        operator: OperatorIdentity,
        fields: dict[str, Any],
        receipt_id: str,
        *,
        command: str,
        status: str,
    ) -> dict[str, Any]:
        cur.execute(
            """
            select id::text as id, opportunity_id::text as opportunity_id, status, version
              from crm.task
             where id = %s
               for update
            """,
            (fields["task_id"],),
        )
        task = self._row(cur)
        if task is None:
            raise CommandRefused(404, "task_not_found", "no such task")
        if int(task["version"]) != fields["task_version"]:
            raise CommandRefused(
                409,
                "task_version_conflict",
                f"this task changed since it was shown to you (you saw version "
                f"{fields['task_version']}, it is at {task['version']}); re-read it",
            )
        if task["status"] != "open":
            raise CommandRefused(
                409, "task_is_not_open", f"this task is already '{task['status']}'"
            )
        cur.execute(
            """
            update crm.task
               set status = %s,
                   completed_at = case when %s = 'done' then now() else null end,
                   cancel_reason = case when %s = 'cancelled' then %s else null end,
                   version = version + 1,
                   updated_at = now()
             where id = %s and version = %s
            """,
            (status, status, status, fields["note"], task["id"], int(task["version"])),
        )
        if cur.rowcount != 1:
            raise CommandRefused(409, "task_version_conflict", "this task changed while the command ran")
        event = self._append_event(
            cur,
            aggregate_kind="task",
            aggregate_id=task["id"],
            event_type="task.completed" if status == "done" else "task.cancelled",
            payload={"opportunity_id": task["opportunity_id"], "note": fields["note"]},
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "command": command,
            "task_id": task["id"],
            "task_version": int(task["version"]) + 1,
            "opportunity_id": task["opportunity_id"],
            "status": status,
            "event_ids": [event],
        }

    def _complete_task(self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                       receipt_id: str) -> dict[str, Any]:
        return self._close_task(cur, operator, fields, receipt_id, command=COMPLETE_TASK, status="done")

    def _cancel_task(self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                     receipt_id: str) -> dict[str, Any]:
        return self._close_task(cur, operator, fields, receipt_id, command=CANCEL_TASK, status="cancelled")

    _HANDLERS = {
        CREATE_TASK: _create_task,
        COMPLETE_TASK: _complete_task,
        CANCEL_TASK: _cancel_task,
    }
