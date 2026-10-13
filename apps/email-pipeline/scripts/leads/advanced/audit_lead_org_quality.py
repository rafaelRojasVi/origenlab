#!/usr/bin/env python3
"""Audit lead_master org_name quality: junk counts, top names, suspected bad rows."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.leads_schema import ensure_leads_tables
from origenlab_email_pipeline.org_normalize import is_junk_org_name, normalize_org_name


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit lead org_name quality")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    settings = load_settings()
    conn = connect(args.db or settings.resolved_sqlite_path())
    ensure_leads_tables(conn)

    cur = conn.execute(
        "SELECT id, org_name, domain, website, email, source_name, evidence_summary FROM lead_master"
    )
    total = 0
    junk = 0
    missing = 0
    raw_counter: Counter[str] = Counter()
    norm_counter: Counter[str] = Counter()
    suspect: list[tuple[int, str, str]] = []

    for lid, org, dom, web, em, src, evid in cur:
        total += 1
        o = (org or "").strip()
        if not o:
            missing += 1
        raw_counter[o or "(empty)"] += 1
        if is_junk_org_name(o):
            junk += 1
        nn = normalize_org_name(o)
        if nn:
            norm_counter[nn] += 1
        if (not o or is_junk_org_name(o)) and (dom or web or em):
            suspect.append((lid, o or "", f"domain={dom} web={web} email={em}"))

    print(f"lead_master rows: {total}")
    print(f"empty org_name: {missing}")
    print(f"junk org_name (heuristic): {junk}")
    print()
    print(f"Top {args.top} raw org_name:")
    for name, c in raw_counter.most_common(args.top):
        print(f"  {c:6d}  {name[:100]!r}")
    print()
    print(f"Top {args.top} normalized org_name:")
    for name, c in norm_counter.most_common(args.top):
        print(f"  {c:6d}  {name[:100]!r}")
    print()
    print("Sample suspect rows (junk/missing org but has domain/web/email), max 15:")
    for row in suspect[:15]:
        print(f"  id={row[0]} org={row[1]!r} {row[2]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
