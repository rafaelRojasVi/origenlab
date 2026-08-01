"""PR5B acquisition contract versions and vocabulary."""

from __future__ import annotations

from typing import Final

# Snapshot/observation contract stays v1: normalized evidence fields unchanged.
# OCDS range *query* identity is versioned separately (see OCDS_QUERY_CONTRACT_VERSION).
ACQUISITION_CONTRACT_VERSION: Final = "commercial_procurement_acquisition_v1"
PARSER_VERSION: Final = "procurement_acquisition_parser_v1"
MANIFEST_VERSION: Final = "acquisition_snapshot_manifest_v1"
QUERY_CONTRACT_VERSION: Final = "acquisition_query_v1"
# ListaOCDSAgnoMes range wire semantics measured in PR5B.1 (offset/limit).
OCDS_QUERY_CONTRACT_VERSION: Final = "acquisition_query_v2"
RUN_CONTRACT_VERSION: Final = "acquisition_run_v1"

# Live listaOCDSAgnoMes wire contract (PR5B.1 measurement):
# path /{year}/{month}/{offset}/{limit} with offset>=0 and 1<=limit<=1000.
OCDS_RANGE_SEMANTICS: Final = "zero_based_offset_limit_v1"

SOURCE_KIND_TICKET_API: Final = "mercado_publico_ticket_api"
SOURCE_KIND_OCDS: Final = "chilecompra_ocds"

ENDPOINT_TICKET_LICITACIONES_SUMMARY: Final = "ticket_licitaciones_summary"
ENDPOINT_TICKET_LICITACION_DETAIL: Final = "ticket_licitacion_detail"
ENDPOINT_OCDS_MONTHLY_RANGE: Final = "ocds_lista_agno_mes_range"
ENDPOINT_OCDS_MONTHLY_MONTH: Final = "ocds_lista_agno_mes_month"

ENDPOINT_PATH_TICKET: Final = "/servicios/v1/publico/licitaciones.json"
ENDPOINT_PATH_OCDS_TEMPLATE: Final = (
    "/APISOCDS/OCDS/listaOCDSAgnoMes/{year}/{month}/{start}/{end}"
)
ENDPOINT_PATH_OCDS_MONTH_TEMPLATE: Final = (
    "/APISOCDS/OCDS/listaOCDSAgnoMes/{year}/{month}"
)

OCDS_MAX_PAGE_SIZE: Final = 1000

RAW_PAYLOAD_DIGEST_ALGORITHM: Final = "sha256_canonical_json_v1"
ORIGINAL_BYTES_DIGEST_ALGORITHM: Final = "sha256_utf8_bytes_v1"
# v2 includes authoritative snapshot-level source_reported_total (not observation counts).
SOURCE_FINGERPRINT_ALGORITHM: Final = "acquisition_source_fingerprint_v2"
NORMALIZED_SEMANTIC_DIGEST_ALGORITHM: Final = "acquisition_normalized_semantic_digest_v1"
RUN_SOURCE_FINGERPRINT_ALGORITHM: Final = "acquisition_run_source_fingerprint_v1"

ID_ALGORITHMS: Final = {
    "acquisition_query_id": "acquisition_query_id_v1",
    "acquisition_page_id": "acquisition_page_id_v1",
    "procurement_source_observation_id": "procurement_source_observation_id_v1",
    "procurement_tender_observation_id": "procurement_tender_observation_id_v1",
    "procurement_line_observation_id": "procurement_line_observation_id_v1",
    "procurement_snapshot_id": "procurement_snapshot_id_v1",
    "acquisition_run_id": "acquisition_run_id_v1",
}

# Page/snapshot content completeness (not fixture origin).
COMPLETENESS_STATUSES: Final = (
    "complete",
    "partial_page_failure",
    "partial_detail_failure",
    "incomplete_range",
    "malformed_response",
    "source_total_mismatch",
    "duplicate_page",
    "overlapping_range",
    "empty_page",
    "terminal_empty_page",
    "detail_empty",
    "detail_multiple_results",
    "detail_code_mismatch",
)

# Ticket detail-specific statuses.
TICKET_DETAIL_STATUSES: Final = (
    "complete",
    "malformed_response",
    "detail_empty",
    "detail_multiple_results",
    "detail_code_mismatch",
    "source_total_mismatch",
)

# Monthly OCDS assembler statuses.
OCDS_MONTHLY_COMPLETENESS_STATUSES: Final = (
    "complete",
    "terminal_empty_page",
    "incomplete_range",
    "duplicate_page",
    "overlapping_range",
    "source_total_mismatch",
    "malformed_response",
    "partial_page_failure",
)

RELEASE_KIND_HISTORICAL: Final = "historical_release"
RELEASE_KIND_COMPILED: Final = "compiled_release"
# Policy B: historical releases preferred; compiledRelease emitted only when its
# (ocid, release.id) is not already present among historical releases.

TICKET_API_TRANSPORT_POLICY: Final = {
    "one_ticket_per_person": True,
    "documented_daily_request_limit": 10_000,
    "preferred_bulk_window_local": "22:00-07:00 America/Santiago",
    "rate_limit_undocumented_note": "not found in official documentation (do not invent)",
    "retry": {
        "bounded_attempts": True,
        "exponential_backoff_with_jitter": True,
        "retryable_http_statuses": [408, 429, 500, 502, 503, 504],
        "no_retry_invalid_credentials": True,
        "no_retry_malformed_request": True,
        "no_infinite_retry": True,
        "ticket_redaction_in_exceptions": True,
    },
    "quota_budget_lanes": [
        "active_summary_query",
        "detail_queries",
        "reconciliation_queries",
    ],
    "operationalized_in_pr5b": False,
}

EXISTING_OPERATIONAL_FLOW: Final = (
    "ticket API → summary keyword prefilter → bounded detail lookups / detail cache "
    "→ equipment-first classifier → CSV / manifest publication "
    "→ typed equipment opportunity publication"
)
