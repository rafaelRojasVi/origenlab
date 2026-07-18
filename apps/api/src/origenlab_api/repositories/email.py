"""EmailRepository — read-only recent canonical messages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from origenlab_email_pipeline.cases_review_queue import fetch_cases_review_queue
from origenlab_email_pipeline.operational_scope import CANONICAL_SCOPE_NOTE

from origenlab_api.settings import get_settings
from origenlab_api.sqlite_ro import open_operator_sqlite


def _folder_hint(source_file: str | None) -> str | None:
    if not source_file:
        return None
    s = source_file.strip()
    for token in ("/[Gmail]/", "/INBOX", "/Sent", "/Enviados"):
        if token in s:
            idx = s.find(token)
            return s[idx + 1 :].split("/")[0] if idx >= 0 else None
    if "/" in s:
        return s.rsplit("/", 1)[-1] or None
    return None


def _bool_signal(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return bool(value)


def list_recent_emails(
    sqlite_path: Path,
    *,
    days_window: int = 7,
    limit: int = 50,
    exclude_noise: bool = True,
    folder: str | None = None,
) -> tuple[list[dict[str, Any]], bool, bool, str]:
    """Return (rows, enrichment_available, reduced_mode, scope_note).

    Uses ``fetch_cases_review_queue`` (preview fields only; no body columns).
    """
    if not sqlite_path.is_file():
        return [], False, True, CANONICAL_SCOPE_NOTE

    cap = max(1, min(int(limit), 200))
    conn = open_operator_sqlite(sqlite_path, settings=get_settings())
    try:
        # cases_review_queue enforces an internal floor of 10; slice to API ``limit`` afterward.
        result = fetch_cases_review_queue(
            conn,
            days_window=days_window,
            exclude_obvious_noise=exclude_noise,
            positive_signal_only=False,
            limit=cap,
        )
    finally:
        conn.close()

    rows: list[dict[str, Any]] = []
    folder_needle = (folder or "").strip().lower()
    for raw in result.rows:
        source_file = raw.get("source_file")
        if folder_needle:
            hay = f"{source_file or ''} {raw.get('folder') or ''}".lower()
            if folder_needle not in hay:
                continue
        rows.append(
            {
                "email_id": int(raw["email_id"]),
                "date_iso": raw.get("date_iso"),
                "subject_preview": raw.get("subject_preview") or "",
                "sender_preview": raw.get("sender_preview") or "",
                "source_file": source_file,
                "folder_hint": _folder_hint(source_file if isinstance(source_file, str) else None),
                "has_positive_signal": _bool_signal(raw.get("has_positive_signal")),
                "has_suppression_signal": _bool_signal(raw.get("has_suppression_signal")),
            }
        )
        if len(rows) >= cap:
            break

    return rows[:cap], result.enrichment_available, result.reduced_mode, CANONICAL_SCOPE_NOTE
