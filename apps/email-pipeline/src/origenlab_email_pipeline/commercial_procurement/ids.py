"""Deterministic stable IDs for commercial procurement (PR4)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


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


def plan_digest(*, table_rows: dict[str, list[dict[str, Any]]], algorithm: str) -> str:
    """Order-independent digest over every planned row in every proposed table."""
    payload: dict[str, Any] = {"algorithm": algorithm, "tables": {}}
    for table in sorted(table_rows.keys()):
        rows = sorted(table_rows[table], key=canonical_json)
        payload["tables"][table] = {
            "n": len(rows),
            "sha256": hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest(),
        }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
