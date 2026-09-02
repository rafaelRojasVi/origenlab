"""Real-Postgres coverage for CRM-Q2B's customer_quote_close command.

Requires a disposable Postgres migrated to Alembic head (see
test_customer_quote_workflow_repository_postgres.py's module docstring for
the exact setup). No mocks: every test exercises
PostgresCustomerQuoteRepository.close_quote against a real connection, so
the CAS/idempotency/concurrency semantics are actually proven, not just
their call shape.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
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
        "Alembic head to run CRM-Q2B closure tests."
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


OPERATOR = "tatiana@origenlab.cl"


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
              'Centrífuga CEAF', %(op)s, %(op)s, %(op)s, now()
            )
            """,
            {"id": sales_opportunity_id, "op": OPERATOR},
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


def _numbering(prefix: str, *, seed: int = 1) -> QuoteNumberingConfig:
    return QuoteNumberingConfig(
        document_prefix=prefix, serial_pad_width=4, seed_next_serial=seed
    )


def _create_sent_quote(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> str:
    """Seed a sales opportunity + a quote already progressed to 'sent'.
    Returns quote_id."""

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    prefix = _uid("P")[:6].upper().replace("_", "")[:6] or "PREFIX"
    bundle = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
        numbering=_numbering(prefix),
        template_reference=None,
    )
    quote_id = bundle.quote.quote_id

    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)
    repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=2)
    repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=3)

    return quote_id


def test_close_quote_won_from_sent_transitions_to_closed_won(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    quote_id = _create_sent_quote(admin_conn, repo)

    bundle = repo.close_quote(
        quote_id=quote_id,
        operator=OPERATOR,
        expected_version=4,
        outcome="won",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    assert bundle.revision.status == "closed_won"
    assert bundle.quote.version == 5


def test_close_quote_null_from_sent_transitions_to_closed_null(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    quote_id = _create_sent_quote(admin_conn, repo)

    bundle = repo.close_quote(
        quote_id=quote_id,
        operator=OPERATOR,
        expected_version=4,
        outcome="null",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    assert bundle.revision.status == "closed_null"


@pytest.mark.parametrize("illegal_from", ["draft", "pending_approval", "approved"])
def test_close_quote_refuses_from_non_sent_statuses(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
    illegal_from: str,
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    prefix = _uid("P")[:6].upper().replace("_", "")[:6] or "PREFIX"
    bundle = repo.create_quote(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
        numbering=_numbering(prefix),
        template_reference=None,
    )
    quote_id = bundle.quote.quote_id
    version = 1

    if illegal_from in ("pending_approval", "approved"):
        repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
    if illegal_from == "approved":
        repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1

    with pytest.raises(CommercialOperationConflictError):
        repo.close_quote(
            quote_id=quote_id,
            operator=OPERATOR,
            expected_version=version,
            outcome="won",
            idempotency_key=_uid("idem"),
            request_fingerprint="f" * 64,
        )


def test_close_quote_rejects_stale_version(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    quote_id = _create_sent_quote(admin_conn, repo)

    with pytest.raises(CommercialOperationConflictError):
        repo.close_quote(
            quote_id=quote_id,
            operator=OPERATOR,
            expected_version=99,
            outcome="won",
            idempotency_key=_uid("idem"),
            request_fingerprint="f" * 64,
        )


def test_close_quote_on_missing_quote_raises_not_found(
    repo: PostgresCustomerQuoteRepository,
) -> None:
    with pytest.raises(CommercialOperationNotFoundError):
        repo.close_quote(
            quote_id=_uid("quote"),
            operator=OPERATOR,
            expected_version=1,
            outcome="won",
            idempotency_key=_uid("idem"),
            request_fingerprint="f" * 64,
        )


def test_close_quote_appends_exactly_one_quote_closed_event_with_outcome_in_payload(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    quote_id = _create_sent_quote(admin_conn, repo)

    repo.close_quote(
        quote_id=quote_id,
        operator=OPERATOR,
        expected_version=4,
        outcome="won",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT payload FROM commercial.customer_quote_event
            WHERE quote_id = %(quote_id)s AND event_type = 'quote_closed'
            """,
            {"quote_id": quote_id},
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0]["payload"]["outcome"] == "won"
    assert rows[0]["payload"]["from_status"] == "sent"
    assert rows[0]["payload"]["to_status"] == "closed_won"


def test_close_quote_is_idempotent_on_replay_no_duplicate_event(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    quote_id = _create_sent_quote(admin_conn, repo)
    key = _uid("idem")

    first = repo.close_quote(
        quote_id=quote_id,
        operator=OPERATOR,
        expected_version=4,
        outcome="won",
        idempotency_key=key,
        request_fingerprint="f" * 64,
    )
    second = repo.close_quote(
        quote_id=quote_id,
        operator=OPERATOR,
        expected_version=4,
        outcome="won",
        idempotency_key=key,
        request_fingerprint="f" * 64,
    )

    assert first.revision.status == second.revision.status == "closed_won"
    assert first.quote.version == second.quote.version

    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS n FROM commercial.customer_quote_event
            WHERE quote_id = %(quote_id)s AND event_type = 'quote_closed'
            """,
            {"quote_id": quote_id},
        )
        assert cur.fetchone()["n"] == 1


def test_close_quote_never_touches_sales_opportunity(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    """Quote outcome belongs to the quote lifecycle; opportunity outcome
    belongs to the sales lifecycle -- close_quote must never write
    commercial.sales_opportunity, even for outcome=won."""

    quote_id = _create_sent_quote(admin_conn, repo)
    bundle_before = repo.get_quote_bundle(quote_id=quote_id)
    assert bundle_before is not None
    sales_opportunity_id = bundle_before.quote.sales_opportunity_id

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT stage, version FROM commercial.sales_opportunity WHERE sales_opportunity_id = %(id)s",
            {"id": sales_opportunity_id},
        )
        before = cur.fetchone()

    repo.close_quote(
        quote_id=quote_id,
        operator=OPERATOR,
        expected_version=4,
        outcome="won",
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT stage, version FROM commercial.sales_opportunity WHERE sales_opportunity_id = %(id)s",
            {"id": sales_opportunity_id},
        )
        after = cur.fetchone()

    assert after["stage"] == before["stage"]
    assert after["version"] == before["version"]


def test_two_concurrent_close_attempts_one_wins_one_conflicts(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    quote_id = _create_sent_quote(admin_conn, repo)

    outcomes: list[str] = []
    lock = threading.Lock()

    def _attempt(outcome: str) -> None:
        try:
            repo.close_quote(
                quote_id=quote_id,
                operator=OPERATOR,
                expected_version=4,
                outcome=outcome,
                idempotency_key=_uid("idem"),
                request_fingerprint="f" * 64,
            )
            with lock:
                outcomes.append("won")
        except CommercialOperationConflictError:
            with lock:
                outcomes.append("conflict")

    threads = [
        threading.Thread(target=_attempt, args=("won",)),
        threading.Thread(target=_attempt, args=("null",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert outcomes.count("won") == 1
    assert outcomes.count("conflict") == 1

    bundle = repo.get_quote_bundle(quote_id=quote_id)
    assert bundle is not None
    assert bundle.revision.status in ("closed_won", "closed_null")
