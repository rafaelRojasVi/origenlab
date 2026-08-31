"""Correct the durable customer-quote numbering model (CRM-Q1D).

Revision ID: 20260831_0041
Revises: 20260830_0040
Create Date: 2026-08-31

CRM-Q1 (20260830_0040) collapsed two distinct business identifiers into one
``quote_number`` field: it treated the configured series ``prefix`` (e.g.
"CN") as part of the human-facing quote number itself
(``quote_number = prefix + padded_serial``, e.g. "CN011729"). Real OrigenLab
customer quotations prove this is wrong. The correct model is:

* an allocated **serial** (e.g. 1183), never exposed on its own;
* the human customer-facing **quote_number**
  (``<padded serial>-<2-digit issue year>``, e.g. "01183-26"), unrelated to
  any prefix;
* the separate Drive **document_number**
  (``<document_prefix><padded serial>``, e.g. "CN01183").

This migration:

1. Fails closed if any row already exists in ``commercial.customer_quote``
   or ``commercial.customer_quote_number_series``. CRM-Q1 has not
   intentionally been activated in production yet, so there is no
   ambiguous historical ``CNxxxxxx`` format to migrate -- a row existing
   here means an operator must reconcile it by hand, never have this
   migration guess a conversion.
2. Renames the series' ``prefix`` column to ``document_prefix``: it was
   never honestly named -- it seeds only the Drive document number, never
   the human quote_number. ``pad_width``/``next_serial`` are already
   honestly named (both are shared by quote_number and document_number)
   and are left untouched.
3. Adds the structural columns the corrected model needs to
   ``commercial.customer_quote`` (``serial``, ``issue_year``,
   ``document_number``) so the business identifier never has to be
   reparsed from a string at read time. ``quote_number`` itself keeps its
   existing column/constraints -- only what the application writes into it
   changes.
4. Recreates ``api.v_commercial_customer_quote`` with the new columns.

Revision creation (business letters A / AI / AII) is explicitly deferred;
``revision_number = 1`` continues to mean the unsuffixed initial
quotation -- this migration does not touch
``commercial.customer_quote_revision``.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260831_0041"
down_revision: Union[str, Sequence[str], None] = "20260830_0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Fail-closed guard: refuse to reinterpret existing durable rows.
    # Must run before any structural change below.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM commercial.customer_quote) THEN
            RAISE EXCEPTION
              'Cannot upgrade CRM-Q1D: durable commercial.customer_quote '
              'rows already exist -- reconcile the corrected numbering '
              'model by hand instead of guessing a conversion';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.customer_quote_number_series) THEN
            RAISE EXCEPTION
              'Cannot upgrade CRM-Q1D: an activated '
              'commercial.customer_quote_number_series row already exists '
              '-- reconcile the corrected numbering model by hand instead '
              'of guessing a conversion';
          END IF;
        END $$
        """
    )

    # ------------------------------------------------------------------
    # 2. The series' prefix seeds only the Drive document number, never
    # the human quote_number -- rename it (and its constraints) honestly.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_number_series
          RENAME COLUMN prefix TO document_prefix
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_number_series
          RENAME CONSTRAINT customer_quote_series_prefix_nonblank_check
          TO customer_quote_series_document_prefix_nonblank_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_number_series
          RENAME CONSTRAINT customer_quote_series_prefix_maxlen_check
          TO customer_quote_series_document_prefix_maxlen_check
        """
    )

    # ------------------------------------------------------------------
    # 3. Structural columns the corrected model needs. The table is
    # guaranteed empty by the guard above, so NOT NULL with no DEFAULT is
    # safe (nothing to backfill).
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          ADD COLUMN serial BIGINT NOT NULL,
          ADD COLUMN issue_year SMALLINT NOT NULL,
          ADD COLUMN document_number TEXT NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          ADD CONSTRAINT customer_quote_serial_positive_check
            CHECK (serial >= 1),
          ADD CONSTRAINT customer_quote_issue_year_check
            CHECK (issue_year BETWEEN 2000 AND 2999),
          ADD CONSTRAINT customer_quote_document_number_nonblank_check
            CHECK (length(trim(document_number)) > 0),
          ADD CONSTRAINT customer_quote_document_number_maxlen_check
            CHECK (length(document_number) <= 32),
          ADD CONSTRAINT uq_customer_quote_serial
            UNIQUE (serial),
          ADD CONSTRAINT uq_customer_quote_document_number
            UNIQUE (document_number)
        """
    )

    # ------------------------------------------------------------------
    # 4. Read view: append the new columns strictly at the end -- Postgres
    # only allows CREATE OR REPLACE VIEW to add trailing output columns;
    # inserting one before an existing column renames that existing output
    # column and is rejected.
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
          document_number
        FROM commercial.customer_quote
        """
    )

    # ------------------------------------------------------------------
    # Comments.
    # ------------------------------------------------------------------
    op.execute(
        """
        COMMENT ON COLUMN commercial.customer_quote.quote_number IS
          'Human customer-facing business number: <padded serial>-<2-digit issue year>, e.g. "01183-26". Never includes the Drive document prefix.'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN commercial.customer_quote.document_number IS
          'Drive document stem: <document_prefix><padded serial>, e.g. "CN01183". Distinct from quote_number; never parsed from it.'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN commercial.customer_quote_number_series.document_prefix IS
          'Seeds only the Drive document_number on first allocation -- never part of the human quote_number.'
        """
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Fail closed: a downgrade must never silently discard the corrected
    # structural columns' data.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM commercial.customer_quote) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-Q1D: durable commercial.customer_quote '
              'rows exist (serial/issue_year/document_number data would be '
              'lost)';
          END IF;
        END $$
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
          updated_at
        FROM commercial.customer_quote
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
            GRANT SELECT ON api.v_commercial_customer_quote TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON api.v_commercial_customer_quote TO origenlab_api_rw;
          END IF;
        END $$
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.customer_quote
          DROP COLUMN serial,
          DROP COLUMN issue_year,
          DROP COLUMN document_number
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.customer_quote_number_series
          RENAME CONSTRAINT customer_quote_series_document_prefix_nonblank_check
          TO customer_quote_series_prefix_nonblank_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_number_series
          RENAME CONSTRAINT customer_quote_series_document_prefix_maxlen_check
          TO customer_quote_series_prefix_maxlen_check
        """
    )
    op.execute(
        """
        ALTER TABLE commercial.customer_quote_number_series
          RENAME COLUMN document_prefix TO prefix
        """
    )
