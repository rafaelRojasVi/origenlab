"""ARCH-3B6 read-only CRM repository tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest

import origenlab_api.repositories.postgres.commercial_operations_read as read_repo
from origenlab_api.repositories.postgres.commercial_operations_read import (
    PostgresCommercialOperationsReadRepository,
)
from origenlab_api.settings import Settings


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        many: list[dict[str, Any]] | None = None,
    ) -> None:
        self.one = one
        self.many = many or []
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
        normalized = " ".join(sql.split())
        self.executed.append(
            (
                normalized,
                dict(params or {}),
            )
        )

    def fetchone(self) -> dict[str, Any] | None:
        return self.one

    def fetchall(self) -> list[dict[str, Any]]:
        return self.many


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


def _repo(
    monkeypatch: pytest.MonkeyPatch,
    cursor: FakeCursor,
) -> PostgresCommercialOperationsReadRepository:
    connection = FakeConnection(cursor)

    @contextmanager
    def fake_connection(
        settings: Settings,
    ) -> Iterator[FakeConnection]:
        del settings
        yield connection

    monkeypatch.setattr(
        read_repo,
        "postgres_connection",
        fake_connection,
    )
    monkeypatch.setattr(
        read_repo,
        "require_psycopg",
        lambda: FakePg(),
    )

    return PostgresCommercialOperationsReadRepository(Settings())


def test_operator_state_reads_api_view_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        one={
            "opportunity_id": "o_" + ("a" * 32),
            "confirmation_status": "confirmed",
            "manual_stage": None,
            "owner_key": None,
            "version": 1,
            "created_by": "tatiana@origenlab.cl",
            "updated_by": "tatiana@origenlab.cl",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )

    result = _repo(
        monkeypatch,
        cursor,
    ).get_operator_state("o_" + ("a" * 32))

    assert result is not None
    assert result.version == 1

    sql = cursor.executed[0][0]
    assert "FROM api.v_commercial_opportunity_operator_state" in sql


def test_missing_operator_state_is_normal_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(one=None)

    result = _repo(
        monkeypatch,
        cursor,
    ).get_operator_state("o_" + ("a" * 32))

    assert result is None


def test_activity_list_reads_view_in_reverse_chronological_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        many=[
            {
                "activity_id": "act_1",
                "opportunity_id": "o_" + ("a" * 32),
                "account_id": None,
                "contact_id": None,
                "activity_type": "call",
                "occurred_at": NOW,
                "summary": "Called",
                "detail": None,
                "created_by": "tatiana@origenlab.cl",
                "created_at": NOW,
            }
        ]
    )

    result = _repo(
        monkeypatch,
        cursor,
    ).list_activities_for_opportunity("o_" + ("a" * 32))

    assert len(result) == 1

    sql = cursor.executed[0][0]
    assert "FROM api.v_commercial_activity" in sql
    assert "ORDER BY occurred_at DESC" in sql


def test_task_list_reads_view_with_open_tasks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        many=[
            {
                "task_id": "task_" + ("b" * 32),
                "opportunity_id": "o_" + ("a" * 32),
                "account_id": None,
                "contact_id": None,
                "title": "Follow up",
                "status": "open",
                "priority": "normal",
                "due_at": NOW,
                "owner_key": None,
                "version": 1,
                "created_by": "tatiana@origenlab.cl",
                "updated_by": "tatiana@origenlab.cl",
                "completed_at": None,
                "created_at": NOW,
                "updated_at": NOW,
            }
        ]
    )

    result = _repo(
        monkeypatch,
        cursor,
    ).list_tasks_for_opportunity("o_" + ("a" * 32))

    assert len(result) == 1

    sql = cursor.executed[0][0]
    assert "FROM api.v_commercial_task" in sql
    assert "WHEN 'open' THEN 0" in sql


def test_activity_list_can_read_by_sales_opportunity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        many=[
            {
                "activity_id": "act_1",
                "opportunity_id": "opp_1",
                "sales_opportunity_id": "sales_1",
                "account_id": None,
                "contact_id": None,
                "activity_type": "call",
                "occurred_at": NOW,
                "summary": "Called",
                "detail": None,
                "created_by": "tatiana@origenlab.cl",
                "created_at": NOW,
            }
        ]
    )

    result = _repo(
        monkeypatch,
        cursor,
    ).list_activities_for_sales_opportunity("sales_1")

    assert len(result) == 1
    assert result[0].sales_opportunity_id == "sales_1"

    sql, params = cursor.executed[0]

    assert "FROM api.v_commercial_activity" in sql
    assert "WHERE sales_opportunity_id =" in sql
    assert params["sales_opportunity_id"] == "sales_1"


def test_task_list_can_read_by_sales_opportunity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor(
        many=[
            {
                "task_id": "task_1",
                "opportunity_id": "opp_1",
                "sales_opportunity_id": "sales_1",
                "account_id": None,
                "contact_id": None,
                "title": "Follow up",
                "status": "open",
                "priority": "normal",
                "due_at": NOW,
                "owner_key": None,
                "version": 1,
                "created_by": "tatiana@origenlab.cl",
                "updated_by": "tatiana@origenlab.cl",
                "completed_at": None,
                "created_at": NOW,
                "updated_at": NOW,
            }
        ]
    )

    result = _repo(
        monkeypatch,
        cursor,
    ).list_tasks_for_sales_opportunity("sales_1")

    assert len(result) == 1
    assert result[0].sales_opportunity_id == "sales_1"

    sql, params = cursor.executed[0]

    assert "FROM api.v_commercial_task" in sql
    assert "WHERE sales_opportunity_id =" in sql
    assert params["sales_opportunity_id"] == "sales_1"
    assert "WHEN 'open' THEN 0" in sql


def test_read_repository_contains_no_mutation_sql() -> None:
    source = open(
        read_repo.__file__,
        encoding="utf-8",
    ).read()

    for token in (
        "INSERT INTO ",
        "UPDATE commercial.",
        "DELETE FROM ",
    ):
        assert token not in source


def test_work_queue_reads_open_tasks_and_review_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_row = {
        "task_id": "task_" + ("b" * 32),
        "opportunity_id": "o_" + ("a" * 32),
        "sales_opportunity_id": "sales_1",
        "account_id": "a_1",
        "contact_id": "c_1",
        "title": "Llamar cliente",
        "status": "open",
        "priority": "urgent",
        "due_at": NOW,
        "owner_key": None,
        "version": 1,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "completed_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        "contact_display_email": "buyer@example.cl",
        "account_display_domain": "example.cl",
        "canonical_stage": "quote_sent",
        "machine_review_status": "needs_review",
    }

    opportunity_row = {
        "opportunity_id": "o_" + ("a" * 32),
        "contact_display_email": "buyer@example.cl",
        "account_display_domain": "example.cl",
        "canonical_stage": "quote_sent",
        "machine_review_status": "needs_review",
        "confirmation_status": None,
        "manual_stage": None,
        "owner_key": None,
        "operator_state_version": None,
    }

    cursor = FakeCursor()

    results = [
        [task_row],
        [opportunity_row],
        [opportunity_row],
    ]

    def fetchall() -> list[dict[str, Any]]:
        if not results:
            raise AssertionError("Unexpected fetchall()")
        return results.pop(0)

    cursor.fetchall = fetchall  # type: ignore[method-assign]

    repo = _repo(
        monkeypatch,
        cursor,
    )

    (
        tasks,
        review,
        quotes,
    ) = repo.get_work_queue(limit=25)

    assert len(tasks) == 1
    assert tasks[0].task.status == "open"
    assert tasks[0].task.sales_opportunity_id == "sales_1"
    assert tasks[0].account_display_domain == "example.cl"

    assert len(review) == 1
    assert review[0].canonical_stage == "quote_sent"

    assert len(quotes) == 1

    statements = [sql for sql, _ in cursor.executed]

    assert len(statements) == 3

    assert "FROM api.v_commercial_task AS t" in statements[0]
    assert "t.status = 'open'" in statements[0]

    assert "api.v_commercial_opportunity_operator_state" in statements[1]
    assert "o.review_status = 'needs_review'" in statements[1]

    assert "o.canonical_stage = 'quote_sent'" in statements[2]


def test_work_queue_read_source_still_has_no_mutations() -> None:
    source = open(
        read_repo.__file__,
        encoding="utf-8",
    ).read()

    assert "INSERT INTO " not in source
    assert "UPDATE commercial." not in source
    assert "DELETE FROM " not in source
