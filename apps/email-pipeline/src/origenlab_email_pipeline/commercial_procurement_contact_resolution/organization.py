"""Deterministic organization/account resolution for PR5E."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from origenlab_email_pipeline.commercial_identity.normalize import (
    is_consumer_domain,
    is_institutional_domain,
    is_internal_domain,
    safe_org_normalized,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    RESOLUTION_LINKED,
)
from origenlab_email_pipeline.commercial_procurement.link_routes import (
    AccountIndex,
    classify_account_link_route,
)
from origenlab_email_pipeline.commercial_procurement.normalize import (
    is_marketplace_domain,
    is_weak_public_unit_name,
)
from origenlab_email_pipeline.commercial_procurement.resolution import (
    assert_resolution_invariants,
    build_account_resolution,
)
from origenlab_email_pipeline.commercial_procurement.sources import (
    load_pr2_account_index,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    OrganizationResolution,
)
from origenlab_email_pipeline.org_normalize import normalize_domain


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def load_pr4_resolutions_by_procurement(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    """Load PR4 account resolutions keyed by procurement_id (read-only)."""
    if not _table_exists(conn, "commercial_procurement_account_resolution"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        """
        SELECT resolution_id, procurement_id, resolution_status, account_id,
               link_route, reason_code, candidate_account_ids_json
        FROM commercial_procurement_account_resolution
        """
    )
    for r in rows:
        out[str(r["procurement_id"])] = {
            "resolution_id": str(r["resolution_id"]),
            "procurement_id": str(r["procurement_id"]),
            "resolution_status": str(r["resolution_status"] or ""),
            "account_id": (str(r["account_id"]) if r["account_id"] else None),
            "link_route": (str(r["link_route"]) if r["link_route"] else None),
            "reason_code": str(r["reason_code"] or ""),
        }
    return out


def buyer_domain_candidate(buyer_source_id: str | None) -> str | None:
    """Treat buyer_source_id as a domain candidate only — never as account_id."""
    raw = (buyer_source_id or "").strip().lower()
    if not raw or " " in raw or "@" in raw:
        return None
    if "." not in raw:
        return None
    dom = normalize_domain(raw) or raw
    if is_marketplace_domain(dom) or is_consumer_domain(dom) or is_internal_domain(dom):
        return None
    if not is_institutional_domain(dom):
        # Still allow exact index lookup; refused domains handled by link_routes.
        pass
    return dom


def assess_buyer_field_sufficiency(tender: CoalescedProcurementTender) -> str:
    name = safe_org_normalized(tender.buyer_display_selected or "")
    domain = buyer_domain_candidate(tender.buyer_source_id_selected)
    if name and domain:
        return "name_and_domain"
    if name:
        return "name_only"
    if domain:
        return "domain_only"
    if tender.pr4_procurement_ids:
        return "pr4_ids_only"
    return "insufficient"


def _org_id(payload: Mapping[str, Any]) -> str:
    digest = canonical_json_digest({"kind": "organization_resolution", **dict(payload)})
    return f"org_{digest[:32]}"


def resolve_organization_for_tender(
    tender: CoalescedProcurementTender,
    *,
    relevance_decision_id: str,
    account_index: AccountIndex,
    known_account_ids: frozenset[str],
    pr4_by_procurement: Mapping[str, Mapping[str, Any]],
    identity_fingerprint: str,
) -> OrganizationResolution:
    """Resolve exactly one organization decision for one coalesced tender."""
    sufficiency = assess_buyer_field_sufficiency(tender)
    evidence_refs = tuple(tender.evidence_ref_ids)
    pr4_ids = tuple(sorted(set(tender.pr4_procurement_ids or ())))
    if tender.pr4_procurement_id and tender.pr4_procurement_id not in pr4_ids:
        pr4_ids = tuple(sorted({*pr4_ids, tender.pr4_procurement_id}))

    # 1) Carry forward PR4 linked accounts only when consistent.
    linked_accounts: list[str] = []
    pr4_resolution_ids: list[str] = []
    routes: list[str] = []
    for pid in pr4_ids:
        row = pr4_by_procurement.get(pid)
        if not row:
            continue
        pr4_resolution_ids.append(str(row["resolution_id"]))
        if row.get("resolution_status") == RESOLUTION_LINKED and row.get("account_id"):
            aid = str(row["account_id"])
            if aid not in known_account_ids:
                # Selected account must exist in frozen PR2 input.
                continue
            linked_accounts.append(aid)
            if row.get("link_route"):
                routes.append(str(row["link_route"]))

    unique_linked = sorted(set(linked_accounts))
    if len(unique_linked) > 1:
        payload = {
            "coalesced_tender_id": tender.coalesced_tender_id,
            "status": "ambiguous",
            "source": "pr4_linked_conflict",
            "accounts": unique_linked,
        }
        return OrganizationResolution(
            organization_resolution_id=_org_id(payload),
            coalesced_tender_id=tender.coalesced_tender_id,
            relevance_decision_id=relevance_decision_id,
            resolution_status="ambiguous",
            resolution_source="pr4_linked_conflict",
            account_id=None,
            link_route=None,
            reason_code="conflicting_pr4_linked_accounts",
            candidate_account_ids=tuple(unique_linked),
            evidence_ref_ids=evidence_refs,
            pr4_procurement_ids=pr4_ids,
            pr4_resolution_ids=tuple(sorted(set(pr4_resolution_ids))),
            buyer_field_sufficiency=sufficiency,
            identity_fingerprint=identity_fingerprint,
        )
    if len(unique_linked) == 1:
        payload = {
            "coalesced_tender_id": tender.coalesced_tender_id,
            "status": "linked",
            "source": "pr4_linked_consistent",
            "account_id": unique_linked[0],
        }
        return OrganizationResolution(
            organization_resolution_id=_org_id(payload),
            coalesced_tender_id=tender.coalesced_tender_id,
            relevance_decision_id=relevance_decision_id,
            resolution_status="linked",
            resolution_source="pr4_linked_consistent",
            account_id=unique_linked[0],
            link_route=routes[0] if len(set(routes)) == 1 else (routes[0] if routes else None),
            reason_code="pr4_linked_account_carried_forward",
            candidate_account_ids=(),
            evidence_ref_ids=evidence_refs,
            pr4_procurement_ids=pr4_ids,
            pr4_resolution_ids=tuple(sorted(set(pr4_resolution_ids))),
            buyer_field_sufficiency=sufficiency,
            identity_fingerprint=identity_fingerprint,
        )

    # 2) Live / unresolved: reuse PR4 link_routes on coalesced buyer fields.
    name_norm = safe_org_normalized(tender.buyer_display_selected or "") or None
    domain = buyer_domain_candidate(tender.buyer_source_id_selected)
    if not name_norm and not domain:
        payload = {
            "coalesced_tender_id": tender.coalesced_tender_id,
            "status": "deferred_insufficient_buyer_fields",
            "source": "buyer_fields_insufficient",
        }
        return OrganizationResolution(
            organization_resolution_id=_org_id(payload),
            coalesced_tender_id=tender.coalesced_tender_id,
            relevance_decision_id=relevance_decision_id,
            resolution_status="deferred_insufficient_buyer_fields",
            resolution_source="buyer_fields_insufficient",
            account_id=None,
            link_route=None,
            reason_code="insufficient_buyer_fields_for_account_link",
            candidate_account_ids=(),
            evidence_ref_ids=evidence_refs,
            pr4_procurement_ids=pr4_ids,
            pr4_resolution_ids=tuple(sorted(set(pr4_resolution_ids))),
            buyer_field_sufficiency=sufficiency,
            identity_fingerprint=identity_fingerprint,
        )

    weak = is_weak_public_unit_name(name_norm, tender.buyer_display_selected)
    route = classify_account_link_route(
        index=account_index,
        buyer_name_norm=name_norm,
        buyer_domain=domain,
        email_domain=None,
        email_norm=None,
        weak_public_unit_name=weak,
    )
    pseudo_procurement_id = tender.coalesced_tender_id
    resolution = build_account_resolution(
        procurement_id=pseudo_procurement_id,
        result=route,
    )
    assert_resolution_invariants(resolution)
    account_id = resolution.account_id
    if account_id is not None and account_id not in known_account_ids:
        account_id = None
        status = "unlinked"
        reason = "linked_account_missing_from_pr2_identity"
        candidates = resolution.candidate_account_ids
    else:
        status = resolution.resolution_status
        reason = resolution.reason_code
        candidates = resolution.candidate_account_ids

    payload = {
        "coalesced_tender_id": tender.coalesced_tender_id,
        "status": status,
        "source": "live_link_route",
        "account_id": account_id,
        "route": resolution.link_route,
        "reason": reason,
    }
    return OrganizationResolution(
        organization_resolution_id=_org_id(payload),
        coalesced_tender_id=tender.coalesced_tender_id,
        relevance_decision_id=relevance_decision_id,
        resolution_status=status,
        resolution_source="live_link_route",
        account_id=account_id,
        link_route=resolution.link_route,
        reason_code=reason,
        candidate_account_ids=tuple(candidates),
        evidence_ref_ids=evidence_refs,
        pr4_procurement_ids=pr4_ids,
        pr4_resolution_ids=tuple(sorted(set(pr4_resolution_ids))),
        buyer_field_sufficiency=sufficiency,
        identity_fingerprint=identity_fingerprint,
    )


def open_account_index(
    conn: sqlite3.Connection,
) -> tuple[AccountIndex, frozenset[str]]:
    index = load_pr2_account_index(conn)
    known = frozenset(index.accounts_by_id.keys())
    return index, known
