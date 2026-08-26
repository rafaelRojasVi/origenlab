"""CRM-1 read tests for durable sales opportunities."""

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


NOW = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(
        self,
        one: dict[str, Any] | None,
    ) -> None:
        self.one = one
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
                " ".join(sql.split()),
                dict(params or {}),
            )
        )

    def fetchone(self) -> dict[str, Any] | None:
        return self.one


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_value = cursor

    def cursor(
        self,
        *,
        row_factory: object,
    ) -> FakeCursor:
        del row_factory
        return self.cursor_value


class FakeRows:
    dict_row = object()


class FakePg:
    rows = FakeRows()


def _repo(
    monkeypatch: pytest.MonkeyPatch,
    row: dict[str, Any] | None,
) -> tuple[
    PostgresCommercialOperationsReadRepository,
    FakeCursor,
]:
    cursor = FakeCursor(row)
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

    return (
        PostgresCommercialOperationsReadRepository(Settings()),
        cursor,
    )


def test_sales_opportunity_reads_api_view_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        {
            "sales_opportunity_id": "sales_1",
            "source_kind": "pr3",
            "source_opportunity_id": "o_1",
            "account_id": "a_1",
            "primary_contact_id": "c_1",
            "title": "Centrífuga",
            "stage": "new",
            "owner_key": "tatiana@origenlab.cl",
            "version": 1,
            "created_by": "tatiana@origenlab.cl",
            "updated_by": "tatiana@origenlab.cl",
            "created_at": NOW,
            "updated_at": NOW,
        },
    )

    result = repo.get_sales_opportunity("sales_1")

    assert result is not None
    assert result.stage == "new"

    sql = cursor.executed[0][0]

    assert "FROM api.v_commercial_sales_opportunity" in sql
    assert "FROM commercial.sales_opportunity" not in sql


def test_missing_sales_opportunity_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _repo(
        monkeypatch,
        None,
    )

    assert repo.get_sales_opportunity("missing") is None
