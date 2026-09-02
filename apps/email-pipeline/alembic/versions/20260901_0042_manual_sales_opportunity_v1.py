"""Add manual (non-PR3) sales-opportunity provenance.

Revision ID: 20260901_0042
Revises: 20260831_0041
Create Date: 2026-09-01

CRM-1 through CRM-Q1D supported only PR3-promoted sales opportunities
(`source_kind = 'pr3'`, `source_opportunity_id NOT NULL` referencing the PR3
machine projection). Operators must be able to start commercial work
manually, without first working a PR3-sourced opportunity.

This migration widens exactly two existing CHECK constraints -- no column,
index, or view changes. `source_opportunity_id` stays NOT NULL: application
code (not this migration) sets it equal to the freshly generated
`sales_opportunity_id` for a manual row, which already satisfies both
NOT NULL and the existing `uq_sales_opportunity_source UNIQUE (source_kind,
source_opportunity_id)` constraint with no schema change. No new
organization/contact schema is needed either: `commercial.organization` and
`commercial.contact` already accept a plain INSERT with no required PR2
provenance row.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260901_0042"
down_revision: Union[str, Sequence[str], None] = "20260831_0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          DROP CONSTRAINT sales_opportunity_source_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ADD CONSTRAINT sales_opportunity_source_kind_check
          CHECK (
            source_kind IN (
              'pr3',
              'manual'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.command_idempotency
          DROP CONSTRAINT command_idempotency_command_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.command_idempotency
          ADD CONSTRAINT command_idempotency_command_kind_check
          CHECK (
            command_kind IN (
              'activity_create',
              'task_create',
              'sales_opportunity_promote',
              'customer_quote_create',
              'sales_opportunity_create_manual'
            )
          )
        """
    )


def downgrade() -> None:
    # Fail closed: a downgrade must never silently leave a `source_kind =
    # 'manual'` row that would violate the narrower constraint it is about
    # to restore.
    op.execute(
        """
        DO $$
        DECLARE
          manual_count INT;
        BEGIN
          SELECT count(*) INTO manual_count FROM commercial.sales_opportunity
            WHERE source_kind = 'manual';

          IF manual_count > 0 THEN
            RAISE EXCEPTION
              'Cannot downgrade 0042: % manual sales opportunit(y/ies) exist',
              manual_count;
          END IF;
        END $$
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.command_idempotency
          DROP CONSTRAINT command_idempotency_command_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.command_idempotency
          ADD CONSTRAINT command_idempotency_command_kind_check
          CHECK (
            command_kind IN (
              'activity_create',
              'task_create',
              'sales_opportunity_promote',
              'customer_quote_create'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          DROP CONSTRAINT sales_opportunity_source_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ADD CONSTRAINT sales_opportunity_source_kind_check
          CHECK (
            source_kind IN (
              'pr3'
            )
          )
        """
    )
