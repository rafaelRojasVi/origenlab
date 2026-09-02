"""Read-only durable CRM + lead_intel evidence queries for the Cotizaciones
intake resolver ("Incorporar al CRM", CRM-Q2B).

Every method here is a pure SELECT against the existing api.v_commercial_*
read views (and the new api.v_lead_intel_prospect_evidence view, CRM-Q2B) --
never a write, matching commercial_operations_read.py's read-role
connection convention. Nothing here mutates Drive or durable CRM state.
"""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_api.repositories.postgres.common import (
    postgres_connection,
    require_psycopg,
)
from origenlab_api.settings import Settings


@dataclass(frozen=True)
class OrganizationMatch:
    organization_id: str
    display_name: str


@dataclass(frozen=True)
class ContactMatch:
    contact_id: str
    organization_id: str
    display_name: str | None
    primary_email: str | None


@dataclass(frozen=True)
class LeadIntelEvidence:
    organization_name: str
    contact_name: str | None
    email: str | None
    domain: str | None
    gmail_sent_count: int | None
    gmail_received_count: int | None
    gmail_last_contacted_at: str | None


@dataclass(frozen=True)
class SalesOpportunityMatch:
    sales_opportunity_id: str
    title: str
    stage: str


_TERMINAL_STAGES = ["won", "lost"]


class PostgresCustomerQuoteIntakeResolutionRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def find_organization_matches(
        self, *, name_candidate: str, limit: int = 5
    ) -> list[OrganizationMatch]:
        normalized = name_candidate.strip()
        if not normalized:
            return []

        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT organization_id, display_name
                    FROM api.v_commercial_organization
                    WHERE display_name ILIKE %(pattern)s
                    ORDER BY display_name
                    LIMIT %(limit)s
                    """,
                    {"pattern": f"%{normalized}%", "limit": limit},
                )
                return [OrganizationMatch(**dict(row)) for row in cur.fetchall()]

    def find_contacts_for_organization(
        self, *, organization_id: str, limit: int = 5
    ) -> list[ContactMatch]:
        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT contact_id, organization_id, display_name, primary_email
                    FROM api.v_commercial_contact
                    WHERE organization_id = %(organization_id)s
                    ORDER BY display_name NULLS LAST
                    LIMIT %(limit)s
                    """,
                    {"organization_id": organization_id, "limit": limit},
                )
                return [ContactMatch(**dict(row)) for row in cur.fetchall()]

    def find_lead_intel_evidence(
        self, *, name_candidate: str, limit: int = 5
    ) -> list[LeadIntelEvidence]:
        normalized = name_candidate.strip()
        if not normalized:
            return []

        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT organization_name, contact_name, email, domain,
                           gmail_sent_count, gmail_received_count,
                           gmail_last_contacted_at
                    FROM api.v_lead_intel_prospect_evidence
                    WHERE organization_name ILIKE %(pattern)s
                    ORDER BY gmail_sent_count DESC NULLS LAST
                    LIMIT %(limit)s
                    """,
                    {"pattern": f"%{normalized}%", "limit": limit},
                )
                return [LeadIntelEvidence(**dict(row)) for row in cur.fetchall()]

    def find_open_sales_opportunity_for_organization(
        self, *, organization_id: str
    ) -> SalesOpportunityMatch | None:
        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT sales_opportunity_id, title, stage
                    FROM api.v_commercial_sales_opportunity
                    WHERE organization_id = %(organization_id)s
                      AND NOT (stage = ANY(%(terminal)s))
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    {"organization_id": organization_id, "terminal": _TERMINAL_STAGES},
                )
                row = cur.fetchone()
                return SalesOpportunityMatch(**dict(row)) if row else None

    def document_number_in_use(self, *, document_number: str) -> bool:
        return self._exists(
            "SELECT 1 FROM api.v_commercial_customer_quote WHERE document_number = %(value)s LIMIT 1",
            document_number,
        )

    def quote_number_in_use(self, *, quote_number: str) -> bool:
        return self._exists(
            "SELECT 1 FROM api.v_commercial_customer_quote WHERE quote_number = %(value)s LIMIT 1",
            quote_number,
        )

    def _exists(self, sql: str, value: str) -> bool:
        pg = require_psycopg()
        with postgres_connection(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"value": value})
                return cur.fetchone() is not None
