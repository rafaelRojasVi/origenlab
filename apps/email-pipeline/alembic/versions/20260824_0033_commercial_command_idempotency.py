"""Durable idempotency for commercial create commands (ARCH-3B8).

Revision ID: 20260824_0033
Revises: 20260824_0032
Create Date: 2026-08-24

The idempotency table belongs to durable human/operator state, not to the
replaceable PR3 commercial projection.

Keys are scoped to the trusted operator identity. The same operator/key pair
may be replayed only with the same command kind and normalized request
fingerprint.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260824_0033"
down_revision: Union[str, Sequence[str], None] = "20260824_0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE commercial.command_idempotency (
          operator_key TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,

          command_kind TEXT NOT NULL
            CHECK (
              command_kind IN (
                'activity_create',
                'task_create'
              )
            ),

          request_fingerprint TEXT NOT NULL,

          -- NULL only while the owning transaction is creating the durable
          -- result. The reservation and result write occur in one transaction.
          result_id TEXT,

          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          PRIMARY KEY (
            operator_key,
            idempotency_key
          ),

          CHECK (length(trim(operator_key)) > 0),
          CHECK (length(operator_key) <= 320),

          CHECK (length(trim(idempotency_key)) > 0),
          CHECK (length(idempotency_key) <= 200),

          CHECK (
            request_fingerprint ~ '^[0-9a-f]{64}$'
          ),

          CHECK (
            result_id IS NULL
            OR length(trim(result_id)) > 0
          )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_command_idempotency_result
          ON commercial.command_idempotency (
            command_kind,
            result_id
          )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE commercial.command_idempotency IS
          'Durable per-operator idempotency reservations for commercial create commands.'
        """
    )

    # Restricted writer only. This internal table is intentionally not
    # exposed through an api.* read view and is not granted to the read role.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT, INSERT, UPDATE ON
              commercial.command_idempotency
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS commercial.command_idempotency")
