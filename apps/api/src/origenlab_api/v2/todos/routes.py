"""«Pendientes» — the shared to-do list on «Hoy».

| Route | Does |
|---|---|
| `GET /v2/workspace/todos?done_days=7` | the open lines in order, and the lines done in the last N days |
| `POST /v2/commands/create-todo` | adds a line |
| `POST /v2/commands/update-todo` | changes its text, urgency, day or case |
| `POST /v2/commands/move-todo` | puts it after another line of its group |
| `POST /v2/commands/complete-todo` | it was done |
| `POST /v2/commands/reopen-todo` | it was not done after all |
| `POST /v2/commands/delete-todo` | removes it from the list |

Any signed-in operator reads; the commands are behind `Deciding` (a `viewer` is 403), an
`Idempotency-Key`, the version of the line the operator was shown and a non-blank `note`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.todos.commands import (
    COMPLETE_TODO,
    CREATE_TODO,
    DELETE_TODO,
    MOVE_TODO,
    REOPEN_TODO,
    UPDATE_TODO,
    CompleteTodoBody,
    CreateTodoBody,
    DeleteTodoBody,
    MoveTodoBody,
    ReopenTodoBody,
    UpdateTodoBody,
    validated_todo,
)
from origenlab_api.v2.todos.reads import TodoReadRepository
from origenlab_api.v2.todos.repository import V2TodoCommandRepository

todo_command_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])
todo_read_router = APIRouter(prefix="/v2/workspace", tags=["v2-todos"], route_class=ContactRedactingRoute)


def get_todo_command_repository(request: Request) -> V2TodoCommandRepository:
    repo = getattr(request.app.state, "v2_todo_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo


def get_todo_read_repository(request: Request) -> TodoReadRepository:
    repo = getattr(request.app.state, "v2_todo_read_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 read boundary is not configured")
    return repo


CommandRepo = Annotated[V2TodoCommandRepository, Depends(get_todo_command_repository)]
ReadRepo = Annotated[TodoReadRepository, Depends(get_todo_read_repository)]


def _run(*, command_name: str, body: Any, repo: V2TodoCommandRepository,
         operator: Any, idempotency_key: str | None) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated_todo(command_name, body)
        digest = request_digest(command_name, body)
        return repo.execute(command_name=command_name, operator=operator, fields=fields,
                            idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@todo_read_router.get("/todos")
def list_todos(_: Operator, repo: ReadRepo,
               done_days: Annotated[int, Query(ge=1, le=31)] = 7) -> dict[str, Any]:
    return repo.list(done_days)


@todo_command_router.post("/create-todo")
def create_todo(body: CreateTodoBody, operator: Deciding, repo: CommandRepo,
                idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Add a line at the top of its group."""
    return _run(command_name=CREATE_TODO, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@todo_command_router.post("/update-todo")
def update_todo(body: UpdateTodoBody, operator: Deciding, repo: CommandRepo,
                idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Change the line's text, urgency, day or case."""
    return _run(command_name=UPDATE_TODO, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@todo_command_router.post("/move-todo")
def move_todo(body: MoveTodoBody, operator: Deciding, repo: CommandRepo,
              idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Put the line right after another open line of its group."""
    return _run(command_name=MOVE_TODO, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@todo_command_router.post("/complete-todo")
def complete_todo(body: CompleteTodoBody, operator: Deciding, repo: CommandRepo,
                  idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """The line was done."""
    return _run(command_name=COMPLETE_TODO, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@todo_command_router.post("/reopen-todo")
def reopen_todo(body: ReopenTodoBody, operator: Deciding, repo: CommandRepo,
                idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """The line was not done after all."""
    return _run(command_name=REOPEN_TODO, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@todo_command_router.post("/delete-todo")
def delete_todo(body: DeleteTodoBody, operator: Deciding, repo: CommandRepo,
                idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Remove the line from the list."""
    return _run(command_name=DELETE_TODO, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)
