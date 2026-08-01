"""PR5C coalescence / lifecycle planner constants (no relevance/outcome states)."""

from __future__ import annotations

from typing import Final

PLANNER_VERSION: Final = "procurement_candidate_planner_v1"
COALESCENCE_POLICY_VERSION: Final = "procurement_coalescence_policy_v1"
LIFECYCLE_POLICY_VERSION: Final = "procurement_lifecycle_policy_v1"
FIELD_PRECEDENCE_VERSION: Final = "procurement_field_precedence_v1"

CANDIDATE_INPUT_SOURCE_FP_ALGORITHM: Final = "candidate_input_source_fp_v1"
CANDIDATE_BUILD_PLAN_FP_ALGORITHM: Final = "candidate_build_plan_fp_v1"
CANDIDATE_SEMANTIC_DIGEST_ALGORITHM: Final = "candidate_semantic_digest_v1"

AS_OF_TIMEZONE: Final = "America/Santiago"
DEFAULT_FRESHNESS_THRESHOLD_HOURS: Final = 48

EVIDENCE_PLANE_PR4: Final = "pr4"
EVIDENCE_PLANE_ACQUISITION: Final = "acquisition"

CANDIDATE_SOURCE_KINDS: Final = (
    "pr4",
    "live_snapshot",
    "both",
)

COALESCENCE_STATUSES: Final = (
    "pr4_only",
    "live_only",
    "exact_agreement",
    "live_source_newer",
    "status_conflict",
    "date_conflict",
    "buyer_identity_conflict",
    "multiple_live_sources_agree",
    "multiple_live_sources_conflict",
)

# Reuse PR5A documented lifecycle vocabulary (no relevance).
LIFECYCLE_CLASSES: Final = (
    "active_open",
    "future_scheduled",
    "closed",
    "awarded",
    "cancelled",
    "status_conflict",
    "date_missing",
    "status_unknown",
)

CLOSING_SOON_BUCKETS: Final = (
    "lt_24h",
    "d1_to_d3",
    "d4_to_d7",
    "gt_7d",
    "not_applicable",
)

CURRENTNESS_CLASSES: Final = (
    "current_authoritative_snapshot",
    "stale_authoritative_snapshot",
    "acquisition_timestamp_missing",
    "acquisition_timestamp_invalid",
    "historical_pr4_only",
)

UNRESOLVED_REASONS: Final = (
    "live_canonical_candidate_missing",
    "live_canonical_candidate_malformed",
    "ocds_ocid_only_unresolved",
    "source_native_identity_not_canonical",
    "unsupported_candidate_kind",
    "incomplete_or_failed_page",
    "timezone_unresolved",
)

CONFLICT_KINDS: Final = (
    "status_conflict",
    "date_conflict",
    "buyer_identity_conflict",
    "duplicate_live_observation_conflict",
    "incompatible_canonical_identity",
    "acquisition_timestamp_conflict",
)

CANONICAL_KIND_MERCADO_PUBLICO: Final = "mercado_publico_codigo_externo"
TENDER_KEY_KIND_MERCADO_PUBLICO: Final = "mercado_publico_codigo_externo"

# Field precedence ranks (higher = preferred for display selection).
# Lista-index stubs never override detailed tender fields they do not carry.
SOURCE_RANK_TICKET_DETAIL: Final = 100
SOURCE_RANK_TICKET_SUMMARY: Final = 80
SOURCE_RANK_OCDS_RELEASE: Final = 70
SOURCE_RANK_OCDS_RECORD: Final = 65
SOURCE_RANK_PR4: Final = 40
SOURCE_RANK_OCDS_LISTA_INDEX: Final = 10
SOURCE_RANK_UNKNOWN: Final = 0

# ChileCompra status reuse (same codes as PR4 / PR5A).
ACTIVE_STATUS_CODE: Final = "5"
AWARDED_STATUS_CODES: Final = frozenset({"8"})
CANCELLED_STATUS_CODES: Final = frozenset({"7", "18", "19"})  # desierta/revocada/suspendida
CLOSED_STATUS_CODES: Final = frozenset({"6"})
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
AWARDED_STATUS_NAMES: Final = frozenset({"adjudicada"})
CANCELLED_STATUS_NAMES: Final = frozenset(
    {"desierta", "revocada", "suspendida"}
)
CLOSED_STATUS_NAMES: Final = frozenset({"cerrada"})
PUBLICADA_STATUS_NAMES: Final = frozenset({"publicada", "publicada."})

REQUIRED_PR4_META_KEYS: Final = (
    "source_fingerprint",
    "build_plan_fingerprint",
    "semantic_plan_digest",
    "schema_version",
    "build_contract",
    "identity_fingerprint",
    "as_of_date",
)

FORBIDDEN_CLI_FLAGS: Final = (
    "--apply",
    "--persist",
    "--network",
    "--ticket",
    "--gmail",
    "--postgres",
    "--outreach",
    "--schedule",
)
