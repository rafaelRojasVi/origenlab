"""Introduce durable canonical CRM organization and contact schema.

Revision ID: 20260827_0038
Revises: 20260826_0037
Create Date: 2026-08-27

CRM-1..CRM-3A established durable human-owned ``commercial.sales_opportunity``
state and anchored durable work to it, while keeping PR2/PR3 identity as
rebuildable provenance snapshots (``account_id`` / ``primary_contact_id``).

CRM-4A adds the durable canonical CRM entities themselves:

* ``commercial.organization`` -- a durable CRM company record;
* ``commercial.contact`` -- a durable CRM person record;
* ``*_source`` side tables recording where a durable entity was first
  reconciled from (v1: ``pr2_account`` / ``pr2_contact`` only);
* nullable durable foreign keys ``organization_id`` and
  ``primary_crm_contact_id`` on ``commercial.sales_opportunity``.

CRM-4A is deliberately schema-only. It does NOT:

* create, seed, or reconcile any organization/contact rows;
* read PR2/PR3 machine identity or any reporting projection;
* populate the new sales-opportunity foreign keys for existing rows;
* change promotion behaviour, request schemas, or the idempotency
  fingerprint;
* add mutation routes or writer grants;
* rename or drop ``account_id`` / ``primary_contact_id``.

Durable ``organization_id`` values follow the CRM convention ``org_<32 hex>``,
but the database intentionally does not enforce that shape: durable CRM IDs
must not become dependent on PR2 deterministic account IDs.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260827_0038"
down_revision: Union[str, Sequence[str], None] = "20260826_0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. commercial.organization -- durable canonical company record.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.organization (
          organization_id TEXT PRIMARY KEY,

          display_name TEXT NOT NULL,

          primary_domain TEXT,

          -- Optimistic-concurrency version for future durable CRM mutation.
          version INTEGER NOT NULL DEFAULT 1,

          created_by TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT organization_id_nonblank_check
            CHECK (length(trim(organization_id)) > 0),
          CONSTRAINT organization_id_maxlen_check
            CHECK (length(organization_id) <= 128),

          CONSTRAINT organization_display_name_nonblank_check
            CHECK (length(trim(display_name)) > 0),
          CONSTRAINT organization_display_name_maxlen_check
            CHECK (length(display_name) <= 500),

          -- A domain is optional; if present it must be nonblank and bounded.
          CONSTRAINT organization_primary_domain_len_check
            CHECK (
              primary_domain IS NULL
              OR (
                length(trim(primary_domain)) > 0
                AND length(primary_domain) <= 253
              )
            ),

          CONSTRAINT organization_version_positive_check
            CHECK (version >= 1),

          CONSTRAINT organization_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT organization_created_by_maxlen_check
            CHECK (length(created_by) <= 320),
          CONSTRAINT organization_updated_by_nonblank_check
            CHECK (length(trim(updated_by)) > 0),
          CONSTRAINT organization_updated_by_maxlen_check
            CHECK (length(updated_by) <= 320)
        )
        """
    )

    # ------------------------------------------------------------------
    # 2. commercial.organization_source -- provenance of reconciliation.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.organization_source (
          organization_id TEXT NOT NULL
            REFERENCES commercial.organization(organization_id)
            ON DELETE CASCADE,

          source_kind TEXT NOT NULL
            CHECK (
              source_kind IN (
                'pr2_account'
              )
            ),

          source_id TEXT NOT NULL,

          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT uq_organization_source
            UNIQUE (source_kind, source_id),

          CONSTRAINT organization_source_source_id_nonblank_check
            CHECK (length(trim(source_id)) > 0),
          CONSTRAINT organization_source_source_id_maxlen_check
            CHECK (length(source_id) <= 128),
          CONSTRAINT organization_source_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT organization_source_created_by_maxlen_check
            CHECK (length(created_by) <= 320)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_organization_source_organization
          ON commercial.organization_source (
            organization_id
          )
        """
    )

    # ------------------------------------------------------------------
    # 3. commercial.contact -- durable canonical person record.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.contact (
          contact_id TEXT PRIMARY KEY,

          organization_id TEXT
            REFERENCES commercial.organization(organization_id)
            ON DELETE RESTRICT,

          display_name TEXT,
          primary_email TEXT,
          role_title TEXT,

          version INTEGER NOT NULL DEFAULT 1,

          created_by TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT uq_contact_id_organization
            UNIQUE (contact_id, organization_id),

          CONSTRAINT contact_id_nonblank_check
            CHECK (length(trim(contact_id)) > 0),
          CONSTRAINT contact_id_maxlen_check
            CHECK (length(contact_id) <= 128),

          CONSTRAINT contact_display_name_len_check
            CHECK (
              display_name IS NULL
              OR (
                length(trim(display_name)) > 0
                AND length(display_name) <= 500
              )
            ),

          CONSTRAINT contact_primary_email_len_check
            CHECK (
              primary_email IS NULL
              OR (
                length(trim(primary_email)) > 0
                AND length(primary_email) <= 320
              )
            ),

          CONSTRAINT contact_role_title_len_check
            CHECK (
              role_title IS NULL
              OR (
                length(trim(role_title)) > 0
                AND length(role_title) <= 200
              )
            ),

          CONSTRAINT contact_version_positive_check
            CHECK (version >= 1),

          CONSTRAINT contact_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT contact_created_by_maxlen_check
            CHECK (length(created_by) <= 320),
          CONSTRAINT contact_updated_by_nonblank_check
            CHECK (length(trim(updated_by)) > 0),
          CONSTRAINT contact_updated_by_maxlen_check
            CHECK (length(updated_by) <= 320)
        )
        """
    )

    # A person's email is NOT globally unique: the same address can legitimately
    # appear across durable contact records (shared inboxes, re-created people).
    op.execute(
        """
        CREATE INDEX idx_contact_organization
          ON commercial.contact (
            organization_id
          )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_contact_primary_email
          ON commercial.contact (
            primary_email
          )
        """
    )

    # ------------------------------------------------------------------
    # 4. commercial.contact_source -- provenance of reconciliation.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE commercial.contact_source (
          contact_id TEXT NOT NULL
            REFERENCES commercial.contact(contact_id)
            ON DELETE CASCADE,

          source_kind TEXT NOT NULL
            CHECK (
              source_kind IN (
                'pr2_contact'
              )
            ),

          source_id TEXT NOT NULL,

          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

          CONSTRAINT uq_contact_source
            UNIQUE (source_kind, source_id),

          CONSTRAINT contact_source_source_id_nonblank_check
            CHECK (length(trim(source_id)) > 0),
          CONSTRAINT contact_source_source_id_maxlen_check
            CHECK (length(source_id) <= 128),
          CONSTRAINT contact_source_created_by_nonblank_check
            CHECK (length(trim(created_by)) > 0),
          CONSTRAINT contact_source_created_by_maxlen_check
            CHECK (length(created_by) <= 320)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_contact_source_contact
          ON commercial.contact_source (
            contact_id
          )
        """
    )

    # ------------------------------------------------------------------
    # 5. Durable nullable CRM links on commercial.sales_opportunity.
    #
    # account_id / primary_contact_id are deliberately left untouched: they
    # stay as PR2/rebuildable identity provenance snapshots. The new columns
    # are the durable CRM references and are nullable with no default so that
    # existing rows are not implicitly linked.
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          ADD COLUMN organization_id TEXT,
          ADD COLUMN primary_crm_contact_id TEXT,
          ADD CONSTRAINT sales_opportunity_organization_id_fkey
            FOREIGN KEY (organization_id)
            REFERENCES commercial.organization(organization_id)
            ON DELETE RESTRICT,
          ADD CONSTRAINT sales_opportunity_primary_crm_contact_id_fkey
            FOREIGN KEY (primary_crm_contact_id)
            REFERENCES commercial.contact(contact_id)
            ON DELETE RESTRICT,
          ADD CONSTRAINT sales_opportunity_primary_contact_organization_fkey
            FOREIGN KEY (primary_crm_contact_id, organization_id)
            REFERENCES commercial.contact(contact_id, organization_id)
            ON DELETE RESTRICT
        """
    )

    op.execute(
        """
        CREATE INDEX idx_sales_opportunity_organization
          ON commercial.sales_opportunity (
            organization_id
          )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_sales_opportunity_primary_crm_contact
          ON commercial.sales_opportunity (
            primary_crm_contact_id
          )
        """
    )

    # ------------------------------------------------------------------
    # 6. Read model: preserve every existing column and its order, then
    # append the two durable CRM references. CREATE OR REPLACE keeps read
    # grants and dependent readers intact.
    # ------------------------------------------------------------------
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
          updated_at,
          organization_id,
          primary_crm_contact_id
        FROM commercial.sales_opportunity
        """
    )

    # ------------------------------------------------------------------
    # Comments.
    # ------------------------------------------------------------------
    op.execute(
        """
        COMMENT ON TABLE commercial.organization IS
          'Durable canonical CRM company record. IDs follow the org_<32 hex> convention but the DB does not depend on PR2 deterministic account IDs.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE commercial.contact IS
          'Durable canonical CRM person record. primary_email is intentionally not globally unique.'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE commercial.organization_source IS
          'Provenance of the source record a durable organization was first reconciled from (v1: pr2_account only).'
        """
    )
    op.execute(
        """
        COMMENT ON TABLE commercial.contact_source IS
          'Provenance of the source record a durable contact was first reconciled from (v1: pr2_contact only).'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN commercial.sales_opportunity.organization_id IS
          'Durable CRM organization link; nullable. account_id remains a rebuildable PR2 identity provenance snapshot.'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN commercial.sales_opportunity.primary_crm_contact_id IS
          'Durable CRM contact link; nullable. primary_contact_id remains a rebuildable PR2 identity provenance snapshot.'
        """
    )

    # ------------------------------------------------------------------
    # Permissions: least privilege. CRM-4A introduces no writers, so the
    # new durable tables receive no INSERT/UPDATE/DELETE grants. Only the
    # replaced sales-opportunity read view is (re)asserted for readers.
    # ------------------------------------------------------------------
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


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Fail closed: a downgrade must never silently destroy durable CRM
    # entities or durable CRM links that only exist at this revision.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM commercial.organization) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-4A: durable commercial.organization rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.organization_source) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-4A: durable commercial.organization_source rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.contact) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-4A: durable commercial.contact rows exist';
          END IF;

          IF EXISTS (SELECT 1 FROM commercial.contact_source) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-4A: durable commercial.contact_source rows exist';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM commercial.sales_opportunity
            WHERE organization_id IS NOT NULL
               OR primary_crm_contact_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade CRM-4A: sales opportunities carry durable CRM links';
          END IF;
        END $$
        """
    )

    # CREATE OR REPLACE cannot remove appended view columns, so restore the
    # exact pre-CRM-4A (revision 0037) view shape before dropping columns.
    op.execute("DROP VIEW IF EXISTS api.v_commercial_sales_opportunity")
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
          created_at,
          version,
          updated_by,
          updated_at
        FROM commercial.sales_opportunity
        """
    )

    op.execute(
        """
        ALTER TABLE commercial.sales_opportunity
          DROP CONSTRAINT sales_opportunity_primary_contact_organization_fkey,
          DROP CONSTRAINT sales_opportunity_primary_crm_contact_id_fkey,
          DROP CONSTRAINT sales_opportunity_organization_id_fkey,
          DROP COLUMN primary_crm_contact_id,
          DROP COLUMN organization_id
        """
    )

    # FK-safe teardown: source side tables first, then contact (which
    # references organization), then organization.
    op.execute("DROP TABLE IF EXISTS commercial.contact_source")
    op.execute("DROP TABLE IF EXISTS commercial.organization_source")
    op.execute("DROP TABLE IF EXISTS commercial.contact")
    op.execute("DROP TABLE IF EXISTS commercial.organization")

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
