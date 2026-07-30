"""Load a ProcurementPlan into a disposable SQLite DB and validate constraints."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from origenlab_email_pipeline.commercial_procurement.models import ProcurementPlan
from origenlab_email_pipeline.commercial_procurement.schema import (
    TABLE_INSERT_ORDER,
    create_validation_schema,
)


class TempSchemaValidationError(ValueError):
    """Plan failed temporary-schema validation."""


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
    for row in rows:
        conn.execute(sql, [row[c] for c in cols])


def load_plan_into_temp_db(
    plan: ProcurementPlan,
    *,
    known_account_ids: frozenset[str] | None = None,
) -> sqlite3.Connection:
    """Create an in-memory DB, load the plan, and run integrity checks.

    Returns the open connection (caller must close). Does not touch production.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_validation_schema(conn)
    tables = plan.table_rows()
    try:
        for table in TABLE_INSERT_ORDER:
            _insert_rows(conn, table, tables[table])
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.close()
        raise TempSchemaValidationError(f"integrity error loading plan: {exc}") from exc

    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        conn.close()
        raise TempSchemaValidationError(f"foreign_key_check failed: {list(fk)}")

    # Row counts
    for table, rows in tables.items():
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if int(n) != len(rows):
            conn.close()
            raise TempSchemaValidationError(
                f"row count mismatch for {table}: db={n} plan={len(rows)}"
            )

    # Resolution cardinality
    signals = conn.execute("SELECT COUNT(*) FROM commercial_procurement_signal").fetchone()[0]
    resolutions = conn.execute(
        "SELECT COUNT(*) FROM commercial_procurement_account_resolution"
    ).fetchone()[0]
    if int(signals) != int(resolutions):
        conn.close()
        raise TempSchemaValidationError("signal/resolution cardinality mismatch")

    # Every evidence subject resolves
    for row in conn.execute(
        "SELECT subject_kind, subject_id FROM commercial_procurement_evidence"
    ):
        kind, sid = row["subject_kind"], row["subject_id"]
        ok = False
        if kind == "signal":
            ok = (
                conn.execute(
                    "SELECT 1 FROM commercial_procurement_signal WHERE procurement_id=?",
                    (sid,),
                ).fetchone()
                is not None
            )
        elif kind == "resolution":
            ok = (
                conn.execute(
                    "SELECT 1 FROM commercial_procurement_account_resolution WHERE resolution_id=?",
                    (sid,),
                ).fetchone()
                is not None
            )
        elif kind in {"conflict", "unresolved_source", "line_conflict", "resolution_conflict"}:
            ok = (
                conn.execute(
                    "SELECT 1 FROM commercial_procurement_conflict WHERE conflict_id=?",
                    (sid,),
                ).fetchone()
                is not None
            )
        elif kind == "enrichment":
            ok = (
                conn.execute(
                    "SELECT 1 FROM commercial_procurement_enrichment_candidate WHERE candidate_id=?",
                    (sid,),
                ).fetchone()
                is not None
            )
        else:
            ok = True  # unknown kinds still allowed as pointers
        if not ok:
            conn.close()
            raise TempSchemaValidationError(
                f"evidence subject does not resolve: {kind}/{sid}"
            )

    # Linked accounts exist in known set when provided
    if known_account_ids is not None:
        for row in conn.execute(
            """
            SELECT account_id FROM commercial_procurement_account_resolution
            WHERE resolution_status='linked'
            """
        ):
            if row["account_id"] not in known_account_ids:
                conn.close()
                raise TempSchemaValidationError(
                    f"linked account missing from PR2: {row['account_id']}"
                )

    # JSON fields parse
    for table, col in (
        ("commercial_procurement_signal", "constituent_source_ids_json"),
        ("commercial_procurement_account_resolution", "candidate_account_ids_json"),
    ):
        for row in conn.execute(f"SELECT {col} AS j FROM {table}"):
            try:
                json.loads(row["j"])
            except json.JSONDecodeError as exc:
                conn.close()
                raise TempSchemaValidationError(f"invalid JSON in {table}.{col}") from exc

    return conn


def validate_plan_in_temp_sqlite(
    plan: ProcurementPlan,
    *,
    known_account_ids: frozenset[str] | None = None,
) -> dict[str, int]:
    """Validate plan against disposable schema; return table row counts."""
    conn = load_plan_into_temp_db(plan, known_account_ids=known_account_ids)
    try:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in TABLE_INSERT_ORDER
        }
    finally:
        conn.close()


__all__ = [
    "TempSchemaValidationError",
    "load_plan_into_temp_db",
    "validate_plan_in_temp_sqlite",
]
