"""Provenance helpers for acquisition walkthroughs."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.constants import (
    EXISTING_OPERATIONAL_FLOW,
    TICKET_API_TRANSPORT_POLICY,
)


def provenance_document() -> dict[str, Any]:
    return {
        "existing_operational_flow": EXISTING_OPERATIONAL_FLOW,
        "pr5b_role": (
            "Canonical acquisition snapshot contract and parsers. "
            "Existing equipment-first refresh remains the operational consumer "
            "and is not routed through PR5B in this PR."
        ),
        "cross_source_coalescence": "deferred_to_pr5c",
        "ticket_api_transport_policy": TICKET_API_TRANSPORT_POLICY,
        "lanes": {
            "A": "ticket_licitaciones_summary (estado=activas)",
            "B": "ticket_licitacion_detail (codigo)",
            "C": "ocds_lista_agno_mes_range (≤1000/page)",
            "bulk": "PR4 historical/backfill — not newly parsed in PR5B",
        },
    }
