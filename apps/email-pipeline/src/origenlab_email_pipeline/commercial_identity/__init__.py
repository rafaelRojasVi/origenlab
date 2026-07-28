"""Public API for the commercial identity read model (PR2)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_identity.builder import (
    CommercialIdentityPathError,
    IdentityBuildPlan,
    apply_identity_build,
    plan_identity_build,
    require_explicit_sqlite_path,
    run_identity_build,
)
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_identity.schema import (
    ensure_commercial_identity_tables,
)

__all__ = [
    "CommercialIdentityPathError",
    "IdentityBuildPlan",
    "apply_identity_build",
    "ensure_commercial_identity_tables",
    "plan_identity_build",
    "require_explicit_sqlite_path",
    "resolve_identity",
    "run_identity_build",
]
