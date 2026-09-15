#!/usr/bin/env python3
"""Process reviewed broad marketing contacts (DeepSearch volume lane).

Validates ``reviewed_marketing_contacts.csv``, dedupes, and splits against SQLite gate context
and, by default, ``do_not_repeat_master.csv``. ``--allow-prior-outreach`` is the explicit
repeat-campaign mode: prior Sent/contacted/replied history is informational, while hard
suppression, snoozed state, internal/supplier/noise checks still apply.

Does not send mail or import into lead_contact_research.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.core.outbound.broad_marketing_contacts import (
    REQUIRED_INPUT_COLUMNS,
    SEND_READY_FIELDS,
    blocked_output_fieldnames,
    build_marketing_contacts_summary,
    load_master_norms_from_csv,
    process_reviewed_marketing_rows,
    review_output_fieldnames,
    safe_output_fieldnames,
)
from origenlab_email_pipeline.csv_contracts import has_required_columns, read_csv_normalized
from origenlab_email_pipeline.outbound_core import (
    gate_context_for_lead_master_export,
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument(
        "--workspace",
        type=Path,
        default=_ROOT / "reports" / "out" / "active" / "current",
    )
    ap.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Defaults to <workspace>/reviewed_marketing_contacts.csv",
    )
    ap.add_argument(
        "--master",
        type=Path,
        default=None,
        help="Defaults to <workspace>/do_not_repeat_master.csv",
    )
    ap.add_argument("--gmail-user", default=None)
    ap.add_argument("--sent-folder", action="append", default=[])
    ap.add_argument(
        "--variant-type",
        default="broad_marketing",
        help="Written to send_ready_marketing.variant_type",
    )
    ap.add_argument(
        "--allow-prior-outreach",
        action="store_true",
        help=(
            "Repeat-campaign mode: do not hard-block solely because an address appears in "
            "Gmail Sent, historical contacted/replied state, or the legacy do-not-repeat master. "
            "Suppression, snoozed, internal/supplier/noise and quality checks still apply."
        ),
    )
    args = ap.parse_args(argv)

    workspace = Path(args.workspace)
    inp = Path(args.input) if args.input else workspace / "reviewed_marketing_contacts.csv"
    master_path = Path(args.master) if args.master else workspace / "do_not_repeat_master.csv"

    if not inp.is_file():
        print(f"Input not found: {inp}", file=sys.stderr)
        return 1

    settings = load_settings()
    db_path = args.db or settings.resolved_sqlite_path()
    if not db_path.is_file():
        print(f"SQLite database not found: {db_path}", file=sys.stderr)
        return 1

    workspace.mkdir(parents=True, exist_ok=True)
    out_safe = workspace / "marketing_safe_to_send.csv"
    out_blocked = workspace / "marketing_blocked_already_known.csv"
    out_review = workspace / "marketing_needs_manual_review.csv"
    out_send = workspace / "send_ready_marketing.csv"
    out_summary = workspace / "marketing_contacts_summary.json"

    headers, rows = read_csv_normalized(inp)
    ok, missing = has_required_columns(headers, REQUIRED_INPUT_COLUMNS)
    if not ok:
        print(f"Missing required columns: {', '.join(missing)}", file=sys.stderr)
        return 2

    # The legacy master intentionally merges all historical sends. That is useful
    # audit memory, but in an explicitly-declared repeat campaign it must not turn
    # "emailed once" into a permanent unsubscribe. Hard blockers are still loaded
    # directly from SQLite into GateContext below.
    master_set = set() if args.allow_prior_outreach else load_master_norms_from_csv(master_path)

    gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
    sent_folders = resolve_outbound_sent_folders(args.sent_folder)

    conn = _connect_readonly(db_path)
    try:
        ctx = gate_context_for_lead_master_export(
            conn,
            gmail_user=gmail_user,
            sent_folders=sent_folders,
            allow_prior_outreach_history=bool(args.allow_prior_outreach),
        )
    finally:
        conn.close()

    result = process_reviewed_marketing_rows(
        rows,
        master_email_norms=master_set,
        ctx=ctx,
        variant_type=args.variant_type,
    )

    def _write(path: Path, data: list[dict[str, str]], fieldnames: list[str]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            for r in data:
                w.writerow(r)

    _write(out_safe, result.safe_rows, safe_output_fieldnames())
    _write(out_blocked, result.blocked_rows, blocked_output_fieldnames())
    _write(out_review, result.review_rows, review_output_fieldnames())
    _write(out_send, result.send_ready_rows, list(SEND_READY_FIELDS))

    summary: dict[str, Any] = build_marketing_contacts_summary(
        db_path=db_path,
        workspace=workspace,
        input_path=inp,
        master_path=master_path,
        gmail_user=gmail_user,
        sent_folders=sent_folders,
        input_row_count=len(rows),
        result=result,
        out_safe=out_safe,
        out_blocked=out_blocked,
        out_review=out_review,
        out_send=out_send,
        out_summary=out_summary,
    )
    summary["policy"] = {
        "allow_prior_outreach_history": bool(args.allow_prior_outreach),
        "do_not_repeat_master_enforced": not bool(args.allow_prior_outreach),
        "hard_suppression_still_enforced": True,
        "snoozed_still_enforced": True,
    }

    out_summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("Broad marketing contacts")
    print(json.dumps(summary["counts"], indent=2))
    print("Policy:")
    print(json.dumps(summary["policy"], indent=2))
    print(f"Wrote: {out_safe}")
    print(f"Wrote: {out_blocked}")
    print(f"Wrote: {out_review}")
    print(f"Wrote: {out_send}")
    print(f"Wrote: {out_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
