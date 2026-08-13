"""Constants for ANEXO-T1 structured tender intelligence."""

TENDER_TERMS_VERSION = "anexo_t1_v2"
TENDER_TERMS_DIGEST_ALGORITHM = "anexo_t1.structured_tender_terms"

FACT_STATE_EXPLICIT = "explicit"
FACT_STATE_DERIVED = "derived"
FACT_STATE_NOT_EXPLICITLY_FOUND = "not_explicitly_found"
FACT_STATE_UNKNOWN = "unknown"
FACT_STATE_CONFLICTING = "conflicting"

FACT_STATES = frozenset(
    {
        FACT_STATE_EXPLICIT,
        FACT_STATE_DERIVED,
        FACT_STATE_NOT_EXPLICITLY_FOUND,
        FACT_STATE_UNKNOWN,
        FACT_STATE_CONFLICTING,
    }
)

COVERAGE_COMPLETE = "complete"
COVERAGE_INCOMPLETE = "incomplete"

COVERAGE_STATES = frozenset(
    {
        COVERAGE_COMPLETE,
        COVERAGE_INCOMPLETE,
    }
)
