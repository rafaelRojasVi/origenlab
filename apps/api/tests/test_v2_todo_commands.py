"""The «Pendientes» todo commands, in process: the bodies and `validated_todo` (no database).

The database half is `test_v2_todo_commands_db.py`. Every name and id is fictitious.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.todos.commands import (
    COMPLETE_TODO,
    CREATE_TODO,
    DELETE_TODO,
    MOVE_TODO,
    REOPEN_TODO,
    TODO_BODY_BY_COMMAND,
    TODO_COMMAND_NAMES,
    UPDATE_TODO,
    CompleteTodoBody,
    CreateTodoBody,
    MoveTodoBody,
    UpdateTodoBody,
    validated_todo,
)

TODO_ID = "dddddddd-4444-4444-8444-444444444444"
OTHER_ID = "eeeeeeee-5555-4555-8555-555555555555"
CASE_ID = "aaaaaaaa-1111-4111-8111-111111111111"


def test_every_command_has_a_body() -> None:
    assert set(TODO_BODY_BY_COMMAND) == set(TODO_COMMAND_NAMES)
    assert TODO_COMMAND_NAMES == (CREATE_TODO, UPDATE_TODO, MOVE_TODO, COMPLETE_TODO, REOPEN_TODO, DELETE_TODO)


@pytest.mark.parametrize("text", ["", "   ", "x" * 501])
def test_a_blank_or_oversized_line_is_refused(text: str) -> None:
    with pytest.raises(ValidationError):
        CreateTodoBody(body=text, note="x")
    with pytest.raises(ValidationError):
        UpdateTodoBody(todo_id=TODO_ID, todo_version=1, body=text, is_urgent=False, due_on=None,
                       opportunity_id=None, note="x")


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        CreateTodoBody(body="Llamar", note="x", position=3)
    with pytest.raises(ValidationError):
        CompleteTodoBody(todo_id=TODO_ID, todo_version=1, note="x", status="done")


def test_a_version_below_one_is_refused() -> None:
    with pytest.raises(ValidationError):
        CompleteTodoBody(todo_id=TODO_ID, todo_version=0, note="x")


def test_an_update_names_every_editable_field() -> None:
    with pytest.raises(ValidationError):
        UpdateTodoBody(todo_id=TODO_ID, todo_version=1, body="Llamar", is_urgent=False, note="x")
    with pytest.raises(ValidationError):
        MoveTodoBody(todo_id=TODO_ID, todo_version=1, note="x")


def test_validated_todo_parses_ids_and_strips_text() -> None:
    created = validated_todo(CREATE_TODO, CreateTodoBody(
        body="  Llamar a la clienta  ", opportunity_id=CASE_ID.upper(), note=" ficticio "))
    assert created == {"note": "ficticio", "body": "Llamar a la clienta", "is_urgent": False,
                       "due_on": None, "opportunity_id": CASE_ID}
    moved = validated_todo(MOVE_TODO, MoveTodoBody(
        todo_id=TODO_ID, todo_version=2, after_todo_id=OTHER_ID, note="x"))
    assert moved["todo_id"] == TODO_ID and moved["after_todo_id"] == OTHER_ID and moved["todo_version"] == 2
    top = validated_todo(MOVE_TODO, MoveTodoBody(todo_id=TODO_ID, todo_version=1, after_todo_id=None, note="x"))
    assert top["after_todo_id"] is None


@pytest.mark.parametrize("bad", ["nope", ""])
def test_a_malformed_id_is_refused_by_name(bad: str) -> None:
    with pytest.raises(CommandRefused) as exc:
        validated_todo(COMPLETE_TODO, CompleteTodoBody(todo_id=bad, todo_version=1, note="x"))
    assert exc.value.code == "malformed_identifier"
    with pytest.raises(CommandRefused) as exc:
        validated_todo(CREATE_TODO, CreateTodoBody(body="Llamar", opportunity_id=bad, note="x"))
    assert exc.value.code == "malformed_identifier"


# ---------------------------------------------------------------- route surface

from fastapi.testclient import TestClient  # noqa: E402

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER  # noqa: E402
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup  # noqa: E402

_LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_TODO = "dddddddd-4444-4444-8444-444444444444"
_CASE = "aaaaaaaa-1111-4111-8111-111111111111"

_ROUTES: tuple[tuple[str, str, dict], ...] = (
    ("/v2/commands/create-todo", CREATE_TODO, {"body": "Llamar", "note": "alta"}),
    ("/v2/commands/update-todo", UPDATE_TODO,
     {"todo_id": _TODO, "todo_version": 1, "body": "Llamar", "is_urgent": True, "due_on": None,
      "opportunity_id": _CASE, "note": "cambio"}),
    ("/v2/commands/move-todo", MOVE_TODO,
     {"todo_id": _TODO, "todo_version": 1, "after_todo_id": None, "note": "orden"}),
    ("/v2/commands/complete-todo", COMPLETE_TODO, {"todo_id": _TODO, "todo_version": 1, "note": "hecho"}),
    ("/v2/commands/reopen-todo", REOPEN_TODO, {"todo_id": _TODO, "todo_version": 2, "note": "no era"}),
    ("/v2/commands/delete-todo", DELETE_TODO, {"todo_id": _TODO, "todo_version": 1, "note": "sobra"}),
)


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity) -> None:
        self._operator = operator

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return self._operator


def _operator(role: str = "sales") -> OperatorIdentity:
    return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="operator@example.cl",
                            display_name="Operator", role=role, status="active")


class _FakeRepo:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"command": kwargs["command_name"], "replayed": False}


def _client(repo: _FakeRepo, role: str = "sales") -> TestClient:
    from fastapi import FastAPI

    from origenlab_api.v2.todos.routes import todo_command_router

    app = FastAPI()
    app.include_router(todo_command_router)
    app.state.v2_todo_command_repository = repo
    app.state.v2_identity = LocalDevIdentity(_LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


def _headers(key: str | None = "key-1") -> dict[str, str]:
    headers = {OPERATOR_EMAIL_HEADER: "operator@example.cl"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_every_todo_command_has_a_post_route() -> None:
    from origenlab_api.v2.todos.routes import todo_command_router

    assert {c for _, c, _ in _ROUTES} == set(TODO_COMMAND_NAMES)
    assert {r.path for r in todo_command_router.routes} == {p for p, _, _ in _ROUTES}
    for route in todo_command_router.routes:
        assert set(route.methods) == {"POST"}


@pytest.mark.parametrize(("path", "command", "body"), _ROUTES)
def test_a_todo_command_reaches_the_repository_with_its_name(path: str, command: str, body: dict) -> None:
    repo = _FakeRepo()
    response = _client(repo).post(path, json=body, headers=_headers())
    assert response.status_code == 200, response.text
    assert repo.calls[0]["command_name"] == command
    assert repo.calls[0]["idempotency_key"] == "key-1"
    assert repo.calls[0]["operator"].operator_id == _operator().operator_id


@pytest.mark.parametrize(("path", "_command", "body"), _ROUTES)
def test_a_viewer_may_not_write_a_todo(path: str, _command: str, body: dict) -> None:
    repo = _FakeRepo()
    assert _client(repo, "viewer").post(path, json=body, headers=_headers()).status_code == 403
    assert repo.calls == []


@pytest.mark.parametrize(("path", "_command", "body"), _ROUTES)
def test_a_todo_command_needs_a_key_and_a_note(path: str, _command: str, body: dict) -> None:
    repo = _FakeRepo()
    assert _client(repo).post(path, json=body, headers=_headers(None)).status_code == 400
    assert _client(repo).post(path, json={**body, "note": "  "}, headers=_headers()).status_code == 422
    assert _client(repo).post(path, json={**body, "owner_operator_id": _CASE},
                              headers=_headers()).status_code == 422
    assert repo.calls == []
