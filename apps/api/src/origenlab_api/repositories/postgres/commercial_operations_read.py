"""Read-only durable commercial operations repository (ARCH-3B6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from origenlab_api.repositories.postgres.common import (
    postgres_connection,
    require_psycopg,
)
from origenlab_api.repositories.postgres.commercial_operations import (
    Activity,
    OperatorState,
    SalesOpportunity,
    Task,
)
from origenlab_api.settings import Settings


_SALES_OPPORTUNITY_LIST_FILTERS_SQL = """
  (%(stages)s::text[] IS NULL OR so.stage = ANY(%(stages)s))
  AND (%(owner_key)s::text IS NULL OR so.owner_key = %(owner_key)s::text)
  AND (
    %(source_opportunity_ids)s::text[] IS NULL
    OR so.source_opportunity_id = ANY(%(source_opportunity_ids)s)
  )
"""


@dataclass(frozen=True)
class SalesOpportunityBoardItem:
    sales_opportunity_id: str
    source_kind: str
    source_opportunity_id: str
    account_id: str | None
    primary_contact_id: str | None
    organization_id: str | None
    primary_crm_contact_id: str | None
    title: str
    stage: str
    owner_key: str
    version: int
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
    stage_updated_at: datetime
    contact_display_email: str | None
    account_display_domain: str | None
    open_task_count: int
    next_task_id: str | None
    next_task_title: str | None
    next_task_due_at: datetime | None


class PostgresCommercialOperationsReadRepository:
    """Read durable human CRM state exclusively through api.* views."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_sales_opportunity(
        self,
        sales_opportunity_id: str,
    ) -> SalesOpportunity | None:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_sales_opportunity
                    WHERE sales_opportunity_id =
                      %(sales_opportunity_id)s
                    LIMIT 1
                    """,
                    {
                        "sales_opportunity_id": (sales_opportunity_id),
                    },
                )

                row = cur.fetchone()

                if row is None:
                    return None

                return SalesOpportunity(**dict(row))

    def get_operator_state(
        self,
        opportunity_id: str,
    ) -> OperatorState | None:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_opportunity_operator_state
                    WHERE opportunity_id = %(opportunity_id)s
                    LIMIT 1
                    """,
                    {"opportunity_id": opportunity_id},
                )

                row = cur.fetchone()

                if row is None:
                    return None

                return OperatorState(**dict(row))

    def list_activities_for_opportunity(
        self,
        opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Activity]:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_activity
                    WHERE opportunity_id = %(opportunity_id)s
                    ORDER BY occurred_at DESC, created_at DESC
                    LIMIT %(limit)s
                    """,
                    {
                        "opportunity_id": opportunity_id,
                        "limit": limit,
                    },
                )

                return [Activity(**dict(row)) for row in cur.fetchall()]

    def list_activities_for_sales_opportunity(
        self,
        sales_opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Activity]:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_activity
                    WHERE sales_opportunity_id =
                      %(sales_opportunity_id)s
                    ORDER BY occurred_at DESC, created_at DESC
                    LIMIT %(limit)s
                    """,
                    {
                        "sales_opportunity_id": sales_opportunity_id,
                        "limit": limit,
                    },
                )

                return [Activity(**dict(row)) for row in cur.fetchall()]

    def list_tasks_for_opportunity(
        self,
        opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Task]:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_task
                    WHERE opportunity_id = %(opportunity_id)s
                    ORDER BY
                      CASE status
                        WHEN 'open' THEN 0
                        ELSE 1
                      END,
                      due_at NULLS LAST,
                      created_at DESC
                    LIMIT %(limit)s
                    """,
                    {
                        "opportunity_id": opportunity_id,
                        "limit": limit,
                    },
                )

                return [Task(**dict(row)) for row in cur.fetchall()]

    def list_tasks_for_sales_opportunity(
        self,
        sales_opportunity_id: str,
        *,
        limit: int = 100,
    ) -> list[Task]:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM api.v_commercial_task
                    WHERE sales_opportunity_id =
                      %(sales_opportunity_id)s
                    ORDER BY
                      CASE status
                        WHEN 'open' THEN 0
                        ELSE 1
                      END,
                      due_at NULLS LAST,
                      created_at DESC
                    LIMIT %(limit)s
                    """,
                    {
                        "sales_opportunity_id": sales_opportunity_id,
                        "limit": limit,
                    },
                )

                return [Task(**dict(row)) for row in cur.fetchall()]

    def get_work_queue(
        self,
        *,
        limit: int = 100,
    ) -> tuple[
        list["CommercialWorkQueueTask"],
        list["CommercialWorkQueueOpportunity"],
        list["CommercialWorkQueueOpportunity"],
    ]:
        pg = require_psycopg()

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT
                      t.*,
                      o.contact_display_email,
                      o.account_display_domain,
                      o.canonical_stage,
                      o.review_status AS machine_review_status
                    FROM api.v_commercial_task AS t
                    LEFT JOIN api.v_commercial_opportunity AS o
                      ON o.opportunity_id = t.opportunity_id
                    WHERE t.status = 'open'
                    ORDER BY
                      t.due_at NULLS LAST,
                      CASE t.priority
                        WHEN 'urgent' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'normal' THEN 2
                        ELSE 3
                      END,
                      t.created_at DESC
                    LIMIT %(limit)s
                    """,
                    {"limit": limit},
                )

                task_rows = cur.fetchall()

                open_tasks = [
                    CommercialWorkQueueTask(
                        task=Task(
                            task_id=row["task_id"],
                            opportunity_id=row["opportunity_id"],
                            sales_opportunity_id=row["sales_opportunity_id"],
                            account_id=row["account_id"],
                            contact_id=row["contact_id"],
                            title=row["title"],
                            status=row["status"],
                            priority=row["priority"],
                            due_at=row["due_at"],
                            owner_key=row["owner_key"],
                            version=row["version"],
                            created_by=row["created_by"],
                            updated_by=row["updated_by"],
                            completed_at=row["completed_at"],
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                        ),
                        contact_display_email=row["contact_display_email"],
                        account_display_domain=row["account_display_domain"],
                        canonical_stage=row["canonical_stage"],
                        machine_review_status=row["machine_review_status"],
                    )
                    for row in task_rows
                ]

                cur.execute(
                    """
                    SELECT
                      o.opportunity_id,
                      o.contact_display_email,
                      o.account_display_domain,
                      o.canonical_stage,
                      o.review_status AS machine_review_status,
                      s.confirmation_status,
                      s.manual_stage,
                      s.owner_key,
                      s.version AS operator_state_version
                    FROM api.v_commercial_opportunity AS o
                    LEFT JOIN
                      api.v_commercial_opportunity_operator_state AS s
                      ON s.opportunity_id = o.opportunity_id
                    WHERE
                      o.stage_is_terminal = FALSE
                      AND (
                        s.confirmation_status = 'needs_review'
                        OR (
                          s.opportunity_id IS NULL
                          AND o.review_status = 'needs_review'
                        )
                      )
                    ORDER BY
                      o.last_activity_at DESC NULLS LAST,
                      o.opportunity_id
                    LIMIT %(limit)s
                    """,
                    {"limit": limit},
                )

                review_opportunities = [
                    _work_queue_opportunity(dict(row)) for row in cur.fetchall()
                ]

                cur.execute(
                    """
                    SELECT
                      o.opportunity_id,
                      o.contact_display_email,
                      o.account_display_domain,
                      o.canonical_stage,
                      o.review_status AS machine_review_status,
                      s.confirmation_status,
                      s.manual_stage,
                      s.owner_key,
                      s.version AS operator_state_version
                    FROM api.v_commercial_opportunity AS o
                    LEFT JOIN
                      api.v_commercial_opportunity_operator_state AS s
                      ON s.opportunity_id = o.opportunity_id
                    WHERE
                      o.stage_is_terminal = FALSE
                      AND o.canonical_stage = 'quote_sent'
                      AND COALESCE(
                        s.confirmation_status,
                        'needs_review'
                      ) <> 'rejected'
                    ORDER BY
                      o.last_activity_at DESC NULLS LAST,
                      o.opportunity_id
                    LIMIT %(limit)s
                    """,
                    {"limit": limit},
                )

                quote_followups = [
                    _work_queue_opportunity(dict(row)) for row in cur.fetchall()
                ]

                return (
                    open_tasks,
                    review_opportunities,
                    quote_followups,
                )

    def list_sales_opportunities(
        self,
        *,
        stages: list[str] | None = None,
        owner_key: str | None = None,
        source_opportunity_ids: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[SalesOpportunityBoardItem], int]:
        pg = require_psycopg()

        params = {
            "stages": stages,
            "owner_key": owner_key,
            "source_opportunity_ids": source_opportunity_ids,
            "limit": limit,
            "offset": offset,
        }

        with postgres_connection(self._settings) as conn:
            with conn.cursor(row_factory=pg.rows.dict_row) as cur:
                cur.execute(
                    f"""
                    SELECT count(*) AS total
                    FROM api.v_commercial_sales_opportunity so
                    WHERE {_SALES_OPPORTUNITY_LIST_FILTERS_SQL}
                    """,
                    params,
                )
                total_row = cur.fetchone()
                total_count = int(total_row["total"]) if total_row else 0

                cur.execute(
                    f"""
                    SELECT
                      so.sales_opportunity_id,
                      so.source_kind,
                      so.source_opportunity_id,
                      so.account_id,
                      so.primary_contact_id,
                      so.organization_id,
                      so.primary_crm_contact_id,
                      so.title,
                      so.stage,
                      so.owner_key,
                      so.version,
                      so.created_by,
                      so.updated_by,
                      so.created_at,
                      so.updated_at,
                      COALESCE(se.stage_changed_at, so.created_at) AS stage_updated_at,
                      o.contact_display_email,
                      o.account_display_domain,
                      COALESCE(ct.open_task_count, 0) AS open_task_count,
                      nt.next_task_id,
                      nt.next_task_title,
                      nt.next_task_due_at
                    FROM api.v_commercial_sales_opportunity so
                    LEFT JOIN api.v_commercial_opportunity o
                      ON o.opportunity_id = so.source_opportunity_id
                    LEFT JOIN LATERAL (
                      SELECT e.created_at AS stage_changed_at
                      FROM api.v_commercial_sales_opportunity_event e
                      WHERE e.sales_opportunity_id = so.sales_opportunity_id
                        AND e.event_type = 'stage_changed'
                      ORDER BY e.created_at DESC
                      LIMIT 1
                    ) se ON true
                    LEFT JOIN LATERAL (
                      SELECT
                        t.task_id AS next_task_id,
                        t.title AS next_task_title,
                        t.due_at AS next_task_due_at
                      FROM api.v_commercial_task t
                      WHERE t.sales_opportunity_id = so.sales_opportunity_id
                        AND t.status = 'open'
                      ORDER BY t.due_at NULLS LAST, t.created_at DESC
                      LIMIT 1
                    ) nt ON true
                    LEFT JOIN LATERAL (
                      SELECT count(*)::int AS open_task_count
                      FROM api.v_commercial_task t2
                      WHERE t2.sales_opportunity_id = so.sales_opportunity_id
                        AND t2.status = 'open'
                    ) ct ON true
                    WHERE {_SALES_OPPORTUNITY_LIST_FILTERS_SQL}
                    ORDER BY so.updated_at DESC, so.sales_opportunity_id
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    params,
                )

                items = [SalesOpportunityBoardItem(**dict(row)) for row in cur.fetchall()]

                return items, total_count


@dataclass(frozen=True)
class CommercialWorkQueueTask:
    task: Task
    contact_display_email: str | None
    account_display_domain: str | None
    canonical_stage: str | None
    machine_review_status: str | None


@dataclass(frozen=True)
class CommercialWorkQueueOpportunity:
    opportunity_id: str
    contact_display_email: str | None
    account_display_domain: str | None
    canonical_stage: str
    machine_review_status: str
    confirmation_status: str | None
    manual_stage: str | None
    owner_key: str | None
    operator_state_version: int | None


def _work_queue_opportunity(
    row: dict[str, object],
) -> CommercialWorkQueueOpportunity:
    return CommercialWorkQueueOpportunity(
        opportunity_id=str(row["opportunity_id"]),
        contact_display_email=(
            None
            if row["contact_display_email"] is None
            else str(row["contact_display_email"])
        ),
        account_display_domain=(
            None
            if row["account_display_domain"] is None
            else str(row["account_display_domain"])
        ),
        canonical_stage=str(row["canonical_stage"]),
        machine_review_status=str(row["machine_review_status"]),
        confirmation_status=(
            None
            if row["confirmation_status"] is None
            else str(row["confirmation_status"])
        ),
        manual_stage=(
            None if row["manual_stage"] is None else str(row["manual_stage"])
        ),
        owner_key=(None if row["owner_key"] is None else str(row["owner_key"])),
        operator_state_version=(
            None
            if row["operator_state_version"] is None
            else int(row["operator_state_version"])
        ),
    )
