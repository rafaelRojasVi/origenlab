"""Grant writer access and read views for CRM-4A reconciliation.

Revision ID: 20260828_0039
Revises: 20260827_0038
Create Date: 2026-08-28

CRM-4A (20260827_0038) introduced the durable ``commercial.organization`` /
``commercial.contact`` (+``*_source``) schema deliberately with zero writer
grants. This migration adds exactly what a production reconciliation writer
needs and nothing else:

* ``SELECT, INSERT`` on the four CRM-4A tables for ``origenlab_api_rw`` --
  the reconciliation writer only ever inserts durable organization/contact
  rows during promotion; it never updates or deletes them in this revision.
* Two new read views, ``api.v_commercial_organization`` and
  ``api.v_commercial_contact``, following the existing ``api.v_commercial_*``
  naming convention, granted ``SELECT`` to both ``origenlab_api_ro`` and
  ``origenlab_api_rw`` -- the read role has no grant on raw ``commercial.*``
  tables (by design, see 20260519_0016), so the sales-opportunity read model
  can only join durable organization/contact display names through these
  views.

No table/column/constraint changes. No data changes. Safe to apply on top of
any existing CRM-4A installation.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260828_0039"
down_revision: Union[str, Sequence[str], None] = "20260827_0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Writer grants: the reconciliation writer inserts durable
    # organization/contact rows and reads them back to resolve existing
    # identity. No UPDATE/DELETE -- this revision never mutates or removes
    # a durable organization/contact row once created.
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
              commercial.organization,
              commercial.organization_source,
              commercial.contact,
              commercial.contact_source
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )

    # ------------------------------------------------------------------
    # 2. Read views: the read role has no grant on raw commercial.* tables
    # (only on api.* views), so a resolved organization/contact display
    # name can only reach the sales-opportunity read model through views.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE VIEW api.v_commercial_organization AS
        SELECT
          organization_id,
          display_name,
          primary_domain,
          version,
          created_by,
          updated_by,
          created_at,
          updated_at
        FROM commercial.organization
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_contact AS
        SELECT
          contact_id,
          organization_id,
          display_name,
          primary_email,
          role_title,
          version,
          created_by,
          updated_by,
          created_at,
          updated_at
        FROM commercial.contact
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
              api.v_commercial_organization,
              api.v_commercial_contact
            TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT SELECT ON
              api.v_commercial_organization,
              api.v_commercial_contact
            TO origenlab_api_rw;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    # No data loss: dropping read views and revoking grants never destroys
    # durable rows, so this downgrade does not need to fail closed.
    op.execute("DROP VIEW IF EXISTS api.v_commercial_contact")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_organization")

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_roles
            WHERE rolname = 'origenlab_api_rw'
          ) THEN
            REVOKE SELECT, INSERT ON
              commercial.organization,
              commercial.organization_source,
              commercial.contact,
              commercial.contact_source
            FROM origenlab_api_rw;
          END IF;
        END $$
        """
    )
