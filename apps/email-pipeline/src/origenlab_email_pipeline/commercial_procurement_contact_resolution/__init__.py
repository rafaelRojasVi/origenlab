"""PR5E — deterministic organization/contact resolution (read-only planning)."""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    CONTACT_RESOLUTION_PLANNER_VERSION,
    CONTACT_RESOLUTION_STATUSES_V1,
    FORBIDDEN_CLI_FLAGS,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.planner import (
    ContactReconciliationError,
    build_contact_resolution_plan,
    write_contact_resolution_outputs,
)

__all__ = [
    "CONTACT_RESOLUTION_PLANNER_VERSION",
    "CONTACT_RESOLUTION_STATUSES_V1",
    "FORBIDDEN_CLI_FLAGS",
    "ContactReconciliationError",
    "build_contact_resolution_plan",
    "write_contact_resolution_outputs",
]
