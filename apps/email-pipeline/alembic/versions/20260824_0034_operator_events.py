"""Durable audit events for commercial operator state changes (ARCH-3C).

Revision ID: 20260824_0034
Revises: 20260824_0033
Create Date: 2026-08-24

PR3 commercial opportunities are replaceable projections.

Operator events are durable human audit history and therefore deliberately
DO NOT use a foreign key to commercial.opportunity. opportunity_id is a
logical cross-reference only.

The restricted API writer may append and inspect audit events, but may not
update or delete them.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260824_0034"
down_revision: Union[str, Sequence[str], None] = "20260824_0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE commercial.opportunity_operator_event (
          event_id TEXT PRIMARY KEY,

          opportunity_id TEXT NOT NULL,

          event_type TEXT NOT NULL
            CHECK (
              event_type IN (
                'operator_state_changed'
              )
            ),

          actor_key TEXT NOT NULL,

          payload JSONB NOT NULL,

          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CHECK (length(trim(event_id)) > 0),
          CHECK (length(event_id) <= 128),

          CHECK (length(trim(opportunity_id)) > 0),
          CHECK (length(opportunity_id) <= 128),

          CHECK (length(trim(actor_key)) > 0),
          CHECK (length(actor_key) <= 320)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_operator_event_opportunity_created
          ON commercial.opportunity_operator_event (
            opportunity_id,
            created_at DESC,
            event_id DESC
          )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE commercial.opportunity_operator_event IS
          'Append-only durable audit history for human commercial opportunity state changes.'
        """
    )

    # Restricted writer only.
    #
    # Audit events are append-only: the writer receives SELECT + INSERT,
    # never UPDATE or DELETE.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT USAGE ON SCHEMA commercial
              TO origenlab_api_rw;

            GRANT SELECT, INSERT ON
              commercial.opportunity_operator_event
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS commercial.opportunity_operator_event")
