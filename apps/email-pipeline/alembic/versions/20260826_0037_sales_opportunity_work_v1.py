"""Anchor durable commercial work to CRM sales opportunities.

Revision ID: 20260826_0037
Revises: 20260826_0036
Create Date: 2026-08-26

CRM-1 introduced durable human-owned ``commercial.sales_opportunity`` rows.
CRM-2 introduced their lifecycle.

Activities and tasks predate that durable CRM boundary. They are themselves
durable, but their ``opportunity_id`` field is only a logical pointer to the
replaceable PR3 machine projection.

CRM-3A adds a durable sales-opportunity anchor while preserving the existing
PR3 logical provenance reference for backwards compatibility.

For the current source model every sales opportunity has ``source_kind='pr3'``.
Therefore CRM-linked activities/tasks retain the corresponding PR3
``opportunity_id`` as provenance in addition to ``sales_opportunity_id``.

This migration does not change work-queue semantics, quote modeling, owner
mutation, lifecycle reasons, or dashboard workflow.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260826_0037"
down_revision: Union[str, Sequence[str], None] = "20260826_0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Unlike PR3, commercial.sales_opportunity is durable human-owned state.
    # A physical FK is therefore desirable for work that belongs to a sales
    # pursuit.
    op.execute(
        """
        ALTER TABLE commercial.activity
          ADD COLUMN sales_opportunity_id TEXT,
          ADD CONSTRAINT activity_sales_opportunity_id_fkey
            FOREIGN KEY (sales_opportunity_id)
            REFERENCES commercial.sales_opportunity(sales_opportunity_id)
            ON DELETE RESTRICT,
          ADD CONSTRAINT activity_sales_opportunity_requires_source_check
            CHECK (
              sales_opportunity_id IS NULL
              OR opportunity_id IS NOT NULL
            )
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.task
          ADD COLUMN sales_opportunity_id TEXT,
          ADD CONSTRAINT task_sales_opportunity_id_fkey
            FOREIGN KEY (sales_opportunity_id)
            REFERENCES commercial.sales_opportunity(sales_opportunity_id)
            ON DELETE RESTRICT,
          ADD CONSTRAINT task_sales_opportunity_requires_source_check
            CHECK (
              sales_opportunity_id IS NULL
              OR opportunity_id IS NOT NULL
            )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_activity_sales_opportunity
          ON commercial.activity (
            sales_opportunity_id,
            occurred_at DESC
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_task_sales_opportunity
          ON commercial.task (
            sales_opportunity_id
          )
        """
    )

    # Existing work against PR3 is deterministically attached when that PR3
    # opportunity has already been explicitly promoted. The unique
    # (source_kind, source_opportunity_id) CRM constraint guarantees at most
    # one target sales opportunity.
    op.execute(
        """
        UPDATE commercial.activity AS a
        SET sales_opportunity_id = s.sales_opportunity_id
        FROM commercial.sales_opportunity AS s
        WHERE a.sales_opportunity_id IS NULL
          AND a.opportunity_id IS NOT NULL
          AND s.source_kind = 'pr3'
          AND s.source_opportunity_id = a.opportunity_id
        """
    )

    op.execute(
        """
        UPDATE commercial.task AS t
        SET sales_opportunity_id = s.sales_opportunity_id
        FROM commercial.sales_opportunity AS s
        WHERE t.sales_opportunity_id IS NULL
          AND t.opportunity_id IS NOT NULL
          AND s.source_kind = 'pr3'
          AND s.source_opportunity_id = t.opportunity_id
        """
    )

    # PostgreSQL CREATE OR REPLACE VIEW permits new columns only at the end.
    # Preserve the complete legacy view contract and append the CRM anchor.
    op.execute(
        """
        CREATE OR REPLACE VIEW api.v_commercial_activity AS
        SELECT
          activity_id,
          opportunity_id,
          account_id,
          contact_id,
          activity_type,
          occurred_at,
          summary,
          detail,
          created_by,
          created_at,
          sales_opportunity_id
        FROM commercial.activity
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW api.v_commercial_task AS
        SELECT
          task_id,
          opportunity_id,
          account_id,
          contact_id,
          title,
          status,
          priority,
          due_at,
          owner_key,
          version,
          created_by,
          updated_by,
          completed_at,
          created_at,
          updated_at,
          sales_opportunity_id
        FROM commercial.task
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.activity.sales_opportunity_id IS
          'Durable CRM sales-opportunity anchor; PR3 opportunity_id remains logical provenance.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.task.sales_opportunity_id IS
          'Durable CRM sales-opportunity anchor; PR3 opportunity_id remains logical provenance.'
        """
    )

    # Reassert read access for deployments where API roles already exist.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_ro'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_activity,
              api.v_commercial_task
            TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_activity,
              api.v_commercial_task
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    # The legacy representation can faithfully preserve CRM-linked work only
    # when its logical opportunity_id still matches the CRM source pointer.
    # Fail closed if manual/inconsistent data would lose information.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM commercial.activity AS a
            JOIN commercial.sales_opportunity AS s
              ON s.sales_opportunity_id = a.sales_opportunity_id
            WHERE a.sales_opportunity_id IS NOT NULL
              AND (
                s.source_kind <> 'pr3'
                OR s.source_opportunity_id IS DISTINCT FROM a.opportunity_id
              )
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-3A: activity CRM anchor is not reconstructible from PR3 provenance';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM commercial.task AS t
            JOIN commercial.sales_opportunity AS s
              ON s.sales_opportunity_id = t.sales_opportunity_id
            WHERE t.sales_opportunity_id IS NOT NULL
              AND (
                s.source_kind <> 'pr3'
                OR s.source_opportunity_id IS DISTINCT FROM t.opportunity_id
              )
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-3A: task CRM anchor is not reconstructible from PR3 provenance';
          END IF;
        END $$
        """
    )

    # CREATE OR REPLACE cannot remove appended view columns, so restore the
    # exact pre-CRM-3A view shapes before dropping table columns.
    op.execute("DROP VIEW IF EXISTS api.v_commercial_activity")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_task")

    op.execute(
        """
        ALTER TABLE commercial.activity
          DROP CONSTRAINT activity_sales_opportunity_requires_source_check,
          DROP CONSTRAINT activity_sales_opportunity_id_fkey,
          DROP COLUMN sales_opportunity_id
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.task
          DROP CONSTRAINT task_sales_opportunity_requires_source_check,
          DROP CONSTRAINT task_sales_opportunity_id_fkey,
          DROP COLUMN sales_opportunity_id
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_activity AS
        SELECT
          activity_id,
          opportunity_id,
          account_id,
          contact_id,
          activity_type,
          occurred_at,
          summary,
          detail,
          created_by,
          created_at
        FROM commercial.activity
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_task AS
        SELECT
          task_id,
          opportunity_id,
          account_id,
          contact_id,
          title,
          status,
          priority,
          due_at,
          owner_key,
          version,
          created_by,
          updated_by,
          completed_at,
          created_at,
          updated_at
        FROM commercial.task
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_ro'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_activity,
              api.v_commercial_task
            TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_activity,
              api.v_commercial_task
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )
