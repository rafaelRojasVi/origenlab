"""ContactRepository — read-only contact intelligence from SQLite + DNR CSV."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.contact_domain_suppression import load_suppressed_contact_domain_norms
from origenlab_email_pipeline.contact_email_suppression import fetch_contact_email_suppression_row
from origenlab_email_pipeline.equipment_deepsearch_vetted_queue import load_email_set_from_csv
from origenlab_email_pipeline.outbound_core import resolve_outbound_gmail_user, resolve_outbound_sent_folders
from origenlab_email_pipeline.outreach_contact_state import (
    fetch_outreach_contact_state_row,
    normalize_contact_email_for_outreach,
)

from origenlab_api.repositories.equipment_opportunities import load_manifest

_BLOCKING_OUTREACH_STATES = frozenset({"contacted", "replied", "snoozed"})


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _domain_from_email(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].lower().strip()


def _is_domain_suppressed(domain: str, suppressed: frozenset[str]) -> bool:
    if not domain or not suppressed:
        return False
    d = domain.lower().strip()
    if d in suppressed:
        return True
    parts = d.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[i:])
        if parent in suppressed:
            return True
    return False


def resolve_do_not_repeat_master_csv(active_current: Path, manifest: dict[str, Any]) -> Path | None:
    stale = {
        str(entry.get("path") or "").strip()
        for entry in (manifest.get("stale_files") or [])
        if entry.get("path")
    }
    for rel in manifest.get("canonical_files") or []:
        rel_s = str(rel).strip()
        if rel_s == "do_not_repeat_master.csv" and rel_s not in stale:
            candidate = active_current / rel_s
            if candidate.is_file():
                return candidate
    fallback = active_current / "do_not_repeat_master.csv"
    return fallback if fallback.is_file() else None


def _fetch_contact_master(conn: sqlite3.Connection, email_norm: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "contact_master"):
        return None
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT email, contact_name_best, domain, organization_name_guess,
               first_seen_at, last_seen_at, total_emails
        FROM contact_master
        WHERE lower(trim(email)) = ?
        """,
        (email_norm,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _fetch_organization_master(conn: sqlite3.Connection, domain: str) -> dict[str, Any] | None:
    if not domain or not _table_exists(conn, "organization_master"):
        return None
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT domain, organization_name_guess, first_seen_at, last_seen_at, total_emails
        FROM organization_master
        WHERE lower(trim(domain)) = ?
        """,
        (domain.lower(),),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def _fetch_sent_history(
    conn: sqlite3.Connection,
    email_norm: str,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
) -> dict[str, Any]:
    out = {"sent_count": 0, "latest_sent_at": None, "latest_subject": None}
    if not _table_exists(conn, "emails"):
        return out
    folders = tuple(f.strip() for f in sent_folders if f.strip())
    if not folders:
        return out
    like_pat = f"gmail:{gmail_user.strip().lower()}/%"
    ph = ",".join("?" * len(folders))
    needle = f"%{email_norm}%"
    try:
        rows = conn.execute(
            f"""
            SELECT date_iso, subject, recipients FROM emails
            WHERE lower(source_file) LIKE ?
              AND folder IN ({ph})
              AND lower(COALESCE(recipients, '')) LIKE ?
            ORDER BY date_iso DESC
            LIMIT 100
            """,
            (like_pat, *folders, needle),
        ).fetchall()
    except sqlite3.OperationalError:
        return out

    count = 0
    latest_at: str | None = None
    latest_subj: str | None = None
    for date_iso, subject, recipients in rows:
        if email_norm not in emails_in(recipients or ""):
            continue
        count += 1
        if latest_at is None:
            latest_at = date_iso if date_iso else None
            latest_subj = (subject or "")[:200] if subject else None
    out["sent_count"] = count
    out["latest_sent_at"] = latest_at
    out["latest_subject"] = latest_subj
    return out


def fetch_contact_intelligence(
    sqlite_path: Path,
    active_current: Path,
    email_raw: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str], bool]:
    """
    Return (contact, outreach, sent_history, warnings, reduced_mode).

    Raises ValueError for invalid email format.
    """
    email_norm = normalize_contact_email_for_outreach(email_raw)
    domain = _domain_from_email(email_norm)
    warnings: list[str] = []
    reduced_mode = False

    if not sqlite_path.is_file():
        warnings.append("SQLite database file not found.")
        return (
            _empty_contact(email_raw, email_norm, domain),
            _empty_outreach(),
            _empty_sent_history(),
            warnings,
            True,
        )

    manifest = load_manifest(active_current)
    dnr_path = resolve_do_not_repeat_master_csv(active_current, manifest)
    dnr_set: set[str] = set()
    if dnr_path:
        dnr_set = load_email_set_from_csv(
            dnr_path,
            "email_norm",
            "contact_email",
            "email",
        )
    else:
        warnings.append("do_not_repeat_master.csv not found under active/current.")

    from origenlab_email_pipeline.config import load_settings

    ep = load_settings()
    gmail_user = resolve_outbound_gmail_user(ep, explicit=None)
    sent_folders = resolve_outbound_sent_folders(None)

    from origenlab_api.settings import get_settings
    from origenlab_api.sqlite_ro import open_operator_sqlite

    conn = open_operator_sqlite(sqlite_path, settings=get_settings())
    try:
        mart_row = _fetch_contact_master(conn, email_norm)
        if mart_row is None and _table_exists(conn, "contact_master"):
            warnings.append("No contact_master row for this email.")
        elif not _table_exists(conn, "contact_master"):
            reduced_mode = True
            warnings.append("contact_master table not present (mart not built).")

        org_row = _fetch_organization_master(conn, domain) if domain else None
        if domain and org_row is None and _table_exists(conn, "organization_master"):
            warnings.append("No organization_master row for contact domain.")

        outreach_row = fetch_outreach_contact_state_row(conn, email_norm)
        if outreach_row is None and _table_exists(conn, "outreach_contact_state"):
            warnings.append("No outreach_contact_state row (defaults to not_contacted semantics).")

        supp_email_row = fetch_contact_email_suppression_row(conn, email_norm)
        suppressed_domains = load_suppressed_contact_domain_norms(conn)
        suppressed_domain = _is_domain_suppressed(domain, suppressed_domains)

        sent = _fetch_sent_history(
            conn,
            email_norm,
            gmail_user=gmail_user,
            sent_folders=sent_folders,
        )

        org_name = ""
        if mart_row and mart_row.get("organization_name_guess"):
            org_name = str(mart_row["organization_name_guess"] or "")
        elif org_row and org_row.get("organization_name_guess"):
            org_name = str(org_row["organization_name_guess"] or "")

        org_domain = domain
        if mart_row and mart_row.get("domain"):
            org_domain = str(mart_row["domain"] or domain)
        elif org_row and org_row.get("domain"):
            org_domain = str(org_row["domain"] or domain)

        contact = {
            "email": email_raw.strip(),
            "normalized_email": email_norm,
            "name": str(mart_row.get("contact_name_best") or "") if mart_row else "",
            "domain": domain,
            "organization_name": org_name,
            "organization_domain": org_domain,
            "last_seen_at": _str_or_none(mart_row.get("last_seen_at") if mart_row else None),
            "first_seen_at": _str_or_none(mart_row.get("first_seen_at") if mart_row else None),
            "message_count": int(mart_row.get("total_emails") or 0) if mart_row else 0,
        }

        state = None
        last_contacted = None
        source = None
        updated_by = None
        notes = None
        if outreach_row:
            state = str(outreach_row.get("state") or "") or None
            last_contacted = _str_or_none(outreach_row.get("last_contacted_at"))
            source = _str_or_none(outreach_row.get("source"))
            updated_by = _str_or_none(outreach_row.get("updated_by"))
            notes = _str_or_none(outreach_row.get("notes"))

        do_not_repeat = email_norm in dnr_set
        if not do_not_repeat and state in _BLOCKING_OUTREACH_STATES:
            do_not_repeat = True
        if not do_not_repeat and sent["sent_count"] > 0:
            do_not_repeat = True

        outreach = {
            "state": state,
            "last_contacted_at": last_contacted,
            "source": source or "",
            "updated_by": updated_by or "",
            "notes": notes or "",
            "do_not_repeat": do_not_repeat,
            "suppressed_email": supp_email_row is not None,
            "suppressed_domain": suppressed_domain,
        }

        return contact, outreach, sent, warnings, reduced_mode
    finally:
        conn.close()


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _empty_contact(email_raw: str, email_norm: str, domain: str) -> dict[str, Any]:
    return {
        "email": email_raw.strip(),
        "normalized_email": email_norm,
        "name": "",
        "domain": domain,
        "organization_name": "",
        "organization_domain": domain,
        "last_seen_at": None,
        "first_seen_at": None,
        "message_count": 0,
    }


def _empty_outreach() -> dict[str, Any]:
    return {
        "state": None,
        "last_contacted_at": None,
        "source": "",
        "updated_by": "",
        "notes": "",
        "do_not_repeat": False,
        "suppressed_email": False,
        "suppressed_domain": False,
    }


def _empty_sent_history() -> dict[str, Any]:
    return {"sent_count": 0, "latest_sent_at": None, "latest_subject": None}
