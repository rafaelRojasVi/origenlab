#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (break-glass): Deletes all SQLite rows for one mailbox (emails + sidecars).
# Dry-run by default; --apply is irreversible. Does not delete from Gmail.
# See docs/SCRIPT_MAP.md — "Break-glass scripts".
# -----------------------------------------------------------------------------
"""Remove one mailbox everywhere in the SQLite archive + operator sidecars.

Deletes ``emails`` rows whose From/Cc/To headers mention the address (angle-bracket safe),
cascades attachments / document_master / commercial facts as configured in schema,
removes ``contact_master``, ``contact_email_suppression``, ``outreach_contact_state``,
and lead junction rows for that exact email.

Does **not** delete from Gmail.

Example::

  uv run python scripts/tools/purge_mailbox_from_sqlite.py --email persona.ejemplo@example.invalid
  uv run python scripts/tools/purge_mailbox_from_sqlite.py --email persona.ejemplo@example.invalid --apply

Then rebuild mart if you rely on contact_master::

  uv run python scripts/mart/build_business_mart.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.config import load_settings


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _normalize_email(raw: str) -> str:
    found = emails_in((raw or "").strip().lower())
    if not found:
        raise ValueError(f"Not a single extractable mailbox: {raw!r}")
    return found[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge one email address from SQLite archive + sidecars.")
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config)")
    ap.add_argument("--email", required=True, help="Full address, e.g. persona.ejemplo@example.invalid")
    ap.add_argument("--apply", action="store_true", help="Execute deletes (default: counts only).")
    args = ap.parse_args()

    try:
        norm = _normalize_email(args.email)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    db_path = args.db or load_settings().resolved_sqlite_path()
    if not db_path.is_file():
        print("Database file not found:", db_path, file=sys.stderr)
        return 1

    like_hdr = f"%{norm}%"

    conn = sqlite3.connect(str(db_path), timeout=120.0)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        ids = [
            int(r[0])
            for r in conn.execute(
                """
                SELECT id FROM emails
                WHERE lower(COALESCE(sender, '')) LIKE ?
                   OR lower(COALESCE(recipients, '')) LIKE ?
                """,
                (like_hdr, like_hdr),
            ).fetchall()
        ]
        print(f"emails with From/To/Cc mentioning {norm!r}: {len(ids):,}")

        if _table_exists(conn, "contact_master"):
            n = conn.execute(
                "SELECT COUNT(*) FROM contact_master WHERE lower(email) = ?",
                (norm,),
            ).fetchone()[0]
            print(f"contact_master rows for this mailbox: {n:,}")
        if _table_exists(conn, "contact_email_suppression"):
            n = conn.execute(
                "SELECT COUNT(*) FROM contact_email_suppression WHERE lower(email) = ?",
                (norm,),
            ).fetchone()[0]
            print(f"contact_email_suppression rows: {n:,}")
        if _table_exists(conn, "outreach_contact_state"):
            n = conn.execute(
                "SELECT COUNT(*) FROM outreach_contact_state WHERE contact_email_norm = ?",
                (norm,),
            ).fetchone()[0]
            print(f"outreach_contact_state rows: {n:,}")

        if not args.apply:
            print("\nDry run. Pass --apply to delete.")
            return 0

        if ids:
            placeholders = ",".join("?" * len(ids))
            if _table_exists(conn, "opportunity_signals"):
                conn.execute(f"DELETE FROM opportunity_signals WHERE email_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM emails WHERE id IN ({placeholders})", ids)
            print(f"Deleted emails rows: {len(ids):,}")

        if _table_exists(conn, "contact_master"):
            cur = conn.execute("DELETE FROM contact_master WHERE lower(email) = ?", (norm,))
            print(f"Deleted contact_master rows: {cur.rowcount:,}")
        if _table_exists(conn, "contact_email_suppression"):
            cur = conn.execute("DELETE FROM contact_email_suppression WHERE lower(email) = ?", (norm,))
            print(f"Deleted contact_email_suppression rows: {cur.rowcount:,}")
        if _table_exists(conn, "outreach_contact_state"):
            cur = conn.execute("DELETE FROM outreach_contact_state WHERE contact_email_norm = ?", (norm,))
            print(f"Deleted outreach_contact_state rows: {cur.rowcount:,}")

        if _table_exists(conn, "lead_upstream_reconcile_log") and _table_exists(conn, "lead_master"):
            cur = conn.execute(
                """
                DELETE FROM lead_upstream_reconcile_log
                WHERE lead_id IN (
                  SELECT id FROM lead_master WHERE lower(trim(COALESCE(email,''))) = ?
                     OR lower(trim(COALESCE(email_norm,''))) = ?
                )
                """,
                (norm, norm),
            )
            print(f"Deleted lead_upstream_reconcile_log rows: {cur.rowcount:,}")
        if _table_exists(conn, "lead_matches_existing_contacts"):
            cur = conn.execute(
                "DELETE FROM lead_matches_existing_contacts WHERE lower(matched_contact_email) = ?",
                (norm,),
            )
            print(f"Deleted lead_matches_existing_contacts rows: {cur.rowcount:,}")
        if _table_exists(conn, "lead_master"):
            cur = conn.execute(
                """
                DELETE FROM lead_master
                WHERE lower(trim(COALESCE(email,''))) = ?
                   OR lower(trim(COALESCE(email_norm,''))) = ?
                """,
                (norm, norm),
            )
            print(f"Deleted lead_master rows: {cur.rowcount:,}")

        conn.commit()
    finally:
        conn.close()

    print("Done. Rebuild mart if needed: uv run python scripts/mart/build_business_mart.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
