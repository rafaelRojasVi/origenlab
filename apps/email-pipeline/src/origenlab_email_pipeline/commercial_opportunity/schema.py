"""SQLite DDL for the rebuildable commercial opportunity stage read model (PR3).

Additive tables only. Does not modify commercial_deal*, emails, suppressions,
outreach state, classifications, commercial_action_bucket, or commercial_identity_*.
"""

from __future__ import annotations

import sqlite3

from origenlab_email_pipeline.commercial_opportunity.constants import (
    REBUILDABLE_DATA_TABLES,
    SCHEMA_VERSION,
)

COMMERCIAL_OPPORTUNITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commercial_opportunity (
  opportunity_id TEXT PRIMARY KEY,
  record_kind TEXT NOT NULL,
  account_id TEXT,
  primary_contact_id TEXT,
  source_kind TEXT NOT NULL,
  source_key TEXT NOT NULL,
  commercial_deal_id INTEGER,
  deal_key TEXT,
  canonical_stage TEXT NOT NULL,
  source_stage TEXT NOT NULL,
  stage_reason_code TEXT NOT NULL,
  stage_confidence TEXT NOT NULL,
  stage_is_current INTEGER NOT NULL CHECK (stage_is_current IN (0, 1)),
  stage_is_terminal INTEGER NOT NULL CHECK (stage_is_terminal IN (0, 1)),
  stage_evidence_at TEXT,
  stage_evidence_id TEXT,
  first_activity_at TEXT,
  last_activity_at TEXT,
  identity_link_status TEXT NOT NULL,
  review_status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_co_account ON commercial_opportunity(account_id);
CREATE INDEX IF NOT EXISTS idx_co_contact ON commercial_opportunity(primary_contact_id);
CREATE INDEX IF NOT EXISTS idx_co_stage ON commercial_opportunity(canonical_stage);
CREATE INDEX IF NOT EXISTS idx_co_deal_key ON commercial_opportunity(deal_key);
CREATE INDEX IF NOT EXISTS idx_co_record_kind ON commercial_opportunity(record_kind);

CREATE TABLE IF NOT EXISTS commercial_opportunity_event (
  event_id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL,
  canonical_event_type TEXT NOT NULL,
  source_event_type TEXT NOT NULL,
  event_at TEXT,
  source_table TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  source_email_id INTEGER,
  source_attachment_id INTEGER,
  confidence TEXT NOT NULL,
  operator_confirmed INTEGER NOT NULL CHECK (operator_confirmed IN (0, 1)),
  detail_json TEXT,
  FOREIGN KEY(opportunity_id) REFERENCES commercial_opportunity(opportunity_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_coe_opportunity ON commercial_opportunity_event(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_coe_event_at ON commercial_opportunity_event(event_at);

CREATE TABLE IF NOT EXISTS commercial_opportunity_evidence (
  evidence_id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  source_table TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  evidence_at TEXT,
  confidence TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  source_email_id INTEGER,
  source_attachment_id INTEGER,
  detail_json TEXT,
  FOREIGN KEY(opportunity_id) REFERENCES commercial_opportunity(opportunity_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cox_opportunity ON commercial_opportunity_evidence(opportunity_id);

CREATE TABLE IF NOT EXISTS commercial_opportunity_conflict (
  conflict_id TEXT PRIMARY KEY,
  opportunity_id TEXT,
  conflict_type TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  subject_keys_json TEXT NOT NULL,
  evidence_pointers_json TEXT NOT NULL,
  review_status TEXT NOT NULL,
  detail_json TEXT,
  FOREIGN KEY(opportunity_id) REFERENCES commercial_opportunity(opportunity_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_coc_opportunity ON commercial_opportunity_conflict(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_coc_reason ON commercial_opportunity_conflict(reason_code);

CREATE TABLE IF NOT EXISTS commercial_opportunity_build_meta (
  meta_key TEXT PRIMARY KEY,
  meta_value TEXT NOT NULL
);
"""


def ensure_commercial_opportunity_tables(conn: sqlite3.Connection) -> None:
    """Additive schema ensure. May auto-commit via executescript (contract B)."""
    conn.executescript(COMMERCIAL_OPPORTUNITY_SCHEMA_SQL)
    conn.execute(
        """
        INSERT INTO commercial_opportunity_build_meta(meta_key, meta_value)
        VALUES ('schema_version', ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
        """,
        (SCHEMA_VERSION,),
    )
    conn.commit()


def clear_rebuildable_opportunity_tables(conn: sqlite3.Connection) -> None:
    """DELETE rebuildable opportunity *data* (caller transaction). Order respects FKs."""
    for table in REBUILDABLE_DATA_TABLES:
        # build_meta cleared last conceptually; delete children first via list order
        if table == "commercial_opportunity_build_meta":
            conn.execute("DELETE FROM commercial_opportunity_build_meta")
        else:
            conn.execute(f"DELETE FROM {table}")
