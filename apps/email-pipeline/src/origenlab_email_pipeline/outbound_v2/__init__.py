"""Slice 0 outbound campaign policy: pure evaluation, preview and freeze.

Three pure functions, no callers yet (the three SQLite-era lanes still run unchanged):

- :func:`evaluate_recipient_eligibility` — one candidate, every reason it fails;
- :func:`build_audience_preview` — a whole ranked candidate set, reviewable;
- :func:`freeze_campaign` — a reviewed preview turned into one transaction's writes.

Nothing in this package opens a database connection, reads a clock or sends anything.
"""

from origenlab_email_pipeline.outbound_v2.audience import (
    AudiencePreview,
    PreviewRow,
    build_audience_preview,
)
from origenlab_email_pipeline.outbound_v2.criteria import (
    AUDIENCE_CRITERIA_VERSION,
    AudienceCriteria,
)
from origenlab_email_pipeline.outbound_v2.eligibility import (
    ADDRESS_SHAPE_PATTERN,
    CampaignPolicy,
    ContactControlIndex,
    EligibilityVerdict,
    RecipientCandidate,
    RecontactOverride,
    evaluate_recipient_eligibility,
    normalize_address,
)
from origenlab_email_pipeline.outbound_v2.freeze import (
    CampaignContent,
    CampaignDraft,
    FreezeRefused,
    FrozenCampaignPlan,
    RecipientInsert,
    freeze_campaign,
)
from origenlab_email_pipeline.outbound_v2.reasons import (
    EXCLUSION_VOCABULARY,
    OVERRIDABLE_REASONS,
)

__all__ = [
    "ADDRESS_SHAPE_PATTERN",
    "AUDIENCE_CRITERIA_VERSION",
    "AudienceCriteria",
    "AudiencePreview",
    "CampaignContent",
    "CampaignDraft",
    "CampaignPolicy",
    "ContactControlIndex",
    "EXCLUSION_VOCABULARY",
    "EligibilityVerdict",
    "FreezeRefused",
    "FrozenCampaignPlan",
    "OVERRIDABLE_REASONS",
    "PreviewRow",
    "RecipientCandidate",
    "RecipientInsert",
    "RecontactOverride",
    "build_audience_preview",
    "evaluate_recipient_eligibility",
    "freeze_campaign",
    "normalize_address",
]
