"""WarmCaseRepository — read-only warm commercial queue."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from origenlab_email_pipeline.cases_review_queue import fetch_cases_review_queue

from origenlab_api.schemas.cases import WarmCaseItem
from origenlab_api.settings import get_settings
from origenlab_api.sqlite_ro import open_sqlite_readonly


def fetch_warm_cases(
    sqlite_path: Path,
    *,
    days_window: int = 14,
    limit: int = 50,
    category: str | None = None,
    positive_signal_only: bool = False,
    include_noise: bool = False,
) -> tuple[list[WarmCaseItem], bool, bool, str]:
    """
    Return (items, enrichment_available, reduced_mode, note).

    Uses ``fetch_cases_review_queue``; no email body columns.
    """
    if not sqlite_path.is_file():
        return [], False, True, "SQLite database file not found."

    cap = max(1, min(int(limit), 200))
    conn = open_sqlite_readonly(
        sqlite_path, immutable=bool(get_settings().sqlite_immutable_ro)
    )
    try:
        result = fetch_cases_review_queue(
            conn,
            days_window=days_window,
            exclude_obvious_noise=not include_noise,
            positive_signal_only=positive_signal_only,
            limit=cap,
        )
    finally:
        conn.close()

    reduced_mode = result.reduced_mode or (
        positive_signal_only and not result.enrichment_available
    )
    note = result.caption_es

    from origenlab_api.services.warm_case_classification import row_to_warm_case_item

    from origenlab_api.services.warm_case_output_normalize import normalize_warm_case_items

    category_needle = (category or "").strip().lower() or None
    raw_items: list[WarmCaseItem] = []
    for raw in result.rows:
        item, _cat = row_to_warm_case_item(
            raw,
            enrichment_available=result.enrichment_available,
            include_noise=include_noise,
        )
        raw_items.append(item)
        if len(raw_items) >= cap * 2:
            break

    items = normalize_warm_case_items(
        raw_items,
        include_noise=include_noise,
        category_filter=category_needle,
        positive_signal_only=positive_signal_only,
    )[:cap]

    return items, result.enrichment_available, reduced_mode, note
