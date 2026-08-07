"""Separate deterministic operator queues (no combined lead score).

Each queue declares its grain explicitly so an operator can tell what one row
means, and declares its header so an empty queue still writes a readable CSV
instead of a zero-byte file.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from origenlab_email_pipeline.commercial_procurement_institution_prospects.catalog_scope import (
    DISPOSITION_CATALOG_FIT,
    DISPOSITION_POSSIBLE_FIT,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.lifecycle_precedence import (
    KNOWN_OPEN,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.line_claims import (
    SCOPE_AMBIGUOUS,
)

QUEUE_GRAINS: dict[str, str] = {
    "current_opportunity_queue": "institution_id + tender_code + equipment_category",
    "historical_prospect_queue": "institution_id + equipment_category + commercial_signal_type",
    "institution_match_review_queue": "institution_id + institution_review_cluster_id",
    "contact_gap_queue": "institution_id",
    "line_evidence_review_queue": "coalesced_tender_id + unit_id",
    "retender_review_queue": "procurement_event_family_id",
}

_BASE_TENDER_HEADER = [
    "queue_row_id",
    "queue",
    "institution_id",
    "display_name",
    "tender_code",
    "coalesced_tender_id",
    "equipment_category",
    "lifecycle_class",
    "review_disposition",
    "commercial_signal_type",
    "catalog_fit_status",
    "catalog_match_status",
    "opportunity_urgency_band",
    "prospect_strength_band",
    "line_evidence_unit_count",
    "reason_codes",
    "eligibility_reason_codes",
    "contact_authorization",
    "outreach_authorization",
]

EMPTY_QUEUE_HEADERS: dict[str, list[str]] = {
    "current_opportunity_queue": list(_BASE_TENDER_HEADER)
    + ["closing_soon_bucket", "publication_timestamp", "close_timestamp"],
    "historical_prospect_queue": [
        "queue_row_id",
        "queue",
        "institution_id",
        "display_name",
        "equipment_category",
        "commercial_signal_type",
        "tender_count",
        "tender_codes",
        "first_observed_date",
        "most_recent_observed_date",
        "review_dispositions",
        "prospect_strength_band",
        "contact_authorization",
        "outreach_authorization",
    ],
    "institution_match_review_queue": [
        "queue_row_id",
        "queue",
        "institution_id",
        "display_name",
        "institution_review_cluster_id",
        "cluster_resolution_status",
        "member_profile_ids",
        "identifier_conflicts",
        "cluster_reason_codes",
        "account_resolution_status",
        "account_resolution_reason",
        "identity_kind",
        "identity_review_required",
        "operator_next_action",
        "confirmed_account",
        "contact_authorization",
        "outreach_authorization",
    ],
    "contact_gap_queue": [
        "queue_row_id",
        "queue",
        "institution_id",
        "display_name",
        "contact_gap_status",
        "contact_resolution_status",
        "account_resolution_reason",
        "prospect_strength_band",
        "prospect_strength_score",
        "opportunity_urgency_band",
        "known_contact_count",
        "suitable_contact_count",
        "verified_contact_count",
        "equipment_purchase_tender_count",
        "queue_entry_reason",
        "operator_next_action",
        "contact_authorization",
        "outreach_authorization",
    ],
    "line_evidence_review_queue": [
        "queue_row_id",
        "queue",
        "institution_id",
        "display_name",
        "tender_code",
        "coalesced_tender_id",
        "unit_id",
        "unit_decision_id",
        "relevance_class",
        "equipment_scopes",
        "canonical_equipment_classes",
        "line_disposition",
        "line_reason_codes",
        "ambiguity_reason_codes",
        "contact_authorization",
        "outreach_authorization",
    ],
    "retender_review_queue": [
        "queue_row_id",
        "queue",
        "family_id",
        "buyer_key",
        "member_tender_codes",
        "raw_tender_count",
        "confirmed_same_event_family_count",
        "independent_event_count_lower_bound",
        "independent_event_count_upper_bound",
        "unresolved_relationship_count",
        "recurrence_status",
        "family_resolution_status",
        "family_reason_codes",
        "relationship_edges",
        "contact_authorization",
        "outreach_authorization",
    ],
}

# Nested values are serialized canonically so a CSV cell round-trips as JSON.
_NESTED_TYPES = (list, tuple, dict, set, frozenset)


def _cell(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        value = sorted(value)
    if isinstance(value, _NESTED_TYPES):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def canonical_queue_row(queue: str, row: dict[str, Any]) -> dict[str, Any]:
    """Serialize nested cells and stamp a stable row identity."""
    out = {k: _cell(v) for k, v in row.items()}
    out["queue"] = queue
    out["contact_authorization"] = False
    out["outreach_authorization"] = False
    out["queue_row_id"] = queue_row_id(queue, out)
    return out


def queue_row_id(queue: str, row: dict[str, Any]) -> str:
    """Stable id over the queue grain, independent of row ordering."""
    grain_fields = QUEUE_GRAINS.get(queue, "").split(" + ")
    parts = [queue] + [f"{f}={row.get(f) or ''}" for f in grain_fields]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]


def _is_current_opportunity(row: dict[str, Any], line_evidence: int) -> bool:
    """Current opportunity = open lifecycle + catalog-eligible purchase + evidence."""
    lifecycle = row.get("projected_lifecycle_class") or row.get("lifecycle_class")
    if lifecycle not in KNOWN_OPEN:
        return False
    if row.get("review_disposition") not in {
        DISPOSITION_CATALOG_FIT,
        DISPOSITION_POSSIBLE_FIT,
    }:
        return False
    if row.get("commercial_signal_type") != "equipment_purchase_signal":
        return False
    catalog_status = row.get("catalog_fit_status")
    if catalog_status not in {
        "catalog_fit_candidate",
        "possible_fit_needs_specs",
        "catalog_equipment_class_needs_confirmation",
    }:
        return False
    return line_evidence > 0


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
    line_evidence_by_tender: dict[str, int] = {}
    for row in line_rows:
        tid = str(row.get("coalesced_tender_id") or "")
        if row.get("line_disposition") != "excluded":
            line_evidence_by_tender[tid] = line_evidence_by_tender.get(tid, 0) + 1

    current: list[dict[str, Any]] = []
    historical_acc: dict[tuple[str, str, str], dict[str, Any]] = {}
    current_institutions: set[str] = set()
    historical_institutions: set[str] = set()

    for row in sorted(
        tender_rows,
        key=lambda r: (str(r.get("tender_code") or ""), str(r.get("institution_id") or "")),
    ):
        tid = str(row.get("coalesced_tender_id") or "")
        lifecycle = row.get("projected_lifecycle_class") or row.get("lifecycle_class")
        institution_id = str(row.get("institution_id") or "")
        category = row.get("canonical_equipment_category") or ""
        signal = row.get("commercial_signal_type") or row.get(
            "commercial_evidence_signal"
        )
        evidence_count = line_evidence_by_tender.get(tid, 0)
        # A tender with no line units still carries its own title evidence.
        if not line_evidence_by_tender:
            evidence_count = 1

        if _is_current_opportunity(row, evidence_count):
            current_institutions.add(institution_id)
            current.append(
                canonical_queue_row(
                    "current_opportunity",
                    {
                        "institution_id": institution_id,
                        "display_name": row.get("display_name"),
                        "tender_code": row.get("tender_code"),
                        "coalesced_tender_id": tid,
                        "equipment_category": category,
                        "lifecycle_class": lifecycle,
                        "review_disposition": row.get("review_disposition"),
                        "commercial_signal_type": signal,
                        "catalog_fit_status": row.get("catalog_fit_status"),
                        "catalog_match_status": row.get("catalog_match_status"),
                        "opportunity_urgency_band": row.get("opportunity_urgency_band"),
                        "prospect_strength_band": row.get("prospect_strength_band"),
                        "line_evidence_unit_count": evidence_count,
                        "closing_soon_bucket": row.get("closing_soon_bucket"),
                        "publication_timestamp": row.get("publication_timestamp"),
                        "close_timestamp": row.get("close_timestamp"),
                        "reason_codes": row.get("reason_codes") or [],
                        "eligibility_reason_codes": row.get("eligibility_reason_codes")
                        or [],
                    },
                )
            )
        elif lifecycle not in KNOWN_OPEN and signal in {
            "equipment_purchase_signal",
            "installed_base_signal",
            "rental_or_comodato_signal",
        }:
            key = (institution_id, str(category), str(signal))
            entry = historical_acc.setdefault(
                key,
                {
                    "institution_id": institution_id,
                    "display_name": row.get("display_name"),
                    "equipment_category": category,
                    "commercial_signal_type": signal,
                    "tender_count": 0,
                    "tender_codes": [],
                    "first_observed_date": None,
                    "most_recent_observed_date": None,
                    "review_dispositions": [],
                    "prospect_strength_band": row.get("prospect_strength_band"),
                },
            )
            historical_institutions.add(institution_id)
            entry["tender_count"] += 1
            code = row.get("tender_code")
            if code and code not in entry["tender_codes"]:
                entry["tender_codes"].append(code)
            disposition = row.get("review_disposition")
            if disposition and disposition not in entry["review_dispositions"]:
                entry["review_dispositions"].append(disposition)
            observed = row.get("publication_timestamp") or row.get("close_timestamp")
            if observed:
                first = entry["first_observed_date"]
                last = entry["most_recent_observed_date"]
                entry["first_observed_date"] = min(first or observed, observed)
                entry["most_recent_observed_date"] = max(last or observed, observed)

    historical = [
        canonical_queue_row(
            "historical_prospect",
            {
                **entry,
                "tender_codes": sorted(entry["tender_codes"]),
                "review_dispositions": sorted(entry["review_dispositions"]),
            },
        )
        for _key, entry in sorted(historical_acc.items())
    ]

    line_review = [
        canonical_queue_row(
            "line_evidence_review",
            {
                "institution_id": row.get("institution_id"),
                "display_name": row.get("display_name"),
                "tender_code": row.get("tender_code"),
                "coalesced_tender_id": row.get("coalesced_tender_id"),
                "unit_id": row.get("unit_id"),
                "unit_decision_id": row.get("unit_decision_id"),
                "relevance_class": row.get("relevance_class"),
                "equipment_scopes": row.get("equipment_scopes") or [],
                "canonical_equipment_classes": row.get("canonical_equipment_classes")
                or [],
                "line_disposition": row.get("line_disposition"),
                "line_reason_codes": row.get("line_reason_codes") or [],
                "ambiguity_reason_codes": row.get("ambiguity_reason_codes") or [],
            },
        )
        for row in line_rows
        if _is_genuine_line_ambiguity(row)
    ]

    retender_review = [
        canonical_queue_row(
            "retender_review",
            {
                "family_id": fam.get("family_id"),
                "buyer_key": fam.get("buyer_key"),
                "member_tender_codes": fam.get("member_tender_codes") or [],
                "raw_tender_count": fam.get("raw_tender_count"),
                "confirmed_same_event_family_count": fam.get(
                    "confirmed_same_event_family_count"
                ),
                "independent_event_count_lower_bound": fam.get(
                    "independent_event_count_lower_bound"
                ),
                "independent_event_count_upper_bound": fam.get(
                    "independent_event_count_upper_bound"
                ),
                "unresolved_relationship_count": fam.get(
                    "unresolved_relationship_count"
                ),
                "recurrence_status": fam.get("recurrence_status"),
                "family_resolution_status": fam.get("family_resolution_status"),
                "family_reason_codes": fam.get("family_reason_codes") or [],
                "relationship_edges": fam.get("relationship_edges") or [],
            },
        )
        for fam in families
        if fam.get("family_resolution_status") == "retender_review_required"
    ]

    identity_queue = [
        canonical_queue_row(
            "institution_match_review",
            {
                "institution_id": row.get("institution_id"),
                "display_name": row.get("display_name"),
                "institution_review_cluster_id": row.get(
                    "institution_review_cluster_id"
                ),
                "account_resolution_status": row.get("account_resolution_status"),
                "account_resolution_reason": row.get("account_resolution_reason"),
                "identity_kind": row.get("identity_kind"),
                "identity_review_required": row.get("identity_review_required"),
                "operator_next_action": row.get("operator_next_action"),
                "confirmed_account": False,
            },
        )
        for row in match_review
    ]
    for cluster in clusters:
        identity_queue.append(
            canonical_queue_row(
                "institution_match_review",
                {
                    "institution_review_cluster_id": cluster.get(
                        "institution_review_cluster_id"
                    ),
                    "cluster_resolution_status": cluster.get(
                        "cluster_resolution_status"
                    ),
                    "member_profile_ids": cluster.get("member_profile_ids") or [],
                    "identifier_conflicts": cluster.get("identifier_conflicts") or [],
                    "cluster_reason_codes": cluster.get("cluster_reason_codes") or [],
                    "confirmed_account": False,
                },
            )
        )

    # Contact gaps only matter for institutions the operator would actually work.
    relevant_institutions = current_institutions | historical_institutions
    contact_gap_rows = [
        canonical_queue_row(
            "contact_gap",
            {
                **{k: v for k, v in gap.items() if k != "queue"},
                "queue_entry_reason": (
                    "current_catalog_opportunity"
                    if str(gap.get("institution_id") or "") in current_institutions
                    else "relevant_historical_prospect"
                ),
            },
        )
        for gap in contact_gaps
        if str(gap.get("institution_id") or "") in relevant_institutions
    ]

    def _sort_rows(rows: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: tuple(str(r.get(k) or "") for k in keys))

    return {
        "current_opportunity_queue": _sort_rows(
            current, "tender_code", "institution_id", "equipment_category"
        ),
        "historical_prospect_queue": _sort_rows(
            historical, "institution_id", "equipment_category", "commercial_signal_type"
        ),
        "institution_match_review_queue": _sort_rows(
            identity_queue, "institution_id", "institution_review_cluster_id"
        ),
        "contact_gap_queue": _sort_rows(contact_gap_rows, "institution_id"),
        "line_evidence_review_queue": _sort_rows(
            line_review, "tender_code", "unit_id"
        ),
        "retender_review_queue": _sort_rows(retender_review, "family_id"),
    }


def _is_genuine_line_ambiguity(row: dict[str, Any]) -> bool:
    """Only unresolved evidence enters line review, not every non-purchase line."""
    if row.get("line_disposition") != "review_required":
        return False
    scopes = row.get("equipment_scopes") or []
    if scopes and set(scopes) - {SCOPE_AMBIGUOUS}:
        # Every clause resolved to a real commercial scope — nothing to review.
        return SCOPE_AMBIGUOUS in scopes
    return True


__all__ = [
    "EMPTY_QUEUE_HEADERS",
    "QUEUE_GRAINS",
    "build_operator_queues",
    "canonical_queue_row",
    "queue_row_id",
]
