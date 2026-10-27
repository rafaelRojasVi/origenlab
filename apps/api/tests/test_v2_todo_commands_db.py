"""The «Pendientes» todo commands against PostgreSQL (opt-in: `ORIGENLAB_V2_TEST_DSN`).

Runs in a disposable database (`tests/v2_command_harness.py`) as `origenlab_api`: ordering inside
the urgent / not-urgent groups, a stale version writing nothing, a closed case being linkable, and one
event plus one receipt per command. Every name and address is fictitious.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
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

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402

_KEYS = iter(range(1, 1_000_000))


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture
def world(db):
    """One sales operator, one open case and one closed case (terminal, like won); the todo list is emptied per test."""
    import psycopg

    tag = uuid.uuid4().hex[:12]
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("alter table crm.todo disable trigger todo_never_deleted")
        cur.execute("delete from crm.todo")
        cur.execute("alter table crm.todo enable trigger todo_never_deleted")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
            "values (gen_random_uuid(), %s, 'Vendedora Ficticia', 'sales', 'active') returning id::text",
            (f"ventas-{tag}@example.test",),
        )
        operator_id = cur.fetchone()[0]
        cur.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) "
            "returning id::text, version",
            (f"Caso ficticio {tag}", operator_id),
        )
        case_id, case_version = cur.fetchone()
        cur.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) "
            "returning id::text",
            (f"Caso cerrado ficticio {tag}", operator_id),
        )
        won_id = cur.fetchone()[0]
        cur.execute("update crm.opportunity set stage = 'abandoned', close_reason = 'prueba', closed_at = now(), "
                    "version = version + 1 where id = %s", (won_id,))
    return {"operator_id": operator_id, "case_id": case_id, "case_version": case_version,
            "won_id": won_id, "db": db}


def _run(world, name, body, key=None):
    import psycopg

    from origenlab_api.v2.todos.repository import V2TodoCommandRepository

    repo = V2TodoCommandRepository(psycopg.connect, runtime_dsn(world["db"]))
    operator = OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                                display_name="Vendedora Ficticia", role="sales", status="active")
    return repo.execute(command_name=name, operator=operator, fields=validated_todo(name, body),
                        idempotency_key=key or f"pytest-todo-{next(_KEYS)}", digest=request_digest(name, body))


def _one(world, sql, *params):
    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _create(world, text, *, urgent=False, **extra):
    return _run(world, CREATE_TODO, CreateTodoBody(body=text, is_urgent=urgent, note="alta", **extra))


def _position(world, todo_id) -> Decimal:
    return _one(world, "select position from crm.todo where id = %s", todo_id)[0]


def _order(world, urgent: bool) -> list[str]:
    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select body from crm.todo where status = 'open' and is_urgent = %s order by position",
                    (urgent,))
        return [row[0] for row in cur.fetchall()]


def _move(world, line, after, version=1):
    return _run(world, MOVE_TODO, MoveTodoBody(
        todo_id=line["todo_id"], todo_version=version, after_todo_id=after and after["todo_id"], note="orden"))


@needs_db
def test_create_puts_line_at_top_of_its_group(world) -> None:
    first = _create(world, "Primera")
    second = _create(world, "Segunda")
    urgent = _create(world, "Urgente", urgent=True)
    assert _position(world, first["todo_id"]) == 0
    assert _position(world, second["todo_id"]) == -1
    assert _position(world, urgent["todo_id"]) == 0  # its own group was empty
    assert _order(world, False) == ["Segunda", "Primera"]
    assert second["status"] == "open" and second["todo_version"] == 1


@needs_db
def test_toggle_urgent_moves_to_top_of_destination_group(world) -> None:
    _create(world, "Urgente A", urgent=True)
    low = _create(world, "Normal")
    _create(world, "Urgente B", urgent=True)  # position -1: the top of the urgent group
    updated = _run(world, UPDATE_TODO, UpdateTodoBody(
        todo_id=low["todo_id"], todo_version=1, body="Normal", is_urgent=True, due_on=None,
        opportunity_id=None, note="sube"))
    assert updated["todo_version"] == 2
    assert _order(world, True) == ["Normal", "Urgente B", "Urgente A"]
    assert _order(world, False) == []


@needs_db
def test_update_changes_text_day_and_case_and_clears_them(world) -> None:
    from datetime import date

    line = _create(world, "Llamar")
    updated = _run(world, UPDATE_TODO, UpdateTodoBody(
        todo_id=line["todo_id"], todo_version=1, body="Llamar a la clienta", is_urgent=False,
        due_on=date(2026, 11, 3), opportunity_id=world["case_id"], note="detalle"))
    assert _one(world, "select body, due_on, opportunity_id::text from crm.todo where id = %s",
                line["todo_id"]) == ("Llamar a la clienta", date(2026, 11, 3), world["case_id"])
    payload = _one(world, "select payload from crm.domain_event where aggregate_id = %s and "
                   "event_type = 'todo.updated'", line["todo_id"])[0]
    assert set(payload["changed"]) == {"body", "due_on", "opportunity_id"}
    _run(world, UPDATE_TODO, UpdateTodoBody(
        todo_id=line["todo_id"], todo_version=updated["todo_version"], body="Llamar a la clienta",
        is_urgent=False, due_on=None, opportunity_id=None, note="limpia"))
    assert _one(world, "select due_on, opportunity_id from crm.todo where id = %s",
                line["todo_id"]) == (None, None)


@needs_db
def test_move_between_two_lines_orders_them(world) -> None:
    a = _create(world, "A")
    b = _create(world, "B")
    c = _create(world, "C")
    assert _order(world, False) == ["C", "B", "A"]
    _move(world, a, c)  # A right after C, ahead of B
    assert _order(world, False) == ["C", "A", "B"]
    assert _position(world, c["todo_id"]) < _position(world, a["todo_id"]) < _position(world, b["todo_id"])
    _move(world, c, b, version=1)  # after the last line
    assert _order(world, False) == ["A", "B", "C"]
    _move(world, b, None, version=1)  # to the top
    assert _order(world, False) == ["B", "A", "C"]
    _move(world, b, None, version=2)  # alone at the top stays at the top
    assert _order(world, False)[0] == "B"


@needs_db
def test_move_to_other_group_refused(world) -> None:
    normal = _create(world, "Normal")
    other = _create(world, "Otra")
    urgent = _create(world, "Urgente", urgent=True)
    with pytest.raises(CommandRefused) as wrong_group:
        _move(world, normal, urgent)
    assert wrong_group.value.code == "todo_move_target_invalid"
    with pytest.raises(CommandRefused) as itself:
        _move(world, normal, normal)
    assert itself.value.code == "todo_move_target_invalid"
    _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=other["todo_id"], todo_version=1, note="x"))
    with pytest.raises(CommandRefused) as done:
        _move(world, normal, other)
    assert done.value.code == "todo_move_target_invalid"
    with pytest.raises(CommandRefused) as missing:
        _run(world, MOVE_TODO, MoveTodoBody(todo_id=normal["todo_id"], todo_version=1,
                                            after_todo_id=str(uuid.uuid4()), note="x"))
    assert missing.value.code == "todo_move_target_invalid"


@needs_db
def test_stale_version_refused_and_writes_nothing(world) -> None:
    line = _create(world, "Llamar")
    receipts = _one(world, "select count(*) from platform.command_receipt")[0]
    events = _one(world, "select count(*) from crm.domain_event where aggregate_kind = 'todo'")[0]
    for name, body in (
        (UPDATE_TODO, UpdateTodoBody(todo_id=line["todo_id"], todo_version=7, body="Otro", is_urgent=True,
                                     due_on=None, opportunity_id=None, note="x")),
        (MOVE_TODO, MoveTodoBody(todo_id=line["todo_id"], todo_version=7, after_todo_id=None, note="x")),
        (COMPLETE_TODO, CompleteTodoBody(todo_id=line["todo_id"], todo_version=7, note="x")),
        (REOPEN_TODO, ReopenTodoBody(todo_id=line["todo_id"], todo_version=7, note="x")),
        (DELETE_TODO, DeleteTodoBody(todo_id=line["todo_id"], todo_version=7, note="x")),
    ):
        with pytest.raises(CommandRefused) as stale:
            _run(world, name, body)
        assert stale.value.code == "todo_version_conflict", name
    assert _one(world, "select count(*) from platform.command_receipt")[0] == receipts
    assert _one(world, "select count(*) from crm.domain_event where aggregate_kind = 'todo'")[0] == events
    assert _one(world, "select body, is_urgent, status, version from crm.todo where id = %s",
                line["todo_id"]) == ("Llamar", False, "open", 1)


@needs_db
def test_link_to_won_case_allowed(world) -> None:
    line = _create(world, "Pedir factura", opportunity_id=world["won_id"])
    assert _one(world, "select opportunity_id::text from crm.todo where id = %s", line["todo_id"]) == (
        world["won_id"],)
    assert _one(world, "select stage, version from crm.opportunity where id = %s", world["won_id"]) == (
        "abandoned", 2)  # the case is read, never written


@needs_db
def test_unknown_case_404(world) -> None:
    with pytest.raises(CommandRefused) as missing:
        _create(world, "Llamar", opportunity_id=str(uuid.uuid4()))
    assert (missing.value.status_code, missing.value.code) == (404, "case_not_found")
    line = _create(world, "Llamar")
    with pytest.raises(CommandRefused) as on_update:
        _run(world, UPDATE_TODO, UpdateTodoBody(
            todo_id=line["todo_id"], todo_version=1, body="Llamar", is_urgent=False, due_on=None,
            opportunity_id=str(uuid.uuid4()), note="x"))
    assert on_update.value.code == "case_not_found"
    with pytest.raises(CommandRefused) as no_line:
        _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=str(uuid.uuid4()), todo_version=1, note="x"))
    assert (no_line.value.status_code, no_line.value.code) == (404, "todo_not_found")


@needs_db
def test_complete_then_reopen_keeps_position(world) -> None:
    _create(world, "Base")
    line = _create(world, "Llamar")
    position = _position(world, line["todo_id"])
    done = _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=line["todo_id"], todo_version=1, note="hecho"))
    assert done["status"] == "done" and done["todo_version"] == 2
    assert _one(world, "select completed_at is not null from crm.todo where id = %s", line["todo_id"]) == (True,)
    assert _order(world, False) == ["Base"]
    reopened = _run(world, REOPEN_TODO, ReopenTodoBody(todo_id=line["todo_id"], todo_version=2, note="no"))
    assert reopened["status"] == "open" and reopened["todo_version"] == 3
    assert _position(world, line["todo_id"]) == position
    assert _one(world, "select completed_at from crm.todo where id = %s", line["todo_id"]) == (None,)
    assert _order(world, False) == ["Llamar", "Base"]
    with pytest.raises(CommandRefused) as not_done:
        _run(world, REOPEN_TODO, ReopenTodoBody(todo_id=line["todo_id"], todo_version=3, note="x"))
    assert not_done.value.code == "todo_is_not_done"


@needs_db
def test_complete_twice_refused(world) -> None:
    line = _create(world, "Llamar")
    _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=line["todo_id"], todo_version=1, note="hecho"))
    with pytest.raises(CommandRefused) as again:
        _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=line["todo_id"], todo_version=2, note="hecho"))
    assert (again.value.status_code, again.value.code) == (409, "todo_is_not_open")


@needs_db
def test_a_deleted_line_takes_no_more_commands(world) -> None:
    done = _create(world, "Hecha")
    _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=done["todo_id"], todo_version=1, note="x"))
    gone = _run(world, DELETE_TODO, DeleteTodoBody(todo_id=done["todo_id"], todo_version=2, note="fuera"))
    assert gone["status"] == "deleted"  # a done line can be deleted too
    assert _one(world, "select status, completed_at from crm.todo where id = %s", done["todo_id"]) == (
        "deleted", None)
    version = gone["todo_version"]
    for name, body in (
        (UPDATE_TODO, UpdateTodoBody(todo_id=done["todo_id"], todo_version=version, body="x", is_urgent=False,
                                     due_on=None, opportunity_id=None, note="x")),
        (MOVE_TODO, MoveTodoBody(todo_id=done["todo_id"], todo_version=version, after_todo_id=None, note="x")),
        (COMPLETE_TODO, CompleteTodoBody(todo_id=done["todo_id"], todo_version=version, note="x")),
        (REOPEN_TODO, ReopenTodoBody(todo_id=done["todo_id"], todo_version=version, note="x")),
        (DELETE_TODO, DeleteTodoBody(todo_id=done["todo_id"], todo_version=version, note="x")),
    ):
        with pytest.raises(CommandRefused) as refused:
            _run(world, name, body)
        assert refused.value.code == "todo_is_deleted", name


@needs_db
def test_each_command_writes_one_event_and_receipt(world) -> None:
    line = _create(world, "Llamar")
    other = _create(world, "Otra")
    steps = [
        (UPDATE_TODO, "todo.updated", UpdateTodoBody(
            todo_id=line["todo_id"], todo_version=1, body="Llamar hoy", is_urgent=False, due_on=None,
            opportunity_id=None, note="x")),
        (MOVE_TODO, "todo.moved", MoveTodoBody(
            todo_id=line["todo_id"], todo_version=2, after_todo_id=other["todo_id"], note="x")),
        (COMPLETE_TODO, "todo.completed", CompleteTodoBody(todo_id=line["todo_id"], todo_version=3, note="x")),
        (REOPEN_TODO, "todo.reopened", ReopenTodoBody(todo_id=line["todo_id"], todo_version=4, note="x")),
        (DELETE_TODO, "todo.deleted", DeleteTodoBody(todo_id=line["todo_id"], todo_version=5, note="x")),
    ]
    assert _one(world, "select event_type from crm.domain_event where aggregate_id = %s", line["todo_id"]) == (
        "todo.created",)
    for name, event_type, body in steps:
        receipts = _one(world, "select count(*) from platform.command_receipt")[0]
        result = _run(world, name, body)
        assert _one(world, "select count(*) from platform.command_receipt")[0] == receipts + 1
        assert _one(world, "select count(*) from crm.domain_event where aggregate_id = %s and event_type = %s",
                    line["todo_id"], event_type)[0] == 1
        assert set(result) >= {"command", "todo_id", "todo_version", "status", "event_ids"}
        assert result["command"] == name and len(result["event_ids"]) == 1
    assert _one(world, "select count(*) from crm.domain_event where aggregate_id = %s", line["todo_id"])[0] == 6


@needs_db
def test_replay_same_key_returns_same_result(world) -> None:
    body = CreateTodoBody(body="Llamar", note="alta")
    first = _run(world, CREATE_TODO, body, key="pytest-todo-replay")
    second = _run(world, CREATE_TODO, body, key="pytest-todo-replay")
    assert second["replayed"] is True and second["todo_id"] == first["todo_id"]
    assert _one(world, "select count(*) from crm.todo")[0] == 1
