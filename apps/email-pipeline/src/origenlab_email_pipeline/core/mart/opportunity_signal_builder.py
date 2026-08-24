"""Rebuild ``opportunity_signals`` from contact and organization rollups."""

from __future__ import annotations

import sqlite3
import time

from origenlab_email_pipeline.business_mart import signal_row
from origenlab_email_pipeline.core.mart.contact_org_builder import ContactMap, OrgMap
from origenlab_email_pipeline.core.mart.directional_opportunity_signal_builder import (
    compute_directional_opportunity_signal_rows,
)


def compute_opportunity_signal_rows(contact: ContactMap, org: OrgMap) -> list[tuple]:
    """Build opportunity signal rows in memory (no SQLite writes)."""
    sig_rows: list[tuple] = []
    for email, row in contact.items():
        if row["quote_email"] >= 2 and row["quote_doc"] >= 1:
            sig_rows.append(
                signal_row(
                    signal_type="quote_email_plus_quote_doc",
                    entity_kind="contact",
                    entity_key=email,
                    score=min(1.0, 0.2 + 0.1 * row["quote_email"] + 0.2 * min(row["quote_doc"], 3)),
                    details={"quote_email_count": row["quote_email"], "quote_doc_count": row["quote_doc"]},
                )
            )

    for d, o in org.items():
        if o["org_type"] == "education" and (o["quote_email"] >= 3 or o["quote_doc"] >= 1):
            sig_rows.append(
                signal_row(
                    signal_type="education_with_quote_activity",
                    entity_kind="organization",
                    entity_key=d,
                    score=min(1.0, 0.2 + 0.05 * o["quote_email"] + 0.2 * min(o["quote_doc"], 3)),
                    details={"quote_email_count": o["quote_email"], "quote_doc_count": o["quote_doc"]},
                )
            )

    cutoff = "2024-03-01"
    for email, row in contact.items():
        if (row["total"] or 0) >= 15 and row["last_seen_at"] and row["last_seen_at"] < cutoff:
            sig_rows.append(
                signal_row(
                    signal_type="dormant_contact",
                    entity_kind="contact",
                    entity_key=email,
                    score=0.6,
                    details={"last_seen_at": row["last_seen_at"], "total_emails": row["total"]},
                )
            )

    for email, row in contact.items():
        top_tag, top_cnt = (row["equip"].most_common(1) or [("", 0)])[0]
        if top_tag and top_cnt >= 5:
            sig_rows.append(
                signal_row(
                    signal_type="repeated_equipment_theme",
                    entity_kind="contact",
                    entity_key=email,
                    score=min(1.0, 0.25 + 0.05 * top_cnt),
                    details={"equipment_tag": top_tag, "mentions": top_cnt},
                )
            )

    return sig_rows


def rebuild_opportunity_signals(
    conn: sqlite3.Connection,
    contact: ContactMap,
    org: OrgMap,
) -> None:
    stage_t0 = time.monotonic()
    conn.execute("DELETE FROM opportunity_signals")
    sig_rows = compute_opportunity_signal_rows(contact, org)
    directional_rows = compute_directional_opportunity_signal_rows(conn)
    all_rows = sig_rows + directional_rows
    conn.executemany(
        """
        INSERT INTO opportunity_signals
        (signal_type, entity_kind, entity_key, email_id, attachment_id, score, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        all_rows,
    )
    conn.commit()
    print(f"opportunity_signals rows: {len(all_rows):,}")
    print(f"opportunity_signals directional rows: {len(directional_rows):,}")
    print(f"[timing] opportunity_signals_seconds={time.monotonic() - stage_t0:.2f}")
