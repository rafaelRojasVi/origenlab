"""`GET /v2/workspace/todos` («Pendientes») against a disposable database, as `origenlab_api`.

Todos are seeded as the owner; ordering, the done window, hidden deleted lines and the case label
are read back through the route. Every value is invented (public repository).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.todos.reads import TodoReadRepository
from origenlab_api.v2.todos.routes import todo_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


class _Lookup(OperatorLookup):
    def __init__(self, operators) -> None:
        self._by_email = {o.email_norm: o for o in operators.values()}

    def by_email(self, email_norm: str):
        return self._by_email.get(email_norm)


@pytest.fixture
def world(db):
    tag = uuid.uuid4().hex[:10]
    operators = {}
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set session_replication_role = replica")  # the stage walk is not under test
        cur.execute("set role origenlab_owner")
        cur.execute("alter table crm.todo disable trigger todo_never_deleted")
        cur.execute("delete from crm.todo")
        cur.execute("alter table crm.todo enable trigger todo_never_deleted")
        for role in ("sales", "viewer"):
            cur.execute(
                "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                (f"{role}-{tag}@example.test", f"Operador {role} {tag}", role))
            operators[role] = OperatorIdentity(
                operator_id=cur.fetchone()[0], email_norm=f"{role}-{tag}@example.test",
                display_name=f"Operador {role} {tag}", role=role, status="active")
        cur.execute("insert into crm.organization (kind, name, confirmation) "
                    "values ('customer', %s, 'confirmed') returning id::text", (f"Instituto Ficticio {tag}",))
        org_id = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                    "values (%s, 'quoting', %s, %s) returning id::text",
                    (f"Caso ficticio {tag}", operators["sales"].operator_id, org_id))
        case_id = cur.fetchone()[0]
        for number in (f"CN{tag}A", f"CN{tag}B"):
            cur.execute("insert into crm.quote (opportunity_id, quote_number, number_origin) "
                        "values (%s, %s, 'minted')", (case_id, number))
    app = FastAPI()
    app.include_router(todo_read_router)
    app.state.v2_todo_read_repository = TodoReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return {"db": db, "tag": tag, "operators": operators, "case_id": case_id, "client": TestClient(app)}


def _todo(world, body, *, urgent=False, position=0, status="open", done_ago_days=None, case=False, due=None):
    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.todo (body, is_urgent, position, status, completed_at, created_by_operator_id, "
            "opportunity_id, due_on) values (%s, %s, %s, %s, "
            "case when %s::int is null then null else now() - make_interval(days => %s::int) end, "
            "%s, %s, %s)",
            (body, urgent, position, status, done_ago_days if status == "done" else None, done_ago_days,
             world["operators"]["sales"].operator_id, world["case_id"] if case else None, due))


def _get(world, role="sales", **params):
    return world["client"].get("/v2/workspace/todos", params=params,
                               headers={OPERATOR_EMAIL_HEADER: world["operators"][role].email_norm})


def test_open_lines_are_urgent_first_then_by_position(world) -> None:
    _todo(world, "c normal", position=-1)
    _todo(world, "b normal", position=-5)
    _todo(world, "a urgente", urgent=True, position=3)
    body = _get(world).json()
    assert [t["body"] for t in body["open"]] == ["a urgente", "b normal", "c normal"]
    assert body["done"] == []
    item = body["open"][1]
    assert item["position"] == "-5" and item["due_on"] is None and item["opportunity"] is None
    assert item["status"] == "open" and item["version"] == 1 and item["completed_at"] is None
    assert item["created_by"] == world["operators"]["sales"].display_name


def test_a_tie_reads_newest_first(world) -> None:
    _todo(world, "antigua", position=2)
    _todo(world, "nueva", position=2)  # inserted later: newer created_at
    _todo(world, "arriba", position=1)
    assert [t["body"] for t in _get(world).json()["open"]] == ["arriba", "nueva", "antigua"]


def test_done_lines_are_limited_to_the_window_newest_first_and_deleted_never(world) -> None:
    _todo(world, "hace 2", status="done", done_ago_days=2)
    _todo(world, "hace 1", status="done", done_ago_days=1)
    _todo(world, "hace 10", status="done", done_ago_days=10)
    _todo(world, "borrada", status="deleted")
    assert [t["body"] for t in _get(world).json()["done"]] == ["hace 1", "hace 2"]
    wide = _get(world, done_days=31).json()
    assert [t["body"] for t in wide["done"]] == ["hace 1", "hace 2", "hace 10"]
    assert wide["open"] == []
    assert wide["done"][0]["completed_at"] is not None


def test_a_line_about_a_case_carries_its_label_and_latest_quote(world) -> None:
    _todo(world, "con caso", case=True, due="2026-11-03")
    item = _get(world).json()["open"][0]
    assert item["due_on"] == "2026-11-03"
    assert item["opportunity"] == {
        "id": world["case_id"], "title": f"Caso ficticio {world['tag']}", "stage": "quoting",
        "organization_name": f"Instituto Ficticio {world['tag']}", "quote_number": f"CN{world['tag']}B"}


@pytest.mark.parametrize("days", [0, 32])
def test_done_days_is_bounded(world, days) -> None:
    assert _get(world, done_days=days).status_code == 422


def test_a_viewer_may_read_the_list(world) -> None:
    assert _get(world, role="viewer").status_code == 200
