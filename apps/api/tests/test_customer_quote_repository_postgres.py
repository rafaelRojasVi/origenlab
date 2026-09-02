"""Real-Postgres adversarial coverage for CRM-Q1 non-Drive concurrency
boundaries (Phase CRM-Q1A, item D).

Requires a disposable Postgres migrated to Alembic head:

    cd apps/email-pipeline
    ALEMBIC_DATABASE_URL=$ORIGENLAB_TEST_POSTGRES_URL uv run alembic upgrade head

Then set ORIGENLAB_TEST_POSTGRES_URL and run pytest directly (this file is
intentionally excluded from apps/api/scripts/validate.sh's default run,
which disables Postgres, matching the existing CRM-4A integration pattern).

No mocks: every test exercises PostgresCustomerQuoteRepository against a
real connection with real threads, so the row-lock serialization, the
unique-constraint backstop, and the optimistic-concurrency version check are
actually proven -- not merely asserted against scripted stubs.

Covers:
  D1. Two concurrent first-ever allocations when the number-series row does
      not yet exist.
  D2. Same Idempotency-Key on a different opportunity must conflict, never
      return the first opportunity's quote.
  D3. Retry after commit plus lost HTTP response returns the same quote.
  D4. Process failure after DB commit but before Drive provisioning cannot
      leave an unrecoverable pending workspace.
  D5. Simultaneous begin_drive_provision_attempt racers presenting the
      IDENTICAL expected_version: exactly one wins, the rest conflict.

      CORRECTION (CRM-Q1C): this test alone does NOT prove the service
      layer can never issue two concurrent Drive writes for the same
      workspace, contrary to an earlier claim in this file/the PR
      description. It only proves simultaneous callers cannot all win the
      same version race. A caller that reads the version an in-flight
      attempt has *just* produced (begin_drive_provision_attempt commits
      its own transaction and releases the row lock before the service
      layer ever calls the Drive provider) could still start a second,
      concurrent attempt while the first was actively running under the
      pre-fix version-only check. That sequential-steal race -- and the
      active-attempt lease that closes it -- is covered separately in
      test_customer_quote_drive_provision_fencing_postgres.py.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    CUSTOMER_QUOTE_SERIES_KEY,
    PostgresCustomerQuoteRepository,
    QuoteNumberingConfig,
)
from origenlab_api.repositories.postgres.common import normalize_postgres_url
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
        "Alembic head to run CRM-Q1A adversarial concurrency tests."
    ),
)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _settings(url: str) -> Settings:
    return Settings(
        postgres_write_url=url,
        postgres_url=url,
        commercial_operations_writes_enabled=True,
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
    # Every test seeds its own fixtures; keep the number-series table clean
    # between tests since it is process-wide state keyed by prefix.
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


def _numbering(prefix: str, *, seed: int = 1) -> QuoteNumberingConfig:
    return QuoteNumberingConfig(
        document_prefix=prefix, serial_pad_width=4, seed_next_serial=seed
    )


# --- D1: two concurrent first-ever allocations, no series row yet ---------


def test_concurrent_first_allocations_serialize_and_never_collide(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    prefix = _uid("D1")[:8].upper().replace("_", "")[:8] or "D1PREFIX"
    numbering = _numbering(prefix, seed=1)

    sales_a = _uid("sales")
    sales_b = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_a)
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_b)

    results: list[object] = [None, None]
    errors: list[Exception | None] = [None, None]

    def _create(index: int, sales_id: str) -> None:
        try:
            results[index] = repo.create_quote(
                quote_id=_uid("quote"),
                sales_opportunity_id=sales_id,
                operator="tatiana@origenlab.cl",
                idempotency_key=_uid("idem"),
                request_fingerprint="f" * 64,
                numbering=numbering,
                template_reference=None,
            )
        except Exception as exc:  # pragma: no cover - failure path asserted below
            errors[index] = exc

    threads = [
        threading.Thread(target=_create, args=(0, sales_a)),
        threading.Thread(target=_create, args=(1, sales_b)),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == [None, None], errors
    assert results[0] is not None and results[1] is not None

    # document_number (not quote_number, which no longer encodes the
    # prefix) is what proves the allocated serials are distinct and
    # sequential here.
    documents = {results[0].quote.document_number, results[1].quote.document_number}

    # Both allocations succeeded, got distinct sequential numbers, and the
    # series row was seeded exactly once despite two concurrent first-ever
    # INSERT ... ON CONFLICT DO NOTHING attempts.
    assert documents == {f"{prefix}0001", f"{prefix}0002"}

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT next_serial FROM commercial.customer_quote_number_series "
            "WHERE series_key = %(series_key)s",
            {"series_key": CUSTOMER_QUOTE_SERIES_KEY},
        )
        row = cur.fetchone()

    assert row is not None
    assert row["next_serial"] == 3


# --- D2: same idempotency key, different opportunity, must conflict -------


def test_same_idempotency_key_different_opportunity_conflicts(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering = _numbering(_uid("D2")[:8].upper().replace("_", "")[:8] or "D2PREFIX")

    sales_a = _uid("sales")
    sales_b = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_a)
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_b)

    shared_key = _uid("idem")
    operator = "tatiana@origenlab.cl"

    first = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_a,
        operator=operator,
        idempotency_key=shared_key,
        request_fingerprint="a" * 64,
        numbering=numbering,
        template_reference=None,
    )

    with pytest.raises(CommercialOperationConflictError):
        repo.create_quote(
            quote_id=_uid("quote"),
            sales_opportunity_id=sales_b,
            operator=operator,
            idempotency_key=shared_key,
            request_fingerprint="b" * 64,
            numbering=numbering,
            template_reference=None,
        )

    # The reused key must never silently return opportunity A's quote for a
    # request that was actually about opportunity B.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM commercial.customer_quote "
            "WHERE sales_opportunity_id = %(id)s",
            {"id": sales_b},
        )
        count_row = cur.fetchone()

    assert count_row is not None
    assert count_row["n"] == 0
    assert first.quote.sales_opportunity_id == sales_a


# --- D3: retry after commit + lost response returns the same quote --------


def test_retry_with_same_idempotency_key_and_opportunity_replays_same_quote(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering = _numbering(_uid("D3")[:8].upper().replace("_", "")[:8] or "D3PREFIX")

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    key = _uid("idem")
    operator = "tatiana@origenlab.cl"
    fingerprint = "c" * 64

    first = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=operator,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        numbering=numbering,
        template_reference=None,
    )

    # Simulate the client never seeing the first response and retrying the
    # identical logical request (a fresh quote_id, since the client cannot
    # know the server-generated one -- the server must ignore it and replay).
    second = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=operator,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        numbering=numbering,
        template_reference=None,
    )

    assert second.quote.quote_id == first.quote.quote_id
    assert second.quote.quote_number == first.quote.quote_number

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM commercial.customer_quote "
            "WHERE sales_opportunity_id = %(id)s",
            {"id": sales_id},
        )
        count_row = cur.fetchone()

    assert count_row is not None
    assert count_row["n"] == 1


# --- D4: crash after commit, before Drive provisioning -> recoverable -----


def test_workspace_committed_before_any_drive_call_stays_recoverable(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering = _numbering(_uid("D4")[:8].upper().replace("_", "")[:8] or "D4PREFIX")

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    bundle = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
        numbering=numbering,
        template_reference=None,
    )

    # No Drive call happens here -- simulating a process crash right after
    # the commit above, before the service layer ever calls the provider.
    reloaded = repo.get_quote_bundle(quote_id=bundle.quote.quote_id)

    assert reloaded is not None
    assert reloaded.workspace.provisioning_status == "pending"
    assert reloaded.workspace.attempt_count == 0
    assert reloaded.workspace.version == 1

    # A later process (or an operator-triggered retry) must still be able to
    # begin a provisioning attempt against durable state -- nothing about
    # the crash left the workspace stuck or invisible.
    attempt = repo.begin_drive_provision_attempt(
        quote_id=bundle.quote.quote_id,
        operator="tatiana@origenlab.cl",
        expected_version=reloaded.workspace.version,
    )

    assert attempt.attempt_count == 1
    assert attempt.provisioning_status == "pending"


# --- D5: concurrent retry attempts cannot both proceed to Drive -----------


def test_concurrent_begin_provision_attempts_only_one_wins(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering = _numbering(_uid("D5")[:8].upper().replace("_", "")[:8] or "D5PREFIX")

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    bundle = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
        numbering=numbering,
        template_reference=None,
    )

    expected_version = bundle.workspace.version

    outcomes: list[str] = []
    lock = threading.Lock()

    def _attempt() -> None:
        try:
            repo.begin_drive_provision_attempt(
                quote_id=bundle.quote.quote_id,
                operator="tatiana@origenlab.cl",
                expected_version=expected_version,
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

    # Every one of the 5 racers targets the identical expected_version:
    # exactly one can win. This alone does NOT prove the service layer can
    # never issue two concurrent Drive writes for the same quote -- see the
    # module docstring's D5 correction and
    # test_customer_quote_drive_provision_fencing_postgres.py for the
    # sequential-steal race this test's shape cannot catch, and the
    # active-attempt lease that actually closes it.
    assert outcomes.count("won") == 1
    assert outcomes.count("conflict") == 4

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT attempt_count, version FROM "
            "commercial.customer_quote_drive_workspace WHERE quote_id = %(id)s",
            {"id": bundle.quote.quote_id},
        )
        row = cur.fetchone()

    assert row is not None
    assert row["attempt_count"] == 1
    assert row["version"] == expected_version + 1


# --- CRM-Q2 follow-up: folder-only completion (template provisioning off) -


def test_complete_drive_provision_with_no_sheet_marks_folder_ready(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering = _numbering(_uid("FR")[:8].upper().replace("_", "")[:8] or "FRPREFIX")

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    bundle = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
        numbering=numbering,
        template_reference=None,
    )

    attempt = repo.begin_drive_provision_attempt(
        quote_id=bundle.quote.quote_id,
        operator="tatiana@origenlab.cl",
        expected_version=bundle.workspace.version,
    )

    workspace = repo.complete_drive_provision(
        quote_id=bundle.quote.quote_id,
        operator="tatiana@origenlab.cl",
        attempt_version=attempt.version,
        folder_id="folder-only-1",
        folder_web_url="https://drive.google.com/drive/folders/folder-only-1",
    )

    assert workspace.provisioning_status == "folder_ready"
    assert workspace.folder_id == "folder-only-1"
    assert workspace.sheet_file_id is None
    assert workspace.sheet_web_url is None

    reloaded = repo.get_quote_bundle(quote_id=bundle.quote.quote_id)
    assert reloaded is not None
    assert reloaded.workspace.provisioning_status == "folder_ready"

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT event_type FROM commercial.customer_quote_event "
            "WHERE quote_id = %(id)s ORDER BY created_at DESC LIMIT 1",
            {"id": bundle.quote.quote_id},
        )
        row = cur.fetchone()

    assert row is not None
    assert row["event_type"] == "drive_workspace_folder_ready"


def test_complete_drive_provision_with_sheet_still_marks_ready(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    # Regression guard: the pre-existing full folder+sheet completion path
    # must be unaffected by the new optional sheet_file_id/sheet_web_url.
    numbering = _numbering(_uid("FR")[:8].upper().replace("_", "")[:8] or "FRPREFIX2")

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    bundle = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
        numbering=numbering,
        template_reference=None,
    )

    attempt = repo.begin_drive_provision_attempt(
        quote_id=bundle.quote.quote_id,
        operator="tatiana@origenlab.cl",
        expected_version=bundle.workspace.version,
    )

    workspace = repo.complete_drive_provision(
        quote_id=bundle.quote.quote_id,
        operator="tatiana@origenlab.cl",
        attempt_version=attempt.version,
        folder_id="folder-1",
        folder_web_url="https://drive.google.com/drive/folders/folder-1",
        sheet_file_id="sheet-1",
        sheet_web_url="https://docs.google.com/spreadsheets/d/sheet-1",
    )

    assert workspace.provisioning_status == "ready"
    assert workspace.sheet_file_id == "sheet-1"
