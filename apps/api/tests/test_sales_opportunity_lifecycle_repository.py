"""CRM-2 repository tests for durable sales-opportunity lifecycle changes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Any, Iterator

import pytest

import origenlab_api.repositories.postgres.commercial_operations as operations
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
    PostgresCommercialOperationsRepository,
)
from origenlab_api.settings import Settings


NOW = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)


def _row(
    *,
    stage: str = "new",
    version: int = 1,
) -> dict[str, Any]:
    return {
        "sales_opportunity_id": "sales_1",
        "source_kind": "pr3",
        "source_opportunity_id": "o_1",
        "account_id": "a_1",
        "primary_contact_id": "c_1",
        "title": "Centrífuga refrigerada",
        "stage": stage,
        "owner_key": "tatiana@origenlab.cl",
        "version": version,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }


def _sql(sql: str) -> str:
    return " ".join(sql.split())


class FakeCursor:
    def __init__(
        self,
        results: list[dict[str, Any] | None],
    ) -> None:
        self.results = list(results)
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
        if not self.results:
            raise AssertionError("Unexpected fetchone()")

        return self.results.pop(0)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(
        self,
        *,
        row_factory: object,
    ) -> FakeCursor:
        del row_factory
        return self._cursor


class FakeRows:
    dict_row = object()


class FakePg:
    rows = FakeRows()


def _repo(
    monkeypatch: pytest.MonkeyPatch,
    results: list[dict[str, Any] | None],
) -> tuple[
    PostgresCommercialOperationsRepository,
    FakeCursor,
]:
    cursor = FakeCursor(results)
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
    monkeypatch.setattr(
        operations,
        "_utcnow",
        lambda: NOW,
    )

    return (
        PostgresCommercialOperationsRepository(Settings()),
        cursor,
    )


def _transition(
    repo: PostgresCommercialOperationsRepository,
    *,
    stage: str = "qualifying",
    expected_version: int = 1,
):
    return repo.transition_sales_opportunity_stage(
        sales_opportunity_id="sales_1",
        stage=stage,
        operator="tatiana@origenlab.cl",
        expected_version=expected_version,
    )


def test_stage_transition_updates_version_and_appends_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [
            _row(stage="new", version=1),
            _row(stage="qualifying", version=2),
        ],
    )

    result = _transition(repo)

    assert result.stage == "qualifying"
    assert result.version == 2
    assert result.updated_by == "tatiana@origenlab.cl"

    statements = [sql for sql, _ in cursor.executed]

    assert len(statements) == 3
    assert statements[0].startswith("SELECT * FROM commercial.sales_opportunity")
    assert "FOR UPDATE" in statements[0]
    assert statements[1].startswith("UPDATE commercial.sales_opportunity")
    assert statements[2].startswith("INSERT INTO commercial.sales_opportunity_event")

    update_params = cursor.executed[1][1]

    assert update_params["stage"] == "qualifying"
    assert update_params["current_version"] == 1
    assert update_params["operator"] == "tatiana@origenlab.cl"

    event_params = cursor.executed[2][1]
    payload = json.loads(event_params["payload"])

    assert event_params["actor_key"] == "tatiana@origenlab.cl"
    assert payload == {
        "from": {
            "stage": "new",
            "version": 1,
        },
        "to": {
            "stage": "qualifying",
            "version": 2,
        },
    }


def test_missing_sales_opportunity_is_404_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [None],
    )

    with pytest.raises(
        CommercialOperationNotFoundError,
        match="Sales opportunity not found",
    ):
        _transition(repo)

    assert len(cursor.executed) == 1


def test_stale_expected_version_conflicts_before_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [_row(version=2)],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="version conflict",
    ):
        _transition(
            repo,
            expected_version=1,
        )

    assert len(cursor.executed) == 1


def test_same_stage_is_not_a_silent_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [_row(stage="qualifying")],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="already in the requested stage",
    ):
        _transition(
            repo,
            stage="qualifying",
        )

    assert len(cursor.executed) == 1


@pytest.mark.parametrize(
    "terminal_stage",
    [
        "won",
        "lost",
    ],
)
def test_terminal_stage_cannot_be_changed(
    monkeypatch: pytest.MonkeyPatch,
    terminal_stage: str,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [_row(stage=terminal_stage)],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="terminal",
    ):
        _transition(
            repo,
            stage="qualifying",
        )

    assert len(cursor.executed) == 1
