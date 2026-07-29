"""Path safety and orchestration for commercial opportunity stage rebuilds (PR3).

Transaction contract B: additive schema may remain after first-run DDL;
DELETE+INSERT opportunity data is atomic with foreign_keys=ON.

Dry-run resolves PR2 identity in-memory (no identity table writes).
Apply refuses unless persisted PR2 identity snapshot fingerprint matches.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.commercial_identity.builder import (
    CommercialIdentityPathError,
    normalize_run_context,
    require_explicit_sqlite_path,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
)
from origenlab_email_pipeline.commercial_identity.fingerprint import identity_resolution_fingerprint
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_identity.sources import load_source_identity_rows
from origenlab_email_pipeline.commercial_opportunity.constants import TRANSACTION_CONTRACT
from origenlab_email_pipeline.commercial_opportunity.identity_gate import (
    IdentitySnapshotError,
    load_identity_snapshot_meta,
    verify_identity_snapshot,
)
from origenlab_email_pipeline.commercial_opportunity.models import OpportunityResolution
from origenlab_email_pipeline.commercial_opportunity.persist import write_opportunity_resolution
from origenlab_email_pipeline.commercial_opportunity.resolve import resolve_opportunities
from origenlab_email_pipeline.commercial_opportunity.schema import ensure_commercial_opportunity_tables
from origenlab_email_pipeline.commercial_opportunity.sources import load_opportunity_sources


@dataclass(frozen=True)
class OpportunityBuildPlan:
    sqlite_path: Path
    apply: bool
    resolution: OpportunityResolution
    planned_writes: dict[str, int]
    run_context: str
    identity_fingerprint: str
    identity_fingerprint_match_status: str


def plan_opportunity_build(
    *,
    sqlite_path: Path,
    apply: bool,
    run_context: str | None = None,
) -> OpportunityBuildPlan:
    """Read sources + resolve identity in-memory + resolve opportunities. No writes."""
    ctx = normalize_run_context(run_context)
    conn = sqlite3.connect(f"file:{sqlite_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        identity_rows = load_source_identity_rows(conn)
        sources = load_opportunity_sources(conn)
        snapshot = load_identity_snapshot_meta(conn)
    finally:
        conn.close()

    identity = resolve_identity(identity_rows)
    fingerprint = identity_resolution_fingerprint(identity)

    match_status = "not_checked"
    if apply:
        # Gate before planning apply — fail closed on missing/stale snapshot.
        try:
            match_status = verify_identity_snapshot(
                snapshot=snapshot,
                expected_fingerprint=fingerprint,
            )
        except IdentitySnapshotError:
            match_status = "missing" if not snapshot.present else "mismatched"
            # Re-raise after attaching status for caller metrics
            raise
    else:
        if snapshot.present and snapshot.identity_fingerprint:
            match_status = (
                "matched"
                if snapshot.identity_fingerprint == fingerprint
                and snapshot.schema_version
                else "mismatched"
            )
            if snapshot.identity_fingerprint == fingerprint:
                match_status = "matched"
            else:
                match_status = "mismatched"
        elif snapshot.present:
            match_status = "missing"
        else:
            match_status = "missing"

    resolution = resolve_opportunities(
        identity=identity,
        deals=sources["deals"],
        events=sources["events"],
        documents=sources["documents"],
        payments=sources["payments"],
        signals=sources["signals"],
        contact_master=sources["contact_master"],
        identity_fingerprint=fingerprint,
        identity_fingerprint_match_status=match_status,
    )
    resolution.metrics["label"] = ctx
    resolution.metrics["run_context"] = ctx

    planned = {
        "commercial_opportunity": len(resolution.opportunities),
        "commercial_opportunity_event": len(resolution.events),
        "commercial_opportunity_evidence": len(resolution.evidence),
        "commercial_opportunity_conflict": len(resolution.conflicts),
    }
    return OpportunityBuildPlan(
        sqlite_path=sqlite_path,
        apply=apply,
        resolution=resolution,
        planned_writes=planned,
        run_context=ctx,
        identity_fingerprint=fingerprint,
        identity_fingerprint_match_status=match_status,
    )


def apply_opportunity_build(
    plan: OpportunityBuildPlan,
    *,
    inject_failure: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    if not plan.apply:
        raise CommercialIdentityPathError("apply_opportunity_build called without apply=True")

    conn = sqlite3.connect(str(plan.sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        # Re-verify identity snapshot under write connection (fail closed).
        snapshot = load_identity_snapshot_meta(conn)
        verify_identity_snapshot(
            snapshot=snapshot,
            expected_fingerprint=plan.identity_fingerprint,
        )

        ensure_commercial_opportunity_tables(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        if fk is None or int(fk[0]) != 1:
            raise RuntimeError("PRAGMA foreign_keys=ON failed; refusing unsafe opportunity rebuild")

        conn.execute("BEGIN")
        try:
            counts = write_opportunity_resolution(
                conn,
                plan.resolution,
                run_context=plan.run_context,
                identity_fingerprint=plan.identity_fingerprint,
                identity_fingerprint_match_status=plan.identity_fingerprint_match_status,
            )
            if inject_failure is not None:
                inject_failure(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "applied": True,
            "written": counts,
            "metrics": plan.resolution.metrics,
            "transaction_contract": TRANSACTION_CONTRACT,
            "run_context": plan.run_context,
            "identity_fingerprint": plan.identity_fingerprint,
        }
    finally:
        conn.close()


def run_opportunity_build(
    *,
    sqlite_path: Path,
    apply: bool,
    run_context: str | None = None,
) -> dict[str, Any]:
    """Dry-run (default) or apply commercial opportunity stage rebuild."""
    ctx = run_context or RUN_CONTEXT_LOCAL_FIXTURE
    if apply:
        # plan_opportunity_build raises IdentitySnapshotError when gate fails
        plan = plan_opportunity_build(sqlite_path=sqlite_path, apply=True, run_context=ctx)
        summary: dict[str, Any] = {
            "sqlite_path": str(sqlite_path),
            "mode": "apply",
            "planned_writes": plan.planned_writes,
            "metrics": plan.resolution.metrics,
            "applied": False,
            "transaction_contract": TRANSACTION_CONTRACT,
            "run_context": plan.run_context,
            "identity_fingerprint": plan.identity_fingerprint,
            "identity_fingerprint_match_status": plan.identity_fingerprint_match_status,
        }
        result = apply_opportunity_build(plan)
        summary["applied"] = True
        summary["written"] = result["written"]
        return summary

    plan = plan_opportunity_build(sqlite_path=sqlite_path, apply=False, run_context=ctx)
    return {
        "sqlite_path": str(sqlite_path),
        "mode": "dry-run",
        "planned_writes": plan.planned_writes,
        "metrics": plan.resolution.metrics,
        "applied": False,
        "transaction_contract": TRANSACTION_CONTRACT,
        "run_context": plan.run_context,
        "identity_fingerprint": plan.identity_fingerprint,
        "identity_fingerprint_match_status": plan.identity_fingerprint_match_status,
    }


__all__ = [
    "CommercialIdentityPathError",
    "IdentitySnapshotError",
    "OpportunityBuildPlan",
    "apply_opportunity_build",
    "plan_opportunity_build",
    "require_explicit_sqlite_path",
    "run_opportunity_build",
]
