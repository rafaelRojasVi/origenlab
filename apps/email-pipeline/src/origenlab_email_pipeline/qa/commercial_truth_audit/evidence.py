"""Load commercial evidence from SQLite into per-prospect audit rows."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from origenlab_email_pipeline.business_mart import domain_of
from origenlab_email_pipeline.contacto_gmail_source import (
    LEGACY_LABDELIVERY_SOURCE_LIKE,
    classify_email_source,
)
from origenlab_email_pipeline.lead_research.commercial_action_buckets import (
    enrich_prospect_for_dashboard,
)
from origenlab_email_pipeline.lead_research.lead_research_operational_overlay import (
    apply_operational_overlay_to_prospect,
    load_operational_indexes_from_sqlite,
    normalize_prospect_email,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.dimensions import enrich_audit_dimensions
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import table_columns, table_exists
from origenlab_email_pipeline.qa.commercial_truth_audit.redaction import normalize_email


def _parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        # Common SQLite date-only values.
        try:
            return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _days_since(value: str | None, *, now: datetime | None = None) -> int | None:
    dt = _parse_iso_date(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return max(0, int((ref - dt).total_seconds() // 86400))


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _load_contact_master(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "contact_master"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM contact_master"):
        d = _row_dict(row)
        em = normalize_email(d.get("email"))
        if em:
            out[em] = d
    return out


def _load_org_master(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "organization_master"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM organization_master"):
        d = _row_dict(row)
        dom = str(d.get("domain") or "").strip().lower()
        if dom:
            out[dom] = d
    return out


def _load_signal_rollups(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(conn, "commercial_contact_signal_rollup"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM commercial_contact_signal_rollup"):
        d = _row_dict(row)
        em = normalize_email(d.get("contact_email"))
        if em:
            out[em] = d
    return out


def _load_deals_by_email_or_domain(conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_email: dict[str, dict[str, Any]] = {}
    by_domain: dict[str, dict[str, Any]] = {}
    if not table_exists(conn, "commercial_deal"):
        return by_email, by_domain
    for row in conn.execute("SELECT * FROM commercial_deal"):
        d = _row_dict(row)
        em = normalize_email(
            d.get("client_contact_email")
            or d.get("primary_contact_email")
            or d.get("contact_email")
        )
        if em:
            by_email[em] = d
        dom = str(
            d.get("client_domain") or d.get("org_domain") or d.get("domain") or ""
        ).strip().lower()
        if dom:
            by_domain[dom] = d
    return by_email, by_domain


def _email_source_flags(conn: sqlite3.Connection) -> dict[str, dict[str, bool]]:
    """Map normalized contact emails seen in emails table to origenlab/labdelivery flags.

    Uses From/To-ish columns when present; falls back to scanning common address columns.
    """
    flags: dict[str, dict[str, bool]] = defaultdict(lambda: {"origenlab": False, "labdelivery": False})
    if not table_exists(conn, "emails"):
        return flags
    cols = table_columns(conn, "emails")
    if "source_file" not in cols:
        return flags
    # Prefer lightweight scan of source_file + address-bearing columns.
    address_cols = [c for c in ("from_email", "sender_email", "to_emails", "cc_emails", "recipients") if c in cols]
    if not address_cols:
        # Still count source tiers globally via source_file only (no per-contact join).
        return flags
    select = ", ".join(["source_file", *address_cols])
    for row in conn.execute(f"SELECT {select} FROM emails"):
        tier = classify_email_source(row[0] if row[0] is not None else None)
        is_lab = tier == "legacy_labdelivery" or (
            row[0] is not None and "contacto@labdelivery" in str(row[0]).lower()
        )
        is_origen = tier == "canonical_gmail"
        for col_idx in range(1, len(row)):
            raw = row[col_idx]
            if not raw:
                continue
            text = str(raw)
            # Extract simple emails.
            for token in text.replace("<", " ").replace(">", " ").replace(",", " ").split():
                em = normalize_email(token)
                if not em or "@" not in em:
                    continue
                if is_origen:
                    flags[em]["origenlab"] = True
                if is_lab:
                    flags[em]["labdelivery"] = True
    return flags


def _load_prospects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "lead_research_prospect"):
        return []
    cols = table_columns(conn, "lead_research_prospect")
    # Optional gmail count columns may be present after overlay builders.
    rows = [_row_dict(r) for r in conn.execute("SELECT * FROM lead_research_prospect")]
    # Ensure expected keys exist.
    for row in rows:
        for key in ("gmail_sent_count", "gmail_received_count", "source_type", "dataset_label"):
            if key not in cols:
                row.setdefault(key, 0 if "count" in key else "")
    return rows


def build_prospect_evidence_rows(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return overlaid prospects enriched with mart/signal/deal evidence and audit dimensions."""
    idx = load_operational_indexes_from_sqlite(conn)
    contacts = _load_contact_master(conn)
    orgs = _load_org_master(conn)
    signals = _load_signal_rollups(conn)
    deals_by_email, deals_by_domain = _load_deals_by_email_or_domain(conn)
    email_flags = _email_source_flags(conn)

    prospects = _load_prospects(conn)
    out: list[dict[str, Any]] = []
    for raw in prospects:
        overlaid = apply_operational_overlay_to_prospect(dict(raw), idx)
        overlaid = enrich_prospect_for_dashboard(overlaid)
        em = normalize_prospect_email(overlaid.get("email"))
        dom = str(overlaid.get("domain") or "").strip().lower() or (domain_of(em) if em else None) or ""
        cm = contacts.get(em or "", {})
        org = orgs.get(dom, {})
        sig = signals.get(em or "", {})
        deal = deals_by_email.get(em or "") or deals_by_domain.get(dom, {})
        flags = email_flags.get(em or "", {"origenlab": False, "labdelivery": False})

        overlaid["domain"] = dom
        overlaid["quote_email_count"] = int(cm.get("quote_email_count") or org.get("quote_email_count") or 0)
        overlaid["invoice_email_count"] = int(cm.get("invoice_email_count") or org.get("invoice_email_count") or 0)
        overlaid["purchase_email_count"] = int(
            cm.get("purchase_email_count") or org.get("purchase_email_count") or 0
        )
        overlaid["top_equipment_tags"] = str(
            cm.get("top_equipment_tags") or org.get("top_equipment_tags") or overlaid.get("product_angle") or ""
        )
        overlaid["contact_name_best"] = cm.get("contact_name_best") or overlaid.get("contact_name")
        overlaid["role_title"] = overlaid.get("role_title") or ""
        overlaid["quote_signal_count"] = int(sig.get("quote_signal_count") or 0)
        overlaid["procurement_signal_count"] = int(sig.get("procurement_signal_count") or 0)
        overlaid["technical_signal_count"] = int(sig.get("technical_signal_count") or 0)
        overlaid["has_commercial_deal"] = bool(deal)
        overlaid["deal_stage"] = str(
            deal.get("deal_status") or deal.get("stage") or deal.get("status") or ""
        )
        overlaid["has_labdelivery_evidence"] = bool(flags.get("labdelivery"))
        # Also treat explicit source_type / dataset labels as labdelivery evidence.
        source_type = str(overlaid.get("source_type") or "").strip().lower()
        if "labdelivery" in source_type:
            overlaid["has_labdelivery_evidence"] = True
        overlaid["has_origenlab_gmail_evidence"] = bool(flags.get("origenlab"))
        # Historic Labdelivery-labeled rows should not be treated as OrigenLab Gmail merely
        # because gmail_* counters were backfilled onto the prospect row.
        if "labdelivery" not in source_type:
            if int(overlaid.get("gmail_sent_count") or 0) > 0 or int(overlaid.get("gmail_received_count") or 0) > 0:
                overlaid["has_origenlab_gmail_evidence"] = True
            if source_type in {"gmail_historico", "followup_antiguo", "caso_activo"}:
                # Conservative: historic OrigenLab/shared history without labdelivery label.
                overlaid["has_origenlab_gmail_evidence"] = True
        elif flags.get("labdelivery"):
            overlaid["has_labdelivery_evidence"] = True
        last_seen = cm.get("last_seen_at") or org.get("last_seen_at")
        overlaid["days_since_last_seen"] = _days_since(last_seen, now=now)
        overlaid["has_tender_evidence"] = str(overlaid.get("classification") or "") == "public_tender_review" or (
            "tender" in str(overlaid.get("buyer_type") or "").lower()
            or "licit" in str(overlaid.get("likely_need") or "").lower()
        )
        overlaid["tender_active"] = overlaid["has_tender_evidence"] and not overlaid.get("is_blocked")
        # Suppression / outreach convenience fields for dimensions.
        if em and em in idx.suppressions:
            overlaid["suppression_reason_code"] = idx.suppressions[em].reason_code
        if em and em in idx.outreach:
            overlaid["outreach_state"] = idx.outreach[em].state
        if dom and dom in idx.domain_suppressions:
            overlaid["domain_suppressed"] = True
        if dom and dom in idx.domains_with_contacted and not em:
            overlaid["same_domain_contacted"] = True
        overlaid["in_contact_master_only"] = False
        out.append(enrich_audit_dimensions(overlaid))

    # Stable ordering for deterministic outputs.
    out.sort(
        key=lambda r: (
            str(r.get("prospect_key") or ""),
            str(r.get("email") or ""),
            str(r.get("organization_name") or ""),
        )
    )
    return out


def count_email_source_tiers(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    if not table_exists(conn, "emails"):
        return dict(counts)
    cols = table_columns(conn, "emails")
    if "source_file" not in cols:
        return dict(counts)
    for (source_file,) in conn.execute("SELECT source_file FROM emails"):
        counts[classify_email_source(source_file)] += 1
    return dict(sorted(counts.items()))


def labdelivery_like_predicate() -> str:
    return f"lower(COALESCE(source_file, '')) LIKE '{LEGACY_LABDELIVERY_SOURCE_LIKE}'"
