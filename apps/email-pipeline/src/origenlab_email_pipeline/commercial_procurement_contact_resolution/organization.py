"""Deterministic organization/account resolution for PR5E."""

from __future__ import annotations

import json
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
    assert_active_read_transaction,
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
    assert_active_read_transaction(conn)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


class Pr4ProvenanceError(ValueError):
    """Malformed or incomplete PR4 provenance that must not be silently ignored."""


def _parse_candidate_account_ids(
    raw: Any,
    *,
    already_parsed: bool = False,
) -> tuple[str, ...]:
    """Parse candidate account IDs; never silently coerce malformed values to ()."""
    if already_parsed and isinstance(raw, (list, tuple)):
        return tuple(sorted({str(x) for x in raw if str(x).strip()}))
    if raw is None:
        raise Pr4ProvenanceError("candidate_account_ids_json is null")
    if isinstance(raw, (list, tuple)):
        return tuple(sorted({str(x) for x in raw if str(x).strip()}))
    text = str(raw).strip()
    if not text:
        raise Pr4ProvenanceError("candidate_account_ids_json is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Pr4ProvenanceError(
            f"candidate_account_ids_json is not valid JSON: {exc}"
        ) from exc
    if parsed is None:
        raise Pr4ProvenanceError("candidate_account_ids_json decoded to null")
    if not isinstance(parsed, list):
        raise Pr4ProvenanceError(
            f"candidate_account_ids_json must be a JSON list; got {type(parsed).__name__}"
        )
    return tuple(sorted({str(x) for x in parsed if str(x).strip()}))


def load_pr4_resolutions_by_procurement(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    """Load PR4 account resolutions keyed by procurement_id (read-only).

    Preserves ``candidate_account_ids`` — conflicts must not be discarded.
    Malformed JSON raises ``Pr4ProvenanceError``.
    """
    assert_active_read_transaction(conn)
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
        raw_json = r["candidate_account_ids_json"]
        try:
            cands = _parse_candidate_account_ids(raw_json)
        except Pr4ProvenanceError as exc:
            raise Pr4ProvenanceError(
                f"procurement_id={r['procurement_id']}: {exc}"
            ) from exc
        out[str(r["procurement_id"])] = {
            "resolution_id": str(r["resolution_id"]),
            "procurement_id": str(r["procurement_id"]),
            "resolution_status": str(r["resolution_status"] or ""),
            "account_id": (str(r["account_id"]) if r["account_id"] else None),
            "link_route": (str(r["link_route"]) if r["link_route"] else None),
            "reason_code": str(r["reason_code"] or ""),
            "candidate_account_ids": cands,
            "candidate_account_ids_json": str(raw_json if raw_json is not None else "[]"),
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


def _deterministic_link_route(routes: list[str]) -> str | None:
    """Preserve multi-route provenance without arbitrary first-route selection."""
    uniq = sorted({r for r in routes if r})
    if not uniq:
        return None
    if len(uniq) == 1:
        return uniq[0]
    return "|".join(uniq)


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

    if pr4_ids:
        missing: list[str] = []
        rows: list[Mapping[str, Any]] = []
        for pid in pr4_ids:
            row = pr4_by_procurement.get(pid)
            if not row:
                missing.append(pid)
            else:
                rows.append(row)

        pr4_resolution_ids = tuple(
            sorted({str(r["resolution_id"]) for r in rows if r.get("resolution_id")})
        )

        if missing:
            payload = {
                "coalesced_tender_id": tender.coalesced_tender_id,
                "status": "unlinked",
                "source": "pr4_constituent_incomplete",
                "missing": sorted(missing),
            }
            return OrganizationResolution(
                organization_resolution_id=_org_id(payload),
                coalesced_tender_id=tender.coalesced_tender_id,
                relevance_decision_id=relevance_decision_id,
                resolution_status="unlinked",
                resolution_source="pr4_constituent_incomplete",
                account_id=None,
                link_route=None,
                reason_code="missing_pr4_constituent_resolution",
                candidate_account_ids=(),
                evidence_ref_ids=evidence_refs,
                pr4_procurement_ids=pr4_ids,
                pr4_resolution_ids=pr4_resolution_ids,
                buyer_field_sufficiency=sufficiency,
                identity_fingerprint=identity_fingerprint,
            )

        linked_accounts: list[str] = []
        stale_accounts: list[str] = []
        routes: list[str] = []
        all_candidate_accounts: set[str] = set()
        non_unanimous = False

        for row in rows:
            try:
                if row.get("candidate_account_ids_json") is None and "candidate_account_ids" in row:
                    cands = _parse_candidate_account_ids(
                        row.get("candidate_account_ids") or (),
                        already_parsed=True,
                    )
                else:
                    cands = _parse_candidate_account_ids(
                        row.get("candidate_account_ids")
                        if isinstance(row.get("candidate_account_ids"), (list, tuple))
                        else row.get("candidate_account_ids_json")
                    )
            except Pr4ProvenanceError:
                payload = {
                    "coalesced_tender_id": tender.coalesced_tender_id,
                    "status": "unlinked",
                    "source": "pr4_provenance_malformed",
                    "procurement_id": row.get("procurement_id"),
                }
                return OrganizationResolution(
                    organization_resolution_id=_org_id(payload),
                    coalesced_tender_id=tender.coalesced_tender_id,
                    relevance_decision_id=relevance_decision_id,
                    resolution_status="unlinked",
                    resolution_source="pr4_provenance_malformed",
                    account_id=None,
                    link_route=None,
                    reason_code="malformed_pr4_candidate_account_ids_json",
                    candidate_account_ids=(),
                    evidence_ref_ids=evidence_refs,
                    pr4_procurement_ids=pr4_ids,
                    pr4_resolution_ids=pr4_resolution_ids,
                    buyer_field_sufficiency=sufficiency,
                    identity_fingerprint=identity_fingerprint,
                )
            all_candidate_accounts.update(cands)
            status = str(row.get("resolution_status") or "")
            aid = str(row["account_id"]) if row.get("account_id") else None
            if status == RESOLUTION_LINKED and aid:
                if aid not in known_account_ids:
                    stale_accounts.append(aid)
                else:
                    linked_accounts.append(aid)
                    if row.get("link_route"):
                        routes.append(str(row["link_route"]))
            else:
                non_unanimous = True

        if stale_accounts:
            payload = {
                "coalesced_tender_id": tender.coalesced_tender_id,
                "status": "unlinked",
                "source": "pr4_linked_stale",
                "stale": sorted(set(stale_accounts)),
            }
            return OrganizationResolution(
                organization_resolution_id=_org_id(payload),
                coalesced_tender_id=tender.coalesced_tender_id,
                relevance_decision_id=relevance_decision_id,
                resolution_status="unlinked",
                resolution_source="pr4_linked_stale",
                account_id=None,
                link_route=None,
                reason_code="linked_account_missing_from_pr2_identity",
                candidate_account_ids=tuple(sorted(set(stale_accounts) | all_candidate_accounts)),
                evidence_ref_ids=evidence_refs,
                pr4_procurement_ids=pr4_ids,
                pr4_resolution_ids=pr4_resolution_ids,
                buyer_field_sufficiency=sufficiency,
                identity_fingerprint=identity_fingerprint,
            )

        unique_linked = sorted(set(linked_accounts))
        competing = sorted(all_candidate_accounts)

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
                candidate_account_ids=tuple(
                    sorted(set(unique_linked) | set(competing))
                ),
                evidence_ref_ids=evidence_refs,
                pr4_procurement_ids=pr4_ids,
                pr4_resolution_ids=pr4_resolution_ids,
                buyer_field_sufficiency=sufficiency,
                identity_fingerprint=identity_fingerprint,
            )

        if len(unique_linked) == 1:
            aid = unique_linked[0]
            # Candidate-account conflicts from any constituent must not be discarded.
            foreign_cands = sorted(a for a in competing if a != aid)
            if foreign_cands:
                payload = {
                    "coalesced_tender_id": tender.coalesced_tender_id,
                    "status": "ambiguous",
                    "source": "pr4_candidate_conflict",
                    "account_id": aid,
                    "foreign": foreign_cands,
                }
                return OrganizationResolution(
                    organization_resolution_id=_org_id(payload),
                    coalesced_tender_id=tender.coalesced_tender_id,
                    relevance_decision_id=relevance_decision_id,
                    resolution_status="ambiguous",
                    resolution_source="pr4_candidate_conflict",
                    account_id=None,
                    link_route=None,
                    reason_code="conflicting_pr4_candidate_accounts",
                    candidate_account_ids=tuple(sorted({aid, *foreign_cands})),
                    evidence_ref_ids=evidence_refs,
                    pr4_procurement_ids=pr4_ids,
                    pr4_resolution_ids=pr4_resolution_ids,
                    buyer_field_sufficiency=sufficiency,
                    identity_fingerprint=identity_fingerprint,
                )
            if non_unanimous:
                # Genuine agreement requires every constituent to be linked to aid.
                payload = {
                    "coalesced_tender_id": tender.coalesced_tender_id,
                    "status": "unlinked",
                    "source": "pr4_constituents_not_unanimously_linked",
                    "account_id": aid,
                }
                return OrganizationResolution(
                    organization_resolution_id=_org_id(payload),
                    coalesced_tender_id=tender.coalesced_tender_id,
                    relevance_decision_id=relevance_decision_id,
                    resolution_status="unlinked",
                    resolution_source="pr4_constituents_not_unanimously_linked",
                    account_id=None,
                    link_route=None,
                    reason_code="pr4_constituents_not_unanimously_linked",
                    candidate_account_ids=(aid,),
                    evidence_ref_ids=evidence_refs,
                    pr4_procurement_ids=pr4_ids,
                    pr4_resolution_ids=pr4_resolution_ids,
                    buyer_field_sufficiency=sufficiency,
                    identity_fingerprint=identity_fingerprint,
                )
            payload = {
                "coalesced_tender_id": tender.coalesced_tender_id,
                "status": "linked",
                "source": "pr4_linked_consistent",
                "account_id": aid,
                "routes": sorted(set(routes)),
            }
            return OrganizationResolution(
                organization_resolution_id=_org_id(payload),
                coalesced_tender_id=tender.coalesced_tender_id,
                relevance_decision_id=relevance_decision_id,
                resolution_status="linked",
                resolution_source="pr4_linked_consistent",
                account_id=aid,
                link_route=_deterministic_link_route(routes),
                reason_code=(
                    "pr4_linked_account_carried_forward"
                    if len(set(routes)) <= 1
                    else "pr4_linked_account_carried_forward_multi_route"
                ),
                candidate_account_ids=(),
                evidence_ref_ids=evidence_refs,
                pr4_procurement_ids=pr4_ids,
                pr4_resolution_ids=pr4_resolution_ids,
                buyer_field_sufficiency=sufficiency,
                identity_fingerprint=identity_fingerprint,
            )

        # No linked constituents — candidate-only conflicts still matter.
        if len(competing) > 1:
            payload = {
                "coalesced_tender_id": tender.coalesced_tender_id,
                "status": "ambiguous",
                "source": "pr4_candidate_conflict",
                "accounts": competing,
            }
            return OrganizationResolution(
                organization_resolution_id=_org_id(payload),
                coalesced_tender_id=tender.coalesced_tender_id,
                relevance_decision_id=relevance_decision_id,
                resolution_status="ambiguous",
                resolution_source="pr4_candidate_conflict",
                account_id=None,
                link_route=None,
                reason_code="conflicting_pr4_candidate_accounts",
                candidate_account_ids=tuple(competing),
                evidence_ref_ids=evidence_refs,
                pr4_procurement_ids=pr4_ids,
                pr4_resolution_ids=pr4_resolution_ids,
                buyer_field_sufficiency=sufficiency,
                identity_fingerprint=identity_fingerprint,
            )
        # PR4 constituents present but not unanimously linked — never live-reinterpret.
        payload = {
            "coalesced_tender_id": tender.coalesced_tender_id,
            "status": "unlinked",
            "source": "pr4_constituents_unresolved",
            "candidates": competing,
        }
        return OrganizationResolution(
            organization_resolution_id=_org_id(payload),
            coalesced_tender_id=tender.coalesced_tender_id,
            relevance_decision_id=relevance_decision_id,
            resolution_status="unlinked",
            resolution_source="pr4_constituents_unresolved",
            account_id=None,
            link_route=None,
            reason_code="pr4_constituents_present_but_unresolved",
            candidate_account_ids=tuple(competing),
            evidence_ref_ids=evidence_refs,
            pr4_procurement_ids=pr4_ids,
            pr4_resolution_ids=pr4_resolution_ids,
            buyer_field_sufficiency=sufficiency,
            identity_fingerprint=identity_fingerprint,
        )

    # Live-only tenders (no PR4 constituents): reuse exact link routes.
    name_norm = safe_org_normalized(tender.buyer_display_selected or "") or None
    domain = buyer_domain_candidate(tender.buyer_source_id_selected)
    pr4_resolution_ids_live: tuple[str, ...] = ()
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
            pr4_resolution_ids=pr4_resolution_ids_live,
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
    resolution = build_account_resolution(
        procurement_id=tender.coalesced_tender_id,
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
        pr4_resolution_ids=pr4_resolution_ids_live,
        buyer_field_sufficiency=sufficiency,
        identity_fingerprint=identity_fingerprint,
    )


def open_account_index(
    conn: sqlite3.Connection,
) -> tuple[AccountIndex, frozenset[str]]:
    assert_active_read_transaction(conn)
    index = load_pr2_account_index(conn)
    known = frozenset(index.accounts_by_id.keys())
    return index, known
