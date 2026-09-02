"""Durable customer-quote revision workflow + Drive-folder adoption (CRM-Q2).

Revision ID: 20260902_0043
Revises: 20260901_0042
Create Date: 2026-09-02

CRM-Q1/CRM-Q1D shipped the durable quote aggregate as draft-only on both
``commercial.customer_quote.status`` and
``commercial.customer_quote_revision.status``, with no UPDATE grant on
either table (quote rows are immutable in V1). This migration widens only
what the Cotizaciones workflow board needs:

1. The commercial-lifecycle state machine lives on
   ``customer_quote_revision.status`` (draft / pending_approval /
   adjustments_requested / approved / sent / superseded) -- never on
   ``customer_quote.status``, which is left untouched (still draft-only;
   the aggregate-level status is not a second workflow axis).
2. A SINGLE optimistic-concurrency token: ``customer_quote.version``. Every
   workflow command (submit-for-review / request-adjustments / approve /
   confirm-send) checks and increments the aggregate's own version, not a
   second competing counter on the revision. ``customer_quote_revision``
   gains ``updated_by``/``updated_at`` (it is now mutable) but no
   ``version`` column of its own.
3. "Incorporar al CRM" (adopting a pre-existing Drive-only folder into a
   durable quote) needs a durable quote whose ``serial``/``issue_year``
   were never allocated by ``customer_quote_number_series`` -- forcing a
   value would fabricate one, and guessing one from the discovered
   document identifier (e.g. "CN01191") is exactly the kind of derivation
   CRM-Q1D already forbids for quote_number. ``quote_origin`` records which
   case a row is, and a sum-type CHECK ties it to ``serial``/``issue_year``
   nullability: a 'generated' quote always has both, an 'adopted' quote
   always has neither. ``document_number``/``quote_number`` stay mandatory
   and unique for every quote regardless of origin.
4. The event/command-kind allowlists gain one domain-specific entry per new
   command (never a generic "transition" catch-all), plus a new read-only
   event view for the drawer's history feed.

Permissions stay least-privilege and view-based for reads: the two new
UPDATE grants are added directly on the raw ``commercial.*`` tables the
writer already has INSERT/SELECT on (matching CRM-Q1's existing grant
shape for that role), not on the ``api.v_*`` read views -- those remain
SELECT-only for both roles, unchanged.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0043"
down_revision: Union[str, Sequence[str], None] = "20260901_0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. customer_quote_revision becomes mutable: widen its status CHECK
    # to the full revision-workflow vocabulary. 'superseded' is reserved
    # for a future multi-revision slice -- no command in this migration's
    # API surface transitions anything into it.
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
              'superseded'
            )
          )
        """
    )

    # ------------------------------------------------------------------
    # 2. customer_quote_revision audit columns. Existing rows (if any)
    # backfill from created_by/created_at: nothing has "updated" a revision
    # before this migration, so the creator and creation time are the
    # honest last-touched values.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          ADD COLUMN updated_by TEXT,
          ADD COLUMN updated_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        UPDATE commercial.customer_quote_revision
        SET updated_by = created_by,
            updated_at = created_at
        WHERE updated_by IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          ALTER COLUMN updated_by SET NOT NULL,
          ALTER COLUMN updated_at SET NOT NULL,
          ALTER COLUMN updated_at SET DEFAULT now()
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          ADD CONSTRAINT customer_quote_revision_updated_by_nonblank_check
            CHECK (length(trim(updated_by)) > 0),
          ADD CONSTRAINT customer_quote_revision_updated_by_maxlen_check
            CHECK (length(updated_by) <= 320)
        """
    )

    # ------------------------------------------------------------------
    # 3. Adoption identity: quote_origin + the sum-type CHECK tying it to
    # serial/issue_year nullability. The existing serial/issue_year CHECK
    # constraints (>= 1 / BETWEEN 2000 AND 2999) already pass on NULL, so
    # they need no change.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          ADD COLUMN quote_origin TEXT NOT NULL DEFAULT 'generated'
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          ALTER COLUMN serial DROP NOT NULL,
          ALTER COLUMN issue_year DROP NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          ADD CONSTRAINT customer_quote_origin_check
            CHECK (
              quote_origin IN ('generated', 'adopted')
            ),
          ADD CONSTRAINT customer_quote_origin_serial_shape_check
            CHECK (
              (
                quote_origin = 'generated'
                AND serial IS NOT NULL
                AND issue_year IS NOT NULL
              )
              OR (
                quote_origin = 'adopted'
                AND serial IS NULL
                AND issue_year IS NULL
              )
            )
        """
    )

    # ------------------------------------------------------------------
    # 4. Widen the append-only event allowlist and the command-idempotency
    # allowlist with one domain-specific entry per new command -- never a
    # generic "transition"/"command" catch-all.
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

    # ------------------------------------------------------------------
    # 5. Read views: append new trailing columns (Postgres only allows
    # CREATE OR REPLACE VIEW to add columns at the end), plus a new
    # SELECT-only event view for the drawer's history feed -- no such view
    # existed before this migration.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW api.v_commercial_customer_quote AS
        SELECT
          quote_id,
          sales_opportunity_id,
          quote_number,
          status,
          version,
          created_by,
          updated_by,
          created_at,
          updated_at,
          serial,
          issue_year,
          document_number,
          quote_origin
        FROM commercial.customer_quote
        """
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW api.v_commercial_customer_quote_revision AS
        SELECT
          quote_id,
          revision_number,
          template_reference,
          status,
          created_by,
          created_at,
          updated_by,
          updated_at
        FROM commercial.customer_quote_revision
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_customer_quote_event AS
        SELECT
          event_id,
          quote_id,
          event_type,
          actor_key,
          payload,
          created_at
        FROM commercial.customer_quote_event
        """
    )

    # ------------------------------------------------------------------
    # 6. Permissions: least privilege. The writer already has SELECT,
    # INSERT on customer_quote and customer_quote_revision (CRM-Q1); the
    # revision workflow needs UPDATE on both raw tables (not the read
    # views, which stay SELECT-only for both roles). customer_quote_event
    # stays append-only -- no UPDATE/DELETE grant is ever added.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT UPDATE ON
              commercial.customer_quote,
              commercial.customer_quote_revision
            TO origenlab_api_rw;
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
            WHERE rolname = 'origenlab_api_ro'
          ) THEN
            GRANT SELECT ON api.v_commercial_customer_quote_event
              TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON api.v_commercial_customer_quote_event
              TO origenlab_api_rw;
          END IF;
        END $$
        """
    )

    # ------------------------------------------------------------------
    # Comments.
    # ------------------------------------------------------------------
    op.execute(
        """
        COMMENT ON COLUMN commercial.customer_quote.quote_origin IS
          'generated: serial/issue_year/quote_number/document_number were all allocated by customer_quote_number_series. adopted: the quote records a pre-existing Drive folder discovered via CRM-Q2 "Incorporar al CRM" -- serial/issue_year are NULL (never allocated by the series; never guessed from the discovered document identifier), while quote_number and document_number remain mandatory, operator-confirmed, and unique.'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN commercial.customer_quote_revision.status IS
          'Revision-level commercial workflow: draft/adjustments_requested -> pending_approval (submit_for_review); pending_approval -> adjustments_requested (request_adjustments) or approved (approve); approved -> sent (confirm_send). superseded is reserved for a future multi-revision slice and unreachable by any command shipped in CRM-Q2. Concurrency is governed by customer_quote.version, not a separate revision version.'
        """
    )
    op.execute(
        """
        COMMENT ON VIEW api.v_commercial_customer_quote_event IS
          'Read-only append-only audit trail for the Cotizaciones drawer. commercial.customer_quote_event itself is INSERT/SELECT only for the writer role -- never UPDATE/DELETE.'
        """
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Fail closed: refuse to silently coerce adopted-quote identity or
    # in-progress workflow state back into the pre-CRM-Q2 model.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM commercial.customer_quote WHERE quote_origin = 'adopted'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q2: adopted customer_quote rows exist '
              '(quote_origin = ''adopted'' has no representation in the '
              'pre-CRM-Q2 model)';
          END IF;

          IF EXISTS (
            SELECT 1 FROM commercial.customer_quote_revision
            WHERE status <> 'draft'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q2: customer_quote_revision rows exist '
              'with a non-draft workflow status';
          END IF;

          IF EXISTS (
            SELECT 1 FROM commercial.customer_quote_event
            WHERE event_type NOT IN (
              'quote_created',
              'drive_provision_requested',
              'drive_workspace_ready',
              'drive_provision_failed'
            )
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q2: customer_quote_event rows exist '
              'with an event_type introduced by CRM-Q2';
          END IF;

          IF EXISTS (
            SELECT 1 FROM commercial.command_idempotency
            WHERE command_kind IN (
              'customer_quote_adopt_drive',
              'customer_quote_submit_for_review',
              'customer_quote_request_adjustments',
              'customer_quote_approve',
              'customer_quote_confirm_send'
            )
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q2: command_idempotency rows exist for '
              'a command_kind introduced by CRM-Q2';
          END IF;
        END $$
        """
    )

    op.execute("DROP VIEW IF EXISTS api.v_commercial_customer_quote_event")

    op.execute("DROP VIEW IF EXISTS api.v_commercial_customer_quote_revision")
    op.execute(
        """
        CREATE VIEW api.v_commercial_customer_quote_revision AS
        SELECT
          quote_id,
          revision_number,
          template_reference,
          status,
          created_by,
          created_at
        FROM commercial.customer_quote_revision
        """
    )

    op.execute("DROP VIEW IF EXISTS api.v_commercial_customer_quote")
    op.execute(
        """
        CREATE VIEW api.v_commercial_customer_quote AS
        SELECT
          quote_id,
          sales_opportunity_id,
          quote_number,
          status,
          version,
          created_by,
          updated_by,
          created_at,
          updated_at,
          serial,
          issue_year,
          document_number
        FROM commercial.customer_quote
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
              'drive_provision_failed'
            )
          )
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          DROP CONSTRAINT customer_quote_origin_serial_shape_check,
          DROP CONSTRAINT customer_quote_origin_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          ALTER COLUMN serial SET NOT NULL,
          ALTER COLUMN issue_year SET NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          DROP COLUMN quote_origin
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          DROP CONSTRAINT customer_quote_revision_updated_by_maxlen_check,
          DROP CONSTRAINT customer_quote_revision_updated_by_nonblank_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_revision
          DROP COLUMN updated_by,
          DROP COLUMN updated_at
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
              'draft'
            )
          )
        """
    )
