"""Commercial opportunity lifecycle read-model mirror (ARCH-2A).

Revision ID: 20260822_0031
Revises: 20260617_0030
Create Date: 2026-08-22

Mirrors the SQLite PR3 commercial opportunity graph into Postgres for
read-only API/dashboard consumption.

The SQLite PR3 model remains the source of truth. These tables are a
replaceable projection.

Identity IDs remain opaque PR2 stable IDs. contact_display_email and
account_display_domain are nullable display enrichments reconstructed
at mirror time from the existing mart using the canonical PR2 hash
functions; they are not identity truth.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260822_0031"
down_revision: Union[str, Sequence[str], None] = "20260617_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # commercial schema already exists in deployed environments.
    op.execute(
        """
        CREATE TABLE commercial.opportunity (
          opportunity_id TEXT PRIMARY KEY,

          record_kind TEXT NOT NULL,

          account_id TEXT,
          primary_contact_id TEXT,

          contact_display_email TEXT,
          account_display_domain TEXT,

          source_kind TEXT NOT NULL,
          source_key TEXT NOT NULL,

          deal_key TEXT,

          canonical_stage TEXT NOT NULL,
          source_stage TEXT NOT NULL,
          stage_reason_code TEXT NOT NULL,
          stage_confidence TEXT NOT NULL,
          stage_is_current BOOLEAN NOT NULL,
          stage_is_terminal BOOLEAN NOT NULL,

          stage_evidence_at TEXT,
          stage_evidence_id TEXT,

          first_activity_at TEXT,
          last_activity_at TEXT,

          identity_link_status TEXT NOT NULL,
          review_status TEXT NOT NULL,

          synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_record_kind
          ON commercial.opportunity (record_kind)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_deal_key
          ON commercial.opportunity (deal_key)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_stage
          ON commercial.opportunity (canonical_stage)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_contact
          ON commercial.opportunity (primary_contact_id)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_account
          ON commercial.opportunity (account_id)
        """
    )

    op.execute(
        """
        CREATE TABLE commercial.opportunity_event (
          event_id TEXT PRIMARY KEY,

          opportunity_id TEXT NOT NULL
            REFERENCES commercial.opportunity(opportunity_id)
            ON DELETE CASCADE,

          canonical_event_type TEXT NOT NULL,
          source_event_type TEXT NOT NULL,
          event_at TEXT,

          source_table TEXT NOT NULL,
          source_record_id TEXT NOT NULL,

          source_email_id BIGINT,
          source_attachment_id BIGINT,

          confidence TEXT NOT NULL,
          operator_confirmed BOOLEAN NOT NULL,

          detail_json JSONB,

          synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_event_opportunity
          ON commercial.opportunity_event (opportunity_id)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_event_at
          ON commercial.opportunity_event (event_at)
        """
    )

    op.execute(
        """
        CREATE TABLE commercial.opportunity_evidence (
          evidence_id TEXT PRIMARY KEY,

          opportunity_id TEXT NOT NULL
            REFERENCES commercial.opportunity(opportunity_id)
            ON DELETE CASCADE,

          subject_kind TEXT NOT NULL,

          source_table TEXT NOT NULL,
          source_record_id TEXT NOT NULL,

          evidence_type TEXT NOT NULL,
          evidence_at TEXT,

          confidence TEXT NOT NULL,
          reason_code TEXT NOT NULL,

          source_email_id BIGINT,
          source_attachment_id BIGINT,

          detail_json JSONB,

          synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_evidence_opportunity
          ON commercial.opportunity_evidence (opportunity_id)
        """
    )

    op.execute(
        """
        CREATE TABLE commercial.opportunity_conflict (
          conflict_id TEXT PRIMARY KEY,

          opportunity_id TEXT
            REFERENCES commercial.opportunity(opportunity_id)
            ON DELETE SET NULL,

          conflict_type TEXT NOT NULL,
          reason_code TEXT NOT NULL,

          subject_keys_json JSONB NOT NULL,
          evidence_pointers_json JSONB NOT NULL,

          review_status TEXT NOT NULL,

          detail_json JSONB,

          synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_conflict_opportunity
          ON commercial.opportunity_conflict (opportunity_id)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_commercial_opportunity_conflict_reason
          ON commercial.opportunity_conflict (reason_code)
        """
    )

    # Minimal pass-through read views. Curated list/detail surfaces belong to ARCH-2B.
    op.execute(
        """
        CREATE VIEW api.v_commercial_opportunity AS
        SELECT
          opportunity_id,
          record_kind,
          account_id,
          primary_contact_id,
          contact_display_email,
          account_display_domain,
          source_kind,
          source_key,
          deal_key,
          canonical_stage,
          source_stage,
          stage_reason_code,
          stage_confidence,
          stage_is_current,
          stage_is_terminal,
          stage_evidence_at,
          stage_evidence_id,
          first_activity_at,
          last_activity_at,
          identity_link_status,
          review_status,
          synced_at
        FROM commercial.opportunity
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_opportunity_event AS
        SELECT
          event_id,
          opportunity_id,
          canonical_event_type,
          source_event_type,
          event_at,
          source_table,
          source_record_id,
          source_email_id,
          source_attachment_id,
          confidence,
          operator_confirmed,
          detail_json,
          synced_at
        FROM commercial.opportunity_event
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_opportunity_evidence AS
        SELECT
          evidence_id,
          opportunity_id,
          subject_kind,
          source_table,
          source_record_id,
          evidence_type,
          evidence_at,
          confidence,
          reason_code,
          source_email_id,
          source_attachment_id,
          detail_json,
          synced_at
        FROM commercial.opportunity_evidence
        """
    )

    op.execute(
        """
        CREATE VIEW api.v_commercial_opportunity_conflict AS
        SELECT
          conflict_id,
          opportunity_id,
          conflict_type,
          reason_code,
          subject_keys_json,
          evidence_pointers_json,
          review_status,
          detail_json,
          synced_at
        FROM commercial.opportunity_conflict
        """
    )

    op.execute(
        """
        COMMENT ON TABLE commercial.opportunity IS
          'Replaceable Postgres projection of SQLite PR3 commercial opportunities.'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN commercial.opportunity.contact_display_email IS
          'Nullable display enrichment resolved from mart contact email using PR2 stable_contact_id; not canonical identity truth.'
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN commercial.opportunity.account_display_domain IS
          'Nullable display enrichment resolved from mart organization domain using PR2 stable_account_id_for_domain; not canonical identity truth.'
        """
    )

    op.execute(
        """
        COMMENT ON VIEW api.v_commercial_opportunity IS
          'Read-only pass-through projection of commercial.opportunity; curated lifecycle API belongs to ARCH-2B.'
        """
    )

    # 0016 establishes default API-schema privileges, but explicitly grant these
    # views as well so this migration is self-contained across environments.
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
              api.v_commercial_opportunity,
              api.v_commercial_opportunity_event,
              api.v_commercial_opportunity_evidence,
              api.v_commercial_opportunity_conflict
            TO origenlab_api_ro;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS api.v_commercial_opportunity_conflict")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_opportunity_evidence")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_opportunity_event")
    op.execute("DROP VIEW IF EXISTS api.v_commercial_opportunity")

    op.execute("DROP TABLE IF EXISTS commercial.opportunity_conflict")
    op.execute("DROP TABLE IF EXISTS commercial.opportunity_evidence")
    op.execute("DROP TABLE IF EXISTS commercial.opportunity_event")
    op.execute("DROP TABLE IF EXISTS commercial.opportunity")
