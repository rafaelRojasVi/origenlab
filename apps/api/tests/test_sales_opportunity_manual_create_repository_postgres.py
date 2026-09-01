"""Real-Postgres coverage for manual (non-PR3) sales-opportunity creation.

Requires a disposable Postgres migrated to Alembic head (through 0042); set
ORIGENLAB_TEST_POSTGRES_URL to run.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
    PostgresCommercialOperationsRepository,
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
        "Set ORIGENLAB_TEST_POSTGRES_URL to a disposable Postgres migrated "
        "to Alembic head (0042+) to run manual sales-opportunity tests."
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
def repo(settings: Settings) -> PostgresCommercialOperationsRepository:
    return PostgresCommercialOperationsRepository(settings)


@pytest.fixture(autouse=True)
def _clean_slate(admin_conn: object) -> Iterator[None]:
    with admin_conn.cursor() as cur:
        cur.execute("DELETE FROM commercial.sales_opportunity_event")
        cur.execute("DELETE FROM commercial.command_idempotency")
        cur.execute("DELETE FROM commercial.sales_opportunity")
        cur.execute("DELETE FROM commercial.contact_source")
        cur.execute("DELETE FROM commercial.contact")
        cur.execute("DELETE FROM commercial.organization_source")
        cur.execute("DELETE FROM commercial.organization")
    yield


def test_creates_a_manual_sales_opportunity_with_no_organization(
    repo: PostgresCommercialOperationsRepository,
) -> None:
    sales_id = _uid("sales")

    result = repo.create_manual_sales_opportunity(
        sales_opportunity_id=sales_id,
        title="Centrífuga refrigerada para laboratorio clínico",
        owner_key=OPERATOR,
        organization_id=None,
        organization_display_name=None,
        contact_id=None,
        contact_display_name=None,
        contact_email=None,
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="a" * 64,
    )

    assert result.sales_opportunity_id == sales_id
    assert result.source_kind == "manual"
    assert result.source_opportunity_id == sales_id
    assert result.stage == "new"
    assert result.version == 1
    assert result.organization_id is None
    assert result.primary_crm_contact_id is None


def test_creates_a_manual_sales_opportunity_with_a_new_organization_and_contact(
    repo: PostgresCommercialOperationsRepository,
    admin_conn: object,
) -> None:
    sales_id = _uid("sales")

    result = repo.create_manual_sales_opportunity(
        sales_opportunity_id=sales_id,
        title="Autoclave de mesa 23L",
        owner_key=OPERATOR,
        organization_id=None,
        organization_display_name="Hospital Regional de Rancagua",
        contact_id=None,
        contact_display_name="Marcela Soto",
        contact_email="marcela.soto@hospitalrancagua.cl",
        operator=OPERATOR,
        idempotency_key=_uid("idem"),
        request_fingerprint="b" * 64,
    )

    assert result.organization_id is not None
    assert result.primary_crm_contact_id is not None

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT display_name FROM commercial.organization WHERE organization_id = %(id)s",
            {"id": result.organization_id},
        )
        org_row = cur.fetchone()
        assert org_row["display_name"] == "Hospital Regional de Rancagua"

        cur.execute(
            "SELECT display_name, primary_email FROM commercial.contact WHERE contact_id = %(id)s",
            {"id": result.primary_crm_contact_id},
        )
        contact_row = cur.fetchone()
        assert contact_row["display_name"] == "Marcela Soto"
        assert contact_row["primary_email"] == "marcela.soto@hospitalrancagua.cl"

        # A manual organization/contact has no PR2 provenance row.
        cur.execute(
            "SELECT count(*) AS n FROM commercial.organization_source WHERE organization_id = %(id)s",
            {"id": result.organization_id},
        )
        assert cur.fetchone()["n"] == 0


def test_rejects_an_unknown_existing_organization_id(
    repo: PostgresCommercialOperationsRepository,
) -> None:
    with pytest.raises(CommercialOperationNotFoundError):
        repo.create_manual_sales_opportunity(
            sales_opportunity_id=_uid("sales"),
            title="Equipo de PCR en tiempo real",
            owner_key=OPERATOR,
            organization_id="org_does_not_exist",
            organization_display_name=None,
            contact_id=None,
            contact_display_name=None,
            contact_email=None,
            operator=OPERATOR,
            idempotency_key=_uid("idem"),
            request_fingerprint="c" * 64,
        )


def test_idempotency_replay_returns_the_same_row(
    repo: PostgresCommercialOperationsRepository,
) -> None:
    key = _uid("idem")
    first = repo.create_manual_sales_opportunity(
        sales_opportunity_id=_uid("sales"),
        title="Balanza analítica de precisión",
        owner_key=OPERATOR,
        organization_id=None,
        organization_display_name=None,
        contact_id=None,
        contact_display_name=None,
        contact_email=None,
        operator=OPERATOR,
        idempotency_key=key,
        request_fingerprint="d" * 64,
    )

    second = repo.create_manual_sales_opportunity(
        sales_opportunity_id=_uid("sales"),
        title="Balanza analítica de precisión",
        owner_key=OPERATOR,
        organization_id=None,
        organization_display_name=None,
        contact_id=None,
        contact_display_name=None,
        contact_email=None,
        operator=OPERATOR,
        idempotency_key=key,
        request_fingerprint="d" * 64,
    )

    assert second.sales_opportunity_id == first.sales_opportunity_id


def test_idempotency_key_reused_with_different_request_conflicts(
    repo: PostgresCommercialOperationsRepository,
) -> None:
    key = _uid("idem")
    repo.create_manual_sales_opportunity(
        sales_opportunity_id=_uid("sales"),
        title="Microscopio invertido",
        owner_key=OPERATOR,
        organization_id=None,
        organization_display_name=None,
        contact_id=None,
        contact_display_name=None,
        contact_email=None,
        operator=OPERATOR,
        idempotency_key=key,
        request_fingerprint="e" * 64,
    )

    with pytest.raises(CommercialOperationConflictError):
        repo.create_manual_sales_opportunity(
            sales_opportunity_id=_uid("sales"),
            title="Microscopio invertido, otra versión",
            owner_key=OPERATOR,
            organization_id=None,
            organization_display_name=None,
            contact_id=None,
            contact_display_name=None,
            contact_email=None,
            operator=OPERATOR,
            idempotency_key=key,
            request_fingerprint="f" * 64,
        )
