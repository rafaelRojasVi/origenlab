"""Soft lifecycle between external_leads_raw and lead_master.

Identity model: one row per (source_name, canonical source_record_id), aligned with
`lead_master_keys`. Rows missing from the current raw snapshot can be marked
`upstream_sync_state = 'retired_no_raw'` (no hard delete). `normalize_leads` upsert
reactivates when the raw row reappears.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from origenlab_email_pipeline.leads_ingest import now_iso

RETIRED_UPSTREAM_STATE = "retired_no_raw"
DEFAULT_RETIRE_REASON = "missing_from_external_leads_raw"
LOG_ACTION_RETIRE = "retire_upstream_absent"


def sql_upstream_active(alias: str = "lm") -> str:
    """SQL predicate: lead is not soft-retired for missing raw."""
    a = alias.strip() or "lm"
    return (
        f"(COALESCE(NULLIF(TRIM({a}.upstream_sync_state), ''), 'active') "
        f"!= '{RETIRED_UPSTREAM_STATE}')"
    )


def sql_upstream_active_bare() -> str:
    """Predicate when the FROM clause uses unqualified lead_master columns."""
    return (
        f"(COALESCE(NULLIF(TRIM(upstream_sync_state), ''), 'active') "
        f"!= '{RETIRED_UPSTREAM_STATE}')"
    )


def sources_with_raw_snapshot(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT DISTINCT source_name FROM external_leads_raw").fetchall()
    return {str(r[0]) for r in rows if r[0] is not None and str(r[0]).strip()}


def _resolved_scope_sources(
    conn: sqlite3.Connection,
    *,
    only_sources: frozenset[str] | None,
) -> tuple[set[str], list[str]]:
    """Sources that have ≥1 raw row, optionally filtered. Returns (scope, warnings)."""
    raw_sources = sources_with_raw_snapshot(conn)
    warnings: list[str] = []
    if only_sources is not None:
        missing = sorted(only_sources - raw_sources)
        for s in missing:
            warnings.append(
                f"Source {s!r} has no rows in external_leads_raw; skipping retire "
                "for that source (conservative: empty snapshot)."
            )
        scope = raw_sources & only_sources
    else:
        scope = set(raw_sources)
    return scope, warnings


def list_retire_candidates(
    conn: sqlite3.Connection,
    *,
    only_sources: frozenset[str] | None = None,
) -> tuple[list[tuple[int, str, str]], list[str]]:
    """Return (candidates, warnings). Each candidate is (lead_id, source_name, canonical_id)."""
    scope, warnings = _resolved_scope_sources(conn, only_sources=only_sources)
    if not scope:
        return [], warnings
    placeholders = ",".join("?" * len(scope))
    canon_lm = "COALESCE(NULLIF(TRIM(lm.source_record_id), ''), '')"
    sql = f"""
    SELECT lm.id, lm.source_name, {canon_lm}
    FROM lead_master AS lm
    WHERE {sql_upstream_active("lm")}
      AND lm.source_name IN ({placeholders})
      AND NOT EXISTS (
        SELECT 1 FROM external_leads_raw AS r
        WHERE r.source_name = lm.source_name
          AND COALESCE(NULLIF(TRIM(r.source_record_id), ''), '') = {canon_lm}
      )
    ORDER BY lm.source_name, lm.id
    """
    params = tuple(sorted(scope))
    rows = conn.execute(sql, params).fetchall()
    out = [(int(r[0]), str(r[1]), str(r[2])) for r in rows]
    return out, warnings


def count_reactivatable_by_normalize(conn: sqlite3.Connection) -> int:
    """Leads currently retired but whose key exists again in raw (next normalize will reactivate)."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM lead_master AS lm
        WHERE lm.upstream_sync_state = ?
          AND EXISTS (
            SELECT 1 FROM external_leads_raw AS r
            WHERE r.source_name = lm.source_name
              AND COALESCE(NULLIF(TRIM(r.source_record_id), ''), '')
                  = COALESCE(NULLIF(TRIM(lm.source_record_id), ''), '')
          )
        """,
        (RETIRED_UPSTREAM_STATE,),
    ).fetchone()
    return int(row[0] if row else 0)


@dataclass
class ReconcileRunResult:
    dry_run: bool
    run_at: str
    scope_sources: list[str]
    warnings: list[str] = field(default_factory=list)
    retire_candidates: list[tuple[int, str, str]] = field(default_factory=list)
    reactivatable_next_normalize: int = 0
    retired_applied: int = 0


def run_upstream_reconcile(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    only_sources: frozenset[str] | None = None,
) -> ReconcileRunResult:
    """Compare raw keys to lead_master; optionally apply soft retire + log rows."""
    run_at = now_iso()
    candidates, warnings = list_retire_candidates(conn, only_sources=only_sources)
    scope, _ = _resolved_scope_sources(conn, only_sources=only_sources)
    re_n = count_reactivatable_by_normalize(conn)
    result = ReconcileRunResult(
        dry_run=dry_run,
        run_at=run_at,
        scope_sources=sorted(scope),
        warnings=warnings,
        retire_candidates=candidates,
        reactivatable_next_normalize=re_n,
        retired_applied=0,
    )
    if dry_run or not candidates:
        return result

    reason = DEFAULT_RETIRE_REASON
    dry_i = 0
    log_sql = """
    INSERT INTO lead_upstream_reconcile_log (
      run_at, dry_run, lead_id, source_name, canonical_source_record_id, action, detail
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    for lead_id, source_name, sk in candidates:
        conn.execute(
            log_sql,
            (
                run_at,
                dry_i,
                lead_id,
                source_name,
                sk,
                LOG_ACTION_RETIRE,
                reason,
            ),
        )
    ids = [c[0] for c in candidates]
    chunk_sz = 400
    for i in range(0, len(ids), chunk_sz):
        chunk = ids[i : i + chunk_sz]
        ph = ",".join("?" * len(chunk))
        conn.execute(
            f"""
            UPDATE lead_master
            SET upstream_sync_state = ?,
                upstream_retired_at = ?,
                upstream_retired_reason = ?
            WHERE id IN ({ph})
            """,
            (RETIRED_UPSTREAM_STATE, run_at, reason, *chunk),
        )
    result.retired_applied = len(ids)
    conn.commit()
    return result


def format_reconcile_report(result: ReconcileRunResult, *, preview_limit: int = 40) -> list[str]:
    """Human-readable lines for CLI or logs."""
    lines: list[str] = [
        "=== lead upstream reconcile (external_leads_raw vs lead_master) ===",
        f"run_at: {result.run_at}",
        f"mode: {'DRY-RUN (no writes)' if result.dry_run else 'APPLY'}",
        f"sources_in_scope (have raw rows): {', '.join(result.scope_sources) or '(none)'}",
        f"reactivatable_on_next_normalize: {result.reactivatable_next_normalize}",
        f"retire_candidates: {len(result.retire_candidates)}",
    ]
    for w in result.warnings:
        lines.append(f"WARNING: {w}")
    if result.retire_candidates:
        lines.append("--- preview (lead_id, source_name, canonical_source_record_id) ---")
        for tup in result.retire_candidates[:preview_limit]:
            lines.append(f"  {tup[0]}\t{tup[1]!r}\t{tup[2]!r}")
        if len(result.retire_candidates) > preview_limit:
            lines.append(f"  … {len(result.retire_candidates) - preview_limit} more")
    if not result.dry_run:
        lines.append(f"rows_updated: {result.retired_applied}")
    return lines


def reconcile_result_to_json_dict(result: ReconcileRunResult) -> dict:
    return {
        "dry_run": result.dry_run,
        "run_at": result.run_at,
        "scope_sources": result.scope_sources,
        "warnings": result.warnings,
        "retire_candidate_count": len(result.retire_candidates),
        "retire_candidates": [
            {"lead_id": a, "source_name": b, "canonical_source_record_id": c}
            for a, b, c in result.retire_candidates
        ],
        "reactivatable_next_normalize": result.reactivatable_next_normalize,
        "rows_updated": result.retired_applied,
    }


def dump_reconcile_json(result: ReconcileRunResult) -> str:
    return json.dumps(reconcile_result_to_json_dict(result), indent=2, ensure_ascii=False)
