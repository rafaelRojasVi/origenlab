#!/usr/bin/env python3
"""Scan emails.sqlite for Tatiana/Vivanco signals (headers + bodies) — discovery, not a label."""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.business_mart import domain_of, primary_sender_email
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

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\w")


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def redact_identity_text(text: str, *, max_len: int = 120) -> str:
    """Remove raw emails and truncate for safe stdout."""
    redacted = _EMAIL_RE.sub("<email:redacted>", text)
    if len(redacted) > max_len:
        return redacted[: max_len - 1] + "…"
    return redacted


def format_redacted_sender_sample(sender: str) -> str:
    pe = primary_sender_email(sender)
    domain = domain_of(pe or "") or "(no-address)"
    return f"from-domain={domain} id={_short_hash(sender)}"


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
    domain_trusted_identity: Counter[str]
    domain_tatiana_in_from: Counter[str]
    domain_vivanco_in_from: Counter[str]
    sender_samples_t: list[str]
    sender_samples_v: list[str]


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
    domain_trusted_identity: Counter[str] = Counter()
    domain_tatiana_in_from: Counter[str] = Counter()
    domain_vivanco_in_from: Counter[str] = Counter()
    sender_samples_t: list[str] = []
    sender_samples_v: list[str] = []

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
                pe = primary_sender_email(sender)
                dtf = domain_of(pe or "") or "(no address)"
                domain_tatiana_in_from[dtf] += 1
                if len(sender_samples_t) < 8:
                    sender_samples_t.append(sender)
            if v_in_s:
                hit_sender_v += 1
                pev = primary_sender_email(sender)
                dvf = domain_of(pev or "") or "(no address)"
                domain_vivanco_in_from[dvf] += 1
                if len(sender_samples_v) < 8:
                    sender_samples_v.append(sender)
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
                pe = primary_sender_email(sender)
                d = domain_of(pe or "") or "(no domain)"
                domain_trusted_identity[d] += 1

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
        domain_trusted_identity=domain_trusted_identity,
        domain_tatiana_in_from=domain_tatiana_in_from,
        domain_vivanco_in_from=domain_vivanco_in_from,
        sender_samples_t=sender_samples_t,
        sender_samples_v=sender_samples_v,
    )


def render_audit_report(
    stats: TatianaAuditStats,
    *,
    trusted_domain_count: int,
    sample_limit: int,
) -> str:
    lines: list[str] = []
    lines.append(f"Rows scanned: {stats.rows_scanned:,}")
    lines.append(f"Trusted sender domain count (internal ∪ voice): {trusted_domain_count}")
    lines.append("")
    lines.append("Counts — word-boundary Tatiana / Vivanco:")
    lines.append(f"  From header contains 'Tatiana': {stats.hit_sender_t:,}")
    if stats.domain_tatiana_in_from:
        lines.append("    → by parsed From-address domain (top 12):")
        for dom, c in stats.domain_tatiana_in_from.most_common(12):
            lines.append(f"       {dom}: {c:,}")
    lines.append(f"  From header contains 'Vivanco': {stats.hit_sender_v:,}")
    if stats.domain_vivanco_in_from:
        lines.append("    → by parsed From-address domain (top 12):")
        for dom, c in stats.domain_vivanco_in_from.most_common(12):
            lines.append(f"       {dom}: {c:,}")
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
    if stats.domain_trusted_identity:
        lines.append("  Sender domains (same rows):")
        for dom, c in stats.domain_trusted_identity.most_common(15):
            lines.append(f"    {dom}: {c:,}")
    lines.append("")
    if stats.sender_samples_t:
        lines.append(f"Sample From headers mentioning Tatiana (up to {sample_limit}, redacted):")
        for s in stats.sender_samples_t[:sample_limit]:
            lines.append(f"  {format_redacted_sender_sample(s)}")
    if stats.sender_samples_v:
        lines.append(f"Sample From headers mentioning Vivanco (up to {sample_limit}, redacted):")
        for s in stats.sender_samples_v[:sample_limit]:
            lines.append(f"  {format_redacted_sender_sample(s)}")
    lines.append("")
    lines.append(
        "Note: client replies often say “Hola Tatiana” in body; those usually have "
        "external From domains and are not counted as trusted-domain signature hits."
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=8, help="redacted examples per category (stdout)")
    args = ap.parse_args()

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
            sample_limit=args.sample,
        )
    )


if __name__ == "__main__":
    main()
