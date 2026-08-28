"""Shared infrastructure helpers for `origenlab_email_pipeline`.

This package holds real implementation modules only (`safety`, `step_runner`,
`reports_out`, `research_automation`, plus the `mart` and `outbound`
subpackages). The former facade layer that re-exported root modules
(`core.config`, `core.db`, `core.gmail.*`, `core.leads.*`, `core.suppliers.*`,
…) was removed in the 2026-08 commercial platform reset: implementations live
in, and are imported from, the top-level `origenlab_email_pipeline` modules.
"""

from . import safety as safety

__all__ = ["safety"]
