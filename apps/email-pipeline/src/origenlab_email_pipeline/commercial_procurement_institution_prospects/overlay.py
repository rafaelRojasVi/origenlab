"""Contact-gap overlay and three independent commercial axes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    HISTORICAL_LIFECYCLE,
    OPEN_LIFECYCLE,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.identity import (
    InstitutionIdentity,
)


@dataclass(frozen=True)
class AxisScore:
    axis: str
    band: str
    score: int
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "band": self.band,
            "score": self.score,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class ContactOverlay:
    account_resolution_status: str
    account_resolution_source: str | None
    account_resolution_reason: str | None
    linked_account_present: bool
    contact_resolution_status: str
    known_contact_count: int
    suitable_contact_count: int
    verified_contact_count: int
    blocked_or_safety_unknown_count: int
    selected_contact_present: bool
    contact_gap_status: str
    contact_next_action: str
    selected_contact_id: str | None
    contact_authorization: bool
    outreach_authorization: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_resolution_status": self.account_resolution_status,
            "account_resolution_source": self.account_resolution_source,
            "account_resolution_reason": self.account_resolution_reason,
            "linked_account_present": self.linked_account_present,
            "contact_resolution_status": self.contact_resolution_status,
            "known_contact_count": self.known_contact_count,
            "suitable_contact_count": self.suitable_contact_count,
            "verified_contact_count": self.verified_contact_count,
            "blocked_or_safety_unknown_count": self.blocked_or_safety_unknown_count,
            "selected_contact_present": self.selected_contact_present,
            "contact_gap_status": self.contact_gap_status,
            "contact_next_action": self.contact_next_action,
            "selected_contact_id": self.selected_contact_id,
            "contact_authorization": False,
            "outreach_authorization": False,
        }


def _band(score: int) -> str:
    if score <= 0:
        return "none"
    if score <= 2:
        return "low"
    if score <= 5:
        return "medium"
    return "high"


def build_contact_overlay(
    *,
    identity: InstitutionIdentity,
    org: OrganizationResolution | None,
    summary: ContactResolutionSummary | None,
    candidates: list[ContactCandidate],
) -> ContactOverlay:
    status = identity.account_resolution_status
    linked = identity.linked_account_present

    known = summary.considered_contact_count if summary else 0
    suitable = summary.suitable_contact_count if summary else 0
    blocked = summary.blocked_contact_count if summary else 0
    selected = bool(summary and summary.selected_contact_id)
    contact_status = (
        summary.final_contact_status
        if summary
        else (
            "contact_resolution_deferred"
            if not linked
            else "no_contact_found"
        )
    )
    next_action = summary.next_action if summary else (
        "resolve_account" if not linked else "research_new_contact"
    )

    verified = sum(
        1
        for c in candidates
        if c.verification_status in {"verified", "human_verified", "confirmed"}
        or (c.identity_status == "verified" and c.has_usable_email)
    )
    safety_unknown = sum(1 for c in candidates if c.safety_unknown or c.safety_blocked)

    if status in {"ambiguous"}:
        gap = "account_ambiguous"
    elif not linked:
        gap = "account_unlinked"
    elif selected and verified > 0:
        gap = "existing_verified_contact"
    elif known > 0 and suitable > 0 and verified == 0:
        gap = "existing_contact_needs_role_review"
    elif known > 0 and suitable == 0 and any(
        c.role_suitability not in {"unknown", "unsuitable", ""} and not c.has_usable_email
        for c in candidates
    ):
        gap = "role_known_email_missing"
    elif known > 0 and (blocked > 0 or safety_unknown > 0) and suitable == 0:
        gap = "contacts_present_but_blocked"
    elif linked and known == 0:
        gap = "linked_account_no_contact"
    elif linked and known > 0 and not selected:
        gap = "existing_contact_needs_role_review"
    else:
        gap = "linked_account_no_contact"

    return ContactOverlay(
        account_resolution_status=status,
        account_resolution_source=identity.account_resolution_source
        or (org.resolution_source if org else None),
        account_resolution_reason=identity.account_resolution_reason
        or (org.reason_code if org else None),
        linked_account_present=linked,
        contact_resolution_status=contact_status,
        known_contact_count=known,
        suitable_contact_count=suitable,
        verified_contact_count=verified,
        blocked_or_safety_unknown_count=max(blocked, safety_unknown),
        selected_contact_present=selected,
        contact_gap_status=gap,
        contact_next_action=next_action,
        selected_contact_id=summary.selected_contact_id if summary else None,
        contact_authorization=False,
        outreach_authorization=False,
    )


def score_prospect_strength(
    *,
    purchase_tender_count: int,
    repeated_category_count: int,
    open_purchase_count: int,
    historical_purchase_count: int,
    recent_signal: bool,
) -> AxisScore:
    score = 0
    reasons: list[str] = []
    if purchase_tender_count > 0:
        score += 2
        reasons.append("has_equipment_purchase_evidence")
    if purchase_tender_count >= 2:
        score += 2
        reasons.append("multiple_purchase_tenders")
    if repeated_category_count > 0:
        score += 2
        reasons.append("repeated_observed_demand")
    if historical_purchase_count > 0:
        score += 1
        reasons.append("historical_buying_intent")
    if open_purchase_count > 0:
        score += 1
        reasons.append("open_purchase_tender_present")
    if recent_signal:
        score += 1
        reasons.append("recent_observation")
    if score == 0:
        reasons.append("no_equipment_purchase_evidence")
    return AxisScore(
        axis="prospect_strength",
        band=_band(score),
        score=score,
        reason_codes=tuple(reasons),
    )


def score_opportunity_urgency(
    *,
    open_tender_count: int,
    closing_soon_count: int,
    current_opportunity_like: int,
) -> AxisScore:
    score = 0
    reasons: list[str] = []
    if open_tender_count <= 0:
        return AxisScore(
            axis="opportunity_urgency",
            band="none",
            score=0,
            reason_codes=("no_open_tender",),
        )
    score += 2
    reasons.append("open_tender_present")
    if current_opportunity_like > 0:
        score += 3
        reasons.append("current_opportunity_like_evidence")
    if closing_soon_count > 0:
        score += 2
        reasons.append("closing_soon")
    return AxisScore(
        axis="opportunity_urgency",
        band=_band(score),
        score=score,
        reason_codes=tuple(reasons),
    )


def score_contact_readiness(overlay: ContactOverlay) -> AxisScore:
    score = 0
    reasons: list[str] = []
    if not overlay.linked_account_present:
        return AxisScore(
            axis="contact_readiness",
            band="none",
            score=0,
            reason_codes=("account_unlinked", overlay.contact_gap_status),
        )
    score += 1
    reasons.append("linked_account")
    if overlay.known_contact_count > 0:
        score += 1
        reasons.append("known_contacts_present")
    if overlay.suitable_contact_count > 0:
        score += 2
        reasons.append("suitable_contacts_present")
    if overlay.verified_contact_count > 0:
        score += 2
        reasons.append("verified_contacts_present")
    if overlay.selected_contact_present:
        score += 1
        reasons.append("selected_contact_present")
    if overlay.contact_gap_status == "linked_account_no_contact":
        reasons.append("linked_account_no_contact")
    return AxisScore(
        axis="contact_readiness",
        band=_band(score),
        score=score,
        reason_codes=tuple(reasons),
    )


def operator_next_action(overlay: ContactOverlay, urgency: AxisScore) -> str:
    if overlay.contact_gap_status == "account_unlinked":
        return "resolve_account"
    if overlay.contact_gap_status == "account_ambiguous":
        return "review_account_identity"
    if overlay.contact_gap_status == "existing_verified_contact":
        return "use_existing_verified_contact"
    if overlay.contact_gap_status in {
        "existing_contact_needs_role_review",
        "role_known_email_missing",
        "contacts_present_but_blocked",
    }:
        return "review_existing_contacts"
    if overlay.contact_gap_status == "linked_account_no_contact":
        return "research_new_contact"
    if urgency.band in {"medium", "high"}:
        return "review_open_opportunity"
    return "monitor_historical_prospect"


def is_open_lifecycle(lifecycle: str) -> bool:
    return lifecycle in OPEN_LIFECYCLE


def is_historical_lifecycle(lifecycle: str) -> bool:
    return lifecycle in HISTORICAL_LIFECYCLE


__all__ = [
    "AxisScore",
    "ContactOverlay",
    "build_contact_overlay",
    "is_historical_lifecycle",
    "is_open_lifecycle",
    "operator_next_action",
    "score_contact_readiness",
    "score_opportunity_urgency",
    "score_prospect_strength",
]
