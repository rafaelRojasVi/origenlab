"""Deterministic commercial_deal aggregation for the commercial truth audit."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from origenlab_email_pipeline.qa.commercial_truth_audit.constants import (
    ACTIVE_DEAL_STATUSES,
    CONSUMER_EMAIL_DOMAINS,
    TERMINAL_DEAL_STATUSES,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.emails import normalize_valid_email
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import table_columns, table_exists
from origenlab_email_pipeline.qa.commercial_truth_audit.redaction import email_domain


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


@dataclass(frozen=True)
class DealSelection:
    commercial_deal_count: int
    active_commercial_deal_count: int
    latest_commercial_deal_at: str
    selected_commercial_deal_stage: str
    selected_commercial_deal_reason: str
    commercial_deal_stage_ambiguous: int
    commercial_deal_match_type: str
    selected_deal_id: str
    selected_deal_updated_at: str
    has_commercial_deal: bool


_EMPTY = DealSelection(
    commercial_deal_count=0,
    active_commercial_deal_count=0,
    latest_commercial_deal_at="",
    selected_commercial_deal_stage="",
    selected_commercial_deal_reason="no_matching_deal",
    commercial_deal_stage_ambiguous=0,
    commercial_deal_match_type="none",
    selected_deal_id="",
    selected_deal_updated_at="",
    has_commercial_deal=False,
)


def _deal_identity(deal: dict[str, Any]) -> str:
    for key in ("id", "deal_key"):
        if deal.get(key) is not None and str(deal.get(key)).strip():
            return str(deal[key])
    return ""


def _deal_status(deal: dict[str, Any]) -> str:
    return str(
        deal.get("deal_status") or deal.get("stage") or deal.get("status") or ""
    ).strip().lower()


def _deal_timestamp(deal: dict[str, Any]) -> tuple[datetime | None, str]:
    """Return (parsed, raw) preferring updated_at then created_at; unknown if neither usable."""
    for key in ("updated_at", "created_at", "latest_event_at"):
        raw = str(deal.get(key) or "").strip()
        if not raw:
            continue
        parsed = _parse_iso(raw)
        if parsed is not None:
            return parsed, raw
    return None, ""


def _is_active(status: str) -> bool:
    if status in ACTIVE_DEAL_STATUSES:
        return True
    if status in TERMINAL_DEAL_STATUSES:
        return False
    # Unknown status: treat as non-terminal but lower priority than known active.
    return bool(status) and status not in TERMINAL_DEAL_STATUSES


def select_representative_deal(
    deals: list[dict[str, Any]],
    *,
    match_type: str,
) -> DealSelection:
    """Select one representative deal with documented deterministic rules."""
    if not deals:
        return _EMPTY

    scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for d in deals:
        status = _deal_status(d)
        ts, raw_ts = _deal_timestamp(d)
        # Sort key: active first (0), then newest timestamp, then stable id.
        active_rank = 0 if _is_active(status) else 1
        ts_rank = ts or datetime.min.replace(tzinfo=timezone.utc)
        ident = _deal_identity(d)
        scored.append(((active_rank, -ts_rank.timestamp(), ident), d))

    scored.sort(key=lambda x: x[0])
    best_key, best = scored[0]
    best_status = _deal_status(best)
    _, best_ts_raw = _deal_timestamp(best)

    # Ambiguity: another deal with same active rank and same timestamp competing.
    ambiguous = 0
    for key, other in scored[1:]:
        if key[0] == best_key[0] and key[1] == best_key[1]:
            ambiguous = 1
            break
        # Also ambiguous if multiple active deals with different stages.
        if _is_active(_deal_status(other)) and _is_active(best_status) and _deal_status(other) != best_status:
            ambiguous = 1
            break

    active_count = sum(1 for d in deals if _is_active(_deal_status(d)))
    latest_at = ""
    latest_dt: datetime | None = None
    for d in deals:
        dt, raw = _deal_timestamp(d)
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_at = raw

    reason_parts = [f"match:{match_type}", f"status:{best_status or 'unknown'}"]
    if ambiguous:
        reason_parts.append("ambiguous_competitors")
    if not best_ts_raw:
        reason_parts.append("missing_deal_timestamp")

    return DealSelection(
        commercial_deal_count=len(deals),
        active_commercial_deal_count=active_count,
        latest_commercial_deal_at=latest_at,
        selected_commercial_deal_stage=best_status,
        selected_commercial_deal_reason="|".join(reason_parts),
        commercial_deal_stage_ambiguous=ambiguous,
        commercial_deal_match_type=match_type,
        selected_deal_id=_deal_identity(best),
        selected_deal_updated_at=best_ts_raw,
        has_commercial_deal=True,
    )


def load_deals_index(conn: sqlite3.Connection) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Return (by_exact_email, by_institutional_domain) deal lists."""
    by_email: dict[str, list[dict[str, Any]]] = {}
    by_domain: dict[str, list[dict[str, Any]]] = {}
    if not table_exists(conn, "commercial_deal"):
        return by_email, by_domain

    cols = table_columns(conn, "commercial_deal")
    # Fail safe: if neither status-like nor contact columns exist, return empty.
    has_status = bool(cols & {"deal_status", "stage", "status"})
    has_email = bool(cols & {"client_contact_email", "primary_contact_email", "contact_email"})
    has_domain = bool(cols & {"client_domain", "org_domain", "domain"})
    if not has_status:
        return by_email, by_domain

    for row in conn.execute("SELECT * FROM commercial_deal"):
        d = _row_dict(row)
        em = None
        if has_email:
            em = normalize_valid_email(
                d.get("client_contact_email")
                or d.get("primary_contact_email")
                or d.get("contact_email")
            )
        if em:
            by_email.setdefault(em, []).append(d)
        if has_domain:
            dom = str(
                d.get("client_domain") or d.get("org_domain") or d.get("domain") or ""
            ).strip().lower()
            if dom and dom not in CONSUMER_EMAIL_DOMAINS:
                by_domain.setdefault(dom, []).append(d)
    return by_email, by_domain


def resolve_deals_for_contact(
    *,
    email: str | None,
    domain: str | None,
    by_email: dict[str, list[dict[str, Any]]],
    by_domain: dict[str, list[dict[str, Any]]],
) -> DealSelection:
    """Exact-email match before institutional-domain match; refuse consumer-domain fallback."""
    em = normalize_valid_email(email)
    if em and em in by_email:
        return select_representative_deal(by_email[em], match_type="exact_email")

    dom = (domain or email_domain(em) or "").strip().lower()
    if not dom or dom in CONSUMER_EMAIL_DOMAINS:
        if dom in CONSUMER_EMAIL_DOMAINS:
            return DealSelection(
                commercial_deal_count=0,
                active_commercial_deal_count=0,
                latest_commercial_deal_at="",
                selected_commercial_deal_stage="",
                selected_commercial_deal_reason="consumer_domain_fallback_refused",
                commercial_deal_stage_ambiguous=0,
                commercial_deal_match_type="consumer_domain_refused",
                selected_deal_id="",
                selected_deal_updated_at="",
                has_commercial_deal=False,
            )
        return _EMPTY

    domain_deals = by_domain.get(dom) or []
    if not domain_deals:
        return _EMPTY
    sel = select_representative_deal(domain_deals, match_type="institutional_domain")
    # Domain matches are lower confidence; mark ambiguous when many deals compete.
    ambiguous = sel.commercial_deal_stage_ambiguous
    if sel.commercial_deal_count > 1:
        ambiguous = 1
    return DealSelection(
        commercial_deal_count=sel.commercial_deal_count,
        active_commercial_deal_count=sel.active_commercial_deal_count,
        latest_commercial_deal_at=sel.latest_commercial_deal_at,
        selected_commercial_deal_stage=sel.selected_commercial_deal_stage,
        selected_commercial_deal_reason=sel.selected_commercial_deal_reason + "|domain_lower_confidence",
        commercial_deal_stage_ambiguous=ambiguous,
        commercial_deal_match_type="institutional_domain",
        selected_deal_id=sel.selected_deal_id,
        selected_deal_updated_at=sel.selected_deal_updated_at,
        has_commercial_deal=sel.has_commercial_deal,
    )
