"""Acquisition-instance identity (event capture) distinct from PR5B snapshot_id."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement_acquisition.models import (
    AcquisitionSnapshot,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    ACQUISITION_INSTANCE_ID_ALGORITHM,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    stable_content_id,
)


def acquisition_instance_event_payload(snap: AcquisitionSnapshot) -> dict[str, Any]:
    """Deterministic acquisition-event evidence for instance identity.

    Excludes file path, mtime, materialized_at_utc, report path, and input order.
    """
    pages = sorted(snap.pages, key=lambda p: p.page_id)
    return {
        "algorithm": ACQUISITION_INSTANCE_ID_ALGORITHM,
        "snapshot_id": snap.snapshot_id,
        "acquisition_query_id": snap.query.acquisition_query_id,
        "pages": [
            {
                "page_id": p.page_id,
                "acquired_at_utc": p.acquired_at_utc,
                "http_status": p.http_status,
                "parser_status": p.parser_status,
                "completeness_status": p.completeness_status,
                "error_classification": p.error_classification,
            }
            for p in pages
        ],
    }


def candidate_acquisition_instance_id(snap: AcquisitionSnapshot) -> str:
    return stable_content_id(
        "acquisition_instance",
        acquisition_instance_event_payload(snap),
    )


def acquisition_instance_fingerprint_row(
    snap: AcquisitionSnapshot, *, acquisition_instance_id: str
) -> dict[str, Any]:
    pages = sorted(snap.pages, key=lambda p: p.page_id)
    return {
        "acquisition_instance_id": acquisition_instance_id,
        "snapshot_id": snap.snapshot_id,
        "acquisition_query_id": snap.query.acquisition_query_id,
        "page_ids": [p.page_id for p in pages],
        "pages": [
            {
                "page_id": p.page_id,
                "acquired_at_utc": p.acquired_at_utc,
                "http_status": p.http_status,
                "parser_status": p.parser_status,
                "completeness_status": p.completeness_status,
                "error_classification": p.error_classification,
            }
            for p in pages
        ],
        "source_fingerprint": snap.source_fingerprint,
        "normalized_semantic_digest": snap.normalized_semantic_digest,
        "completeness_status": snap.completeness_status,
    }


def event_observation_identity(
    *,
    acquisition_instance_id: str | None,
    observation_id: str | None,
) -> str | None:
    if not observation_id:
        return None
    if acquisition_instance_id:
        return f"{acquisition_instance_id}|{observation_id}"
    return observation_id
