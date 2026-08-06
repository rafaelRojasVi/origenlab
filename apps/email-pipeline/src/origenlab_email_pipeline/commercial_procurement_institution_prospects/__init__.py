"""PR5E.2 — institution prospect recognition over PR5C/D/E (read-only)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_scope import (
    classify_provisional_disposition,
    refine_commercial_signal,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.clusters import (
    InstitutionReviewCluster,
    build_institution_review_clusters,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    CONTRACT_VERSION,
    FORBIDDEN_CLI_FLAGS,
    OPERATOR_QUEUE_NAMES,
    PLANNER_VERSION,
    RECOGNITION_LAYER_VERSION,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.event_families import (
    ProcurementEventFamily,
    resolve_procurement_event_families,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
    LifecycleProjection,
    apply_lifecycle_precedence,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (
    InstitutionProspectPlanResult,
    build_institution_prospect_plan,
    build_institution_prospects_from_plans,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.queues import (
    build_operator_queues,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.reconcile import (
    InstitutionReconciliationError,
)

__all__ = [
    "CONTRACT_VERSION",
    "FORBIDDEN_CLI_FLAGS",
    "InstitutionProspectPlanResult",
    "InstitutionReconciliationError",
    "InstitutionReviewCluster",
    "LifecycleProjection",
    "OPERATOR_QUEUE_NAMES",
    "PLANNER_VERSION",
    "ProcurementEventFamily",
    "RECOGNITION_LAYER_VERSION",
    "apply_lifecycle_precedence",
    "build_institution_prospect_plan",
    "build_institution_prospects_from_plans",
    "build_institution_review_clusters",
    "build_operator_queues",
    "classify_provisional_disposition",
    "refine_commercial_signal",
    "resolve_procurement_event_families",
]
