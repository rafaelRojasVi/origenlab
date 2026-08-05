"""Shared pure stable-ID projectors used by construction and reconciliation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)


def _digest_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = canonical_json_digest(dict(payload))
    return f"{prefix}_{digest[:32]}"


def organization_resolution_id_from_payload(payload: Mapping[str, Any]) -> str:
    """Stable organization resolution ID from the same payload used at construction."""
    return _digest_id(
        "org",
        {"kind": "organization_resolution", **dict(payload)},
    )


def contact_resolution_id_deferred(
    *,
    coalesced_tender_id: str,
    organization_resolution_id: str,
    reason: str,
) -> str:
    return _digest_id(
        "crs",
        {
            "coalesced_tender_id": coalesced_tender_id,
            "status": "contact_resolution_deferred",
            "organization_resolution_id": organization_resolution_id,
            "reason": reason,
        },
    )


def contact_resolution_id_linked(
    *,
    coalesced_tender_id: str,
    organization_resolution_id: str,
    account_id: str,
    status: str,
    selected_contact_id: str | None,
    reason: str,
) -> str:
    return _digest_id(
        "crs",
        {
            "coalesced_tender_id": coalesced_tender_id,
            "status": status,
            "organization_resolution_id": organization_resolution_id,
            "account_id": account_id,
            "selected_contact_id": selected_contact_id,
            "reason": reason,
        },
    )


def candidate_id_for(
    *,
    contact_id: str,
    account_id: str,
    ranking_tier: str,
) -> str:
    """Candidate ID from source keys only — never from a provisional parent CRS."""
    return _digest_id(
        "ccand",
        {
            "contact_id": contact_id,
            "account_id": account_id,
            "tier": ranking_tier,
        },
    )


def conflict_id_for(
    *,
    tender_id: str,
    contact_ids: Sequence[str],
    tier: str,
) -> str:
    return _digest_id(
        "cconf",
        {
            "tender": tender_id,
            "contacts": sorted(contact_ids),
            "tier": tier,
        },
    )
