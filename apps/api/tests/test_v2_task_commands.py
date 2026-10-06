"""The W11 task commands — `create_task`, `complete_task`, `cancel_task`.

**In process, always run:** the route surface, the four universal requirements, an aware `due_at`
and the read model's «next action» from an open task.

**Database-backed, opt-in** (`ORIGENLAB_V2_TEST_DSN`): a task is written with its event, it never
moves the case or its version, a closed case takes none, a stale version and a second close are
refused by name, and a refused command writes nothing. Runs in a disposable database
(`tests/v2_command_harness.py`) as `origenlab_api`. Every name and address is fictitious.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.crm_workspace import compose_pipeline, task_next_action
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.task_commands import (
    CANCEL_TASK,
    COMPLETE_TASK,
    CREATE_TASK,
    TASK_COMMAND_NAMES,
    CancelTaskBody,
    CompleteTaskBody,
    CreateTaskBody,
    validated_task,
)

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
CASE_ID = "aaaaaaaa-1111-4111-8111-111111111111"
TASK_ID = "dddddddd-4444-4444-8444-444444444444"

ROUTES: tuple[tuple[str, str, dict], ...] = (
    ("/v2/commands/create-task", CREATE_TASK,
     {"opportunity_id": CASE_ID, "title": "Retomar", "due_at": "2026-11-02T12:00:00Z", "note": "fondos"}),
    ("/v2/commands/complete-task", COMPLETE_TASK, {"task_id": TASK_ID, "task_version": 1, "note": "hecho"}),
    ("/v2/commands/cancel-task", CANCEL_TASK, {"task_id": TASK_ID, "task_version": 1, "note": "retomada"}),
)


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity | None) -> None:
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


def _client(repo: _FakeRepo, operator: OperatorIdentity | None = None) -> TestClient:
    from fastapi import FastAPI

    from origenlab_api.v2.task_command_routes import task_command_router

    app = FastAPI()
    app.include_router(task_command_router)
    app.state.v2_task_command_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operator or _operator()))
    return TestClient(app)


def _headers(key: str | None = "key-1") -> dict[str, str]:
    headers = {OPERATOR_EMAIL_HEADER: "operator@example.cl"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_every_task_command_has_a_post_route() -> None:
    from origenlab_api.v2.task_command_routes import task_command_router

    assert {c for _, c, _ in ROUTES} == set(TASK_COMMAND_NAMES)
    assert {r.path for r in task_command_router.routes} == {p for p, _, _ in ROUTES}
    for route in task_command_router.routes:
        assert set(route.methods) == {"POST"}


@pytest.mark.parametrize(("path", "command", "body"), ROUTES)
def test_a_task_command_reaches_the_repository_with_its_name(path: str, command: str, body: dict) -> None:
    repo = _FakeRepo()
    response = _client(repo).post(path, json=body, headers=_headers())
    assert response.status_code == 200, response.text
    assert repo.calls[0]["command_name"] == command
    assert repo.calls[0]["operator"].operator_id == _operator().operator_id


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_a_viewer_may_not_write_a_task(path: str, _command: str, body: dict) -> None:
    repo = _FakeRepo()
    response = _client(repo, _operator(role="viewer")).post(path, json=body, headers=_headers())
    assert response.status_code == 403
    assert repo.calls == []


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_a_task_command_needs_a_key_and_a_note(path: str, _command: str, body: dict) -> None:
    repo = _FakeRepo()
    assert _client(repo).post(path, json=body, headers=_headers(None)).status_code == 400
    assert _client(repo).post(path, json={**body, "note": "  "}, headers=_headers()).status_code == 422
    assert _client(repo).post(path, json={**body, "owner_operator_id": CASE_ID},
                              headers=_headers()).status_code == 422
    assert repo.calls == []


def test_a_due_date_without_a_time_zone_is_refused() -> None:
    with pytest.raises(ValidationError):
        CreateTaskBody(opportunity_id=CASE_ID, title="Retomar", due_at=datetime(2026, 11, 2, 12), note="x")
    with pytest.raises(ValidationError):
        CreateTaskBody(opportunity_id=CASE_ID, title="   ", due_at=datetime(2026, 11, 2, tzinfo=UTC), note="x")
    with pytest.raises(CommandRefused) as exc:
        validated_task(COMPLETE_TASK, CompleteTaskBody(task_id="nope", task_version=1, note="x"))
    assert exc.value.code == "malformed_identifier"


def test_the_card_names_its_earliest_open_task_as_the_next_action() -> None:
    opp = {"opportunity_id": "o1", "title": "Caso", "stage": "quoting", "version": 3}
    tasks = [
        {"task_id": "t2", "opportunity_id": "o1", "title": "Llamar", "due_at": "2026-12-01T12:00:00Z",
         "version": 1, "owner_display_name": "Ventas"},
        {"task_id": "t1", "opportunity_id": "o1", "title": "Retomar: fondos", "due_at": "2026-11-02T12:00:00Z",
         "version": 2, "owner_display_name": "Ventas"},
    ]
    card = compose_pipeline([opp], [], [], [], {}, [], {}, None, tasks)[0]
    assert [t["task_id"] for t in card["open_tasks"]] == ["t1", "t2"]
    assert card["open_tasks"][0] == {"task_id": "t1", "title": "Retomar: fondos",
                                     "due_at": "2026-11-02T12:00:00Z", "version": 2, "owner": "Ventas"}
    assert card["next_action"] == {"text": "Retomar: fondos", "source": "task", "due_at": "2026-11-02T12:00:00Z"}
    bare = compose_pipeline([opp], [], [], [], {}, [], {})[0]
    assert bare["open_tasks"] == [] and bare["next_action"]["source"] == "suggested"
    assert task_next_action([]) is None


# ------------------------------------------------------------------ against PostgreSQL

from v2_command_harness import build_disposable_database, needs_db, runtime_dsn  # noqa: E402

_KEYS = iter(range(1, 1_000_000))
DUE = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture
def world(db):
    """One sales operator and one open case at `lead`."""
    import psycopg

    tag = uuid.uuid4().hex[:12]
    with psycopg.connect(db, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
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
        case_id, version = cur.fetchone()
    return {"operator_id": operator_id, "case_id": case_id, "case_version": version, "db": db}


def _run(world, name, body, key=None):
    import psycopg

    from origenlab_api.v2.task_command_repository import V2TaskCommandRepository

    repo = V2TaskCommandRepository(psycopg.connect, runtime_dsn(world["db"]))
    operator = OperatorIdentity(operator_id=world["operator_id"], email_norm="ventas@example.test",
                                display_name="Vendedora Ficticia", role="sales", status="active")
    return repo.execute(command_name=name, operator=operator, fields=validated_task(name, body),
                        idempotency_key=key or f"pytest-task-{next(_KEYS)}", digest=request_digest(name, body))


def _one(world, sql, *params):
    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


@needs_db
def test_a_task_is_written_with_its_event_and_the_case_does_not_move(world) -> None:
    created = _run(world, CREATE_TASK, CreateTaskBody(
        opportunity_id=world["case_id"], title="Retomar: fondos", due_at=DUE, note="vuelve en noviembre"))
    assert created["status"] == "open" and created["task_version"] == 1
    status, due, owner = _one(world, "select status, due_at, owner_operator_id::text from crm.task where id = %s",
                              created["task_id"])
    assert (status, due, owner) == ("open", DUE, world["operator_id"])
    assert _one(world, "select stage, version from crm.opportunity where id = %s", world["case_id"]) == (
        "lead", world["case_version"])
    kind, payload = _one(world, "select event_type, payload from crm.domain_event where aggregate_id = %s",
                         created["task_id"])
    assert kind == "task.created" and payload["opportunity_id"] == world["case_id"]

    cancelled = _run(world, CANCEL_TASK, CancelTaskBody(task_id=created["task_id"], task_version=1, note="retomada"))
    assert cancelled["status"] == "cancelled" and cancelled["task_version"] == 2
    assert _one(world, "select status, cancel_reason from crm.task where id = %s", created["task_id"]) == (
        "cancelled", "retomada")
    with pytest.raises(CommandRefused) as again:
        _run(world, COMPLETE_TASK, CompleteTaskBody(task_id=created["task_id"], task_version=2, note="hecho"))
    assert again.value.code == "task_is_not_open"


@needs_db
def test_a_stale_version_is_refused_and_writes_nothing(world) -> None:
    created = _run(world, CREATE_TASK, CreateTaskBody(
        opportunity_id=world["case_id"], title="Llamar", due_at=DUE, note="seguimiento"))
    before = _one(world, "select count(*) from platform.command_receipt")[0]
    with pytest.raises(CommandRefused) as stale:
        _run(world, COMPLETE_TASK, CompleteTaskBody(task_id=created["task_id"], task_version=5, note="hecho"))
    assert stale.value.code == "task_version_conflict"
    assert _one(world, "select count(*) from platform.command_receipt")[0] == before
    done = _run(world, COMPLETE_TASK, CompleteTaskBody(task_id=created["task_id"], task_version=1, note="hecho"))
    assert done["status"] == "done"
    assert _one(world, "select completed_at is not null from crm.task where id = %s", created["task_id"]) == (True,)


@needs_db
def test_a_closed_case_takes_no_task_and_a_replay_writes_one(world) -> None:
    body = CreateTaskBody(opportunity_id=world["case_id"], title="Retomar", due_at=DUE, note="pausa")
    first = _run(world, CREATE_TASK, body, key="pytest-task-replay")
    second = _run(world, CREATE_TASK, body, key="pytest-task-replay")
    assert second["replayed"] is True and second["task_id"] == first["task_id"]
    assert _one(world, "select count(*) from crm.task where opportunity_id = %s", world["case_id"])[0] == 1

    import psycopg

    with psycopg.connect(world["db"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("update crm.opportunity set stage = 'abandoned', close_reason = 'prueba', closed_at = now(), "
                    "version = version + 1 where id = %s", (world["case_id"],))
    with pytest.raises(CommandRefused) as closed:
        _run(world, CREATE_TASK, CreateTaskBody(
            opportunity_id=world["case_id"], title="Otra", due_at=DUE, note="x"))
    assert closed.value.code == "case_is_closed"
    with pytest.raises(CommandRefused) as missing:
        _run(world, CREATE_TASK, CreateTaskBody(
            opportunity_id=str(uuid.uuid4()), title="Otra", due_at=DUE, note="x"))
    assert missing.value.code == "case_not_found"
