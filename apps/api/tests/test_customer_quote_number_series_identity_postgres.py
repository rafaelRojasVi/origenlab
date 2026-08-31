"""Real-Postgres coverage for CRM-Q1C item 4: quote-number series identity
must fail closed on configuration drift.

The durable ``commercial.customer_quote_number_series`` row becomes counter
truth after the first allocation -- the environment-configured
prefix/pad-width only ever seeds that first row. Before this fix the
configured *prefix* doubled as the series' primary key, so a later prefix
typo or environment change would silently INSERT a *second* series row
(keyed by the new prefix) instead of failing closed against the existing
durable policy.

Requires a disposable Postgres migrated to Alembic head (see
test_customer_quote_repository_postgres.py's module docstring for the
migration command); set ORIGENLAB_TEST_POSTGRES_URL to run.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.customer_quotes import (
    CUSTOMER_QUOTE_SERIES_KEY,
    PostgresCustomerQuoteRepository,
    QuoteNumberingConfig,
    QuoteNumberingPolicyMismatchError,
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
        "Alembic head to run CRM-Q1C adversarial numbering tests."
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


def test_prefix_change_after_first_allocation_fails_closed_no_second_series(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering_v1 = QuoteNumberingConfig(
        document_prefix="CN", serial_pad_width=6, seed_next_serial=11729
    )

    sales_a = _uid("sales")
    sales_b = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_a)
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_b)

    first = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_a,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
        numbering=numbering_v1,
        template_reference=None,
    )
    # quote_number no longer encodes the document prefix -- document_number
    # is the identifier that proves the durable series policy applied.
    assert first.quote.document_number == "CN011729"

    # A typo'd / changed prefix in later config must never start a second
    # series -- it must fail closed against the durable policy instead.
    numbering_v2 = QuoteNumberingConfig(
        document_prefix="CX", serial_pad_width=6, seed_next_serial=1
    )

    with pytest.raises(QuoteNumberingPolicyMismatchError):
        repo.create_quote(
            quote_id=_uid("quote"),
            sales_opportunity_id=sales_b,
            operator="tatiana@origenlab.cl",
            idempotency_key=_uid("idem"),
            request_fingerprint="b" * 64,
            numbering=numbering_v2,
            template_reference=None,
        )

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM commercial.customer_quote_number_series"
        )
        assert cur.fetchone()["n"] == 1

        cur.execute(
            "SELECT document_prefix, pad_width, next_serial FROM "
            "commercial.customer_quote_number_series WHERE series_key = %(key)s",
            {"key": CUSTOMER_QUOTE_SERIES_KEY},
        )
        row = cur.fetchone()
        assert row is not None
        assert row["document_prefix"] == "CN"
        assert row["pad_width"] == 6
        # The failed allocation attempt must not have consumed a serial.
        assert row["next_serial"] == 11730

        cur.execute(
            "SELECT COUNT(*) AS n FROM commercial.customer_quote "
            "WHERE sales_opportunity_id = %(id)s",
            {"id": sales_b},
        )
        assert cur.fetchone()["n"] == 0


def test_pad_width_change_after_first_allocation_fails_closed(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering_v1 = QuoteNumberingConfig(
        document_prefix="CN", serial_pad_width=6, seed_next_serial=1
    )
    sales_a = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_a)

    repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_a,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
        numbering=numbering_v1,
        template_reference=None,
    )

    numbering_v2 = QuoteNumberingConfig(
        document_prefix="CN", serial_pad_width=4, seed_next_serial=1
    )
    sales_b = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_b)

    with pytest.raises(QuoteNumberingPolicyMismatchError):
        repo.create_quote(
            quote_id=_uid("quote"),
            sales_opportunity_id=sales_b,
            operator="tatiana@origenlab.cl",
            idempotency_key=_uid("idem"),
            request_fingerprint="b" * 64,
            numbering=numbering_v2,
            template_reference=None,
        )

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM commercial.customer_quote_number_series"
        )
        assert cur.fetchone()["n"] == 1


def test_matching_configuration_continues_the_same_series(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> None:
    numbering = QuoteNumberingConfig(
        document_prefix="CN", serial_pad_width=6, seed_next_serial=11729
    )
    sales_a = _uid("sales")
    sales_b = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_a)
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_b)

    first = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_a,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
        numbering=numbering,
        template_reference=None,
    )
    second = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_b,
        operator="tatiana@origenlab.cl",
        idempotency_key=_uid("idem"),
        request_fingerprint="b" * 64,
        numbering=numbering,
        template_reference=None,
    )

    assert first.quote.document_number == "CN011729"
    assert second.quote.document_number == "CN011730"
