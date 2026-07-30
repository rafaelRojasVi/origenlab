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

class SchemaIncompatibilityError(ValueError):
    """Existing commercial_procurement_* schema is incompatible; refuse migration."""


# Structural manifest: name -> (affinity, notnull, pk_position 0=not pk else 1-based)
# Affinity is normalized uppercase SQLite type affinity class.
ColumnSpec = tuple[str, int, int]


def _affinity(declared: str) -> str:
    t = (declared or "").strip().upper()
    if not t:
        return "BLOB"
    if "INT" in t:
        return "INTEGER"
    if any(x in t for x in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in t:
        return "BLOB"
    if any(x in t for x in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


SCHEMA_COLUMN_MANIFEST: dict[str, dict[str, ColumnSpec]] = {
    "commercial_procurement_signal": {
        "procurement_id": ("TEXT", 0, 1),
        "source_system": ("TEXT", 1, 0),
        "canonical_tender_key": ("TEXT", 1, 0),
        "tender_key_kind": ("TEXT", 1, 0),
        "buyer_name_raw": ("TEXT", 0, 0),
        "buyer_name_norm": ("TEXT", 0, 0),
        "buyer_domain_norm": ("TEXT", 0, 0),
        "buyer_email_norm": ("TEXT", 0, 0),
        "region": ("TEXT", 0, 0),
        "title": ("TEXT", 0, 0),
        "status_code": ("TEXT", 0, 0),
        "status_name": ("TEXT", 0, 0),
        "publication_at": ("TEXT", 0, 0),
        "close_at": ("TEXT", 0, 0),
        "procurement_context": ("TEXT", 1, 0),
        "context_reason_code": ("TEXT", 1, 0),
        "confidence": ("TEXT", 1, 0),
        "line_item_count": ("INTEGER", 1, 0),
        "constituent_source_ids_json": ("TEXT", 1, 0),
        "constituent_lines_fp": ("TEXT", 1, 0),
        "first_seen_at": ("TEXT", 0, 0),
        "last_seen_at": ("TEXT", 0, 0),
        "review_status": ("TEXT", 1, 0),
    },
    "commercial_procurement_account_resolution": {
        "resolution_id": ("TEXT", 0, 1),
        "procurement_id": ("TEXT", 1, 0),
        "resolution_status": ("TEXT", 1, 0),
        "account_id": ("TEXT", 0, 0),
        "link_route": ("TEXT", 1, 0),
        "confidence": ("TEXT", 1, 0),
        "reason_code": ("TEXT", 1, 0),
        "auto_link_allowed": ("INTEGER", 1, 0),
        "review_status": ("TEXT", 1, 0),
        "candidate_account_ids_json": ("TEXT", 1, 0),
    },
    "commercial_procurement_evidence": {
        "evidence_id": ("TEXT", 0, 1),
        "subject_kind": ("TEXT", 1, 0),
        "subject_id": ("TEXT", 1, 0),
        "source_system": ("TEXT", 0, 0),
        "source_table": ("TEXT", 1, 0),
        "source_record_id": ("TEXT", 1, 0),
        "subject_key": ("TEXT", 0, 0),
        "evidence_type": ("TEXT", 1, 0),
        "evidence_at": ("TEXT", 0, 0),
        "reason_code": ("TEXT", 1, 0),
        "detail_json": ("TEXT", 0, 0),
    },
    "commercial_procurement_conflict": {
        "conflict_id": ("TEXT", 0, 1),
        "procurement_id": ("TEXT", 0, 0),
        "source_system": ("TEXT", 0, 0),
        "source_record_id": ("TEXT", 0, 0),
        "subject_kind": ("TEXT", 1, 0),
        "subject_key": ("TEXT", 0, 0),
        "account_id": ("TEXT", 0, 0),
        "reason_code": ("TEXT", 1, 0),
        "confidence": ("TEXT", 1, 0),
        "detail_json": ("TEXT", 0, 0),
        "created_at": ("TEXT", 1, 0),
    },
    "commercial_procurement_enrichment_candidate": {
        "candidate_id": ("TEXT", 0, 1),
        "procurement_id": ("TEXT", 0, 0),
        "source_system": ("TEXT", 0, 0),
        "source_record_id": ("TEXT", 0, 0),
        "buyer_name_raw": ("TEXT", 0, 0),
        "account_id": ("TEXT", 0, 0),
        "reason_code": ("TEXT", 1, 0),
        "confidence": ("TEXT", 1, 0),
        "recommended_research_field": ("TEXT", 1, 0),
        "priority": ("INTEGER", 1, 0),
        "operator_queue_eligible": ("INTEGER", 1, 0),
        "candidate_account_ids_json": ("TEXT", 0, 0),
    },
    "commercial_procurement_build_meta": {
        "meta_key": ("TEXT", 0, 1),
        "meta_value": ("TEXT", 1, 0),
    },
}

REQUIRED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("idx_cps_tender_key", "commercial_procurement_signal", ("source_system", "canonical_tender_key")),
    ("idx_cps_context", "commercial_procurement_signal", ("procurement_context",)),
    ("idx_cps_buyer_norm", "commercial_procurement_signal", ("buyer_name_norm",)),
    ("idx_cps_region", "commercial_procurement_signal", ("region",)),
    ("idx_cpar_account", "commercial_procurement_account_resolution", ("account_id",)),
    ("idx_cpar_status", "commercial_procurement_account_resolution", ("resolution_status",)),
    ("idx_cpar_route", "commercial_procurement_account_resolution", ("link_route",)),
    ("idx_cpe_subject", "commercial_procurement_evidence", ("subject_kind", "subject_id")),
    ("idx_cpe_source_pointer", "commercial_procurement_evidence", ("source_table", "source_record_id")),
    ("idx_cpc_reason", "commercial_procurement_conflict", ("reason_code",)),
    ("idx_cpc_procurement", "commercial_procurement_conflict", ("procurement_id",)),
    ("idx_cpec_eligible", "commercial_procurement_enrichment_candidate", ("operator_queue_eligible",)),
    ("idx_cpec_reason", "commercial_procurement_enrichment_candidate", ("reason_code",)),
    ("idx_cpec_procurement", "commercial_procurement_enrichment_candidate", ("procurement_id",)),
)

# Normalized CHECK substrings that must appear in CREATE TABLE SQL (whitespace-insensitive).
REQUIRED_CHECK_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "commercial_procurement_signal": (
        "tender_key_kind in ('codigo_externo','codigo_licitacion','numero_adquisicion')",
        "procurement_context in ('none','tender_watch','tender_active','historical_tender','unknown')",
        "confidence in ('high','medium','low','none')",
        "line_item_count >= 1",
    ),
    "commercial_procurement_account_resolution": (
        "resolution_status in ('linked','unlinked','ambiguous','refused')",
        "confidence in ('high','medium','low','none')",
        "auto_link_allowed in (0, 1)",
        "resolution_status = 'linked'",
        "account_id is not null",
        "auto_link_allowed = 1",
        "resolution_status in ('unlinked','ambiguous','refused')",
        "account_id is null",
        "auto_link_allowed = 0",
    ),
    "commercial_procurement_conflict": (
        "confidence in ('high','medium','low','none')",
        "procurement_id is not null",
        "source_system is not null and source_record_id is not null",
    ),
    "commercial_procurement_enrichment_candidate": (
        "confidence in ('high','medium','low','none')",
        "operator_queue_eligible in (0, 1)",
    ),
}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _normalize_sql(sql: str) -> str:
    """Lowercase and strip whitespace/punctuation noise for CHECK matching."""
    compact = "".join((sql or "").lower().replace('"', "").replace("`", "").split())
    return compact


def _unique_covers(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> bool:
    for idx in conn.execute(f"PRAGMA index_list({table})"):
        if int(idx[2]) != 1:
            continue
        idx_cols = tuple(str(r[2]) for r in conn.execute(f"PRAGMA index_info({idx[1]})"))
        if idx_cols == columns:
            return True
        if set(idx_cols) == set(columns) and len(idx_cols) == len(columns):
            return True
    return False


def _index_columns(conn: sqlite3.Connection, index_name: str) -> tuple[str, ...] | None:
    rows = list(conn.execute(f"PRAGMA index_info({index_name})"))
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: int(r[0]))
    return tuple(str(r[2]) for r in ordered)


def _fk_ok(conn: sqlite3.Connection, table: str, parent: str, column: str) -> bool:
    for r in conn.execute(f"PRAGMA foreign_key_list({table})"):
        if str(r[2]) == parent and str(r[3]) == column:
            return True
    return False


def assert_schema_compatible(conn: sqlite3.Connection) -> dict[str, Any]:
    """Fail-closed structural compatibility gate."""
    present = [t for t in _REQUIRED_TABLES if _table_exists(conn, t)]
    if not present:
        return {"status": "absent", "tables": []}
    missing = [t for t in _REQUIRED_TABLES if t not in present]
    if missing:
        raise SchemaIncompatibilityError(
            "incompatible commercial_procurement schema: partial tables present "
            f"(missing={missing})"
        )

    for table, cols_manifest in SCHEMA_COLUMN_MANIFEST.items():
        info = {str(r[1]): r for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, (aff, notnull, pkpos) in cols_manifest.items():
            if col not in info:
                raise SchemaIncompatibilityError(
                    f"incompatible {table}: missing column {col}"
                )
            row = info[col]
            got_aff = _affinity(str(row[2]))
            if got_aff != aff:
                raise SchemaIncompatibilityError(
                    f"incompatible {table}.{col}: affinity {got_aff} != {aff}"
                )
            if int(row[3]) != int(notnull):
                raise SchemaIncompatibilityError(
                    f"incompatible {table}.{col}: NOT NULL {int(row[3])} != {notnull}"
                )
            if int(row[5]) != int(pkpos):
                raise SchemaIncompatibilityError(
                    f"incompatible {table}.{col}: pk position {int(row[5])} != {pkpos}"
                )

    if not _unique_covers(
        conn, "commercial_procurement_signal", ("source_system", "canonical_tender_key")
    ):
        raise SchemaIncompatibilityError(
            "incompatible commercial_procurement_signal: missing UNIQUE "
            "(source_system, canonical_tender_key)"
        )
    if not _unique_covers(
        conn, "commercial_procurement_account_resolution", ("procurement_id",)
    ):
        raise SchemaIncompatibilityError(
            "incompatible commercial_procurement_account_resolution: "
            "missing UNIQUE(procurement_id)"
        )

    if not _fk_ok(
        conn,
        "commercial_procurement_account_resolution",
        "commercial_procurement_signal",
        "procurement_id",
    ):
        raise SchemaIncompatibilityError(
            "incompatible account_resolution: missing FK to signal.procurement_id"
        )
    if not _fk_ok(
        conn,
        "commercial_procurement_conflict",
        "commercial_procurement_signal",
        "procurement_id",
    ):
        raise SchemaIncompatibilityError(
            "incompatible conflict: missing FK to signal.procurement_id"
        )
    if not _fk_ok(
        conn,
        "commercial_procurement_enrichment_candidate",
        "commercial_procurement_signal",
        "procurement_id",
    ):
        raise SchemaIncompatibilityError(
            "incompatible enrichment_candidate: missing FK to signal.procurement_id"
        )

    for index_name, table, cols in REQUIRED_INDEXES:
        got = _index_columns(conn, index_name)
        if got is None:
            raise SchemaIncompatibilityError(
                f"incompatible schema: missing index {index_name}"
            )
        if got != cols:
            raise SchemaIncompatibilityError(
                f"incompatible index {index_name}: columns {got} != {cols}"
            )

    for table, fragments in REQUIRED_CHECK_FRAGMENTS.items():
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row is None or not row[0]:
            raise SchemaIncompatibilityError(f"incompatible {table}: missing CREATE SQL")
        norm = _normalize_sql(str(row[0]))
        for frag in fragments:
            if _normalize_sql(frag) not in norm:
                raise SchemaIncompatibilityError(
                    f"incompatible {table}: missing CHECK contract fragment {frag!r}"
                )

    return {"status": "compatible", "tables": list(_REQUIRED_TABLES)}


def procurement_tables_present(conn: sqlite3.Connection) -> bool:
    return any(_table_exists(conn, t) for t in _REQUIRED_TABLES)


def procurement_tables_complete(conn: sqlite3.Connection) -> bool:
    return all(_table_exists(conn, t) for t in _REQUIRED_TABLES)


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
    "REQUIRED_CHECK_FRAGMENTS",
    "REQUIRED_INDEXES",
    "SCHEMA_COLUMN_MANIFEST",
    "TABLE_INSERT_ORDER",
    "SchemaIncompatibilityError",
    "assert_schema_compatible",
    "clear_rebuildable_procurement_tables",
    "create_validation_schema",
    "ensure_commercial_procurement_tables",
    "procurement_tables_complete",
    "procurement_tables_present",
]
