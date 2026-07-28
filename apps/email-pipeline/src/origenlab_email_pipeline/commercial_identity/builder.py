"""Path safety and orchestration for commercial identity rebuilds."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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


@dataclass(frozen=True)
class IdentityBuildPlan:
    sqlite_path: Path
    apply: bool
    resolution: IdentityResolution
    planned_writes: dict[str, int]


def plan_identity_build(*, sqlite_path: Path, apply: bool) -> IdentityBuildPlan:
    """Read sources and resolve identity. Never writes unless caller later applies."""
    conn = sqlite3.connect(f"file:{sqlite_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        rows = load_source_identity_rows(conn)
    finally:
        conn.close()
    resolution = resolve_identity(rows)
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
    )


def apply_identity_build(
    plan: IdentityBuildPlan,
    *,
    inject_failure: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    """Apply rebuild in a single transaction; roll back completely on failure."""
    if not plan.apply:
        raise CommercialIdentityPathError("apply_identity_build called without apply=True")

    conn = sqlite3.connect(str(plan.sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        ensure_commercial_identity_tables(conn)
        counts = write_identity_resolution(conn, plan.resolution)
        if inject_failure is not None:
            inject_failure(conn)
        conn.commit()
        return {"applied": True, "written": counts, "metrics": plan.resolution.metrics}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_identity_build(*, sqlite_path: Path, apply: bool) -> dict[str, Any]:
    """Dry-run (default) or apply commercial identity rebuild."""
    plan = plan_identity_build(sqlite_path=sqlite_path, apply=apply)
    summary: dict[str, Any] = {
        "sqlite_path": str(sqlite_path),
        "mode": "apply" if apply else "dry-run",
        "planned_writes": plan.planned_writes,
        "metrics": plan.resolution.metrics,
        "applied": False,
    }
    if not apply:
        return summary
    result = apply_identity_build(plan)
    summary["applied"] = True
    summary["written"] = result["written"]
    return summary
