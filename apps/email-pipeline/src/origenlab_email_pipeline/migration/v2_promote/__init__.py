"""Evidence → CRM promotion: the conservative identity pass (docs/MIGRATION.md §5 slice 2).

The import (:mod:`..v2_import`) lands historical safety and campaign evidence in
``evidence.*`` and ``outbound.*`` and deliberately writes nothing to ``crm.*``. This module
is the step that follows it: it reads those assertions, applies the operator's approved
conservative identity defaults, and promotes only what the evidence actually establishes.

Module map:

* :mod:`.rules`  — the identity defaults as pure functions. No database, no clock.
* :mod:`.plan`   — the deterministic plan, a pure function of the assertions it reads.
* :mod:`.apply`  — the transactional, idempotent write path, with its audit events.
* :mod:`.report` — the PII-safe aggregate report.

What it never does, in any mode: create a ``crm.person``; attach a contact point to an
organization by email domain; merge two organizations or two people; create a consent,
permission or subscription fact; open Gmail, Drive or any hosted service; or send anything.
"""

from __future__ import annotations

from origenlab_email_pipeline.migration.v2_promote.apply import (
    PromotionResult,
    apply_promotion,
    read_assertions,
)
from origenlab_email_pipeline.migration.v2_promote.plan import (
    AssertionRow,
    PromotionPlan,
    build_plan,
    deterministic_id,
)
from origenlab_email_pipeline.migration.v2_promote.rules import (
    PUBLIC_MAIL_DOMAINS,
    ROLE_LOCAL_PARTS,
    UNKNOWN_ORGANIZATION_KIND,
    AddressDecision,
    PromotionRefused,
    decide_address,
    organization_similarity_key,
)

__all__ = [
    "PUBLIC_MAIL_DOMAINS",
    "ROLE_LOCAL_PARTS",
    "UNKNOWN_ORGANIZATION_KIND",
    "AddressDecision",
    "AssertionRow",
    "PromotionPlan",
    "PromotionRefused",
    "PromotionResult",
    "apply_promotion",
    "build_plan",
    "decide_address",
    "deterministic_id",
    "organization_similarity_key",
    "read_assertions",
]
