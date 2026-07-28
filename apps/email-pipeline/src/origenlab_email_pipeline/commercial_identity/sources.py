"""Load identity assertions from existing SQLite evidence planes (read-only SELECT)."""

from __future__ import annotations

import sqlite3
from typing import Any

from origenlab_email_pipeline.commercial_identity.constants import (
    ORIGIN_BUSINESS_MART,
    ORIGIN_COMMERCIAL_DEAL,
    ORIGIN_LABDELIVERY_ARCHIVE,
    ORIGIN_ORIGENLAB_GMAIL,
    ORIGIN_RESEARCH,
    ORIGIN_UNKNOWN,
    SOURCE_PLANE_CONTACT_MASTER,
    SOURCE_PLANE_DEAL,
    SOURCE_PLANE_ORG_MASTER,
    SOURCE_PLANE_RESEARCH,
)
from origenlab_email_pipeline.commercial_identity.models import SourceIdentityRow
from origenlab_email_pipeline.contacto_gmail_source import classify_email_source


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
    if not _table_exists(conn, name):
        return set()
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({name})")}


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _origin_from_source_file(source_file: str | None) -> str:
    tier = classify_email_source(source_file)
    if tier == "canonical_gmail":
        return ORIGIN_ORIGENLAB_GMAIL
    if tier == "legacy_labdelivery":
        return ORIGIN_LABDELIVERY_ARCHIVE
    return ORIGIN_UNKNOWN


def _load_email_origin_by_address(conn: sqlite3.Connection) -> dict[str, str]:
    """Best-effort map of lowercased mailbox → origin from emails sender/recipients sample.

    Used only to label mart-derived rows when message evidence exists. Missing map entries
    stay as business_mart / unknown — never invent OrigenLab/Labdelivery labels.
    """
    if not _table_exists(conn, "emails"):
        return {}
    out: dict[str, str] = {}
    cols = _columns(conn, "emails")
    if "source_file" not in cols:
        return {}
    # Limit scan to source_file + sender for origin labeling (identity, not body).
    for row in conn.execute("SELECT source_file, sender FROM emails"):
        origin = _origin_from_source_file(row["source_file"] if row["source_file"] is not None else None)
        if origin == ORIGIN_UNKNOWN:
            continue
        sender = str(row["sender"] or "").strip().lower()
        if "@" not in sender:
            continue
        start = sender.find("<")
        end = sender.find(">")
        if 0 <= start < end:
            token = sender[start + 1 : end].strip()
        else:
            token = sender.split()[0] if sender else ""
        if "@" not in token:
            continue
        # Prefer OrigenLab when both tiers are observed for the same address.
        if token not in out or origin == ORIGIN_ORIGENLAB_GMAIL:
            out[token] = origin
    return out


def load_source_identity_rows(conn: sqlite3.Connection) -> list[SourceIdentityRow]:
    """Collect identity assertions from available tables. Order is not semantically significant."""
    rows: list[SourceIdentityRow] = []
    email_origins = _load_email_origin_by_address(conn)

    if _table_exists(conn, "contact_master"):
        for row in conn.execute("SELECT * FROM contact_master"):
            d = _row_dict(row)
            email = str(d.get("email") or "").strip()
            origin = email_origins.get(email.lower(), ORIGIN_BUSINESS_MART)
            rows.append(
                SourceIdentityRow(
                    source_table="contact_master",
                    source_record_id=email or "missing_email",
                    source_plane=SOURCE_PLANE_CONTACT_MASTER,
                    origin_plane=origin,
                    email_raw=email or None,
                    display_name=(str(d.get("contact_name_best") or "").strip() or None),
                    organization_name=(str(d.get("organization_name_guess") or "").strip() or None),
                    domain_raw=(str(d.get("domain") or "").strip() or None),
                    evidence_at=(str(d.get("first_seen_at") or "").strip() or None),
                    extra={
                        "last_seen_at": (str(d.get("last_seen_at") or "").strip() or None),
                        "organization_type_guess": d.get("organization_type_guess"),
                    },
                )
            )
            # Also surface last_seen as a separate timestamp observation when distinct.
            last_at = str(d.get("last_seen_at") or "").strip() or None
            first_at = str(d.get("first_seen_at") or "").strip() or None
            if last_at and last_at != first_at:
                rows.append(
                    SourceIdentityRow(
                        source_table="contact_master",
                        source_record_id=f"{email}|last_seen",
                        source_plane=SOURCE_PLANE_CONTACT_MASTER,
                        origin_plane=origin,
                        email_raw=email or None,
                        display_name=(str(d.get("contact_name_best") or "").strip() or None),
                        organization_name=(str(d.get("organization_name_guess") or "").strip() or None),
                        domain_raw=(str(d.get("domain") or "").strip() or None),
                        evidence_at=last_at,
                        extra={"timestamp_kind": "last_seen_at"},
                    )
                )

    if _table_exists(conn, "organization_master"):
        for row in conn.execute("SELECT * FROM organization_master"):
            d = _row_dict(row)
            dom = str(d.get("domain") or "").strip()
            rows.append(
                SourceIdentityRow(
                    source_table="organization_master",
                    source_record_id=dom or "missing_domain",
                    source_plane=SOURCE_PLANE_ORG_MASTER,
                    origin_plane=ORIGIN_BUSINESS_MART,
                    organization_name=(str(d.get("organization_name_guess") or "").strip() or None),
                    domain_raw=dom or None,
                    evidence_at=(str(d.get("first_seen_at") or "").strip() or None),
                    extra={
                        "last_seen_at": (str(d.get("last_seen_at") or "").strip() or None),
                        "organization_type_guess": d.get("organization_type_guess"),
                    },
                )
            )
            last_at = str(d.get("last_seen_at") or "").strip() or None
            first_at = str(d.get("first_seen_at") or "").strip() or None
            if last_at and last_at != first_at:
                rows.append(
                    SourceIdentityRow(
                        source_table="organization_master",
                        source_record_id=f"{dom}|last_seen",
                        source_plane=SOURCE_PLANE_ORG_MASTER,
                        origin_plane=ORIGIN_BUSINESS_MART,
                        organization_name=(str(d.get("organization_name_guess") or "").strip() or None),
                        domain_raw=dom or None,
                        evidence_at=last_at,
                        extra={"timestamp_kind": "last_seen_at"},
                    )
                )

    if _table_exists(conn, "lead_research_prospect"):
        cols = _columns(conn, "lead_research_prospect")
        for row in conn.execute("SELECT * FROM lead_research_prospect"):
            d = _row_dict(row)
            rid = str(d.get("id") if d.get("id") is not None else d.get("prospect_key") or "")
            rows.append(
                SourceIdentityRow(
                    source_table="lead_research_prospect",
                    source_record_id=rid or "unknown",
                    source_plane=SOURCE_PLANE_RESEARCH,
                    origin_plane=ORIGIN_RESEARCH,
                    email_raw=(str(d.get("email") or "").strip() or None),
                    display_name=(str(d.get("contact_name") or d.get("full_name") or "").strip() or None),
                    role=(str(d.get("role") or d.get("title") or "").strip() or None),
                    organization_name=(str(d.get("organization_name") or d.get("org_name") or "").strip() or None),
                    domain_raw=(str(d.get("domain") or "").strip() or None),
                    evidence_at=(
                        str(d.get("updated_at") or d.get("created_at") or d.get("imported_at") or "").strip() or None
                    ),
                    extra={
                        "classification": d.get("classification"),
                        "source_type": d.get("source_type") if "source_type" in cols else None,
                        "research_only": True,
                    },
                )
            )

    if _table_exists(conn, "commercial_deal"):
        for row in conn.execute("SELECT * FROM commercial_deal"):
            d = _row_dict(row)
            deal_key = str(d.get("deal_key") or d.get("id") or "")
            rows.append(
                SourceIdentityRow(
                    source_table="commercial_deal",
                    source_record_id=deal_key or "unknown",
                    source_plane=SOURCE_PLANE_DEAL,
                    origin_plane=ORIGIN_COMMERCIAL_DEAL,
                    email_raw=(str(d.get("client_contact_email") or "").strip() or None),
                    organization_name=(str(d.get("client_org_name") or "").strip() or None),
                    domain_raw=(str(d.get("client_domain") or "").strip() or None),
                    evidence_at=(
                        str(d.get("updated_at") or d.get("created_at") or d.get("quoted_at") or "").strip() or None
                    ),
                    extra={"party": "client"},
                )
            )

    return rows
