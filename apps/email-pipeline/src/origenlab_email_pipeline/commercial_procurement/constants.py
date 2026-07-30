"""Constants for the PR4 procurement ↔ account-link audit (read-only)."""

from __future__ import annotations

from typing import Final

AUDIT_NAME: Final = "commercial_procurement_link_audit"
AUDIT_VERSION: Final = "pr4_audit_v3"
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

# Verified tender-level key kinds (canonical procurement grain).
TENDER_KEY_CODIGO_EXTERNO: Final = "codigo_externo"
TENDER_KEY_CODIGO_LICITACION: Final = "codigo_licitacion"
TENDER_KEY_NUMERO_ADQUISICION: Final = "numero_adquisicion"
TENDER_KEY_UNRESOLVED: Final = "unresolved_tender_key"
TENDER_KEY_MISSING: Final = "missing"

VERIFIED_TENDER_KEY_KINDS: Final = frozenset(
    {
        TENDER_KEY_CODIGO_EXTERNO,
        TENDER_KEY_CODIGO_LICITACION,
        TENDER_KEY_NUMERO_ADQUISICION,
    }
)

# Observed raw JSON key inventory labels (audit metrics).
RAW_KEY_INVENTORY_LABELS: Final = (
    "CodigoExterno",
    "codigo_externo",
    "CodigoLicitacion",
    "codigo_licitacion",
    "Número de Adquisición",
    "Numero de Adquisicion",
    "numero_adquisicion",
    "Codigo",
    "Correlativo",
    "source_record_id_fallback",
    "missing",
)

# Link-route codes for the audit / proposed resolver.
ROUTE_EXACT_INSTITUTIONAL_DOMAIN: Final = "A_exact_institutional_domain"
ROUTE_EXACT_CANONICAL_NAME: Final = "B_exact_canonical_name"
ROUTE_EXACT_ALIAS: Final = "C_exact_alias"
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
        ROUTE_EXPLICIT_EMAIL_DOMAIN,
        ROUTE_NO_MATCH,
        ROUTE_AMBIGUOUS_MULTI_ACCOUNT,
        ROUTE_DOMAIN_REFUSED,
        ROUTE_NAME_DOMAIN_CONFLICT,
    }
)

# Account-resolution status (one row per verified procurement signal).
RESOLUTION_LINKED: Final = "linked"
RESOLUTION_UNLINKED: Final = "unlinked"
RESOLUTION_AMBIGUOUS: Final = "ambiguous"
RESOLUTION_REFUSED: Final = "refused"

RESOLUTION_STATUSES: Final = frozenset(
    {
        RESOLUTION_LINKED,
        RESOLUTION_UNLINKED,
        RESOLUTION_AMBIGUOUS,
        RESOLUTION_REFUSED,
    }
)

LINKED_ROUTES: Final = frozenset(
    {
        ROUTE_EXACT_INSTITUTIONAL_DOMAIN,
        ROUTE_EXACT_CANONICAL_NAME,
        ROUTE_EXACT_ALIAS,
        ROUTE_EXPLICIT_EMAIL_DOMAIN,
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
REASON_CONSUMER_EMAIL_IGNORED_FOR_ACCOUNT_IDENTITY: Final = (
    "consumer_email_ignored_for_account_identity"
)
REASON_MARKETPLACE_DOMAIN_IGNORED: Final = "marketplace_domain_ignored"
REASON_INTERNAL_DOMAIN_REFUSED: Final = "internal_domain_refused"
REASON_INTERNAL_DOMAIN_IGNORED_FOR_ACCOUNT_IDENTITY: Final = (
    "internal_domain_ignored_for_account_identity"
)
REASON_TENDER_IDENTIFIER_MISSING: Final = "tender_identifier_missing"
REASON_TENDER_KEY_UNRESOLVED: Final = "tender_key_unresolved_line_or_fallback"
REASON_TENDER_STATUS_UNKNOWN: Final = "tender_status_unknown"
REASON_TENDER_DATES_MISSING_OR_MALFORMED: Final = "tender_dates_missing_or_malformed"
REASON_DUPLICATE_SOURCE_RECORDS_NEED_REVIEW: Final = "duplicate_source_records_need_review"
REASON_LINE_FIELD_CONFLICT: Final = "line_field_conflict_across_tender_lines"
REASON_LINE_ITEMS_COALESCED: Final = "line_items_coalesced_to_tender"
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

# Fingerprint / build contracts (design only — not applied).
PROCUREMENT_SOURCE_FP_ALGORITHM: Final = "procurement_source_fp_v1"
PROCUREMENT_BUILD_PLAN_FP_ALGORITHM: Final = "procurement_build_plan_fp_v1"
# Semantic stale-plan identity (excludes execution metadata / wall-clock fields).
PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM: Final = "procurement_semantic_plan_digest_v1"
# Optional digest over exact rows after one explicit materialization timestamp.
PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM: Final = (
    "procurement_materialization_digest_v1"
)
RESOLVER_BUILD_CONTRACT_VERSION: Final = "procurement_resolver_v3"

# Evidence subject kinds (fail-closed validation enum).
EVIDENCE_SUBJECT_KINDS: Final = frozenset(
    {
        "signal",
        "resolution",
        "conflict",
        "enrichment",
        "unresolved_source",
        "line_conflict",
        "resolution_conflict",
    }
)

SEMANTIC_PLAN_TABLES: Final = (
    "commercial_procurement_signal",
    "commercial_procurement_account_resolution",
    "commercial_procurement_evidence",
    "commercial_procurement_conflict",
    "commercial_procurement_enrichment_candidate",
)
SCHEMA_VERSION: Final = "commercial_procurement_v1"
BUILD_CONTRACT: Final = "procurement_account_resolution_read_model_v1"
# Back-compat aliases used by older audit imports
SCHEMA_VERSION_PROPOSED: Final = SCHEMA_VERSION
BUILD_CONTRACT_PROPOSED: Final = BUILD_CONTRACT
TRANSACTION_CONTRACT: Final = "B_schema_additive_data_atomic"
REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM: Final = "identity_fp_v2"
AS_OF_TIMEZONE: Final = "UTC_calendar_date"
AS_OF_INTERPRETATION: Final = (
    "as_of_date is a UTC calendar date used only as the comparison point for "
    "already-parsed tender close/publication dates; never a substitute for missing dates"
)

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

# Operator-queue eligibility (conservative; historical unmatched ≠ tasks).
OPERATOR_ELIGIBLE_CONTEXTS: Final = frozenset(
    {
        PROCUREMENT_CONTEXT_TENDER_ACTIVE,
        PROCUREMENT_CONTEXT_TENDER_WATCH,
    }
)
