"""The task command boundary — `POST /v2/commands/{create,complete,cancel}-task` (W11).

| Route | Records |
|---|---|
| `POST /v2/commands/create-task` | a follow-up on an open case, owned by the operator, due on a date |
| `POST /v2/commands/complete-task` | it was done |
| `POST /v2/commands/cancel-task` | it will not be done, and why |

Mounted with the case commands, behind the same `ORIGENLAB_V2_COMMANDS_ENABLED` switch, with the
same four requirements: an active `sales` or `admin` operator from the verified identity, an
`Idempotency-Key`, the version the operator was shown (of the task; a new task names only its
case) and a non-blank `note`. The dashboard reaches all three through the proxy's
`CASE_COMMAND_POST_PATHS`: «En pausa hasta…» creates a task, «Retomar ahora» cancels it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.task_command_repository import V2TaskCommandRepository
from origenlab_api.v2.task_commands import (
    CANCEL_TASK,
    COMPLETE_TASK,
    CREATE_TASK,
    CancelTaskBody,
    CompleteTaskBody,
    CreateTaskBody,
    validated_task,
)

task_command_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])


def get_task_command_repository(request: Request) -> V2TaskCommandRepository:
    repo = getattr(request.app.state, "v2_task_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo


def _run(*, command_name: str, body: Any, repo: V2TaskCommandRepository,
         operator: OperatorIdentity, idempotency_key: str | None) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated_task(command_name, body)
        digest = request_digest(command_name, body)
        return repo.execute(command_name=command_name, operator=operator, fields=fields,
                            idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@task_command_router.post("/create-task")
def create_task(
    body: CreateTaskBody,
    operator: Deciding,
    repo: V2TaskCommandRepository = Depends(get_task_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """A follow-up on an open case, due at `due_at`. The case itself does not change."""
    return _run(command_name=CREATE_TASK, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@task_command_router.post("/complete-task")
def complete_task(
    body: CompleteTaskBody,
    operator: Deciding,
    repo: V2TaskCommandRepository = Depends(get_task_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """The open task was done (`done`, `completed_at` set)."""
    return _run(command_name=COMPLETE_TASK, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@task_command_router.post("/cancel-task")
def cancel_task(
    body: CancelTaskBody,
    operator: Deciding,
    repo: V2TaskCommandRepository = Depends(get_task_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """The open task will not be done; `note` is the reason (`cancel_reason`)."""
    return _run(command_name=CANCEL_TASK, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)
