"""Deterministic identity resolution (pure; no SQLite writes).

Precedence (conservative):
1. Exact normalized email is the strongest automatic contact key.
2. Institutional domains may join contacts to accounts when org evidence is compatible.
3. Consumer/public email domains never establish institutional account membership.
4. Internal domains (origenlab.cl / labdelivery.cl) never become external commercial accounts.
5. Organization names never silently merge solely because they look similar.
6. Ambiguous evidence becomes conflicts / needs_review — never a silent merge.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from origenlab_email_pipeline.commercial_identity.constants import (
    IDENTITY_CONFIDENCE_HIGH,
    IDENTITY_CONFIDENCE_LOW,
    IDENTITY_CONFIDENCE_MEDIUM,
    IDENTITY_CONFIDENCE_NONE,
    IDENTITY_STATUS_AMBIGUOUS,
    IDENTITY_STATUS_INTERNAL_ACTOR,
    IDENTITY_STATUS_NEEDS_REVIEW,
    IDENTITY_STATUS_RESOLVED,
    IDENTITY_STATUS_UNLINKED,
    ORIGIN_LABDELIVERY_ARCHIVE,
    ORIGIN_ORIGENLAB_GMAIL,
    ORIGIN_RESEARCH,
    REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES,
    REASON_COMPATIBLE_ORG_NAME,
    REASON_COMPETING_ACCOUNT_LINKS,
    REASON_CONSUMER_DOMAIN_REFUSED,
    REASON_CONSUMER_ORG_LINK_WITHHELD,
    REASON_DOMAIN_CONFLICTING_ORGS,
    REASON_EMAIL_COMPETING_DOMAINS,
    REASON_EMAIL_CONFLICTING_ORGS,
    REASON_EXACT_EMAIL,
    REASON_INSTITUTIONAL_DOMAIN,
    REASON_INTERNAL_DOMAIN_EXCLUDED,
    REASON_MISSING_EMAIL,
    REASON_MISSING_ORG,
    REASON_ORG_NAME_EVIDENCE,
    REASON_RESEARCH_ONLY,
)
from origenlab_email_pipeline.commercial_identity.ids import (
    stable_account_id_for_domain,
    stable_account_id_for_name,
    stable_conflict_id,
    stable_contact_id,
    stable_evidence_id,
)
from origenlab_email_pipeline.commercial_identity.models import (
    AccountRecord,
    ConflictRecord,
    ContactRecord,
    EvidenceRecord,
    IdentityResolution,
    SourceIdentityRow,
)
from origenlab_email_pipeline.commercial_identity.normalize import (
    domain_from_email,
    email_input_kind,
    is_consumer_domain,
    is_institutional_domain,
    is_internal_domain,
    normalize_domain,
    prefer_display_name,
    safe_org_normalized,
)
from origenlab_email_pipeline.org_normalize import better_canonical_name, normalize_org_name


def _clean_ts(value: str | None) -> str | None:
    """Preserve missing timestamps as None — never substitute build time."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _min_iso(a: str | None, b: str | None) -> str | None:
    vals = [v for v in (_clean_ts(a), _clean_ts(b)) if v]
    return min(vals) if vals else None


def _max_iso(a: str | None, b: str | None) -> str | None:
    vals = [v for v in (_clean_ts(a), _clean_ts(b)) if v]
    return max(vals) if vals else None


def _json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _ptr(row: SourceIdentityRow) -> dict[str, Any]:
    return {
        "source_table": row.source_table,
        "source_record_id": row.source_record_id,
        "source_plane": row.source_plane,
        "origin_plane": row.origin_plane,
        "evidence_at": _clean_ts(row.evidence_at),
    }


def _bump_alias(
    aliases: dict[str, dict[str, Any]],
    *,
    normalized_alias: str,
    alias_name: str,
    evidence_at: str | None,
) -> None:
    entry = aliases.get(normalized_alias)
    if entry is None:
        aliases[normalized_alias] = {
            "alias_name": alias_name,
            "normalized_alias": normalized_alias,
            "evidence_count": 1,
            "first_evidence_at": _clean_ts(evidence_at),
            "last_evidence_at": _clean_ts(evidence_at),
        }
        return
    entry["evidence_count"] = int(entry["evidence_count"]) + 1
    entry["alias_name"] = better_canonical_name(str(entry["alias_name"]), alias_name)
    entry["first_evidence_at"] = _min_iso(entry.get("first_evidence_at"), evidence_at)
    entry["last_evidence_at"] = _max_iso(entry.get("last_evidence_at"), evidence_at)


def resolve_identity(source_rows: list[SourceIdentityRow]) -> IdentityResolution:
    """Resolve accounts/contacts/evidence/conflicts from source assertions."""
    ordered = sorted(
        source_rows,
        key=lambda r: (
            r.source_table,
            r.source_record_id,
            r.email_raw or "",
            r.organization_name or "",
            r.domain_raw or "",
            r.evidence_at or "",
        ),
    )

    # Origin assertion-row tallies (may overlap with other planes for the same person).
    origin_assertion_rows = {
        ORIGIN_ORIGENLAB_GMAIL: 0,
        ORIGIN_LABDELIVERY_ARCHIVE: 0,
        ORIGIN_RESEARCH: 0,
    }

    metrics: dict[str, Any] = {
        "source_identity_rows_inspected": len(ordered),
        "records_without_usable_email": 0,
        "records_without_usable_organization_identity": 0,
        "consumer_domain_auto_link_refusals": 0,
        "label": "synthetic_or_local_fixture",
        "metric_definitions": {
            "consumer_domain_auto_link_refusals": (
                "Count of distinct canonical contacts whose email domain is a consumer/public "
                "domain and whose institutional auto-link was refused. Denominator concept: "
                "canonical_contact_count. Incremented once per such contact during contact "
                "construction (not during source aggregation)."
            ),
            "origenlab_origin_source_assertion_rows": (
                "Count of source assertion rows whose origin_plane is origenlab_gmail. "
                "Overlaps with other origin tallies are possible across different rows."
            ),
            "labdelivery_origin_source_assertion_rows": (
                "Count of source assertion rows whose origin_plane is labdelivery_archive."
            ),
            "research_origin_source_assertion_rows": (
                "Count of source assertion rows whose origin_plane is research."
            ),
            "canonical_contacts_with_origenlab_origin": (
                "Distinct canonical contacts that have at least one origenlab_gmail evidence origin."
            ),
            "canonical_contacts_with_labdelivery_origin": (
                "Distinct canonical contacts that have at least one labdelivery_archive evidence origin."
            ),
            "canonical_contacts_with_research_origin": (
                "Distinct canonical contacts that have at least one research evidence origin."
            ),
            "canonical_contacts_research_only": (
                "Distinct canonical contacts whose only observed origin_plane is research."
            ),
        },
    }

    contact_emails: dict[str, dict[str, Any]] = {}
    org_names_by_email: dict[str, set[str]] = defaultdict(set)
    org_raw_by_email: dict[str, set[str]] = defaultdict(set)
    # Distinguish email-derived domain vs explicit domain_raw evidence.
    email_domain_by_email: dict[str, str] = {}
    explicit_domains_by_email: dict[str, set[str]] = defaultdict(set)
    domain_ptrs_by_email: dict[str, list[dict[str, Any]]] = defaultdict(list)
    origins_by_email: dict[str, set[str]] = defaultdict(set)
    contact_evidence_src: list[tuple[str, SourceIdentityRow, str]] = []
    org_ptrs_by_email: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Domain / name account evidence
    org_names_by_domain: dict[str, set[str]] = defaultdict(set)
    org_raw_by_domain: dict[str, set[str]] = defaultdict(set)
    domain_evidence_at: dict[str, tuple[str | None, str | None]] = {}
    # Per-domain, per-alias provenance (not domain-wide timestamps copied onto every alias).
    alias_obs_by_domain: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    name_only_orgs: dict[str, dict[str, Any]] = {}
    name_only_alias_obs: dict[str, dict[str, Any]] = {}
    account_evidence_src: list[tuple[str, SourceIdentityRow, str, str | None]] = []
    internal_domain_rows: list[SourceIdentityRow] = []

    for row in ordered:
        kind, em = email_input_kind(row.email_raw)
        org_nn = safe_org_normalized(row.organization_name)
        explicit_dom = normalize_domain(row.domain_raw) if row.domain_raw else None
        email_dom = domain_from_email(em) if em else None

        if row.origin_plane in origin_assertion_rows:
            origin_assertion_rows[row.origin_plane] += 1

        if kind != "valid" or not em:
            metrics["records_without_usable_email"] += 1
        usable_org = bool(org_nn) or bool(explicit_dom and is_institutional_domain(explicit_dom))
        if not usable_org and not (email_dom and is_institutional_domain(email_dom)):
            metrics["records_without_usable_organization_identity"] += 1

        if em:
            bucket = contact_emails.setdefault(
                em,
                {
                    "display_name": None,
                    "role": None,
                    "first_evidence_at": None,
                    "last_evidence_at": None,
                    "origins": set(),
                    "source_planes": set(),
                },
            )
            bucket["display_name"] = prefer_display_name(bucket["display_name"], row.display_name)
            if row.role and not bucket["role"]:
                bucket["role"] = row.role.strip()
            bucket["first_evidence_at"] = _min_iso(bucket["first_evidence_at"], row.evidence_at)
            bucket["last_evidence_at"] = _max_iso(bucket["last_evidence_at"], row.evidence_at)
            bucket["origins"].add(row.origin_plane)
            bucket["source_planes"].add(row.source_plane)
            origins_by_email[em].add(row.origin_plane)
            if email_dom:
                email_domain_by_email[em] = email_dom
            if explicit_dom:
                explicit_domains_by_email[em].add(explicit_dom)
                domain_ptrs_by_email[em].append({**_ptr(row), "domain_raw": explicit_dom, "kind": "explicit"})
            if org_nn:
                org_names_by_email[em].add(org_nn)
                if row.organization_name:
                    org_raw_by_email[em].add(str(row.organization_name).strip())
                org_ptrs_by_email[em].append({**_ptr(row), "normalized_org": org_nn})
            contact_evidence_src.append((em, row, REASON_EXACT_EMAIL))

        # Account creation paths — never create external commercial accounts for INTERNAL_DOMAINS.
        if explicit_dom and is_internal_domain(explicit_dom):
            internal_domain_rows.append(row)
            continue
        if email_dom and is_internal_domain(email_dom) and not explicit_dom:
            # Contact handled later as internal actor; no commercial account from this domain.
            continue

        if explicit_dom and is_institutional_domain(explicit_dom):
            if org_nn:
                org_names_by_domain[explicit_dom].add(org_nn)
                if row.organization_name:
                    org_raw_by_domain[explicit_dom].add(str(row.organization_name).strip())
                    _bump_alias(
                        alias_obs_by_domain[explicit_dom],
                        normalized_alias=org_nn,
                        alias_name=str(row.organization_name).strip(),
                        evidence_at=row.evidence_at,
                    )
            first, last = domain_evidence_at.get(explicit_dom, (None, None))
            domain_evidence_at[explicit_dom] = (
                _min_iso(first, row.evidence_at),
                _max_iso(last, row.evidence_at),
            )
            account_evidence_src.append((explicit_dom, row, REASON_INSTITUTIONAL_DOMAIN, org_nn))
        elif org_nn:
            # Name-only account candidate (including org text observed on consumer-email rows).
            # Consumer contacts are never auto-linked; the account may still exist for review.
            entry = name_only_orgs.setdefault(
                org_nn,
                {
                    "canonical_name": str(row.organization_name or org_nn).strip(),
                    "first_evidence_at": None,
                    "last_evidence_at": None,
                    "origins": set(),
                    "from_consumer_email": False,
                },
            )
            entry["canonical_name"] = better_canonical_name(
                entry["canonical_name"], str(row.organization_name or "")
            )
            entry["first_evidence_at"] = _min_iso(entry["first_evidence_at"], row.evidence_at)
            entry["last_evidence_at"] = _max_iso(entry["last_evidence_at"], row.evidence_at)
            entry["origins"].add(row.origin_plane)
            if (email_dom and is_consumer_domain(email_dom)) or (
                explicit_dom and is_consumer_domain(explicit_dom)
            ):
                entry["from_consumer_email"] = True
            _bump_alias(
                name_only_alias_obs,
                normalized_alias=org_nn,
                alias_name=str(row.organization_name or org_nn).strip(),
                evidence_at=row.evidence_at,
            )
            account_evidence_src.append((f"name:{org_nn}", row, REASON_ORG_NAME_EVIDENCE, org_nn))


    conflicts: list[ConflictRecord] = []
    evidence: list[EvidenceRecord] = []

    # Emit internal-domain exclusion conflicts (domains never become commercial accounts).
    seen_internal: set[str] = set()
    for row in internal_domain_rows:
        dom = normalize_domain(row.domain_raw) or ""
        if not dom or dom in seen_internal:
            continue
        seen_internal.add(dom)
        subject_keys = _json({"domain": dom})
        conflicts.append(
            ConflictRecord(
                conflict_id=stable_conflict_id(
                    reason_code=REASON_INTERNAL_DOMAIN_EXCLUDED,
                    subject_keys=subject_keys,
                ),
                conflict_type=REASON_INTERNAL_DOMAIN_EXCLUDED,
                reason_code=REASON_INTERNAL_DOMAIN_EXCLUDED,
                subject_kind="account",
                subject_keys_json=subject_keys,
                evidence_pointers_json=_json([_ptr(row)]),
                identity_status=IDENTITY_STATUS_INTERNAL_ACTOR,
                detail_json=_json(
                    {
                        "note": (
                            "Internal domains (origenlab.cl / labdelivery.cl) are represented as "
                            "internal actors, never as external commercial customer institutions."
                        ),
                    }
                ),
            )
        )

    # --- Build accounts (domain-first) ---
    accounts: dict[str, AccountRecord] = {}
    domain_to_account: dict[str, str] = {}
    # Multimap: normalized name → set of candidate account_ids (never overwrite).
    name_to_accounts: dict[str, set[str]] = defaultdict(set)

    for dom in sorted(domain_evidence_at.keys()):
        names = sorted(org_names_by_domain.get(dom, set()))
        first, last = domain_evidence_at[dom]
        account_id = stable_account_id_for_domain(dom)
        conflicting = len(names) > 1
        if not names:
            canonical = dom
            normalized = normalize_org_name(dom) or dom
            status = IDENTITY_STATUS_NEEDS_REVIEW
            confidence = IDENTITY_CONFIDENCE_MEDIUM
        else:
            raws = sorted(org_raw_by_domain.get(dom, set()), key=lambda s: (-len(s), s.lower()))
            canonical = raws[0] if raws else names[0]
            normalized = names[0]
            if conflicting:
                status = IDENTITY_STATUS_NEEDS_REVIEW
                confidence = IDENTITY_CONFIDENCE_LOW
                subject_keys = _json({"domain": dom, "normalized_names": names})
                conflicts.append(
                    ConflictRecord(
                        conflict_id=stable_conflict_id(
                            reason_code=REASON_DOMAIN_CONFLICTING_ORGS,
                            subject_keys=subject_keys,
                        ),
                        conflict_type=REASON_DOMAIN_CONFLICTING_ORGS,
                        reason_code=REASON_DOMAIN_CONFLICTING_ORGS,
                        subject_kind="account",
                        subject_keys_json=subject_keys,
                        evidence_pointers_json=_json(
                            [{"domain": dom, "normalized_name": n} for n in names]
                        ),
                        identity_status=IDENTITY_STATUS_NEEDS_REVIEW,
                        detail_json=_json(
                            {
                                "note": (
                                    "Institutional domain associated with multiple normalized org "
                                    "names; not silently merged as confident identity."
                                ),
                            }
                        ),
                    )
                )
            else:
                status = IDENTITY_STATUS_RESOLVED
                confidence = IDENTITY_CONFIDENCE_HIGH

        aliases: dict[str, dict[str, Any]] = {}
        for n in names:
            obs = alias_obs_by_domain.get(dom, {}).get(n)
            if obs:
                aliases[n] = dict(obs)
            else:
                raw_candidates = [
                    r for r in org_raw_by_domain.get(dom, set()) if safe_org_normalized(r) == n
                ]
                alias_name = (
                    sorted(raw_candidates, key=lambda s: (-len(s), s.lower()))[0]
                    if raw_candidates
                    else n
                )
                aliases[n] = {
                    "alias_name": alias_name,
                    "normalized_alias": n,
                    "evidence_count": 1,
                    "first_evidence_at": None,
                    "last_evidence_at": None,
                }

        accounts[account_id] = AccountRecord(
            account_id=account_id,
            canonical_name=canonical,
            normalized_name=normalized,
            primary_domain=dom,
            first_evidence_at=first,
            last_evidence_at=last,
            identity_confidence=confidence,
            identity_status=status,
            aliases=aliases,
            domains={
                dom: {
                    "domain_norm": dom,
                    "is_institutional": 1,
                    "link_method": REASON_INSTITUTIONAL_DOMAIN,
                    "first_evidence_at": first,
                    "last_evidence_at": last,
                }
            },
        )
        domain_to_account[dom] = account_id
        for n in names:
            name_to_accounts[n].add(account_id)
        if not names:
            name_to_accounts[normalized].add(account_id)

    # Name-only accounts: do NOT merge with domain accounts solely because names match.
    # Keep separate name-keyed accounts; name→account multimap collects ALL candidates.
    for org_nn in sorted(name_only_orgs.keys()):
        entry = name_only_orgs[org_nn]
        account_id = stable_account_id_for_name(org_nn)
        if account_id in accounts:
            name_to_accounts[org_nn].add(account_id)
            continue
        origins = entry["origins"]
        research_only = origins == {ORIGIN_RESEARCH} or (
            ORIGIN_RESEARCH in origins
            and not (origins & {ORIGIN_ORIGENLAB_GMAIL, ORIGIN_LABDELIVERY_ARCHIVE})
        )
        from_consumer = bool(entry.get("from_consumer_email"))
        confidence = IDENTITY_CONFIDENCE_LOW if (research_only or from_consumer) else IDENTITY_CONFIDENCE_MEDIUM
        status = (
            IDENTITY_STATUS_NEEDS_REVIEW
            if (research_only or from_consumer)
            else IDENTITY_STATUS_RESOLVED
        )
        alias_obs = name_only_alias_obs.get(org_nn) or {
            "alias_name": entry["canonical_name"],
            "normalized_alias": org_nn,
            "evidence_count": 1,
            "first_evidence_at": entry["first_evidence_at"],
            "last_evidence_at": entry["last_evidence_at"],
        }
        accounts[account_id] = AccountRecord(
            account_id=account_id,
            canonical_name=entry["canonical_name"],
            normalized_name=org_nn,
            primary_domain=None,
            first_evidence_at=entry["first_evidence_at"],
            last_evidence_at=entry["last_evidence_at"],
            identity_confidence=confidence,
            identity_status=status,
            aliases={org_nn: dict(alias_obs)},
            domains={},
        )
        name_to_accounts[org_nn].add(account_id)

    # --- Contacts + linking ---
    contacts: list[ContactRecord] = []
    consumer_refused_contacts: set[str] = set()

    for em in sorted(contact_emails.keys()):
        data = contact_emails[em]
        contact_id = stable_contact_id(em)
        email_dom = email_domain_by_email.get(em) or domain_from_email(em)
        explicit_doms = sorted(explicit_domains_by_email.get(em, set()))
        org_names = sorted(org_names_by_email.get(em, set()))
        conflicting_orgs = len(org_names) > 1

        # Competing domain evidence: email domain vs explicit domain_raw vs multiple explicits.
        competing_domains: set[str] = set()
        if email_dom and is_institutional_domain(email_dom):
            competing_domains.add(email_dom)
        for d in explicit_doms:
            if is_institutional_domain(d):
                competing_domains.add(d)
        domain_disagreement = False
        if email_dom and explicit_doms:
            for d in explicit_doms:
                if is_institutional_domain(d) and is_institutional_domain(email_dom) and d != email_dom:
                    domain_disagreement = True
                if is_institutional_domain(d) and is_consumer_domain(email_dom):
                    # Consumer email with explicit institutional domain_raw — competing signals.
                    domain_disagreement = True
        if len(competing_domains) > 1:
            domain_disagreement = True

        account_id: str | None = None
        link_method: str | None = None
        status = IDENTITY_STATUS_RESOLVED
        confidence = IDENTITY_CONFIDENCE_HIGH

        if email_dom and is_internal_domain(email_dom):
            status = IDENTITY_STATUS_INTERNAL_ACTOR
            confidence = IDENTITY_CONFIDENCE_HIGH
            subject_keys = _json({"email": em, "domain": email_dom})
            conflicts.append(
                ConflictRecord(
                    conflict_id=stable_conflict_id(
                        reason_code=REASON_INTERNAL_DOMAIN_EXCLUDED,
                        subject_keys=subject_keys,
                    ),
                    conflict_type=REASON_INTERNAL_DOMAIN_EXCLUDED,
                    reason_code=REASON_INTERNAL_DOMAIN_EXCLUDED,
                    subject_kind="contact",
                    subject_keys_json=subject_keys,
                    evidence_pointers_json=_json(
                        [_ptr(r) for e_em, r, _reason in contact_evidence_src if e_em == em][:8]
                    ),
                    identity_status=IDENTITY_STATUS_INTERNAL_ACTOR,
                    detail_json=_json(
                        {
                            "note": (
                                "Internal actor contact (OrigenLab/Labdelivery). Not linked to an "
                                "external commercial account; not classified as a customer institution."
                            ),
                        }
                    ),
                )
            )
        elif conflicting_orgs:
            status = IDENTITY_STATUS_NEEDS_REVIEW
            confidence = IDENTITY_CONFIDENCE_LOW
            subject_keys = _json({"email": em, "normalized_names": org_names})
            conflicts.append(
                ConflictRecord(
                    conflict_id=stable_conflict_id(
                        reason_code=REASON_EMAIL_CONFLICTING_ORGS,
                        subject_keys=subject_keys,
                    ),
                    conflict_type=REASON_EMAIL_CONFLICTING_ORGS,
                    reason_code=REASON_EMAIL_CONFLICTING_ORGS,
                    subject_kind="contact",
                    subject_keys_json=subject_keys,
                    evidence_pointers_json=_json(org_ptrs_by_email.get(em, [])),
                    identity_status=IDENTITY_STATUS_NEEDS_REVIEW,
                    detail_json=_json(
                        {
                            "note": (
                                "Exact email associated with incompatible organization names; "
                                "account link withheld."
                            ),
                        }
                    ),
                )
            )
        elif domain_disagreement:
            status = IDENTITY_STATUS_AMBIGUOUS
            confidence = IDENTITY_CONFIDENCE_LOW
            subject_keys = _json(
                {
                    "email": em,
                    "email_domain": email_dom,
                    "explicit_domains": explicit_doms,
                    "candidate_institutional_domains": sorted(competing_domains),
                }
            )
            reason = (
                REASON_EMAIL_COMPETING_DOMAINS
                if len(competing_domains) > 1 or domain_disagreement
                else REASON_COMPETING_ACCOUNT_LINKS
            )
            # Prefer competing-account-links when multiple candidate accounts are implicated.
            candidate_accounts = sorted(
                {domain_to_account[d] for d in competing_domains if d in domain_to_account}
            )
            if len(candidate_accounts) > 1:
                reason = REASON_COMPETING_ACCOUNT_LINKS
            conflicts.append(
                ConflictRecord(
                    conflict_id=stable_conflict_id(reason_code=reason, subject_keys=subject_keys),
                    conflict_type=reason,
                    reason_code=reason,
                    subject_kind="contact",
                    subject_keys_json=subject_keys,
                    evidence_pointers_json=_json(
                        domain_ptrs_by_email.get(em, [])
                        + [{"email": em, "email_domain": email_dom, "kind": "email_domain"}]
                    ),
                    identity_status=IDENTITY_STATUS_AMBIGUOUS,
                    detail_json=_json(
                        {
                            "note": (
                                "Incompatible domain evidence for the same exact email; "
                                "account link withheld."
                            ),
                            "candidate_account_ids": candidate_accounts,
                        }
                    ),
                )
            )
        elif email_dom and is_consumer_domain(email_dom):
            # Never auto-link by domain. Org-name evidence may still create a name-only account
            # (built above), but the consumer contact stays unlinked.
            status = IDENTITY_STATUS_UNLINKED
            confidence = IDENTITY_CONFIDENCE_MEDIUM
            consumer_refused_contacts.add(em)
            subject_keys = _json({"email": em, "domain": email_dom, "org_names": org_names})
            conflicts.append(
                ConflictRecord(
                    conflict_id=stable_conflict_id(
                        reason_code=REASON_CONSUMER_DOMAIN_REFUSED,
                        subject_keys=subject_keys,
                    ),
                    conflict_type=REASON_CONSUMER_DOMAIN_REFUSED,
                    reason_code=REASON_CONSUMER_DOMAIN_REFUSED,
                    subject_kind="contact",
                    subject_keys_json=subject_keys,
                    evidence_pointers_json=_json(
                        [_ptr(r) for e_em, r, _reason in contact_evidence_src if e_em == em][:8]
                    ),
                    identity_status=IDENTITY_STATUS_UNLINKED,
                    detail_json=_json(
                        {
                            "note": "Consumer/public email domain never proves account membership.",
                        }
                    ),
                )
            )
            if org_names:
                # Withheld org-name link review candidate.
                candidate_ids = sorted(name_to_accounts.get(org_names[0], set())) if len(org_names) == 1 else []
                withhold_keys = _json(
                    {
                        "email": em,
                        "org_names": org_names,
                        "candidate_account_ids": candidate_ids,
                    }
                )
                conflicts.append(
                    ConflictRecord(
                        conflict_id=stable_conflict_id(
                            reason_code=REASON_CONSUMER_ORG_LINK_WITHHELD,
                            subject_keys=withhold_keys,
                        ),
                        conflict_type=REASON_CONSUMER_ORG_LINK_WITHHELD,
                        reason_code=REASON_CONSUMER_ORG_LINK_WITHHELD,
                        subject_kind="contact",
                        subject_keys_json=withhold_keys,
                        evidence_pointers_json=_json(org_ptrs_by_email.get(em, [])),
                        identity_status=IDENTITY_STATUS_NEEDS_REVIEW,
                        detail_json=_json(
                            {
                                "note": (
                                    "Organization-name evidence may create a low-confidence "
                                    "name-only account, but the consumer-domain contact is not "
                                    "automatically attached."
                                ),
                            }
                        ),
                    )
                )
        elif email_dom and is_institutional_domain(email_dom) and email_dom in domain_to_account:
            candidate = domain_to_account[email_dom]
            acc = accounts[candidate]
            if not org_names or any(n in acc.aliases or n == acc.normalized_name for n in org_names):
                account_id = candidate
                link_method = REASON_INSTITUTIONAL_DOMAIN
                if acc.identity_status == IDENTITY_STATUS_NEEDS_REVIEW:
                    status = IDENTITY_STATUS_NEEDS_REVIEW
                    confidence = IDENTITY_CONFIDENCE_MEDIUM
                else:
                    status = IDENTITY_STATUS_RESOLVED
                    confidence = IDENTITY_CONFIDENCE_HIGH
            else:
                status = IDENTITY_STATUS_AMBIGUOUS
                confidence = IDENTITY_CONFIDENCE_LOW
                subject_keys = _json(
                    {
                        "email": em,
                        "domain": email_dom,
                        "contact_org_names": org_names,
                        "account_id": candidate,
                    }
                )
                conflicts.append(
                    ConflictRecord(
                        conflict_id=stable_conflict_id(
                            reason_code=REASON_EMAIL_CONFLICTING_ORGS,
                            subject_keys=subject_keys,
                        ),
                        conflict_type=REASON_EMAIL_CONFLICTING_ORGS,
                        reason_code=REASON_EMAIL_CONFLICTING_ORGS,
                        subject_kind="contact",
                        subject_keys_json=subject_keys,
                        evidence_pointers_json=_json(org_ptrs_by_email.get(em, [])),
                        identity_status=IDENTITY_STATUS_AMBIGUOUS,
                        detail_json=None,
                    )
                )
        elif org_names and len(org_names) == 1:
            candidates = sorted(name_to_accounts.get(org_names[0], set()))
            if len(candidates) == 1:
                account_id = candidates[0]
                link_method = REASON_COMPATIBLE_ORG_NAME
                status = IDENTITY_STATUS_RESOLVED
                confidence = IDENTITY_CONFIDENCE_MEDIUM
            elif len(candidates) > 1:
                status = IDENTITY_STATUS_AMBIGUOUS
                confidence = IDENTITY_CONFIDENCE_LOW
                subject_keys = _json(
                    {
                        "email": em,
                        "normalized_name": org_names[0],
                        "candidate_account_ids": candidates,
                    }
                )
                conflicts.append(
                    ConflictRecord(
                        conflict_id=stable_conflict_id(
                            reason_code=REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES,
                            subject_keys=subject_keys,
                        ),
                        conflict_type=REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES,
                        reason_code=REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES,
                        subject_kind="contact",
                        subject_keys_json=subject_keys,
                        evidence_pointers_json=_json(org_ptrs_by_email.get(em, [])),
                        identity_status=IDENTITY_STATUS_AMBIGUOUS,
                        detail_json=_json(
                            {
                                "note": (
                                    "Multiple accounts share this normalized organization name "
                                    "(e.g. distinct institutional domains). Contact left unlinked; "
                                    "no arbitrary account selected."
                                ),
                            }
                        ),
                    )
                )
            else:
                status = IDENTITY_STATUS_UNLINKED
                confidence = IDENTITY_CONFIDENCE_MEDIUM
        else:
            status = IDENTITY_STATUS_UNLINKED
            confidence = IDENTITY_CONFIDENCE_MEDIUM if em else IDENTITY_CONFIDENCE_NONE

        if origins_by_email.get(em) == {ORIGIN_RESEARCH}:
            confidence = IDENTITY_CONFIDENCE_LOW if confidence == IDENTITY_CONFIDENCE_HIGH else confidence

        contacts.append(
            ContactRecord(
                contact_id=contact_id,
                normalized_email=em,
                display_name=data["display_name"],
                role=data["role"],
                account_id=account_id,
                account_link_method=link_method,
                first_evidence_at=data["first_evidence_at"],
                last_evidence_at=data["last_evidence_at"],
                identity_confidence=confidence,
                identity_status=status,
                email_domain=email_dom,
            )
        )

        for _, src_row, reason in [t for t in contact_evidence_src if t[0] == em]:
            eid = stable_evidence_id(
                source_table=src_row.source_table,
                source_record_id=src_row.source_record_id,
                evidence_type="contact_identity",
                subject_id=contact_id,
            )
            evidence.append(
                EvidenceRecord(
                    evidence_id=eid,
                    subject_kind="contact",
                    subject_id=contact_id,
                    source_table=src_row.source_table,
                    source_record_id=src_row.source_record_id,
                    source_plane=src_row.source_plane,
                    origin_plane=src_row.origin_plane,
                    evidence_type="contact_identity",
                    evidence_at=_clean_ts(src_row.evidence_at),
                    matching_reason_code=reason,
                    confidence=confidence,
                    detail_json=_json(
                        {
                            "research_only": src_row.origin_plane == ORIGIN_RESEARCH,
                            "reason_research": REASON_RESEARCH_ONLY
                            if src_row.origin_plane == ORIGIN_RESEARCH
                            else None,
                        }
                    ),
                )
            )

    # Account evidence rows
    for key, src_row, reason, _org_nn in account_evidence_src:
        if key.startswith("name:"):
            org_nn = key[5:]
            candidates = sorted(name_to_accounts.get(org_nn, set()))
            # Attach evidence only to the name-keyed account when present; otherwise skip
            # rather than arbitrarily picking a domain account that shares the name.
            name_keyed = stable_account_id_for_name(org_nn)
            account_id = name_keyed if name_keyed in accounts else (candidates[0] if len(candidates) == 1 else None)
        else:
            account_id = domain_to_account.get(key)
        if not account_id:
            continue
        eid = stable_evidence_id(
            source_table=src_row.source_table,
            source_record_id=src_row.source_record_id,
            evidence_type="account_identity",
            subject_id=account_id,
        )
        evidence.append(
            EvidenceRecord(
                evidence_id=eid,
                subject_kind="account",
                subject_id=account_id,
                source_table=src_row.source_table,
                source_record_id=src_row.source_record_id,
                source_plane=src_row.source_plane,
                origin_plane=src_row.origin_plane,
                evidence_type="account_identity",
                evidence_at=_clean_ts(src_row.evidence_at),
                matching_reason_code=reason,
                confidence=accounts[account_id].identity_confidence,
                detail_json=None,
            )
        )

    # Preserve all distinct evidence IDs; collision would silently drop — assert uniqueness.
    evidence_by_id: dict[str, EvidenceRecord] = {}
    for e in evidence:
        if e.evidence_id in evidence_by_id:
            # Same stable id must refer to the same logical assertion; keep first.
            continue
        evidence_by_id[e.evidence_id] = e
    evidence = [evidence_by_id[k] for k in sorted(evidence_by_id.keys())]

    conflict_by_id = {c.conflict_id: c for c in conflicts}
    conflicts = [conflict_by_id[k] for k in sorted(conflict_by_id.keys())]

    account_list = [accounts[k] for k in sorted(accounts.keys())]
    linked = sum(1 for c in contacts if c.account_id)

    contacts_origenlab = sum(1 for c in contacts if ORIGIN_ORIGENLAB_GMAIL in origins_by_email.get(c.normalized_email, set()))
    contacts_labdelivery = sum(
        1 for c in contacts if ORIGIN_LABDELIVERY_ARCHIVE in origins_by_email.get(c.normalized_email, set())
    )
    contacts_research = sum(1 for c in contacts if ORIGIN_RESEARCH in origins_by_email.get(c.normalized_email, set()))
    contacts_research_only = sum(
        1 for c in contacts if origins_by_email.get(c.normalized_email) == {ORIGIN_RESEARCH}
    )

    metrics["consumer_domain_auto_link_refusals"] = len(consumer_refused_contacts)
    metrics.update(
        {
            "canonical_account_count": len(account_list),
            "canonical_contact_count": len(contacts),
            "contacts_linked_to_accounts": linked,
            "unlinked_contacts": len(contacts) - linked,
            "institutional_domain_links": sum(
                1 for c in contacts if c.account_link_method == REASON_INSTITUTIONAL_DOMAIN
            ),
            "account_conflicts": sum(
                1 for c in conflicts if c.conflict_type == REASON_DOMAIN_CONFLICTING_ORGS
            ),
            "contact_conflicts": sum(
                1
                for c in conflicts
                if c.conflict_type
                in {
                    REASON_EMAIL_CONFLICTING_ORGS,
                    REASON_CONSUMER_DOMAIN_REFUSED,
                    REASON_CONSUMER_ORG_LINK_WITHHELD,
                    REASON_COMPETING_ACCOUNT_LINKS,
                    REASON_EMAIL_COMPETING_DOMAINS,
                    REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES,
                }
            ),
            "origenlab_origin_source_assertion_rows": origin_assertion_rows[ORIGIN_ORIGENLAB_GMAIL],
            "labdelivery_origin_source_assertion_rows": origin_assertion_rows[ORIGIN_LABDELIVERY_ARCHIVE],
            "research_origin_source_assertion_rows": origin_assertion_rows[ORIGIN_RESEARCH],
            "canonical_contacts_with_origenlab_origin": contacts_origenlab,
            "canonical_contacts_with_labdelivery_origin": contacts_labdelivery,
            "canonical_contacts_with_research_origin": contacts_research,
            "canonical_contacts_research_only": contacts_research_only,
            # Back-compat aliases (documented as assertion-row counts).
            "origenlab_origin_identities": origin_assertion_rows[ORIGIN_ORIGENLAB_GMAIL],
            "labdelivery_origin_identities": origin_assertion_rows[ORIGIN_LABDELIVERY_ARCHIVE],
            "research_only_identities": contacts_research_only,
            "missing_email_reason_observations": metrics["records_without_usable_email"],
            "missing_org_reason_code": REASON_MISSING_ORG,
            "missing_email_reason_code": REASON_MISSING_EMAIL,
            "opportunity_stage_fields_inferred": False,
            "next_action_fields_inferred": False,
        }
    )

    return IdentityResolution(
        accounts=account_list,
        contacts=contacts,
        evidence=evidence,
        conflicts=conflicts,
        metrics=metrics,
    )
