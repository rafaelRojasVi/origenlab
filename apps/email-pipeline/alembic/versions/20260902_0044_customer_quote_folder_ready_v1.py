"""Add folder_ready Drive-workspace provisioning status (CRM-Q2 follow-up).

Revision ID: 20260902_0044
Revises: 20260902_0043
Create Date: 2026-09-02

The master quotation spreadsheet template is not yet finalized, so template
copying is now gated behind an explicit, separately-activated setting
(``ORIGENLAB_DRIVE_QUOTE_TEMPLATE_PROVISIONING_ENABLED``, default false --
see ``origenlab_api.settings`` / ``origenlab_api.drive.factory``). While that
gate is off, a generated quote's Drive workspace can only ever reach
"folder created, template step never attempted" -- calling that state
``ready`` (CRM-Q1's "fully provisioned" meaning) would be a lie. This
migration adds a fourth, honest ``provisioning_status`` value,
``folder_ready``, and the append-only event type that records reaching it.

Purely additive: no table/column/grant/view change, no data touched, and --
like every migration in this quote-numbering chain -- the durable
``commercial.customer_quote_number_series`` row is never seeded or altered
here.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0044"
down_revision: Union[str, Sequence[str], None] = "20260902_0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. provisioning_status: add 'folder_ready' -- folder exists and is
    # usable, template/document step not attempted (gated off by default).
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_drive_workspace
          DROP CONSTRAINT customer_quote_drive_workspace_provisioning_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_drive_workspace
          ADD CONSTRAINT customer_quote_drive_workspace_provisioning_status_check
          CHECK (
            provisioning_status IN (
              'pending',
              'ready',
              'folder_ready',
              'failed'
            )
          )
        """
    )

    # ------------------------------------------------------------------
    # 2. event_type: add the event recording a folder-only completion,
    # distinct from 'drive_workspace_ready' (which means folder + sheet).
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
              'quote_send_confirmed'
            )
          )
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN commercial.customer_quote_drive_workspace.provisioning_status IS
          'pending: no completed attempt yet. folder_ready: Drive workspace folder exists and is usable; the optional template/document copy step was not attempted (template provisioning is an explicit, separately-gated activation -- see ORIGENLAB_DRIVE_QUOTE_TEMPLATE_PROVISIONING_ENABLED). ready: folder + copied template document both exist. failed: the most recent attempt failed (see failure_category); prior partial artifacts, if any, stay referenced.'
        """
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Fail closed: never silently reinterpret a folder_ready workspace or a
    # drive_workspace_folder_ready event under the pre-migration vocabulary.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM commercial.customer_quote_drive_workspace
            WHERE provisioning_status = 'folder_ready'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade: commercial.customer_quote_drive_workspace '
              'rows exist with provisioning_status = ''folder_ready'' '
              '(no representation in the pre-migration model)';
          END IF;

          IF EXISTS (
            SELECT 1 FROM commercial.customer_quote_event
            WHERE event_type = 'drive_workspace_folder_ready'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade: commercial.customer_quote_event rows exist '
              'with event_type = ''drive_workspace_folder_ready''';
          END IF;
        END $$
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
        ALTER TABLE commercial.customer_quote_drive_workspace
          DROP CONSTRAINT customer_quote_drive_workspace_provisioning_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_drive_workspace
          ADD CONSTRAINT customer_quote_drive_workspace_provisioning_status_check
          CHECK (
            provisioning_status IN (
              'pending',
              'ready',
              'failed'
            )
          )
        """
    )
