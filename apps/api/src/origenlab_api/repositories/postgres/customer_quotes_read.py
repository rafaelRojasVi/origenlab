"""Read-only durable customer-quote repository (CRM-Q1).

Reads exclusively through ``api.*`` views with the read-role connection, like
every other durable CRM read model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from origenlab_api.repositories.postgres.common import (
    postgres_connection,
    require_psycopg,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuote,
    CustomerQuoteBundle,
    CustomerQuoteDriveWorkspace,
    CustomerQuoteEvent,
    CustomerQuoteRevision,
)
from origenlab_api.settings import Settings


_BUNDLE_SELECT_SQL = """
SELECT
  q.quote_id,
  q.sales_opportunity_id,
  q.quote_number,
  q.status,
  q.version,
  q.created_by,
  q.updated_by,
  q.created_at,
  q.updated_at,
  q.serial,
  q.issue_year,
  q.document_number,
  q.quote_origin,
  so.title AS sales_opportunity_title,
  r.revision_number AS revision_revision_number,
  r.template_reference AS revision_template_reference,
  r.status AS revision_status,
  r.created_by AS revision_created_by,
  r.created_at AS revision_created_at,
  r.updated_by AS revision_updated_by,
  r.updated_at AS revision_updated_at,
  w.provider AS workspace_provider,
  w.provisioning_status AS workspace_provisioning_status,
  w.folder_id AS workspace_folder_id,
  w.folder_web_url AS workspace_folder_web_url,
  w.sheet_file_id AS workspace_sheet_file_id,
  w.sheet_web_url AS workspace_sheet_web_url,
  w.failure_category AS workspace_failure_category,
  w.attempt_count AS workspace_attempt_count,
  w.version AS workspace_version,
  w.lease_expires_at AS workspace_lease_expires_at,
  w.requested_at AS workspace_requested_at,
  w.completed_at AS workspace_completed_at,
  w.created_by AS workspace_created_by,
  w.updated_by AS workspace_updated_by,
  w.created_at AS workspace_created_at,
  w.updated_at AS workspace_updated_at
FROM api.v_commercial_customer_quote q
JOIN api.v_commercial_sales_opportunity so
  ON so.sales_opportunity_id = q.sales_opportunity_id
JOIN api.v_commercial_customer_quote_drive_workspace w
  ON w.quote_id = q.quote_id
JOIN LATERAL (
  SELECT
    revision_number,
    template_reference,
    status,
    created_by,
    created_at,
    updated_by,
    updated_at
  FROM api.v_commercial_customer_quote_revision rev
  WHERE rev.quote_id = q.quote_id
  ORDER BY rev.revision_number DESC
  LIMIT 1
) r ON TRUE
"""


def _bundle_from_row(row: dict[str, Any]) -> CustomerQuoteBundle:
    return CustomerQuoteBundle(
        quote=CustomerQuote(
            quote_id=row["quote_id"],
            sales_opportunity_id=row["sales_opportunity_id"],
            quote_number=row["quote_number"],
            serial=row["serial"],
            issue_year=row["issue_year"],
            document_number=row["document_number"],
            quote_origin=row["quote_origin"],
            status=row["status"],
            version=row["version"],
            created_by=row["created_by"],
            updated_by=row["updated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        ),
        revision=CustomerQuoteRevision(
            quote_id=row["quote_id"],
            revision_number=row["revision_revision_number"],
            template_reference=row["revision_template_reference"],
            status=row["revision_status"],
            created_by=row["revision_created_by"],
            created_at=row["revision_created_at"],
            updated_by=row["revision_updated_by"],
            updated_at=row["revision_updated_at"],
        ),
        workspace=CustomerQuoteDriveWorkspace(
            quote_id=row["quote_id"],
            provider=row["workspace_provider"],
            provisioning_status=row["workspace_provisioning_status"],
            folder_id=row["workspace_folder_id"],
            folder_web_url=row["workspace_folder_web_url"],
            sheet_file_id=row["workspace_sheet_file_id"],
            sheet_web_url=row["workspace_sheet_web_url"],
            failure_category=row["workspace_failure_category"],
            attempt_count=row["workspace_attempt_count"],
            version=row["workspace_version"],
            lease_expires_at=row["workspace_lease_expires_at"],
            requested_at=row["workspace_requested_at"],
            completed_at=row["workspace_completed_at"],
            created_by=row["workspace_created_by"],
            updated_by=row["workspace_updated_by"],
            created_at=row["workspace_created_at"],
            updated_at=row["workspace_updated_at"],
        ),
        sales_opportunity_title=row["sales_opportunity_title"],
    )


@dataclass(frozen=True)
class CustomerQuoteGlobalEntry:
    bundle: CustomerQuoteBundle
    sales_opportunity_stage: str
    sales_opportunity_owner_key: str
    organization_display_name: str | None
    contact_display_name: str | None
    contact_primary_email: str | None
    next_task_title: str | None
    next_task_due_at: datetime | None


_GLOBAL_LIST_SELECT_SQL = """
SELECT
  q.quote_id,
  q.sales_opportunity_id,
  q.quote_number,
  q.status,
  q.version,
  q.created_by,
  q.updated_by,
  q.created_at,
  q.updated_at,
  q.serial,
  q.issue_year,
  q.document_number,
  q.quote_origin,
  so.title AS sales_opportunity_title,
  so.stage AS sales_opportunity_stage,
  so.owner_key AS sales_opportunity_owner_key,
  r.revision_number AS revision_revision_number,
  r.template_reference AS revision_template_reference,
  r.status AS revision_status,
  r.created_by AS revision_created_by,
  r.created_at AS revision_created_at,
  r.updated_by AS revision_updated_by,
  r.updated_at AS revision_updated_at,
  w.provider AS workspace_provider,
  w.provisioning_status AS workspace_provisioning_status,
  w.folder_id AS workspace_folder_id,
  w.folder_web_url AS workspace_folder_web_url,
  w.sheet_file_id AS workspace_sheet_file_id,
  w.sheet_web_url AS workspace_sheet_web_url,
  w.failure_category AS workspace_failure_category,
  w.attempt_count AS workspace_attempt_count,
  w.version AS workspace_version,
  w.lease_expires_at AS workspace_lease_expires_at,
  w.requested_at AS workspace_requested_at,
  w.completed_at AS workspace_completed_at,
  w.created_by AS workspace_created_by,
  w.updated_by AS workspace_updated_by,
  w.created_at AS workspace_created_at,
  w.updated_at AS workspace_updated_at,
  crm_org.display_name AS organization_display_name,
  crm_contact.display_name AS contact_display_name,
  crm_contact.primary_email AS contact_primary_email,
  nt.next_task_title,
  nt.next_task_due_at
FROM api.v_commercial_customer_quote q
JOIN api.v_commercial_sales_opportunity so
  ON so.sales_opportunity_id = q.sales_opportunity_id
JOIN api.v_commercial_customer_quote_drive_workspace w
  ON w.quote_id = q.quote_id
JOIN LATERAL (
  SELECT
    revision_number,
    template_reference,
    status,
    created_by,
    created_at,
    updated_by,
    updated_at
  FROM api.v_commercial_customer_quote_revision rev
  WHERE rev.quote_id = q.quote_id
  ORDER BY rev.revision_number DESC
  LIMIT 1
) r ON TRUE
LEFT JOIN api.v_commercial_organization crm_org
  ON crm_org.organization_id = so.organization_id
LEFT JOIN api.v_commercial_contact crm_contact
  ON crm_contact.contact_id = so.primary_crm_contact_id
LEFT JOIN LATERAL (
  SELECT
    t.title AS next_task_title,
    t.due_at AS next_task_due_at
  FROM api.v_commercial_task t
  WHERE t.sales_opportunity_id = so.sales_opportunity_id
    AND t.status = 'open'
  ORDER BY t.due_at NULLS LAST, t.created_at DESC
  LIMIT 1
) nt ON TRUE
"""


def _global_entry_from_row(row: dict[str, Any]) -> CustomerQuoteGlobalEntry:
    return CustomerQuoteGlobalEntry(
        bundle=_bundle_from_row(row),
        sales_opportunity_stage=row["sales_opportunity_stage"],
        sales_opportunity_owner_key=row["sales_opportunity_owner_key"],
        organization_display_name=row["organization_display_name"],
        contact_display_name=row["contact_display_name"],
        contact_primary_email=row["contact_primary_email"],
        next_task_title=row["next_task_title"],
        next_task_due_at=row["next_task_due_at"],
    )


class PostgresCustomerQuoteReadRepository:
    """Read durable customer-quote state exclusively through api.* views."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def list_for_sales_opportunity(
        self,
        sales_opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[CustomerQuoteBundle]:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    _BUNDLE_SELECT_SQL
                    + """
                    WHERE q.sales_opportunity_id = %(sales_opportunity_id)s
                    ORDER BY q.created_at DESC
                    LIMIT %(limit)s
                    """,
                    {
                        "sales_opportunity_id": sales_opportunity_id,
                        "limit": limit,
                    },
                )

                return [_bundle_from_row(dict(row)) for row in cur.fetchall()]

    def get(self, quote_id: str) -> CustomerQuoteBundle | None:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    _BUNDLE_SELECT_SQL
                    + """
                    WHERE q.quote_id = %(quote_id)s
                    LIMIT 1
                    """,
                    {"quote_id": quote_id},
                )

                row = cur.fetchone()

                if row is None:
                    return None

                return _bundle_from_row(dict(row))

    def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        drive_status: list[str] | None = None,
        stage: list[str] | None = None,
    ) -> tuple[list[CustomerQuoteGlobalEntry], int]:
        pg = require_psycopg()

        params = {
            "drive_status": drive_status,
            "stage": stage,
            "limit": limit,
            "offset": offset,
        }

        filters_sql = """
        (%(drive_status)s::text[] IS NULL OR w.provisioning_status = ANY(%(drive_status)s))
        AND (%(stage)s::text[] IS NULL OR so.stage = ANY(%(stage)s))
        """

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    f"""
                    SELECT count(*) AS total
                    FROM api.v_commercial_customer_quote q
                    JOIN api.v_commercial_sales_opportunity so
                      ON so.sales_opportunity_id = q.sales_opportunity_id
                    JOIN api.v_commercial_customer_quote_drive_workspace w
                      ON w.quote_id = q.quote_id
                    WHERE {filters_sql}
                    """,
                    params,
                )
                total_row = cur.fetchone()
                total_count = int(total_row["total"]) if total_row else 0

                cur.execute(
                    _GLOBAL_LIST_SELECT_SQL
                    + f"""
                    WHERE {filters_sql}
                    ORDER BY q.created_at DESC
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    params,
                )

                entries = [_global_entry_from_row(dict(row)) for row in cur.fetchall()]

                return entries, total_count

    def list_events(
        self,
        quote_id: str,
        *,
        limit: int = 200,
    ) -> list[CustomerQuoteEvent]:
        """Append-only audit trail for the Cotizaciones drawer, most-recent
        first. Reads api.v_commercial_customer_quote_event -- the raw
        commercial.customer_quote_event table stays INSERT/SELECT only for
        the writer role and is never targeted directly here."""

        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                      event_id,
                      quote_id,
                      event_type,
                      actor_key,
                      payload,
                      created_at
                    FROM api.v_commercial_customer_quote_event
                    WHERE quote_id = %(quote_id)s
                    ORDER BY created_at DESC
                    LIMIT %(limit)s
                    """,
                    {"quote_id": quote_id, "limit": limit},
                )

                return [CustomerQuoteEvent(**dict(row)) for row in cur.fetchall()]

    def list_known_drive_folder_ids(self) -> set[str]:
        """Every Drive folder_id already referenced by a durable customer
        quote workspace -- used to exclude Drive-only folders that are
        already represented in the CRM so the operator queue never shows
        the same workspace twice."""

        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT folder_id
                    FROM api.v_commercial_customer_quote_drive_workspace
                    WHERE folder_id IS NOT NULL
                    """
                )

                return {row["folder_id"] for row in cur.fetchall()}
