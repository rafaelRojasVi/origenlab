"""CRM-Q1 SQL-level tests for the durable customer-quote repository."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Any, Iterator

import pytest

import origenlab_api.repositories.postgres.customer_quotes as quotes_module
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    PostgresCustomerQuoteRepository,
    QuoteNumberingConfig,
    QuoteNumberingNotConfiguredError,
)
from origenlab_api.settings import Settings


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)

NUMBERING = QuoteNumberingConfig(
    prefix="CN",
    pad_width=6,
    seed_next_serial=11729,
)


def _quote_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "quote_id": "quote_" + "a" * 32,
        "sales_opportunity_id": "sales_" + "b" * 32,
        "quote_number": "CN011729",
        "status": "draft",
        "version": 1,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def _quote_with_title_row(**overrides: Any) -> dict[str, Any]:
    row = _quote_row(**overrides)
    row["sales_opportunity_title"] = "Centrífuga CEAF"
    return row


def _revision_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "quote_id": "quote_" + "a" * 32,
        "revision_number": 1,
        "template_reference": "template-file-id-1",
        "status": "draft",
        "created_by": "tatiana@origenlab.cl",
        "created_at": NOW,
    }
    row.update(overrides)
    return row


def _workspace_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "quote_id": "quote_" + "a" * 32,
        "provider": "google_drive",
        "provisioning_status": "pending",
        "folder_id": None,
        "folder_web_url": None,
        "sheet_file_id": None,
        "sheet_web_url": None,
        "failure_category": None,
        "attempt_count": 0,
        "version": 1,
        "requested_at": None,
        "completed_at": None,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


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
) -> tuple[PostgresCustomerQuoteRepository, FakeCursor]:
    cursor = FakeCursor(fetchone_results)
    connection = FakeConnection(cursor)

    @contextmanager
    def fake_write_connection(
        settings: Settings,
    ) -> Iterator[FakeConnection]:
        del settings
        yield connection

    monkeypatch.setattr(
        quotes_module,
        "postgres_write_connection",
        fake_write_connection,
    )
    monkeypatch.setattr(
        quotes_module,
        "require_psycopg",
        lambda: FakePg(),
    )

    return (
        PostgresCustomerQuoteRepository(Settings()),
        cursor,
    )


QUOTE_ID = "quote_" + "a" * 32
SALES_ID = "sales_" + "b" * 32


def _create_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "sales_opportunity_id": SALES_ID,
        "operator": "tatiana@origenlab.cl",
        "idempotency_key": "quote-create-1",
        "request_fingerprint": "f" * 64,
        "numbering": NUMBERING,
        "template_reference": "template-file-id-1",
    }
    kwargs.update(overrides)
    return kwargs


def test_create_quote_allocates_number_atomically_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"idempotency_key": "quote-create-1"},  # fresh claim
            {"sales_opportunity_id": SALES_ID, "title": "Centrífuga CEAF"},
            {"prefix": "CN", "pad_width": 6, "allocated_serial": 11729},
            _quote_row(),
            _revision_row(),
            _workspace_row(),
        ],
    )

    bundle = repo.create_quote(**_create_kwargs())

    assert bundle.quote.quote_number == "CN011729"
    assert bundle.revision.revision_number == 1
    assert bundle.workspace.provisioning_status == "pending"
    assert bundle.sales_opportunity_title == "Centrífuga CEAF"

    statements = [sql for sql, _ in cursor.executed]

    assert statements[0].startswith(
        "INSERT INTO commercial.command_idempotency"
    )
    assert "FROM commercial.sales_opportunity" in statements[1]
    assert statements[2].startswith(
        "INSERT INTO commercial.customer_quote_number_series"
    )
    assert "ON CONFLICT ( series_key ) DO NOTHING" in statements[2]

    allocate_sql, allocate_params = cursor.executed[3]

    assert allocate_sql.startswith(
        "UPDATE commercial.customer_quote_number_series"
    )
    assert "next_serial = next_serial + 1" in allocate_sql
    assert "RETURNING" in allocate_sql
    assert "next_serial - 1 AS allocated_serial" in allocate_sql
    assert allocate_params["series_key"] == "CN"

    insert_sql, insert_params = cursor.executed[4]

    assert insert_sql.startswith("INSERT INTO commercial.customer_quote (")
    assert insert_params["quote_number"] == "CN011729"
    assert insert_params["sales_opportunity_id"] == SALES_ID

    assert cursor.executed[5][0].startswith(
        "INSERT INTO commercial.customer_quote_revision"
    )
    assert cursor.executed[5][1]["revision_number"] == 1
    assert cursor.executed[5][1]["template_reference"] == "template-file-id-1"

    assert cursor.executed[6][0].startswith(
        "INSERT INTO commercial.customer_quote_drive_workspace"
    )

    event_sql, event_params = cursor.executed[7]

    assert event_sql.startswith("INSERT INTO commercial.customer_quote_event")
    assert event_params["event_type"] == "quote_created"
    assert event_params["actor_key"] == "tatiana@origenlab.cl"

    payload = json.loads(event_params["payload"])

    assert payload["quote_number"] == "CN011729"
    assert payload["sales_opportunity_id"] == SALES_ID

    assert cursor.executed[8][0].startswith(
        "UPDATE commercial.command_idempotency"
    )
    assert cursor.executed[8][1]["result_id"] == QUOTE_ID


def test_create_quote_number_never_selects_serial_before_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"idempotency_key": "quote-create-1"},
            {"sales_opportunity_id": SALES_ID, "title": "Centrífuga CEAF"},
            {"prefix": "CN", "pad_width": 6, "allocated_serial": 11729},
            _quote_row(),
            _revision_row(),
            _workspace_row(),
        ],
    )

    repo.create_quote(**_create_kwargs())

    for statement, _ in cursor.executed:
        # The allocator is a single row-locked UPDATE ... RETURNING; a
        # read-then-increment (or any MAX()) would be a race.
        assert "MAX(" not in statement.upper().replace(" ", "")
        if statement.startswith("SELECT"):
            assert "customer_quote_number_series" not in statement


def test_create_quote_replays_existing_result_for_same_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            None,  # claim lost: existing key
            {
                "command_kind": "customer_quote_create",
                "request_fingerprint": "f" * 64,
                "result_id": QUOTE_ID,
            },
            _quote_with_title_row(),
            _revision_row(),
            _workspace_row(provisioning_status="ready"),
        ],
    )

    bundle = repo.create_quote(**_create_kwargs())

    assert bundle.quote.quote_id == QUOTE_ID
    assert bundle.workspace.provisioning_status == "ready"

    assert not any(
        statement.startswith("INSERT INTO commercial.customer_quote (")
        for statement, _ in cursor.executed
    )
    assert not any(
        statement.startswith(
            "UPDATE commercial.customer_quote_number_series"
        )
        for statement, _ in cursor.executed
    )


def test_create_quote_missing_sales_opportunity_fails_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"idempotency_key": "quote-create-1"},
            None,  # sales opportunity lookup
        ],
    )

    with pytest.raises(
        CommercialOperationNotFoundError,
        match="Sales opportunity not found",
    ):
        repo.create_quote(**_create_kwargs())

    assert not any(
        statement.startswith("INSERT INTO commercial.customer_quote")
        for statement, _ in cursor.executed
    )


def test_create_quote_without_numbering_config_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"idempotency_key": "quote-create-1"},
            {"sales_opportunity_id": SALES_ID, "title": "Centrífuga CEAF"},
        ],
    )

    with pytest.raises(QuoteNumberingNotConfiguredError):
        repo.create_quote(**_create_kwargs(numbering=None))

    assert not any(
        "customer_quote_number_series" in statement
        or statement.startswith("INSERT INTO commercial.customer_quote")
        for statement, _ in cursor.executed
    )


def test_begin_drive_provision_attempt_bumps_version_and_records_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            _workspace_row(
                attempt_count=1,
                version=2,
                requested_at=NOW,
            ),
        ],
    )

    workspace = repo.begin_drive_provision_attempt(
        quote_id=QUOTE_ID,
        operator="tatiana@origenlab.cl",
        expected_version=1,
    )

    assert workspace.attempt_count == 1
    assert workspace.version == 2

    update_sql, update_params = cursor.executed[0]

    assert update_sql.startswith(
        "UPDATE commercial.customer_quote_drive_workspace"
    )
    assert "attempt_count = attempt_count + 1" in update_sql
    assert "version = version + 1" in update_sql
    assert "AND version = %(expected_version)s" in update_sql
    assert "AND provisioning_status <> 'ready'" in update_sql
    assert update_params["expected_version"] == 1

    event_sql, event_params = cursor.executed[1]

    assert event_sql.startswith("INSERT INTO commercial.customer_quote_event")
    assert event_params["event_type"] == "drive_provision_requested"


def test_begin_drive_provision_attempt_conflicts_on_stale_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            None,  # guarded update matched nothing
            _workspace_row(version=3),
        ],
    )

    with pytest.raises(CommercialOperationConflictError):
        repo.begin_drive_provision_attempt(
            quote_id=QUOTE_ID,
            operator="tatiana@origenlab.cl",
            expected_version=1,
        )

    assert not any(
        statement.startswith("INSERT INTO commercial.customer_quote_event")
        for statement, _ in cursor.executed
    )


def test_begin_drive_provision_attempt_conflicts_when_already_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _repository(
        monkeypatch,
        [
            None,
            _workspace_row(provisioning_status="ready", version=1),
        ],
    )

    with pytest.raises(
        CommercialOperationConflictError,
        match="already provisioned",
    ):
        repo.begin_drive_provision_attempt(
            quote_id=QUOTE_ID,
            operator="tatiana@origenlab.cl",
            expected_version=1,
        )


def test_begin_drive_provision_attempt_missing_quote_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _repository(monkeypatch, [None, None])

    with pytest.raises(CommercialOperationNotFoundError):
        repo.begin_drive_provision_attempt(
            quote_id=QUOTE_ID,
            operator="tatiana@origenlab.cl",
            expected_version=1,
        )


def test_complete_drive_provision_persists_references_and_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            _workspace_row(
                provisioning_status="ready",
                folder_id="folder-1",
                folder_web_url="https://drive.google.com/drive/folders/folder-1",
                sheet_file_id="sheet-1",
                sheet_web_url="https://docs.google.com/spreadsheets/d/sheet-1",
                completed_at=NOW,
                version=3,
            ),
        ],
    )

    workspace = repo.complete_drive_provision(
        quote_id=QUOTE_ID,
        operator="tatiana@origenlab.cl",
        folder_id="folder-1",
        folder_web_url="https://drive.google.com/drive/folders/folder-1",
        sheet_file_id="sheet-1",
        sheet_web_url="https://docs.google.com/spreadsheets/d/sheet-1",
    )

    assert workspace.provisioning_status == "ready"

    update_sql, update_params = cursor.executed[0]

    assert "provisioning_status = 'ready'" in update_sql
    assert "failure_category = NULL" in update_sql
    assert update_params["folder_id"] == "folder-1"
    assert update_params["sheet_file_id"] == "sheet-1"

    _, event_params = cursor.executed[1]

    assert event_params["event_type"] == "drive_workspace_ready"

    payload = json.loads(event_params["payload"])

    assert payload["folder_id"] == "folder-1"
    assert payload["sheet_file_id"] == "sheet-1"


def test_complete_drive_provision_rejects_non_https_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(monkeypatch, [])

    with pytest.raises(ValueError, match="https"):
        repo.complete_drive_provision(
            quote_id=QUOTE_ID,
            operator="tatiana@origenlab.cl",
            folder_id="folder-1",
            folder_web_url="http://drive.google.com/insecure",
            sheet_file_id="sheet-1",
            sheet_web_url="https://docs.google.com/spreadsheets/d/sheet-1",
        )

    assert cursor.executed == []


def test_fail_drive_provision_keeps_partial_folder_and_redacted_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            _workspace_row(
                provisioning_status="failed",
                failure_category="drive_unavailable",
                folder_id="folder-1",
                folder_web_url="https://drive.google.com/drive/folders/folder-1",
                version=3,
            ),
        ],
    )

    workspace = repo.fail_drive_provision(
        quote_id=QUOTE_ID,
        operator="tatiana@origenlab.cl",
        failure_category="drive_unavailable",
        folder_id="folder-1",
        folder_web_url="https://drive.google.com/drive/folders/folder-1",
    )

    assert workspace.provisioning_status == "failed"
    assert workspace.failure_category == "drive_unavailable"
    # The partial workspace stays discoverable for retry/reconciliation.
    assert workspace.folder_id == "folder-1"

    update_sql, update_params = cursor.executed[0]

    assert "provisioning_status = 'failed'" in update_sql
    assert "COALESCE(%(folder_id)s, folder_id)" in update_sql
    assert update_params["failure_category"] == "drive_unavailable"

    _, event_params = cursor.executed[1]

    assert event_params["event_type"] == "drive_provision_failed"

    payload = json.loads(event_params["payload"])

    assert payload["failure_category"] == "drive_unavailable"


def test_fail_drive_provision_rejects_unsafe_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(monkeypatch, [])

    with pytest.raises(ValueError, match="failure_category"):
        repo.fail_drive_provision(
            quote_id=QUOTE_ID,
            operator="tatiana@origenlab.cl",
            failure_category="Error: SSLCertVerificationError at https://...",
        )

    assert cursor.executed == []


def test_get_quote_bundle_returns_none_for_missing_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _repository(monkeypatch, [None])

    assert repo.get_quote_bundle(quote_id=QUOTE_ID) is None


def test_get_quote_bundle_reads_quote_revision_and_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _ = _repository(
        monkeypatch,
        [
            _quote_with_title_row(),
            _revision_row(),
            _workspace_row(),
        ],
    )

    bundle = repo.get_quote_bundle(quote_id=QUOTE_ID)

    assert bundle is not None
    assert bundle.quote.quote_id == QUOTE_ID
    assert bundle.sales_opportunity_title == "Centrífuga CEAF"
    assert bundle.revision.revision_number == 1
    assert bundle.workspace.provisioning_status == "pending"
