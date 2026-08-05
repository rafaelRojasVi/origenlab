"""Field-sufficiency inventory for PR5D product-text evidence."""

from __future__ import annotations

from typing import Any, Final

from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (
    PRODUCT_TEXT_ADAPTER_VERSION,
)

FIELD_SUFFICIENCY_INVENTORY: Final[list[dict[str, Any]]] = [
    {
        "field": "title / title_raw / title_selected",
        "sources": ["PR4.signal.title", "PR5B.tender.title", "PR5C.evidence.title_raw"],
        "available_in_pr5c_output": True,
        "safe_for_relevance": True,
        "required_for_meaningful_relevance": False,
        "notes": (
            "Tender-level only. Insufficient alone for SKU certainty or mixed-line "
            "disambiguation. Traceable via evidence_ref_id."
        ),
        "traceable_to_evidence_ref": True,
    },
    {
        "field": "tender.description",
        "sources": ["PR5B.ProcurementTenderObservation.description"],
        "available_in_pr5c_output": False,
        "safe_for_relevance": True,
        "required_for_meaningful_relevance": False,
        "notes": (
            "Dropped by PR5C snapshot_to_evidence. Recoverable by re-reading "
            "immutable AcquisitionSnapshot via snapshot_id + observation_id."
        ),
        "traceable_to_evidence_ref": "indirect_via_snapshot_id",
    },
    {
        "field": "line.product / line.description / line.category",
        "sources": ["PR5B.ProcurementLineObservation"],
        "available_in_pr5c_output": False,
        "safe_for_relevance": True,
        "required_for_meaningful_relevance": True,
        "notes": (
            "Best product-bearing fields. Not on PR5C models. PR5D adapter "
            "re-materializes snapshots without changing PR5C identity/lifecycle."
        ),
        "traceable_to_evidence_ref": "indirect_via_snapshot_id",
    },
    {
        "field": "line.unspsc_or_classification / additional_classifications",
        "sources": ["PR5B.ProcurementLineObservation"],
        "available_in_pr5c_output": False,
        "safe_for_relevance": "supporting_only",
        "required_for_meaningful_relevance": False,
        "notes": "UNSPSC/nivel_* are optional aliases, not SKU truth.",
        "traceable_to_evidence_ref": "indirect_via_snapshot_id",
    },
    {
        "field": "line.quantity / unit",
        "sources": ["PR5B.ProcurementLineObservation"],
        "available_in_pr5c_output": False,
        "safe_for_relevance": "supporting_only",
        "required_for_meaningful_relevance": False,
        "notes": "Scale hints only; not product identity.",
        "traceable_to_evidence_ref": "indirect_via_snapshot_id",
    },
    {
        "field": "constituent_source_ids",
        "sources": ["PR4.signal", "PR5C.evidence"],
        "available_in_pr5c_output": True,
        "safe_for_relevance": "pointer_only",
        "required_for_meaningful_relevance": False,
        "notes": (
            "Pointers into external_leads_raw.raw_json. PR5D may optionally load "
            "read-only; first slice treats missing raw load as typed unresolved "
            "when title-only is empty."
        ),
        "traceable_to_evidence_ref": True,
    },
    {
        "field": "PR4 detail_json / raw ChileCompra payload",
        "sources": ["external_leads_raw", "PR4 evidence detail_json"],
        "available_in_pr5c_output": False,
        "safe_for_relevance": "lower_confidence",
        "required_for_meaningful_relevance": False,
        "notes": (
            "Not normalized like PR5B lines. Source-specific. Optional enrichment "
            "path; not required when title_selected exists."
        ),
        "traceable_to_evidence_ref": "via_constituent_source_ids",
    },
]


RECONCILIATION_EQUATIONS: Final[dict[str, str]] = {
    "tenders": "coalesced_tenders = tenders_with_relevance_decisions",
    "units": (
        "all_extracted_product_text_units = linked_evidence_units "
        "+ typed_unresolved_evidence_units"
    ),
    "empty_text_policy": (
        "tender_with_no_usable_product_text → ambiguous/insufficient_evidence "
        "(never silent unrelated)"
    ),
}


def field_sufficiency_document() -> dict[str, Any]:
    available = [f for f in FIELD_SUFFICIENCY_INVENTORY if f["available_in_pr5c_output"]]
    missing = [
        f for f in FIELD_SUFFICIENCY_INVENTORY if not f["available_in_pr5c_output"]
    ]
    required = [
        f
        for f in FIELD_SUFFICIENCY_INVENTORY
        if f.get("required_for_meaningful_relevance") is True
    ]
    return {
        "adapter_version": PRODUCT_TEXT_ADAPTER_VERSION,
        "conclusion": (
            "PR5C exposes title-level product text only. Meaningful line-level "
            "relevance requires a lossless PR5D adapter that re-reads immutable "
            "PR5B AcquisitionSnapshot artifacts by snapshot_id without changing "
            "PR5C identity or lifecycle semantics. Adapter is feasible."
        ),
        "lossless_adapter_feasible_without_pr5c_change": True,
        "inventory": list(FIELD_SUFFICIENCY_INVENTORY),
        "available_in_pr5c_output": [f["field"] for f in available],
        "missing_from_pr5c_output": [f["field"] for f in missing],
        "required_for_meaningful_relevance": [f["field"] for f in required],
        "reconciliation_equations": dict(RECONCILIATION_EQUATIONS),
        "adapter_join_path": [
            "coalesced_tender_id",
            "evidence_ref_id",
            "snapshot_id + observation_id",
            "tender_observation (title, description)",
            "line_observations (product, description, category, …)",
        ],
    }
