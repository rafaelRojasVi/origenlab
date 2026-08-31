"""Real-Postgres coverage for PostgresCustomerQuoteReadRepository (CRM-Q1C).

This read repository (used by the GET list/detail routes) had no real-
Postgres coverage at all before this file: its SELECT and row-mapping did
not include the new lease_expires_at column added for the provisioning
attempt lease, so reading any quote through it would raise a TypeError
(CustomerQuoteDriveWorkspace missing a required argument) the moment a real
row existed. This proves the fix against a real connection through the
api.* read views, not a fake cursor.

Requires a disposable Postgres migrated to Alembic head; set
ORIGENLAB_TEST_POSTGRES_URL to run.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.common import normalize_postgres_url
from origenlab_api.repositories.postgres.customer_quotes import (
    PostgresCustomerQuoteRepository,
    QuoteNumberingConfig,
)
from origenlab_api.repositories.postgres.customer_quotes_read import (
    PostgresCustomerQuoteReadRepository,
)
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
        "Alembic head to run CRM-Q1C read-repository tests."
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
def settings() -> Settings:
    url = _postgres_test_url_ready()
    assert url is not None
    return _settings(url)


@pytest.fixture
def write_repo(settings: Settings) -> PostgresCustomerQuoteRepository:
    return PostgresCustomerQuoteRepository(settings)


@pytest.fixture
def read_repo(settings: Settings) -> PostgresCustomerQuoteReadRepository:
    return PostgresCustomerQuoteReadRepository(settings)


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


def test_get_reads_a_pending_workspace_with_no_lease(
    admin_conn: object,
    write_repo: PostgresCustomerQuoteRepository,
    read_repo: PostgresCustomerQuoteReadRepository,
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    numbering = QuoteNumberingConfig(document_prefix="RD", serial_pad_width=4, seed_next_serial=1)

    bundle = write_repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
        numbering=numbering,
        template_reference=None,
    )

    read = read_repo.get(bundle.quote.quote_id)

    assert read is not None
    assert read.workspace.provisioning_status == "pending"
    assert read.workspace.lease_expires_at is None


def test_get_reads_an_active_lease(
    admin_conn: object,
    write_repo: PostgresCustomerQuoteRepository,
    read_repo: PostgresCustomerQuoteReadRepository,
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    numbering = QuoteNumberingConfig(document_prefix="RE", serial_pad_width=4, seed_next_serial=1)

    bundle = write_repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
        numbering=numbering,
        template_reference=None,
    )
    write_repo.begin_drive_provision_attempt(
        quote_id=bundle.quote.quote_id,
        operator=OPERATOR,
        expected_version=1,
    )

    read = read_repo.get(bundle.quote.quote_id)

    assert read is not None
    assert read.workspace.provisioning_status == "pending"
    assert read.workspace.lease_expires_at is not None
    assert read.workspace.lease_expires_at > datetime.now(timezone.utc)


def test_list_for_sales_opportunity_reads_lease_expires_at(
    admin_conn: object,
    write_repo: PostgresCustomerQuoteRepository,
    read_repo: PostgresCustomerQuoteReadRepository,
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    numbering = QuoteNumberingConfig(document_prefix="RL", serial_pad_width=4, seed_next_serial=1)

    bundle = write_repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
        numbering=numbering,
        template_reference=None,
    )
    write_repo.begin_drive_provision_attempt(
        quote_id=bundle.quote.quote_id,
        operator=OPERATOR,
        expected_version=1,
    )

    items = read_repo.list_for_sales_opportunity(sales_id)

    assert len(items) == 1
    assert items[0].workspace.lease_expires_at is not None
