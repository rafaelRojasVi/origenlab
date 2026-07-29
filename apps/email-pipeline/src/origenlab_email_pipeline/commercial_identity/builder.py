"""Path safety and orchestration for commercial identity rebuilds.

Transaction contract B (documented in constants.TRANSACTION_CONTRACT):
- Additive schema (`CREATE TABLE IF NOT EXISTS` via executescript) may remain after a
  first-run failure because SQLite executescript auto-commits DDL.
- Prior read-model *data* is never partially replaced: DELETE+INSERT runs in an
  explicit transaction with ``PRAGMA foreign_keys=ON`` and rolls back atomically.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
    RUN_CONTEXT_PRODUCTION_APPLY,
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
    TRANSACTION_CONTRACT,
    VALID_RUN_CONTEXTS,
)
from origenlab_email_pipeline.commercial_identity.fingerprint import (
    FINGERPRINT_ALGORITHM_VERSION,
    identity_resolution_fingerprint,
)
from origenlab_email_pipeline.commercial_identity.models import IdentityResolution
from origenlab_email_pipeline.commercial_identity.persist import write_identity_resolution
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_identity.schema import ensure_commercial_identity_tables
from origenlab_email_pipeline.commercial_identity.sources import load_source_identity_rows


class CommercialIdentityPathError(ValueError):
    """Raised when required explicit database path is missing or unsafe."""


def require_explicit_sqlite_path(sqlite_path: Path | None) -> Path:
    """Require explicit --sqlite-path (no ORIGENLAB_SQLITE_PATH / settings fallback)."""
    if sqlite_path is None:
        raise CommercialIdentityPathError(
            "--sqlite-path is required; refusing to fall back to ORIGENLAB_SQLITE_PATH / settings."
        )
    resolved = Path(sqlite_path).expanduser().resolve()
    if not resolved.is_file():
        raise CommercialIdentityPathError(f"SQLite path does not exist or is not a file: {resolved}")
    return resolved


def normalize_run_context(run_context: str | None) -> str:
    """Validate orchestrator-supplied run context. Default is local_fixture (conservative)."""
    ctx = (run_context or RUN_CONTEXT_LOCAL_FIXTURE).strip()
    if ctx not in VALID_RUN_CONTEXTS:
        raise CommercialIdentityPathError(
            f"Invalid --run-context {ctx!r}; expected one of {sorted(VALID_RUN_CONTEXTS)}"
        )
    return ctx


def validate_run_context_mode(*, run_context: str, apply: bool) -> None:
    """Refuse misleading run-context / apply combinations."""
    if run_context == RUN_CONTEXT_PRODUCTION_APPLY and not apply:
        raise CommercialIdentityPathError(
            "run-context production_apply is valid only with --apply"
        )
    if run_context == RUN_CONTEXT_PRODUCTION_DRY_RUN and apply:
        raise CommercialIdentityPathError(
            "run-context production_dry_run is valid only without --apply"
        )


@dataclass(frozen=True)
class IdentityBuildPlan:
    sqlite_path: Path
    apply: bool
    resolution: IdentityResolution
    planned_writes: dict[str, int]
    run_context: str
    identity_fingerprint: str
    identity_fingerprint_algorithm_version: str


def plan_identity_build(
    *,
    sqlite_path: Path,
    apply: bool,
    run_context: str | None = None,
) -> IdentityBuildPlan:
    """Read sources and resolve identity. Never writes unless caller later applies."""
    ctx = normalize_run_context(run_context)
    validate_run_context_mode(run_context=ctx, apply=apply)
    conn = sqlite3.connect(f"file:{sqlite_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        rows = load_source_identity_rows(conn)
    finally:
        conn.close()
    resolution = resolve_identity(rows)
    resolution.metrics["label"] = ctx
    resolution.metrics["run_context"] = ctx
    fingerprint = identity_resolution_fingerprint(resolution)
    resolution.metrics["identity_fingerprint"] = fingerprint
    resolution.metrics["identity_fingerprint_algorithm_version"] = FINGERPRINT_ALGORITHM_VERSION
    planned = {
        "commercial_identity_account": len(resolution.accounts),
        "commercial_identity_contact": len(resolution.contacts),
        "commercial_identity_evidence": len(resolution.evidence),
        "commercial_identity_conflict": len(resolution.conflicts),
        "commercial_identity_account_alias": sum(len(a.aliases) for a in resolution.accounts),
        "commercial_identity_account_domain": sum(len(a.domains) for a in resolution.accounts),
    }
    return IdentityBuildPlan(
        sqlite_path=sqlite_path,
        apply=apply,
        resolution=resolution,
        planned_writes=planned,
        run_context=ctx,
        identity_fingerprint=fingerprint,
        identity_fingerprint_algorithm_version=FINGERPRINT_ALGORITHM_VERSION,
    )


def apply_identity_build(
    plan: IdentityBuildPlan,
    *,
    inject_failure: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    """Apply rebuild under transaction contract B.

    1. Ensure additive schema (may auto-commit via executescript).
    2. BEGIN data replacement with foreign_keys=ON.
    3. DELETE+INSERT identity rows; commit or roll back atomically.
    """
    if not plan.apply:
        raise CommercialIdentityPathError("apply_identity_build called without apply=True")

    conn = sqlite3.connect(str(plan.sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        # Schema ensure is outside the data transaction (executescript commits DDL).
        ensure_commercial_identity_tables(conn)

        conn.execute("PRAGMA foreign_keys = ON")
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        if fk is None or int(fk[0]) != 1:
            raise RuntimeError("PRAGMA foreign_keys=ON failed; refusing unsafe identity rebuild")

        conn.execute("BEGIN")
        try:
            counts = write_identity_resolution(
                conn,
                plan.resolution,
                run_context=plan.run_context,
                identity_fingerprint=plan.identity_fingerprint,
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
            "identity_fingerprint_algorithm_version": plan.identity_fingerprint_algorithm_version,
        }
    finally:
        conn.close()


def run_identity_build(
    *,
    sqlite_path: Path,
    apply: bool,
    run_context: str | None = None,
) -> dict[str, Any]:
    """Dry-run (default) or apply commercial identity rebuild."""
    plan = plan_identity_build(sqlite_path=sqlite_path, apply=apply, run_context=run_context)
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
    }
    if not apply:
        return summary
    result = apply_identity_build(plan)
    summary["applied"] = True
    summary["written"] = result["written"]
    return summary
