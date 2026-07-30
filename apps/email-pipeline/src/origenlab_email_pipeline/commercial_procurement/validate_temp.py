"""Load a ProcurementPlan into a disposable SQLite DB and validate constraints."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from origenlab_email_pipeline.commercial_procurement.constants import EVIDENCE_SUBJECT_KINDS
from origenlab_email_pipeline.commercial_procurement.models import ProcurementPlan
from origenlab_email_pipeline.commercial_procurement.schema import (
    TABLE_INSERT_ORDER,
    create_validation_schema,
)

_BUILD_META_JSON_KEYS = frozenset(
    {
        "source_fingerprint_components_json",
        "metrics_json",
    }
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


def _parse_json(value: str | None, *, field: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise TempSchemaValidationError(f"invalid JSON in {field}") from exc


def _require_string_array(obj: Any, *, field: str) -> None:
    if not isinstance(obj, list) or not all(isinstance(x, str) for x in obj):
        raise TempSchemaValidationError(f"{field} must be a JSON array of strings")


def _require_object_or_array(obj: Any, *, field: str) -> None:
    if not isinstance(obj, (dict, list)):
        raise TempSchemaValidationError(f"{field} must be a JSON object or array")


def _require_object(obj: Any, *, field: str) -> None:
    if not isinstance(obj, dict):
        raise TempSchemaValidationError(f"{field} must be a JSON object")


def load_plan_into_temp_db(
    plan: ProcurementPlan,
    *,
    known_account_ids: frozenset[str] | None = None,
    source_pointer_registry: frozenset[tuple[str, str]] | None = None,
) -> sqlite3.Connection:
    """Create an in-memory DB, load the plan, and run fail-closed integrity checks.

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

    for table, rows in tables.items():
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if int(n) != len(rows):
            conn.close()
            raise TempSchemaValidationError(
                f"row count mismatch for {table}: db={n} plan={len(rows)}"
            )

    signals = conn.execute("SELECT COUNT(*) FROM commercial_procurement_signal").fetchone()[0]
    resolutions = conn.execute(
        "SELECT COUNT(*) FROM commercial_procurement_account_resolution"
    ).fetchone()[0]
    if int(signals) != int(resolutions):
        conn.close()
        raise TempSchemaValidationError("signal/resolution cardinality mismatch")

    registry = source_pointer_registry if source_pointer_registry is not None else (
        plan.source_pointer_registry
    )

    try:
        # Evidence subject integrity + fail-closed subject kinds
        for row in conn.execute(
            """
            SELECT subject_kind, subject_id, source_table, source_record_id, detail_json
            FROM commercial_procurement_evidence
            """
        ):
            kind, sid = row["subject_kind"], row["subject_id"]
            if kind not in EVIDENCE_SUBJECT_KINDS:
                raise TempSchemaValidationError(f"unknown evidence subject_kind: {kind}")
            if not row["source_record_id"]:
                raise TempSchemaValidationError(
                    f"empty source_record_id for evidence subject {kind}/{sid}"
                )
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
            elif kind in {
                "conflict",
                "unresolved_source",
                "line_conflict",
                "resolution_conflict",
            }:
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
            if not ok:
                raise TempSchemaValidationError(
                    f"evidence subject does not resolve: {kind}/{sid}"
                )

            src_table = row["source_table"]
            src_id = row["source_record_id"]
            if src_table in {"external_leads_raw", "lead_master"}:
                if (src_table, src_id) not in registry:
                    raise TempSchemaValidationError(
                        f"evidence source pointer missing from registry: "
                        f"{src_table}/{src_id}"
                    )
            detail = _parse_json(row["detail_json"], field="evidence.detail_json")
            if detail is not None:
                _require_object_or_array(detail, field="evidence.detail_json")

        # JSON contracts
        for row in conn.execute(
            "SELECT constituent_source_ids_json AS j FROM commercial_procurement_signal"
        ):
            obj = _parse_json(row["j"], field="signal.constituent_source_ids_json")
            _require_string_array(obj, field="signal.constituent_source_ids_json")

        for row in conn.execute(
            """
            SELECT resolution_status, account_id, candidate_account_ids_json AS j
            FROM commercial_procurement_account_resolution
            """
        ):
            obj = _parse_json(row["j"], field="resolution.candidate_account_ids_json")
            _require_string_array(obj, field="resolution.candidate_account_ids_json")
            status = row["resolution_status"]
            if status != "linked" and row["account_id"] is not None:
                raise TempSchemaValidationError(
                    f"non-linked resolution must have null account_id ({status})"
                )
            if known_account_ids is not None:
                if status == "linked":
                    if row["account_id"] not in known_account_ids:
                        raise TempSchemaValidationError(
                            f"linked account missing from PR2: {row['account_id']}"
                        )
                for cand in obj:
                    if cand not in known_account_ids:
                        raise TempSchemaValidationError(
                            f"ambiguous candidate missing from PR2: {cand}"
                        )

        for row in conn.execute(
            "SELECT detail_json FROM commercial_procurement_conflict WHERE detail_json IS NOT NULL"
        ):
            obj = _parse_json(row["detail_json"], field="conflict.detail_json")
            _require_object_or_array(obj, field="conflict.detail_json")

        for row in conn.execute(
            """
            SELECT account_id, candidate_account_ids_json AS j
            FROM commercial_procurement_enrichment_candidate
            """
        ):
            if row["j"] is not None:
                obj = _parse_json(row["j"], field="enrichment.candidate_account_ids_json")
                _require_string_array(obj, field="enrichment.candidate_account_ids_json")
                if known_account_ids is not None:
                    for cand in obj:
                        if cand not in known_account_ids:
                            raise TempSchemaValidationError(
                                f"enrichment candidate account missing from PR2: {cand}"
                            )
            if known_account_ids is not None and row["account_id"] is not None:
                if row["account_id"] not in known_account_ids:
                    raise TempSchemaValidationError(
                        f"enrichment account_id missing from PR2: {row['account_id']}"
                    )

        for row in conn.execute(
            "SELECT meta_key, meta_value FROM commercial_procurement_build_meta"
        ):
            if row["meta_key"] in _BUILD_META_JSON_KEYS:
                obj = _parse_json(row["meta_value"], field=f"build_meta.{row['meta_key']}")
                _require_object(obj, field=f"build_meta.{row['meta_key']}")

    except TempSchemaValidationError:
        conn.close()
        raise

    return conn


def validate_plan_in_temp_sqlite(
    plan: ProcurementPlan,
    *,
    known_account_ids: frozenset[str] | None = None,
    source_pointer_registry: frozenset[tuple[str, str]] | None = None,
) -> dict[str, int]:
    """Validate plan against disposable schema; return table row counts."""
    conn = load_plan_into_temp_db(
        plan,
        known_account_ids=known_account_ids,
        source_pointer_registry=source_pointer_registry,
    )
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
