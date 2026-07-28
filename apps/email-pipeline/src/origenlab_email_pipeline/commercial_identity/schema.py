"""SQLite DDL for the rebuildable commercial identity read model (PR2).

Additive tables only. Does not modify emails, suppressions, outreach state,
classifications, or commercial_action_bucket.
"""

from __future__ import annotations

import sqlite3

from origenlab_email_pipeline.commercial_identity.constants import REBUILDABLE_TABLES, SCHEMA_VERSION

COMMERCIAL_IDENTITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commercial_identity_account (
  account_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  primary_domain TEXT,
  first_evidence_at TEXT,
  last_evidence_at TEXT,
  identity_confidence TEXT NOT NULL,
  identity_status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ci_account_normalized_name
  ON commercial_identity_account(normalized_name);
CREATE INDEX IF NOT EXISTS idx_ci_account_primary_domain
  ON commercial_identity_account(primary_domain);
CREATE INDEX IF NOT EXISTS idx_ci_account_status
  ON commercial_identity_account(identity_status);

CREATE TABLE IF NOT EXISTS commercial_identity_account_alias (
  account_id TEXT NOT NULL,
  alias_name TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 1,
  first_evidence_at TEXT,
  last_evidence_at TEXT,
  PRIMARY KEY (account_id, normalized_alias),
  FOREIGN KEY(account_id) REFERENCES commercial_identity_account(account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ci_alias_normalized
  ON commercial_identity_account_alias(normalized_alias);

CREATE TABLE IF NOT EXISTS commercial_identity_account_domain (
  account_id TEXT NOT NULL,
  domain_norm TEXT NOT NULL,
  is_institutional INTEGER NOT NULL,
  link_method TEXT NOT NULL,
  first_evidence_at TEXT,
  last_evidence_at TEXT,
  PRIMARY KEY (account_id, domain_norm),
  FOREIGN KEY(account_id) REFERENCES commercial_identity_account(account_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_ci_account_domain_norm
  ON commercial_identity_account_domain(domain_norm);

CREATE TABLE IF NOT EXISTS commercial_identity_contact (
  contact_id TEXT PRIMARY KEY,
  normalized_email TEXT NOT NULL UNIQUE,
  display_name TEXT,
  role TEXT,
  account_id TEXT,
  account_link_method TEXT,
  first_evidence_at TEXT,
  last_evidence_at TEXT,
  identity_confidence TEXT NOT NULL,
  identity_status TEXT NOT NULL,
  email_domain TEXT,
  FOREIGN KEY(account_id) REFERENCES commercial_identity_account(account_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ci_contact_account
  ON commercial_identity_contact(account_id);
CREATE INDEX IF NOT EXISTS idx_ci_contact_domain
  ON commercial_identity_contact(email_domain);
CREATE INDEX IF NOT EXISTS idx_ci_contact_status
  ON commercial_identity_contact(identity_status);

CREATE TABLE IF NOT EXISTS commercial_identity_evidence (
  evidence_id TEXT PRIMARY KEY,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  source_table TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  source_plane TEXT NOT NULL,
  origin_plane TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  evidence_at TEXT,
  matching_reason_code TEXT NOT NULL,
  confidence TEXT NOT NULL,
  detail_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_ci_evidence_subject
  ON commercial_identity_evidence(subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS idx_ci_evidence_source
  ON commercial_identity_evidence(source_table, source_record_id);
CREATE INDEX IF NOT EXISTS idx_ci_evidence_origin
  ON commercial_identity_evidence(origin_plane);

CREATE TABLE IF NOT EXISTS commercial_identity_conflict (
  conflict_id TEXT PRIMARY KEY,
  conflict_type TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  subject_kind TEXT NOT NULL,
  subject_keys_json TEXT NOT NULL,
  evidence_pointers_json TEXT NOT NULL,
  identity_status TEXT NOT NULL,
  detail_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_ci_conflict_type
  ON commercial_identity_conflict(conflict_type);
CREATE INDEX IF NOT EXISTS idx_ci_conflict_reason
  ON commercial_identity_conflict(reason_code);

CREATE TABLE IF NOT EXISTS commercial_identity_build_meta (
  meta_key TEXT PRIMARY KEY,
  meta_value TEXT NOT NULL
);
"""


def ensure_commercial_identity_tables(conn: sqlite3.Connection) -> None:
    """Create identity read-model tables if missing. Idempotent."""
    conn.executescript(COMMERCIAL_IDENTITY_SCHEMA_SQL)
    conn.execute(
        """
        INSERT INTO commercial_identity_build_meta(meta_key, meta_value)
        VALUES ('schema_version', ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
        """,
        (SCHEMA_VERSION,),
    )


def clear_rebuildable_identity_tables(conn: sqlite3.Connection) -> None:
    """Delete rebuildable identity rows (FK-safe order). Does not drop tables."""
    for table in REBUILDABLE_TABLES:
        if table == "commercial_identity_build_meta":
            continue
        conn.execute(f"DELETE FROM {table}")
