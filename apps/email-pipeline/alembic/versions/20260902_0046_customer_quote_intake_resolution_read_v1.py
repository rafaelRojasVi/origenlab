"""Narrow read access to lead_intel.prospect for the intake resolver (CRM-Q2B).

Revision ID: 20260902_0046
Revises: 20260902_0045
Create Date: 2026-09-02

The intake resolver (apps/api) proposes organization/contact/email evidence
for "Incorporar al CRM" from Gmail interaction history already mirrored into
lead_intel.prospect. The API read role has no grant on the lead_intel schema
at all today. Rather than a broad cross-schema grant, this adds one narrow
view exposing only the fields a resolver may safely show an operator as
evidence -- never evidence_note/risk_flags/block_or_review_reason/
spanish_message_angle, which are internal freeform prospecting fields with
no place in a durable-adjacent evidence payload.

Purely additive: one new view, two guarded GRANT statements. No existing
table/column touched.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0046"
down_revision: Union[str, Sequence[str], None] = "20260902_0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW api.v_lead_intel_prospect_evidence AS
        SELECT
          prospect_key,
          organization_name,
          contact_name,
          email,
          domain,
          gmail_sent_count,
          gmail_received_count,
          gmail_last_contacted_at
        FROM lead_intel.prospect
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'origenlab_api_ro'
          ) THEN
            GRANT USAGE ON SCHEMA lead_intel TO origenlab_api_ro;
            GRANT SELECT ON api.v_lead_intel_prospect_evidence
              TO origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'origenlab_api_rw'
          ) THEN
            GRANT USAGE ON SCHEMA lead_intel TO origenlab_api_rw;
            GRANT SELECT ON api.v_lead_intel_prospect_evidence
              TO origenlab_api_rw;
          END IF;
        END $$
        """
    )

    op.execute(
        """
        COMMENT ON VIEW api.v_lead_intel_prospect_evidence IS
          'Narrow read-only evidence surface for the Cotizaciones intake resolver (CRM-Q2B). Deliberately excludes evidence_note/risk_flags/block_or_review_reason/spanish_message_angle.'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'origenlab_api_ro'
          ) THEN
            REVOKE SELECT ON api.v_lead_intel_prospect_evidence
              FROM origenlab_api_ro;
            REVOKE USAGE ON SCHEMA lead_intel FROM origenlab_api_ro;
          END IF;

          IF EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = 'origenlab_api_rw'
          ) THEN
            REVOKE SELECT ON api.v_lead_intel_prospect_evidence
              FROM origenlab_api_rw;
            REVOKE USAGE ON SCHEMA lead_intel FROM origenlab_api_rw;
          END IF;
        END $$
        """
    )
    op.execute("DROP VIEW IF EXISTS api.v_lead_intel_prospect_evidence")
