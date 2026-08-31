"""Read-only durable customer-quote repository (CRM-Q1).

Reads exclusively through ``api.*`` views with the read-role connection, like
every other durable CRM read model.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.repositories.postgres.common import (
    postgres_connection,
    require_psycopg,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuote,
    CustomerQuoteBundle,
    CustomerQuoteDriveWorkspace,
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
  so.title AS sales_opportunity_title,
  r.revision_number AS revision_revision_number,
  r.template_reference AS revision_template_reference,
  r.status AS revision_status,
  r.created_by AS revision_created_by,
  r.created_at AS revision_created_at,
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
    created_at
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
