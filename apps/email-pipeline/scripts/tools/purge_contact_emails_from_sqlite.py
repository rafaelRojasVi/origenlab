#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (break-glass): Deletes SQLite rows across many tables (emails, sidecars,
# commercial keys, etc.). Dry-run by default; --apply is irreversible. Does not
# delete from Gmail. See docs/SCRIPT_MAP.md — "Break-glass scripts".
# -----------------------------------------------------------------------------
"""Remove archived mail and related SQLite rows for specific contact email addresses.

For each address, finds ``emails`` rows whose ``sender`` or ``recipients`` header fields
contain that address (case-insensitive substring match), then:

- Deletes matching ``opportunity_signals`` rows: by ``email_id`` when linked to
  matching ``emails`` rows, and by ``entity_key`` when it equals the contact
  address (mart signals often use ``email_id IS NULL``).
- Deletes those ``emails`` rows (``ON DELETE CASCADE`` cleans attachments,
  ``attachment_extracts``, ``document_master``, ``commercial_email_signal_fact`` where FK exists).
- Deletes ``contact_master`` rows for the same normalized addresses.
- Optionally cleans operator sidecars and commercial durable rows keyed by contact email.

**This does not delete anything from Gmail.** See ``purge_email_domain_from_sqlite.py`` docstring
for mailbox cleanup.

Dry-run by default; pass ``--apply`` to execute.

Example::

  uv run python scripts/tools/purge_contact_emails_from_sqlite.py \\
    --email contacto.ejemplo1@example.invalid \\
    --email contacto.ejemplo2@example.invalid

Apply::

  uv run python scripts/tools/purge_contact_emails_from_sqlite.py --apply \\
    --email a@example.com --email b@example.com

Afterwards, rebuild the mart if you rely on derived tables::

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

from origenlab_email_pipeline.config import load_settings


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _collect_email_ids(conn: sqlite3.Connection, addr: str) -> list[int]:
    needle = f"%{_norm_email(addr)}%"
    return [
        int(r[0])
        for r in conn.execute(
            """
            SELECT id FROM emails
            WHERE lower(COALESCE(sender, '')) LIKE ?
               OR lower(COALESCE(recipients, '')) LIKE ?
            """,
            (needle, needle),
        ).fetchall()
    ]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Purge SQLite archive + sidecar rows for specific contact email addresses.",
    )
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config)")
    ap.add_argument(
        "--email",
        action="append",
        default=[],
        dest="emails",
        help="Contact email (repeatable)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Execute deletes (default: print counts only).",
    )
    ap.add_argument(
        "--no-commercial-candidates",
        action="store_true",
        help="Do not delete contact_candidate / rollups / review rows for these emails.",
    )
    args = ap.parse_args()

    raw_addrs = [_norm_email(x) for x in (args.emails or []) if _norm_email(x)]
    if not raw_addrs:
        print("Pass at least one --email.", file=sys.stderr)
        return 2

    db_path = args.db or load_settings().resolved_sqlite_path()
    if not db_path.is_file():
        print("Database file not found:", db_path, file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path), timeout=120.0)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        all_ids: set[int] = set()
        per_addr: dict[str, list[int]] = {}
        for a in raw_addrs:
            ids = _collect_email_ids(conn, a)
            per_addr[a] = ids
            all_ids.update(ids)

        print("Per-address emails.id matches:")
        for a, ids in sorted(per_addr.items()):
            print(f"  {a}: {len(ids):,}")
        print(f"Unique emails.id to delete: {len(all_ids):,}")

        if _table_exists(conn, "contact_master"):
            ph = ",".join("?" * len(raw_addrs))
            c = conn.execute(
                f"SELECT COUNT(*) FROM contact_master WHERE lower(email) IN ({ph})",
                raw_addrs,
            ).fetchone()[0]
            print(f"contact_master rows for these emails: {c:,}")

        if not args.apply:
            print("\nDry run. Pass --apply to delete.")
            return 0

        ph = ",".join("?" * len(raw_addrs))

        ids_list = sorted(all_ids)
        if _table_exists(conn, "opportunity_signals"):
            n_sig_key = conn.execute(
                f"DELETE FROM opportunity_signals WHERE lower(trim(entity_key)) IN ({ph})",
                raw_addrs,
            ).rowcount
            print(f"Deleted opportunity_signals rows (entity_key match): {n_sig_key:,}")
        if ids_list:
            placeholders = ",".join("?" * len(ids_list))
            if _table_exists(conn, "opportunity_signals"):
                n_sig = conn.execute(
                    f"DELETE FROM opportunity_signals WHERE email_id IN ({placeholders})",
                    ids_list,
                ).rowcount
                print(f"Deleted opportunity_signals rows (email_id match): {n_sig:,}")
            conn.execute(f"DELETE FROM emails WHERE id IN ({placeholders})", ids_list)
            print(f"Deleted emails rows: {len(ids_list):,}")

        if _table_exists(conn, "contact_master"):
            cur = conn.execute(
                f"DELETE FROM contact_master WHERE lower(email) IN ({ph})",
                raw_addrs,
            )
            print(f"Deleted contact_master rows: {cur.rowcount:,}")

        if _table_exists(conn, "outreach_contact_state"):
            cur = conn.execute(
                f"DELETE FROM outreach_contact_state WHERE contact_email_norm IN ({ph})",
                raw_addrs,
            )
            print(f"Deleted outreach_contact_state rows: {cur.rowcount:,}")

        if _table_exists(conn, "contact_email_suppression"):
            cur = conn.execute(
                f"DELETE FROM contact_email_suppression WHERE lower(email) IN ({ph})",
                raw_addrs,
            )
            print(f"Deleted contact_email_suppression rows: {cur.rowcount:,}")

        if not args.no_commercial_candidates:
            if _table_exists(conn, "commercial_contact_signal_rollup"):
                cur = conn.execute(
                    f"DELETE FROM commercial_contact_signal_rollup WHERE lower(contact_email) IN ({ph})",
                    raw_addrs,
                )
                print(f"Deleted commercial_contact_signal_rollup rows: {cur.rowcount:,}")
            if _table_exists(conn, "contact_candidate"):
                cur = conn.execute(
                    f"DELETE FROM contact_candidate WHERE lower(contact_email) IN ({ph})",
                    raw_addrs,
                )
                print(f"Deleted contact_candidate rows: {cur.rowcount:,}")
            if _table_exists(conn, "candidate_review_event"):
                cur = conn.execute(
                    """
                    DELETE FROM candidate_review_event
                    WHERE entity_kind = 'contact' AND lower(entity_key) IN ({})
                    """.format(ph),
                    raw_addrs,
                )
                print(f"Deleted candidate_review_event (contact) rows: {cur.rowcount:,}")
            if _table_exists(conn, "candidate_manual_override"):
                cur = conn.execute(
                    """
                    DELETE FROM candidate_manual_override
                    WHERE entity_kind = 'contact' AND lower(entity_key) IN ({})
                    """.format(ph),
                    raw_addrs,
                )
                print(f"Deleted candidate_manual_override (contact) rows: {cur.rowcount:,}")
            if _table_exists(conn, "commercial_opportunity_fact"):
                cur = conn.execute(
                    f"DELETE FROM commercial_opportunity_fact WHERE lower(top_contact_email) IN ({ph})",
                    raw_addrs,
                )
                print(f"Deleted commercial_opportunity_fact rows (top_contact_email): {cur.rowcount:,}")

        if _table_exists(conn, "lead_matches_existing_contacts"):
            cur = conn.execute(
                f"DELETE FROM lead_matches_existing_contacts WHERE lower(matched_contact_email) IN ({ph})",
                raw_addrs,
            )
            print(f"Deleted lead_matches_existing_contacts rows: {cur.rowcount:,}")

        conn.commit()
    finally:
        conn.close()

    print("Done. Rebuild mart if needed: uv run python scripts/mart/build_business_mart.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
