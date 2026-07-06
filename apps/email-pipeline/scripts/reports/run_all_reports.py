#!/usr/bin/env python3
"""
Run all reports into a single timestamped folder.

Generates:
  - unique_emails.csv (all unique sender/recipient addresses + counts)
  - index.html (client dashboard with charts)
  - summary.json (full aggregates, by-year, top domains, etc.)
  - ALCANCE_INFORME.md (scope disclaimer for client)
  - business_filter_summary.json
  - business_only_sample.json
  - category_counts.csv
  - sender_domain_by_view.csv

  cd apps/email-pipeline   # from monorepo root
  uv run python scripts/reports/run_all_reports.py
  uv run python scripts/reports/run_all_reports.py --fast          # skip full domain scan (faster, fewer stats)
  uv run python scripts/reports/run_all_reports.py --embeddings   # add ML clusters (needs GPU/CUDA)
  uv run python scripts/reports/run_all_reports.py --dedupe       # safe dedupe dry-run preview
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

def _repo_root() -> Path:
    # scripts/reports/run_all_reports.py -> apps/email-pipeline
    return Path(__file__).resolve().parents[2]


_ROOT = _repo_root()
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run all reports (unique emails, client report, business filter) into one folder"
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory (default: reports/out/full_YYYYMMDD_HHMMSS)",
    )
    ap.add_argument(
        "--fast",
        action="store_true",
        help="Use --fast on client report (skip domain streaming; faster, fewer top-domains)",
    )
    ap.add_argument(
        "--embeddings",
        action="store_true",
        help="Run embeddings + clusters in client report (slower, needs ML stack)",
    )
    ap.add_argument(
        "--dedupe",
        action="store_true",
        help=(
            "Run canonical Gmail Message-ID dedupe dry-run before reports (no writes). "
            "Use scripts/maintenance/dedupe_canonical_gmail_messages.py with "
            "--apply --ack-sqlite-backup for break-glass deletes."
        ),
    )
    args = ap.parse_args()

    settings = load_settings()
    db_path = settings.resolved_sqlite_path()
    if not db_path.is_file():
        print("DB not found:", db_path, file=sys.stderr)
        sys.exit(1)

    reports_root = _ROOT / "reports" / "out"
    if args.out is not None:
        out_dir = Path(args.out)
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = reports_root / f"full_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Output directory:", out_dir.resolve())

    scripts_dir = _ROOT / "scripts"
    py = sys.executable

    def run(script: str, extra: list[str]) -> bool:
        cmd = [py, str(scripts_dir / script)] + extra
        print("\n---", script, "---")
        r = subprocess.run(cmd, cwd=str(_ROOT))
        if r.returncode != 0:
            print("Failed:", script, file=sys.stderr)
            return False
        return True

    if args.dedupe:
        if not run("maintenance/dedupe_canonical_gmail_messages.py", []):
            sys.exit(1)

    if not run(
        "tools/export_unique_emails_csv.py",
        ["--out", str(out_dir / "unique_emails.csv")],
    ):
        sys.exit(1)

    client_args = [
        "--out", str(out_dir),
        "--with-business-filter",
    ]
    if args.fast:
        client_args.append("--fast")
    if args.embeddings:
        client_args.extend(["--embeddings-sample", "2000", "--embeddings-clusters", "12"])
    if not run("reports/generate_client_report.py", client_args):
        sys.exit(1)

    print("\nDone. All artifacts in:", out_dir.resolve())
    print("  - unique_emails.csv")
    print("  - index.html (open in browser)")
    print("  - summary.json")
    print("  - ALCANCE_INFORME.md")
    print("  - business_filter_summary.json, business_only_sample.json")
    print("  - category_counts.csv, sender_domain_by_view.csv")


if __name__ == "__main__":
    main()
