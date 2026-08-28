#!/usr/bin/env python3
"""Build a unified do-not-repeat email master list (read-only).

Merges Gmail Sent recipients, outreach sidecar state, suppressions, marketing CSVs,
send manifests, and send_ready from the active reports tree. Does not modify SQLite.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.core.outbound.do_not_repeat_master import (
    MASTER_FIELDS,
    DoNotRepeatAgg,
    apply_gmail_sent_bounds,
    apply_outreach_state_dates,
    apply_suppression_set,
    build_do_not_repeat_summary,
    build_master_csv_rows,
    discover_active_files,
    format_email_list_txt,
    parse_send_manifest_payload,
    rel_path_for_docs,
    scan_csv_emails,
    touch_source_with_paths,
)
from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.outbound_core import (
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)
from origenlab_email_pipeline.outreach_contact_state import outreach_contact_state_table_exists


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _sent_recipient_date_bounds(
    conn: sqlite3.Connection,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
) -> tuple[dict[str, tuple[str, str]], int]:
    if not _table_exists(conn, "emails"):
        return {}, 0
    user = gmail_user.strip()
    folders = tuple(f.strip() for f in sent_folders if f.strip())
    if not user or not folders:
        return {}, 0
    like_pat = f"gmail:{user}/%".lower()
    ph = ",".join("?" * len(folders))
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(emails)").fetchall()}
    date_expr = (
        "COALESCE(NULLIF(TRIM(date_iso), ''), NULLIF(TRIM(date_raw), ''), '')"
        if "date_raw" in cols
        else "COALESCE(NULLIF(TRIM(date_iso), ''), '')"
    )
    cur = conn.execute(
        f"""
        SELECT recipients, {date_expr} AS dts
        FROM emails
        WHERE lower(source_file) LIKE ?
          AND folder IN ({ph})
        """,
        (like_pat, *folders),
    )
    out: dict[str, tuple[str, str]] = {}
    n_rows = 0
    for recipients, dts in cur:
        n_rows += 1
        ds = str(dts or "").strip()
        if not recipients:
            continue
        for em in emails_in(recipients):
            prev = out.get(em)
            if prev is None:
                out[em] = (ds, ds)
                continue
            old_min, old_max = prev
            new_min = ds if ds and (not old_min or ds < old_min) else old_min
            new_max = ds if ds and (not old_max or ds > old_max) else old_max
            out[em] = (new_min, new_max)
    return out, n_rows


def _load_outreach_blocking_with_dates(
    conn: sqlite3.Connection,
) -> dict[str, tuple[str, str]]:
    if not outreach_contact_state_table_exists(conn):
        return {}
    rows = conn.execute(
        """
        SELECT lower(trim(contact_email_norm)) AS e,
               COALESCE(NULLIF(TRIM(first_contacted_at), ''), ''),
               COALESCE(NULLIF(TRIM(last_contacted_at), ''), '')
        FROM outreach_contact_state
        WHERE state IN ('contacted', 'replied', 'snoozed')
          AND length(trim(contact_email_norm)) > 0
        """
    ).fetchall()
    out: dict[str, tuple[str, str]] = {}
    for e, f, l in rows:
        if not e:
            continue
        dates = [d for d in (str(f or "").strip(), str(l or "").strip()) if d]
        if not dates:
            out[str(e)] = ("", "")
        else:
            out[str(e)] = (min(dates), max(dates))
    return out


def _load_suppression_emails(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "contact_email_suppression"):
        return set()
    rows = conn.execute(
        "SELECT lower(trim(email)) AS e FROM contact_email_suppression WHERE length(trim(email)) > 0"
    ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument(
        "--reports-out-dir",
        type=Path,
        default=_ROOT / "reports" / "out" / "active",
    )
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--gmail-user", default=None)
    ap.add_argument("--sent-folder", action="append", default=[])
    args = ap.parse_args(argv)

    settings = load_settings()
    db_path = args.db or settings.resolved_sqlite_path()
    if not db_path.is_file():
        print(f"SQLite database not found: {db_path}", file=sys.stderr)
        return 1

    out_dir = args.out_dir or (args.reports_out_dir / "current")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "do_not_repeat_master.csv"
    txt_path = out_dir / "do_not_repeat_master.txt"
    summary_path = out_dir / "do_not_repeat_summary.json"

    gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
    sent_folders = resolve_outbound_sent_folders(args.sent_folder)
    rel_root = _ROOT

    conn = _connect_readonly(db_path)
    try:
        sent_bounds, _n_sent_rows = _sent_recipient_date_bounds(
            conn, gmail_user=gmail_user, sent_folders=sent_folders
        )
        outreach_dates = _load_outreach_blocking_with_dates(conn)
        suppressed = _load_suppression_emails(conn)
    finally:
        conn.close()

    agg: dict[str, DoNotRepeatAgg] = {}
    apply_gmail_sent_bounds(agg, sent_bounds)
    apply_outreach_state_dates(agg, outreach_dates)
    apply_suppression_set(agg, suppressed)

    file_email_counts: Counter[str] = Counter()

    for kind, name, path in discover_active_files(args.reports_out_dir):
        rel = rel_path_for_docs(path, rel_root)
        tag = f"{kind}:{name}"
        if kind == "send_manifest":
            try:
                payload: Any = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            emails, _root = parse_send_manifest_payload(payload)
            touch_source_with_paths(agg, emails, tag=tag, rel=rel)
            file_email_counts[rel] += len(set(emails))
            continue
        try:
            email_set = scan_csv_emails(path)
        except OSError:
            continue
        touch_source_with_paths(agg, email_set, tag=tag, rel=rel)
        file_email_counts[rel] += len(email_set)

    rows_out = build_master_csv_rows(agg)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    txt_path.write_text(format_email_list_txt(agg), encoding="utf-8")

    gen_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary = build_do_not_repeat_summary(
        generated_at=gen_at,
        db_path=db_path,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        reports_out_dir=args.reports_out_dir,
        agg=agg,
        file_email_counts=file_email_counts,
        csv_path=csv_path,
        txt_path=txt_path,
    )
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    n_gmail = int(summary["from_gmail_sent"])
    n_state = int(summary["from_outreach_state"])
    n_supp = int(summary["from_email_suppression"])
    n_mkt = int(summary["from_marketing_csv_or_manifest_or_send_ready_files"])
    top_files = list(summary["top_source_files_by_email_rows_scanned"])

    print("Do-not-repeat master (read-only)")
    print(f"  unique emails: {len(agg):,}")
    print(f"  from Gmail Sent: {n_gmail:,}")
    print(f"  from outreach_state (contacted|replied|snoozed): {n_state:,}")
    print(f"  from email_suppression: {n_supp:,}")
    print(f"  touched marketing/manifest/send_ready files (subset overlap): {n_mkt:,}")
    print("  top source files:")
    for p in top_files[:8]:
        print(f"    - {p} ({file_email_counts[p]:,} email cells)")
    print(f"Wrote: {csv_path}")
    print(f"Wrote: {txt_path}")
    print(f"Wrote: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
