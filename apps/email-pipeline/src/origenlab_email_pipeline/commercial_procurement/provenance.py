"""Deterministic field-level source provenance for ChileCompra outcomes."""

from __future__ import annotations

from typing import Callable

from origenlab_email_pipeline.commercial_procurement.constants import (
    FIELD_ORIGIN_ABSENT,
    FIELD_ORIGIN_BOTH_EQUAL,
    FIELD_ORIGIN_CONFLICT,
    FIELD_ORIGIN_LEAD,
    FIELD_ORIGIN_RAW,
)


def resolve_field_origin(
    lead_value: str | None,
    raw_value: str | None,
    *,
    normalize: Callable[[str], str] | None = None,
) -> tuple[str | None, str]:
    """Return (selected_value, origin_marker).

    When both planes disagree after normalization, selected_value is None and
    origin is conflict (callers keep both plane values for evidence).
    """
    lead = (lead_value or "").strip() or None
    raw = (raw_value or "").strip() or None
    if lead is None and raw is None:
        return None, FIELD_ORIGIN_ABSENT
    if lead is None:
        return raw, FIELD_ORIGIN_RAW
    if raw is None:
        return lead, FIELD_ORIGIN_LEAD
    left = normalize(lead) if normalize else lead.lower()
    right = normalize(raw) if normalize else raw.lower()
    if left == right:
        return lead, FIELD_ORIGIN_BOTH_EQUAL
    return None, FIELD_ORIGIN_CONFLICT


def provenance_payload(markers: dict[str, str]) -> dict[str, str]:
    """Stable ordered provenance map for fingerprints."""
    return {k: markers[k] for k in sorted(markers.keys())}


def origins_for_evidence(origin: str) -> tuple[str, ...]:
    """Which source tables should receive field evidence for this origin."""
    if origin == FIELD_ORIGIN_RAW:
        return (FIELD_ORIGIN_RAW,)
    if origin == FIELD_ORIGIN_LEAD:
        return (FIELD_ORIGIN_LEAD,)
    if origin in {FIELD_ORIGIN_BOTH_EQUAL, FIELD_ORIGIN_CONFLICT}:
        return (FIELD_ORIGIN_RAW, FIELD_ORIGIN_LEAD)
    return ()


__all__ = [
    "origins_for_evidence",
    "provenance_payload",
    "resolve_field_origin",
]
