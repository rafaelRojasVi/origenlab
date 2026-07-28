"""Read-only commercial truth audit (PR1).

Quantifies whether prospect / lead / Gmail / Labdelivery / tender classifications
match the real commercial workflow. Does not mutate SQLite, Gmail, or Postgres.
"""

from __future__ import annotations

from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import (
    connect_sqlite_readonly,
    require_explicit_paths,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.runner import (
    CommercialTruthAuditResult,
    run_commercial_truth_audit,
)

__all__ = [
    "CommercialTruthAuditResult",
    "connect_sqlite_readonly",
    "require_explicit_paths",
    "run_commercial_truth_audit",
]
