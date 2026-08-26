"""Add durable human-owned CRM sales opportunities.

Revision ID: 20260825_0035
Revises: 20260824_0034
Create Date: 2026-08-25

PR3 ``commercial.opportunity`` remains a replaceable machine projection.

``commercial.sales_opportunity`` is different: it represents a commercial
pursuit that a human operator has explicitly chosen to work.

A sales opportunity may be promoted from a PR3 opportunity, but deliberately
does NOT use a foreign key to ``commercial.opportunity``. Rebuilding PR3 must
never delete human-owned CRM state.

CRM-1 v1 supports only explicit PR3 promotion and the initial ``new`` stage.
Later lifecycle stages, quotes, tender sources, and manual creation are
deliberately outside this migration.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260825_0035"
down_revision: Union[str, Sequence[str], None] = "20260824_0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extend the existing durable command-idempotency contract so promotion
    # gets the same per-operator replay protection as activity/task creation.
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
              'sales_opportunity_promote'
            )
          )
        """
    )

    op.execute(
        """
        CREATE TABLE commercial.sales_opportunity (
          sales_opportunity_id TEXT PRIMARY KEY,

          source_kind TEXT NOT NULL
            CHECK (
              source_kind IN (
                'pr3'
              )
            ),

          source_opportunity_id TEXT NOT NULL,

          -- Snapshots of the canonical identity references present on the
          -- machine projection at the moment a human promotes it.
          --
          -- These are logical references rather than physical FKs because
          -- their upstream identity/projection layers remain rebuildable.
          account_id TEXT,
          primary_contact_id TEXT,

          title TEXT NOT NULL,

          -- CRM-1 deliberately starts with exactly one allowed lifecycle
          -- state. Expanding this vocabulary is a later product decision.
          stage TEXT NOT NULL DEFAULT 'new'
            CHECK (
              stage IN (
                'new'
              )
            ),

          -- Human ownership is required for a human-owned sales pursuit.
          -- v1 uses the existing owner-key convention; a canonical operator
          -- directory does not exist yet.
          owner_key TEXT NOT NULL,

          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT uq_sales_opportunity_source
            UNIQUE (
              source_kind,
              source_opportunity_id
            ),

          CHECK (length(trim(sales_opportunity_id)) > 0),
          CHECK (length(sales_opportunity_id) <= 128),

          CHECK (length(trim(source_opportunity_id)) > 0),
          CHECK (length(source_opportunity_id) <= 128),

          CHECK (
            account_id IS NULL
            OR (
              length(trim(account_id)) > 0
              AND length(account_id) <= 128
            )
          ),

          CHECK (
            primary_contact_id IS NULL
            OR (
              length(trim(primary_contact_id)) > 0
              AND length(primary_contact_id) <= 128
            )
          ),

          CHECK (length(trim(title)) > 0),
          CHECK (length(title) <= 500),

          CHECK (length(trim(owner_key)) > 0),
          CHECK (length(owner_key) <= 320),

          CHECK (length(trim(created_by)) > 0),
          CHECK (length(created_by) <= 320)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_sales_opportunity_account
          ON commercial.sales_opportunity (
            account_id
          )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_sales_opportunity_contact
          ON commercial.sales_opportunity (
            primary_contact_id
          )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_sales_opportunity_owner
          ON commercial.sales_opportunity (
            owner_key
          )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_sales_opportunity_stage
          ON commercial.sales_opportunity (
            stage
          )
        """
    )

    # Durable CRM events are within the durable CRM boundary, so unlike the
    # PR3 logical source reference this FK is safe and desirable.
    op.execute(
        """
        CREATE TABLE commercial.sales_opportunity_event (
          event_id TEXT PRIMARY KEY,

          sales_opportunity_id TEXT NOT NULL
            REFERENCES commercial.sales_opportunity(sales_opportunity_id)
            ON DELETE RESTRICT,

          event_type TEXT NOT NULL
            CHECK (
              event_type IN (
                'created'
              )
            ),

          actor_key TEXT NOT NULL,

          payload JSONB NOT NULL,

          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CHECK (length(trim(event_id)) > 0),
          CHECK (length(event_id) <= 128),

          CHECK (length(trim(actor_key)) > 0),
          CHECK (length(actor_key) <= 320)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_sales_opportunity_event_opportunity_created
          ON commercial.sales_opportunity_event (
            sales_opportunity_id,
            created_at DESC,
            event_id DESC
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
        CREATE VIEW api.v_commercial_sales_opportunity_event AS
        SELECT
          event_id,
          sales_opportunity_id,
          event_type,
          actor_key,
          payload,
          created_at
        FROM commercial.sales_opportunity_event
        """
    )

    op.execute(
        """
        COMMENT ON TABLE commercial.sales_opportunity IS
          'Durable human-owned CRM sales pursuit. PR3 source references are logical only and may disappear or rebuild independently.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.sales_opportunity.source_opportunity_id IS
          'Logical provenance pointer to the PR3 machine opportunity that was explicitly promoted; deliberately no FK.'
        """
    )

    op.execute(
        """
        COMMENT ON TABLE commercial.sales_opportunity_event IS
          'Append-only durable audit history for CRM sales-opportunity lifecycle events.'
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
              api.v_commercial_sales_opportunity,
              api.v_commercial_sales_opportunity_event
            TO origenlab_api_ro;
          END IF;
        END $$
        """
    )

    # CRM-1 permits creation only. There is intentionally no UPDATE/DELETE
    # grant on sales_opportunity until lifecycle mutation semantics exist.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT USAGE ON SCHEMA commercial, api
              TO origenlab_api_rw;

            GRANT SELECT, INSERT ON
              commercial.sales_opportunity,
              commercial.sales_opportunity_event
            TO origenlab_api_rw;

            GRANT SELECT ON
              api.v_commercial_opportunity,
              api.v_commercial_sales_opportunity,
              api.v_commercial_sales_opportunity_event
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS api.v_commercial_sales_opportunity_event")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_sales_opportunity")

    op.execute("DROP TABLE IF EXISTS commercial.sales_opportunity_event")
    op.execute("DROP TABLE IF EXISTS commercial.sales_opportunity")

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
              'task_create'
            )
          )
        """
    )
