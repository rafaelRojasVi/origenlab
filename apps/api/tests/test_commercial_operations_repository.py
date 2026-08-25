"""ARCH-3B3 SQL-level tests for durable commercial operations repository."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

import origenlab_api.repositories.postgres.commercial_operations as operations
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
    PostgresCommercialOperationsRepository,
)
from origenlab_api.settings import Settings


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


def _operator_state_row(
    *,
    version: int = 1,
    status: str = "confirmed",
) -> dict[str, Any]:
    return {
        "opportunity_id": "opp_1",
        "confirmation_status": status,
        "manual_stage": "quote_sent",
        "owner_key": "tatiana@origenlab.cl",
        "version": version,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }


def _activity_row() -> dict[str, Any]:
    return {
        "activity_id": "act_1",
        "opportunity_id": "opp_1",
        "account_id": None,
        "contact_id": None,
        "activity_type": "call",
        "occurred_at": NOW,
        "summary": "Called customer",
        "detail": None,
        "created_by": "tatiana@origenlab.cl",
        "created_at": NOW,
    }


def _task_row(
    *,
    status: str = "open",
    version: int = 1,
) -> dict[str, Any]:
    return {
        "task_id": "task_1",
        "opportunity_id": "opp_1",
        "account_id": None,
        "contact_id": None,
        "title": "Follow up",
        "status": status,
        "priority": "high",
        "due_at": NOW,
        "owner_key": "tatiana@origenlab.cl",
        "version": version,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "completed_at": NOW if status == "done" else None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _sql(sql: str) -> str:
    return " ".join(sql.split())


class FakeCursor:
    def __init__(
        self,
        fetchone_results: list[dict[str, Any] | None],
    ) -> None:
        self.fetchone_results = list(fetchone_results)
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.executed.append(
            (
                _sql(sql),
                dict(params or {}),
            )
        )

    def fetchone(self) -> dict[str, Any] | None:
        if not self.fetchone_results:
            raise AssertionError("Unexpected fetchone()")

        return self.fetchone_results.pop(0)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self, *, row_factory: object) -> FakeCursor:
        del row_factory
        return self._cursor


class FakeRows:
    dict_row = object()


class FakePg:
    rows = FakeRows()


def _repository(
    monkeypatch: pytest.MonkeyPatch,
    fetchone_results: list[dict[str, Any] | None],
) -> tuple[PostgresCommercialOperationsRepository, FakeCursor]:
    cursor = FakeCursor(fetchone_results)
    connection = FakeConnection(cursor)

    @contextmanager
    def fake_write_connection(
        settings: Settings,
    ) -> Iterator[FakeConnection]:
        del settings
        yield connection

    monkeypatch.setattr(
        operations,
        "postgres_write_connection",
        fake_write_connection,
    )
    monkeypatch.setattr(
        operations,
        "require_psycopg",
        lambda: FakePg(),
    )

    return (
        PostgresCommercialOperationsRepository(Settings()),
        cursor,
    )


def test_insert_operator_state_checks_pr3_opportunity_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"exists": 1},
            None,
            _operator_state_row(version=1),
        ],
    )

    result = repo.upsert_operator_state(
        opportunity_id="opp_1",
        confirmation_status="confirmed",
        manual_stage="quote_sent",
        owner_key="tatiana@origenlab.cl",
        operator="tatiana@origenlab.cl",
        expected_version=0,
    )

    assert result.version == 1

    statements = [sql for sql, _ in cursor.executed]

    assert "FROM api.v_commercial_opportunity" in statements[0]
    assert "FROM commercial.opportunity_operator_state" in statements[1]
    assert "FOR UPDATE" in statements[1]
    assert "INSERT INTO commercial.opportunity_operator_state" in statements[2]

    event_sql, event_params = cursor.executed[3]

    assert event_sql.startswith("INSERT INTO commercial.opportunity_operator_event")
    assert event_params["opportunity_id"] == "opp_1"
    assert event_params["event_type"] == "operator_state_changed"
    assert event_params["actor_key"] == "tatiana@origenlab.cl"

    payload = json.loads(event_params["payload"])

    assert payload["from"] is None
    assert payload["to"] == {
        "confirmation_status": "confirmed",
        "manual_stage": "quote_sent",
        "owner_key": "tatiana@origenlab.cl",
        "version": 1,
    }


def test_operator_state_missing_pr3_opportunity_fails_before_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(monkeypatch, [None])

    with pytest.raises(
        CommercialOperationNotFoundError,
        match="Commercial opportunity not found",
    ):
        repo.upsert_operator_state(
            opportunity_id="missing",
            confirmation_status="confirmed",
            manual_stage=None,
            owner_key=None,
            operator="tatiana@origenlab.cl",
            expected_version=0,
        )

    assert len(cursor.executed) == 1
    assert "FROM api.v_commercial_opportunity" in cursor.executed[0][0]

    assert not any(
        statement.startswith(("INSERT ", "UPDATE ", "DELETE "))
        for statement, _ in cursor.executed
    )


def test_operator_state_update_uses_optimistic_version_and_preserves_creator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"exists": 1},
            _operator_state_row(version=3),
            _operator_state_row(
                version=4,
                status="needs_review",
            ),
        ],
    )

    result = repo.upsert_operator_state(
        opportunity_id="opp_1",
        confirmation_status="needs_review",
        manual_stage="negotiation",
        owner_key="tatiana@origenlab.cl",
        operator="rafael@origenlab.cl",
        expected_version=3,
    )

    assert result.version == 4

    update_sql, params = cursor.executed[2]

    assert update_sql.startswith("UPDATE commercial.opportunity_operator_state")
    assert "version = version + 1" in update_sql
    assert "AND version = %(current_version)s" in update_sql
    assert "updated_by = %(operator)s" in update_sql
    assert "created_by =" not in update_sql

    assert params["current_version"] == 3
    assert params["operator"] == "rafael@origenlab.cl"

    event_sql, event_params = cursor.executed[3]

    assert event_sql.startswith("INSERT INTO commercial.opportunity_operator_event")

    payload = json.loads(event_params["payload"])

    assert payload["from"] == {
        "confirmation_status": "confirmed",
        "manual_stage": "quote_sent",
        "owner_key": "tatiana@origenlab.cl",
        "version": 3,
    }

    assert payload["to"] == {
        "confirmation_status": "needs_review",
        "manual_stage": "quote_sent",
        "owner_key": "tatiana@origenlab.cl",
        "version": 4,
    }


def test_operator_state_stale_version_fails_before_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"exists": 1},
            _operator_state_row(version=3),
        ],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="version conflict",
    ):
        repo.upsert_operator_state(
            opportunity_id="opp_1",
            confirmation_status="confirmed",
            manual_stage=None,
            owner_key=None,
            operator="tatiana@origenlab.cl",
            expected_version=2,
        )

    assert len(cursor.executed) == 2

    assert not any(sql.startswith("UPDATE ") for sql, _ in cursor.executed)

    assert not any(
        "commercial.opportunity_operator_event" in sql for sql, _ in cursor.executed
    )


def test_create_activity_writes_only_durable_activity_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"idempotency_key": "activity-key-1"},
            _activity_row(),
        ],
    )

    result = repo.create_activity(
        activity_id="act_1",
        opportunity_id="opp_1",
        account_id=None,
        contact_id=None,
        activity_type="call",
        occurred_at=NOW,
        summary="Called customer",
        detail=None,
        operator="tatiana@origenlab.cl",
        idempotency_key="activity-key-1",
        request_fingerprint="a" * 64,
    )

    assert result.activity_id == "act_1"

    assert len(cursor.executed) == 3

    claim_sql, claim_params = cursor.executed[0]
    activity_sql, params = cursor.executed[1]
    result_sql, result_params = cursor.executed[2]

    assert claim_sql.startswith("INSERT INTO commercial.command_idempotency")
    assert claim_params["command_kind"] == "activity_create"

    assert activity_sql.startswith("INSERT INTO commercial.activity")
    assert "CAST(%(opportunity_id)s AS text) IS NULL" in activity_sql
    assert params["operator"] == "tatiana@origenlab.cl"

    assert result_sql.startswith("UPDATE commercial.command_idempotency")
    assert result_params["result_id"] == "act_1"


def test_create_task_sets_creator_and_updater(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"idempotency_key": "task-key-1"},
            _task_row(),
        ],
    )

    result = repo.create_task(
        task_id="task_1",
        opportunity_id="opp_1",
        account_id=None,
        contact_id=None,
        title="Follow up",
        priority="high",
        due_at=NOW,
        owner_key="tatiana@origenlab.cl",
        operator="tatiana@origenlab.cl",
        idempotency_key="task-key-1",
        request_fingerprint="b" * 64,
    )

    assert result.version == 1

    assert len(cursor.executed) == 3

    claim_sql, claim_params = cursor.executed[0]
    task_sql, params = cursor.executed[1]
    result_sql, result_params = cursor.executed[2]

    assert claim_sql.startswith("INSERT INTO commercial.command_idempotency")
    assert claim_params["command_kind"] == "task_create"

    assert task_sql.startswith("INSERT INTO commercial.task")
    assert "CAST(%(opportunity_id)s AS text) IS NULL" in task_sql
    assert "created_by" in task_sql
    assert "updated_by" in task_sql
    assert params["operator"] == "tatiana@origenlab.cl"

    assert result_sql.startswith("UPDATE commercial.command_idempotency")
    assert result_params["result_id"] == "task_1"


def test_task_transition_requires_open_status_and_expected_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [_task_row(status="done", version=2)],
    )

    result = repo.transition_task(
        task_id="task_1",
        status="done",
        operator="rafael@origenlab.cl",
        expected_version=1,
    )

    assert result.status == "done"
    assert result.version == 2

    sql, params = cursor.executed[0]

    assert sql.startswith("UPDATE commercial.task")
    assert "AND status = 'open'" in sql
    assert "AND version = %(expected_version)s" in sql
    assert "version = version + 1" in sql
    assert "updated_by = %(operator)s" in sql
    assert "created_by =" not in sql

    assert params["expected_version"] == 1
    assert params["operator"] == "rafael@origenlab.cl"


def test_task_transition_missing_task_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            None,
            None,
        ],
    )

    with pytest.raises(
        CommercialOperationNotFoundError,
        match="Commercial task not found",
    ):
        repo.transition_task(
            task_id="missing",
            status="done",
            operator="tatiana@origenlab.cl",
            expected_version=1,
        )

    assert len(cursor.executed) == 2
    assert cursor.executed[0][0].startswith("UPDATE commercial.task")
    assert "FROM commercial.task" in cursor.executed[1][0]


def test_task_transition_stale_or_terminal_task_returns_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            None,
            {
                "task_id": "task_1",
                "status": "open",
                "version": 2,
            },
        ],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="no longer open or its version changed",
    ):
        repo.transition_task(
            task_id="task_1",
            status="cancelled",
            operator="tatiana@origenlab.cl",
            expected_version=1,
        )

    assert len(cursor.executed) == 2


def test_repository_source_has_no_pr3_mutation_sql() -> None:
    source_path = Path(operations.__file__)
    source = source_path.read_text(encoding="utf-8")

    forbidden = (
        "INSERT INTO commercial.opportunity ",
        "UPDATE commercial.opportunity SET",
        "DELETE FROM commercial.opportunity",
        "INSERT INTO commercial.opportunity_event",
        "UPDATE commercial.opportunity_event",
        "DELETE FROM commercial.opportunity_event",
        "INSERT INTO commercial.opportunity_evidence",
        "UPDATE commercial.opportunity_evidence",
        "DELETE FROM commercial.opportunity_evidence",
        "INSERT INTO commercial.opportunity_conflict",
        "UPDATE commercial.opportunity_conflict",
        "DELETE FROM commercial.opportunity_conflict",
    )

    for statement in forbidden:
        assert statement not in source


def test_activity_and_task_creation_guard_opportunity_refs_atomically() -> None:
    from origenlab_api.repositories.postgres import (
        commercial_operations as repository_module,
    )

    source = open(
        repository_module.__file__,
        encoding="utf-8",
    ).read()

    activity = source.split(
        "    def create_activity(",
        1,
    )[1].split(
        "    def create_task(",
        1,
    )[0]

    task = source.split(
        "    def create_task(",
        1,
    )[1].split(
        "    def transition_task(",
        1,
    )[0]

    for section in (activity, task):
        assert "FROM api.v_commercial_opportunity" in section
        assert "CAST(%(opportunity_id)s AS text) IS NULL" in section
        assert " OR EXISTS (" in section
        assert "CommercialOperationNotFoundError" in section


def test_activity_and_task_creation_do_not_write_pr3() -> None:
    from origenlab_api.repositories.postgres import (
        commercial_operations as repository_module,
    )

    source = open(
        repository_module.__file__,
        encoding="utf-8",
    ).read()

    assert "INSERT INTO api.v_commercial_opportunity" not in source
    assert "UPDATE api.v_commercial_opportunity" not in source
    assert "DELETE FROM api.v_commercial_opportunity" not in source


def test_activity_identical_idempotency_replay_returns_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_row = {
        **_activity_row(),
        "activity_id": "act_original",
    }

    repo, cursor = _repository(
        monkeypatch,
        [
            None,
            {
                "command_kind": "activity_create",
                "request_fingerprint": "a" * 64,
                "result_id": "act_original",
            },
            replay_row,
        ],
    )

    result = repo.create_activity(
        activity_id="act_new_should_not_write",
        opportunity_id="opp_1",
        account_id=None,
        contact_id=None,
        activity_type="call",
        occurred_at=NOW,
        summary="Called customer",
        detail=None,
        operator="tatiana@origenlab.cl",
        idempotency_key="activity-key",
        request_fingerprint="a" * 64,
    )

    assert result.activity_id == "act_original"

    statements = [sql for sql, _ in cursor.executed]

    assert statements[0].startswith("INSERT INTO commercial.command_idempotency")
    assert "FOR UPDATE" in statements[1]
    assert statements[2].startswith("SELECT * FROM commercial.activity")

    assert not any(
        sql.startswith("INSERT INTO commercial.activity") for sql in statements
    )


def test_activity_idempotency_key_reuse_with_changed_request_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            None,
            {
                "command_kind": "activity_create",
                "request_fingerprint": "b" * 64,
                "result_id": "act_original",
            },
        ],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="different request",
    ):
        repo.create_activity(
            activity_id="act_new",
            opportunity_id="opp_1",
            account_id=None,
            contact_id=None,
            activity_type="call",
            occurred_at=NOW,
            summary="Different request",
            detail=None,
            operator="tatiana@origenlab.cl",
            idempotency_key="activity-key",
            request_fingerprint="a" * 64,
        )

    assert len(cursor.executed) == 2


def test_task_identical_idempotency_replay_returns_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_row = {
        **_task_row(),
        "task_id": "task_original",
    }

    repo, cursor = _repository(
        monkeypatch,
        [
            None,
            {
                "command_kind": "task_create",
                "request_fingerprint": "c" * 64,
                "result_id": "task_original",
            },
            replay_row,
        ],
    )

    result = repo.create_task(
        task_id="task_new_should_not_write",
        opportunity_id="opp_1",
        account_id=None,
        contact_id=None,
        title="Follow up",
        priority="high",
        due_at=NOW,
        owner_key="tatiana@origenlab.cl",
        operator="tatiana@origenlab.cl",
        idempotency_key="task-key",
        request_fingerprint="c" * 64,
    )

    assert result.task_id == "task_original"

    statements = [sql for sql, _ in cursor.executed]

    assert statements[2].startswith("SELECT * FROM commercial.task")

    assert not any(
        sql.startswith("INSERT INTO commercial.task (") for sql in statements
    )


def test_idempotency_key_cannot_cross_command_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _repository(
        monkeypatch,
        [
            None,
            {
                "command_kind": "activity_create",
                "request_fingerprint": "d" * 64,
                "result_id": "act_existing",
            },
        ],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="different request",
    ):
        repo.create_task(
            task_id="task_new",
            opportunity_id="opp_1",
            account_id=None,
            contact_id=None,
            title="Follow up",
            priority="normal",
            due_at=None,
            owner_key=None,
            operator="tatiana@origenlab.cl",
            idempotency_key="shared-key",
            request_fingerprint="d" * 64,
        )
