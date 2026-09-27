"""Slice 0 outbound campaign policy: pure evaluation and preview.

Two pure functions, no callers yet (the three SQLite-era lanes still run unchanged):

- :func:`evaluate_recipient_eligibility` — one candidate, every reason it fails;
- :func:`build_audience_preview` — a whole ranked candidate set, reviewable.

**There is no freeze here.** The one audience freeze is the CRM command
``freeze-campaign-audience`` in ``apps/api`` (``origenlab_api.v2.audience_freeze``), whose
snapshot the database keeps write-once. An earlier pure ``freeze_campaign`` in this package
had no caller and contradicted it (no preheader in the content fingerprint; "editing a frozen
campaign returns it to draft"); it was deleted, and ``test_campaign_safety.py`` refuses its
return.

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
from origenlab_email_pipeline.outbound_v2.reasons import (
    EXCLUSION_VOCABULARY,
    OVERRIDABLE_REASONS,
)

__all__ = [
    "ADDRESS_SHAPE_PATTERN",
    "AUDIENCE_CRITERIA_VERSION",
    "AudienceCriteria",
    "AudiencePreview",
    "CampaignPolicy",
    "ContactControlIndex",
    "EXCLUSION_VOCABULARY",
    "EligibilityVerdict",
    "OVERRIDABLE_REASONS",
    "PreviewRow",
    "RecipientCandidate",
    "RecontactOverride",
    "build_audience_preview",
    "evaluate_recipient_eligibility",
    "normalize_address",
]
