"""The six todo commands of «Pendientes» — the shared to-do list on «Hoy».

| Command | What durable fact it records |
|---|---|
| `create_todo` | a line was added to the list (top of its group) |
| `update_todo` | the line's text, urgency, day or case changed |
| `move_todo` | the line was put after another one of the same group |
| `complete_todo` | it was done |
| `reopen_todo` | it was not done after all (keeps its place) |
| `delete_todo` | it was removed (the row stays, `status = 'deleted'`) |

A todo is `crm.todo` (`DOMAIN.md` §7 #68): a line of text, optionally urgent, optionally dated,
optionally about one case. It is not `crm.task` (a case follow-up with a due instant) and it
**never changes the case** it names — not its stage, not its version — so a closed or won case
may be linked too.

Every body carries a non-blank `note` (`DecisionBody`) and `extra="forbid"` refuses a field a
caller guessed. A line is changed against the version the operator was shown.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import Field, field_validator

from origenlab_api.v2.commands import DecisionBody, as_uuid

CREATE_TODO = "create_todo"
UPDATE_TODO = "update_todo"
MOVE_TODO = "move_todo"
COMPLETE_TODO = "complete_todo"
REOPEN_TODO = "reopen_todo"
DELETE_TODO = "delete_todo"

TODO_COMMAND_NAMES: tuple[str, ...] = (
    CREATE_TODO,
    UPDATE_TODO,
    MOVE_TODO,
    COMPLETE_TODO,
    REOPEN_TODO,
    DELETE_TODO,
)


def _body_is_not_whitespace(value: str) -> str:
    if not value.strip():
        raise ValueError("body must not be blank")
    return value


class CreateTodoBody(DecisionBody):
    """A new line, put at the top of its group (urgent or not)."""

    body: Annotated[str, Field(min_length=1, max_length=500)]
    is_urgent: bool = False
    due_on: date | None = None
    opportunity_id: str | None = None

    _body_check = field_validator("body")(_body_is_not_whitespace)


class _TodoBody(DecisionBody):
    """Which line, and the version of it the operator was shown."""

    todo_id: str
    todo_version: Annotated[int, Field(ge=1)]


class UpdateTodoBody(_TodoBody):
    """The line's full editable state: every key is required, and `null` clears `due_on` / the case.

    Flipping `is_urgent` puts the line at the top of the group it moves into.
    """

    body: Annotated[str, Field(min_length=1, max_length=500)]
    is_urgent: bool
    due_on: date | None
    opportunity_id: str | None

    _body_check = field_validator("body")(_body_is_not_whitespace)


class MoveTodoBody(_TodoBody):
    """Put the line right after `after_todo_id` — an open line of the same group. `null` = top."""

    after_todo_id: str | None


class CompleteTodoBody(_TodoBody):
    """The line was done."""


class ReopenTodoBody(_TodoBody):
    """The line was not done after all."""


class DeleteTodoBody(_TodoBody):
    """The line is removed from the list."""


TODO_BODY_BY_COMMAND: dict[str, type[DecisionBody]] = {
    CREATE_TODO: CreateTodoBody,
    UPDATE_TODO: UpdateTodoBody,
    MOVE_TODO: MoveTodoBody,
    COMPLETE_TODO: CompleteTodoBody,
    REOPEN_TODO: ReopenTodoBody,
    DELETE_TODO: DeleteTodoBody,
}


def validated_todo(command_name: str, body: DecisionBody) -> dict[str, Any]:
    """Everything that needs no database, checked in one place."""
    fields: dict[str, Any] = {"note": body.note.strip()}
    if isinstance(body, CreateTodoBody | UpdateTodoBody):
        fields["body"] = body.body.strip()
        fields["is_urgent"] = body.is_urgent
        fields["due_on"] = body.due_on
        fields["opportunity_id"] = (
            as_uuid(body.opportunity_id, "opportunity_id") if body.opportunity_id is not None else None
        )
    if isinstance(body, CreateTodoBody):
        return fields
    assert isinstance(body, _TodoBody)  # noqa: S101 - every other command is about a line
    fields["todo_id"] = as_uuid(body.todo_id, "todo_id")
    fields["todo_version"] = int(body.todo_version)
    if isinstance(body, MoveTodoBody):
        fields["after_todo_id"] = (
            as_uuid(body.after_todo_id, "after_todo_id") if body.after_todo_id is not None else None
        )
    return fields
