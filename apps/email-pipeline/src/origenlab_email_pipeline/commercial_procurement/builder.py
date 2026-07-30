"""Orchestration for commercial procurement dry-run planning (PR4).

This checkpoint is dry-run only. Production --apply is intentionally refused.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_identity.builder import (
    CommercialIdentityPathError,
    normalize_run_context,
    require_explicit_sqlite_path,
    validate_run_context_mode,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_PRODUCTION_APPLY,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    BUILD_CONTRACT,
    PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
    PROCUREMENT_PLAN_DIGEST_ALGORITHM,
    PROCUREMENT_SOURCE_FP_ALGORITHM,
    REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM,
    RESOLVER_BUILD_CONTRACT_VERSION,
    SCHEMA_VERSION,
    TRANSACTION_CONTRACT,
)
from origenlab_email_pipeline.commercial_procurement.models import ProcurementPlan
from origenlab_email_pipeline.commercial_procurement.planner import (
    IdentityGateError,
    PlanValidationError,
    plan_procurement_from_connection,
)
from origenlab_email_pipeline.commercial_procurement.sources import SourceSchemaError
from origenlab_email_pipeline.commercial_procurement.validate_temp import (
    TempSchemaValidationError,
    validate_plan_in_temp_sqlite,
)


class ApplyNotImplementedError(CommercialIdentityPathError):
    """Production persistence is out of scope for this PR."""


@dataclass(frozen=True)
class ProcurementBuildResult:
    sqlite_path: Path
    plan: ProcurementPlan
    summary: dict[str, Any]
    temp_schema_counts: dict[str, int]


def connect_production_readonly(sqlite_path: Path) -> sqlite3.Connection:
    path = sqlite_path.resolve()
    if not path.is_file():
        raise CommercialIdentityPathError(f"sqlite path does not exist: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def assert_no_write_connection(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE TEMP TABLE _procurement_write_probe(x INTEGER)")
    except sqlite3.OperationalError:
        return
    raise CommercialIdentityPathError("SQLite connection is writable; expected read-only")


def parse_as_of_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommercialIdentityPathError(
            f"--as-of-date must be YYYY-MM-DD; got {value!r}"
        ) from exc


def plan_to_json_summary(plan: ProcurementPlan, *, sqlite_path: Path) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "applied": False,
        "sqlite_path": str(sqlite_path.resolve()),
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "resolver_build_contract_version": RESOLVER_BUILD_CONTRACT_VERSION,
        "transaction_contract": TRANSACTION_CONTRACT,
        "source_fingerprint_algorithm": PROCUREMENT_SOURCE_FP_ALGORITHM,
        "source_fingerprint": plan.source_fingerprint,
        "source_fingerprint_components": plan.source_fingerprint_components,
        "identity_fingerprint_algorithm_version": plan.identity_fingerprint_algorithm_version,
        "identity_fingerprint": plan.identity_fingerprint,
        "build_plan_fingerprint_algorithm": PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
        "build_plan_fingerprint": plan.build_plan_fingerprint,
        "plan_digest_algorithm": PROCUREMENT_PLAN_DIGEST_ALGORITHM,
        "plan_digest": plan.plan_digest,
        "as_of_date": plan.as_of_date,
        "run_context": plan.run_context,
        "source_outcome_counts": {
            "all": plan.metrics["source_outcome_count"],
            "verified_lines": plan.metrics["verified_source_line_count"],
            "unresolved": plan.metrics["unresolved_source_row_count"],
        },
        "signal_count": plan.metrics["signal_count"],
        "resolution_distribution": plan.metrics["resolution_distribution"],
        "route_distribution": plan.metrics["route_distribution"],
        "procurement_context_distribution": plan.metrics["procurement_context_distribution"],
        "evidence_count": plan.metrics["evidence_count"],
        "conflict_distribution": plan.metrics["conflict_distribution"],
        "enrichment_distribution": plan.metrics["enrichment_distribution"],
        "operator_queue_eligible_count": plan.metrics["operator_queue_eligible_count"],
        "unique_linked_accounts": plan.metrics["unique_linked_accounts"],
        "metrics": plan.metrics,
        "planned_writes": {
            "commercial_procurement_signal": len(plan.signals),
            "commercial_procurement_account_resolution": len(plan.resolutions),
            "commercial_procurement_evidence": len(plan.evidence),
            "commercial_procurement_conflict": len(plan.conflicts),
            "commercial_procurement_enrichment_candidate": len(plan.enrichment_candidates),
            "commercial_procurement_build_meta": len(plan.build_meta),
        },
        "required_identity_fingerprint_algorithm": REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM,
    }


def run_procurement_dry_run(
    *,
    sqlite_path: Path,
    as_of_date: date | str,
    run_context: str | None = None,
    apply: bool = False,
    validate_temp_schema: bool = True,
) -> ProcurementBuildResult:
    """Read-only production dry-run. Refuses --apply."""
    if apply:
        raise ApplyNotImplementedError(
            "commercial procurement --apply is not implemented in this PR "
            "(planner/dry-run checkpoint only)"
        )
    path = require_explicit_sqlite_path(sqlite_path)
    ctx = normalize_run_context(run_context)
    if ctx == RUN_CONTEXT_PRODUCTION_APPLY:
        raise CommercialIdentityPathError(
            "run-context production_apply is invalid without --apply; "
            "this PR refuses --apply"
        )
    validate_run_context_mode(run_context=ctx, apply=False)
    as_of = parse_as_of_date(as_of_date) if isinstance(as_of_date, str) else as_of_date

    conn = connect_production_readonly(path)
    try:
        assert_no_write_connection(conn)
        # Capture PR2/PR3 fingerprints before planning for immutability proof in tests
        plan = plan_procurement_from_connection(
            conn=conn,
            as_of_date=as_of,
            run_context=ctx,
        )
        known = frozenset(
            r["account_id"]
            for r in conn.execute("SELECT account_id FROM commercial_identity_account")
        )
    finally:
        conn.close()

    temp_counts: dict[str, int] = {}
    if validate_temp_schema:
        temp_counts = validate_plan_in_temp_sqlite(plan, known_account_ids=known)

    summary = plan_to_json_summary(plan, sqlite_path=path)
    summary["temp_schema_validation"] = {
        "ok": True,
        "row_counts": temp_counts,
    }
    return ProcurementBuildResult(
        sqlite_path=path,
        plan=plan,
        summary=summary,
        temp_schema_counts=temp_counts,
    )


__all__ = [
    "ApplyNotImplementedError",
    "CommercialIdentityPathError",
    "IdentityGateError",
    "PlanValidationError",
    "ProcurementBuildResult",
    "SourceSchemaError",
    "TempSchemaValidationError",
    "assert_no_write_connection",
    "connect_production_readonly",
    "parse_as_of_date",
    "plan_to_json_summary",
    "require_explicit_sqlite_path",
    "run_procurement_dry_run",
]
