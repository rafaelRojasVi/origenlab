"""Real-Postgres coverage for CRM-Q2 revision-workflow commands and
"Incorporar al CRM" Drive-folder adoption.

Requires a disposable Postgres migrated to Alembic head:

    cd apps/email-pipeline
    ALEMBIC_DATABASE_URL=$ORIGENLAB_TEST_POSTGRES_URL uv run alembic upgrade head

Then set ORIGENLAB_TEST_POSTGRES_URL and run pytest directly (excluded from
apps/api/scripts/validate.sh's default SQLite-backed run, matching the
existing CRM-Q1A/CRM-4A integration pattern).

No mocks: every test exercises PostgresCustomerQuoteRepository against a
real connection, so the CHECK constraints, the sum-type origin/serial
shape, the single customer_quote.version CAS token, and the append-only
event trail are all actually proven.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    CUSTOMER_QUOTE_SERIES_KEY,
    PostgresCustomerQuoteRepository,
    QuoteNumberingConfig,
)
from origenlab_api.repositories.postgres.customer_quotes_read import (
    PostgresCustomerQuoteReadRepository,
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
        "Alembic head to run CRM-Q2 workflow/adoption tests."
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


@pytest.fixture
def read_repo() -> PostgresCustomerQuoteReadRepository:
    url = _postgres_test_url_ready()
    assert url is not None
    return PostgresCustomerQuoteReadRepository(_settings(url))


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


def _create_generated_quote(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
) -> tuple[str, str]:
    """Seed a sales opportunity + a normal generated-flow quote. Returns
    (sales_opportunity_id, quote_id)."""

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
    return sales_id, bundle.quote.quote_id


# --- submit_for_review ------------------------------------------------


def test_submit_for_review_from_draft_transitions_to_pending_approval(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)

    bundle = repo.submit_for_review(
        quote_id=quote_id, operator=OPERATOR, expected_version=1
    )

    assert bundle.revision.status == "pending_approval"
    assert bundle.quote.version == 2


def test_submit_for_review_from_adjustments_requested_transitions_to_pending_approval(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)
    repo.request_adjustments(quote_id=quote_id, operator=OPERATOR, expected_version=2)

    bundle = repo.submit_for_review(
        quote_id=quote_id, operator=OPERATOR, expected_version=3
    )

    assert bundle.revision.status == "pending_approval"
    assert bundle.quote.version == 4


@pytest.mark.parametrize(
    "setup_to_status",
    ["pending_approval", "approved", "sent"],
)
def test_submit_for_review_refuses_from_illegal_statuses(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
    setup_to_status: str,
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    version = 1
    if setup_to_status in ("pending_approval", "approved", "sent"):
        repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
    if setup_to_status in ("approved", "sent"):
        repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
    if setup_to_status == "sent":
        repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1

    with pytest.raises(CommercialOperationConflictError):
        repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=version)


# --- request_adjustments -----------------------------------------------


def test_request_adjustments_from_pending_approval_succeeds(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)

    bundle = repo.request_adjustments(
        quote_id=quote_id, operator=OPERATOR, expected_version=2
    )

    assert bundle.revision.status == "adjustments_requested"
    assert bundle.quote.version == 3


@pytest.mark.parametrize("illegal_from", ["draft", "approved", "sent"])
def test_request_adjustments_refuses_from_illegal_statuses(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
    illegal_from: str,
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    version = 1

    if illegal_from == "draft":
        with pytest.raises(CommercialOperationConflictError):
            repo.request_adjustments(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        return

    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=version)
    version += 1

    if illegal_from == "approved":
        repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
        with pytest.raises(CommercialOperationConflictError):
            repo.request_adjustments(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        return

    if illegal_from == "sent":
        repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
        repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
        with pytest.raises(CommercialOperationConflictError):
            repo.request_adjustments(quote_id=quote_id, operator=OPERATOR, expected_version=version)


# --- approve -------------------------------------------------------------


def test_approve_from_pending_approval_succeeds(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)

    bundle = repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=2)

    assert bundle.revision.status == "approved"
    assert bundle.quote.version == 3


@pytest.mark.parametrize("illegal_from", ["draft", "adjustments_requested", "sent"])
def test_approve_refuses_from_illegal_statuses(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
    illegal_from: str,
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    version = 1

    if illegal_from == "draft":
        with pytest.raises(CommercialOperationConflictError):
            repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        return

    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=version)
    version += 1

    if illegal_from == "adjustments_requested":
        repo.request_adjustments(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
        with pytest.raises(CommercialOperationConflictError):
            repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        return

    if illegal_from == "sent":
        repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
        repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
        with pytest.raises(CommercialOperationConflictError):
            repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=version)


# --- confirm_send --------------------------------------------------------


def test_confirm_send_from_approved_succeeds(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)
    repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=2)

    bundle = repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=3)

    assert bundle.revision.status == "sent"
    assert bundle.quote.version == 4


@pytest.mark.parametrize("illegal_from", ["draft", "pending_approval", "adjustments_requested"])
def test_confirm_send_refuses_from_illegal_statuses(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
    illegal_from: str,
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    version = 1

    if illegal_from == "draft":
        with pytest.raises(CommercialOperationConflictError):
            repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        return

    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=version)
    version += 1

    if illegal_from == "pending_approval":
        with pytest.raises(CommercialOperationConflictError):
            repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        return

    if illegal_from == "adjustments_requested":
        repo.request_adjustments(quote_id=quote_id, operator=OPERATOR, expected_version=version)
        version += 1
        with pytest.raises(CommercialOperationConflictError):
            repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=version)


def test_confirm_send_never_moves_or_touches_drive_workspace(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    """confirm_send is a Postgres-only transition: it must never modify
    customer_quote_drive_workspace (no desired-bucket/Drive move in this
    slice)."""

    _, quote_id = _create_generated_quote(admin_conn, repo)
    before = repo.get_quote_bundle(quote_id=quote_id)
    assert before is not None
    workspace_before = before.workspace

    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)
    repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=2)
    repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=3)

    after = repo.get_quote_bundle(quote_id=quote_id)
    assert after is not None
    assert after.workspace.version == workspace_before.version
    assert after.workspace.provisioning_status == workspace_before.provisioning_status


# --- expected_version conflict -------------------------------------------


def test_transition_with_stale_expected_version_conflicts(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)

    with pytest.raises(CommercialOperationConflictError):
        repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=99)


def test_transition_on_missing_quote_raises_not_found(
    repo: PostgresCustomerQuoteRepository,
) -> None:
    with pytest.raises(CommercialOperationNotFoundError):
        repo.submit_for_review(
            quote_id=_uid("quote"), operator=OPERATOR, expected_version=1
        )


# --- event append + no revision-level version ----------------------------


def test_each_transition_appends_exactly_one_event_with_from_to_status(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)
    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)

    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_type, payload
            FROM commercial.customer_quote_event
            WHERE quote_id = %(quote_id)s AND event_type = 'quote_submitted_for_review'
            """,
            {"quote_id": quote_id},
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["revision_number"] == 1
    assert payload["from_status"] == "draft"
    assert payload["to_status"] == "pending_approval"


def test_customer_quote_revision_has_no_version_column(
    admin_conn: object,
) -> None:
    """customer_quote.version is the sole CAS token -- proves the migration
    (and this repository) never introduced a second counter."""

    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'commercial'
              AND table_name = 'customer_quote_revision'
              AND column_name = 'version'
            """
        )
        assert cur.fetchall() == []


def test_transitions_never_touch_customer_quote_number_series(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    _, quote_id = _create_generated_quote(admin_conn, repo)

    with admin_conn.cursor() as cur:
        cur.execute("SELECT next_serial FROM commercial.customer_quote_number_series")
        before = cur.fetchall()

    repo.submit_for_review(quote_id=quote_id, operator=OPERATOR, expected_version=1)
    repo.approve(quote_id=quote_id, operator=OPERATOR, expected_version=2)
    repo.confirm_send(quote_id=quote_id, operator=OPERATOR, expected_version=3)

    with admin_conn.cursor() as cur:
        cur.execute("SELECT next_serial FROM commercial.customer_quote_number_series")
        after = cur.fetchall()

    assert before == after


# --- adopt_drive_folder ---------------------------------------------------


def test_adopt_drive_folder_creates_quote_revision_and_ready_workspace(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    bundle = repo.adopt_drive_folder(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        document_number="CN01191",
        quote_number="01191-24",
        folder_id="drive-folder-1191",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-1191",
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    assert bundle.quote.quote_origin == "adopted"
    assert bundle.quote.serial is None
    assert bundle.quote.issue_year is None
    assert bundle.quote.document_number == "CN01191"
    assert bundle.quote.quote_number == "01191-24"
    assert bundle.revision.revision_number == 1
    assert bundle.revision.status == "draft"
    assert bundle.workspace.provisioning_status == "ready"
    assert bundle.workspace.folder_id == "drive-folder-1191"
    assert bundle.workspace.sheet_file_id is None


def test_adopt_drive_folder_appends_adoption_event(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    quote_id = _uid("quote")

    repo.adopt_drive_folder(
        quote_id=quote_id,
        sales_opportunity_id=sales_id,
        document_number="CN01190",
        quote_number="01190-24",
        folder_id="drive-folder-1190",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-1190",
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_type, payload
            FROM commercial.customer_quote_event
            WHERE quote_id = %(quote_id)s
            """,
            {"quote_id": quote_id},
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0]["event_type"] == "quote_adopted_from_drive"
    assert rows[0]["payload"]["document_number"] == "CN01190"
    assert rows[0]["payload"]["folder_id"] == "drive-folder-1190"


def test_adopt_drive_folder_is_idempotent_on_replay(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)
    key = _uid("idem")

    first = repo.adopt_drive_folder(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        document_number="CN01185",
        quote_number="01185-24",
        folder_id="drive-folder-1185",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-1185",
        operator=OPERATOR,
        idempotency_key=key,
        request_fingerprint="f" * 64,
    )

    second = repo.adopt_drive_folder(
        quote_id=_uid("quote"),  # different quote_id: proves the replay wins, not this
        sales_opportunity_id=sales_id,
        document_number="CN01185",
        quote_number="01185-24",
        folder_id="drive-folder-1185",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-1185",
        operator=OPERATOR,
        idempotency_key=key,
        request_fingerprint="f" * 64,
    )

    assert second.quote.quote_id == first.quote.quote_id

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM commercial.customer_quote WHERE document_number = 'CN01185'"
        )
        assert cur.fetchone()["n"] == 1


def test_adopt_drive_folder_never_consumes_a_number_series_serial(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    repo.adopt_drive_folder(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        document_number="CN01199",
        quote_number="01199-24",
        folder_id="drive-folder-1199",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-1199",
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM commercial.customer_quote_number_series"
        )
        assert cur.fetchone()["n"] == 0


def test_adopt_drive_folder_never_derives_quote_number_from_document_number(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    """document_number and quote_number are independent operator-confirmed
    inputs -- a deliberately unrelated pair must be stored verbatim, never
    reconciled/derived."""

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    bundle = repo.adopt_drive_folder(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        document_number="CN09999",
        quote_number="00042-19",  # deliberately unrelated to CN09999
        folder_id="drive-folder-9999",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-9999",
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    assert bundle.quote.document_number == "CN09999"
    assert bundle.quote.quote_number == "00042-19"


def test_adopt_drive_folder_rejects_duplicate_document_number(
    admin_conn: object, repo: PostgresCustomerQuoteRepository
) -> None:
    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    repo.adopt_drive_folder(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        document_number="CN05000",
        quote_number="05000-24",
        folder_id="drive-folder-5000",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-5000",
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    with pytest.raises(CommercialOperationConflictError):
        repo.adopt_drive_folder(
            quote_id=_uid("quote"),
            sales_opportunity_id=sales_id,
            document_number="CN05000",
            quote_number="05001-24",
            folder_id="drive-folder-5001",
            folder_web_url="https://drive.google.com/drive/folders/drive-folder-5001",
            operator=OPERATOR,
            idempotency_key=_uid("idem"),
            request_fingerprint="f" * 64,
        )


def test_adopt_drive_folder_missing_sales_opportunity_raises_not_found(
    repo: PostgresCustomerQuoteRepository,
) -> None:
    with pytest.raises(CommercialOperationNotFoundError):
        repo.adopt_drive_folder(
            quote_id=_uid("quote"),
            sales_opportunity_id=_uid("sales"),
            document_number="CN01000",
            quote_number="01000-24",
            folder_id="drive-folder-1000",
            folder_web_url="https://drive.google.com/drive/folders/drive-folder-1000",
            operator=OPERATOR,
            idempotency_key=_uid("idem"),
            request_fingerprint="f" * 64,
        )


def test_adopted_folder_disappears_from_known_drive_folder_ids_dedup_set(
    admin_conn: object,
    repo: PostgresCustomerQuoteRepository,
    read_repo: PostgresCustomerQuoteReadRepository,
) -> None:
    """The drive-pending projection's dedup key is folder_id on
    customer_quote_drive_workspace -- adoption must populate exactly that,
    with no separate dedup mechanism needed."""

    sales_id = _uid("sales")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id)

    assert "drive-folder-adopted-1" not in read_repo.list_known_drive_folder_ids()

    repo.adopt_drive_folder(
        quote_id=_uid("quote"),
        sales_opportunity_id=sales_id,
        document_number="CN07777",
        quote_number="07777-24",
        folder_id="drive-folder-adopted-1",
        folder_web_url="https://drive.google.com/drive/folders/drive-folder-adopted-1",
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="f" * 64,
    )

    assert "drive-folder-adopted-1" in read_repo.list_known_drive_folder_ids()
