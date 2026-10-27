"""The transactional half of the todo commands (`commands.py`, «Pendientes» on Hoy).

The same `CommandTransaction` as every other V2 command: one transaction, one receipt per
`(operator, Idempotency-Key)`, one `crm.domain_event` per change, and a refusal rolls back all
three.

What it writes is `crm.todo` and its `todo.*` events — nothing else. A linked case is read (it
must exist) and never written, and it may be in any stage: a line can be about a won or lost case.

Order is a fractional `position` inside a group (urgent / not urgent); smaller is higher. A new or
regrouped line takes `min(position) - 1` among the open lines of its group, a moved line the
midpoint between its new neighbours, so a move normally rewrites one row. Done lines do not count
towards a group's order, and a reopened line keeps the position it had — so two open lines can tie
(tick the top line, add one, «Deshacer»). Ties read newest first — `(position, created_at desc,
id)`, the same order here, in the read and in the renumber — and when a move's neighbours tie or the
midpoint collapses onto one of them (Decimal bisection runs out of digits after ~90 moves into one
gap), the group's open lines are renumbered 1..n in that order, in the same transaction, before the
move is placed. Renumbering rewrites only `position`: no version, no event, so nobody else's line
goes stale. It first locks the group's open lines in id order, then ranks them in a fresh statement:
a move of another line committed meanwhile is ranked where it now is, not overwritten from a stale
snapshot, and two renumbers of one group queue instead of deadlocking.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.todos.commands import (
    COMPLETE_TODO,
    CREATE_TODO,
    DELETE_TODO,
    MOVE_TODO,
    REOPEN_TODO,
    UPDATE_TODO,
)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


class V2TodoCommandRepository(CommandTransaction):
    """`create_todo` … `delete_todo` — each in one transaction."""

    # ------------------------------------------------------------------ helpers

    def _top_position(self, cur: Any, *, is_urgent: bool, excluding: str | None = None) -> Decimal:
        """The position that puts a line above every open line of the group (0 for an empty one)."""
        cur.execute(
            """
            select min(position) as lowest
              from crm.todo
             where status = 'open' and is_urgent = %s and (%s::uuid is null or id <> %s::uuid)
            """,
            (is_urgent, excluding, excluding),
        )
        lowest = self._row(cur)["lowest"]
        return Decimal(0) if lowest is None else Decimal(lowest) - 1

    def _renumber_open(self, cur: Any, *, is_urgent: bool) -> None:
        """Rewrite the group's open positions to 1..n in read order.

        Lock first, rank after. Under READ COMMITTED a single `update ... from (ranked)` takes its
        snapshot before it waits on a row another transaction is moving; once that commits, the
        row is re-checked but the ranking is not, so the committed move would be overwritten by a
        stale rank (its receipt, version and event already recorded). Locking the group's open
        lines (in id order, so two renumbers cannot deadlock) makes the ranking statement start
        after every concurrent writer of the group has finished.
        """
        cur.execute(
            "select id from crm.todo where status = 'open' and is_urgent = %s order by id for update",
            (is_urgent,),
        )
        cur.execute(
            """
            update crm.todo t
               set position = ranked.n
              from (select id, row_number() over (order by position, created_at desc, id) as n
                      from crm.todo
                     where status = 'open' and is_urgent = %s) ranked
             where t.id = ranked.id and t.status = 'open' and t.is_urgent = %s and t.position <> ranked.n
            """,
            (is_urgent, is_urgent),
        )

    def _slot_after(self, cur: Any, todo: dict[str, Any], after_id: str) -> tuple[Decimal, Decimal | None]:
        """The anchor's position and the next open line's (in read order, skipping the moved one)."""
        cur.execute(
            """
            select a.position as anchor,
                   (select n.position
                      from crm.todo n
                     where n.status = 'open' and n.is_urgent = a.is_urgent and n.id <> %s
                       and (n.position > a.position
                            or (n.position = a.position
                                and (n.created_at < a.created_at
                                     or (n.created_at = a.created_at and n.id > a.id))))
                     order by n.position, n.created_at desc, n.id
                     limit 1) as following
              from crm.todo a
             where a.id = %s
            """,
            (todo["id"], after_id),
        )
        row = self._row(cur)
        following = row["following"]
        return Decimal(row["anchor"]), None if following is None else Decimal(following)

    def _require_case(self, cur: Any, opportunity_id: str | None) -> None:
        if opportunity_id is None:
            return
        cur.execute("select 1 from crm.opportunity where id = %s for key share", (opportunity_id,))
        if cur.fetchone() is None:
            raise CommandRefused(404, "case_not_found", "no such commercial case")

    def _lock_line(self, cur: Any, fields: dict[str, Any]) -> dict[str, Any]:
        """The line, locked, at the version the operator saw, and not deleted."""
        cur.execute(
            """
            select id::text as id, body, is_urgent, position, due_on, opportunity_id::text as opportunity_id,
                   status, version
              from crm.todo
             where id = %s
               for update
            """,
            (fields["todo_id"],),
        )
        todo = self._row(cur)
        if todo is None:
            raise CommandRefused(404, "todo_not_found", "no such to-do")
        if int(todo["version"]) != fields["todo_version"]:
            raise CommandRefused(
                409,
                "todo_version_conflict",
                f"this to-do changed since it was shown to you (you saw version "
                f"{fields['todo_version']}, it is at {todo['version']}); re-read it",
            )
        if todo["status"] == "deleted":
            raise CommandRefused(409, "todo_is_deleted", "this to-do was deleted")
        return todo

    def _change(
        self,
        cur: Any,
        operator: OperatorIdentity,
        receipt_id: str,
        todo: dict[str, Any],
        *,
        command: str,
        event_type: str,
        assignments: dict[str, Any],
        payload: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        """Apply `assignments` at the locked version, bump it, and write the event."""
        columns = ", ".join(f"{name} = %s" for name in assignments)
        cur.execute(
            f"update crm.todo set {columns}, version = version + 1, updated_at = now() "  # noqa: S608 - names are literals
            "where id = %s and version = %s",
            (*assignments.values(), todo["id"], int(todo["version"])),
        )
        if cur.rowcount != 1:
            raise CommandRefused(409, "todo_version_conflict", "this to-do changed while the command ran")
        event = self._append_event(
            cur,
            aggregate_kind="todo",
            aggregate_id=todo["id"],
            event_type=event_type,
            payload=payload,
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "command": command,
            "todo_id": todo["id"],
            "todo_version": int(todo["version"]) + 1,
            "status": status,
            "event_ids": [event],
        }

    # ------------------------------------------------------------------ commands

    def _create_todo(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        self._require_case(cur, fields["opportunity_id"])
        position = self._top_position(cur, is_urgent=fields["is_urgent"])
        cur.execute(
            """
            insert into crm.todo
                (body, is_urgent, position, due_on, opportunity_id, created_by_operator_id)
            values (%s, %s, %s, %s, %s, %s)
            returning id::text as id, version
            """,
            (fields["body"], fields["is_urgent"], position, fields["due_on"], fields["opportunity_id"],
             operator.operator_id),
        )
        todo = self._row(cur)
        assert todo is not None  # noqa: S101 - `returning` on a successful insert
        event = self._append_event(
            cur,
            aggregate_kind="todo",
            aggregate_id=todo["id"],
            event_type="todo.created",
            payload={
                "body": fields["body"],
                "is_urgent": fields["is_urgent"],
                "due_on": _iso(fields["due_on"]),
                "opportunity_id": fields["opportunity_id"],
                "position": str(position),
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "command": CREATE_TODO,
            "todo_id": todo["id"],
            "todo_version": int(todo["version"]),
            "status": "open",
            "event_ids": [event],
        }

    def _update_todo(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        todo = self._lock_line(cur, fields)
        self._require_case(cur, fields["opportunity_id"])
        assignments: dict[str, Any] = {
            "body": fields["body"],
            "is_urgent": fields["is_urgent"],
            "due_on": fields["due_on"],
            "opportunity_id": fields["opportunity_id"],
        }
        changed = {
            name: _iso(value)
            for name, value in assignments.items()
            if value != todo[name]
        }
        if fields["is_urgent"] != todo["is_urgent"]:
            # It lands at the top of the group it moves into, not at its old place in the other.
            assignments["position"] = self._top_position(cur, is_urgent=fields["is_urgent"])
            changed["position"] = str(assignments["position"])
        return self._change(
            cur, operator, receipt_id, todo,
            command=UPDATE_TODO, event_type="todo.updated", assignments=assignments,
            payload={"changed": changed, "note": fields["note"]}, status=todo["status"],
        )

    def _move_todo(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        todo = self._lock_line(cur, fields)
        if todo["status"] != "open":
            raise CommandRefused(409, "todo_is_not_open", f"this to-do is already '{todo['status']}'")
        after_id = fields["after_todo_id"]
        if after_id is None:
            position = self._top_position(cur, is_urgent=todo["is_urgent"], excluding=todo["id"])
        else:
            cur.execute(
                "select position, is_urgent, status from crm.todo where id = %s",
                (after_id,),
            )
            after = self._row(cur)
            if (
                after_id == todo["id"]
                or after is None
                or after["status"] != "open"
                or after["is_urgent"] != todo["is_urgent"]
            ):
                raise CommandRefused(
                    409,
                    "todo_move_target_invalid",
                    "a to-do can only go after another open to-do of its own group",
                )
            anchor, following = self._slot_after(cur, todo, after_id)
            if following is not None and (following == anchor or (anchor + following) / 2 in (anchor, following)):
                # Tied neighbours, or no digits left between them: renumber the group, then place.
                self._renumber_open(cur, is_urgent=todo["is_urgent"])
                anchor, following = self._slot_after(cur, todo, after_id)
            position = anchor + 1 if following is None else (anchor + following) / 2
        return self._change(
            cur, operator, receipt_id, todo,
            command=MOVE_TODO, event_type="todo.moved", assignments={"position": position},
            payload={"after_todo_id": after_id, "position": str(position), "note": fields["note"]},
            status="open",
        )

    def _complete_todo(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        todo = self._lock_line(cur, fields)
        if todo["status"] != "open":
            raise CommandRefused(409, "todo_is_not_open", f"this to-do is already '{todo['status']}'")
        return self._change(
            cur, operator, receipt_id, todo,
            command=COMPLETE_TODO, event_type="todo.completed",
            assignments={"status": "done", "completed_at": self._now(cur)},
            payload={"note": fields["note"]}, status="done",
        )

    def _reopen_todo(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        todo = self._lock_line(cur, fields)
        if todo["status"] != "done":
            raise CommandRefused(409, "todo_is_not_done", f"this to-do is '{todo['status']}', not done")
        result = self._change(
            cur, operator, receipt_id, todo,
            command=REOPEN_TODO, event_type="todo.reopened",
            assignments={"status": "open", "completed_at": None},
            payload={"note": fields["note"]}, status="open",
        )
        cur.execute(
            "select 1 from crm.todo where status = 'open' and is_urgent = %s and position = %s and id <> %s",
            (todo["is_urgent"], todo["position"], todo["id"]),
        )
        if cur.fetchone() is not None:
            # Its old place was taken while it was done: untie the group (read order is unchanged).
            self._renumber_open(cur, is_urgent=todo["is_urgent"])
        return result

    def _delete_todo(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        todo = self._lock_line(cur, fields)
        return self._change(
            cur, operator, receipt_id, todo,
            command=DELETE_TODO, event_type="todo.deleted", assignments={"status": "deleted", "completed_at": None},
            payload={"previous_status": todo["status"], "note": fields["note"]}, status="deleted",
        )

    def _now(self, cur: Any) -> Any:
        cur.execute("select now() as now")
        return self._row(cur)["now"]

    _HANDLERS = {
        CREATE_TODO: _create_todo,
        UPDATE_TODO: _update_todo,
        MOVE_TODO: _move_todo,
        COMPLETE_TODO: _complete_todo,
        REOPEN_TODO: _reopen_todo,
        DELETE_TODO: _delete_todo,
    }
