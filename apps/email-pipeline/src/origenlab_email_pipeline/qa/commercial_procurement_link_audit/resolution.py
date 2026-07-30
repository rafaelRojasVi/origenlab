"""Deterministic account-resolution rows (one per verified procurement signal)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    LINKED_ROUTES,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_LINKED,
    RESOLUTION_REFUSED,
    RESOLUTION_UNLINKED,
    ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
    ROUTE_DOMAIN_REFUSED,
    ROUTE_NAME_DOMAIN_CONFLICT,
    ROUTE_NO_MATCH,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.link_routes import (
    LinkRouteResult,
)


@dataclass(frozen=True)
class AccountResolution:
    resolution_id: str
    procurement_id: str
    resolution_status: str
    account_id: str | None
    link_route: str
    confidence: str
    reason_code: str
    auto_link_allowed: bool
    review_status: str
    candidate_account_ids: tuple[str, ...]
    auxiliary_reason_codes: tuple[str, ...]
    notes: str

    def to_row(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "procurement_id": self.procurement_id,
            "resolution_status": self.resolution_status,
            "account_id": self.account_id,
            "link_route": self.link_route,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "auto_link_allowed": int(self.auto_link_allowed),
            "review_status": self.review_status,
            "candidate_account_ids_json": json.dumps(
                list(self.candidate_account_ids), separators=(",", ":")
            ),
            "auxiliary_reason_codes": "|".join(self.auxiliary_reason_codes),
            "notes": self.notes,
        }


def _resolution_id(procurement_id: str, route: str, reason: str, status: str) -> str:
    material = f"v1|procurement-resolution|{procurement_id}|{route}|{reason}|{status}"
    return "r_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def resolution_status_for_route(result: LinkRouteResult) -> str:
    if result.auto_link_allowed and result.route in LINKED_ROUTES and len(result.account_ids) == 1:
        return RESOLUTION_LINKED
    if result.route == ROUTE_AMBIGUOUS_MULTI_ACCOUNT:
        return RESOLUTION_AMBIGUOUS
    if result.route == ROUTE_DOMAIN_REFUSED:
        return RESOLUTION_REFUSED
    if result.route in {ROUTE_NO_MATCH, ROUTE_NAME_DOMAIN_CONFLICT}:
        if result.route == ROUTE_NAME_DOMAIN_CONFLICT:
            return RESOLUTION_AMBIGUOUS
        return RESOLUTION_UNLINKED
    # Weak name exact match: route A/B/C/E but auto_link_allowed False → unlinked review
    if result.route in LINKED_ROUTES and not result.auto_link_allowed:
        return RESOLUTION_UNLINKED
    return RESOLUTION_UNLINKED


def build_account_resolution(
    *,
    procurement_id: str,
    result: LinkRouteResult,
) -> AccountResolution:
    """Map a link-route result to one deterministic resolution row.

    CHECK semantics (proposed):
    - linked: account_id NOT NULL, auto_link_allowed=1, route in A/B/C/E
    - unlinked / ambiguous / refused: account_id IS NULL, auto_link_allowed=0
    Candidate IDs for ambiguity live in candidate_account_ids_json, never as
    a falsely selected account_id.
    """
    status = resolution_status_for_route(result)
    if status == RESOLUTION_LINKED:
        account_id = result.account_ids[0]
        candidates: tuple[str, ...] = ()
        auto = True
        review = "ok"
    else:
        account_id = None
        candidates = tuple(result.account_ids)
        auto = False
        review = "needs_review"

    return AccountResolution(
        resolution_id=_resolution_id(
            procurement_id, result.route, result.reason_code, status
        ),
        procurement_id=procurement_id,
        resolution_status=status,
        account_id=account_id,
        link_route=result.route,
        confidence=result.confidence,
        reason_code=result.reason_code,
        auto_link_allowed=auto,
        review_status=review,
        candidate_account_ids=candidates,
        auxiliary_reason_codes=result.auxiliary_reason_codes,
        notes=result.notes,
    )


def assert_resolution_invariants(resolution: AccountResolution) -> None:
    if resolution.resolution_status == RESOLUTION_LINKED:
        assert resolution.account_id is not None
        assert resolution.auto_link_allowed is True
        assert resolution.link_route in LINKED_ROUTES
        assert not resolution.candidate_account_ids
    else:
        assert resolution.account_id is None
        assert resolution.auto_link_allowed is False


__all__ = [
    "AccountResolution",
    "assert_resolution_invariants",
    "build_account_resolution",
    "resolution_status_for_route",
]
