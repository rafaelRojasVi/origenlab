"""Deterministic identity resolution (pure; no SQLite writes).

Precedence (conservative):
1. Exact normalized email is the strongest automatic contact key.
2. Institutional domains may join contacts to accounts when org evidence is compatible.
3. Consumer/public email domains never establish institutional account membership.
4. Organization names never silently merge solely because they look similar.
5. Ambiguous evidence becomes conflicts / needs_review — never a silent merge.
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
    IDENTITY_STATUS_NEEDS_REVIEW,
    IDENTITY_STATUS_RESOLVED,
    IDENTITY_STATUS_UNLINKED,
    ORIGIN_LABDELIVERY_ARCHIVE,
    ORIGIN_ORIGENLAB_GMAIL,
    ORIGIN_RESEARCH,
    REASON_COMPATIBLE_ORG_NAME,
    REASON_CONSUMER_DOMAIN_REFUSED,
    REASON_DOMAIN_CONFLICTING_ORGS,
    REASON_EMAIL_CONFLICTING_ORGS,
    REASON_EXACT_EMAIL,
    REASON_INSTITUTIONAL_DOMAIN,
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


def resolve_identity(source_rows: list[SourceIdentityRow]) -> IdentityResolution:
    """Resolve accounts/contacts/evidence/conflicts from source assertions."""
    # Sort for determinism of intermediate structures; IDs themselves are order-independent.
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

    metrics: dict[str, Any] = {
        "source_identity_rows_inspected": len(ordered),
        "records_without_usable_email": 0,
        "records_without_usable_organization_identity": 0,
        "consumer_domain_auto_link_refusals": 0,
        "origenlab_origin_identities": 0,
        "labdelivery_origin_identities": 0,
        "research_only_identities": 0,
        "label": "synthetic_or_local_fixture",
    }

    # --- Contact aggregation by exact email ---
    contact_emails: dict[str, dict[str, Any]] = {}
    org_names_by_email: dict[str, set[str]] = defaultdict(set)
    org_raw_by_email: dict[str, set[str]] = defaultdict(set)
    domains_by_email: dict[str, set[str]] = defaultdict(set)
    origins_by_email: dict[str, set[str]] = defaultdict(set)
    contact_evidence_src: list[tuple[str, SourceIdentityRow, str]] = []

    # --- Domain / name account evidence ---
    org_names_by_domain: dict[str, set[str]] = defaultdict(set)
    org_raw_by_domain: dict[str, set[str]] = defaultdict(set)
    domain_evidence_at: dict[str, tuple[str | None, str | None]] = {}
    name_only_orgs: dict[str, dict[str, Any]] = {}
    account_evidence_src: list[tuple[str, SourceIdentityRow, str, str | None]] = []

    for row in ordered:
        kind, em = email_input_kind(row.email_raw)
        org_nn = safe_org_normalized(row.organization_name)
        dom = normalize_domain(row.domain_raw) if row.domain_raw else None
        if em:
            d_from_email = domain_from_email(em)
            if d_from_email:
                dom = dom or d_from_email

        if kind != "valid" or not em:
            metrics["records_without_usable_email"] += 1
        if not org_nn and not (dom and is_institutional_domain(dom)):
            metrics["records_without_usable_organization_identity"] += 1

        if row.origin_plane == ORIGIN_ORIGENLAB_GMAIL:
            metrics["origenlab_origin_identities"] += 1
        elif row.origin_plane == ORIGIN_LABDELIVERY_ARCHIVE:
            metrics["labdelivery_origin_identities"] += 1
        elif row.origin_plane == ORIGIN_RESEARCH:
            metrics["research_only_identities"] += 1

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
            if org_nn:
                org_names_by_email[em].add(org_nn)
                if row.organization_name:
                    org_raw_by_email[em].add(str(row.organization_name).strip())
            if dom:
                domains_by_email[em].add(dom)
            contact_evidence_src.append((em, row, REASON_EXACT_EMAIL))

        if dom and is_institutional_domain(dom):
            if org_nn:
                org_names_by_domain[dom].add(org_nn)
                if row.organization_name:
                    org_raw_by_domain[dom].add(str(row.organization_name).strip())
            first, last = domain_evidence_at.get(dom, (None, None))
            domain_evidence_at[dom] = (
                _min_iso(first, row.evidence_at),
                _max_iso(last, row.evidence_at),
            )
            account_evidence_src.append((dom, row, REASON_INSTITUTIONAL_DOMAIN, org_nn))
        elif org_nn and not (dom and is_consumer_domain(dom)):
            # Name-only account candidate (no institutional domain on this row).
            entry = name_only_orgs.setdefault(
                org_nn,
                {
                    "canonical_name": str(row.organization_name or org_nn).strip(),
                    "first_evidence_at": None,
                    "last_evidence_at": None,
                    "origins": set(),
                },
            )
            entry["canonical_name"] = better_canonical_name(
                entry["canonical_name"], str(row.organization_name or "")
            )
            entry["first_evidence_at"] = _min_iso(entry["first_evidence_at"], row.evidence_at)
            entry["last_evidence_at"] = _max_iso(entry["last_evidence_at"], row.evidence_at)
            entry["origins"].add(row.origin_plane)
            account_evidence_src.append((f"name:{org_nn}", row, REASON_ORG_NAME_EVIDENCE, org_nn))
        elif dom and is_consumer_domain(dom) and em:
            metrics["consumer_domain_auto_link_refusals"] += 1

    conflicts: list[ConflictRecord] = []
    evidence: list[EvidenceRecord] = []

    # --- Build accounts (domain-first) ---
    accounts: dict[str, AccountRecord] = {}
    domain_to_account: dict[str, str] = {}
    name_to_account: dict[str, str] = {}

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
            # Prefer longest raw name among observed aliases for display.
            raws = sorted(org_raw_by_domain.get(dom, set()), key=lambda s: (-len(s), s.lower()))
            canonical = raws[0] if raws else names[0]
            normalized = names[0] if len(names) == 1 else names[0]
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
                            [
                                {"domain": dom, "normalized_name": n}
                                for n in names
                            ]
                        ),
                        identity_status=IDENTITY_STATUS_NEEDS_REVIEW,
                        detail_json=_json(
                            {
                                "note": "Institutional domain associated with multiple normalized org names; not silently merged as confident identity.",
                            }
                        ),
                    )
                )
            else:
                status = IDENTITY_STATUS_RESOLVED
                confidence = IDENTITY_CONFIDENCE_HIGH

        aliases = {}
        for n in names:
            raw_candidates = [r for r in org_raw_by_domain.get(dom, set()) if safe_org_normalized(r) == n]
            alias_name = sorted(raw_candidates, key=lambda s: (-len(s), s.lower()))[0] if raw_candidates else n
            aliases[n] = {
                "alias_name": alias_name,
                "normalized_alias": n,
                "evidence_count": 1,
                "first_evidence_at": first,
                "last_evidence_at": last,
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
        if len(names) == 1:
            name_to_account[names[0]] = account_id

    # Name-only accounts when name is not already claimed by a domain account.
    for org_nn in sorted(name_only_orgs.keys()):
        if org_nn in name_to_account:
            continue
        # Do not create a second account if this name already appears on a domain account.
        already = False
        for acc in accounts.values():
            if org_nn in acc.aliases or acc.normalized_name == org_nn:
                already = True
                name_to_account[org_nn] = acc.account_id
                break
        if already:
            continue
        entry = name_only_orgs[org_nn]
        account_id = stable_account_id_for_name(org_nn)
        # Research-only name accounts stay distinguishable and low confidence.
        origins = entry["origins"]
        research_only = origins == {ORIGIN_RESEARCH} or (
            ORIGIN_RESEARCH in origins and not (origins & {ORIGIN_ORIGENLAB_GMAIL, ORIGIN_LABDELIVERY_ARCHIVE})
        )
        accounts[account_id] = AccountRecord(
            account_id=account_id,
            canonical_name=entry["canonical_name"],
            normalized_name=org_nn,
            primary_domain=None,
            first_evidence_at=entry["first_evidence_at"],
            last_evidence_at=entry["last_evidence_at"],
            identity_confidence=IDENTITY_CONFIDENCE_LOW if research_only else IDENTITY_CONFIDENCE_MEDIUM,
            identity_status=IDENTITY_STATUS_NEEDS_REVIEW if research_only else IDENTITY_STATUS_RESOLVED,
            aliases={
                org_nn: {
                    "alias_name": entry["canonical_name"],
                    "normalized_alias": org_nn,
                    "evidence_count": 1,
                    "first_evidence_at": entry["first_evidence_at"],
                    "last_evidence_at": entry["last_evidence_at"],
                }
            },
            domains={},
        )
        name_to_account[org_nn] = account_id

    # --- Contacts + linking ---
    contacts: list[ContactRecord] = []
    for em in sorted(contact_emails.keys()):
        data = contact_emails[em]
        contact_id = stable_contact_id(em)
        email_dom = domain_from_email(em)
        org_names = sorted(org_names_by_email.get(em, set()))
        conflicting_orgs = len(org_names) > 1

        account_id: str | None = None
        link_method: str | None = None
        status = IDENTITY_STATUS_RESOLVED
        confidence = IDENTITY_CONFIDENCE_HIGH

        if conflicting_orgs:
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
                    evidence_pointers_json=_json(
                        [{"email": em, "normalized_name": n} for n in org_names]
                    ),
                    identity_status=IDENTITY_STATUS_NEEDS_REVIEW,
                    detail_json=_json(
                        {"note": "Exact email associated with incompatible organization names; account link withheld."}
                    ),
                )
            )
        elif email_dom and is_consumer_domain(email_dom):
            # Never auto-link by domain. Remain unlinked (even if org text present).
            status = IDENTITY_STATUS_UNLINKED
            confidence = IDENTITY_CONFIDENCE_MEDIUM
            metrics["consumer_domain_auto_link_refusals"] += 1
            subject_keys = _json({"email": em, "domain": email_dom})
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
                    evidence_pointers_json=_json([{"email": em, "domain": email_dom}]),
                    identity_status=IDENTITY_STATUS_UNLINKED,
                    detail_json=_json(
                        {
                            "note": "Consumer/public email domain never proves account membership.",
                        }
                    ),
                )
            )
        elif email_dom and is_institutional_domain(email_dom) and email_dom in domain_to_account:
            candidate = domain_to_account[email_dom]
            acc = accounts[candidate]
            # Compatible if contact has no org name, or name matches account aliases.
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
                        evidence_pointers_json=_json(
                            [{"email": em, "account_id": candidate, "contact_org_names": org_names}]
                        ),
                        identity_status=IDENTITY_STATUS_AMBIGUOUS,
                        detail_json=None,
                    )
                )
        elif org_names and len(org_names) == 1 and org_names[0] in name_to_account:
            # Link by compatible org name only when no conflicting domain path exists.
            account_id = name_to_account[org_names[0]]
            link_method = REASON_COMPATIBLE_ORG_NAME
            status = IDENTITY_STATUS_RESOLVED
            confidence = IDENTITY_CONFIDENCE_MEDIUM
        else:
            status = IDENTITY_STATUS_UNLINKED
            confidence = IDENTITY_CONFIDENCE_MEDIUM if em else IDENTITY_CONFIDENCE_NONE

        # Research-only contacts must not be labeled as customers (no customer field; status note via evidence).
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
            account_id = name_to_account.get(org_nn)
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

    # Deduplicate evidence by evidence_id (stable).
    evidence_by_id = {e.evidence_id: e for e in evidence}
    evidence = [evidence_by_id[k] for k in sorted(evidence_by_id.keys())]

    # Deduplicate conflicts by conflict_id.
    conflict_by_id = {c.conflict_id: c for c in conflicts}
    conflicts = [conflict_by_id[k] for k in sorted(conflict_by_id.keys())]

    account_list = [accounts[k] for k in sorted(accounts.keys())]
    linked = sum(1 for c in contacts if c.account_id)
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
                in {REASON_EMAIL_CONFLICTING_ORGS, REASON_CONSUMER_DOMAIN_REFUSED}
            ),
            "missing_email_reason_observations": metrics["records_without_usable_email"],
            "missing_org_reason_code": REASON_MISSING_ORG,
            "missing_email_reason_code": REASON_MISSING_EMAIL,
        }
    )

    # Explicit: no opportunity-stage / next-action fields are produced.
    metrics["opportunity_stage_fields_inferred"] = False
    metrics["next_action_fields_inferred"] = False

    return IdentityResolution(
        accounts=account_list,
        contacts=contacts,
        evidence=evidence,
        conflicts=conflicts,
        metrics=metrics,
    )
