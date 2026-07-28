"""Constants and reason codes for the commercial identity read model (PR2).

Identity does not imply relationship stage.
Relationship history does not imply an active opportunity.
Suppression does not imply identity.
A shared consumer email domain never proves account membership.
"""

from __future__ import annotations

from typing import Final

from origenlab_email_pipeline.qa.commercial_truth_audit.constants import (
    CONSUMER_EMAIL_DOMAINS as _PR1_CONSUMER_EMAIL_DOMAINS,
    INTERNAL_DOMAINS,
)

SCHEMA_VERSION: Final = "commercial_identity_v1"

# PR1 list plus explicit proton.me (and keep protonmail.com).
CONSUMER_EMAIL_DOMAINS: Final = frozenset(
    set(_PR1_CONSUMER_EMAIL_DOMAINS)
    | {
        "proton.me",
        "protonmail.com",
    }
)

IDENTITY_CONFIDENCE_HIGH: Final = "high"
IDENTITY_CONFIDENCE_MEDIUM: Final = "medium"
IDENTITY_CONFIDENCE_LOW: Final = "low"
IDENTITY_CONFIDENCE_NONE: Final = "none"

IDENTITY_STATUS_RESOLVED: Final = "resolved"
IDENTITY_STATUS_NEEDS_REVIEW: Final = "needs_review"
IDENTITY_STATUS_AMBIGUOUS: Final = "ambiguous"
IDENTITY_STATUS_UNLINKED: Final = "unlinked"

ORIGIN_ORIGENLAB_GMAIL: Final = "origenlab_gmail"
ORIGIN_LABDELIVERY_ARCHIVE: Final = "labdelivery_archive"
ORIGIN_RESEARCH: Final = "research"
ORIGIN_BUSINESS_MART: Final = "business_mart"
ORIGIN_COMMERCIAL_DEAL: Final = "commercial_deal"
ORIGIN_UNKNOWN: Final = "unknown"

SOURCE_PLANE_CONTACT_MASTER: Final = "contact_master"
SOURCE_PLANE_ORG_MASTER: Final = "organization_master"
SOURCE_PLANE_RESEARCH: Final = "lead_research_prospect"
SOURCE_PLANE_DEAL: Final = "commercial_deal"
SOURCE_PLANE_EMAILS: Final = "emails"

# Matching / conflict reason codes (deterministic, stable strings).
REASON_EXACT_EMAIL: Final = "exact_email"
REASON_EMAIL_CASE_WHITESPACE: Final = "email_case_whitespace_normalized"
REASON_INSTITUTIONAL_DOMAIN: Final = "institutional_domain"
REASON_CONSUMER_DOMAIN_REFUSED: Final = "consumer_domain_auto_link_refused"
REASON_ORG_NAME_EVIDENCE: Final = "org_name_evidence"
REASON_COMPATIBLE_ORG_NAME: Final = "compatible_org_name"
REASON_MISSING_EMAIL: Final = "missing_or_invalid_email"
REASON_MISSING_ORG: Final = "missing_org_identity"
REASON_RESEARCH_ONLY: Final = "research_only_identity"
REASON_EMAIL_CONFLICTING_ORGS: Final = "exact_email_conflicting_organizations"
REASON_DOMAIN_CONFLICTING_ORGS: Final = "institutional_domain_conflicting_organizations"
REASON_COMPETING_ACCOUNT_LINKS: Final = "contact_competing_account_links"
REASON_INSUFFICIENT_MERGE_EVIDENCE: Final = "insufficient_merge_evidence"
REASON_DISPLAY_NAME_ONLY: Final = "display_name_not_contact_key"
REASON_NAME_SIMILARITY_UNMERGED: Final = "similar_org_names_not_merged"

CONFLICT_TYPES: Final = frozenset(
    {
        REASON_EMAIL_CONFLICTING_ORGS,
        REASON_DOMAIN_CONFLICTING_ORGS,
        REASON_COMPETING_ACCOUNT_LINKS,
        REASON_INSUFFICIENT_MERGE_EVIDENCE,
        REASON_CONSUMER_DOMAIN_REFUSED,
        REASON_NAME_SIMILARITY_UNMERGED,
    }
)

REBUILDABLE_TABLES: Final = (
    "commercial_identity_evidence",
    "commercial_identity_conflict",
    "commercial_identity_account_alias",
    "commercial_identity_account_domain",
    "commercial_identity_contact",
    "commercial_identity_account",
    "commercial_identity_build_meta",
)

__all__ = [
    "SCHEMA_VERSION",
    "CONSUMER_EMAIL_DOMAINS",
    "INTERNAL_DOMAINS",
    "IDENTITY_CONFIDENCE_HIGH",
    "IDENTITY_CONFIDENCE_MEDIUM",
    "IDENTITY_CONFIDENCE_LOW",
    "IDENTITY_CONFIDENCE_NONE",
    "IDENTITY_STATUS_RESOLVED",
    "IDENTITY_STATUS_NEEDS_REVIEW",
    "IDENTITY_STATUS_AMBIGUOUS",
    "IDENTITY_STATUS_UNLINKED",
    "ORIGIN_ORIGENLAB_GMAIL",
    "ORIGIN_LABDELIVERY_ARCHIVE",
    "ORIGIN_RESEARCH",
    "ORIGIN_BUSINESS_MART",
    "ORIGIN_COMMERCIAL_DEAL",
    "ORIGIN_UNKNOWN",
    "SOURCE_PLANE_CONTACT_MASTER",
    "SOURCE_PLANE_ORG_MASTER",
    "SOURCE_PLANE_RESEARCH",
    "SOURCE_PLANE_DEAL",
    "SOURCE_PLANE_EMAILS",
    "REASON_EXACT_EMAIL",
    "REASON_EMAIL_CASE_WHITESPACE",
    "REASON_INSTITUTIONAL_DOMAIN",
    "REASON_CONSUMER_DOMAIN_REFUSED",
    "REASON_ORG_NAME_EVIDENCE",
    "REASON_COMPATIBLE_ORG_NAME",
    "REASON_MISSING_EMAIL",
    "REASON_MISSING_ORG",
    "REASON_RESEARCH_ONLY",
    "REASON_EMAIL_CONFLICTING_ORGS",
    "REASON_DOMAIN_CONFLICTING_ORGS",
    "REASON_COMPETING_ACCOUNT_LINKS",
    "REASON_INSUFFICIENT_MERGE_EVIDENCE",
    "REASON_DISPLAY_NAME_ONLY",
    "REASON_NAME_SIMILARITY_UNMERGED",
    "CONFLICT_TYPES",
    "REBUILDABLE_TABLES",
]
