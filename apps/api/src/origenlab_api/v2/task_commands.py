"""The three task commands of `WORKFLOWS.md` W11 — a follow-up with a date, on one case.

| Command | What durable fact it records |
|---|---|
| `create_task` | a named operator will do this on this case by this date |
| `complete_task` | it was done |
| `cancel_task` | it will not be done, and why |

A task is `crm.task` (`DOMAIN.md` §7 #9): one case, one owner, one due date. It **never changes
the case** — not its stage, not its version — which is why these commands live apart from the
commercial-case commands, whose module promises never to touch `crm.task`. The dashboard's
«En pausa hasta…» is a `create_task` («Retomar …», due on the day the case comes back), and
«Retomar ahora» is a `cancel_task`: nothing about a pause is stored anywhere but the task.

Every body carries a non-blank `note` (`DecisionBody`), and `extra="forbid"` refuses a field a
caller guessed. A task is decided against the version the operator was shown, as a case is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, field_validator

from origenlab_api.v2.commands import DecisionBody, as_uuid

CREATE_TASK = "create_task"
COMPLETE_TASK = "complete_task"
CANCEL_TASK = "cancel_task"

TASK_COMMAND_NAMES: tuple[str, ...] = (CREATE_TASK, COMPLETE_TASK, CANCEL_TASK)


class CreateTaskBody(DecisionBody):
    """A follow-up on one open case, owned by the operator who writes it, due at `due_at`.

    `due_at` must carry a time zone: «el lunes» in Santiago and in UTC are different mornings,
    and a task that comes due on the wrong one is a missed follow-up.
    """

    opportunity_id: str
    title: Annotated[str, Field(min_length=1, max_length=200)]
    due_at: datetime

    @field_validator("title")
    @classmethod
    def _title_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("due_at")
    @classmethod
    def _due_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("due_at must carry a time zone")
        return value


class _TaskBody(DecisionBody):
    """Which task, and the version of it the operator was shown."""

    task_id: str
    task_version: Annotated[int, Field(ge=1)]


class CompleteTaskBody(_TaskBody):
    """The task was done. `note` says what was done."""


class CancelTaskBody(_TaskBody):
    """The task will not be done. `note` is the reason, stored as `cancel_reason`."""


TASK_BODY_BY_COMMAND: dict[str, type[DecisionBody]] = {
    CREATE_TASK: CreateTaskBody,
    COMPLETE_TASK: CompleteTaskBody,
    CANCEL_TASK: CancelTaskBody,
}


def validated_task(command_name: str, body: DecisionBody) -> dict[str, Any]:
    """Everything that needs no database, checked in one place."""
    fields: dict[str, Any] = {"note": body.note.strip()}
    if isinstance(body, CreateTaskBody):
        fields["opportunity_id"] = as_uuid(body.opportunity_id, "opportunity_id")
        fields["title"] = body.title.strip()
        fields["due_at"] = body.due_at
        return fields
    assert isinstance(body, _TaskBody)  # noqa: S101 - every other command is about a task
    fields["task_id"] = as_uuid(body.task_id, "task_id")
    fields["task_version"] = int(body.task_version)
    return fields
