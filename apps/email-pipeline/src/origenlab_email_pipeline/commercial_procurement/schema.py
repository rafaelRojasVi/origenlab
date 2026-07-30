"""Temporary / in-memory SQLite DDL for proposed commercial_procurement_* schema.

Used only for plan validation harnesses. Never applied to production SQLite.
"""

from __future__ import annotations

import sqlite3

COMMERCIAL_PROCUREMENT_VALIDATION_SCHEMA_SQL = """
CREATE TABLE commercial_procurement_signal (
  procurement_id TEXT PRIMARY KEY,
  source_system TEXT NOT NULL,
  canonical_tender_key TEXT NOT NULL,
  tender_key_kind TEXT NOT NULL
    CHECK (tender_key_kind IN ('codigo_externo','codigo_licitacion','numero_adquisicion')),
  buyer_name_raw TEXT,
  buyer_name_norm TEXT,
  buyer_domain_norm TEXT,
  buyer_email_norm TEXT,
  region TEXT,
  title TEXT,
  status_code TEXT,
  status_name TEXT,
  publication_at TEXT,
  close_at TEXT,
  procurement_context TEXT NOT NULL,
  context_reason_code TEXT NOT NULL,
  confidence TEXT NOT NULL,
  line_item_count INTEGER NOT NULL CHECK (line_item_count >= 1),
  constituent_source_ids_json TEXT NOT NULL,
  constituent_lines_fp TEXT NOT NULL,
  first_seen_at TEXT,
  last_seen_at TEXT,
  review_status TEXT NOT NULL,
  UNIQUE (source_system, canonical_tender_key)
);

CREATE TABLE commercial_procurement_account_resolution (
  resolution_id TEXT PRIMARY KEY,
  procurement_id TEXT NOT NULL,
  resolution_status TEXT NOT NULL
    CHECK (resolution_status IN ('linked','unlinked','ambiguous','refused')),
  account_id TEXT,
  link_route TEXT NOT NULL,
  confidence TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  auto_link_allowed INTEGER NOT NULL CHECK (auto_link_allowed IN (0, 1)),
  review_status TEXT NOT NULL,
  candidate_account_ids_json TEXT NOT NULL,
  FOREIGN KEY (procurement_id) REFERENCES commercial_procurement_signal(procurement_id),
  UNIQUE (procurement_id),
  CHECK (
    (resolution_status = 'linked'
      AND account_id IS NOT NULL
      AND auto_link_allowed = 1
      AND link_route IN (
        'A_exact_institutional_domain',
        'B_exact_canonical_name',
        'C_exact_alias',
        'E_explicit_email_domain'
      ))
    OR
    (resolution_status IN ('unlinked','ambiguous','refused')
      AND account_id IS NULL
      AND auto_link_allowed = 0)
  )
);

CREATE TABLE commercial_procurement_evidence (
  evidence_id TEXT PRIMARY KEY,
  subject_kind TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  source_system TEXT,
  source_table TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  subject_key TEXT,
  evidence_type TEXT NOT NULL,
  evidence_at TEXT,
  reason_code TEXT NOT NULL,
  detail_json TEXT
);

CREATE TABLE commercial_procurement_conflict (
  conflict_id TEXT PRIMARY KEY,
  procurement_id TEXT,
  source_system TEXT,
  source_record_id TEXT,
  subject_kind TEXT NOT NULL,
  subject_key TEXT,
  account_id TEXT,
  reason_code TEXT NOT NULL,
  confidence TEXT NOT NULL,
  detail_json TEXT,
  created_at TEXT NOT NULL,
  CHECK (
    procurement_id IS NOT NULL
    OR (source_system IS NOT NULL AND source_record_id IS NOT NULL)
  ),
  FOREIGN KEY (procurement_id) REFERENCES commercial_procurement_signal(procurement_id)
);

CREATE TABLE commercial_procurement_enrichment_candidate (
  candidate_id TEXT PRIMARY KEY,
  procurement_id TEXT,
  source_system TEXT,
  source_record_id TEXT,
  buyer_name_raw TEXT,
  account_id TEXT,
  reason_code TEXT NOT NULL,
  confidence TEXT NOT NULL,
  recommended_research_field TEXT NOT NULL,
  priority INTEGER NOT NULL,
  operator_queue_eligible INTEGER NOT NULL CHECK (operator_queue_eligible IN (0, 1)),
  candidate_account_ids_json TEXT,
  FOREIGN KEY (procurement_id) REFERENCES commercial_procurement_signal(procurement_id)
);

CREATE TABLE commercial_procurement_build_meta (
  meta_key TEXT PRIMARY KEY,
  meta_value TEXT NOT NULL
);

CREATE INDEX idx_cps_tender_key ON commercial_procurement_signal(source_system, canonical_tender_key);
CREATE INDEX idx_cpar_status ON commercial_procurement_account_resolution(resolution_status);
CREATE INDEX idx_cpe_subject ON commercial_procurement_evidence(subject_kind, subject_id);
CREATE INDEX idx_cpc_reason ON commercial_procurement_conflict(reason_code);
CREATE INDEX idx_cpec_eligible ON commercial_procurement_enrichment_candidate(operator_queue_eligible);
"""

TABLE_INSERT_ORDER = (
    "commercial_procurement_signal",
    "commercial_procurement_account_resolution",
    "commercial_procurement_evidence",
    "commercial_procurement_conflict",
    "commercial_procurement_enrichment_candidate",
    "commercial_procurement_build_meta",
)


def create_validation_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(COMMERCIAL_PROCUREMENT_VALIDATION_SCHEMA_SQL)
    conn.execute("PRAGMA foreign_keys = ON")


__all__ = [
    "COMMERCIAL_PROCUREMENT_VALIDATION_SCHEMA_SQL",
    "TABLE_INSERT_ORDER",
    "create_validation_schema",
]
