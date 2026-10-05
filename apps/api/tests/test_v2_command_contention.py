"""A busy row, a busy database and a lost unique race answer a refusal, never a 500.

Every app in this file answers through `errors.register_exception_handlers`, the handlers
`main.create_app` registers, so the bodies asserted here are the envelope a deployed API
sends (`{"error": {"code", "message", "details", "request_id"}}`) — not the bare
`{"detail": …}` a handler-less `FastAPI()` produces. The dashboard parses exactly these.

The first half needs no database: a fake connection raises the driver's own exception classes
inside the real `CommandTransaction`. The second half runs the real commands, as the real
`origenlab_api` role, through `create_app()` against a disposable database, and makes
PostgreSQL itself raise `lock_timeout`, `statement_timeout` and the unique violation.

Every name and address is fictitious.
"""

from __future__ import annotations

import threading
import time
import uuid
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.errors import (
    SERVICE_BUSY_RETRY_AFTER_SECONDS,
    database_refusal,
    register_exception_handlers,
)
from origenlab_api.request_id import RequestIdMiddleware
from origenlab_api.settings import Settings, get_settings
from origenlab_api.v2.audience_freeze import V2AudienceFreezeRepository
from origenlab_api.v2.command_core import DEFAULT_COMMAND_LOCK_TIMEOUT_MS
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.crm_authoring_routes import crm_authoring_router
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
PERSON_ID = "00000000-0000-4000-8000-0000000000b1"


def _operator(role: str = "sales") -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="00000000-0000-4000-8000-000000000001",
        email_norm="ventas@example.test",
        display_name="Ventas Ficticia",
        role=role,
        status="active",
    )


class _Lookup(OperatorLookup):
    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return _operator()


# ------------------------------------------------------------------ a connection that fails


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self.description: list[tuple[str]] | None = None
        self._row: tuple | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        self._conn.statements.append(" ".join(sql.split()))
        if self._conn.fails(sql):
            raise self._conn.error
        if "insert into platform.command_receipt" in sql:
            self.description, self._row = [("id",)], ("00000000-0000-4000-8000-0000000000aa",)
        else:
            self.description, self._row = None, None

    def fetchone(self) -> tuple | None:
        row, self._row = self._row, None
        return row


class _FakeConn:
    """One transaction whose first locking read raises `error`, as PostgreSQL would."""

    def __init__(self, error: BaseException, when: str = "for update") -> None:
        self.error = error
        self._when = when
        self.statements: list[str] = []
        self.committed = False
        self.rolled_back = False

    def fails(self, sql: str) -> bool:
        return self._when in sql

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def _connect_to(conn: _FakeConn):
    def connect(_dsn: str, *, autocommit: bool = False) -> _FakeConn:
        assert autocommit is False
        return conn

    return connect


def _no_free_connection(_dsn: str, *, autocommit: bool = False):
    raise PoolTimeout("couldn't get a connection after 30.00 sec")


class _UniqueOnContactPoint(psycopg.errors.UniqueViolation):
    """What psycopg raises for a lost race on `crm.contact_point (kind, value_norm)`."""

    @property
    def diag(self) -> SimpleNamespace:  # type: ignore[override]
        return SimpleNamespace(constraint_name="contact_point_kind_value_key")


def _production_app(repo: object) -> TestClient:
    """The CRM authoring router with the error handlers and request ids production has."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(crm_authoring_router)
    app.state.crm_authoring_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup())
    # A genuine fault must still reach the client as the 500 envelope, not as a raised error.
    return TestClient(app, raise_server_exceptions=False)


def _update_person(client: TestClient, *, key: str | None = None):
    return client.post(
        "/v2/commands/update-person",
        json={"person_id": PERSON_ID, "expected_version": 1, "display_name": "Nombre Nuevo", "note": "prueba"},
        headers={OPERATOR_EMAIL_HEADER: "ventas@example.test", "Idempotency-Key": key or uuid.uuid4().hex},
    )


def _repo_over(conn_or_connect) -> V2CrmAuthoringRepository:
    connect = conn_or_connect if callable(conn_or_connect) else _connect_to(conn_or_connect)
    return V2CrmAuthoringRepository(connect, "fake-dsn", statement_timeout_ms=4321, lock_timeout_ms=1234)


# ------------------------------------------------------------------ the mapping, alone


def test_each_sqlstate_maps_to_one_refusal_and_everything_else_stays_a_fault() -> None:
    busy = database_refusal(psycopg.errors.LockNotAvailable("canceling statement due to lock timeout"))
    assert (busy.status_code, busy.code, busy.retry_after) == (409, "record_busy", None)

    for exc in (psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
                PoolTimeout("couldn't get a connection after 30.00 sec")):
        refusal = database_refusal(exc)
        assert (refusal.status_code, refusal.code) == (503, "service_busy")
        assert refusal.retry_after == SERVICE_BUSY_RETRY_AFTER_SECONDS == 5

    duplicate = database_refusal(_UniqueOnContactPoint("duplicate key value"))
    assert (duplicate.status_code, duplicate.code) == (409, "duplicate")
    assert duplicate.details == {"constraint": "contact_point_kind_value_key"}
    # Without a server diagnostic there is no constraint to name, and nothing is invented.
    assert database_refusal(psycopg.errors.UniqueViolation("duplicate key value")).details == {}

    for fault in (psycopg.errors.SerializationFailure("x"), psycopg.errors.UndefinedTable("x"),
                  psycopg.OperationalError("x"), ValueError("x"), CommandRefused(409, "stale_version", "x")):
        assert database_refusal(fault) is None


# ------------------------------------------------------------------ the envelope, end to end


def test_a_stale_version_reaches_the_client_in_the_production_envelope() -> None:
    """The shape the dashboard must parse: a generic `conflict`, the specific code in details."""

    class _Stale:
        def execute(self, **_kw: object) -> dict:
            raise CommandRefused(409, "stale_version", "the person was modified since you loaded it")

    response = _update_person(_production_app(_Stale()))
    assert response.status_code == 409
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == "conflict"
    assert body["error"]["message"] == "the person was modified since you loaded it"
    assert body["error"]["details"] == {
        "code": "stale_version",
        "message": "the person was modified since you loaded it",
    }
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_a_row_lock_held_past_lock_timeout_is_record_busy_and_writes_nothing() -> None:
    conn = _FakeConn(psycopg.errors.LockNotAvailable("canceling statement due to lock timeout"))
    response = _update_person(_production_app(_repo_over(conn)))

    assert response.status_code == 409
    error = response.json()["error"]
    assert (error["code"], error["details"]) == ("record_busy", {})
    assert "Retry-After" not in response.headers
    # Rolled back, the receipt claim with it; never committed.
    assert conn.rolled_back is True and conn.committed is False
    assert conn.statements[:2] == ["set local statement_timeout = 4321", "set local lock_timeout = 1234"]


def test_a_statement_timeout_inside_a_command_is_service_busy_with_retry_after() -> None:
    conn = _FakeConn(psycopg.errors.QueryCanceled("canceling statement due to statement timeout"))
    response = _update_person(_production_app(_repo_over(conn)))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_busy"
    assert response.headers["Retry-After"] == "5"
    assert conn.rolled_back is True and conn.committed is False


def test_no_free_pooled_connection_is_service_busy_with_retry_after() -> None:
    response = _update_person(_production_app(_repo_over(_no_free_connection)))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_busy"
    assert response.headers["Retry-After"] == "5"


def test_a_pool_timeout_on_a_read_is_service_busy_too() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe")
    def probe() -> dict:
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    response = TestClient(app, raise_server_exceptions=False).get("/probe")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_busy"
    assert response.headers["Retry-After"] == "5"


def test_a_lost_unique_race_is_duplicate_naming_the_constraint_never_the_value() -> None:
    value = "duplicado-ficticio@example.test"
    conn = _FakeConn(_UniqueOnContactPoint(
        'duplicate key value violates unique constraint "contact_point_kind_value_key"\n'
        f"DETAIL:  Key (kind, value_norm)=(email, {value}) already exists."
    ))
    response = _update_person(_production_app(_repo_over(conn)))

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "duplicate"
    assert error["details"] == {"constraint": "contact_point_kind_value_key"}
    assert value not in response.text
    assert conn.rolled_back is True and conn.committed is False


def test_a_genuinely_unexpected_database_error_still_answers_500() -> None:
    conn = _FakeConn(psycopg.errors.UndefinedTable('relation "crm.person" does not exist'))
    response = _update_person(_production_app(_repo_over(conn)))

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "crm.person" not in response.text
    assert conn.rolled_back is True


def test_the_isolation_level_still_comes_first_for_the_audience_freeze() -> None:
    conn = _FakeConn(psycopg.errors.LockNotAvailable("lock timeout"), when="insert into platform.command_receipt")
    repo = V2AudienceFreezeRepository(_connect_to(conn), "fake-dsn", statement_timeout_ms=4321, lock_timeout_ms=1234)
    with pytest.raises(Exception) as raised:
        repo.execute(
            command_name="freeze-campaign-audience", operator=_operator(), fields={},
            idempotency_key="k", digest="d",
        )
    assert getattr(raised.value, "code", None) == "record_busy"
    assert conn.statements[:3] == [
        "set transaction isolation level repeatable read",
        "set local statement_timeout = 4321",
        "set local lock_timeout = 1234",
    ]


# ------------------------------------------------------------------ the setting


def test_the_lock_timeout_defaults_to_three_seconds() -> None:
    assert DEFAULT_COMMAND_LOCK_TIMEOUT_MS == 3_000
    assert Settings(_env_file=None).v2_lock_timeout_ms == 3_000


def _v2_env(monkeypatch: pytest.MonkeyPatch, database_url: str, *, lock_ms: int, statement_ms: int = 15_000) -> None:
    import secrets

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", database_url)
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORIGENLAB_AUTH_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.setenv("ORIGENLAB_V2_LOCK_TIMEOUT_MS", str(lock_ms))
    monkeypatch.setenv("ORIGENLAB_V2_STATEMENT_TIMEOUT_MS", str(statement_ms))
    monkeypatch.setenv("ORIGENLAB_V2_CRM_AUTHORING_ENABLED", "true")
    get_settings.cache_clear()


def test_create_app_hands_the_lock_timeout_to_every_command_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    from origenlab_api.main import create_app
    from origenlab_api.v2.command_core import CommandTransaction

    _v2_env(monkeypatch, "postgresql://u:p@127.0.0.1:54332/unused", lock_ms=250)
    for switch in ("COMMANDS", "CRM_AUTHORING", "CAMPAIGN_DRAFTS", "AUDIENCE_FREEZE", "CAMPAIGN_PLANNING",
                   "CAMPAIGN_BLOCKS", "UNSUBSCRIBE_APPLY"):
        monkeypatch.setenv(f"ORIGENLAB_V2_{switch}_ENABLED", "true")
    app = create_app()
    repos = {name: value for name, value in app.state._state.items() if isinstance(value, CommandTransaction)}
    assert set(repos) >= {
        "v2_command_repository", "v2_case_command_repository", "crm_authoring_repository",
        "campaign_draft_repository", "audience_freeze_repository", "campaign_planning_repository",
        "campaign_block_repository", "unsubscribe_repository",
    }
    assert {name: repo._lock_timeout_ms for name, repo in repos.items()} == {name: 250 for name in repos}


# ------------------------------------------------------------------ PostgreSQL itself


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def seeded(disposable_database):
    tag = uuid.uuid4().hex[:10]
    email = f"ventas-{tag}@example.test"
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status)"
            " values (gen_random_uuid(), %s, 'Ventas Ficticia', 'sales', 'active') returning id::text",
            (email,),
        )
        operator_id = cur.fetchone()[0]
        people = []
        for name in ("Persona Ficticia Uno", "Persona Ficticia Dos", "Persona Ficticia Tres"):
            cur.execute(
                "insert into crm.person (display_name, confirmation, confirmed_by_operator_id, status)"
                " values (%s, 'confirmed', %s::uuid, 'active') returning id::text, version",
                (name, operator_id),
            )
            people.append(cur.fetchone())
    return {"tag": tag, "email": email, "people": people}


def _headers(seeded: dict, key: str) -> dict[str, str]:
    return {OPERATOR_EMAIL_HEADER: seeded["email"], "Idempotency-Key": key}


def _receipts(dsn: str, key: str) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select count(*) from platform.command_receipt where idempotency_key = %s", (key,))
        return cur.fetchone()[0]


def _hold_person(dsn: str, person_id: str) -> psycopg.Connection:
    """Another transaction that has locked the person and has not finished."""
    holder = psycopg.connect(dsn)
    holder.execute("set role origenlab_owner")
    holder.execute("select id from crm.person where id = %s::uuid for update", (person_id,))
    return holder


@needs_db
def test_postgres_lock_timeout_answers_record_busy_and_the_same_key_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, disposable_database, seeded
) -> None:
    from origenlab_api.main import create_app

    person_id, version = seeded["people"][0]
    _v2_env(monkeypatch, runtime_dsn(disposable_database), lock_ms=300)
    body = {"person_id": person_id, "expected_version": version, "display_name": "Persona Renombrada", "note": "prueba"}
    key = uuid.uuid4().hex
    with TestClient(create_app()) as client:
        holder = _hold_person(disposable_database, person_id)
        try:
            started = time.monotonic()
            busy = client.post("/v2/commands/update-person", json=body, headers=_headers(seeded, key))
            waited = time.monotonic() - started
        finally:
            holder.rollback()
            holder.close()
        assert busy.status_code == 409, busy.text
        assert busy.json()["error"]["code"] == "record_busy"
        assert waited < 5, "lock_timeout, not statement_timeout, ended the wait"
        assert _receipts(disposable_database, key) == 0  # rolled back, receipt included

        # Nothing was written, so the identical retry under the same key simply runs.
        retried = client.post("/v2/commands/update-person", json=body, headers=_headers(seeded, key))
    assert retried.status_code == 200, retried.text
    assert retried.json()["replayed"] is False


@needs_db
def test_postgres_statement_timeout_inside_a_command_answers_service_busy(
    monkeypatch: pytest.MonkeyPatch, disposable_database, seeded
) -> None:
    from origenlab_api.main import create_app

    person_id, version = seeded["people"][1]
    # The statement timeout fires first: lock_timeout is deliberately longer here.
    _v2_env(monkeypatch, runtime_dsn(disposable_database), lock_ms=10_000, statement_ms=400)
    key = uuid.uuid4().hex
    with TestClient(create_app()) as client:
        holder = _hold_person(disposable_database, person_id)
        try:
            response = client.post(
                "/v2/commands/update-person",
                json={"person_id": person_id, "expected_version": version, "display_name": "Otra", "note": "prueba"},
                headers=_headers(seeded, key),
            )
        finally:
            holder.rollback()
            holder.close()
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "service_busy"
    assert response.headers["Retry-After"] == "5"
    assert _receipts(disposable_database, key) == 0


@needs_db
def test_postgres_unique_race_on_a_contact_point_answers_duplicate(
    monkeypatch: pytest.MonkeyPatch, disposable_database, seeded
) -> None:
    """Two subjects claim one new address at once: the loser gets 409 `duplicate`, not a 500.

    `add-contact-point` looks for the address first, but a row another transaction has inserted
    and not committed is invisible to that read, so both pass it; the loser's insert then waits
    on the winner's unique-index entry and fails when the winner commits.
    """
    from origenlab_api.main import create_app

    winner_id, loser_id = seeded["people"][2][0], seeded["people"][0][0]
    address = f"carrera-{seeded['tag']}@example.test"
    _v2_env(monkeypatch, runtime_dsn(disposable_database), lock_ms=10_000)
    key = uuid.uuid4().hex
    with TestClient(create_app()) as client:
        # The loser's version may have moved in an earlier test; read the current one.
        with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute("select version from crm.person where id = %s::uuid", (loser_id,))
            loser_version = cur.fetchone()[0]

        winner = psycopg.connect(disposable_database)
        winner.execute("set role origenlab_owner")
        winner.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation)"
            " values ('email', %s, %s, %s::uuid, 'personal', 'confirmed')",
            (address, address, winner_id),
        )
        answer: dict[str, object] = {}

        def _loser() -> None:
            answer["response"] = client.post(
                "/v2/commands/add-contact-point",
                json={"person_id": loser_id, "expected_version": loser_version, "kind": "email",
                      "value": address, "usage": "personal", "note": "prueba"},
                headers=_headers(seeded, key),
            )

        thread = threading.Thread(target=_loser)
        thread.start()
        try:
            with psycopg.connect(disposable_database, autocommit=True) as watch:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if watch.execute("select count(*) from pg_locks where not granted").fetchone()[0]:
                        break
                    time.sleep(0.05)
                else:  # pragma: no cover - a hang here is the bug the test exists to catch
                    pytest.fail("the loser's insert never waited on the winner's row")
            winner.commit()
        finally:
            thread.join(timeout=20)
            winner.close()

    response = answer["response"]
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "duplicate"
    assert error["details"] == {"constraint": "contact_point_kind_value_key"}
    assert address not in response.text
    assert _receipts(disposable_database, key) == 0
