"""Conservative Gmail/Drive evidence staging for the V2 durable core.

This package turns an operator-produced manifest of Gmail messages or Drive files into
**review candidates** — `evidence.source_record` rows marked `pending` and
`evidence.assertion` rows marked `unresolved` — and stops there.

It creates no contact, no organization and no person; it merges nothing; it grants no
permission to send; and it opens no network connection to Google or to anything else. The
step that decides what staged evidence establishes about a real person or institution is
`promote_evidence_into_crm.py`, which is a separate tool run by a human.

Three boundaries hold by construction:

* **Offline.** No Google client is imported anywhere in this package. The only input is a
  local JSON file (:mod:`.manifest`), so what reaches the database is always something a
  human could read and refuse first.
* **Loopback-only.** The database target is resolved by the Wave 1A/1B importer's own guard
  (:mod:`..v2_import.target`), unchanged and unweakened. There is no override flag.
* **Never durable identity.** The `crm.*` row count is taken before and after inside the
  staging transaction, and any change rolls the whole pass back (:mod:`.apply`).
"""

from __future__ import annotations

from origenlab_email_pipeline.migration.v2_evidence_stage.apply import (
    StageResult,
    apply_staging,
    assert_schema_accepts_staging,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (
    MANIFEST_VERSION,
    PROVIDER_ASSERTION_KINDS,
    PROVIDER_SOURCE_KIND,
    Manifest,
    ManifestRefused,
    Observation,
    StagedRecord,
    load_manifest,
    parse_manifest,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.report import (
    build_report,
    render_console,
)

__all__ = [
    "MANIFEST_VERSION",
    "PROVIDER_ASSERTION_KINDS",
    "PROVIDER_SOURCE_KIND",
    "Manifest",
    "ManifestRefused",
    "Observation",
    "StageResult",
    "StagedRecord",
    "apply_staging",
    "assert_schema_accepts_staging",
    "build_report",
    "load_manifest",
    "parse_manifest",
    "render_console",
]
