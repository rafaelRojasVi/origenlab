"""Pure audience preview: evaluate a whole candidate set without writing anything.

This is the "build audience" step of the campaign lifecycle. It runs while the campaign is
still ``draft``, produces a per-row verdict for the operator to review, and is the input
:func:`freeze_campaign` turns into rows. It touches no database and no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from origenlab_email_pipeline.outbound_v2.criteria import AudienceCriteria
from origenlab_email_pipeline.outbound_v2.eligibility import (
    CampaignPolicy,
    ContactControlIndex,
    EligibilityVerdict,
    RecipientCandidate,
    RecontactOverride,
    evaluate_recipient_eligibility,
    normalize_address,
)


@dataclass(frozen=True)
class PreviewRow:
    """One candidate and its verdict, in the order the caller ranked it."""

    candidate: RecipientCandidate
    verdict: EligibilityVerdict


@dataclass(frozen=True)
class AudiencePreview:
    """The reviewable result of an audience build. Nothing here has been written."""

    rows: tuple[PreviewRow, ...]
    criteria_fingerprint: str
    eligible_count: int
    excluded_count: int
    #: Reason code → how many rows carry it. A row with three reasons counts in all three,
    #: so the values do not sum to ``excluded_count``.
    counts_by_reason: Mapping[str, int] = field(default_factory=dict)
    #: True when the eligible set is larger than the campaign's ``max_sends`` budget.
    exceeds_max_sends: bool = False
    #: Duplicate appearances of an address already present in the preview, dropped rather
    #: than excluded — the same address is one row, not two.
    duplicate_addresses_dropped: int = 0


def build_audience_preview(
    *,
    candidates: Iterable[RecipientCandidate],
    criteria: AudienceCriteria,
    controls: ContactControlIndex,
    policy: CampaignPolicy,
    now: datetime,
    overrides: Mapping[str, RecontactOverride] | None = None,
) -> AudiencePreview:
    """Evaluate every candidate and return the reviewable audience. Writes nothing.

    Ordering is the caller's: candidates are evaluated in the order given (the lane already
    ranks them) and rows come back in that order, which makes the per-person tie-break
    below deterministic and explainable — the highest-ranked address wins the person's slot.

    Two structural rules the database also enforces or implies:

    - **one row per address** — ``(campaign_id, address_norm)`` is unique, so a repeated
      address is dropped, not excluded twice;
    - **one destination per person** — a second *eligible* address for a person already in
      the audience is excluded with ``already_in_audience``, so nobody is contacted twice
      in one campaign. Rows that were excluded for other reasons never take the slot.

    Args:
        candidates: the ranked candidate set.
        criteria: the criteria this preview was built from; its fingerprint travels with
            the preview so a freeze can prove the two agree.
        controls: contact controls and manual statuses.
        policy: the campaign's eligibility knobs, including ``max_sends``.
        now: the evaluation instant, passed through to every verdict.
        overrides: address → operator recontact override, when any exist.

    Returns:
        An :class:`AudiencePreview`. ``exceeds_max_sends`` reports a budget overflow rather
        than silently truncating — trimming an audience is an operator decision.
    """
    overrides = overrides or {}
    rows: list[PreviewRow] = []
    counts: dict[str, int] = {}
    seen_addresses: set[str] = set()
    persons_taken: set[str] = set()
    duplicates = 0
    eligible = 0

    for candidate in candidates:
        address = normalize_address(candidate.address_norm)
        if address is not None:
            if address in seen_addresses:
                duplicates += 1
                continue
            seen_addresses.add(address)

        person = candidate.person_id
        verdict = evaluate_recipient_eligibility(
            candidate=candidate,
            controls=controls,
            policy=policy,
            now=now,
            override=overrides.get(address) if address else None,
            already_in_audience=bool(person) and person in persons_taken,
        )
        if verdict.eligible:
            eligible += 1
            if person:
                persons_taken.add(person)
        for reason in verdict.reasons:
            counts[reason] = counts.get(reason, 0) + 1
        rows.append(PreviewRow(candidate=candidate, verdict=verdict))

    return AudiencePreview(
        rows=tuple(rows),
        criteria_fingerprint=criteria.fingerprint(),
        eligible_count=eligible,
        excluded_count=len(rows) - eligible,
        counts_by_reason=dict(sorted(counts.items())),
        exceeds_max_sends=eligible > policy.max_sends,
        duplicate_addresses_dropped=duplicates,
    )
