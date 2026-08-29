"""CRM-1 repository tests for PR3 -> durable sales-opportunity promotion."""

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


NOW = datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc)


def _sales_row(
    *,
    sales_opportunity_id: str = "sales_1",
) -> dict[str, Any]:
    return {
        "sales_opportunity_id": sales_opportunity_id,
        "source_kind": "pr3",
        "source_opportunity_id": "o_1",
        "account_id": "a_1",
        "primary_contact_id": "c_1",
        "title": "Centrífuga refrigerada",
        "stage": "new",
        "owner_key": "tatiana@origenlab.cl",
        "version": 1,
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
        fetchone_results: list[dict[str, Any] | None],
    ) -> None:
        self.results = list(fetchone_results)
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


def _promote(
    repo: PostgresCommercialOperationsRepository,
):
    return repo.promote_sales_opportunity(
        sales_opportunity_id="sales_1",
        source_opportunity_id="o_1",
        title="Centrífuga refrigerada",
        owner_key="tatiana@origenlab.cl",
        operator="tatiana@origenlab.cl",
        idempotency_key="promote-key",
        request_fingerprint="a" * 64,
    )


def test_promotion_snapshots_pr3_identity_and_writes_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [
            {"idempotency_key": "promote-key"},
            {
                "account_id": "a_1",
                "primary_contact_id": "c_1",
                "contact_display_email": None,
                "account_display_domain": None,
            },
            None,  # organization_source lookup: no existing org
            _sales_row(),
        ],
    )

    result = _promote(repo)

    assert result.stage == "new"
    assert result.account_id == "a_1"
    assert result.primary_contact_id == "c_1"

    statements = [sql for sql, _ in cursor.executed]

    assert statements[0].startswith("INSERT INTO commercial.command_idempotency")
    assert "FROM api.v_commercial_opportunity" in statements[1]
    assert "FROM commercial.organization_source" in statements[2]
    assert statements[3].startswith("INSERT INTO commercial.sales_opportunity")
    assert statements[4].startswith("INSERT INTO commercial.sales_opportunity_event")
    assert statements[5].startswith("UPDATE commercial.command_idempotency")

    claim_params = cursor.executed[0][1]

    assert claim_params["command_kind"] == "sales_opportunity_promote"

    insert_sql, insert_params = cursor.executed[3]

    assert "'pr3'" in insert_sql
    assert "'new'" in insert_sql
    assert insert_params["account_id"] == "a_1"
    assert insert_params["primary_contact_id"] == "c_1"
    assert insert_params["organization_id"] is None
    assert insert_params["primary_crm_contact_id"] is None

    event_params = cursor.executed[4][1]
    payload = json.loads(event_params["payload"])

    assert event_params["actor_key"] == "tatiana@origenlab.cl"
    assert payload["source"] == {
        "kind": "pr3",
        "opportunity_id": "o_1",
    }
    assert payload["snapshot"] == {
        "account_id": "a_1",
        "primary_contact_id": "c_1",
        "organization_id": None,
        "primary_crm_contact_id": None,
    }


def test_missing_pr3_source_fails_before_sales_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [
            {"idempotency_key": "promote-key"},
            None,
        ],
    )

    with pytest.raises(
        CommercialOperationNotFoundError,
        match="Commercial opportunity not found",
    ):
        _promote(repo)

    statements = [sql for sql, _ in cursor.executed]

    assert len(statements) == 2
    assert not any(
        sql.startswith("INSERT INTO commercial.sales_opportunity") for sql in statements
    )


def test_duplicate_pr3_promotion_returns_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [
            {"idempotency_key": "promote-key"},
            {
                "account_id": "a_1",
                "primary_contact_id": "c_1",
                "contact_display_email": None,
                "account_display_domain": None,
            },
            None,  # organization_source lookup: no existing org
            None,  # sales_opportunity INSERT: swallowed by ON CONFLICT
            {
                "sales_opportunity_id": "sales_existing",
            },
        ],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="already promoted",
    ):
        _promote(repo)

    assert len(cursor.executed) == 5


def test_identical_idempotency_replay_returns_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repo(
        monkeypatch,
        [
            None,
            {
                "command_kind": "sales_opportunity_promote",
                "request_fingerprint": "a" * 64,
                "result_id": "sales_original",
            },
            _sales_row(sales_opportunity_id="sales_original"),
        ],
    )

    result = _promote(repo)

    assert result.sales_opportunity_id == "sales_original"

    statements = [sql for sql, _ in cursor.executed]

    assert len(statements) == 3
    assert statements[2].startswith("SELECT * FROM commercial.sales_opportunity")

    assert not any(
        sql.startswith("INSERT INTO commercial.sales_opportunity (")
        for sql in statements
    )


def test_idempotency_key_changed_request_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _repo(
        monkeypatch,
        [
            None,
            {
                "command_kind": "sales_opportunity_promote",
                "request_fingerprint": "b" * 64,
                "result_id": "sales_original",
            },
        ],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="different request",
    ):
        _promote(repo)
