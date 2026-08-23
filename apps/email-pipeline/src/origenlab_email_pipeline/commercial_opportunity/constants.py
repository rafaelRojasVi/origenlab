"""Constants for the commercial opportunity stage read model (PR3).

Identity does not prove an opportunity.
Historical commercial evidence does not prove a current stage.
A current stage requires dated, typed, defensible evidence.
Cumulative counts never prove fulfillment or purchase pending.
Tender evidence remains a separate procurement dimension.
PR3 does not generate operator next actions.
"""

from __future__ import annotations

from typing import Final

from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
    RUN_CONTEXT_PRODUCTION_APPLY,
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
    RUN_CONTEXT_SYNTHETIC_FIXTURE,
    VALID_RUN_CONTEXTS,
)

SCHEMA_VERSION: Final = "commercial_opportunity_v1"
# Resolver semantics v2: causal supplier fulfillment gating, source-side deal
# classification, compatible late client prerequisites, same-stage latest-UTC wins.
# Schema is additive. Production apply is operator-triggered only (not part of
# daily-core automation) and always requires a current, matching PR2 identity
# snapshot — see operator_cli build-commercial-identity /
# build-commercial-opportunity / refresh-commercial-opportunity-models.
BUILD_CONTRACT: Final = "opportunity_stage_read_model_v2"
TRANSACTION_CONTRACT: Final = "B_schema_additive_data_atomic"

# Required PR2 identity schema for apply gating.
REQUIRED_IDENTITY_SCHEMA_VERSION: Final = "commercial_identity_v1"

RECORD_KIND_EXPLICIT: Final = "explicit_opportunity"
RECORD_KIND_EVIDENCE_CANDIDATE: Final = "evidence_candidate"
RECORD_KIND_HISTORY: Final = "commercial_history"

CANONICAL_STAGES: Final = frozenset(
    {
        "qualifying",
        "quote_requested",
        "quote_preparing",
        "quote_sent",
        "technical_review",
        "purchase_pending",
        "won",
        "fulfillment",
        "post_sale",
        "lost",
        "commercial_history",
        "unknown",
    }
)

TERMINAL_STAGES: Final = frozenset({"lost", "post_sale", "won"})

# Hard terminals cannot be overwritten by later non-terminal or alternate terminal evidence
# except same-timestamp conflict handling. ``won`` is lifecycle-terminal for currentness
# but may still be refined to fulfillment by dated supplier/logistics evidence.
HARD_TERMINAL_STAGES: Final = frozenset({"lost", "post_sale"})


# Higher = further along the commercial lifecycle (non-terminal progression).
STAGE_RANK: Final[dict[str, int]] = {
    "unknown": 0,
    "commercial_history": 5,
    "qualifying": 10,
    "quote_requested": 20,
    "quote_preparing": 30,
    "quote_sent": 40,
    "technical_review": 50,
    "purchase_pending": 60,
    "won": 70,
    "fulfillment": 80,
    "post_sale": 90,
    "lost": 100,
}

# commercial_deal.deal_status → (canonical_stage, is_lifecycle_terminal)
# ``closed`` alone is unknown — resolver may promote with supporting evidence.
DEAL_STATUS_STAGE_MAP: Final[dict[str, tuple[str, bool]]] = {
    "draft": ("quote_preparing", False),
    "quoted": ("quote_sent", False),
    "client_po_received": ("purchase_pending", False),
    "client_invoiced": ("purchase_pending", False),
    "client_paid": ("won", True),
    "supplier_po_sent": ("fulfillment", False),
    "supplier_invoiced": ("fulfillment", False),
    "supplier_paid": ("fulfillment", False),
    "logistics_pending": ("fulfillment", False),
    "in_transit": ("fulfillment", False),
    "delivered": ("post_sale", True),
    "cancelled": ("lost", True),
    "needs_review": ("unknown", False),
    "closed": ("unknown", False),
}

# Deterministic source-side for commercial_deal.deal_status (never inferred from stage alone).
# Supplier statuses must not satisfy the client-commitment gate.
DEAL_STATUS_SOURCE_SIDE: Final[dict[str, str]] = {
    "draft": "client",
    "quoted": "client",
    "client_po_received": "client",
    "client_invoiced": "client",
    "client_paid": "client",
    "cancelled": "client",
    "delivered": "client",
    "needs_review": "client",
    "closed": "client",
    "supplier_po_sent": "supplier",
    "supplier_invoiced": "supplier",
    "supplier_paid": "supplier",
    "logistics_pending": "supplier",
    "in_transit": "supplier",
}

SOURCE_SIDE_CLIENT: Final = "client"
SOURCE_SIDE_SUPPLIER: Final = "supplier"

# Dated client-side evidence mapped to these stages counts as client commitment.
CLIENT_COMMITMENT_STAGES: Final = frozenset({"purchase_pending", "won"})

# Stages that are compatible late prerequisites when fulfillment is already selected.
# Later client commitment/payment after fulfillment is corroboration, not regression.
FULFILLMENT_COMPATIBLE_LATE_CLIENT_STAGES: Final = frozenset(
    {"purchase_pending", "won"}
)

# Evidence that may select fulfillment after causal commitment (never supplier_proforma alone).
FULFILLMENT_EXECUTION_SOURCE_STAGES: Final = frozenset(
    {
        # deal_status
        "supplier_po_sent",
        "supplier_invoiced",
        "supplier_paid",
        "logistics_pending",
        "in_transit",
        # events
        "supplier_invoice_received",
        "supplier_payment_sent",
        "supplier_payment_confirmed",
        "shipment_released",
        "delivery_estimate_communicated",
        # documents
        "supplier_po",
        "supplier_invoice",
        "logistics_doc",
        # payments
        "payment_outbound",
    }
)

# supplier_proforma is provenance/supporting only — never stage-bearing fulfillment.
SUPPLIER_PROFORMA_DOCUMENT_TYPE: Final = "supplier_proforma"

EVIDENCE_REASON_SUPPLIER_WITHHELD_BEFORE_COMMITMENT: Final = (
    "supplier_stage_withheld_before_client_commitment"
)

# commercial_deal_event.event_type → (canonical_stage | None, is_terminal, client_side)
EVENT_TYPE_STAGE_MAP: Final[dict[str, tuple[str | None, bool, bool]]] = {
    "deal_created": ("qualifying", False, True),
    "client_quote_sent": ("quote_sent", False, True),
    "client_po_received": ("purchase_pending", False, True),
    "client_invoice_sent": ("purchase_pending", False, True),
    "client_payment_received": ("won", True, True),
    "client_bank_details_requested": ("purchase_pending", False, True),
    "supplier_po_sent": ("fulfillment", False, False),
    "supplier_invoice_received": ("fulfillment", False, False),
    "supplier_payment_sent": ("fulfillment", False, False),
    "supplier_payment_confirmed": ("fulfillment", False, False),
    "logistics_pending": ("fulfillment", False, False),
    "shipment_released": ("fulfillment", False, False),
    "delivery_estimate_communicated": ("fulfillment", False, False),
    "delivered": ("post_sale", True, True),
    "deal_closed": ("unknown", False, True),  # requires supporting evidence
    "deal_cancelled": ("lost", True, True),
    "margin_review_requested": (None, False, True),
    "note": (None, False, True),
}

# Documents: payment_* never establish won without direction-aware payment/event.
# supplier_proforma is provenance only — never stage-bearing fulfillment.
DOCUMENT_TYPE_STAGE_MAP: Final[dict[str, tuple[str | None, bool, bool]]] = {
    "client_quote": ("quote_sent", False, True),
    "client_po": ("purchase_pending", False, True),
    "client_invoice": ("purchase_pending", False, True),
    "supplier_po": ("fulfillment", False, False),
    "supplier_proforma": (None, False, False),
    "supplier_invoice": ("fulfillment", False, False),
    "payment_voucher": (None, False, True),
    "payment_confirmation": (None, False, True),
    "logistics_doc": ("fulfillment", False, False),
    "other": (None, False, True),
}

# Statuses that claim terminality but without a usable timestamp cannot prove it.
UNDATED_TERMINAL_SOURCE_STATUSES: Final = frozenset(
    {
        "client_paid",
        "delivered",
        "cancelled",
        "closed",
        "client_payment_received",
        "deal_cancelled",
        "payment_inbound",
    }
)

CONFIDENCE_RANK: Final[dict[str, int]] = {
    "operator_confirmed": 40,
    "extracted_high": 30,
    "derived_high_confidence": 28,
    "derived_medium_confidence": 20,
    "extracted_low": 10,
    "needs_review": 5,
    "unavailable": 0,
    "historical": 2,
}

IDENTITY_LINK_LINKED: Final = "linked"
IDENTITY_LINK_UNRESOLVED: Final = "unresolved"
IDENTITY_LINK_AMBIGUOUS: Final = "ambiguous"
IDENTITY_LINK_WITHHELD: Final = "withheld"
IDENTITY_LINK_NOT_APPLICABLE: Final = "not_applicable"
# Opportunity account linked via explicit deal institutional domain while contact
# membership remains withheld (consumer email). Does not change PR2 membership.
IDENTITY_LINK_ACCOUNT_VIA_DEAL_DOMAIN: Final = "account_via_deal_domain"

REVIEW_STATUS_OK: Final = "ok"
REVIEW_STATUS_REQUIRED: Final = "needs_review"

CONFLICT_REASON_CODES: Final = frozenset(
    {
        "opportunity_identity_unresolved",
        "opportunity_identity_ambiguous",
        "deal_contact_account_mismatch",
        "duplicate_deal_key_evidence",
        "source_event_missing_timestamp",
        "malformed_event_timestamp",
        "conflicting_terminal_events",
        "stage_regression_prevented",
        "same_timestamp_stage_conflict",
        "unsupported_source_stage",
        "undated_signal_history_only",
        "undated_terminal_unproven",
        "stale_or_missing_identity_snapshot",
        "stale_build_plan",
        "supplier_only_not_client_opportunity",
        "source_schema_incompatible",
        "closed_without_supporting_evidence",
        "deal_status_without_usable_timestamp",
    }
)

# Subset of CONFLICT_REASON_CODES that force review_status=needs_review.
REVIEW_REQUIRING_CONFLICT_REASONS: Final = frozenset(
    {
        "opportunity_identity_unresolved",
        "opportunity_identity_ambiguous",
        "deal_contact_account_mismatch",
        "duplicate_deal_key_evidence",
        "source_event_missing_timestamp",
        "malformed_event_timestamp",
        "conflicting_terminal_events",
        "stage_regression_prevented",
        "same_timestamp_stage_conflict",
        "unsupported_source_stage",
        "undated_signal_history_only",
        "undated_terminal_unproven",
        "stale_or_missing_identity_snapshot",
        "stale_build_plan",
        "supplier_only_not_client_opportunity",
        "source_schema_incompatible",
        "closed_without_supporting_evidence",
        "deal_status_without_usable_timestamp",
    }
)

REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION: Final = "identity_fp_v2"
OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION: Final = "opportunity_source_fp_v1"


REBUILDABLE_DATA_TABLES: Final = (
    "commercial_opportunity_conflict",
    "commercial_opportunity_evidence",
    "commercial_opportunity_event",
    "commercial_opportunity",
    "commercial_opportunity_build_meta",
)

__all__ = [
    "SCHEMA_VERSION",
    "BUILD_CONTRACT",
    "TRANSACTION_CONTRACT",
    "REQUIRED_IDENTITY_SCHEMA_VERSION",
    "RECORD_KIND_EXPLICIT",
    "RECORD_KIND_EVIDENCE_CANDIDATE",
    "RECORD_KIND_HISTORY",
    "CANONICAL_STAGES",
    "TERMINAL_STAGES",
    "HARD_TERMINAL_STAGES",
    "STAGE_RANK",
    "DEAL_STATUS_STAGE_MAP",
    "DEAL_STATUS_SOURCE_SIDE",
    "SOURCE_SIDE_CLIENT",
    "SOURCE_SIDE_SUPPLIER",
    "CLIENT_COMMITMENT_STAGES",
    "FULFILLMENT_COMPATIBLE_LATE_CLIENT_STAGES",
    "FULFILLMENT_EXECUTION_SOURCE_STAGES",
    "SUPPLIER_PROFORMA_DOCUMENT_TYPE",
    "EVIDENCE_REASON_SUPPLIER_WITHHELD_BEFORE_COMMITMENT",
    "EVENT_TYPE_STAGE_MAP",
    "DOCUMENT_TYPE_STAGE_MAP",
    "UNDATED_TERMINAL_SOURCE_STATUSES",
    "CONFIDENCE_RANK",
    "IDENTITY_LINK_LINKED",
    "IDENTITY_LINK_UNRESOLVED",
    "IDENTITY_LINK_AMBIGUOUS",
    "IDENTITY_LINK_WITHHELD",
    "IDENTITY_LINK_NOT_APPLICABLE",
    "IDENTITY_LINK_ACCOUNT_VIA_DEAL_DOMAIN",
    "REVIEW_STATUS_OK",
    "REVIEW_STATUS_REQUIRED",
    "CONFLICT_REASON_CODES",
    "REVIEW_REQUIRING_CONFLICT_REASONS",
    "REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION",
    "OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION",
    "REBUILDABLE_DATA_TABLES",
    "RUN_CONTEXT_SYNTHETIC_FIXTURE",
    "RUN_CONTEXT_LOCAL_FIXTURE",
    "RUN_CONTEXT_PRODUCTION_DRY_RUN",
    "RUN_CONTEXT_PRODUCTION_APPLY",
    "VALID_RUN_CONTEXTS",
]
