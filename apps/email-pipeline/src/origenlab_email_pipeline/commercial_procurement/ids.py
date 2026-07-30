"""Deterministic stable IDs and digests for commercial procurement (PR4)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from origenlab_email_pipeline.commercial_procurement.constants import (
    PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM,
    PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM,
    SEMANTIC_PLAN_TABLES,
)

# Volatile conflict fields excluded from semantic hashing.
_SEMANTIC_CONFLICT_EXCLUDE_FIELDS = frozenset({"created_at"})


def _sha32(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def stable_procurement_id(*, source_system: str, canonical_tender_key: str) -> str:
    return "p_" + _sha32(
        f"v1|procurement|{(source_system or '').strip()}|{(canonical_tender_key or '').strip()}"
    )


def stable_resolution_id(
    *,
    procurement_id: str,
    link_route: str,
    reason_code: str,
    resolution_status: str,
) -> str:
    material = (
        f"v1|procurement-resolution|{procurement_id}|{link_route}|{reason_code}|{resolution_status}"
    )
    return "r_" + _sha32(material)


def stable_evidence_id(
    *,
    subject_kind: str,
    subject_id: str,
    source_table: str,
    source_record_id: str,
    evidence_type: str,
    reason_code: str,
) -> str:
    material = "|".join(
        [
            "v1|procurement-evidence",
            subject_kind,
            subject_id,
            source_table,
            source_record_id,
            evidence_type,
            reason_code,
        ]
    )
    return "e_" + _sha32(material)


def stable_conflict_id_for_source(
    *,
    source_system: str,
    source_record_id: str,
    reason_code: str,
) -> str:
    material = (
        f"v1|procurement-conflict|{source_system}|{source_record_id}|{reason_code}"
    )
    return "c_" + _sha32(material)


def stable_conflict_id_for_signal(
    *,
    procurement_id: str,
    reason_code: str,
    detail_key: str,
) -> str:
    material = f"v1|procurement-conflict|{procurement_id}|{reason_code}|{detail_key}"
    return "c_" + _sha32(material)


def stable_enrichment_candidate_id(
    *,
    subject_id: str,
    reason_code: str,
    research_field: str,
) -> str:
    material = f"v1|procurement-enrichment|{subject_id}|{reason_code}|{research_field}"
    return "q_" + _sha32(material)


def subject_key_for_source(*, source_system: str, source_record_id: str) -> str:
    return f"v1|procurement-source|{source_system}|{source_record_id}"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize_semantic_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if table == "commercial_procurement_conflict":
        for field in _SEMANTIC_CONFLICT_EXCLUDE_FIELDS:
            out.pop(field, None)
    return out


def table_rows_digest(
    *,
    table_rows: dict[str, list[dict[str, Any]]],
    algorithm: str,
    normalize_row=None,
) -> str:
    """Order-independent digest over the given table rows."""
    payload: dict[str, Any] = {"algorithm": algorithm, "tables": {}}
    for table in sorted(table_rows.keys()):
        rows = table_rows[table]
        if normalize_row is not None:
            rows = [normalize_row(table, r) for r in rows]
        ordered = sorted(rows, key=canonical_json)
        payload["tables"][table] = {
            "n": len(ordered),
            "sha256": hashlib.sha256(canonical_json(ordered).encode("utf-8")).hexdigest(),
        }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def semantic_plan_digest(*, table_rows: dict[str, list[dict[str, Any]]]) -> str:
    """Hash only deterministic semantic business tables (no build_meta / wall-clock)."""
    semantic = {
        name: table_rows[name]
        for name in SEMANTIC_PLAN_TABLES
        if name in table_rows
    }
    return table_rows_digest(
        table_rows=semantic,
        algorithm=PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM,
        normalize_row=_normalize_semantic_row,
    )


def materialization_digest(*, table_rows: dict[str, list[dict[str, Any]]]) -> str:
    """Exact-row digest after an explicit materialization timestamp is assigned."""
    return table_rows_digest(
        table_rows=table_rows,
        algorithm=PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM,
        normalize_row=None,
    )


# Back-compat alias used by older call sites during transition.
def plan_digest(*, table_rows: dict[str, list[dict[str, Any]]], algorithm: str) -> str:
    return table_rows_digest(table_rows=table_rows, algorithm=algorithm)
