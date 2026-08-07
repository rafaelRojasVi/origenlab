"""Deterministic institution identity keying (no fuzzy / LLM / web linking)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.commercial_identity.normalize import safe_org_normalized
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (
    IDENTITY_KIND_ACCOUNT,
    IDENTITY_KIND_BUYER_NAME,
    IDENTITY_KIND_CHILECOMPRA_BUYER_ID,
    IDENTITY_KIND_REVIEW,
    IDENTITY_KIND_UNRESOLVED,
)


def _digest(*parts: str) -> str:
    payload = "\0".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InstitutionIdentity:
    """Stable procurement-buyer identity for prospect aggregation."""

    institution_id: str
    identity_kind: str
    display_name: str | None
    normalized_name: str | None
    chilecompra_buyer_source_id: str | None
    account_id: str | None
    account_resolution_status: str
    account_resolution_source: str | None
    account_resolution_reason: str | None
    linked_account_present: bool
    identity_review_required: bool
    provenance: tuple[dict[str, str], ...]
    aliases: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "institution_id": self.institution_id,
            "identity_kind": self.identity_kind,
            "display_name": self.display_name,
            "normalized_name": self.normalized_name,
            "chilecompra_buyer_source_id": self.chilecompra_buyer_source_id,
            "account_id": self.account_id,
            "account_resolution_status": self.account_resolution_status,
            "account_resolution_source": self.account_resolution_source,
            "account_resolution_reason": self.account_resolution_reason,
            "linked_account_present": self.linked_account_present,
            "identity_review_required": self.identity_review_required,
            "provenance": list(self.provenance),
            "aliases": list(self.aliases),
        }


def institution_identity_from_tender(
    tender: CoalescedProcurementTender,
    org: OrganizationResolution | None,
) -> InstitutionIdentity:
    """
    Priority:
      1) PR5E linked account_id
      2) typed ChileCompra buyer_source_id
      3) strict normalized buyer name
      4) unresolved (still retained as a profile key)
    """
    display = (tender.buyer_display_selected or "").strip() or None
    name_norm = safe_org_normalized(display) if display else None
    buyer_id = (tender.buyer_source_id_selected or "").strip() or None

    org_status = org.resolution_status if org else "unlinked"
    org_source = org.resolution_source if org else None
    org_reason = org.reason_code if org else "organization_resolution_missing"
    account_id = org.account_id if org and org.resolution_status == "linked" else None
    linked = bool(account_id)

    provenance: list[dict[str, str]] = [
        {
            "coalesced_tender_id": tender.coalesced_tender_id,
            "canonical_tender_key": tender.canonical_tender_key,
            "candidate_source_kind": tender.candidate_source_kind,
        }
    ]
    if buyer_id:
        provenance.append(
            {
                "identifier_kind": IDENTITY_KIND_CHILECOMPRA_BUYER_ID,
                "identifier_value": buyer_id,
            }
        )
    if name_norm:
        provenance.append(
            {
                "identifier_kind": IDENTITY_KIND_BUYER_NAME,
                "identifier_value": name_norm,
            }
        )

    aliases: list[str] = []
    if display:
        aliases.append(display)
    if name_norm and name_norm not in aliases:
        aliases.append(name_norm)

    if linked and account_id:
        iid = _digest(IDENTITY_KIND_ACCOUNT, account_id)
        return InstitutionIdentity(
            institution_id=iid,
            identity_kind=IDENTITY_KIND_ACCOUNT,
            display_name=display,
            normalized_name=name_norm,
            chilecompra_buyer_source_id=buyer_id,
            account_id=account_id,
            account_resolution_status=org_status,
            account_resolution_source=org_source,
            account_resolution_reason=org_reason,
            linked_account_present=True,
            identity_review_required=False,
            provenance=tuple(provenance),
            aliases=tuple(aliases),
        )

    if buyer_id:
        iid = _digest(IDENTITY_KIND_CHILECOMPRA_BUYER_ID, buyer_id)
        return InstitutionIdentity(
            institution_id=iid,
            identity_kind=IDENTITY_KIND_CHILECOMPRA_BUYER_ID,
            display_name=display,
            normalized_name=name_norm,
            chilecompra_buyer_source_id=buyer_id,
            account_id=None,
            account_resolution_status=org_status,
            account_resolution_source=org_source,
            account_resolution_reason=org_reason,
            linked_account_present=False,
            identity_review_required=False,
            provenance=tuple(provenance),
            aliases=tuple(aliases),
        )

    if name_norm:
        iid = _digest(IDENTITY_KIND_BUYER_NAME, name_norm)
        return InstitutionIdentity(
            institution_id=iid,
            identity_kind=IDENTITY_KIND_BUYER_NAME,
            display_name=display,
            normalized_name=name_norm,
            chilecompra_buyer_source_id=None,
            account_id=None,
            account_resolution_status=org_status,
            account_resolution_source=org_source,
            account_resolution_reason=org_reason,
            linked_account_present=False,
            identity_review_required=False,
            provenance=tuple(provenance),
            aliases=tuple(aliases),
        )

    # Unresolved: still emit a profile keyed by tender identity (not dropped).
    iid = _digest(
        IDENTITY_KIND_UNRESOLVED,
        tender.coalesced_tender_id,
        tender.canonical_tender_key,
    )
    return InstitutionIdentity(
        institution_id=iid,
        identity_kind=IDENTITY_KIND_UNRESOLVED,
        display_name=display,
        normalized_name=None,
        chilecompra_buyer_source_id=None,
        account_id=None,
        account_resolution_status=org_status,
        account_resolution_source=org_source,
        account_resolution_reason=org_reason,
        linked_account_present=False,
        identity_review_required=True,
        provenance=tuple(provenance),
        aliases=tuple(aliases),
    )


def detect_name_identifier_conflicts(
    identities_by_tender: dict[str, InstitutionIdentity],
) -> dict[str, InstitutionIdentity]:
    """
    Identical normalized names with conflicting ChileCompra buyer IDs become
    identity-review cases (no silent merge). Returns remapped identities.
    """
    by_name: dict[str, set[str]] = {}
    for ident in identities_by_tender.values():
        if not ident.normalized_name:
            continue
        if not ident.chilecompra_buyer_source_id:
            continue
        by_name.setdefault(ident.normalized_name, set()).add(
            ident.chilecompra_buyer_source_id
        )

    conflict_names = {n for n, ids in by_name.items() if len(ids) > 1}
    if not conflict_names:
        return identities_by_tender

    out: dict[str, InstitutionIdentity] = {}
    for tid, ident in identities_by_tender.items():
        if (
            ident.normalized_name in conflict_names
            and ident.chilecompra_buyer_source_id
            and ident.identity_kind != IDENTITY_KIND_ACCOUNT
        ):
            # Keep buyer-id granularity; flag review instead of merging by name.
            out[tid] = InstitutionIdentity(
                institution_id=ident.institution_id,
                identity_kind=IDENTITY_KIND_REVIEW
                if ident.identity_kind == IDENTITY_KIND_BUYER_NAME
                else ident.identity_kind,
                display_name=ident.display_name,
                normalized_name=ident.normalized_name,
                chilecompra_buyer_source_id=ident.chilecompra_buyer_source_id,
                account_id=ident.account_id,
                account_resolution_status=ident.account_resolution_status,
                account_resolution_source=ident.account_resolution_source,
                account_resolution_reason=ident.account_resolution_reason,
                linked_account_present=ident.linked_account_present,
                identity_review_required=True,
                provenance=ident.provenance
                + (
                    {
                        "conflict": "normalized_name_with_conflicting_buyer_ids",
                        "normalized_name": ident.normalized_name or "",
                    },
                ),
                aliases=ident.aliases,
            )
        else:
            out[tid] = ident
    return out


__all__ = [
    "InstitutionIdentity",
    "detect_name_identifier_conflicts",
    "institution_identity_from_tender",
]
