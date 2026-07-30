"""Deterministic account-link route classification (no fuzzy / LLM matching)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.commercial_identity.normalize import (
    is_consumer_domain,
    is_institutional_domain,
    is_internal_domain,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_NONE,
    REASON_BUYER_ACCOUNT_NOT_FOUND,
    REASON_BUYER_CONTACT_MISSING,
    REASON_BUYER_DOMAIN_CONFLICTS_WITH_NAME,
    REASON_BUYER_DOMAIN_MISSING,
    REASON_BUYER_NAME_AMBIGUOUS,
    REASON_CONSUMER_EMAIL_LINK_WITHHELD,
    REASON_INTERNAL_DOMAIN_REFUSED,
    REASON_MARKETPLACE_DOMAIN_IGNORED,
    REASON_WEAK_PUBLIC_UNIT_NAME,
    ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
    ROUTE_DOMAIN_REFUSED,
    ROUTE_EXACT_ALIAS,
    ROUTE_EXACT_CANONICAL_NAME,
    ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
    ROUTE_EXPLICIT_EMAIL_DOMAIN,
    ROUTE_NAME_DOMAIN_CONFLICT,
    ROUTE_NO_MATCH,
    ROUTE_UNIQUE_COMPATIBLE_NAME,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.normalize import (
    is_marketplace_domain,
)


@dataclass(frozen=True)
class AccountIndex:
    """In-memory PR2 account lookup structures (from persisted tables or fixtures)."""

    accounts_by_id: dict[str, dict[str, Any]]
    domain_to_accounts: dict[str, set[str]]
    name_to_accounts: dict[str, set[str]]
    alias_to_accounts: dict[str, set[str]]


@dataclass(frozen=True)
class LinkRouteResult:
    route: str
    confidence: str
    reason_code: str
    auto_link_allowed: bool
    account_ids: tuple[str, ...]
    notes: str


def build_account_index(
    *,
    accounts: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
    domains: list[dict[str, Any]],
) -> AccountIndex:
    accounts_by_id = {str(a["account_id"]): a for a in accounts}
    domain_to_accounts: dict[str, set[str]] = {}
    for d in domains:
        dom = (d.get("domain_norm") or "").strip().lower()
        aid = str(d.get("account_id") or "")
        if not dom or not aid:
            continue
        domain_to_accounts.setdefault(dom, set()).add(aid)
    name_to_accounts: dict[str, set[str]] = {}
    for a in accounts:
        nn = (a.get("canonical_name_norm") or a.get("name_norm") or "").strip().lower()
        aid = str(a.get("account_id") or "")
        if nn and aid:
            name_to_accounts.setdefault(nn, set()).add(aid)
    alias_to_accounts: dict[str, set[str]] = {}
    for al in aliases:
        nn = (al.get("alias_norm") or al.get("name_norm") or "").strip().lower()
        aid = str(al.get("account_id") or "")
        if nn and aid:
            alias_to_accounts.setdefault(nn, set()).add(aid)
    return AccountIndex(
        accounts_by_id=accounts_by_id,
        domain_to_accounts=domain_to_accounts,
        name_to_accounts=name_to_accounts,
        alias_to_accounts=alias_to_accounts,
    )


def classify_account_link_route(
    *,
    index: AccountIndex,
    buyer_name_norm: str | None,
    buyer_domain: str | None,
    email_domain: str | None,
    email_norm: str | None,
    weak_public_unit_name: bool = False,
) -> LinkRouteResult:
    """Classify one procurement buyer against PR2 accounts.

    Precedence (first decisive win):
    H refused domains → I name/domain conflict → A institutional domain →
    E email institutional domain → C exact alias → B exact canonical name →
    D unique compatible name → G ambiguous → F no match.
    """
    name = (buyer_name_norm or "").strip().lower() or None
    dom = (buyer_domain or "").strip().lower() or None
    edom = (email_domain or "").strip().lower() or None

    # H — refuse consumer / internal / marketplace domains as institutional membership.
    for candidate, label in ((dom, "buyer_domain"), (edom, "email_domain")):
        if not candidate:
            continue
        if is_marketplace_domain(candidate):
            return LinkRouteResult(
                route=ROUTE_DOMAIN_REFUSED,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_MARKETPLACE_DOMAIN_IGNORED,
                auto_link_allowed=False,
                account_ids=(),
                notes=f"{label} is marketplace; ignored for institutional membership",
            )
        if is_internal_domain(candidate):
            return LinkRouteResult(
                route=ROUTE_DOMAIN_REFUSED,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_INTERNAL_DOMAIN_REFUSED,
                auto_link_allowed=False,
                account_ids=(),
                notes=f"{label} is internal actor domain",
            )
        if is_consumer_domain(candidate):
            return LinkRouteResult(
                route=ROUTE_DOMAIN_REFUSED,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_CONSUMER_EMAIL_LINK_WITHHELD,
                auto_link_allowed=False,
                account_ids=(),
                notes=f"{label} is consumer; never establishes institutional membership",
            )

    name_hits = sorted(index.name_to_accounts.get(name, set()) | index.alias_to_accounts.get(name, set())) if name else []
    domain_hits: list[str] = []
    if dom and is_institutional_domain(dom):
        domain_hits = sorted(index.domain_to_accounts.get(dom, set()))

    # I — name points to account A, institutional domain points to distinct account B.
    if name_hits and domain_hits and set(name_hits).isdisjoint(set(domain_hits)):
        return LinkRouteResult(
            route=ROUTE_NAME_DOMAIN_CONFLICT,
            confidence=CONFIDENCE_NONE,
            reason_code=REASON_BUYER_DOMAIN_CONFLICTS_WITH_NAME,
            auto_link_allowed=False,
            account_ids=tuple(sorted(set(name_hits) | set(domain_hits))),
            notes="buyer name and institutional domain resolve to different accounts",
        )

    # A — exact institutional domain (compatible with name if present).
    if domain_hits:
        if len(domain_hits) > 1:
            return LinkRouteResult(
                route=ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_NAME_AMBIGUOUS,
                auto_link_allowed=False,
                account_ids=tuple(domain_hits),
                notes="institutional domain maps to multiple accounts",
            )
        if name_hits and domain_hits[0] not in name_hits:
            # Should have been caught by I; keep defensive.
            return LinkRouteResult(
                route=ROUTE_NAME_DOMAIN_CONFLICT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_DOMAIN_CONFLICTS_WITH_NAME,
                auto_link_allowed=False,
                account_ids=tuple(sorted(set(name_hits) | set(domain_hits))),
                notes="domain/name conflict",
            )
        return LinkRouteResult(
            route=ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
            confidence=CONFIDENCE_HIGH,
            reason_code="exact_institutional_domain",
            auto_link_allowed=True,
            account_ids=(domain_hits[0],),
            notes="exact institutional domain with compatible name evidence",
        )

    # E — explicit contact email institutional domain uniquely maps.
    if edom and is_institutional_domain(edom):
        e_hits = sorted(index.domain_to_accounts.get(edom, set()))
        if len(e_hits) == 1:
            if name_hits and e_hits[0] not in name_hits:
                return LinkRouteResult(
                    route=ROUTE_NAME_DOMAIN_CONFLICT,
                    confidence=CONFIDENCE_NONE,
                    reason_code=REASON_BUYER_DOMAIN_CONFLICTS_WITH_NAME,
                    auto_link_allowed=False,
                    account_ids=tuple(sorted(set(name_hits) | set(e_hits))),
                    notes="email domain conflicts with buyer name accounts",
                )
            return LinkRouteResult(
                route=ROUTE_EXPLICIT_EMAIL_DOMAIN,
                confidence=CONFIDENCE_HIGH,
                reason_code="explicit_email_institutional_domain",
                auto_link_allowed=True,
                account_ids=(e_hits[0],),
                notes="unique institutional domain from buyer contact email",
            )
        if len(e_hits) > 1:
            return LinkRouteResult(
                route=ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_NAME_AMBIGUOUS,
                auto_link_allowed=False,
                account_ids=tuple(e_hits),
                notes="email institutional domain maps to multiple accounts",
            )

    # C / B — exact alias then exact canonical name.
    if name:
        alias_hits = sorted(index.alias_to_accounts.get(name, set()))
        canon_hits = sorted(index.name_to_accounts.get(name, set()))
        if len(alias_hits) == 1 and (not canon_hits or alias_hits[0] in canon_hits or set(alias_hits) == set(canon_hits)):
            if weak_public_unit_name:
                return LinkRouteResult(
                    route=ROUTE_EXACT_ALIAS,
                    confidence=CONFIDENCE_LOW,
                    reason_code=REASON_WEAK_PUBLIC_UNIT_NAME,
                    auto_link_allowed=False,
                    account_ids=(alias_hits[0],),
                    notes="exact alias but weak/generic public-unit name → needs review",
                )
            return LinkRouteResult(
                route=ROUTE_EXACT_ALIAS,
                confidence=CONFIDENCE_MEDIUM,
                reason_code="exact_unique_alias",
                auto_link_allowed=True,
                account_ids=(alias_hits[0],),
                notes="exact unique account alias; no competing domain conflict",
            )
        if len(canon_hits) == 1:
            if weak_public_unit_name:
                return LinkRouteResult(
                    route=ROUTE_EXACT_CANONICAL_NAME,
                    confidence=CONFIDENCE_LOW,
                    reason_code=REASON_WEAK_PUBLIC_UNIT_NAME,
                    auto_link_allowed=False,
                    account_ids=(canon_hits[0],),
                    notes="exact canonical name but weak/generic → needs review",
                )
            return LinkRouteResult(
                route=ROUTE_EXACT_CANONICAL_NAME,
                confidence=CONFIDENCE_MEDIUM,
                reason_code="exact_unique_canonical_name",
                auto_link_allowed=True,
                account_ids=(canon_hits[0],),
                notes="exact unique canonical account name",
            )
        # D — unique compatible across alias∪name
        union = sorted(set(alias_hits) | set(canon_hits))
        if len(union) == 1:
            if weak_public_unit_name:
                return LinkRouteResult(
                    route=ROUTE_UNIQUE_COMPATIBLE_NAME,
                    confidence=CONFIDENCE_LOW,
                    reason_code=REASON_WEAK_PUBLIC_UNIT_NAME,
                    auto_link_allowed=False,
                    account_ids=(union[0],),
                    notes="unique compatible name but weak/generic → needs review",
                )
            return LinkRouteResult(
                route=ROUTE_UNIQUE_COMPATIBLE_NAME,
                confidence=CONFIDENCE_MEDIUM,
                reason_code="unique_compatible_buyer_name",
                auto_link_allowed=True,
                account_ids=(union[0],),
                notes="exactly one compatible account for normalized buyer name",
            )
        if len(union) > 1:
            return LinkRouteResult(
                route=ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_NAME_AMBIGUOUS,
                auto_link_allowed=False,
                account_ids=tuple(union),
                notes="multiple PR2 accounts share this normalized buyer name",
            )

    # F — no match / missing contact enrichment cues.
    if not name and not dom and not email_norm:
        return LinkRouteResult(
            route=ROUTE_NO_MATCH,
            confidence=CONFIDENCE_NONE,
            reason_code=REASON_BUYER_CONTACT_MISSING,
            auto_link_allowed=False,
            account_ids=(),
            notes="no buyer name, domain, or contact email",
        )
    if name and not dom:
        return LinkRouteResult(
            route=ROUTE_NO_MATCH,
            confidence=CONFIDENCE_NONE,
            reason_code=REASON_BUYER_DOMAIN_MISSING,
            auto_link_allowed=False,
            account_ids=(),
            notes="buyer name present but no institutional domain and no unique name match",
        )
    return LinkRouteResult(
        route=ROUTE_NO_MATCH,
        confidence=CONFIDENCE_NONE,
        reason_code=REASON_BUYER_ACCOUNT_NOT_FOUND,
        auto_link_allowed=False,
        account_ids=(),
        notes="no compatible PR2 account",
    )


__all__ = [
    "AccountIndex",
    "LinkRouteResult",
    "build_account_index",
    "classify_account_link_route",
]
