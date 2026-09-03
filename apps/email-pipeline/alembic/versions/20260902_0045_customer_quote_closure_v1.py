"""Explicit quote closure: Ganada/Nula outcomes (CRM-Q2B).

Revision ID: 20260902_0045
Revises: 20260902_0044
Create Date: 2026-09-02

Sent quotes need an explicit terminal outcome distinct from the revision
workflow's approval path. Rather than a second status column (which would
create two competing workflow axes on the same aggregate), this widens the
existing single workflow axis, commercial.customer_quote_revision.status,
with two new terminal values: 'closed_won' (the customer accepted) and
'closed_null' (the quote is no longer active -- void/cancelled, NOT
necessarily a lost sale). Both are only reachable from 'sent' via the new
customer_quote_close command. Whether the linked sales_opportunity also
moves to 'won' is a deliberately separate, operator-visible action in Ventas
(the existing generic stage-transition command already supports 'won') --
this migration adds no cross-aggregate coupling.

Purely additive: three CHECK constraints widened, one new domain-specific
event type, one new command_idempotency command_kind. No table/column/grant/
view change.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0045"
down_revision: Union[str, Sequence[str], None] = "20260902_0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. customer_quote_revision.status: add closed_won/closed_null.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          DROP CONSTRAINT customer_quote_revision_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          ADD CONSTRAINT customer_quote_revision_status_check
          CHECK (
            status IN (
              'draft',
              'pending_approval',
              'adjustments_requested',
              'approved',
              'sent',
              'superseded',
              'closed_won',
              'closed_null'
            )
          )
        """
    )

    # ------------------------------------------------------------------
    # 2. customer_quote_event: add quote_closed.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_event
          DROP CONSTRAINT customer_quote_event_event_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_event
          ADD CONSTRAINT customer_quote_event_event_type_check
          CHECK (
            event_type IN (
              'quote_created',
              'drive_provision_requested',
              'drive_workspace_ready',
              'drive_workspace_folder_ready',
              'drive_provision_failed',
              'quote_adopted_from_drive',
              'quote_submitted_for_review',
              'quote_adjustments_requested',
              'quote_approved',
              'quote_send_confirmed',
              'quote_closed'
            )
          )
        """
    )

    # ------------------------------------------------------------------
    # 3. command_idempotency: add customer_quote_close.
    # ------------------------------------------------------------------
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
              'sales_opportunity_create_manual',
              'customer_quote_adopt_drive',
              'customer_quote_submit_for_review',
              'customer_quote_request_adjustments',
              'customer_quote_approve',
              'customer_quote_confirm_send',
              'customer_quote_close'
            )
          )
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.customer_quote_revision.status IS
          'Revision-level commercial workflow: draft/adjustments_requested -> pending_approval (submit_for_review); pending_approval -> adjustments_requested (request_adjustments) or approved (approve); approved -> sent (confirm_send); sent -> closed_won or closed_null (close). superseded is reserved for a future multi-revision slice and unreachable by any shipped command. Concurrency is governed by customer_quote.version.'
        """
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Fail closed: never silently discard a closed revision, a quote_closed
    # audit event, or a recorded close command's idempotency claim.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM commercial.customer_quote_revision
            WHERE status IN ('closed_won', 'closed_null')
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q2B: customer_quote_revision rows exist '
              'with a closed status (closed_won/closed_null)';
          END IF;

          IF EXISTS (
            SELECT 1 FROM commercial.customer_quote_event
            WHERE event_type = 'quote_closed'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q2B: customer_quote_event rows exist '
              'with event_type = ''quote_closed''';
          END IF;

          IF EXISTS (
            SELECT 1 FROM commercial.command_idempotency
            WHERE command_kind = 'customer_quote_close'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q2B: command_idempotency rows exist for '
              'command_kind = ''customer_quote_close''';
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
              'customer_quote_create',
              'sales_opportunity_create_manual',
              'customer_quote_adopt_drive',
              'customer_quote_submit_for_review',
              'customer_quote_request_adjustments',
              'customer_quote_approve',
              'customer_quote_confirm_send'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.customer_quote_event
          DROP CONSTRAINT customer_quote_event_event_type_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_event
          ADD CONSTRAINT customer_quote_event_event_type_check
          CHECK (
            event_type IN (
              'quote_created',
              'drive_provision_requested',
              'drive_workspace_ready',
              'drive_workspace_folder_ready',
              'drive_provision_failed',
              'quote_adopted_from_drive',
              'quote_submitted_for_review',
              'quote_adjustments_requested',
              'quote_approved',
              'quote_send_confirmed'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          DROP CONSTRAINT customer_quote_revision_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          ADD CONSTRAINT customer_quote_revision_status_check
          CHECK (
            status IN (
              'draft',
              'pending_approval',
              'adjustments_requested',
              'approved',
              'sent',
              'superseded'
            )
          )
        """
    )
