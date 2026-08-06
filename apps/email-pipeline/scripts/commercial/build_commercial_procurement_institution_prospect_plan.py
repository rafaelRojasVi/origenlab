#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: read-only institution prospect map over PR5C→PR5D→PR5E.
# No network. No --apply DB writes. Rejects --ticket/--gmail/--postgres/--send.
# Does not authorize outreach or persist institution profiles.
# See docs/audits/COMMERCIAL_PROCUREMENT_INSTITUTION_PROSPECTS_PR5E1.md
# -----------------------------------------------------------------------------
"""Build institution-level prospect intelligence from live+historical tenders."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.commercial_identity.constants import (  # noqa: E402
    RUN_CONTEXT_LOCAL_FIXTURE,
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
    VALID_RUN_CONTEXTS,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (  # noqa: E402
    DEFAULT_FRESHNESS_THRESHOLD_HOURS,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (  # noqa: E402
    ReportOutputError,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.constants import (  # noqa: E402
    FORBIDDEN_CLI_FLAGS,
)
from origenlab_email_pipeline.commercial_procurement_institution_prospects.planner import (  # noqa: E402
    build_institution_prospect_plan,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.constants import (  # noqa: E402
    LIVE_FEED_MAX_AGE_HOURS,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (  # noqa: E402
    LiveFeedFreshnessError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument(
        "--acquisition-snapshot-json",
        type=Path,
        action="append",
        default=None,
        help="Repeatable AcquisitionSnapshot JSON path (alternative to detail-cache).",
    )
    parser.add_argument(
        "--detail-cache-dir",
        type=Path,
        default=None,
        help="ChileCompra equipment detail-cache directory (PR5B.2 seam).",
    )
    parser.add_argument("--as-of-utc", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--equipment-manifest", type=Path, default=None)
    parser.add_argument("--refresh-state", type=Path, default=None)
    parser.add_argument(
        "--freshness-threshold-hours",
        type=int,
        default=DEFAULT_FRESHNESS_THRESHOLD_HOURS,
    )
    parser.add_argument(
        "--max-feed-age-hours",
        type=int,
        default=LIVE_FEED_MAX_AGE_HOURS,
    )
    parser.add_argument("--allow-stale-feed", action="store_true")
    parser.add_argument(
        "--run-context",
        choices=sorted(
            c
            for c in VALID_RUN_CONTEXTS
            if c in {RUN_CONTEXT_LOCAL_FIXTURE, RUN_CONTEXT_PRODUCTION_DRY_RUN}
        ),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
    )
    parser.add_argument("--json-summary", action="store_true")
    return parser


def _reject_forbidden(argv: list[str]) -> None:
    for flag in FORBIDDEN_CLI_FLAGS:
        if flag in argv:
            raise SystemExit(f"error: forbidden flag {flag}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _reject_forbidden(argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.acquisition_snapshot_json and not args.detail_cache_dir:
        raise SystemExit(
            "error: provide --detail-cache-dir and/or --acquisition-snapshot-json"
        )

    print("PR5E.1 institution prospect inputs:")
    print(f"  sqlite_path={args.sqlite_path.resolve()}")
    if args.detail_cache_dir:
        print(f"  detail_cache_dir={args.detail_cache_dir.resolve()}")
    if args.acquisition_snapshot_json:
        print(f"  acquisition_snapshots={len(args.acquisition_snapshot_json)}")
    print(f"  as_of_utc={args.as_of_utc}")
    print(f"  out_dir={args.out_dir}")

    try:
        written = build_institution_prospect_plan(
            sqlite_path=args.sqlite_path,
            as_of_utc=args.as_of_utc,
            out_dir=args.out_dir,
            run_context=args.run_context,
            freshness_threshold_hours=args.freshness_threshold_hours,
            acquisition_snapshot_paths=list(args.acquisition_snapshot_json or []),
            detail_cache_dir=args.detail_cache_dir,
            equipment_manifest_path=args.equipment_manifest,
            refresh_state_path=args.refresh_state,
            allow_stale_feed=args.allow_stale_feed,
            max_feed_age_hours=args.max_feed_age_hours,
        )
    except (LiveFeedFreshnessError, ReportOutputError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary_path = Path(written["summary.json"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print("Wrote:")
    for name, path in sorted(written.items()):
        print(f"  {name}: {path}")
    print(
        "counts:",
        json.dumps(summary.get("counts", {}), ensure_ascii=False, sort_keys=True),
    )
    if args.json_summary:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
