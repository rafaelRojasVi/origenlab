"""Real-Postgres coverage for the intake-resolution read repository
(CRM-Q2B). Requires a disposable Postgres migrated to Alembic head -- see
test_customer_quote_workflow_repository_postgres.py's module docstring.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Iterator

import pytest

from origenlab_api.repositories.postgres.common import normalize_postgres_url
from origenlab_api.repositories.postgres.customer_quote_intake_resolution import (
    PostgresCustomerQuoteIntakeResolutionRepository,
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
        "Alembic head to run CRM-Q2B intake resolution repository tests."
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
NOW = datetime.now(timezone.utc)


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
def repo() -> PostgresCustomerQuoteIntakeResolutionRepository:
    url = _postgres_test_url_ready()
    assert url is not None
    return PostgresCustomerQuoteIntakeResolutionRepository(_settings(url))


@pytest.fixture(autouse=True)
def _clean_slate(admin_conn: object) -> Iterator[None]:
    with admin_conn.cursor() as cur:
        cur.execute("DELETE FROM lead_intel.evidence")
        cur.execute("DELETE FROM lead_intel.recommendation")
        cur.execute("DELETE FROM lead_intel.prospect")
        cur.execute("DELETE FROM commercial.customer_quote_event")
        cur.execute("DELETE FROM commercial.customer_quote_drive_workspace")
        cur.execute("DELETE FROM commercial.customer_quote_revision")
        cur.execute("DELETE FROM commercial.customer_quote")
        cur.execute("DELETE FROM commercial.customer_quote_number_series")
        cur.execute("DELETE FROM commercial.command_idempotency")
        cur.execute("DELETE FROM commercial.sales_opportunity_event")
        cur.execute("DELETE FROM commercial.sales_opportunity")
        cur.execute("DELETE FROM commercial.contact_source")
        cur.execute("DELETE FROM commercial.contact")
        cur.execute("DELETE FROM commercial.organization_source")
        cur.execute("DELETE FROM commercial.organization")
    yield


def _seed_organization(admin_conn: object, *, organization_id: str, display_name: str) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.organization (
              organization_id, display_name, primary_domain,
              version, created_by, updated_by, created_at, updated_at
            ) VALUES (
              %(id)s, %(name)s, NULL, 1, %(op)s, %(op)s, %(now)s, %(now)s
            )
            """,
            {"id": organization_id, "name": display_name, "op": OPERATOR, "now": NOW},
        )


def _seed_contact(
    admin_conn: object,
    *,
    contact_id: str,
    organization_id: str,
    display_name: str | None,
    primary_email: str | None,
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.contact (
              contact_id, organization_id, display_name, primary_email,
              version, created_by, updated_by, created_at, updated_at
            ) VALUES (
              %(id)s, %(org_id)s, %(name)s, %(email)s, 1, %(op)s, %(op)s, %(now)s, %(now)s
            )
            """,
            {
                "id": contact_id,
                "org_id": organization_id,
                "name": display_name,
                "email": primary_email,
                "op": OPERATOR,
                "now": NOW,
            },
        )


def _seed_sales_opportunity(
    admin_conn: object, *, sales_opportunity_id: str, organization_id: str | None, stage: str = "new"
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.sales_opportunity (
              sales_opportunity_id, source_kind, source_opportunity_id,
              title, stage, owner_key, organization_id,
              version, created_by, updated_by, created_at, updated_at
            ) VALUES (
              %(id)s, 'manual', %(id)s, 'ICN Chile — Cotización', %(stage)s, %(op)s, %(org_id)s,
              1, %(op)s, %(op)s, %(now)s, %(now)s
            )
            """,
            {"id": sales_opportunity_id, "stage": stage, "op": OPERATOR, "org_id": organization_id, "now": NOW},
        )


def _seed_prospect(
    admin_conn: object,
    *,
    prospect_key: str,
    organization_name: str,
    contact_name: str | None,
    email: str | None,
    gmail_sent_count: int | None = None,
) -> None:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lead_intel.prospect (
              prospect_key, organization_name, contact_name, email,
              classification, status, gmail_sent_count
            ) VALUES (
              %(key)s, %(org_name)s, %(contact_name)s, %(email)s,
              'prospect', 'new', %(sent_count)s
            )
            """,
            {
                "key": prospect_key,
                "org_name": organization_name,
                "contact_name": contact_name,
                "email": email,
                "sent_count": gmail_sent_count,
            },
        )


def test_find_organization_matches_normalizes_case_and_whitespace(
    admin_conn: object, repo: PostgresCustomerQuoteIntakeResolutionRepository
) -> None:
    org_id = _uid("org")
    _seed_organization(admin_conn, organization_id=org_id, display_name="ICN Chile")

    matches = repo.find_organization_matches(name_candidate="  icn chile  ")

    assert any(m.organization_id == org_id and m.display_name == "ICN Chile" for m in matches)


def test_find_organization_matches_returns_empty_for_no_match(
    repo: PostgresCustomerQuoteIntakeResolutionRepository,
) -> None:
    assert repo.find_organization_matches(name_candidate="Nonexistent Corp XYZ") == []


def test_find_contacts_for_organization_scoped_to_organization_id(
    admin_conn: object, repo: PostgresCustomerQuoteIntakeResolutionRepository
) -> None:
    org_a = _uid("org")
    org_b = _uid("org")
    _seed_organization(admin_conn, organization_id=org_a, display_name="ICN Chile")
    _seed_organization(admin_conn, organization_id=org_b, display_name="Other Corp")
    _seed_contact(admin_conn, contact_id=_uid("contact"), organization_id=org_a, display_name="Ana", primary_email="ana@icn.cl")
    _seed_contact(admin_conn, contact_id=_uid("contact"), organization_id=org_a, display_name="Bruno", primary_email="bruno@icn.cl")
    _seed_contact(admin_conn, contact_id=_uid("contact"), organization_id=org_b, display_name="Carla", primary_email="carla@other.cl")

    contacts = repo.find_contacts_for_organization(organization_id=org_a)

    assert len(contacts) == 2
    assert all(c.organization_id == org_a for c in contacts)


def test_find_lead_intel_evidence_matches_by_organization_name(
    admin_conn: object, repo: PostgresCustomerQuoteIntakeResolutionRepository
) -> None:
    _seed_prospect(
        admin_conn,
        prospect_key=_uid("prospect"),
        organization_name="ICN Chile",
        contact_name="Ana Example",
        email="ana.example@icn.example",
        gmail_sent_count=8,
    )

    evidence = repo.find_lead_intel_evidence(name_candidate="ICN Chile")

    assert len(evidence) == 1
    assert evidence[0].gmail_sent_count == 8
    assert evidence[0].email == "ana.example@icn.example"


def test_find_open_sales_opportunity_excludes_terminal_stages(
    admin_conn: object, repo: PostgresCustomerQuoteIntakeResolutionRepository
) -> None:
    org_id = _uid("org")
    _seed_organization(admin_conn, organization_id=org_id, display_name="ICN Chile")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=_uid("sales"), organization_id=org_id, stage="won")

    assert repo.find_open_sales_opportunity_for_organization(organization_id=org_id) is None


def test_find_open_sales_opportunity_returns_the_open_one(
    admin_conn: object, repo: PostgresCustomerQuoteIntakeResolutionRepository
) -> None:
    org_id = _uid("org")
    sales_id = _uid("sales")
    _seed_organization(admin_conn, organization_id=org_id, display_name="ICN Chile")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id, organization_id=org_id, stage="quoting")

    match = repo.find_open_sales_opportunity_for_organization(organization_id=org_id)

    assert match is not None
    assert match.sales_opportunity_id == sales_id


def test_document_number_and_quote_number_conflict_checks(
    admin_conn: object, repo: PostgresCustomerQuoteIntakeResolutionRepository
) -> None:
    org_id = _uid("org")
    sales_id = _uid("sales")
    _seed_organization(admin_conn, organization_id=org_id, display_name="ICN Chile")
    _seed_sales_opportunity(admin_conn, sales_opportunity_id=sales_id, organization_id=org_id)

    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO commercial.customer_quote (
              quote_id, sales_opportunity_id, quote_number, document_number,
              quote_origin, serial, issue_year, status, version,
              created_by, updated_by, created_at, updated_at
            ) VALUES (
              %(quote_id)s, %(sales_id)s, '01191-26', 'CN01191', 'adopted', NULL, NULL,
              'draft', 1, %(op)s, %(op)s, %(now)s, %(now)s
            )
            """,
            {"quote_id": _uid("quote"), "sales_id": sales_id, "op": OPERATOR, "now": NOW},
        )

    assert repo.document_number_in_use(document_number="CN01191") is True
    assert repo.document_number_in_use(document_number="CN99999") is False
    assert repo.quote_number_in_use(quote_number="01191-26") is True
    assert repo.quote_number_in_use(quote_number="99999-26") is False
