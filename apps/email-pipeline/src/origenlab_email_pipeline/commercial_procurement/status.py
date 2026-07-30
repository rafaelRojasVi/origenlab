"""Procurement-status mapping (structured fields only — never build time)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from origenlab_email_pipeline.commercial_procurement.constants import (
    ACTIVE_STATUS_CODE,
    INACTIVE_STATUS_CODES,
    INACTIVE_STATUS_NAMES,
    PROCUREMENT_CONTEXT_HISTORICAL,
    PROCUREMENT_CONTEXT_TENDER_ACTIVE,
    PROCUREMENT_CONTEXT_TENDER_WATCH,
    PROCUREMENT_CONTEXT_UNKNOWN,
    REASON_TENDER_DATES_MISSING_OR_MALFORMED,
    REASON_TENDER_STATUS_UNKNOWN,
)


def parse_tender_date(value: str | None) -> date | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Common ChileCompra shapes: YYYY-MM-DD, DD-MM-YYYY, YYYYMMDD, ISO datetime.
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:19].replace("Z", ""), fmt).date()
        except ValueError:
            continue
    # Strip time suffixes loosely.
    if "T" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def classify_procurement_context(
    *,
    status_code: str | None,
    status_name: str | None,
    close_date: str | None,
    publication_date: str | None = None,
    as_of_date: date | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Map structured ChileCompra fields → procurement context.

    Rules:
    - active requires status code/name evidence and/or a valid future/today close date
      with publicada status;
    - closed/awarded/cancelled → historical_tender;
    - missing/malformed dates with no usable status → unknown;
    - never uses build/wall-clock as a substitute for missing dates;
    - ``as_of_date`` (preferred) is the sole comparison point for parsed close dates.
    """
    code = (status_code or "").strip()
    name = (status_name or "").strip().lower()
    close = parse_tender_date(close_date)
    pub = parse_tender_date(publication_date)
    now = as_of_date or today
    if now is None:
        raise ValueError("as_of_date is required for deterministic procurement status classification")

    if code in INACTIVE_STATUS_CODES or name in INACTIVE_STATUS_NAMES:
        return {
            "procurement_context": PROCUREMENT_CONTEXT_HISTORICAL,
            "reason_code": "status_inactive_or_closed",
            "status_code": code or None,
            "status_name": status_name,
            "close_date_parsed": close.isoformat() if close else None,
            "publication_date_parsed": pub.isoformat() if pub else None,
            "close_date_malformed": bool(close_date) and close is None,
        }

    if code == ACTIVE_STATUS_CODE or name in {"publicada", "publicada."}:
        if close is None:
            if close_date:
                return {
                    "procurement_context": PROCUREMENT_CONTEXT_UNKNOWN,
                    "reason_code": REASON_TENDER_DATES_MISSING_OR_MALFORMED,
                    "status_code": code or None,
                    "status_name": status_name,
                    "close_date_parsed": None,
                    "publication_date_parsed": pub.isoformat() if pub else None,
                    "close_date_malformed": True,
                }
            # Publicada without close date → watch, not confident active.
            return {
                "procurement_context": PROCUREMENT_CONTEXT_TENDER_WATCH,
                "reason_code": REASON_TENDER_DATES_MISSING_OR_MALFORMED,
                "status_code": code or None,
                "status_name": status_name,
                "close_date_parsed": None,
                "publication_date_parsed": pub.isoformat() if pub else None,
                "close_date_malformed": False,
            }
        if close < now:
            return {
                "procurement_context": PROCUREMENT_CONTEXT_HISTORICAL,
                "reason_code": "close_date_in_past",
                "status_code": code or None,
                "status_name": status_name,
                "close_date_parsed": close.isoformat(),
                "publication_date_parsed": pub.isoformat() if pub else None,
                "close_date_malformed": False,
            }
        return {
            "procurement_context": PROCUREMENT_CONTEXT_TENDER_ACTIVE,
            "reason_code": "publicada_with_future_or_today_close",
            "status_code": code or None,
            "status_name": status_name,
            "close_date_parsed": close.isoformat(),
            "publication_date_parsed": pub.isoformat() if pub else None,
            "close_date_malformed": False,
        }

    if close is not None:
        if close < now:
            return {
                "procurement_context": PROCUREMENT_CONTEXT_HISTORICAL,
                "reason_code": "close_date_in_past_status_unknown",
                "status_code": code or None,
                "status_name": status_name,
                "close_date_parsed": close.isoformat(),
                "publication_date_parsed": pub.isoformat() if pub else None,
                "close_date_malformed": False,
            }
        return {
            "procurement_context": PROCUREMENT_CONTEXT_TENDER_WATCH,
            "reason_code": "future_close_without_publicada_status",
            "status_code": code or None,
            "status_name": status_name,
            "close_date_parsed": close.isoformat(),
            "publication_date_parsed": pub.isoformat() if pub else None,
            "close_date_malformed": False,
        }

    return {
        "procurement_context": PROCUREMENT_CONTEXT_UNKNOWN,
        "reason_code": REASON_TENDER_STATUS_UNKNOWN,
        "status_code": code or None,
        "status_name": status_name,
        "close_date_parsed": None,
        "publication_date_parsed": pub.isoformat() if pub else None,
        "close_date_malformed": bool(close_date) and close is None,
    }


__all__ = ["classify_procurement_context", "parse_tender_date"]
