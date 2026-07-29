"""Path safety and orchestration for commercial opportunity stage rebuilds (PR3).

Transaction contract B: additive schema may remain after first-run DDL;
DELETE+INSERT opportunity data is atomic with foreign_keys=ON.

Dry-run resolves PR2 identity in-memory (no identity table writes).
Apply refuses unless persisted PR2 identity snapshot fingerprint matches.
Apply re-verifies identity + opportunity source fingerprints inside the write
transaction before DELETE+INSERT (stale-plan refuse).
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
    validate_run_context_mode,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
)
from origenlab_email_pipeline.commercial_identity.fingerprint import (
    FINGERPRINT_ALGORITHM_VERSION,
    identity_resolution_fingerprint,
)
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_identity.sources import load_source_identity_rows
from origenlab_email_pipeline.commercial_opportunity.constants import (
    OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION,
    TRANSACTION_CONTRACT,
)
from origenlab_email_pipeline.commercial_opportunity.identity_gate import (
    IdentitySnapshotError,
    StaleBuildPlanError,
    classify_identity_snapshot,
    load_identity_snapshot_meta,
    verify_identity_snapshot,
)
from origenlab_email_pipeline.commercial_opportunity.models import OpportunityResolution
from origenlab_email_pipeline.commercial_opportunity.persist import write_opportunity_resolution
from origenlab_email_pipeline.commercial_opportunity.resolve import resolve_opportunities
from origenlab_email_pipeline.commercial_opportunity.schema import ensure_commercial_opportunity_tables
from origenlab_email_pipeline.commercial_opportunity.source_fingerprint import (
    opportunity_source_fingerprint,
)
from origenlab_email_pipeline.commercial_opportunity.sources import load_opportunity_sources


@dataclass(frozen=True)
class OpportunityBuildPlan:
    sqlite_path: Path
    apply: bool
    resolution: OpportunityResolution
    planned_writes: dict[str, int]
    run_context: str
    identity_fingerprint: str
    identity_fingerprint_algorithm_version: str
    identity_fingerprint_match_status: str
    opportunity_source_fingerprint: str
    opportunity_source_fingerprint_algorithm_version: str


def plan_opportunity_build(
    *,
    sqlite_path: Path,
    apply: bool,
    run_context: str | None = None,
) -> OpportunityBuildPlan:
    """Read sources + resolve identity in-memory + resolve opportunities. No writes."""
    ctx = normalize_run_context(run_context)
    validate_run_context_mode(run_context=ctx, apply=apply)
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
    source_fp = opportunity_source_fingerprint(
        deals=sources["deals"],
        events=sources["events"],
        documents=sources["documents"],
        payments=sources["payments"],
        signals=sources["signals"],
        contact_master=sources["contact_master"],
    )

    match_status = classify_identity_snapshot(
        snapshot=snapshot,
        expected_fingerprint=fingerprint,
    )
    if apply and match_status != "matched":
        verify_identity_snapshot(snapshot=snapshot, expected_fingerprint=fingerprint)

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
    resolution.metrics["identity_fingerprint_algorithm_version"] = FINGERPRINT_ALGORITHM_VERSION
    resolution.metrics["opportunity_source_fingerprint"] = source_fp
    resolution.metrics["opportunity_source_fingerprint_algorithm_version"] = (
        OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION
    )

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
        identity_fingerprint_algorithm_version=FINGERPRINT_ALGORITHM_VERSION,
        identity_fingerprint_match_status=match_status,
        opportunity_source_fingerprint=source_fp,
        opportunity_source_fingerprint_algorithm_version=OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION,
    )


def apply_opportunity_build(
    plan: OpportunityBuildPlan,
    *,
    inject_failure: Callable[[sqlite3.Connection], None] | None = None,
    inject_source_change: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    if not plan.apply:
        raise CommercialIdentityPathError("apply_opportunity_build called without apply=True")

    conn = sqlite3.connect(str(plan.sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_commercial_opportunity_tables(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        if fk is None or int(fk[0]) != 1:
            raise RuntimeError("PRAGMA foreign_keys=ON failed; refusing unsafe opportunity rebuild")

        conn.execute("BEGIN")
        try:
            # Optional injected mutation before fingerprint recompute (race simulation).
            if inject_source_change is not None:
                inject_source_change(conn)

            # Recompute fingerprints under the write transaction (contract A).
            identity_rows = load_source_identity_rows(conn)
            sources = load_opportunity_sources(conn)
            snapshot = load_identity_snapshot_meta(conn)
            live_identity = resolve_identity(identity_rows)
            live_identity_fp = identity_resolution_fingerprint(live_identity)
            live_source_fp = opportunity_source_fingerprint(
                deals=sources["deals"],
                events=sources["events"],
                documents=sources["documents"],
                payments=sources["payments"],
                signals=sources["signals"],
                contact_master=sources["contact_master"],
            )
            verify_identity_snapshot(
                snapshot=snapshot,
                expected_fingerprint=live_identity_fp,
            )
            if live_identity_fp != plan.identity_fingerprint:
                raise StaleBuildPlanError(
                    "stale_build_plan: identity fingerprint changed between plan and apply"
                )
            if live_source_fp != plan.opportunity_source_fingerprint:
                raise StaleBuildPlanError(
                    "stale_build_plan: opportunity source fingerprint changed between plan and apply"
                )

            counts = write_opportunity_resolution(
                conn,
                plan.resolution,
                run_context=plan.run_context,
                identity_fingerprint=plan.identity_fingerprint,
                identity_fingerprint_match_status=plan.identity_fingerprint_match_status,
                identity_fingerprint_algorithm_version=plan.identity_fingerprint_algorithm_version,
                opportunity_source_fingerprint=plan.opportunity_source_fingerprint,
                opportunity_source_fingerprint_algorithm_version=(
                    plan.opportunity_source_fingerprint_algorithm_version
                ),
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
            "opportunity_source_fingerprint": plan.opportunity_source_fingerprint,
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
    plan = plan_opportunity_build(sqlite_path=sqlite_path, apply=apply, run_context=ctx)
    summary: dict[str, Any] = {
        "sqlite_path": str(sqlite_path),
        "mode": "apply" if apply else "dry-run",
        "planned_writes": plan.planned_writes,
        "metrics": plan.resolution.metrics,
        "applied": False,
        "transaction_contract": TRANSACTION_CONTRACT,
        "run_context": plan.run_context,
        "identity_fingerprint": plan.identity_fingerprint,
        "identity_fingerprint_algorithm_version": plan.identity_fingerprint_algorithm_version,
        "identity_fingerprint_match_status": plan.identity_fingerprint_match_status,
        "opportunity_source_fingerprint": plan.opportunity_source_fingerprint,
        "opportunity_source_fingerprint_algorithm_version": (
            plan.opportunity_source_fingerprint_algorithm_version
        ),
    }
    if not apply:
        return summary
    result = apply_opportunity_build(plan)
    summary["applied"] = True
    summary["written"] = result["written"]
    return summary


__all__ = [
    "CommercialIdentityPathError",
    "IdentitySnapshotError",
    "OpportunityBuildPlan",
    "StaleBuildPlanError",
    "apply_opportunity_build",
    "plan_opportunity_build",
    "require_explicit_sqlite_path",
    "run_opportunity_build",
]
