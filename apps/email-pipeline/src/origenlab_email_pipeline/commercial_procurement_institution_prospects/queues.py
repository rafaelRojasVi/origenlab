"""Separate deterministic operator queues (no combined lead score)."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_scope import (
    DISPOSITION_CATALOG_FIT,
    DISPOSITION_POSSIBLE_FIT,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
    KNOWN_OPEN,
)


def build_operator_queues(
    *,
    profiles: list[dict[str, Any]],
    tender_rows: list[dict[str, Any]],
    line_rows: list[dict[str, Any]],
    families: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    match_review: list[dict[str, Any]],
    contact_gaps: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    Produce separate queues. No queue implies outreach authorization.
    """
    current: list[dict[str, Any]] = []
    historical: list[dict[str, Any]] = []
    line_review: list[dict[str, Any]] = []
    retender_review: list[dict[str, Any]] = []

    for row in tender_rows:
        life = row.get("projected_lifecycle_class") or row.get("lifecycle_class")
        disp = row.get("review_disposition")
        signal = row.get("commercial_signal_type") or row.get("commercial_evidence_signal")
        base = {
            "tender_code": row.get("tender_code"),
            "institution_id": row.get("institution_id"),
            "display_name": row.get("display_name"),
            "lifecycle_class": life,
            "review_disposition": disp,
            "commercial_signal_type": signal,
            "catalog_fit_status": row.get("catalog_fit_status"),
            "opportunity_urgency_band": row.get("opportunity_urgency_band"),
            "prospect_strength_band": row.get("prospect_strength_band"),
            "reason_codes": row.get("reason_codes") or [],
            "contact_authorization": False,
            "outreach_authorization": False,
        }
        if life in KNOWN_OPEN and disp in {
            DISPOSITION_CATALOG_FIT,
            DISPOSITION_POSSIBLE_FIT,
        }:
            current.append(
                {
                    **base,
                    "queue": "current_opportunity",
                    "eligibility_reason_codes": row.get("eligibility_reason_codes")
                    or [],
                }
            )
        elif signal in {
            "equipment_purchase_signal",
            "installed_base_signal",
            "rental_or_comodato_signal",
        } and life not in KNOWN_OPEN:
            historical.append(
                {
                    **base,
                    "queue": "historical_prospect",
                    "first_observed_date": row.get("publication_timestamp"),
                    "most_recent_observed_date": row.get("close_timestamp")
                    or row.get("publication_timestamp"),
                }
            )

    for row in line_rows:
        if row.get("line_disposition") in {
            "review_required",
            "ambiguous",
        } or row.get("partition") == "review_required":
            line_review.append(
                {
                    **row,
                    "contact_authorization": False,
                    "outreach_authorization": False,
                }
            )

    for fam in families:
        if fam.get("family_resolution_status") == "retender_review_required":
            retender_review.append(
                {
                    **fam,
                    "contact_authorization": False,
                    "outreach_authorization": False,
                }
            )

    # Institution match review: unresolved/ambiguous/fragmented.
    identity_queue = list(match_review)
    for cluster in clusters:
        identity_queue.append(
            {
                "queue": "institution_match_review",
                "institution_review_cluster_id": cluster.get(
                    "institution_review_cluster_id"
                ),
                "cluster_resolution_status": cluster.get("cluster_resolution_status"),
                "member_profile_ids": cluster.get("member_profile_ids"),
                "identifier_conflicts": cluster.get("identifier_conflicts"),
                "cluster_reason_codes": cluster.get("cluster_reason_codes"),
                "confirmed_account": False,
                "contact_authorization": False,
                "outreach_authorization": False,
            }
        )

    def _sort_key(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda r: tuple(str(r.get(k) or "") for k in keys),
        )

    return {
        "current_opportunity_queue": _sort_key(
            current, "tender_code", "institution_id"
        ),
        "historical_prospect_queue": _sort_key(
            historical, "tender_code", "institution_id"
        ),
        "institution_match_review_queue": _sort_key(
            identity_queue,
            "institution_id",
            "institution_review_cluster_id",
        ),
        "contact_gap_queue": _sort_key(
            contact_gaps, "institution_id", "contact_gap_status"
        ),
        "line_evidence_review_queue": _sort_key(
            line_review, "tender_code", "unit_id"
        ),
        "retender_review_queue": _sort_key(
            retender_review, "family_id"
        ),
    }


__all__ = ["build_operator_queues"]
