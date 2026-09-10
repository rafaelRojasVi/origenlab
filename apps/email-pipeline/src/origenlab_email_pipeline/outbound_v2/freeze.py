"""Pure campaign freeze: turn a reviewed preview into the writes a freeze would make.

``freeze_campaign`` returns *intent*, never a write. The repository applies the whole plan
in one transaction — one UPDATE of ``outbound.campaign`` guarded by optimistic concurrency
on ``version``, one INSERT of the recipient rows — so a freeze is all-or-nothing, exactly
like every other durable command in this repository.

Approval is the immutability boundary: from ``audience_frozen`` onward the audience, the
content fingerprint and every per-row reason are fixed. Editing anything returns the
campaign to ``draft`` and requires a fresh preview and a fresh freeze.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from origenlab_email_pipeline.outbound_v2.audience import AudiencePreview
from origenlab_email_pipeline.outbound_v2.criteria import (
    AUDIENCE_CRITERIA_VERSION,
    AudienceCriteria,
)
from origenlab_email_pipeline.outbound_v2.eligibility import (
    RecipientCandidate,
    normalize_address,
)

#: The status a freeze may be applied from, and the one it produces.
DRAFT_STATUS = "draft"
FROZEN_STATUS = "audience_frozen"


class FreezeRefused(ValueError):
    """A freeze precondition failed. Nothing is written and nothing is partially frozen."""


@dataclass(frozen=True)
class CampaignContent:
    """What the campaign will send. Frozen with the audience and fingerprinted."""

    subject: str
    body_text: str
    body_html: str | None = None

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("campaign content carries a non-blank subject")
        if not self.body_text.strip():
            raise ValueError("campaign content carries a non-blank body_text")
        if self.body_html is not None and not self.body_html.strip():
            raise ValueError("body_html is either absent or non-blank")

    def sha256(self) -> str:
        """The fingerprint stored in ``campaign.content_sha256``.

        Canonical form is ``subject \\0 body_text \\0 body_html`` (an absent HTML part is
        the empty string), so an operator can recompute it from the frozen row alone.
        """
        canonical = "\0".join([self.subject, self.body_text, self.body_html or ""])
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CampaignDraft:
    """The campaign row as it stands before the freeze."""

    campaign_id: str
    status: str
    version: int
    max_sends: int


@dataclass(frozen=True)
class RecipientInsert:
    """One ``outbound.campaign_recipient`` row the freeze would insert."""

    campaign_id: str
    address_norm: str
    state: str
    exclusion_reasons: tuple[str, ...]
    contact_point_id: str | None = None
    person_id: str | None = None
    organization_id: str | None = None
    recontact_override_by_operator_id: str | None = None
    recontact_override_reason: str | None = None
    recontact_override_at: datetime | None = None


@dataclass(frozen=True)
class FrozenCampaignPlan:
    """Everything one freeze transaction writes, and nothing else."""

    campaign_id: str
    #: Column → value for the guarded UPDATE of ``outbound.campaign``.
    campaign_updates: Mapping[str, Any]
    #: The rows to INSERT into ``outbound.campaign_recipient``.
    recipient_rows: tuple[RecipientInsert, ...]
    #: Optimistic-concurrency guard: the UPDATE must match this ``version``.
    expected_version: int
    content_sha256: str
    eligible_count: int
    excluded_count: int


def freeze_campaign(
    *,
    campaign: CampaignDraft,
    preview: AudiencePreview,
    content: CampaignContent,
    criteria: AudienceCriteria,
    operator_id: str,
    now: datetime,
) -> FrozenCampaignPlan:
    """Build the freeze plan for ``campaign`` from a reviewed ``preview``.

    Args:
        campaign: the current campaign row (status, version, budget).
        preview: the reviewed audience, from :func:`build_audience_preview`.
        content: the subject and body being frozen.
        criteria: the criteria the preview was built from.
        operator_id: the operator performing the freeze; recorded on any override triple.
        now: the freeze instant, written to ``content_frozen_at`` and ``audience_frozen_at``.

    Returns:
        A :class:`FrozenCampaignPlan` describing one transaction's writes.

    Raises:
        FreezeRefused: if the campaign is not in ``draft``; if the preview was built from
            different criteria than the ones being frozen; if the eligible set exceeds
            ``max_sends``; if the preview is empty; or if any previewed candidate carries an
            address the frozen row could not hold. A freeze either produces a complete,
            consistent snapshot or it does not happen — a frozen campaign's row count is
            always fully explained by the preview it came from.
    """
    if campaign.status != DRAFT_STATUS:
        raise FreezeRefused(
            f"campaign {campaign.campaign_id} is {campaign.status!r}, not {DRAFT_STATUS!r}: "
            "editing a frozen campaign returns it to draft first"
        )
    if preview.criteria_fingerprint != criteria.fingerprint():
        raise FreezeRefused(
            "the preview was built from different audience criteria than the ones being "
            "frozen; rebuild the audience before freezing"
        )
    if not preview.rows:
        raise FreezeRefused("the audience preview is empty: nothing to freeze")
    if preview.eligible_count > campaign.max_sends:
        raise FreezeRefused(
            f"{preview.eligible_count} eligible recipients exceed max_sends="
            f"{campaign.max_sends}; trimming the audience is an operator decision"
        )

    unusable = [
        r.candidate
        for r in preview.rows
        if normalize_address(r.candidate.address_norm) is None
    ]
    if unusable:
        raise FreezeRefused(
            f"{len(unusable)} candidate(s) carry an unusable address and could not satisfy "
            f"campaign_recipient_address_shape: {_describe(unusable)}; fix or drop them and "
            "rebuild the audience"
        )

    rows: list[RecipientInsert] = []
    for row in preview.rows:
        address = normalize_address(row.candidate.address_norm)
        assert address is not None  # every unusable address refused the freeze above
        verdict = row.verdict
        override = verdict.override
        rows.append(
            RecipientInsert(
                campaign_id=campaign.campaign_id,
                address_norm=address,
                state="snapshotted" if verdict.eligible else "excluded",
                # Sorted and de-duplicated here: the CHECK constraint cannot express it
                # without a subquery, so canonical order is the application's invariant.
                exclusion_reasons=tuple(sorted(set(verdict.reasons))),
                contact_point_id=row.candidate.contact_point_id,
                person_id=row.candidate.person_id,
                organization_id=row.candidate.organization_id,
                recontact_override_by_operator_id=operator_id if override else None,
                recontact_override_reason=override.reason if override else None,
                recontact_override_at=now if override else None,
            )
        )

    campaign_updates: dict[str, Any] = {
        "status": FROZEN_STATUS,
        "subject": content.subject,
        "body_text": content.body_text,
        "body_html": content.body_html,
        "content_sha256": content.sha256(),
        "content_frozen_at": now,
        "audience_criteria": criteria.to_json(),
        "audience_criteria_version": AUDIENCE_CRITERIA_VERSION,
        "audience_frozen_at": now,
        "version": campaign.version + 1,
        "updated_at": now,
    }

    return FrozenCampaignPlan(
        campaign_id=campaign.campaign_id,
        campaign_updates=campaign_updates,
        recipient_rows=tuple(rows),
        expected_version=campaign.version,
        content_sha256=content.sha256(),
        eligible_count=sum(1 for r in rows if r.state == "snapshotted"),
        excluded_count=sum(1 for r in rows if r.state == "excluded"),
    )


#: How many unusable candidates a refusal names before it summarises the rest.
_MAX_NAMED_CANDIDATES = 10


def _describe(candidates: list[RecipientCandidate]) -> str:
    """Name the candidates a refusal is about: their ``source_ref``, else the raw address."""
    named = [c.source_ref or repr(c.address_norm) for c in candidates[:_MAX_NAMED_CANDIDATES]]
    rest = len(candidates) - len(named)
    return ", ".join(named) + (f" and {rest} more" if rest else "")
