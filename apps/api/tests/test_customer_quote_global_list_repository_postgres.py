"""Real-Postgres coverage for the global customer-quote list (Cotizaciones).

Requires a disposable Postgres migrated to Alembic head; set
ORIGENLAB_TEST_POSTGRES_URL to run.
"""

from __future__ import annotations

import os
import uuid
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
        "Set ORIGENLAB_TEST_POSTGRES_URL to a disposable Postgres migrated "
        "to Alembic head to run the global customer-quote list tests."
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


def _seed_sales_opportunity(
    admin_conn: object,
    *,
    sales_opportunity_id: str,
    organization_id: str | None = None,
    stage: str = "quoting",
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.sales_opportunity (
              sales_opportunity_id, source_kind, source_opportunity_id,
              title, stage, owner_key, created_by, updated_by, updated_at,
              organization_id
            ) VALUES (
              %(id)s, 'pr3', %(id)s,
              'Centrífuga CEAF', %(stage)s, 'tatiana@origenlab.cl', 'tatiana@origenlab.cl',
              'tatiana@origenlab.cl', now(), %(organization_id)s
            )
            """,
            {"id": sales_opportunity_id, "stage": stage, "organization_id": organization_id},
        )


def _seed_organization(admin_conn: object, *, display_name: str) -> str:
    organization_id = _uid("org")
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.organization (
              organization_id, display_name, version,
              created_by, updated_by, created_at, updated_at
            ) VALUES (
              %(id)s, %(name)s, 1, 'tatiana@origenlab.cl', 'tatiana@origenlab.cl', now(), now()
            )
            """,
            {"id": organization_id, "name": display_name},
        )
    return organization_id


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
        cur.execute("DELETE FROM commercial.contact")
        cur.execute("DELETE FROM commercial.organization")
    yield


def test_list_all_returns_quotes_across_multiple_sales_opportunities(
    admin_conn: object,
    write_repo: PostgresCustomerQuoteRepository,
    read_repo: PostgresCustomerQuoteReadRepository,
) -> None:
    org_id = _seed_organization(admin_conn, display_name="Hospital Regional de Rancagua")
    sales_a = _uid("sales")
    sales_b = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_a, organization_id=org_id)
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_b)

    # NOTE: create_quote uses a single fixed global numbering series
    # (CUSTOMER_QUOTE_SERIES_KEY); once a document_prefix/pad_width policy
    # is durably established by the first allocation, a later call with a
    # different prefix fails closed with QuoteNumberingPolicyMismatchError
    # (see test_customer_quote_number_series_identity_postgres.py). Reuse
    # one numbering config across every create_quote call in this test;
    # serial still auto-increments per call.
    numbering = QuoteNumberingConfig(document_prefix="RA", serial_pad_width=4, seed_next_serial=1)
    write_repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_a,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
        numbering=numbering,
        template_reference=None,
    )
    write_repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_b,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="b" * 64,
        numbering=numbering,
        template_reference=None,
    )

    entries, total_count = read_repo.list_all(limit=100, offset=0)

    assert total_count == 2
    assert len(entries) == 2
    by_sales_id = {e.bundle.quote.sales_opportunity_id: e for e in entries}
    assert by_sales_id[sales_a].organization_display_name == "Hospital Regional de Rancagua"
    assert by_sales_id[sales_a].sales_opportunity_stage == "quoting"
    assert by_sales_id[sales_b].organization_display_name is None


def test_list_all_filters_by_drive_status(
    admin_conn: object,
    write_repo: PostgresCustomerQuoteRepository,
    read_repo: PostgresCustomerQuoteReadRepository,
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    write_repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="c" * 64,
        numbering=QuoteNumberingConfig(document_prefix="RC", serial_pad_width=4, seed_next_serial=1),
        template_reference=None,
    )

    ready_entries, _ = read_repo.list_all(limit=100, offset=0, drive_status=["ready"])
    pending_entries, _ = read_repo.list_all(limit=100, offset=0, drive_status=["pending"])

    assert ready_entries == []
    assert len(pending_entries) == 1


def test_list_all_paginates_with_limit_and_offset(
    admin_conn: object,
    write_repo: PostgresCustomerQuoteRepository,
    read_repo: PostgresCustomerQuoteReadRepository,
) -> None:
    # See the note in the first test above: reuse one numbering config
    # across every create_quote call, since the numbering series is a
    # single fixed global series.
    numbering = QuoteNumberingConfig(document_prefix="RP", serial_pad_width=4, seed_next_serial=1)
    for i in range(3):
        sales_id = _uid("sales")
        _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
        write_repo.create_quote(
            quote_id=_uid("quote"),
            sales_opportunity_id=sales_id,
            operator=OPERATOR,
            idempotency_key=_uid("idem"),
            request_fingerprint=f"{i}" * 64,
            numbering=numbering,
            template_reference=None,
        )

    page_one, total_count = read_repo.list_all(limit=2, offset=0)
    page_two, _ = read_repo.list_all(limit=2, offset=2)

    assert total_count == 3
    assert len(page_one) == 2
    assert len(page_two) == 1
