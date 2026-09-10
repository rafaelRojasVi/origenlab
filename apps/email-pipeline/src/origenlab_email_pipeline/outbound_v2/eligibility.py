"""Pure recipient eligibility for a Slice 0 campaign audience.

The single policy site for "may this address be contacted by this campaign, right now".
It replaces the split between ``candidate_export_gate`` (lead/archive exports) and
``outbound_campaign_gate`` (SQLite sidecar) once the lanes move onto Slice 0; until then
both live side by side and this module has no callers.

Four properties the implementation guarantees, and the tests pin:

- **Pure and deterministic** — no database handle, no I/O, and ``now`` is an argument.
  The same inputs always produce the same verdict, which is what makes an audience
  reproducible from its frozen snapshot.
- **Complete** — evaluation never stops at the first failure. An operator who clears one
  reason must not then discover a second (``REASON_INVALID_ADDRESS`` is the one terminal
  reason: with no mailbox there is nothing else to evaluate).
- **Closed vocabulary** — every reason is a member of ``EXCLUSION_VOCABULARY``, so a
  verdict can always be written to ``outbound.campaign_recipient.exclusion_reasons``.
- **Fail-closed** — an absent fact is an exclusion with a reason, never a pass. A campaign
  that requires a promoted contact point excludes candidates that lack one rather than
  guessing an address is reachable.

This function decides *audience membership only*. It never reads ``outbound.send_control``:
the kill switches are a send-time predicate (Slice 5), not an audience fact, and a frozen
audience is a ceiling rather than a permission to send.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from origenlab_email_pipeline.marketing_contact_noise import (
    marketing_outreach_noise_email,
    marketing_outreach_noise_organization_guess,
)
from origenlab_email_pipeline.marketing_supplier_domains import is_supplier_email_domain
from origenlab_email_pipeline.outbound_v2.reasons import (
    OVERRIDABLE_REASONS,
    REASON_ALREADY_IN_AUDIENCE,
    REASON_BLOCK,
    REASON_BLOCK_DOMAIN,
    REASON_COOLDOWN,
    REASON_INVALID_ADDRESS,
    REASON_MANUAL_HOLD,
    REASON_MANUAL_INACTIVE,
    REASON_POLICY_INTERNAL_DOMAIN,
    REASON_POLICY_NO_CHANNEL,
    REASON_POLICY_NOISE_ADDRESS,
    REASON_POLICY_NOISE_ORGANIZATION,
    REASON_POLICY_SUPPLIER,
    REASON_PRECHECK_BLOCK,
    REASON_PRECHECK_SWITCH,
    REASON_PRIOR_CONTACT,
    REASON_PRIOR_REPLY,
)

#: The shape ``campaign_recipient_address_shape`` and ``send_attempt_address_shape`` enforce
#: (``supabase/migrations/20260908120200_slice0_outbound_address_shape_reject_delimiters.sql``);
#: ``tests/outbound_v2/test_address_shape.py`` keeps the two identical. Extracting one mailbox
#: from a raw contact string stays with the ingest adapter (``candidate_export_gate``'s
#: ``normalize_export_email``); by the time a candidate reaches this module it is expected to
#: carry a single address, and anything else is ``REASON_INVALID_ADDRESS``. ``<``, ``>``, ``,``,
#: ``;`` and ``"`` are excluded because they only ever appear in a display name or a joined
#: recipient list — an unextracted header fragment must not pass for a mailbox.
ADDRESS_SHAPE_PATTERN = r'^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$'
_ADDRESS_RE = re.compile(ADDRESS_SHAPE_PATTERN)

#: The fixed evaluation order. ``reasons[0]`` is the first rule that fired, which is what a
#: single-reason report (a CSV column, an operator list) shows.
_EVALUATION_ORDER: tuple[str, ...] = (
    REASON_POLICY_INTERNAL_DOMAIN,
    REASON_MANUAL_INACTIVE,
    REASON_MANUAL_HOLD,
    REASON_BLOCK,
    REASON_BLOCK_DOMAIN,
    REASON_PRIOR_CONTACT,
    REASON_PRIOR_REPLY,
    REASON_COOLDOWN,
    REASON_PRECHECK_BLOCK,
    REASON_PRECHECK_SWITCH,
    REASON_POLICY_NO_CHANNEL,
    REASON_POLICY_SUPPLIER,
    REASON_POLICY_NOISE_ADDRESS,
    REASON_POLICY_NOISE_ORGANIZATION,
    REASON_ALREADY_IN_AUDIENCE,
)


def normalize_address(raw: str | None) -> str | None:
    """Lower-case ``raw`` and return it only if it satisfies the Slice 0 address shape."""
    value = (raw or "").strip().lower()
    if not value or not _ADDRESS_RE.match(value):
        return None
    return value


@dataclass(frozen=True)
class RecipientCandidate:
    """One proposed recipient, already resolved by the lane adapter.

    ``person_id`` drives the per-person dedup in :func:`build_audience_preview`: frequency
    is a property of a person, not of an address, so two addresses for one person are one
    contact opportunity.
    """

    address_norm: str
    person_id: str | None = None
    organization_id: str | None = None
    contact_point_id: str | None = None
    organization_name: str | None = None
    source_lane: str = "unknown"
    source_ref: str | None = None
    #: Verdict of the archive commercial precheck, when the lane runs one: ``'block'`` or
    #: ``'switch'`` (the contact moved on and another address should be used).
    precheck_verdict: str | None = None


@dataclass(frozen=True)
class ContactControlIndex:
    """``outbound.contact_control`` and the manual sidecar, indexed once per freeze.

    Built once for a whole audience so evaluation stays pure and O(1) per candidate; never
    queried row by row.
    """

    blocked_addresses: frozenset[str] = frozenset()
    blocked_domains: frozenset[str] = frozenset()
    prior_contact_addresses: frozenset[str] = frozenset()
    replied_addresses: frozenset[str] = frozenset()
    #: address → the instant its cooldown expires.
    cooldown_until: Mapping[str, datetime] = field(default_factory=dict)
    #: address → ``'active' | 'inactive' | 'hold'``. ``'active'`` is informational and is
    #: never marketing consent; only ``'inactive'`` and ``'hold'`` exclude.
    manual_status: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignPolicy:
    """The campaign's own knobs — the parts of ``outbound.campaign`` eligibility reads."""

    max_sends: int
    recontact_interval_days: int
    policy_include_suppliers: bool = False
    internal_domains: frozenset[str] = frozenset()
    supplier_domains: frozenset[str] = frozenset()
    skip_noise_filter: bool = False
    strict_contact_graph_noise: bool = False
    #: Fail-closed: when true, a candidate with no promoted ``crm.contact_point`` is excluded
    #: rather than contacted on an unverified address.
    require_contact_point: bool = False


@dataclass(frozen=True)
class RecontactOverride:
    """An operator's explicit decision to contact someone again (WORKFLOWS.md §W12).

    Clears only :data:`OVERRIDABLE_REASONS`; the triple is written to
    ``campaign_recipient.recontact_override_*`` and is immutable from then on.
    """

    operator_id: str
    reason: str


@dataclass(frozen=True)
class EligibilityVerdict:
    """A complete, explainable verdict for one candidate at one instant."""

    eligible: bool
    evaluated_at: datetime
    #: Every rule that still excludes this candidate, in the fixed evaluation order.
    #: Empty if and only if ``eligible``.
    reasons: tuple[str, ...]
    #: Reasons an override cleared. Recorded so the snapshot still shows what was waived.
    overridden_reasons: tuple[str, ...] = ()
    override: RecontactOverride | None = None


def evaluate_recipient_eligibility(
    *,
    candidate: RecipientCandidate,
    controls: ContactControlIndex,
    policy: CampaignPolicy,
    now: datetime,
    override: RecontactOverride | None = None,
    already_in_audience: bool = False,
) -> EligibilityVerdict:
    """Evaluate every rule for ``candidate`` and report every reason it fails.

    Args:
        candidate: the proposed recipient, as resolved by the lane adapter.
        controls: contact controls and manual statuses, indexed once for the whole audience.
        policy: the campaign's eligibility knobs.
        now: the evaluation instant. An argument, never a clock read, so a verdict is
            reproducible from its inputs alone.
        override: an operator recontact override, when one exists for this recipient.
        already_in_audience: set by :func:`build_audience_preview` when another address of
            the same person has already taken the campaign's one slot for them.

    Returns:
        An :class:`EligibilityVerdict` whose ``reasons`` are ordered by
        :data:`_EVALUATION_ORDER` and drawn only from ``EXCLUSION_VOCABULARY``.
    """
    address = normalize_address(candidate.address_norm)
    if address is None:
        # Terminal: with no mailbox, no domain, control or noise rule can be evaluated.
        return EligibilityVerdict(
            eligible=False, evaluated_at=now, reasons=(REASON_INVALID_ADDRESS,)
        )

    domain = address.rsplit("@", 1)[-1]
    found: set[str] = set()

    if domain in policy.internal_domains:
        found.add(REASON_POLICY_INTERNAL_DOMAIN)

    manual = controls.manual_status.get(address)
    if manual == "inactive":
        found.add(REASON_MANUAL_INACTIVE)
    elif manual == "hold":
        found.add(REASON_MANUAL_HOLD)

    if address in controls.blocked_addresses:
        found.add(REASON_BLOCK)
    if _domain_or_parent_blocked(domain, controls.blocked_domains):
        found.add(REASON_BLOCK_DOMAIN)

    if address in controls.prior_contact_addresses:
        found.add(REASON_PRIOR_CONTACT)
    if address in controls.replied_addresses:
        found.add(REASON_PRIOR_REPLY)
    cooldown_until = controls.cooldown_until.get(address)
    if cooldown_until is not None and now < cooldown_until:
        found.add(REASON_COOLDOWN)

    if candidate.precheck_verdict == "block":
        found.add(REASON_PRECHECK_BLOCK)
    elif candidate.precheck_verdict == "switch":
        found.add(REASON_PRECHECK_SWITCH)

    if policy.require_contact_point and not candidate.contact_point_id:
        found.add(REASON_POLICY_NO_CHANNEL)

    if not policy.policy_include_suppliers and policy.supplier_domains:
        if is_supplier_email_domain(address, policy.supplier_domains):
            found.add(REASON_POLICY_SUPPLIER)

    if not policy.skip_noise_filter:
        if marketing_outreach_noise_email(
            address, strict_contact_graph=policy.strict_contact_graph_noise
        ):
            found.add(REASON_POLICY_NOISE_ADDRESS)
        if marketing_outreach_noise_organization_guess(candidate.organization_name or ""):
            found.add(REASON_POLICY_NOISE_ORGANIZATION)

    if already_in_audience:
        found.add(REASON_ALREADY_IN_AUDIENCE)

    overridden: tuple[str, ...] = ()
    if override is not None:
        overridden = tuple(r for r in _EVALUATION_ORDER if r in found & OVERRIDABLE_REASONS)
        found -= OVERRIDABLE_REASONS

    reasons = tuple(r for r in _EVALUATION_ORDER if r in found)
    return EligibilityVerdict(
        eligible=not reasons,
        evaluated_at=now,
        reasons=reasons,
        overridden_reasons=overridden,
        override=override if overridden else None,
    )


def _domain_or_parent_blocked(domain: str, blocked: frozenset[str]) -> bool:
    """True when ``domain`` is blocked outright or sits under a blocked registrable domain."""
    if not domain or not blocked:
        return False
    if domain in blocked:
        return True
    return any(domain.endswith("." + b) for b in blocked if b)
