"""Taxonomies and CSV field lists for the commercial truth audit."""

from __future__ import annotations

from typing import Final

AUDIT_VERSION: Final = "commercial_truth_audit_v1"

# Existing production commercial action buckets (observed, not redesigned).
EXISTING_BUCKETS: Final = frozenset(
    {
        "ready_to_contact",
        "needs_email_enrichment",
        "tender_opportunity",
        "review_history",
        "already_contacted",
        "blocked",
    }
)

# Audit-only candidate dimensions (never written to production tables).
RELATIONSHIP_STATES: Final = (
    "net_new",
    "known_labdelivery",
    "known_origenlab",
    "existing_customer",
    "dormant_customer",
    "public_buyer",
    "supplier_or_internal",
    "unknown",
)

COMMERCIAL_STAGES: Final = (
    "database_only",
    "research_needed",
    "contact_planned",
    "contacted_no_reply",
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
    "tender_watch",
    "tender_active",
    "unknown",
)

SAFETY_STATES: Final = (
    "eligible",
    "same_domain_review",
    "previously_contacted",
    "bounced",
    "suppressed",
    "blocked",
    "unknown",
)

# already_contacted breakdown (audit-only).
ALREADY_CONTACTED_BREAKDOWN: Final = (
    "campaign_recipient_only",
    "active_inquiry",
    "quotation_related",
    "purchase_pending",
    "existing_customer",
    "fulfillment_or_post_sale",
    "dormant",
    "undetermined",
)

CONSUMER_EMAIL_DOMAINS: Final = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "live.cl",
        "yahoo.com",
        "yahoo.es",
        "icloud.com",
        "me.com",
        "msn.com",
        "protonmail.com",
        "gmx.com",
        "mail.com",
    }
)

INTERNAL_DOMAINS: Final = frozenset({"origenlab.cl", "labdelivery.cl"})

PRODUCT_CATEGORY_PATTERNS: Final = {
    "centrifuges": ("centrifug", "ultracentrifug", "rotor"),
    "sonicators": ("sonicat", "ultrason", "homogeniz"),
    "mills_sample_prep": ("molino", "mill", "molienda", "sample prep", "preparacion de muestra"),
    "uvvis_analytical": ("uv-vis", "uvvis", "espectrofotometr", "spectrophotometr", "analytical"),
    "reactives_electrophoresis": (
        "electrophoresis",
        "electroforesis",
        "reactivo",
        "reactive",
        "gel doc",
    ),
    "agro_food_dairy_vet": (
        "agro",
        "agricultur",
        "alimento",
        "food",
        "dairy",
        "lacteo",
        "veterinar",
        "pecuaria",
    ),
    "public_labs_universities": (
        "universidad",
        "university",
        "instituto publico",
        "laboratorio publico",
        "minsal",
        "isp.",
        "seremi",
    ),
}

SOURCE_NAMES: Final = (
    "lead_research_prospect",
    "contact_master",
    "organization_master",
    "emails_canonical_gmail",
    "emails_legacy_labdelivery",
    "outreach_contact_state",
    "contact_email_suppression",
    "contact_domain_suppression",
    "commercial_contact_signal_rollup",
    "commercial_opportunity_fact",
    "commercial_deal",
    "commercial_purchase_events",
    "external_leads_raw",
    "lead_research_batch",
)

SOURCE_INVENTORY_FIELDS: Final = [
    "source_name",
    "table_present",
    "total_rows",
    "unique_emails",
    "unique_domains",
    "unique_organizations",
    "contacts_with_name",
    "contacts_with_role",
    "contacts_with_product_interest",
    "contacts_with_gmail_evidence",
    "contacts_with_labdelivery_evidence",
    "contacts_with_quotation_evidence",
    "contacts_with_purchase_invoice_order_evidence",
    "contacts_with_tender_evidence",
    "contacts_blocked_bounced_suppressed",
    "contacts_no_usable_email",
    "last_source_refresh_timestamp",
    "notes",
]

SUMMARY_HEADLINE_KEYS: Final = [
    "prospect_rows",
    "already_contacted_count",
    "already_contacted_campaign_recipient_only_pct",
    "already_contacted_active_inquiry_pct",
    "already_contacted_quotation_related_pct",
    "already_contacted_purchase_pending_pct",
    "already_contacted_existing_customer_pct",
    "already_contacted_fulfillment_or_post_sale_pct",
    "already_contacted_dormant_pct",
    "already_contacted_undetermined_pct",
    "open_thread_without_next_action_count",
    "sent_only_treated_as_opportunity_count",
    "active_cases_hidden_in_generic_buckets_count",
    "hard_bounce_leakage_rate",
    "duplicate_recipient_rate",
    "suppressed_recipient_leakage",
    "labdelivery_unique_contacts",
    "labdelivery_unique_orgs",
    "tender_rows_linked",
    "batch_readiness_categories_with_evidence",
]
