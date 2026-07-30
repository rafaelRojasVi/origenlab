"""Deterministic account-link route classification (no fuzzy / LLM matching)."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY,
    REASON_CONSUMER_EMAIL_LINK_WITHHELD,
    REASON_INTERNAL_DOMAIN_IGNORED_FOR_ACCOUNT_IDENTITY,
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
    auxiliary_reason_codes: tuple[str, ...] = field(default_factory=tuple)


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


def _classify_refused_domain(candidate: str) -> str | None:
    if is_marketplace_domain(candidate):
        return REASON_MARKETPLACE_DOMAIN_IGNORED
    if is_internal_domain(candidate):
        return REASON_INTERNAL_DOMAIN_REFUSED
    if is_consumer_domain(candidate):
        return REASON_CONSUMER_EMAIL_LINK_WITHHELD
    return None


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

    Refused consumer/internal/marketplace domains are ignored as institutional
    evidence and recorded as auxiliary reasons; they do not block an independently
    valid exact institutional domain or compatible exact-name match.

    Route H is returned only when no stronger independent institutional evidence
    remains. Route D (unique_compatible_name) is removed — alias∪canonical is
    evaluated as one candidate set before B/C.
    """
    name = (buyer_name_norm or "").strip().lower() or None
    raw_dom = (buyer_domain or "").strip().lower() or None
    raw_edom = (email_domain or "").strip().lower() or None
    aux: list[str] = []

    # Institutional buyer domain only (already sanitized upstream, but defend).
    dom: str | None = None
    if raw_dom:
        refused = _classify_refused_domain(raw_dom)
        if refused:
            if refused == REASON_MARKETPLACE_DOMAIN_IGNORED:
                aux.append(REASON_MARKETPLACE_DOMAIN_IGNORED)
            elif refused == REASON_INTERNAL_DOMAIN_REFUSED:
                aux.append(REASON_INTERNAL_DOMAIN_IGNORED_FOR_ACCOUNT_IDENTITY)
            else:
                aux.append(REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY)
        elif is_institutional_domain(raw_dom):
            dom = raw_dom

    # Contact email domain: institutional only for linking; refused → auxiliary.
    edom: str | None = None
    if raw_edom:
        refused = _classify_refused_domain(raw_edom)
        if refused:
            if refused == REASON_MARKETPLACE_DOMAIN_IGNORED:
                aux.append(REASON_MARKETPLACE_DOMAIN_IGNORED)
            elif refused == REASON_INTERNAL_DOMAIN_REFUSED:
                aux.append(REASON_INTERNAL_DOMAIN_IGNORED_FOR_ACCOUNT_IDENTITY)
            else:
                aux.append(REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY)
        elif is_institutional_domain(raw_edom):
            edom = raw_edom

    aux_t = tuple(sorted(set(aux)))

    alias_hits = sorted(index.alias_to_accounts.get(name, set())) if name else []
    canon_hits = sorted(index.name_to_accounts.get(name, set())) if name else []
    name_union = sorted(set(alias_hits) | set(canon_hits))

    domain_hits: list[str] = []
    if dom:
        domain_hits = sorted(index.domain_to_accounts.get(dom, set()))

    email_domain_hits: list[str] = []
    if edom:
        email_domain_hits = sorted(index.domain_to_accounts.get(edom, set()))

    # I — name vs institutional domain disagreement.
    if name_union and domain_hits and set(name_union).isdisjoint(set(domain_hits)):
        return LinkRouteResult(
            route=ROUTE_NAME_DOMAIN_CONFLICT,
            confidence=CONFIDENCE_NONE,
            reason_code=REASON_BUYER_DOMAIN_CONFLICTS_WITH_NAME,
            auto_link_allowed=False,
            account_ids=tuple(sorted(set(name_union) | set(domain_hits))),
            notes="buyer name and institutional domain resolve to different accounts",
            auxiliary_reason_codes=aux_t,
        )

    # A — exact institutional domain.
    if domain_hits:
        if len(domain_hits) > 1:
            return LinkRouteResult(
                route=ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_NAME_AMBIGUOUS,
                auto_link_allowed=False,
                account_ids=tuple(domain_hits),
                notes="institutional domain maps to multiple accounts",
                auxiliary_reason_codes=aux_t,
            )
        return LinkRouteResult(
            route=ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
            confidence=CONFIDENCE_HIGH,
            reason_code="exact_institutional_domain",
            auto_link_allowed=True,
            account_ids=(domain_hits[0],),
            notes="exact institutional domain with compatible name evidence",
            auxiliary_reason_codes=aux_t,
        )

    # E — explicit contact email institutional domain uniquely maps.
    if email_domain_hits:
        if name_union and set(name_union).isdisjoint(set(email_domain_hits)):
            return LinkRouteResult(
                route=ROUTE_NAME_DOMAIN_CONFLICT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_DOMAIN_CONFLICTS_WITH_NAME,
                auto_link_allowed=False,
                account_ids=tuple(sorted(set(name_union) | set(email_domain_hits))),
                notes="email domain conflicts with buyer name accounts",
                auxiliary_reason_codes=aux_t,
            )
        if len(email_domain_hits) > 1:
            return LinkRouteResult(
                route=ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_NAME_AMBIGUOUS,
                auto_link_allowed=False,
                account_ids=tuple(email_domain_hits),
                notes="email institutional domain maps to multiple accounts",
                auxiliary_reason_codes=aux_t,
            )
        return LinkRouteResult(
            route=ROUTE_EXPLICIT_EMAIL_DOMAIN,
            confidence=CONFIDENCE_HIGH,
            reason_code="explicit_email_institutional_domain",
            auto_link_allowed=True,
            account_ids=(email_domain_hits[0],),
            notes="unique institutional domain from buyer contact email",
            auxiliary_reason_codes=aux_t,
        )

    # Name routes: evaluate full alias∪canonical set first.
    if name:
        if len(name_union) > 1:
            return LinkRouteResult(
                route=ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
                confidence=CONFIDENCE_NONE,
                reason_code=REASON_BUYER_NAME_AMBIGUOUS,
                auto_link_allowed=False,
                account_ids=tuple(name_union),
                notes="alias∪canonical name resolves to multiple PR2 accounts",
                auxiliary_reason_codes=aux_t,
            )
        if len(name_union) == 1:
            account_id = name_union[0]
            from_alias = account_id in alias_hits
            from_canon = account_id in canon_hits
            if weak_public_unit_name:
                route = ROUTE_EXACT_ALIAS if from_alias and not from_canon else ROUTE_EXACT_CANONICAL_NAME
                if from_alias and from_canon:
                    route = ROUTE_EXACT_ALIAS
                return LinkRouteResult(
                    route=route,
                    confidence=CONFIDENCE_LOW,
                    reason_code=REASON_WEAK_PUBLIC_UNIT_NAME,
                    auto_link_allowed=False,
                    account_ids=(account_id,),
                    notes="exact name/alias but weak/generic public-unit name → needs review",
                    auxiliary_reason_codes=aux_t,
                )
            if from_alias and not from_canon:
                return LinkRouteResult(
                    route=ROUTE_EXACT_ALIAS,
                    confidence=CONFIDENCE_MEDIUM,
                    reason_code="exact_unique_alias",
                    auto_link_allowed=True,
                    account_ids=(account_id,),
                    notes="exact unique account alias; no competing account in alias∪canonical",
                    auxiliary_reason_codes=aux_t,
                )
            if from_canon:
                # Prefer C when also present as alias (same account).
                if from_alias:
                    return LinkRouteResult(
                        route=ROUTE_EXACT_ALIAS,
                        confidence=CONFIDENCE_MEDIUM,
                        reason_code="exact_unique_alias",
                        auto_link_allowed=True,
                        account_ids=(account_id,),
                        notes="exact unique alias coinciding with canonical name",
                        auxiliary_reason_codes=aux_t,
                    )
                return LinkRouteResult(
                    route=ROUTE_EXACT_CANONICAL_NAME,
                    confidence=CONFIDENCE_MEDIUM,
                    reason_code="exact_unique_canonical_name",
                    auto_link_allowed=True,
                    account_ids=(account_id,),
                    notes="exact unique canonical account name",
                    auxiliary_reason_codes=aux_t,
                )

    # H — only when refused domain was the sole domain-like evidence and no name/domain link.
    refused_only = bool(aux) and not dom and not edom and not name_union
    if refused_only and (raw_dom or raw_edom) and not name:
        primary = aux[0] if aux else REASON_CONSUMER_EMAIL_LINK_WITHHELD
        return LinkRouteResult(
            route=ROUTE_DOMAIN_REFUSED,
            confidence=CONFIDENCE_NONE,
            reason_code=primary,
            auto_link_allowed=False,
            account_ids=(),
            notes="refused domain with no independent institutional name/domain evidence",
            auxiliary_reason_codes=aux_t,
        )
    if refused_only and not name_union and (raw_dom or raw_edom) and name:
        # Name present but unmatched; refused domain does not establish membership → F
        # with auxiliary refusal evidence (not H).
        pass

    if not name and not dom and not edom and not email_norm:
        if aux:
            return LinkRouteResult(
                route=ROUTE_DOMAIN_REFUSED,
                confidence=CONFIDENCE_NONE,
                reason_code=aux[0],
                auto_link_allowed=False,
                account_ids=(),
                notes="only refused domain/email evidence present",
                auxiliary_reason_codes=aux_t,
            )
        return LinkRouteResult(
            route=ROUTE_NO_MATCH,
            confidence=CONFIDENCE_NONE,
            reason_code=REASON_BUYER_CONTACT_MISSING,
            auto_link_allowed=False,
            account_ids=(),
            notes="no buyer name, domain, or contact email",
            auxiliary_reason_codes=aux_t,
        )
    if name and not dom and not edom and not name_union:
        return LinkRouteResult(
            route=ROUTE_NO_MATCH,
            confidence=CONFIDENCE_NONE,
            reason_code=REASON_BUYER_DOMAIN_MISSING if not aux else REASON_BUYER_ACCOUNT_NOT_FOUND,
            auto_link_allowed=False,
            account_ids=(),
            notes="buyer name present but no institutional domain and no unique name match",
            auxiliary_reason_codes=aux_t,
        )
    return LinkRouteResult(
        route=ROUTE_NO_MATCH,
        confidence=CONFIDENCE_NONE,
        reason_code=REASON_BUYER_ACCOUNT_NOT_FOUND,
        auto_link_allowed=False,
        account_ids=(),
        notes="no compatible PR2 account",
        auxiliary_reason_codes=aux_t,
    )


__all__ = [
    "AccountIndex",
    "LinkRouteResult",
    "build_account_index",
    "classify_account_link_route",
]
