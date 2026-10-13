#!/usr/bin/env python3
"""Read-only audit: supplier_master domains that may be false positives for lead outreach.

Compares gate blocker domains (supplier_master.domain_norm) against lead_master domains and
flags public/government/academic-style patterns. Does not modify SQLite or gate logic.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.lead_export_queries import sql_upstream_active_lead_master


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open SQLite read-only (avoids PRAGMA side effects that mutate the file header)."""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn

_FIELDNAMES = [
    "domain_norm",
    "supplier_name",
    "supplier_tier",
    "is_exclusion",
    "supplier_source",
    "supplier_notes",
    "matching_lead_count",
    "matching_high_fit_count",
    "matching_medium_fit_count",
    "example_lead_ids",
    "example_organization_names",
    "likely_false_positive_reason",
    "recommended_action",
]

_DOMAIN_KEYWORDS = (
    "universidad",
    "university",
    "municipal",
    "muni",
    "hospital",
    "gobierno",
    "ministerio",
    "salud.",
    "pjud",
    "usc.cl",
    "udec",
    "uchile",
    "usach",
    "utalca",
    "uach",
    "ufr",
    "instituto",
    "senado",
    "congreso",
    "defensa",
    "ejercito",
    "carabineros",
    "fiscalia",
)

_NAME_KEYWORDS = (
    "universidad",
    "hospital",
    "municipal",
    "ministerio",
    "gobierno",
    "instituto",
    "servicio agricola",
    "sag",
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _institutional_false_positive_signals(domain_norm: str, trade_name: str | None) -> tuple[bool, str]:
    d = (domain_norm or "").strip().lower()
    n = (trade_name or "").strip().lower()
    reasons: list[str] = []

    if d.endswith(".gob.cl") or ".gob." in d:
        reasons.append("gov_gob_cl_domain")
    if d.endswith(".edu") or ".edu." in d or d.endswith(".ac.uk") or d.endswith(".ac.cr"):
        reasons.append("edu_academic_tld")
    if re.search(r"\.gob\.|\.gov\.|\.mil\.|\.int$", d) or d.endswith(".gov"):
        reasons.append("gov_style_tld")

    for kw in _DOMAIN_KEYWORDS:
        if kw in d:
            reasons.append(f"domain_keyword:{kw}")
            break
    for kw in _NAME_KEYWORDS:
        if kw in n:
            reasons.append(f"name_keyword:{kw}")
            break

    if not reasons:
        return False, ""
    return True, ";".join(sorted(set(reasons)))


def _recommended_action(
    *,
    institutional: bool,
    matching_lead_count: int,
    high_fit: int,
    medium_fit: int,
) -> str:
    hi_med = high_fit + medium_fit
    if institutional and hi_med > 0:
        return "review_supplier_exclusion"
    if institutional:
        return "needs_manual_review"
    if matching_lead_count == 0:
        return "no_matching_leads"
    if matching_lead_count > 0:
        return "likely_true_supplier"
    return "needs_manual_review"


def _load_lead_domain_index(conn: sqlite3.Connection) -> dict[str, list[tuple[int, str, str]]]:
    """Map normalized domain -> [(lead_id, org_name, fit_bucket), ...]."""
    if not _table_exists(conn, "lead_master"):
        return {}
    up = sql_upstream_active_lead_master("lm")
    cur = conn.execute(
        f"""
        SELECT lm.id, lm.org_name, COALESCE(lm.fit_bucket, 'low_fit') AS fit_bucket,
               lower(trim(coalesce(nullif(trim(lm.domain_norm), ''), nullif(trim(lm.domain), '')))) AS dom
        FROM lead_master lm
        WHERE {up}
          AND nullif(trim(coalesce(nullif(trim(lm.domain_norm), ''), nullif(trim(lm.domain), ''))), '') IS NOT NULL
        """
    )
    out: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for lead_id, org_name, fit_bucket, dom in cur:
        if not dom:
            continue
        out[str(dom)].append((int(lead_id), str(org_name or "").strip(), str(fit_bucket)))
    return dict(out)


def _load_supplier_tier_source(conn: sqlite3.Connection) -> dict[int, tuple[str, str, int]]:
    """supplier_id -> (tier, source_filename, batch_id) for latest batch_id row."""
    if not _table_exists(conn, "supplier_priority_snapshot"):
        return {}
    rows = conn.execute(
        """
        SELECT supplier_id, batch_id, tier
        FROM supplier_priority_snapshot
        """
    ).fetchall()
    best: dict[int, tuple[str, int]] = {}
    for sid, bid, tier in rows:
        sid_i, bid_i = int(sid), int(bid)
        prev = best.get(sid_i)
        if prev is None or bid_i > prev[1]:
            best[sid_i] = (str(tier or ""), bid_i)
    if not best:
        return {}
    batch_ids = {b for (_, b) in best.values()}
    batch_files: dict[int, str] = {}
    if _table_exists(conn, "supplier_import_batch") and batch_ids:
        ph = ",".join("?" * len(batch_ids))
        for bid, fn in conn.execute(
            f"SELECT id, source_filename FROM supplier_import_batch WHERE id IN ({ph})",
            tuple(sorted(batch_ids)),
        ).fetchall():
            batch_files[int(bid)] = str(fn or "")
    out: dict[int, tuple[str, str, int]] = {}
    for sid, (tier, bid) in best.items():
        out[sid] = (tier, batch_files.get(bid, ""), bid)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True, help="Output CSV path.")
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config).")
    ap.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max rows written to CSV after filters (default: 5000).",
    )
    ap.add_argument(
        "--include-zero-lead-domains",
        action="store_true",
        help="Include supplier domains with no matching lead_master domain (default: omit).",
    )
    args = ap.parse_args()

    if args.limit < 1:
        print("--limit must be >= 1", file=sys.stderr)
        return 2

    settings = load_settings()
    db_path = args.db or settings.resolved_sqlite_path()
    if not db_path.is_file():
        print(f"SQLite file not found: {db_path}", file=sys.stderr)
        return 1

    conn = _connect_readonly(db_path)
    try:
        if not _table_exists(conn, "supplier_master"):
            print("supplier_master missing; nothing to audit.", file=sys.stderr)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=_FIELDNAMES)
                w.writeheader()
            print("total supplier domains scanned: 0")
            print("domains matching leads: 0")
            print("likely false positives: 0")
            print("high/medium impact count: 0")
            print("top 10 likely false positives: (none)")
            return 0

        lead_by_dom = _load_lead_domain_index(conn)
        tier_src = _load_supplier_tier_source(conn)

        suppliers = conn.execute(
            """
            SELECT id, lower(trim(domain_norm)) AS domain_norm, trade_name, notes, is_exclusion
            FROM supplier_master
            WHERE domain_norm IS NOT NULL AND trim(domain_norm) != ''
            ORDER BY domain_norm
            """
        ).fetchall()
    finally:
        conn.close()

    total_scanned = len(suppliers)
    domains_matching_leads = 0
    likely_fp_rows: list[dict[str, object]] = []
    all_evaluated: list[dict[str, object]] = []

    for sid, domain_norm, trade_name, notes, is_exclusion in suppliers:
        dom = str(domain_norm or "").strip().lower()
        if not dom:
            continue
        matches = lead_by_dom.get(dom, [])
        n_leads = len(matches)
        hi = sum(1 for _, _, fb in matches if fb == "high_fit")
        med = sum(1 for _, _, fb in matches if fb == "medium_fit")
        if n_leads > 0:
            domains_matching_leads += 1

        ex_ids = [str(m[0]) for m in matches[:5]]
        ex_names = [m[1] for m in matches[:5] if m[1]]
        tier_info = tier_src.get(int(sid), ("", "", 0))
        tier, src_file = tier_info[0], tier_info[1]

        inst, reason = _institutional_false_positive_signals(dom, trade_name)
        action = _recommended_action(
            institutional=inst,
            matching_lead_count=n_leads,
            high_fit=hi,
            medium_fit=med,
        )

        row = {
            "domain_norm": dom,
            "supplier_name": str(trade_name or "").strip(),
            "supplier_tier": tier,
            "is_exclusion": int(is_exclusion or 0),
            "supplier_source": src_file,
            "supplier_notes": str(notes or "").strip(),
            "matching_lead_count": n_leads,
            "matching_high_fit_count": hi,
            "matching_medium_fit_count": med,
            "example_lead_ids": ",".join(ex_ids),
            "example_organization_names": "; ".join(ex_names),
            "likely_false_positive_reason": reason if inst else "",
            "recommended_action": action,
        }
        all_evaluated.append(row)
        if inst and reason:
            likely_fp_rows.append(row)

    likely_fp_count = len([r for r in all_evaluated if r["likely_false_positive_reason"]])
    hi_med_impact = sum(
        int(r["matching_high_fit_count"]) + int(r["matching_medium_fit_count"])
        for r in all_evaluated
        if r["likely_false_positive_reason"]
    )

    fp_sorted = sorted(
        likely_fp_rows,
        key=lambda r: (
            -(int(r["matching_high_fit_count"]) + int(r["matching_medium_fit_count"])),
            -int(r["matching_lead_count"]),
            str(r["domain_norm"]),
        ),
    )
    top10 = fp_sorted[:10]
    top10_s = ", ".join(
        f"{r['domain_norm']}({int(r['matching_high_fit_count'])}H+{int(r['matching_medium_fit_count'])}M)"
        for r in top10
    ) or "(none)"

    out_rows: list[dict[str, object]] = []
    for row in all_evaluated:
        if not args.include_zero_lead_domains and int(row["matching_lead_count"]) == 0:
            continue
        out_rows.append(row)
        if len(out_rows) >= int(args.limit):
            break

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    print(f"Wrote {len(out_rows)} rows to {args.out}")
    print(f"total supplier domains scanned: {total_scanned}")
    print(f"domains matching leads: {domains_matching_leads}")
    print(f"likely false positives: {likely_fp_count}")
    print(f"high/medium impact count: {hi_med_impact}")
    print(f"top 10 likely false positives: {top10_s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
