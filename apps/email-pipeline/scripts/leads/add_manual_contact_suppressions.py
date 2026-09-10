#!/usr/bin/env python3
"""Upsert ``contact_email_suppression`` with reason ``manual_do_not_contact``.

Use for people who must never receive cold outreach (family, partners, etc.).
Add Fernanda Ojeda's address once you know it — Gmail-only aliases are not inferrable from the archive.

Example::

  uv run scripts/leads/add_manual_contact_suppressions.py \\
    --db ~/data/origenlab-email/sqlite/emails.sqlite \\
    persona.ejemplo@example.invalid \\
    --note "Never contact (family)"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.contact_email_suppression import (
    ensure_contact_email_suppression_table,
    upsert_contact_email_suppression,
    validate_contact_email_suppression_payload,
)
from origenlab_email_pipeline.db import connect


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Add or update manual do-not-contact rows in contact_email_suppression."
    )
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config)")
    ap.add_argument(
        "emails",
        nargs="+",
        help="One or more email addresses to suppress.",
    )
    ap.add_argument(
        "--note",
        default="manual_do_not_contact (CLI add_manual_contact_suppressions)",
        help="suppression_reason_text stored in DB.",
    )
    ap.add_argument(
        "--updated-by",
        default="add_manual_contact_suppressions.py",
        help="updated_by column (audit).",
    )
    args = ap.parse_args()

    db_path = args.db or load_settings().sqlite_path
    emails = [e.strip() for e in args.emails if e and str(e).strip()]
    if not emails:
        print("No emails given.", file=sys.stderr)
        return 2

    conn = connect(db_path)
    ensure_contact_email_suppression_table(conn)
    for raw in emails:
        payload = validate_contact_email_suppression_payload(
            email=raw,
            suppression_reason_code="manual_do_not_contact",
            suppression_reason_text=args.note,
            suppression_source="add_manual_contact_suppressions",
            last_bounced_at=None,
            updated_by=args.updated_by,
        )
        upsert_contact_email_suppression(conn, payload=payload)
        print(f"Suppressed: {payload.email}")
    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
