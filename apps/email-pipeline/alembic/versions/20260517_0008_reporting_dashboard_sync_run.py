"""Dashboard Postgres mirror sync audit (reporting.dashboard_sync_run).

Revision ID: 20260517_0008
Revises: 20260516_0007
Create Date: 2026-05-17

Written by scripts/sync/sync_dashboard_postgres_mirror.py after successful loader runs.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260517_0008"
down_revision: Union[str, Sequence[str], None] = "20260516_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reporting.dashboard_sync_run (
          id BIGSERIAL PRIMARY KEY,
          started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          finished_at TIMESTAMPTZ,
          status TEXT NOT NULL,
          sqlite_path TEXT NOT NULL,
          postgres_url_redacted TEXT NOT NULL,
          canonical_contact_count INTEGER,
          canonical_organization_count INTEGER,
          canonical_opportunity_signal_count INTEGER,
          archive_contact_count INTEGER,
          archive_organization_count INTEGER,
          archive_opportunity_signal_count INTEGER,
          email_suppression_count INTEGER,
          domain_suppression_count INTEGER,
          outreach_state_count INTEGER,
          error_message TEXT,
          details_json JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_reporting_dashboard_sync_run_started
          ON reporting.dashboard_sync_run(started_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reporting.dashboard_sync_run")
