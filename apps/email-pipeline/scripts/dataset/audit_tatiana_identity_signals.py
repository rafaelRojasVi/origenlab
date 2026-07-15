#!/usr/bin/env python3
"""Scan emails.sqlite for Tatiana/Vivanco signals (headers + bodies) — discovery, not a label.

Stdout contains numeric aggregates and static labels only. No sender-derived
address, display name, domain, subject, body, hash, or identifier is retained
or printed (CodeQL alerts #20/#22 / py/clear-text-logging-sensitive-data).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.progress import iter_sqlite_email_batches_with_progress
from origenlab_email_pipeline.tatiana_voice_cohort import (
    _RE_TATIANA,
    _RE_VIVANCO,
    load_voice_sender_domains,
    sender_domain_matches_voice_domains,
    trusted_domains_for_identity_mentions,
)


@dataclass
class TatianaAuditStats:
    rows_scanned: int
    hit_sender_t: int
    hit_sender_v: int
    hit_subj_t: int
    hit_subj_v: int
    hit_full_t: int
    hit_full_v: int
    hit_top_t: int
    hit_top_v: int
    hit_any_t: int
    hit_any_v: int
    hit_trusted_identity_in_from_or_body: int


def scan_tatiana_identity_signals(conn: sqlite3.Connection) -> TatianaAuditStats:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT id, sender, subject,
               COALESCE(full_body_clean, '') AS full_body_clean,
               COALESCE(top_reply_clean, '') AS top_reply_clean
        FROM emails
        """
    )

    voice_domains = load_voice_sender_domains()
    trusted = trusted_domains_for_identity_mentions(voice_domains)

    n = 0
    hit_sender_t = hit_sender_v = 0
    hit_subj_t = hit_subj_v = 0
    hit_full_t = hit_full_v = 0
    hit_top_t = hit_top_v = 0
    hit_any_t = hit_any_v = 0
    hit_trusted_identity_in_from_or_body = 0

    for batch in iter_sqlite_email_batches_with_progress(
        conn, cur, desc="Audit Tatiana/Vivanco signals"
    ):
        for row in batch:
            n += 1
            sender = row["sender"] or ""
            subj = row["subject"] or ""
            full = row["full_body_clean"] or ""
            top = row["top_reply_clean"] or ""

            t_in_s = bool(_RE_TATIANA.search(sender))
            v_in_s = bool(_RE_VIVANCO.search(sender))
            t_in_sub = bool(_RE_TATIANA.search(subj))
            v_in_sub = bool(_RE_VIVANCO.search(subj))
            t_in_f = bool(_RE_TATIANA.search(full))
            v_in_f = bool(_RE_VIVANCO.search(full))
            t_in_top = bool(_RE_TATIANA.search(top))
            v_in_top = bool(_RE_VIVANCO.search(top))

            if t_in_s:
                hit_sender_t += 1
            if v_in_s:
                hit_sender_v += 1
            if t_in_sub:
                hit_subj_t += 1
            if v_in_sub:
                hit_subj_v += 1
            if t_in_f:
                hit_full_t += 1
            if v_in_f:
                hit_full_v += 1
            if t_in_top:
                hit_top_t += 1
            if v_in_top:
                hit_top_v += 1

            if t_in_s or t_in_sub or t_in_f or t_in_top:
                hit_any_t += 1
            if v_in_s or v_in_sub or v_in_f or v_in_top:
                hit_any_v += 1

            if sender_domain_matches_voice_domains(sender, trusted) and (
                t_in_s or v_in_s or t_in_f or v_in_f or t_in_top or v_in_top
            ):
                hit_trusted_identity_in_from_or_body += 1

    return TatianaAuditStats(
        rows_scanned=n,
        hit_sender_t=hit_sender_t,
        hit_sender_v=hit_sender_v,
        hit_subj_t=hit_subj_t,
        hit_subj_v=hit_subj_v,
        hit_full_t=hit_full_t,
        hit_full_v=hit_full_v,
        hit_top_t=hit_top_t,
        hit_top_v=hit_top_v,
        hit_any_t=hit_any_t,
        hit_any_v=hit_any_v,
        hit_trusted_identity_in_from_or_body=hit_trusted_identity_in_from_or_body,
    )


def render_audit_report(
    stats: TatianaAuditStats,
    *,
    trusted_domain_count: int,
) -> str:
    """Build a numeric-aggregate stdout report (no sender-derived strings)."""
    lines: list[str] = []
    lines.append(f"Rows scanned: {stats.rows_scanned:,}")
    lines.append(f"Trusted sender domain count (internal ∪ voice): {trusted_domain_count}")
    lines.append("")
    lines.append("Counts — word-boundary Tatiana / Vivanco:")
    lines.append(f"  From header contains 'Tatiana': {stats.hit_sender_t:,}")
    lines.append(f"  From header contains 'Vivanco': {stats.hit_sender_v:,}")
    lines.append(f"  Subject contains 'Tatiana': {stats.hit_subj_t:,}")
    lines.append(f"  Subject contains 'Vivanco': {stats.hit_subj_v:,}")
    lines.append(f"  full_body_clean contains 'Tatiana': {stats.hit_full_t:,}")
    lines.append(f"  full_body_clean contains 'Vivanco': {stats.hit_full_v:,}")
    lines.append(f"  top_reply_clean contains 'Tatiana': {stats.hit_top_t:,}")
    lines.append(f"  top_reply_clean contains 'Vivanco': {stats.hit_top_v:,}")
    lines.append(f"  Any field above — Tatiana: {stats.hit_any_t:,}")
    lines.append(f"  Any field above — Vivanco: {stats.hit_any_v:,}")
    lines.append("")
    lines.append(
        "Trusted-domain senders with Tatiana/Vivanco in From OR clean body "
        f"(cohort-style signal): {stats.hit_trusted_identity_in_from_or_body:,}"
    )
    lines.append("")
    lines.append(
        "Note: client replies often say “Hola Tatiana” in body; those usually have "
        "external From domains and are not counted as trusted-domain signature hits."
    )
    lines.append(
        "Privacy: stdout reports numeric aggregates and static labels only; "
        "no sender-derived address, display name, domain, subject, body, hash, "
        "or identifier is retained or printed."
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sample",
        type=int,
        default=8,
        help="ignored (identity samples are never printed; kept for CLI compatibility)",
    )
    args = ap.parse_args()
    _ = args.sample  # accepted, unused

    settings = load_settings()
    db_path = settings.resolved_sqlite_path()
    if not db_path.is_file():
        print("SQLite DB not found for configured path.", file=sys.stderr)
        sys.exit(1)

    voice_domains = load_voice_sender_domains()
    trusted = trusted_domains_for_identity_mentions(voice_domains)

    conn = connect(db_path)
    try:
        stats = scan_tatiana_identity_signals(conn)
    finally:
        conn.close()

    print(
        render_audit_report(
            stats,
            trusted_domain_count=len(trusted),
        )
    )


if __name__ == "__main__":
    main()
