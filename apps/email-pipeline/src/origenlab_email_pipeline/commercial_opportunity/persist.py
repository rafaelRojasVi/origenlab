"""Transactional persistence for the commercial opportunity stage read model."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from origenlab_email_pipeline.commercial_opportunity.constants import (
    BUILD_CONTRACT,
    SCHEMA_VERSION,
)
from origenlab_email_pipeline.commercial_opportunity.models import OpportunityResolution
from origenlab_email_pipeline.commercial_opportunity.schema import clear_rebuildable_opportunity_tables


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_opportunity_resolution(
    conn: sqlite3.Connection,
    resolution: OpportunityResolution,
    *,
    run_context: str,
    identity_fingerprint: str,
    identity_fingerprint_match_status: str,
) -> dict[str, int]:
    """Replace rebuildable opportunity *data* inside the caller's transaction.

    Caller must ensure schema and open a transaction with foreign_keys=ON.
    Does not mutate commercial_deal* or commercial_identity_* tables.
    """
    clear_rebuildable_opportunity_tables(conn)

    opp_rows = [
        (
            o.opportunity_id,
            o.record_kind,
            o.account_id,
            o.primary_contact_id,
            o.source_kind,
            o.source_key,
            o.commercial_deal_id,
            o.deal_key,
            o.canonical_stage,
            o.source_stage,
            o.stage_reason_code,
            o.stage_confidence,
            1 if o.stage_is_current else 0,
            1 if o.stage_is_terminal else 0,
            o.stage_evidence_at,
            o.stage_evidence_id,
            o.first_activity_at,
            o.last_activity_at,
            o.identity_link_status,
            o.review_status,
        )
        for o in resolution.opportunities
    ]
    if opp_rows:
        conn.executemany(
            """
            INSERT INTO commercial_opportunity (
              opportunity_id, record_kind, account_id, primary_contact_id,
              source_kind, source_key, commercial_deal_id, deal_key,
              canonical_stage, source_stage, stage_reason_code, stage_confidence,
              stage_is_current, stage_is_terminal, stage_evidence_at, stage_evidence_id,
              first_activity_at, last_activity_at, identity_link_status, review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            opp_rows,
        )

    event_rows = [
        (
            e.event_id,
            e.opportunity_id,
            e.canonical_event_type,
            e.source_event_type,
            e.event_at,
            e.source_table,
            e.source_record_id,
            e.source_email_id,
            e.source_attachment_id,
            e.confidence,
            1 if e.operator_confirmed else 0,
            e.detail_json,
        )
        for e in resolution.events
    ]
    if event_rows:
        conn.executemany(
            """
            INSERT INTO commercial_opportunity_event (
              event_id, opportunity_id, canonical_event_type, source_event_type,
              event_at, source_table, source_record_id, source_email_id,
              source_attachment_id, confidence, operator_confirmed, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            event_rows,
        )

    evidence_rows = [
        (
            x.evidence_id,
            x.opportunity_id,
            x.subject_kind,
            x.source_table,
            x.source_record_id,
            x.evidence_type,
            x.evidence_at,
            x.confidence,
            x.reason_code,
            x.source_email_id,
            x.source_attachment_id,
            x.detail_json,
        )
        for x in resolution.evidence
    ]
    if evidence_rows:
        conn.executemany(
            """
            INSERT INTO commercial_opportunity_evidence (
              evidence_id, opportunity_id, subject_kind, source_table, source_record_id,
              evidence_type, evidence_at, confidence, reason_code,
              source_email_id, source_attachment_id, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            evidence_rows,
        )

    conflict_rows = [
        (
            c.conflict_id,
            c.opportunity_id,
            c.conflict_type,
            c.reason_code,
            c.subject_keys_json,
            c.evidence_pointers_json,
            c.review_status,
            c.detail_json,
        )
        for c in resolution.conflicts
    ]
    if conflict_rows:
        conn.executemany(
            """
            INSERT INTO commercial_opportunity_conflict (
              conflict_id, opportunity_id, conflict_type, reason_code,
              subject_keys_json, evidence_pointers_json, review_status, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            conflict_rows,
        )

    meta = {
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "built_at": _now_iso(),
        "run_context": run_context,
        "identity_fingerprint": identity_fingerprint,
        "identity_fingerprint_match_status": identity_fingerprint_match_status,
        "metrics_json": json.dumps(resolution.metrics, sort_keys=True),
    }
    for k, v in meta.items():
        conn.execute(
            """
            INSERT INTO commercial_opportunity_build_meta(meta_key, meta_value)
            VALUES (?, ?)
            ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
            """,
            (k, v),
        )

    return {
        "opportunities": len(opp_rows),
        "events": len(event_rows),
        "evidence": len(evidence_rows),
        "conflicts": len(conflict_rows),
    }
