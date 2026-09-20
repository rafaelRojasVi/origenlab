"""Wave 1A / Wave 1B → V2 import: verify, map, report, and (locally) apply.

The one entry point an operator uses is
`apps/email-pipeline/scripts/migration/import_waves_into_v2.py`. It is **dry-run by
default**; writing requires an explicit `--apply` and a disposable local database.

Module map:

* :mod:`.artifacts` — verified, read-only access to the private migration bundles.
* :mod:`.plan` — the deterministic mapping into `evidence.*` and `outbound.*`.
* :mod:`.target` — the loopback-only database boundary.
* :mod:`.apply` — the transactional, idempotent write path.
* :mod:`.report` — the PII-safe aggregate report and the private reject artifact.

Nothing here writes to `crm.*`: promotion from evidence to CRM truth is an operator
command (`docs/MIGRATION.md` §5 slice 2), not an import step.
"""

from __future__ import annotations

from origenlab_email_pipeline.migration.v2_import.artifacts import (
    ImportInputs,
    InputRefused,
    load_inputs,
)
from origenlab_email_pipeline.migration.v2_import.plan import (
    STRUCTURAL_GAPS,
    ImportPlan,
    MappingRefused,
    build_plan,
)
from origenlab_email_pipeline.migration.v2_import.target import (
    LocalTarget,
    TargetRefused,
    assert_local_target,
)

__all__ = [
    "STRUCTURAL_GAPS",
    "ImportInputs",
    "ImportPlan",
    "InputRefused",
    "LocalTarget",
    "MappingRefused",
    "TargetRefused",
    "assert_local_target",
    "build_plan",
    "load_inputs",
]
