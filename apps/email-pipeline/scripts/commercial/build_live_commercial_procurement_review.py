#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: read-only review over existing ChileCompra detail-cache artifacts.
# No network. No --apply DB writes. Rejects --ticket/--gmail/--postgres/--send.
# Reuses PR5C/PR5D/PR5E planners; does not authorize outreach.
# See docs/audits/COMMERCIAL_PROCUREMENT_LIVE_FEED_BRIDGE_PR5B2.md
# -----------------------------------------------------------------------------
"""Bridge live equipment detail cache into a PR5C→PR5D→PR5E operator review."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.constants import (  # noqa: E402
    FORBIDDEN_CLI_FLAGS,
    LIVE_FEED_MAX_AGE_HOURS,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.freshness import (  # noqa: E402
    LiveFeedFreshnessError,
)
from origenlab_email_pipeline.commercial_procurement_live_feed_bridge.planner import (  # noqa: E402
    build_live_feed_review,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument(
        "--detail-cache-dir",
        type=Path,
        required=True,
        help="Existing chilecompra_detail_cache directory (Ticket detail JSON).",
    )
    parser.add_argument("--as-of-utc", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--equipment-manifest",
        type=Path,
        default=None,
        help="Optional equipment_first_operator_queue_chilecompra_api_*.manifest.json",
    )
    parser.add_argument(
        "--refresh-state",
        type=Path,
        default=None,
        help="Optional chilecompra_equipment_auto_refresh_state.json",
    )
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
    parser.add_argument(
        "--allow-stale-feed",
        action="store_true",
        help="Permit review when feed age exceeds max-feed-age-hours (still no network).",
    )
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

    print("PR5B.2 live-feed bridge inputs:")
    print(f"  sqlite_path={args.sqlite_path.resolve()}")
    print(f"  detail_cache_dir={args.detail_cache_dir.resolve()}")
    if args.equipment_manifest:
        print(f"  equipment_manifest={args.equipment_manifest.resolve()}")
    if args.refresh_state:
        print(f"  refresh_state={args.refresh_state.resolve()}")
    print(f"  as_of_utc={args.as_of_utc}")
    print(f"  out_dir={args.out_dir}")

    try:
        result = build_live_feed_review(
            sqlite_path=args.sqlite_path,
            detail_cache_dir=args.detail_cache_dir,
            as_of_utc=args.as_of_utc,
            out_dir=args.out_dir,
            run_context=args.run_context,
            freshness_threshold_hours=int(args.freshness_threshold_hours),
            equipment_manifest_path=args.equipment_manifest,
            refresh_state_path=args.refresh_state,
            allow_stale_feed=bool(args.allow_stale_feed),
            max_feed_age_hours=int(args.max_feed_age_hours),
        )
    except LiveFeedFreshnessError as exc:
        payload = {
            "ok": False,
            "code": exc.code,
            "error": str(exc),
            "details": exc.details,
        }
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        return 3
    except (ReportOutputError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    written = result["written"]
    summary = result["summary"]
    if args.json_summary:
        out = {
            **summary,
            "written": written,
            "generated_at_utc_wall_clock_excluded_from_fingerprints": (
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            ),
        }
        print(json.dumps(out, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(
            "PR5B.2 live feed review written "
            f"(live_backed={summary.get('live_backed_review_population')}, "
            f"buckets={summary.get('by_commercial_bucket')}, "
            f"actionable_leads={summary.get('actionable_lead_count')})"
        )
        for key in (
            "summary.json",
            "operator_review_packet.json",
            "operator_review.csv",
            "walkthrough.md",
        ):
            if key in written:
                print(f"  {key}: {written[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
