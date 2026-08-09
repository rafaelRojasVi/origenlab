"""ANEXO-P1 — shadow prospect integration (measurement only).

Answers: if annex-backed PR5D evidence were considered by the institution
prospect planner, what prospects / queue memberships WOULD change?

Does not modify production queues, SQLite, Postgres, Gmail, contact resolution,
outreach authorization, or catalog capability. PR5F is not started here.
"""

from .constants import (
    CAPABILITY_CLASSES,
    COMPARISON_VERSION,
    QUEUE_DELTA_CLASSES,
    SHADOW_CHANGE_CLASSES,
)
from .models import (
    ClaimCapability,
    InstitutionShadowDelta,
    QueueShadowDelta,
    ShadowProspectComparisonResult,
    TenderShadowDelta,
)
from .planner import (
    AnexoShadowProspectError,
    assert_shadow_invariants,
    build_summary,
    run_shadow_comparison,
    write_shadow_outputs,
)

__all__ = [
    "AnexoShadowProspectError",
    "CAPABILITY_CLASSES",
    "COMPARISON_VERSION",
    "ClaimCapability",
    "InstitutionShadowDelta",
    "QUEUE_DELTA_CLASSES",
    "QueueShadowDelta",
    "SHADOW_CHANGE_CLASSES",
    "ShadowProspectComparisonResult",
    "TenderShadowDelta",
    "assert_shadow_invariants",
    "build_summary",
    "run_shadow_comparison",
    "write_shadow_outputs",
]
