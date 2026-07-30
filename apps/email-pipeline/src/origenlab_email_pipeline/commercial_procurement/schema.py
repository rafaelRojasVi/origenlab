"""Canonical SQLite DDL for commercial procurement read model (PR4).

One source of truth for production ensure, disposable validation, and tests.
Additive only. No physical FK to commercial_identity_*; PR2 is logical.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from origenlab_email_pipeline.commercial_procurement.constants import (
    PROCUREMENT_TABLE_INSERT_ORDER,
    REBUILDABLE_DATA_TABLES,
    SCHEMA_VERSION,
)

COMMERCIAL_PROCUREMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS commercial_procurement_signal (
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
  procurement_context TEXT NOT NULL
    CHECK (procurement_context IN (
      'none','tender_watch','tender_active','historical_tender','unknown'
    )),
  context_reason_code TEXT NOT NULL,
  confidence TEXT NOT NULL
    CHECK (confidence IN ('high','medium','low','none')),
  line_item_count INTEGER NOT NULL CHECK (line_item_count >= 1),
  constituent_source_ids_json TEXT NOT NULL,
  constituent_lines_fp TEXT NOT NULL,
  first_seen_at TEXT,
  last_seen_at TEXT,
  review_status TEXT NOT NULL,
  UNIQUE (source_system, canonical_tender_key)
);

CREATE TABLE IF NOT EXISTS commercial_procurement_account_resolution (
  resolution_id TEXT PRIMARY KEY,
  procurement_id TEXT NOT NULL,
  resolution_status TEXT NOT NULL
    CHECK (resolution_status IN ('linked','unlinked','ambiguous','refused')),
  account_id TEXT,
  link_route TEXT NOT NULL,
  confidence TEXT NOT NULL
    CHECK (confidence IN ('high','medium','low','none')),
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

CREATE TABLE IF NOT EXISTS commercial_procurement_evidence (
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

CREATE TABLE IF NOT EXISTS commercial_procurement_conflict (
  conflict_id TEXT PRIMARY KEY,
  procurement_id TEXT,
  source_system TEXT,
  source_record_id TEXT,
  subject_kind TEXT NOT NULL,
  subject_key TEXT,
  account_id TEXT,
  reason_code TEXT NOT NULL,
  confidence TEXT NOT NULL
    CHECK (confidence IN ('high','medium','low','none')),
  detail_json TEXT,
  created_at TEXT NOT NULL,
  CHECK (
    procurement_id IS NOT NULL
    OR (source_system IS NOT NULL AND source_record_id IS NOT NULL)
  ),
  FOREIGN KEY (procurement_id) REFERENCES commercial_procurement_signal(procurement_id)
);

CREATE TABLE IF NOT EXISTS commercial_procurement_enrichment_candidate (
  candidate_id TEXT PRIMARY KEY,
  procurement_id TEXT,
  source_system TEXT,
  source_record_id TEXT,
  buyer_name_raw TEXT,
  account_id TEXT,
  reason_code TEXT NOT NULL,
  confidence TEXT NOT NULL
    CHECK (confidence IN ('high','medium','low','none')),
  recommended_research_field TEXT NOT NULL,
  priority INTEGER NOT NULL,
  operator_queue_eligible INTEGER NOT NULL CHECK (operator_queue_eligible IN (0, 1)),
  candidate_account_ids_json TEXT,
  FOREIGN KEY (procurement_id) REFERENCES commercial_procurement_signal(procurement_id)
);

CREATE TABLE IF NOT EXISTS commercial_procurement_build_meta (
  meta_key TEXT PRIMARY KEY,
  meta_value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cps_tender_key
  ON commercial_procurement_signal(source_system, canonical_tender_key);
CREATE INDEX IF NOT EXISTS idx_cps_context
  ON commercial_procurement_signal(procurement_context);
CREATE INDEX IF NOT EXISTS idx_cps_buyer_norm
  ON commercial_procurement_signal(buyer_name_norm);
CREATE INDEX IF NOT EXISTS idx_cps_region
  ON commercial_procurement_signal(region);

CREATE INDEX IF NOT EXISTS idx_cpar_account
  ON commercial_procurement_account_resolution(account_id);
CREATE INDEX IF NOT EXISTS idx_cpar_status
  ON commercial_procurement_account_resolution(resolution_status);
CREATE INDEX IF NOT EXISTS idx_cpar_route
  ON commercial_procurement_account_resolution(link_route);

CREATE INDEX IF NOT EXISTS idx_cpe_subject
  ON commercial_procurement_evidence(subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS idx_cpe_source_pointer
  ON commercial_procurement_evidence(source_table, source_record_id);

CREATE INDEX IF NOT EXISTS idx_cpc_reason
  ON commercial_procurement_conflict(reason_code);
CREATE INDEX IF NOT EXISTS idx_cpc_procurement
  ON commercial_procurement_conflict(procurement_id);

CREATE INDEX IF NOT EXISTS idx_cpec_eligible
  ON commercial_procurement_enrichment_candidate(operator_queue_eligible);
CREATE INDEX IF NOT EXISTS idx_cpec_reason
  ON commercial_procurement_enrichment_candidate(reason_code);
CREATE INDEX IF NOT EXISTS idx_cpec_procurement
  ON commercial_procurement_enrichment_candidate(procurement_id);
"""

# Back-compat alias for validate_temp / older imports.
COMMERCIAL_PROCUREMENT_VALIDATION_SCHEMA_SQL = COMMERCIAL_PROCUREMENT_SCHEMA_SQL

TABLE_INSERT_ORDER = PROCUREMENT_TABLE_INSERT_ORDER

_REQUIRED_TABLES: tuple[str, ...] = (
    "commercial_procurement_signal",
    "commercial_procurement_account_resolution",
    "commercial_procurement_evidence",
    "commercial_procurement_conflict",
    "commercial_procurement_enrichment_candidate",
    "commercial_procurement_build_meta",
)

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "commercial_procurement_signal": frozenset(
        {
            "procurement_id",
            "source_system",
            "canonical_tender_key",
            "tender_key_kind",
            "buyer_name_raw",
            "buyer_name_norm",
            "buyer_domain_norm",
            "buyer_email_norm",
            "region",
            "title",
            "status_code",
            "status_name",
            "publication_at",
            "close_at",
            "procurement_context",
            "context_reason_code",
            "confidence",
            "line_item_count",
            "constituent_source_ids_json",
            "constituent_lines_fp",
            "first_seen_at",
            "last_seen_at",
            "review_status",
        }
    ),
    "commercial_procurement_account_resolution": frozenset(
        {
            "resolution_id",
            "procurement_id",
            "resolution_status",
            "account_id",
            "link_route",
            "confidence",
            "reason_code",
            "auto_link_allowed",
            "review_status",
            "candidate_account_ids_json",
        }
    ),
    "commercial_procurement_evidence": frozenset(
        {
            "evidence_id",
            "subject_kind",
            "subject_id",
            "source_system",
            "source_table",
            "source_record_id",
            "subject_key",
            "evidence_type",
            "evidence_at",
            "reason_code",
            "detail_json",
        }
    ),
    "commercial_procurement_conflict": frozenset(
        {
            "conflict_id",
            "procurement_id",
            "source_system",
            "source_record_id",
            "subject_kind",
            "subject_key",
            "account_id",
            "reason_code",
            "confidence",
            "detail_json",
            "created_at",
        }
    ),
    "commercial_procurement_enrichment_candidate": frozenset(
        {
            "candidate_id",
            "procurement_id",
            "source_system",
            "source_record_id",
            "buyer_name_raw",
            "account_id",
            "reason_code",
            "confidence",
            "recommended_research_field",
            "priority",
            "operator_queue_eligible",
            "candidate_account_ids_json",
        }
    ),
    "commercial_procurement_build_meta": frozenset({"meta_key", "meta_value"}),
}

_REQUIRED_PK: dict[str, str] = {
    "commercial_procurement_signal": "procurement_id",
    "commercial_procurement_account_resolution": "resolution_id",
    "commercial_procurement_evidence": "evidence_id",
    "commercial_procurement_conflict": "conflict_id",
    "commercial_procurement_enrichment_candidate": "candidate_id",
    "commercial_procurement_build_meta": "meta_key",
}


class SchemaIncompatibilityError(ValueError):
    """Existing commercial_procurement_* schema is incompatible; refuse migration."""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def _pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cols = [
        str(r[1])
        for r in conn.execute(f"PRAGMA table_info({table})")
        if int(r[5]) > 0
    ]
    return sorted(cols, key=lambda c: next(
        int(r[5]) for r in conn.execute(f"PRAGMA table_info({table})") if str(r[1]) == c
    ))


def _unique_index_covers(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> bool:
    for idx in conn.execute(f"PRAGMA index_list({table})"):
        # idx: seq, name, unique, origin, partial
        if int(idx[2]) != 1:
            continue
        idx_cols = tuple(
            str(r[2]) for r in conn.execute(f"PRAGMA index_info({idx[1]})")
        )
        if idx_cols == columns:
            return True
    # Also accept UNIQUE constraints surfaced as autoindexes
    return False


def procurement_tables_present(conn: sqlite3.Connection) -> bool:
    return any(_table_exists(conn, t) for t in _REQUIRED_TABLES)


def procurement_tables_complete(conn: sqlite3.Connection) -> bool:
    return all(_table_exists(conn, t) for t in _REQUIRED_TABLES)


def assert_schema_compatible(conn: sqlite3.Connection) -> dict[str, Any]:
    """Fail-closed compatibility gate.

    - no tables → first-run additive create permitted
    - all present + compatible → ok
    - partial or incompatible → refuse
    """
    present = [t for t in _REQUIRED_TABLES if _table_exists(conn, t)]
    if not present:
        return {"status": "absent", "tables": []}
    missing = [t for t in _REQUIRED_TABLES if t not in present]
    if missing:
        raise SchemaIncompatibilityError(
            "incompatible commercial_procurement schema: partial tables present "
            f"(missing={missing})"
        )
    for table, required in _REQUIRED_COLUMNS.items():
        cols = _column_names(conn, table)
        if not required.issubset(cols):
            raise SchemaIncompatibilityError(
                f"incompatible {table}: missing columns "
                f"{sorted(required - cols)}"
            )
        pk = _pk_columns(conn, table)
        expected_pk = _REQUIRED_PK[table]
        if pk != [expected_pk]:
            raise SchemaIncompatibilityError(
                f"incompatible {table}: primary key {pk!r} != {[expected_pk]!r}"
            )
    # Unique (source_system, canonical_tender_key)
    if not _unique_index_covers(
        conn, "commercial_procurement_signal", ("source_system", "canonical_tender_key")
    ):
        # SQLite may name UNIQUE as sqlite_autoindex_*; verify via index_list uniqueness
        # on those two columns in any order of autoindex covering both.
        ok = False
        for idx in conn.execute("PRAGMA index_list(commercial_procurement_signal)"):
            if int(idx[2]) != 1:
                continue
            idx_cols = {str(r[2]) for r in conn.execute(f"PRAGMA index_info({idx[1]})")}
            if idx_cols == {"source_system", "canonical_tender_key"}:
                ok = True
                break
        if not ok:
            raise SchemaIncompatibilityError(
                "incompatible commercial_procurement_signal: missing UNIQUE "
                "(source_system, canonical_tender_key)"
            )
    if not _unique_index_covers(
        conn, "commercial_procurement_account_resolution", ("procurement_id",)
    ):
        ok = False
        for idx in conn.execute(
            "PRAGMA index_list(commercial_procurement_account_resolution)"
        ):
            if int(idx[2]) != 1:
                continue
            idx_cols = [str(r[2]) for r in conn.execute(f"PRAGMA index_info({idx[1]})")]
            if idx_cols == ["procurement_id"]:
                ok = True
                break
        if not ok:
            raise SchemaIncompatibilityError(
                "incompatible commercial_procurement_account_resolution: "
                "missing UNIQUE(procurement_id)"
            )
    # Internal FKs
    fk_res = list(
        conn.execute("PRAGMA foreign_key_list(commercial_procurement_account_resolution)")
    )
    if not any(
        str(r[2]) == "commercial_procurement_signal" and str(r[3]) == "procurement_id"
        for r in fk_res
    ):
        raise SchemaIncompatibilityError(
            "incompatible account_resolution: missing FK to signal.procurement_id"
        )
    return {"status": "compatible", "tables": list(_REQUIRED_TABLES)}


def create_validation_schema(conn: sqlite3.Connection) -> None:
    """Apply canonical DDL to a disposable connection (temp validation / fixtures)."""
    conn.executescript(COMMERCIAL_PROCUREMENT_SCHEMA_SQL)
    conn.execute("PRAGMA foreign_keys = ON")


def ensure_commercial_procurement_tables(conn: sqlite3.Connection) -> dict[str, Any]:
    """Additive schema ensure outside the data-replacement transaction."""
    gate = assert_schema_compatible(conn)
    conn.executescript(COMMERCIAL_PROCUREMENT_SCHEMA_SQL)
    conn.execute(
        """
        INSERT INTO commercial_procurement_build_meta(meta_key, meta_value)
        VALUES ('schema_version', ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
        """,
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return {
        "schema_ensure": "created" if gate["status"] == "absent" else "verified",
        "compatibility": gate["status"],
        "schema_version": SCHEMA_VERSION,
    }


def clear_rebuildable_procurement_tables(conn: sqlite3.Connection) -> None:
    """DELETE rebuildable PR4 data (caller transaction). Children first."""
    for table in REBUILDABLE_DATA_TABLES:
        conn.execute(f"DELETE FROM {table}")


__all__ = [
    "COMMERCIAL_PROCUREMENT_SCHEMA_SQL",
    "COMMERCIAL_PROCUREMENT_VALIDATION_SCHEMA_SQL",
    "TABLE_INSERT_ORDER",
    "SchemaIncompatibilityError",
    "assert_schema_compatible",
    "clear_rebuildable_procurement_tables",
    "create_validation_schema",
    "ensure_commercial_procurement_tables",
    "procurement_tables_complete",
    "procurement_tables_present",
]
