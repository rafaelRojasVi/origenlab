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
    QuoteNumberingPolicyMismatchError,
    chile_issue_year,
)
from origenlab_api.settings import Settings


def test_chile_issue_year_uses_santiago_local_calendar_year() -> None:
    moment = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)

    assert chile_issue_year(moment) == 2026


def test_chile_issue_year_does_not_advance_early_at_utc_new_year() -> None:
    # OrigenLab is Chilean: America/Santiago is behind UTC, so UTC's new
    # year always arrives before Santiago's. Just after UTC midnight on
    # Jan 1, Santiago local time is still Dec 31 of the prior year -- a
    # naive `datetime.now(timezone.utc).year` would wrongly report the new
    # year here.
    moment = datetime(2027, 1, 1, 2, 0, tzinfo=timezone.utc)

    assert chile_issue_year(moment) == 2026


def test_chile_issue_year_advances_once_santiago_local_time_crosses_midnight() -> (
    None
):
    # Once enough hours have passed that Santiago's own local clock has
    # crossed into the new year, the issue year must follow it.
    moment = datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert chile_issue_year(moment) == 2027


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)

# Real-evidence-shaped numbering (see docs/architecture business evidence):
# serial 1183 -> human quote_number "01183-26", document_number "CN01183".
NUMBERING = QuoteNumberingConfig(
    document_prefix="CN",
    serial_pad_width=5,
    seed_next_serial=1183,
)


def _quote_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "quote_id": "quote_" + "a" * 32,
        "sales_opportunity_id": "sales_" + "b" * 32,
        "quote_number": "01183-26",
        "serial": 1183,
        "issue_year": 2026,
        "document_number": "CN01183",
        "quote_origin": "generated",
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
        "updated_by": "tatiana@origenlab.cl",
        "updated_at": NOW,
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
        "lease_expires_at": None,
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
    # Deterministic issue year: NOW's America/Santiago calendar date is
    # 2026-08-30 regardless of DST, well away from any year boundary.
    monkeypatch.setattr(quotes_module, "_utcnow", lambda: NOW)

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
            {"document_prefix": "CN", "pad_width": 5},  # series upsert policy check
            {"document_prefix": "CN", "pad_width": 5, "allocated_serial": 1183},
            _quote_row(),
            _revision_row(),
            _workspace_row(),
        ],
    )

    bundle = repo.create_quote(**_create_kwargs())

    assert bundle.quote.quote_number == "01183-26"
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
    assert (
        "ON CONFLICT ( series_key ) DO UPDATE SET series_key = EXCLUDED.series_key"
        in statements[2]
    )

    series_upsert_params = cursor.executed[2][1]
    # The series identity is fixed -- never the configured document prefix --
    # so a later prefix/pad_width change can never silently start a second
    # series row.
    assert series_upsert_params["series_key"] == "customer_quote"

    allocate_sql, allocate_params = cursor.executed[3]

    assert allocate_sql.startswith(
        "UPDATE commercial.customer_quote_number_series"
    )
    assert "next_serial = next_serial + 1" in allocate_sql
    assert "RETURNING" in allocate_sql
    assert "next_serial - 1 AS allocated_serial" in allocate_sql
    assert allocate_params["series_key"] == "customer_quote"

    insert_sql, insert_params = cursor.executed[4]

    assert insert_sql.startswith("INSERT INTO commercial.customer_quote (")
    assert insert_params["quote_number"] == "01183-26"
    assert insert_params["serial"] == 1183
    assert insert_params["issue_year"] == 2026
    assert insert_params["document_number"] == "CN01183"
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

    assert payload["quote_number"] == "01183-26"
    assert payload["document_number"] == "CN01183"
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
            {"document_prefix": "CN", "pad_width": 5},  # series upsert policy check
            {"document_prefix": "CN", "pad_width": 5, "allocated_serial": 1183},
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


def test_create_quote_fails_closed_on_prefix_drift_from_durable_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, cursor = _repository(
        monkeypatch,
        [
            {"idempotency_key": "quote-create-1"},
            {"sales_opportunity_id": SALES_ID, "title": "Centrífuga CEAF"},
            # The durable series row already exists with a different
            # document prefix than the currently configured one -- must fail
            # closed here, before any allocation or quote write.
            {"document_prefix": "CN", "pad_width": 5},
        ],
    )

    drifted = QuoteNumberingConfig(
        document_prefix="CX", serial_pad_width=5, seed_next_serial=1
    )

    with pytest.raises(QuoteNumberingPolicyMismatchError):
        repo.create_quote(**_create_kwargs(numbering=drifted))

    assert not any(
        statement.startswith("UPDATE commercial.customer_quote_number_series")
        or statement.startswith("INSERT INTO commercial.customer_quote (")
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
        attempt_version=2,
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
            attempt_version=2,
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
        attempt_version=2,
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
            attempt_version=2,
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
