"""Commercial procurement read-model planner + persistence (PR4)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement.builder import (
    ApplyNotImplementedError,
    CommercialIdentityPathError,
    IdentityGateError,
    PersistenceValidationError,
    PlanValidationError,
    ProcurementBuildResult,
    SchemaIncompatibilityError,
    SourceSchemaError,
    StaleProcurementBuildPlanError,
    TempSchemaValidationError,
    UnsafeInvocationError,
    require_explicit_sqlite_path,
    run_procurement_build,
    run_procurement_dry_run,
)
from origenlab_email_pipeline.commercial_procurement.models import ProcurementPlan
from origenlab_email_pipeline.commercial_procurement.planner import (
    plan_procurement,
    plan_procurement_from_connection,
)

__all__ = [
    "ApplyNotImplementedError",
    "CommercialIdentityPathError",
    "IdentityGateError",
    "PersistenceValidationError",
    "PlanValidationError",
    "ProcurementBuildResult",
    "ProcurementPlan",
    "SchemaIncompatibilityError",
    "SourceSchemaError",
    "StaleProcurementBuildPlanError",
    "TempSchemaValidationError",
    "UnsafeInvocationError",
    "plan_procurement",
    "plan_procurement_from_connection",
    "require_explicit_sqlite_path",
    "run_procurement_build",
    "run_procurement_dry_run",
]
