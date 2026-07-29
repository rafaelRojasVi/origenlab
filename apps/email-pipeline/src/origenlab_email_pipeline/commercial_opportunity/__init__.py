"""Public API for the commercial opportunity stage read model (PR3)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_opportunity.builder import (
    CommercialIdentityPathError,
    IdentitySnapshotError,
    OpportunityBuildPlan,
    StaleBuildPlanError,
    apply_opportunity_build,
    plan_opportunity_build,
    require_explicit_sqlite_path,
    run_opportunity_build,
)
from origenlab_email_pipeline.commercial_opportunity.resolve import resolve_opportunities
from origenlab_email_pipeline.commercial_opportunity.schema import (
    ensure_commercial_opportunity_tables,
)
from origenlab_email_pipeline.commercial_opportunity.sources import SourceSchemaError

__all__ = [
    "CommercialIdentityPathError",
    "IdentitySnapshotError",
    "OpportunityBuildPlan",
    "SourceSchemaError",
    "StaleBuildPlanError",
    "apply_opportunity_build",
    "ensure_commercial_opportunity_tables",
    "plan_opportunity_build",
    "require_explicit_sqlite_path",
    "resolve_opportunities",
    "run_opportunity_build",
]
