"""Durable commercial operator state, activities, and tasks (ARCH-3A).

Revision ID: 20260824_0032
Revises: 20260822_0031
Create Date: 2026-08-24

PR3 commercial opportunity tables are replaceable projections.

The tables introduced here contain durable human operational state and
therefore deliberately DO NOT use foreign keys to commercial.opportunity.
An opportunity_id is a logical cross-reference only.

This prevents a PR3 refresh/rebuild from deleting operator decisions,
activities, notes, or follow-up tasks.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260824_0032"
down_revision: Union[str, Sequence[str], None] = "20260822_0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE commercial.opportunity_operator_state (
          opportunity_id TEXT PRIMARY KEY,

          confirmation_status TEXT NOT NULL
            CHECK (
              confirmation_status IN (
                'confirmed',
                'rejected',
                'needs_review'
              )
            ),

          manual_stage TEXT,
          owner_key TEXT,

          version INTEGER NOT NULL DEFAULT 1
            CHECK (version >= 1),

          created_by TEXT NOT NULL,
          updated_by TEXT NOT NULL,

          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CHECK (length(trim(created_by)) > 0),
          CHECK (length(trim(updated_by)) > 0),
          CHECK (
            manual_stage IS NULL
            OR length(trim(manual_stage)) > 0
          ),
          CHECK (
            owner_key IS NULL
            OR length(trim(owner_key)) > 0
          )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_operator_state_status
          ON commercial.opportunity_operator_state (
            confirmation_status
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_operator_state_owner
          ON commercial.opportunity_operator_state (
            owner_key
          )
        """
    )

    op.execute(
        """
        CREATE TABLE commercial.activity (
          activity_id TEXT PRIMARY KEY,

          opportunity_id TEXT,
          account_id TEXT,
          contact_id TEXT,

          activity_type TEXT NOT NULL
            CHECK (
              activity_type IN (
                'call',
                'whatsapp',
                'meeting',
                'email',
                'note',
                'quote',
                'follow_up',
                'other'
              )
            ),

          occurred_at TIMESTAMPTZ NOT NULL,

          summary TEXT NOT NULL,
          detail TEXT,

          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CHECK (length(trim(summary)) > 0),
          CHECK (length(trim(created_by)) > 0),
          CHECK (
            opportunity_id IS NOT NULL
            OR account_id IS NOT NULL
            OR contact_id IS NOT NULL
          ),
          CHECK (
            opportunity_id IS NULL
            OR length(trim(opportunity_id)) > 0
          ),
          CHECK (
            account_id IS NULL
            OR length(trim(account_id)) > 0
          ),
          CHECK (
            contact_id IS NULL
            OR length(trim(contact_id)) > 0
          )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_activity_opportunity
          ON commercial.activity (
            opportunity_id,
            occurred_at DESC
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_activity_account
          ON commercial.activity (
            account_id,
            occurred_at DESC
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_activity_contact
          ON commercial.activity (
            contact_id,
            occurred_at DESC
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_activity_occurred_at
          ON commercial.activity (
            occurred_at DESC
          )
        """
    )

    op.execute(
        """
        CREATE TABLE commercial.task (
          task_id TEXT PRIMARY KEY,

          opportunity_id TEXT,
          account_id TEXT,
          contact_id TEXT,

          title TEXT NOT NULL,

          status TEXT NOT NULL DEFAULT 'open'
            CHECK (
              status IN (
                'open',
                'done',
                'cancelled'
              )
            ),

          priority TEXT NOT NULL DEFAULT 'normal'
            CHECK (
              priority IN (
                'low',
                'normal',
                'high',
                'urgent'
              )
            ),

          due_at TIMESTAMPTZ,
          owner_key TEXT,

          version INTEGER NOT NULL DEFAULT 1
            CHECK (version >= 1),

          created_by TEXT NOT NULL,
          updated_by TEXT NOT NULL,

          completed_at TIMESTAMPTZ,

          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CHECK (length(trim(title)) > 0),
          CHECK (length(trim(created_by)) > 0),
          CHECK (length(trim(updated_by)) > 0),
          CHECK (
            owner_key IS NULL
            OR length(trim(owner_key)) > 0
          ),
          CHECK (
            opportunity_id IS NOT NULL
            OR account_id IS NOT NULL
            OR contact_id IS NOT NULL
          ),
          CHECK (
            opportunity_id IS NULL
            OR length(trim(opportunity_id)) > 0
          ),
          CHECK (
            account_id IS NULL
            OR length(trim(account_id)) > 0
          ),
          CHECK (
            contact_id IS NULL
            OR length(trim(contact_id)) > 0
          ),
          CHECK (
            (
              status = 'done'
              AND completed_at IS NOT NULL
            )
            OR (
              status <> 'done'
              AND completed_at IS NULL
            )
          )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_task_status_due
          ON commercial.task (
            status,
            due_at
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_task_owner_status
          ON commercial.task (
            owner_key,
            status
          )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_task_opportunity
          ON commercial.task (
            opportunity_id
          )
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_opportunity_operator_state AS
        SELECT
          opportunity_id,
          confirmation_status,
          manual_stage,
          owner_key,
          version,
          created_by,
          updated_by,
          created_at,
          updated_at
        FROM commercial.opportunity_operator_state
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
        COMMENT ON TABLE commercial.opportunity_operator_state IS
          'Durable human state keyed to a PR3 opportunity ID. No FK by design because PR3 is replaceable.'
        """
    )

    op.execute(
        """
        COMMENT ON TABLE commercial.activity IS
          'Durable operator-recorded commercial activities and notes.'
        """
    )

    op.execute(
        """
        COMMENT ON TABLE commercial.task IS
          'Durable commercial follow-up tasks and operator work queue.'
        """
    )

    # Existing API read role may inspect the durable state through API views.
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
              api.v_commercial_opportunity_operator_state,
              api.v_commercial_activity,
              api.v_commercial_task
            TO origenlab_api_ro;
          END IF;
        END $$
        """
    )

    # Future ARCH-3B write role. Safe no-op until the role exists.
    #
    # This deliberately grants write access ONLY to the three durable
    # operations tables, never to the replaceable PR3 projection tables.
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

            GRANT SELECT, INSERT, UPDATE ON
              commercial.opportunity_operator_state,
              commercial.activity,
              commercial.task
            TO origenlab_api_rw;

            GRANT SELECT ON
              api.v_commercial_opportunity,
              api.v_commercial_opportunity_event,
              api.v_commercial_opportunity_evidence,
              api.v_commercial_opportunity_conflict,
              api.v_commercial_opportunity_operator_state,
              api.v_commercial_activity,
              api.v_commercial_task
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS api.v_commercial_task")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_activity")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_opportunity_operator_state")

    op.execute("DROP TABLE IF EXISTS commercial.task")
    op.execute("DROP TABLE IF EXISTS commercial.activity")
    op.execute("DROP TABLE IF EXISTS commercial.opportunity_operator_state")
