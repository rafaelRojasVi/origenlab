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


def _owner_sql(world, sql, *params):
    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)


def _all(world, sql, *params):
    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _create(world, text, *, urgent=False, **extra):
    return _run(world, CREATE_TODO, CreateTodoBody(body=text, is_urgent=urgent, note="alta", **extra))


def _position(world, todo_id) -> Decimal:
    return _one(world, "select position from crm.todo where id = %s", todo_id)[0]


def _order(world, urgent: bool) -> list[str]:
    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("select body from crm.todo where status = 'open' and is_urgent = %s "
                    "order by position, created_at desc, id", (urgent,))  # the read's order: newest first on a tie
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
def test_move_past_tied_positions_after_undo_really_moves(world) -> None:
    a = _create(world, "A")  # 0
    b = _create(world, "B")  # -1
    c = _create(world, "C")  # -2
    _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=c["todo_id"], todo_version=1, note="hecho"))
    d = _create(world, "D")  # min(open) - 1 = -2, the position C still holds
    _run(world, REOPEN_TODO, ReopenTodoBody(todo_id=c["todo_id"], todo_version=2, note="deshacer"))
    assert _order(world, False) == ["D", "C", "B", "A"]  # the tie reads newest first: D above C
    # «Subir» on the third line (B): it goes right after the first (D), above C.
    _move(world, b, d)
    assert _order(world, False) == ["D", "B", "C", "A"]
    # A drop between the formerly tied lines lands between them too.
    _move(world, a, d)
    assert _order(world, False) == ["D", "A", "B", "C"]
    versions = {row[0]: row[1] for row in _all(world, "select body, version from crm.todo")}
    assert versions == {"A": 2, "B": 2, "C": 3, "D": 1}  # renumbering bumps no other line's version
    assert _one(world, "select count(*) from crm.domain_event where aggregate_id = %s", d["todo_id"])[0] == 1


@needs_db
def test_move_between_tied_lines_renumbers_then_moves(world) -> None:
    a = _create(world, "A")
    b = _create(world, "B")
    c = _create(world, "C")
    d = _create(world, "D")  # D, C, B, A
    # A tie the move itself must cope with (a reopen now unties its own): C and B share a position.
    _owner_sql(world, "update crm.todo set position = -2 where id = %s", b["todo_id"])
    assert _order(world, False) == ["D", "C", "B", "A"]  # created_at breaks the tie: newest (C) first
    _move(world, a, c)  # between the tied C and B
    assert _order(world, False) == ["D", "C", "A", "B"]
    _move(world, b, d)  # «Subir» on the last line: right after D
    assert _order(world, False) == ["D", "B", "C", "A"]
    assert _all(world, "select body, version from crm.todo order by body") == [
        ("A", 2), ("B", 2), ("C", 1), ("D", 1)]


@needs_db
def test_a_forced_tie_reads_and_moves_newest_first(world) -> None:
    older = _create(world, "Antigua")
    newer = _create(world, "Nueva")
    tail = _create(world, "Cola")  # Cola, Nueva, Antigua
    _owner_sql(world, "update crm.todo set position = 5 where id in (%s, %s)", older["todo_id"], newer["todo_id"])
    assert _order(world, False) == ["Cola", "Nueva", "Antigua"]
    # «Bajar» on Cola sends it after the next line in read order (Nueva): it must land between the tie.
    _move(world, tail, newer)
    assert _order(world, False) == ["Nueva", "Cola", "Antigua"]


@needs_db
def test_renumber_waits_for_a_concurrent_move_and_keeps_it(world) -> None:
    """A renumber must not overwrite a move another transaction commits while it runs.

    Connection 1 holds an uncommitted versioned move of A to the top. Connection 2 (the repository,
    in a thread) reopens C onto a tied position, which renumbers the group; it has to wait for A's
    row lock. Once connection 1 commits, A must still be first — not put back at the rank a stale
    snapshot gave it.
    """
    import threading
    import time

    import psycopg

    a = _create(world, "A")  # 0
    _create(world, "B")  # -1
    c = _create(world, "C")  # -2
    _run(world, COMPLETE_TODO, CompleteTodoBody(todo_id=c["todo_id"], todo_version=1, note="hecho"))
    _create(world, "X")  # -2: C's held position, so reopening C renumbers
    outcome: dict[str, object] = {}
    with psycopg.connect(world["db"], autocommit=False) as mover:
        with mover.cursor() as cur:
            cur.execute("set local role origenlab_owner")
            cur.execute("update crm.todo set position = -10, version = version + 1 where id = %s", (a["todo_id"],))

        def reopen() -> None:
            try:
                outcome["result"] = _run(world, REOPEN_TODO, ReopenTodoBody(
                    todo_id=c["todo_id"], todo_version=2, note="deshacer"))
            except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
                outcome["error"] = exc

        worker = threading.Thread(target=reopen)
        worker.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:  # wait until the reopen is blocked on A's row lock
            waiting = _one(world, "select count(*) from pg_stat_activity where datname = current_database() "
                                  "and wait_event_type = 'Lock'")[0]
            if waiting or not worker.is_alive():
                break
            time.sleep(0.02)
        if "error" in outcome:
            raise outcome["error"]  # type: ignore[misc]
        assert waiting == 1, "the renumber never waited on the concurrent move"
        mover.commit()
    worker.join(15)
    assert not worker.is_alive()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    assert _order(world, False) == ["A", "X", "C", "B"]
    assert _one(world, "select version from crm.todo where id = %s", a["todo_id"])[0] == 2
    assert _one(world, "select count(distinct position) from crm.todo where status = 'open'")[0] == 4


@needs_db
def test_many_moves_into_the_same_gap_keep_order(world) -> None:
    a = _create(world, "A")
    b = _create(world, "B")
    c = _create(world, "C")  # C, B, A
    lines = {"A": a, "B": b}
    versions = {"A": 1, "B": 1}
    expected = ["C", "B", "A"]
    for step in range(120):  # each move halves the gap under C: Decimal bisection collapses near ~90
        name = "A" if step % 2 == 0 else "B"
        before = _position(world, lines[name]["todo_id"])
        moved = _move(world, lines[name], c, version=versions[name])
        versions[name] = moved["todo_version"]
        expected = ["C", name, "B" if name == "A" else "A"]
        assert _order(world, False) == expected, step
        assert _position(world, lines[name]["todo_id"]) != before or step == 0, step
    assert _one(world, "select count(distinct position) from crm.todo where status = 'open'")[0] == 3


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


def _wait_for_lock(world, worker, outcome, *, event: str | None = None) -> int:
    """Poll until one backend of this database waits on a lock (of kind `event`, if given)."""
    import time

    deadline = time.monotonic() + 15
    waiting = 0
    while time.monotonic() < deadline:
        waiting = _one(world, "select count(*) from pg_stat_activity where datname = current_database() "
                              "and wait_event_type = 'Lock' and (%s::text is null or wait_event = %s::text)",
                       event, event)[0]
        if waiting or not worker.is_alive():
            break
        time.sleep(0.02)
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return waiting


def _in_thread(fn, outcome):
    import threading

    def run() -> None:
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised / inspected in the test thread
            outcome["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    return worker


@needs_db
def test_a_reordering_command_queues_on_the_list_order_lock(world) -> None:
    """Moves and reopens take one transaction-scoped advisory lock before their own row lock.

    Connection 1 holds that lock in an open transaction; a move started meanwhile waits on it
    (not on a row) and completes once connection 1 commits. Without it, two renumbering moves on
    lines X < Y could each hold their own row and wait on the other's: a deadlock.
    """
    import psycopg

    a = _create(world, "A")
    _create(world, "B")
    c = _create(world, "C")  # C, B, A
    outcome: dict[str, object] = {}
    with psycopg.connect(world["db"], autocommit=False) as holder:
        holder.execute("select pg_advisory_xact_lock(hashtext('crm.todo:order'))")
        worker = _in_thread(lambda: _move(world, a, c), outcome)
        waiting = _wait_for_lock(world, worker, outcome, event="advisory")
        assert waiting == 1, "the move did not wait on the list-order lock"
        assert worker.is_alive()
        holder.commit()
    worker.join(15)
    assert not worker.is_alive()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    assert outcome["result"]["todo_version"] == 2  # type: ignore[index]
    assert _order(world, False) == ["C", "A", "B"]


@needs_db
def test_a_deadlock_inside_a_todo_command_is_a_version_conflict_not_a_500(world) -> None:
    """Belt: should a deadlock still reach a todo command, the operator gets the calm 409.

    Connection 1 follows no protocol (raw SQL, no advisory lock): it locks Y, the move of X then
    holds X and waits on Y inside the renumber, and connection 1 asks for X. The move's backend
    detects the cycle first (connection 1's deadlock_timeout is raised) and is the one aborted.
    """
    import psycopg

    x = _create(world, "X")
    y = _create(world, "Y")
    z = _create(world, "Z")  # Z, Y, X
    _owner_sql(world, "update crm.todo set position = -1 where id = %s", z["todo_id"])  # Z ties Y: a renumber
    outcome: dict[str, object] = {}
    with psycopg.connect(world["db"], autocommit=False) as other:
        other.execute("set deadlock_timeout = '20s'")
        other.execute("set local role origenlab_owner")
        other.execute("select 1 from crm.todo where id = %s for update", (y["todo_id"],))
        worker = _in_thread(lambda: _move(world, x, z), outcome)  # between the tied Z and Y
        assert _wait_for_lock(world, worker, outcome) == 1, "the move never waited on Y"
        other.execute("select 1 from crm.todo where id = %s for update", (x["todo_id"],))  # closes the cycle
        worker.join(15)
        other.rollback()
    assert not worker.is_alive()
    error = outcome.get("error")
    assert isinstance(error, CommandRefused), error
    assert (error.status_code, error.code) == (409, "todo_version_conflict")
    assert _one(world, "select version from crm.todo where id = %s", x["todo_id"])[0] == 1  # rolled back
