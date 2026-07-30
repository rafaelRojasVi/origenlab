"""Transactional persistence for commercial procurement read model (PR4).

Caller owns BEGIN/COMMIT/ROLLBACK. Never mutates non-PR4 tables.
"""

from __future__ import annotations

import sqlite3

from origenlab_email_pipeline.commercial_procurement.models import ProcurementPlan
from origenlab_email_pipeline.commercial_procurement.schema import (
    clear_rebuildable_procurement_tables,
)


def write_procurement_plan(
    conn: sqlite3.Connection,
    plan: ProcurementPlan,
    *,
    build_meta: dict[str, str],
) -> dict[str, int]:
    """Replace rebuildable PR4 data inside the caller's transaction.

    Clears children first, then inserts parents-first. Does not commit.
    """
    clear_rebuildable_procurement_tables(conn)

    signal_rows = [
        (
            s.procurement_id,
            s.source_system,
            s.canonical_tender_key,
            s.tender_key_kind,
            s.buyer_name_raw,
            s.buyer_name_norm,
            s.buyer_domain_norm,
            s.buyer_email_norm,
            s.region,
            s.title,
            s.status_code,
            s.status_name,
            s.publication_at,
            s.close_at,
            s.procurement_context,
            s.context_reason_code,
            s.confidence,
            s.line_item_count,
            s.constituent_source_ids_json,
            s.constituent_lines_fp,
            s.first_seen_at,
            s.last_seen_at,
            s.review_status,
        )
        for s in plan.signals
    ]
    if signal_rows:
        conn.executemany(
            """
            INSERT INTO commercial_procurement_signal (
              procurement_id, source_system, canonical_tender_key, tender_key_kind,
              buyer_name_raw, buyer_name_norm, buyer_domain_norm, buyer_email_norm,
              region, title, status_code, status_name, publication_at, close_at,
              procurement_context, context_reason_code, confidence, line_item_count,
              constituent_source_ids_json, constituent_lines_fp,
              first_seen_at, last_seen_at, review_status
            ) VALUES (
              ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            signal_rows,
        )

    resolution_rows = [
        (
            r.resolution_id,
            r.procurement_id,
            r.resolution_status,
            r.account_id,
            r.link_route,
            r.confidence,
            r.reason_code,
            r.auto_link_allowed,
            r.review_status,
            r.candidate_account_ids_json,
        )
        for r in plan.resolutions
    ]
    if resolution_rows:
        conn.executemany(
            """
            INSERT INTO commercial_procurement_account_resolution (
              resolution_id, procurement_id, resolution_status, account_id,
              link_route, confidence, reason_code, auto_link_allowed,
              review_status, candidate_account_ids_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            resolution_rows,
        )

    evidence_rows = [
        (
            e.evidence_id,
            e.subject_kind,
            e.subject_id,
            e.source_system,
            e.source_table,
            e.source_record_id,
            e.subject_key,
            e.evidence_type,
            e.evidence_at,
            e.reason_code,
            e.detail_json,
        )
        for e in plan.evidence
    ]
    if evidence_rows:
        conn.executemany(
            """
            INSERT INTO commercial_procurement_evidence (
              evidence_id, subject_kind, subject_id, source_system, source_table,
              source_record_id, subject_key, evidence_type, evidence_at,
              reason_code, detail_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            evidence_rows,
        )

    conflict_rows = [
        (
            c.conflict_id,
            c.procurement_id,
            c.source_system,
            c.source_record_id,
            c.subject_kind,
            c.subject_key,
            c.account_id,
            c.reason_code,
            c.confidence,
            c.detail_json,
            c.created_at,
        )
        for c in plan.conflicts
    ]
    if conflict_rows:
        conn.executemany(
            """
            INSERT INTO commercial_procurement_conflict (
              conflict_id, procurement_id, source_system, source_record_id,
              subject_kind, subject_key, account_id, reason_code, confidence,
              detail_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            conflict_rows,
        )

    enrichment_rows = [
        (
            x.candidate_id,
            x.procurement_id,
            x.source_system,
            x.source_record_id,
            x.buyer_name_raw,
            x.account_id,
            x.reason_code,
            x.confidence,
            x.recommended_research_field,
            x.priority,
            x.operator_queue_eligible,
            x.candidate_account_ids_json,
        )
        for x in plan.enrichment_candidates
    ]
    if enrichment_rows:
        conn.executemany(
            """
            INSERT INTO commercial_procurement_enrichment_candidate (
              candidate_id, procurement_id, source_system, source_record_id,
              buyer_name_raw, account_id, reason_code, confidence,
              recommended_research_field, priority, operator_queue_eligible,
              candidate_account_ids_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            enrichment_rows,
        )

    for key in sorted(build_meta.keys()):
        conn.execute(
            """
            INSERT INTO commercial_procurement_build_meta(meta_key, meta_value)
            VALUES (?, ?)
            """,
            (key, build_meta[key]),
        )

    return {
        "commercial_procurement_signal": len(signal_rows),
        "commercial_procurement_account_resolution": len(resolution_rows),
        "commercial_procurement_evidence": len(evidence_rows),
        "commercial_procurement_conflict": len(conflict_rows),
        "commercial_procurement_enrichment_candidate": len(enrichment_rows),
        "commercial_procurement_build_meta": len(build_meta),
    }


__all__ = ["write_procurement_plan"]
