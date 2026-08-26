"""Add durable CRM sales-opportunity lifecycle state.

Revision ID: 20260826_0036
Revises: 20260825_0035
Create Date: 2026-08-26

CRM-1 established ``commercial.sales_opportunity`` as durable human-owned
commercial state.

CRM-2 adds the minimum mutation contract needed for explicit lifecycle
transitions:

* a controlled stage vocabulary;
* optimistic-concurrency versioning;
* durable update attribution/timestamps;
* append-only ``stage_changed`` audit events;
* least-privilege UPDATE access limited to lifecycle columns.

PR3 remains a replaceable machine projection. CRM-2 does not introduce any
foreign key back to ``commercial.opportunity``.

Quotes, lifecycle reasons, owner changes, reopening terminal opportunities,
tasks/activities re-anchoring, and UI workflow remain outside this migration.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260826_0036"
down_revision: Union[str, Sequence[str], None] = "20260825_0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CRM-1 deliberately allowed only the initial `new` stage. CRM-2 expands
    # the controlled vocabulary while retaining a database-level constraint.
    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          DROP CONSTRAINT sales_opportunity_stage_check
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ADD CONSTRAINT sales_opportunity_stage_check
          CHECK (
            stage IN (
              'new',
              'qualifying',
              'qualified',
              'quoting',
              'negotiating',
              'won',
              'lost',
              'dormant'
            )
          )
        """
    )

    # Existing CRM-1 rows have never been mutated. Their first lifecycle
    # version is therefore 1 and their last-update provenance is creation.
    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ADD COLUMN version INTEGER NOT NULL DEFAULT 1,
          ADD COLUMN updated_by TEXT,
          ADD COLUMN updated_at TIMESTAMPTZ
        """
    )

    op.execute(
        """
        UPDATE commercial.sales_opportunity
        SET
          updated_by = created_by,
          updated_at = created_at
        WHERE updated_by IS NULL
           OR updated_at IS NULL
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ALTER COLUMN updated_by SET NOT NULL,
          ALTER COLUMN updated_at SET NOT NULL
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ADD CONSTRAINT sales_opportunity_version_check
            CHECK (version >= 1),
          ADD CONSTRAINT sales_opportunity_updated_by_nonblank_check
            CHECK (length(trim(updated_by)) > 0),
          ADD CONSTRAINT sales_opportunity_updated_by_length_check
            CHECK (length(updated_by) <= 320)
        """
    )

    # CRM lifecycle history remains append-only. CRM-2 adds exactly one new
    # event type; no UPDATE/DELETE permission is granted to this table.
    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity_event
          DROP CONSTRAINT sales_opportunity_event_event_type_check
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity_event
          ADD CONSTRAINT sales_opportunity_event_event_type_check
          CHECK (
            event_type IN (
              'created',
              'stage_changed'
            )
          )
        """
    )

    # Preserve all CRM-1 view columns in their existing order and append the
    # new lifecycle metadata. CREATE OR REPLACE avoids breaking read grants or
    # dependent readers during the upgrade.
    op.execute(
        """
        CREATE OR REPLACE VIEW api.v_commercial_sales_opportunity AS
        SELECT
          sales_opportunity_id,
          source_kind,
          source_opportunity_id,
          account_id,
          primary_contact_id,
          title,
          stage,
          owner_key,
          created_by,
          created_at,
          version,
          updated_by,
          updated_at
        FROM commercial.sales_opportunity
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.sales_opportunity.version IS
          'Optimistic-concurrency version for durable CRM lifecycle mutation.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.sales_opportunity.updated_by IS
          'Trusted operator identity responsible for the latest durable CRM mutation.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.sales_opportunity.updated_at IS
          'Timestamp of the latest durable CRM mutation.'
        """
    )

    # Reassert read access for deployments where the roles already exist.
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
              api.v_commercial_sales_opportunity
            TO origenlab_api_ro;
          END IF;
        END $$
        """
    )

    # Least privilege: CRM-2 needs mutation of lifecycle state, not arbitrary
    # modification of identity, provenance, title, owner, or creation fields.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_sales_opportunity
            TO origenlab_api_rw;

            GRANT UPDATE (
              stage,
              version,
              updated_by,
              updated_at
            ) ON commercial.sales_opportunity
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    # A downgrade cannot faithfully represent lifecycle history once a real
    # transition has occurred. Fail closed instead of silently destroying
    # human commercial truth.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM commercial.sales_opportunity
            WHERE stage <> 'new'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-2: non-new sales opportunities exist';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM commercial.sales_opportunity_event
            WHERE event_type = 'stage_changed'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-2: stage_changed audit history exists';
          END IF;
        END $$
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            REVOKE UPDATE (
              stage,
              version,
              updated_by,
              updated_at
            ) ON commercial.sales_opportunity
            FROM origenlab_api_rw;
          END IF;
        END $$
        """
    )

    # PostgreSQL cannot remove appended columns through CREATE OR REPLACE VIEW,
    # so downgrade recreates the CRM-1 view before dropping lifecycle columns.
    op.execute("DROP VIEW IF EXISTS api.v_commercial_sales_opportunity")

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity_event
          DROP CONSTRAINT sales_opportunity_event_event_type_check
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity_event
          ADD CONSTRAINT sales_opportunity_event_event_type_check
          CHECK (
            event_type IN (
              'created'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          DROP CONSTRAINT sales_opportunity_stage_check,
          DROP CONSTRAINT sales_opportunity_version_check,
          DROP CONSTRAINT sales_opportunity_updated_by_nonblank_check,
          DROP CONSTRAINT sales_opportunity_updated_by_length_check
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          DROP COLUMN updated_at,
          DROP COLUMN updated_by,
          DROP COLUMN version
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ADD CONSTRAINT sales_opportunity_stage_check
          CHECK (
            stage IN (
              'new'
            )
          )
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_sales_opportunity AS
        SELECT
          sales_opportunity_id,
          source_kind,
          source_opportunity_id,
          account_id,
          primary_contact_id,
          title,
          stage,
          owner_key,
          created_by,
          created_at
        FROM commercial.sales_opportunity
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
              api.v_commercial_sales_opportunity
            TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_sales_opportunity
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )
