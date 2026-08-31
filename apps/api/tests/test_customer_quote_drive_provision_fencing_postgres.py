"""Real-Postgres coverage for CRM-Q1C item 1: Drive provisioning attempts
must be fenced by a server-owned active-attempt lease, not by the version
check alone.

The version-only check (pre-fix) only rejected a caller presenting a STALE
version. It did nothing to stop a caller from reading the version an
in-flight attempt had *just* produced (begin_drive_provision_attempt commits
its own transaction and releases the row lock before the service layer ever
calls the Drive provider) and starting a second, concurrent attempt while
the first is still running. This file proves that race is closed by a
lease, and that the resulting fencing token semantics (stale
complete/fail must conflict, never modify a newer attempt or downgrade a
ready workspace; an expired lease remains safely reclaimable) hold against
a real connection with real threads -- not scripted stubs.

Requires a disposable Postgres migrated to Alembic head (see
test_customer_quote_repository_postgres.py's module docstring for the
migration command); set ORIGENLAB_TEST_POSTGRES_URL to run.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest

from origenlab_api.drive.protocol import DriveFileRef
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    PostgresCustomerQuoteRepository,
    QuoteNumberingConfig,
)
from origenlab_api.repositories.postgres.common import normalize_postgres_url
from origenlab_api.services.customer_quote_service import CustomerQuoteService
from origenlab_api.settings import Settings


def _postgres_test_url_ready() -> str | None:
    url = (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip()
    if not url:
        return None
    try:
        import psycopg

        with psycopg.connect(normalize_postgres_url(url), connect_timeout=2):
            pass
        return url
    except Exception:
        return None


pytestmark = pytest.mark.skipif(
    _postgres_test_url_ready() is None,
    reason=(
        "Set ORIGENLAB_TEST_POSTGRES_URL to a disposable Postgres migrated to "
        "Alembic head to run CRM-Q1C adversarial fencing tests."
    ),
)


OPERATOR = "tatiana@origenlab.cl"


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _settings(url: str) -> Settings:
    return Settings(
        postgres_write_url=url,
        postgres_url=url,
        commercial_operations_writes_enabled=True,
        drive_quote_template_file_id="template-file-1",
    )


@pytest.fixture
def admin_conn() -> Iterator[object]:
    import psycopg

    url = _postgres_test_url_ready()
    assert url is not None
    conn = psycopg.connect(
        normalize_postgres_url(url),
        autocommit=True,
        row_factory=psycopg.rows.dict_row,
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def repo() -> PostgresCustomerQuoteRepository:
    url = _postgres_test_url_ready()
    assert url is not None
    return PostgresCustomerQuoteRepository(_settings(url))


@pytest.fixture
def settings() -> Settings:
    url = _postgres_test_url_ready()
    assert url is not None
    return _settings(url)


def _seed_sales_opportunity(admin_conn: object, *, sales_opportunity_id: str) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.sales_opportunity (
              sales_opportunity_id, source_kind, source_opportunity_id,
              title, owner_key, created_by, updated_by, updated_at
            ) VALUES (
              %(id)s, 'pr3', %(id)s,
              'Centrífuga CEAF', 'tatiana@origenlab.cl', 'tatiana@origenlab.cl',
              'tatiana@origenlab.cl', now()
            )
            """,
            {"id": sales_opportunity_id},
        )


@pytest.fixture(autouse=True)
def _clean_slate(admin_conn: object) -> Iterator[None]:
    with admin_conn.cursor() as cur:
        cur.execute("DELETE FROM commercial.customer_quote_event")
        cur.execute("DELETE FROM commercial.customer_quote_drive_workspace")
        cur.execute("DELETE FROM commercial.customer_quote_revision")
        cur.execute("DELETE FROM commercial.customer_quote")
        cur.execute("DELETE FROM commercial.customer_quote_number_series")
        cur.execute("DELETE FROM commercial.command_idempotency")
        cur.execute("DELETE FROM commercial.sales_opportunity_event")
        cur.execute("DELETE FROM commercial.sales_opportunity")
    yield


def _create_quote(
    repo: PostgresCustomerQuoteRepository, admin_conn: object
) -> str:
    numbering = QuoteNumberingConfig(
        document_prefix=_uid("F")[:8].upper()[:8] or "FENCEPFX",
        serial_pad_width=4,
        seed_next_serial=1,
    )
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    bundle = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
        numbering=numbering,
        template_reference=None,
    )
    return bundle.quote.quote_id


class BlockingDriveProvider:
    """A fake provider whose verify_destination() blocks until released --
    standing in for a slow/real Drive call while a concurrent caller
    attempts to steal the attempt."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []

    def verify_destination(self) -> None:
        self.calls.append("verify_destination")
        self.entered.set()
        # Bounded wait: a real test must never hang forever even if the
        # assertion below fails to release it.
        self.release.wait(timeout=15)

    def find_folder(self, quote_id: str) -> DriveFileRef | None:
        self.calls.append("find_folder")
        return None

    def create_folder(self, quote_id: str, *, name: str) -> DriveFileRef:
        self.calls.append("create_folder")
        return DriveFileRef(file_id="folder-1", web_url="https://drive.google.com/drive/folders/folder-1")

    def find_sheet(self, quote_id: str, *, folder_id: str) -> DriveFileRef | None:
        self.calls.append("find_sheet")
        return None

    def copy_template_sheet(self, quote_id: str, *, folder_id: str, name: str) -> DriveFileRef:
        self.calls.append("copy_template_sheet")
        return DriveFileRef(file_id="sheet-1", web_url="https://docs.google.com/spreadsheets/d/sheet-1")


def test_caller_reading_the_in_flight_version_cannot_reach_the_provider(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
    settings: Settings,
) -> None:
    quote_id = _create_quote(repo, admin_conn)

    provider = BlockingDriveProvider()
    service = CustomerQuoteService(
        settings,
        repository=repo,
        drive_provider_factory=lambda s: provider,
    )

    def run_attempt_a() -> None:
        service.retry_drive_provisioning(
            quote_id=quote_id, operator=OPERATOR, expected_version=1
        )

    thread_a = threading.Thread(target=run_attempt_a)
    thread_a.start()

    try:
        assert provider.entered.wait(timeout=10), "attempt A never reached the provider"

        # A's begin_drive_provision_attempt has already committed (its own
        # transaction) and bumped the version -- prove B can read that new
        # version while A is still blocked inside the provider.
        refreshed = repo.get_quote_bundle(quote_id=quote_id)
        assert refreshed is not None
        assert refreshed.workspace.version == 2
        assert refreshed.workspace.provisioning_status == "pending"

        # B retries using exactly that version. Before the fix this begin
        # call would succeed (the version-only check doesn't know an
        # attempt is actively in flight) and the service layer would then
        # call the (blocking) provider a second time concurrently with A.
        # After the fix it must conflict -- B must never reach the
        # provider while A's lease is active.
        with pytest.raises(CommercialOperationConflictError):
            repo.begin_drive_provision_attempt(
                quote_id=quote_id,
                operator=OPERATOR,
                expected_version=refreshed.workspace.version,
            )

        # Only A's single verify_destination call happened so far -- B never
        # entered the provider at all.
        assert provider.calls.count("verify_destination") == 1
    finally:
        provider.release.set()
        thread_a.join(timeout=10)

    final = repo.get_quote_bundle(quote_id=quote_id)
    assert final is not None
    assert final.workspace.provisioning_status == "ready"


def test_expired_lease_can_be_safely_reclaimed(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    quote_id = _create_quote(repo, admin_conn)

    attempt = repo.begin_drive_provision_attempt(
        quote_id=quote_id, operator=OPERATOR, expected_version=1
    )
    assert attempt.version == 2

    # Simulate a crashed/abandoned attempt whose lease has expired --
    # backdate it directly (real elapsed time is not exercised here).
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE commercial.customer_quote_drive_workspace "
            "SET lease_expires_at = %(past)s WHERE quote_id = %(quote_id)s",
            {
                "past": datetime.now(timezone.utc) - timedelta(seconds=1),
                "quote_id": quote_id,
            },
        )

    reclaimed = repo.begin_drive_provision_attempt(
        quote_id=quote_id, operator=OPERATOR, expected_version=2
    )
    assert reclaimed.version == 3
    assert reclaimed.attempt_count == 2


def test_stale_attempt_cannot_complete_after_reclaim(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    quote_id = _create_quote(repo, admin_conn)

    stale_attempt = repo.begin_drive_provision_attempt(
        quote_id=quote_id, operator=OPERATOR, expected_version=1
    )
    stale_token = stale_attempt.version
    assert stale_token == 2

    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE commercial.customer_quote_drive_workspace "
            "SET lease_expires_at = %(past)s WHERE quote_id = %(quote_id)s",
            {
                "past": datetime.now(timezone.utc) - timedelta(seconds=1),
                "quote_id": quote_id,
            },
        )

    fresh_attempt = repo.begin_drive_provision_attempt(
        quote_id=quote_id, operator=OPERATOR, expected_version=stale_token
    )
    assert fresh_attempt.version == 3

    # The old (stale) A must not be able to complete using its old token --
    # regardless of whether the new attempt (B) has finished yet.
    with pytest.raises(CommercialOperationConflictError):
        repo.complete_drive_provision(
            quote_id=quote_id,
            operator=OPERATOR,
            attempt_version=stale_token,
            folder_id="folder-stale",
            folder_web_url="https://drive.google.com/drive/folders/folder-stale",
            sheet_file_id="sheet-stale",
            sheet_web_url="https://docs.google.com/spreadsheets/d/sheet-stale",
        )

    # And must not be able to fail it either.
    with pytest.raises(CommercialOperationConflictError):
        repo.fail_drive_provision(
            quote_id=quote_id,
            operator=OPERATOR,
            attempt_version=stale_token,
            failure_category="drive_unavailable",
        )

    current = repo.get_quote_bundle(quote_id=quote_id)
    assert current is not None
    assert current.workspace.version == 3
    assert current.workspace.provisioning_status == "pending"
    assert current.workspace.folder_id is None


def test_stale_failure_cannot_downgrade_a_ready_workspace(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    quote_id = _create_quote(repo, admin_conn)

    attempt = repo.begin_drive_provision_attempt(
        quote_id=quote_id, operator=OPERATOR, expected_version=1
    )
    attempt_token = attempt.version

    ready = repo.complete_drive_provision(
        quote_id=quote_id,
        operator=OPERATOR,
        attempt_version=attempt_token,
        folder_id="folder-1",
        folder_web_url="https://drive.google.com/drive/folders/folder-1",
        sheet_file_id="sheet-1",
        sheet_web_url="https://docs.google.com/spreadsheets/d/sheet-1",
    )
    assert ready.provisioning_status == "ready"

    # A stale failure using the same (now superseded) attempt token must
    # conflict, never flip a ready workspace back to failed.
    with pytest.raises(CommercialOperationConflictError):
        repo.fail_drive_provision(
            quote_id=quote_id,
            operator=OPERATOR,
            attempt_version=attempt_token,
            failure_category="drive_unavailable",
        )

    current = repo.get_quote_bundle(quote_id=quote_id)
    assert current is not None
    assert current.workspace.provisioning_status == "ready"
    assert current.workspace.folder_id == "folder-1"


def test_identical_expected_version_racers_still_permit_exactly_one_winner(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    # This is the pre-existing "5 racers, same expected_version" shape,
    # kept alongside the sequential-steal tests above: it proves only that
    # simultaneous callers presenting the identical version cannot all win,
    # not that a caller reading a just-advanced version is blocked (that is
    # test_caller_reading_the_in_flight_version_cannot_reach_the_provider
    # above).
    quote_id = _create_quote(repo, admin_conn)

    outcomes: list[str] = []
    lock = threading.Lock()

    def _attempt() -> None:
        try:
            repo.begin_drive_provision_attempt(
                quote_id=quote_id, operator=OPERATOR, expected_version=1
            )
            with lock:
                outcomes.append("won")
        except CommercialOperationConflictError:
            with lock:
                outcomes.append("conflict")

    threads = [threading.Thread(target=_attempt) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert outcomes.count("won") == 1
    assert outcomes.count("conflict") == 4
