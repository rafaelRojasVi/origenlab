"""Introduce the durable customer-quote aggregate (CRM-Q1).

Revision ID: 20260830_0040
Revises: 20260828_0039
Create Date: 2026-08-30

CRM-Q1 makes the dashboard the front door for every new customer quotation.
This migration adds the durable schema only:

* ``commercial.customer_quote`` -- the durable quote record, owned by a
  durable ``commercial.sales_opportunity`` (customer quotes never create a
  competing customer/opportunity universe);
* ``commercial.customer_quote_revision`` -- revision 1 is created with the
  quote; sent revisions become immutable in a later slice (no sending or PDF
  generation exists yet);
* ``commercial.customer_quote_drive_workspace`` -- Google Drive provisioning
  state (``pending`` / ``ready`` / ``failed``), deliberately separate from the
  commercial lifecycle status. It stores only durable references and safe
  metadata (IDs, https web URLs, redacted failure category, retry counters).
  Never OAuth tokens, credentials, or spreadsheet contents;
* ``commercial.customer_quote_event`` -- append-only audit of quote/workspace
  transitions;
* ``commercial.customer_quote_number_series`` -- the transactional
  quote-number allocator. Allocation is a row-locked
  ``UPDATE ... SET next_serial = next_serial + 1 ... RETURNING`` in the same
  transaction as the quote INSERT -- never ``MAX(...) + 1``, browser-side
  numbering, timestamps, or random numbers.

The migration seeds NO numbering series row: the historical evidence base
(one real quotation filename, ``CN011728A``) is a single example and the
CRM-Q1 policy is to fail closed as ``quote_numbering_not_configured`` until
the canonical prefix/width/next-serial business decision is recorded via
explicit operator configuration.

The human ``quote_number`` and the internal ``quote_id`` remain separate
concepts: ``quote_id`` follows the CRM convention ``quote_<32 hex>`` (as with
other durable IDs, documented but not DB-enforced), while ``quote_number`` is
the unique human-facing business number.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260830_0040"
down_revision: Union[str, Sequence[str], None] = "20260828_0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. commercial.customer_quote -- durable quote record.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.customer_quote (
          quote_id TEXT PRIMARY KEY,

          sales_opportunity_id TEXT NOT NULL
            REFERENCES commercial.sales_opportunity(sales_opportunity_id)
            ON DELETE RESTRICT,

          quote_number TEXT NOT NULL,

          -- V1 commercial lifecycle is draft-only. Widening the lifecycle
          -- (sent/accepted/...) requires a new migration, never an in-place
          -- CHECK rewrite.
          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (
              status IN (
                'draft'
              )
            ),

          version INTEGER NOT NULL DEFAULT 1,

          created_by TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT uq_customer_quote_number
            UNIQUE (quote_number),

          CONSTRAINT customer_quote_id_nonblank_check
            CHECK (length(trim(quote_id)) > 0),
          CONSTRAINT customer_quote_id_maxlen_check
            CHECK (length(quote_id) <= 128),

          CONSTRAINT customer_quote_number_nonblank_check
            CHECK (length(trim(quote_number)) > 0),
          CONSTRAINT customer_quote_number_maxlen_check
            CHECK (length(quote_number) <= 32),

          CONSTRAINT customer_quote_version_positive_check
            CHECK (version >= 1),

          CONSTRAINT customer_quote_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT customer_quote_created_by_maxlen_check
            CHECK (length(created_by) <= 320),
          CONSTRAINT customer_quote_updated_by_nonblank_check
            CHECK (length(trim(updated_by)) > 0),
          CONSTRAINT customer_quote_updated_by_maxlen_check
            CHECK (length(updated_by) <= 320)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_customer_quote_sales_opportunity
          ON commercial.customer_quote (
            sales_opportunity_id
          )
        """
    )

    # ------------------------------------------------------------------
    # 2. commercial.customer_quote_revision -- revision 1 at creation.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.customer_quote_revision (
          quote_id TEXT NOT NULL
            REFERENCES commercial.customer_quote(quote_id)
            ON DELETE RESTRICT,

          revision_number INTEGER NOT NULL,

          -- Reference to the master template this revision was copied from
          -- (a Drive file ID today). Nullable: recorded when known, never
          -- fabricated.
          template_reference TEXT,

          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (
              status IN (
                'draft'
              )
            ),

          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT pk_customer_quote_revision
            PRIMARY KEY (quote_id, revision_number),

          CONSTRAINT customer_quote_revision_number_positive_check
            CHECK (revision_number >= 1),

          CONSTRAINT customer_quote_revision_template_reference_len_check
            CHECK (
              template_reference IS NULL
              OR (
                length(trim(template_reference)) > 0
                AND length(template_reference) <= 256
              )
            ),

          CONSTRAINT customer_quote_revision_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT customer_quote_revision_created_by_maxlen_check
            CHECK (length(created_by) <= 320)
        )
        """
    )

    # ------------------------------------------------------------------
    # 3. commercial.customer_quote_drive_workspace -- external provisioning
    # state, separate from the commercial lifecycle. Stores only durable
    # references and safe metadata; never tokens or spreadsheet data.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.customer_quote_drive_workspace (
          quote_id TEXT PRIMARY KEY
            REFERENCES commercial.customer_quote(quote_id)
            ON DELETE RESTRICT,

          provider TEXT NOT NULL DEFAULT 'google_drive'
            CHECK (
              provider IN (
                'google_drive'
              )
            ),

          provisioning_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (
              provisioning_status IN (
                'pending',
                'ready',
                'failed'
              )
            ),

          folder_id TEXT,
          folder_web_url TEXT,
          sheet_file_id TEXT,
          sheet_web_url TEXT,

          -- Redacted failure category only (e.g. drive_unavailable);
          -- provider payloads/messages never reach durable state.
          failure_category TEXT,

          attempt_count INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 1,

          -- Server-owned active-attempt lease: set when an attempt begins,
          -- cleared on completion/failure. While in the future, no other
          -- caller may begin a new attempt against this workspace even if
          -- it presents the current version -- this is what actually
          -- prevents two concurrent Drive-provider calls for the same
          -- quote (the version check alone only prevented two callers from
          -- winning the *same* expected_version race, not a caller
          -- reusing the version an in-flight attempt just produced).
          lease_expires_at TIMESTAMPTZ,

          requested_at TIMESTAMPTZ,
          completed_at TIMESTAMPTZ,

          created_by TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT customer_quote_drive_folder_id_len_check
            CHECK (
              folder_id IS NULL
              OR (
                length(trim(folder_id)) > 0
                AND length(folder_id) <= 256
              )
            ),
          CONSTRAINT customer_quote_drive_sheet_file_id_len_check
            CHECK (
              sheet_file_id IS NULL
              OR (
                length(trim(sheet_file_id)) > 0
                AND length(sheet_file_id) <= 256
              )
            ),

          -- Only safe https URLs may ever be stored.
          CONSTRAINT customer_quote_drive_folder_url_https_check
            CHECK (
              folder_web_url IS NULL
              OR (
                folder_web_url LIKE 'https://%'
                AND length(folder_web_url) <= 2048
              )
            ),
          CONSTRAINT customer_quote_drive_sheet_url_https_check
            CHECK (
              sheet_web_url IS NULL
              OR (
                sheet_web_url LIKE 'https://%'
                AND length(sheet_web_url) <= 2048
              )
            ),

          CONSTRAINT customer_quote_drive_failure_category_len_check
            CHECK (
              failure_category IS NULL
              OR (
                length(trim(failure_category)) > 0
                AND length(failure_category) <= 64
              )
            ),

          CONSTRAINT customer_quote_drive_attempt_count_check
            CHECK (attempt_count >= 0),
          CONSTRAINT customer_quote_drive_version_positive_check
            CHECK (version >= 1),

          CONSTRAINT customer_quote_drive_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT customer_quote_drive_created_by_maxlen_check
            CHECK (length(created_by) <= 320),
          CONSTRAINT customer_quote_drive_updated_by_nonblank_check
            CHECK (length(trim(updated_by)) > 0),
          CONSTRAINT customer_quote_drive_updated_by_maxlen_check
            CHECK (length(updated_by) <= 320)
        )
        """
    )

    # The same Drive artifact may never be claimed by two quotes.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_customer_quote_drive_folder
          ON commercial.customer_quote_drive_workspace (
            folder_id
          )
          WHERE folder_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_customer_quote_drive_sheet
          ON commercial.customer_quote_drive_workspace (
            sheet_file_id
          )
          WHERE sheet_file_id IS NOT NULL
        """
    )

    # ------------------------------------------------------------------
    # 4. commercial.customer_quote_event -- append-only audit.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.customer_quote_event (
          event_id TEXT PRIMARY KEY,

          quote_id TEXT NOT NULL
            REFERENCES commercial.customer_quote(quote_id)
            ON DELETE RESTRICT,

          event_type TEXT NOT NULL
            CHECK (
              event_type IN (
                'quote_created',
                'drive_provision_requested',
                'drive_workspace_ready',
                'drive_provision_failed'
              )
            ),

          actor_key TEXT NOT NULL,
          payload JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT customer_quote_event_id_nonblank_check
            CHECK (length(trim(event_id)) > 0),
          CONSTRAINT customer_quote_event_actor_nonblank_check
            CHECK (length(trim(actor_key)) > 0),
          CONSTRAINT customer_quote_event_actor_maxlen_check
            CHECK (length(actor_key) <= 320)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_customer_quote_event_quote
          ON commercial.customer_quote_event (
            quote_id,
            created_at
          )
        """
    )

    # ------------------------------------------------------------------
    # 5. commercial.customer_quote_number_series -- transactional allocator.
    # Deliberately NOT seeded here: the sequence start is a business
    # decision recorded via explicit operator configuration (fail closed as
    # quote_numbering_not_configured until then).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.customer_quote_number_series (
          series_key TEXT PRIMARY KEY,

          prefix TEXT NOT NULL,
          pad_width INTEGER NOT NULL,
          next_serial BIGINT NOT NULL,

          created_by TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT customer_quote_series_key_nonblank_check
            CHECK (length(trim(series_key)) > 0),
          CONSTRAINT customer_quote_series_key_maxlen_check
            CHECK (length(series_key) <= 32),

          CONSTRAINT customer_quote_series_prefix_nonblank_check
            CHECK (length(trim(prefix)) > 0),
          CONSTRAINT customer_quote_series_prefix_maxlen_check
            CHECK (length(prefix) <= 8),

          CONSTRAINT customer_quote_series_pad_width_check
            CHECK (pad_width BETWEEN 1 AND 10),

          CONSTRAINT customer_quote_series_next_serial_check
            CHECK (next_serial >= 1),

          CONSTRAINT customer_quote_series_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT customer_quote_series_created_by_maxlen_check
            CHECK (length(created_by) <= 320),
          CONSTRAINT customer_quote_series_updated_by_nonblank_check
            CHECK (length(trim(updated_by)) > 0),
          CONSTRAINT customer_quote_series_updated_by_maxlen_check
            CHECK (length(updated_by) <= 320)
        )
        """
    )

    # ------------------------------------------------------------------
    # 5b. Widen the existing commercial.command_idempotency command_kind
    # allowlist (ARCH-3B8, 20260824_0033) to admit 'customer_quote_create'.
    # That table is shipped and never rewritten; this is the corrective
    # ALTER for the new command kind CRM-Q1 introduces.
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
              'customer_quote_create'
            )
          )
        """
    )

    # ------------------------------------------------------------------
    # 6. Read views (the read role has no grant on raw commercial.* tables).
    # ------------------------------------------------------------------
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
          updated_at
        FROM commercial.customer_quote
        """
    )

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

    op.execute(
        """
        CREATE VIEW api.v_commercial_customer_quote_drive_workspace AS
        SELECT
          quote_id,
          provider,
          provisioning_status,
          folder_id,
          folder_web_url,
          sheet_file_id,
          sheet_web_url,
          failure_category,
          attempt_count,
          version,
          lease_expires_at,
          requested_at,
          completed_at,
          created_by,
          updated_by,
          created_at,
          updated_at
        FROM commercial.customer_quote_drive_workspace
        """
    )

    # ------------------------------------------------------------------
    # Comments.
    # ------------------------------------------------------------------
    op.execute(
        """
        COMMENT ON TABLE commercial.customer_quote IS
          'Durable customer quote, owned by a durable sales opportunity. quote_id follows the quote_<32 hex> convention (not DB-enforced); quote_number is the unique human business number allocated transactionally from customer_quote_number_series.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE commercial.customer_quote_drive_workspace IS
          'Google Drive provisioning state for a quote workspace. Stores durable references and safe metadata only -- never OAuth tokens, credentials, or spreadsheet contents. Provisioning status is separate from the commercial quote lifecycle.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE commercial.customer_quote_number_series IS
          'Transactional quote-number allocator (row-locked UPDATE ... RETURNING; never MAX()+1). Not seeded by migrations: allocation fails closed as quote_numbering_not_configured until the numbering business decision is configured.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE commercial.customer_quote_event IS
          'Append-only audit of customer-quote and Drive-workspace transitions.'
        """
    )

    # ------------------------------------------------------------------
    # Permissions: least privilege. The command writer inserts quotes,
    # revisions, and events (no UPDATE/DELETE: quote rows are immutable in
    # V1 and events are append-only); it updates only the Drive-workspace
    # provisioning row and the number-series counter.
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
            GRANT SELECT, INSERT ON
              commercial.customer_quote,
              commercial.customer_quote_revision,
              commercial.customer_quote_event
            TO origenlab_api_rw;

            GRANT SELECT, INSERT, UPDATE ON
              commercial.customer_quote_drive_workspace,
              commercial.customer_quote_number_series
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
            GRANT SELECT ON
              api.v_commercial_customer_quote,
              api.v_commercial_customer_quote_revision,
              api.v_commercial_customer_quote_drive_workspace
            TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_customer_quote,
              api.v_commercial_customer_quote_revision,
              api.v_commercial_customer_quote_drive_workspace
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Fail closed: a downgrade must never silently destroy durable quotes,
    # revisions, workspace references, audit events, or an activated
    # numbering series.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM commercial.customer_quote) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q1: durable commercial.customer_quote rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.customer_quote_revision) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q1: durable commercial.customer_quote_revision rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.customer_quote_drive_workspace) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q1: durable commercial.customer_quote_drive_workspace rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.customer_quote_event) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q1: durable commercial.customer_quote_event rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.customer_quote_number_series) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q1: an activated commercial.customer_quote_number_series exists';
          END IF;
        END $$
        """
    )

    op.execute("DROP VIEW IF EXISTS api.v_commercial_customer_quote_drive_workspace")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_customer_quote_revision")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_customer_quote")

    # Restore the pre-CRM-Q1 command_kind allowlist. Safe: the guard above
    # already fails closed if any commercial.customer_quote row exists, and
    # every committed customer_quote_create idempotency claim shares a
    # transaction with its customer_quote row (both commit or both roll
    # back), so no orphaned 'customer_quote_create' claim can remain here.
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

    # FK-safe teardown: children before commercial.customer_quote.
    op.execute("DROP TABLE IF EXISTS commercial.customer_quote_event")
    op.execute("DROP TABLE IF EXISTS commercial.customer_quote_drive_workspace")
    op.execute("DROP TABLE IF EXISTS commercial.customer_quote_revision")
    op.execute("DROP TABLE IF EXISTS commercial.customer_quote_number_series")
    op.execute("DROP TABLE IF EXISTS commercial.customer_quote")
