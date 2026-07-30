#!/usr/bin/env python3
"""Read-only PR4 procurement ↔ account-link audit.

Requires explicit --sqlite-path and --output-dir.
Opens SQLite with mode=ro and PRAGMA query_only=ON.
Does not mutate production data. Does not run --apply.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.readonly import (
    ProcurementLinkAuditPathError,
    require_explicit_paths,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.runner import (
    run_procurement_link_audit,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sqlite-path", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument(
        "--allow-output-outside-report-root",
        action="store_true",
        help="Allow --output-dir outside reports/out (tests/tmp only).",
    )
    args = p.parse_args(argv)
    try:
        db, out = require_explicit_paths(
            sqlite_path=args.sqlite_path,
            output_dir=args.output_dir,
            allow_output_outside_report_root=args.allow_output_outside_report_root,
        )
    except ProcurementLinkAuditPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = run_procurement_link_audit(sqlite_path=db, output_dir=out)
    print(json.dumps({"ok": True, "output_dir": str(out), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
