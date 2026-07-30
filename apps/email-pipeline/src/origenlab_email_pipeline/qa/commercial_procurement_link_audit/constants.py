"""Constants for the PR4 procurement ↔ account-link audit (read-only)."""

from __future__ import annotations

from typing import Final

AUDIT_NAME: Final = "commercial_procurement_link_audit"
AUDIT_VERSION: Final = "pr4_audit_v1"
DEFAULT_REPORT_ROOT_REL: Final = "reports/out"
DEFAULT_OUTPUT_DIRNAME: Final = "commercial_procurement_link_audit_2026-07-30"

SOURCE_CHILECOMPRA: Final = "chilecompra"
MARKETPLACE_DOMAINS: Final = frozenset(
    {
        "mercadopublico.cl",
        "www.mercadopublico.cl",
        "chilecompra.cl",
        "www.chilecompra.cl",
        "api.mercadopublico.cl",
        "api.chilecompra.cl",
    }
)

# Procurement context vocabulary (independent of PR3 canonical_stage).
PROCUREMENT_CONTEXT_NONE: Final = "none"
PROCUREMENT_CONTEXT_TENDER_WATCH: Final = "tender_watch"
PROCUREMENT_CONTEXT_TENDER_ACTIVE: Final = "tender_active"
PROCUREMENT_CONTEXT_HISTORICAL: Final = "historical_tender"
PROCUREMENT_CONTEXT_UNKNOWN: Final = "unknown"

PROCUREMENT_CONTEXTS: Final = frozenset(
    {
        PROCUREMENT_CONTEXT_NONE,
        PROCUREMENT_CONTEXT_TENDER_WATCH,
        PROCUREMENT_CONTEXT_TENDER_ACTIVE,
        PROCUREMENT_CONTEXT_HISTORICAL,
        PROCUREMENT_CONTEXT_UNKNOWN,
    }
)

# Link-route codes for the audit / proposed resolver.
ROUTE_EXACT_INSTITUTIONAL_DOMAIN: Final = "A_exact_institutional_domain"
ROUTE_EXACT_CANONICAL_NAME: Final = "B_exact_canonical_name"
ROUTE_EXACT_ALIAS: Final = "C_exact_alias"
ROUTE_UNIQUE_COMPATIBLE_NAME: Final = "D_unique_compatible_name"
ROUTE_EXPLICIT_EMAIL_DOMAIN: Final = "E_explicit_email_domain"
ROUTE_NO_MATCH: Final = "F_no_match"
ROUTE_AMBIGUOUS_MULTI_ACCOUNT: Final = "G_ambiguous_multiple_accounts"
ROUTE_DOMAIN_REFUSED: Final = "H_consumer_internal_marketplace_refused"
ROUTE_NAME_DOMAIN_CONFLICT: Final = "I_name_domain_conflict"

LINK_ROUTES: Final = frozenset(
    {
        ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
        ROUTE_EXACT_CANONICAL_NAME,
        ROUTE_EXACT_ALIAS,
        ROUTE_UNIQUE_COMPATIBLE_NAME,
        ROUTE_EXPLICIT_EMAIL_DOMAIN,
        ROUTE_NO_MATCH,
        ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
        ROUTE_DOMAIN_REFUSED,
        ROUTE_NAME_DOMAIN_CONFLICT,
    }
)

CONFIDENCE_HIGH: Final = "high"
CONFIDENCE_MEDIUM: Final = "medium"
CONFIDENCE_LOW: Final = "low"
CONFIDENCE_NONE: Final = "none"

# Enrichment / conflict reason codes (proposed PR4 policy).
REASON_BUYER_ACCOUNT_NOT_FOUND: Final = "buyer_account_not_found"
REASON_BUYER_NAME_AMBIGUOUS: Final = "buyer_name_ambiguous"
REASON_BUYER_DOMAIN_MISSING: Final = "buyer_domain_missing"
REASON_BUYER_DOMAIN_CONFLICTS_WITH_NAME: Final = "buyer_domain_conflicts_with_name"
REASON_BUYER_CONTACT_MISSING: Final = "buyer_contact_missing"
REASON_CONSUMER_EMAIL_LINK_WITHHELD: Final = "consumer_email_link_withheld"
REASON_MARKETPLACE_DOMAIN_IGNORED: Final = "marketplace_domain_ignored"
REASON_INTERNAL_DOMAIN_REFUSED: Final = "internal_domain_refused"
REASON_TENDER_IDENTIFIER_MISSING: Final = "tender_identifier_missing"
REASON_TENDER_STATUS_UNKNOWN: Final = "tender_status_unknown"
REASON_TENDER_DATES_MISSING_OR_MALFORMED: Final = "tender_dates_missing_or_malformed"
REASON_DUPLICATE_SOURCE_RECORDS_NEED_REVIEW: Final = "duplicate_source_records_need_review"
REASON_LINE_ITEM_COLLAPSED: Final = "line_items_coalesced_to_tender"
REASON_WEAK_PUBLIC_UNIT_NAME: Final = "weak_generic_public_unit_name"

# ChileCompra status codes (Mercado Público API / file).
ACTIVE_STATUS_CODE: Final = "5"
INACTIVE_STATUS_CODES: Final = frozenset({"6", "7", "8", "18", "19"})
INACTIVE_STATUS_NAMES: Final = frozenset(
    {
        "cerrada",
        "desierta",
        "adjudicada",
        "revocada",
        "suspendida",
    }
)

# Proposed fingerprint algorithm version (design only — not applied).
PROCUREMENT_SOURCE_FP_ALGORITHM: Final = "procurement_source_fp_v1"
SCHEMA_VERSION_PROPOSED: Final = "commercial_procurement_v1"
BUILD_CONTRACT_PROPOSED: Final = "procurement_account_link_read_model_v1"
TRANSACTION_CONTRACT: Final = "B_schema_additive_data_atomic"

# Weak/generic public-unit name tokens that should not auto-link on name alone.
WEAK_NAME_TOKENS: Final = frozenset(
    {
        "municipalidad",
        "municipio",
        "servicio de salud",
        "hospital",
        "seremi",
        "direccion",
        "dirección",
        "departamento",
        "unidad",
        "gobierno",
        "ministerio",
    }
)
