"""Repository tests for the durable sales-opportunity board list query."""

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


NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, *, one: dict[str, Any] | None = None, many: list[dict[str, Any]] | None = None) -> None:
        self.one = one
        self.many = many or []
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.executed.append((" ".join(sql.split()), dict(params or {})))

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


def _repo(monkeypatch: pytest.MonkeyPatch, cursor: FakeCursor) -> PostgresCommercialOperationsReadRepository:
    connection = FakeConnection(cursor)

    @contextmanager
    def fake_connection(settings: Settings) -> Iterator[FakeConnection]:
        del settings
        yield connection

    monkeypatch.setattr(read_repo, "postgres_connection", fake_connection)
    monkeypatch.setattr(read_repo, "require_psycopg", lambda: FakePg())

    return PostgresCommercialOperationsReadRepository(Settings())


def _row(**overrides: Any) -> dict[str, Any]:
    base = {
        "sales_opportunity_id": "sales_1",
        "source_kind": "pr3",
        "source_opportunity_id": "o_1",
        "account_id": "a_1",
        "primary_contact_id": "c_1",
        "organization_id": None,
        "primary_crm_contact_id": None,
        "title": "Centrífuga refrigerada",
        "stage": "qualifying",
        "owner_key": "tatiana@origenlab.cl",
        "version": 2,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
        "stage_updated_at": NOW,
        "contact_display_email": "buyer@example.cl",
        "account_display_domain": "example.cl",
        "open_task_count": 1,
        "next_task_id": "task_1",
        "next_task_title": "Llamar cliente",
        "next_task_due_at": NOW,
    }
    base.update(overrides)
    return base


def test_list_returns_items_and_total_count(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(one={"total": 3}, many=[_row()])

    items, total = _repo(monkeypatch, cursor).list_sales_opportunities(
        stages=["qualifying"],
        limit=100,
        offset=0,
    )

    assert total == 3
    assert len(items) == 1
    assert items[0].sales_opportunity_id == "sales_1"
    assert items[0].stage_updated_at == NOW
    assert items[0].open_task_count == 1
    assert items[0].next_task_title == "Llamar cliente"

    count_sql, count_params = cursor.executed[0]
    list_sql, list_params = cursor.executed[1]

    assert "FROM api.v_commercial_sales_opportunity so" in count_sql
    assert "FROM api.v_commercial_sales_opportunity so" in list_sql
    assert "LEFT JOIN api.v_commercial_opportunity o" in list_sql
    assert "LEFT JOIN LATERAL" in list_sql
    assert "api.v_commercial_sales_opportunity_event e" in list_sql
    assert "ORDER BY so.updated_at DESC" in list_sql
    assert count_params["stages"] == ["qualifying"]
    assert list_params["limit"] == 100
    assert list_params["offset"] == 0


def test_list_survives_missing_pr3_and_task_context(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row(
        contact_display_email=None,
        account_display_domain=None,
        open_task_count=0,
        next_task_id=None,
        next_task_title=None,
        next_task_due_at=None,
    )
    cursor = FakeCursor(one={"total": 1}, many=[row])

    items, total = _repo(monkeypatch, cursor).list_sales_opportunities()

    assert total == 1
    assert items[0].contact_display_email is None
    assert items[0].open_task_count == 0
    assert items[0].next_task_id is None


def test_list_passes_source_opportunity_id_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor(one={"total": 0}, many=[])

    _repo(monkeypatch, cursor).list_sales_opportunities(
        source_opportunity_ids=["o_1", "o_2"],
    )

    _, list_params = cursor.executed[1]
    assert list_params["source_opportunity_ids"] == ["o_1", "o_2"]
    assert list_params["stages"] is None
    assert list_params["owner_key"] is None


def test_list_repository_contains_no_mutation_sql() -> None:
    source = open(read_repo.__file__, encoding="utf-8").read()
    for token in ("INSERT INTO ", "UPDATE commercial.", "DELETE FROM "):
        assert token not in source
