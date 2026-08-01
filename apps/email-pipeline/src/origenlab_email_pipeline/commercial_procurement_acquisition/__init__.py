"""PR5B — official procurement acquisition snapshot contract (parsers only).

No authenticated Mercado Público requests. No PR5 candidate planning.
No relevance, account, contact, or outreach logic.

Records policy B (OCDS): historical releases preferred; compiledRelease emitted
only when its (ocid, release.id) is not already present among historical releases.
"""

from __future__ import annotations

from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    ACQUISITION_CONTRACT_VERSION,
    ENDPOINT_OCDS_MONTHLY_RANGE,
    ENDPOINT_TICKET_LICITACION_DETAIL,
    ENDPOINT_TICKET_LICITACIONES_SUMMARY,
    PARSER_VERSION,
    SOURCE_KIND_OCDS,
    SOURCE_KIND_TICKET_API,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.fingerprint import (
    acquisition_normalized_semantic_digest,
    acquisition_source_fingerprint,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ocds import (
    build_ocds_month_snapshot,
    plan_ocds_ranges,
    parse_ocds_package,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.snapshot import (
    build_acquisition_snapshot,
    build_partial_detail_run,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.ticket_api import (
    parse_ticket_detail_payload,
    parse_ticket_summary_payload,
)

__all__ = [
    "ACQUISITION_CONTRACT_VERSION",
    "ENDPOINT_OCDS_MONTHLY_RANGE",
    "ENDPOINT_TICKET_LICITACION_DETAIL",
    "ENDPOINT_TICKET_LICITACIONES_SUMMARY",
    "PARSER_VERSION",
    "SOURCE_KIND_OCDS",
    "SOURCE_KIND_TICKET_API",
    "acquisition_normalized_semantic_digest",
    "acquisition_source_fingerprint",
    "build_acquisition_snapshot",
    "build_ocds_month_snapshot",
    "build_partial_detail_run",
    "parse_ocds_package",
    "parse_ticket_detail_payload",
    "parse_ticket_summary_payload",
    "plan_ocds_ranges",
]
