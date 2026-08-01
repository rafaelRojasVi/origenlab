"""Compatibility adapter: acquisition observations → equipment-first row shape.

Downstream only. Does not classify equipment or mutate operational refresh.
"""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.chilecompra_api import (
    CHILECOMPRA_NORMALIZED_FIELDS,
    normalize_licitacion_detail_items,
    normalize_licitacion_summary,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    ProcurementLineObservation,
    ProcurementTenderObservation,
)


def empty_normalized_row() -> dict[str, str]:
    return {column: "" for column in CHILECOMPRA_NORMALIZED_FIELDS}


def tender_observation_to_normalized_row(
    tender: ProcurementTenderObservation,
    *,
    line: ProcurementLineObservation | None = None,
    tender_code: str | None = None,
) -> dict[str, str]:
    """Map a PR5B tender (+ optional line) observation to CHILECOMPRA_NORMALIZED_FIELDS."""
    row = empty_normalized_row()
    # Prefer explicit code, then source-neutral canonical candidate, then native.
    if tender_code:
        row["codigo"] = tender_code
    elif tender.canonical_tender_key_candidate:
        row["codigo"] = tender.canonical_tender_key_candidate
    elif tender.source_native_tender_key.startswith("ticket_api:codigo_externo:"):
        row["codigo"] = tender.source_native_tender_key.rsplit(":", 1)[-1]
    row["title"] = tender.title or ""
    row["descripcion"] = tender.description or ""
    row["buyer"] = tender.buyer_display or ""
    row["region"] = tender.region or ""
    row["fecha_publicacion"] = tender.publication_timestamp_raw or ""
    row["close_date"] = tender.close_timestamp_raw or ""
    row["chilecompra_status_code"] = tender.source_status_code or ""
    row["chilecompra_status"] = tender.source_status_name or ""
    if line is not None:
        row["line_description"] = line.description or ""
        row["unspsc_code"] = line.unspsc_or_classification or ""
        row["unidad"] = line.unit or ""
        row["cantidad"] = line.quantity or ""
        row["producto"] = line.product or ""
        row["nivel_1"] = line.category or ""
    return row


def assert_compatible_with_existing_normalizer(
    licitacion: dict[str, Any],
    *,
    detail: bool = False,
) -> list[dict[str, str]]:
    """Return existing normalizer rows for a licitacion (compatibility baseline)."""
    if detail:
        return normalize_licitacion_detail_items(licitacion)
    return [normalize_licitacion_summary(licitacion)]
