"""Review-only institution clusters for fragmented buyer identities."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.identity import (
    InstitutionIdentity,
)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InstitutionReviewCluster:
    institution_review_cluster_id: str
    cluster_resolution_status: str
    member_profile_ids: tuple[str, ...]
    member_buyer_units: tuple[str, ...]
    source_identifiers: tuple[str, ...]
    identifier_conflicts: tuple[str, ...]
    linked_account_candidates: tuple[str, ...]
    contact_overlay_summary: dict[str, Any]
    cluster_reason_codes: tuple[str, ...]
    cluster_membership_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "institution_review_cluster_id": self.institution_review_cluster_id,
            "cluster_resolution_status": self.cluster_resolution_status,
            "member_profile_ids": list(self.member_profile_ids),
            "member_buyer_units": list(self.member_buyer_units),
            "source_identifiers": list(self.source_identifiers),
            "identifier_conflicts": list(self.identifier_conflicts),
            "linked_account_candidates": list(self.linked_account_candidates),
            "contact_overlay_summary": dict(self.contact_overlay_summary),
            "cluster_reason_codes": list(self.cluster_reason_codes),
            "cluster_membership_digest": self.cluster_membership_digest,
        }


def _buyer_unit_key(ident: InstitutionIdentity) -> str:
    """Typed buyer unit identity — a display name alone is not an identifier."""
    if ident.chilecompra_buyer_source_id:
        return f"chilecompra_buyer_source_id:{ident.chilecompra_buyer_source_id}"
    if ident.account_id:
        return f"origenlab_account_id:{ident.account_id}"
    if ident.normalized_name:
        return f"normalized_buyer_name:{ident.normalized_name}"
    return f"institution_id:{ident.institution_id}"


def build_institution_review_clusters(
    identities: dict[str, InstitutionIdentity],
    *,
    contact_gap_by_institution: dict[str, str] | None = None,
) -> list[InstitutionReviewCluster]:
    """
    Group profiles that share a normalized buyer name but keep distinct profile IDs.

    Rules:
    * Same authoritative typed buyer identifier already shares a profile — no cluster.
    * Same normalized name with conflicting identifiers → review-only cluster.
    * Similar names do not auto-merge.
    * Evidence stays on originating profiles; clusters do not copy counts.
    * Linked account on one member does not authorize another member.
    """
    contact_gap_by_institution = contact_gap_by_institution or {}
    by_name: dict[str, list[InstitutionIdentity]] = defaultdict(list)
    seen_ids: set[str] = set()
    for ident in identities.values():
        if not ident.normalized_name:
            continue
        if ident.institution_id in seen_ids:
            continue
        seen_ids.add(ident.institution_id)
        by_name[ident.normalized_name].append(ident)

    clusters: list[InstitutionReviewCluster] = []
    for name, members in sorted(by_name.items()):
        # Deduplicate by institution_id
        uniq: dict[str, InstitutionIdentity] = {
            m.institution_id: m for m in members
        }
        if len(uniq) < 2:
            continue
        member_list = sorted(uniq.values(), key=lambda m: m.institution_id)
        buyer_ids = sorted(
            {
                m.chilecompra_buyer_source_id
                for m in member_list
                if m.chilecompra_buyer_source_id
            }
        )
        account_ids = sorted({m.account_id for m in member_list if m.account_id})
        conflicts: list[str] = []
        if len(buyer_ids) > 1:
            conflicts.append("conflicting_chilecompra_buyer_source_ids")
        if len(account_ids) > 1:
            conflicts.append("multiple_linked_account_candidates")
        reasons = [
            "normalized_name_multi_profile_fragmentation",
            "review_only_cluster_no_auto_merge",
        ]
        if conflicts:
            reasons.extend(conflicts)
        buyer_units = tuple(sorted({_buyer_unit_key(m) for m in member_list}))
        # Cluster key is the membership itself, so the id is stable across runs
        # even when a display name is edited upstream.
        cid = _digest("institution_review_cluster", *sorted(uniq))
        membership_digest = _digest("cluster_membership", *sorted(uniq), *buyer_units)
        gap_summary = {
            iid: contact_gap_by_institution.get(iid, "unknown")
            for iid in sorted(uniq)
        }
        clusters.append(
            InstitutionReviewCluster(
                institution_review_cluster_id=cid,
                cluster_resolution_status="review_only_fragmented_identity",
                member_profile_ids=tuple(sorted(uniq)),
                member_buyer_units=buyer_units,
                source_identifiers=tuple(buyer_ids),
                identifier_conflicts=tuple(sorted(set(conflicts))),
                linked_account_candidates=tuple(account_ids),
                contact_overlay_summary={
                    "per_member_contact_gap_status": gap_summary,
                    "linked_account_does_not_authorize_siblings": True,
                },
                cluster_reason_codes=tuple(sorted(set(reasons))),
                cluster_membership_digest=membership_digest,
            )
        )
    clusters.sort(key=lambda c: c.institution_review_cluster_id)
    return clusters


__all__ = [
    "InstitutionReviewCluster",
    "build_institution_review_clusters",
]
