"""PR5C coalescence / lifecycle planner constants (no relevance/outcome states)."""

from __future__ import annotations

from typing import Final

from origenlab_email_pipeline.commercial_procurement.constants import (
    ACTIVE_STATUS_CODE as PR4_ACTIVE_STATUS_CODE,
    INACTIVE_STATUS_CODES as PR4_INACTIVE_STATUS_CODES,
    INACTIVE_STATUS_NAMES as PR4_INACTIVE_STATUS_NAMES,
)

# v2: CoalescedProcurementTender/ProcurementEvidenceRef gained
# procurement_method(_details)(_selected|_raw) fields (PR3).
PLANNER_VERSION: Final = "procurement_candidate_planner_v2"
COALESCENCE_POLICY_VERSION: Final = "procurement_coalescence_policy_v3"
LIFECYCLE_POLICY_VERSION: Final = "procurement_lifecycle_policy_v3"
# v4: SELECTABLE_FIELDS gained procurement_method/procurement_method_details (PR3).
FIELD_PRECEDENCE_VERSION: Final = "procurement_field_precedence_v4"
COALESCED_TENDER_ID_ALGORITHM: Final = "coalesced_tender_id_v2"
ACQUISITION_INSTANCE_ID_ALGORITHM: Final = "candidate_acquisition_instance_id_v1"

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
    "pr4_canonical_key_missing",
    "pr4_tender_key_kind_unsupported",
    "pr4_canonical_identity_corrupt",
)

CONFLICT_KINDS: Final = (
    "status_conflict",
    "date_conflict",
    "buyer_identity_conflict",
    "duplicate_live_observation_conflict",
    "incompatible_canonical_identity",
    "acquisition_timestamp_conflict",
    "procurement_method_conflict",
)

# Canonical identity namespaces (grouping grain).
IDENTITY_NS_MERCADO_PUBLICO: Final = "mercado_publico_codigo_externo"
IDENTITY_NS_PR4_CODIGO_EXTERNO: Final = "pr4_codigo_externo"
IDENTITY_NS_PR4_CODIGO_LICITACION: Final = "pr4_codigo_licitacion"
IDENTITY_NS_PR4_NUMERO_ADQUISICION: Final = "pr4_numero_adquisicion"

CANONICAL_KIND_MERCADO_PUBLICO: Final = "mercado_publico_codigo_externo"
TENDER_KEY_KIND_MERCADO_PUBLICO: Final = "mercado_publico_codigo_externo"

PR4_VERIFIED_TENDER_KEY_KINDS: Final = frozenset(
    {
        "codigo_externo",
        "codigo_licitacion",
        "numero_adquisicion",
    }
)

SOURCE_RANK_TICKET_DETAIL: Final = 100
SOURCE_RANK_TICKET_SUMMARY: Final = 80
SOURCE_RANK_OCDS_RELEASE: Final = 70
SOURCE_RANK_OCDS_RECORD: Final = 65
SOURCE_RANK_PR4: Final = 40
SOURCE_RANK_OCDS_LISTA_INDEX: Final = 10
SOURCE_RANK_UNKNOWN: Final = 0

# Authoritative PR4 ChileCompra codes (imported; do not fork).
ACTIVE_STATUS_CODE: Final = PR4_ACTIVE_STATUS_CODE
INACTIVE_STATUS_CODES: Final = PR4_INACTIVE_STATUS_CODES
INACTIVE_STATUS_NAMES: Final = PR4_INACTIVE_STATUS_NAMES

AWARDED_STATUS_CODES: Final = frozenset({"8"})
CANCELLED_STATUS_CODES: Final = frozenset({"7", "18", "19"})
CLOSED_STATUS_CODES: Final = frozenset({"6"})
AWARDED_STATUS_NAMES: Final = frozenset({"adjudicada"})
CANCELLED_STATUS_NAMES: Final = frozenset({"desierta", "revocada", "suspendida"})
CLOSED_STATUS_NAMES: Final = frozenset({"cerrada"})
PUBLICADA_STATUS_NAMES: Final = frozenset({"publicada", "publicada."})

STATUS_CODE_EXPECTED_NAMES: Final = {
    "5": PUBLICADA_STATUS_NAMES,
    "6": CLOSED_STATUS_NAMES,
    "7": frozenset({"desierta"}),
    "8": AWARDED_STATUS_NAMES,
    "18": frozenset({"revocada"}),
    "19": frozenset({"suspendida"}),
}

STATUS_MEANING_OPENISH: Final = "openish"
STATUS_MEANING_AWARDED: Final = "awarded"
STATUS_MEANING_CANCELLED: Final = "cancelled"
STATUS_MEANING_CLOSED: Final = "closed"
STATUS_MEANING_UNKNOWN: Final = "unknown"

TIMESTAMP_PRECISION_DATE_ONLY: Final = "date_only"
TIMESTAMP_PRECISION_MINUTE: Final = "minute"
TIMESTAMP_PRECISION_SECOND: Final = "second"
TIMESTAMP_PRECISION_OFFSET_DATETIME: Final = "offset_datetime"
TIMESTAMP_PRECISION_UNRESOLVED: Final = "unresolved"

TIMESTAMP_PRECISION_RANK: Final = {
    TIMESTAMP_PRECISION_UNRESOLVED: 0,
    TIMESTAMP_PRECISION_DATE_ONLY: 1,
    TIMESTAMP_PRECISION_MINUTE: 2,
    TIMESTAMP_PRECISION_SECOND: 3,
    TIMESTAMP_PRECISION_OFFSET_DATETIME: 4,
}

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

REPORTS_OUT_REL: Final = "reports/out"
