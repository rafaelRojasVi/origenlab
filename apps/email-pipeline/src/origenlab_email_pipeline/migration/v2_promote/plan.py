"""Build the deterministic promotion plan from the evidence already loaded in V2.

The plan is a pure function of the ``evidence.assertion`` rows it reads. Given the same
evidence it produces the same plan, in the same order, with the same identifiers — which is
what makes the second run of the promotion provably a no-op, and what makes the eventual
hosted replay reproduce the local result rather than merely resemble it.

Nothing here writes. :mod:`.apply` does that, inside one transaction.

What a plan can contain
-----------------------

* ``crm.contact_point`` — one per ``contacted_address`` assertion, recording the channel and
  only the channel. Never a ``person_id``, never an ``organization_id``.
* ``crm.organization`` — one per ``organization_name`` assertion whose folded name is unique
  in the evidence.
* ``evidence.assertion`` resolutions — ``promoted`` for the above, ``ambiguous`` (with a
  mandatory note) for everything routed to review.

What a plan can never contain
-----------------------------

* a ``crm.person``. The evidence carries no display name; see :mod:`.rules`.
* an ``affiliation``, an ``organization_relationship`` or an ``opportunity``. Each asserts a
  relationship the evidence does not record.
* a merge of two organizations or two people, under any circumstance.
* a consent, permission or subscription fact of any kind. ``crm.contact_point`` has no
  consent column and gains none: email presence is never marketing permission.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from origenlab_email_pipeline.migration.v2_promote.rules import (
    UNKNOWN_ORGANIZATION_KIND,
    AddressDecision,
    PromotionRefused,
    decide_address,
    organization_similarity_key,
)

#: The UUID namespace promotion mints identifiers in.
#:
#: Deterministic rather than random, so the same evidence yields the same `crm` identifier on
#: every run and in every environment. That is what lets the hosted replay be *reconciled*
#: against the local run by identifier, instead of only by count.
PROMOTION_NAMESPACE: uuid.UUID = uuid.UUID("6f3b1f4a-0d2e-4c7a-9f51-3a1c0b8e77d2")


def deterministic_id(kind: str, key: str) -> str:
    """The identifier a promoted row will always receive for this evidence."""
    return str(uuid.uuid5(PROMOTION_NAMESPACE, f"{kind}:{key}"))


@dataclass(frozen=True)
class PlannedContactPoint:
    assertion_id: str
    contact_point_id: str
    value_norm: str
    usage: str
    reason: str
    is_role_mailbox: bool
    is_public_domain: bool
    looks_personal: bool
    origin_source_record_id: str | None


@dataclass(frozen=True)
class PlannedOrganization:
    assertion_id: str
    organization_id: str
    name: str
    kind: str
    similarity_key: str
    origin_source_record_id: str | None


@dataclass(frozen=True)
class PlannedReview:
    """An assertion routed to an operator instead of promoted."""

    assertion_id: str
    kind: str
    note: str


@dataclass
class PromotionPlan:
    contact_points: list[PlannedContactPoint] = field(default_factory=list)
    organizations: list[PlannedOrganization] = field(default_factory=list)
    reviews: list[PlannedReview] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)

    def to_report(self) -> dict[str, Any]:
        return {
            "contact_points": len(self.contact_points),
            "organizations": len(self.organizations),
            "reviews": len(self.reviews),
            "counts": dict(sorted(self.counts.items())),
        }


@dataclass(frozen=True)
class AssertionRow:
    """One ``evidence.assertion`` row, as the plan needs it."""

    id: str
    kind: str
    value_norm: str
    value: dict[str, Any] | None
    resolution: str
    source_record_id: str | None


def build_plan(assertions: list[AssertionRow]) -> PromotionPlan:
    """Compute the promotion for a set of assertions.

    Only ``unresolved`` assertions are considered. An assertion already ``promoted``,
    ``linked``, ``rejected`` or ``ambiguous`` is left exactly as it is: re-deciding a
    resolution an operator may have set by hand is precisely the kind of silent overwrite
    this migration must not perform.
    """
    plan = PromotionPlan()
    plan.counts["assertions_seen"] = len(assertions)

    pending = [a for a in assertions if a.resolution == "unresolved"]
    plan.counts["assertions_unresolved"] = len(pending)
    plan.counts["assertions_already_resolved"] = len(assertions) - len(pending)

    _plan_addresses([a for a in pending if a.kind == "contacted_address"], plan)
    _plan_organizations([a for a in pending if a.kind == "organization_name"], plan)

    # `supplier_candidate` assertions are deliberately left unresolved. A supplier is an
    # organization *plus a relationship role* (`crm.organization_relationship`), and the
    # evidence records a trade name and a domain — never the relationship. Promoting one
    # would assert a commercial relationship nobody recorded.
    suppliers = [a for a in pending if a.kind == "supplier_candidate"]
    plan.counts["supplier_candidates_left_unresolved"] = len(suppliers)

    other = [
        a
        for a in pending
        if a.kind not in {"contacted_address", "organization_name", "supplier_candidate"}
    ]
    plan.counts["assertions_of_unhandled_kind"] = len(other)
    return plan


def _plan_addresses(assertions: list[AssertionRow], plan: PromotionPlan) -> None:
    # Sorted so the plan — and therefore the insert order and every report — is identical
    # across runs regardless of how the database returned the rows.
    for assertion in sorted(assertions, key=lambda a: (a.value_norm, a.id)):
        try:
            decision: AddressDecision = decide_address(assertion.value_norm)
        except PromotionRefused as exc:
            plan.reviews.append(
                PlannedReview(
                    assertion_id=assertion.id,
                    kind=assertion.kind,
                    note=f"address cannot be classified: {exc}",
                )
            )
            plan.counts["addresses_unclassifiable"] += 1
            continue

        plan.contact_points.append(
            PlannedContactPoint(
                assertion_id=assertion.id,
                contact_point_id=deterministic_id("contact_point", assertion.value_norm),
                value_norm=assertion.value_norm,
                usage=decision.usage,
                reason=decision.reason,
                is_role_mailbox=decision.is_role_mailbox,
                is_public_domain=decision.is_public_domain,
                looks_personal=decision.looks_personal,
                origin_source_record_id=assertion.source_record_id,
            )
        )
        plan.counts[f"contact_points_{decision.usage}"] += 1
        if decision.looks_personal:
            plan.counts["contact_points_personal_shaped_no_person_created"] += 1
        if decision.is_public_domain:
            plan.counts["contact_points_on_public_domain"] += 1


def _plan_organizations(assertions: list[AssertionRow], plan: PromotionPlan) -> None:
    by_key: dict[str, list[AssertionRow]] = defaultdict(list)
    for assertion in assertions:
        observed = _observed_name(assertion)
        try:
            key = organization_similarity_key(observed)
        except PromotionRefused as exc:
            plan.reviews.append(
                PlannedReview(
                    assertion_id=assertion.id,
                    kind=assertion.kind,
                    note=f"organization name cannot be folded to a key: {exc}",
                )
            )
            plan.counts["organizations_unfoldable"] += 1
            continue
        by_key[key].append(assertion)

    for key in sorted(by_key):
        group = sorted(by_key[key], key=lambda a: (a.value_norm, a.id))
        if len(group) > 1:
            # Similar names are never merged automatically, and they are never promoted
            # separately either: promoting them would create near-duplicate organizations
            # that an operator then has to merge. Both members go to review together.
            for assertion in group:
                plan.reviews.append(
                    PlannedReview(
                        assertion_id=assertion.id,
                        kind=assertion.kind,
                        note=(
                            f"{len(group)} observed organization names fold to the same key "
                            f"'{key}'; they are never merged automatically and are not "
                            "promoted separately, so an operator decides whether they are "
                            "one organization or several"
                        ),
                    )
                )
            plan.counts["organizations_in_similar_name_clusters"] += len(group)
            plan.counts["organization_similar_name_clusters"] += 1
            continue

        assertion = group[0]
        observed = _observed_name(assertion)
        plan.organizations.append(
            PlannedOrganization(
                assertion_id=assertion.id,
                organization_id=deterministic_id("organization", key),
                name=observed,
                kind=UNKNOWN_ORGANIZATION_KIND,
                similarity_key=key,
                origin_source_record_id=assertion.source_record_id,
            )
        )
        plan.counts["organizations_promoted"] += 1


def _observed_name(assertion: AssertionRow) -> str:
    """The name as it was actually observed, not the normalized key.

    ``value_norm`` is lower-cased for uniqueness; ``value->observed_name`` is what a human
    typed. The organization is created with what the human typed, because that is the
    evidence — the normalized form is an index, not a fact about the world.
    """
    value = assertion.value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = None
    if isinstance(value, dict):
        observed = value.get("observed_name")
        if isinstance(observed, str) and observed.strip():
            return observed.strip()
    return assertion.value_norm
