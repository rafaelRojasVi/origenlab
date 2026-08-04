#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: read-only product-relevance plan only. Opens SQLite mode=ro + query_only
# via PR5C. Rejects --apply/--persist/--network/--ticket/--gmail/--postgres/
# --outreach/--schedule. No LLM, embeddings, Gmail, Postgres, or outreach.
# See docs/audits/COMMERCIAL_PROCUREMENT_PRODUCT_RELEVANCE_PR5D.md
# -----------------------------------------------------------------------------
"""Build a deterministic PR5D procurement product-relevance plan (read-only)."""

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
from origenlab_email_pipeline.commercial_procurement_product_relevance.constants import (  # noqa: E402
    FORBIDDEN_CLI_FLAGS,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.planner import (  # noqa: E402
    RelevanceReconciliationError,
    build_product_relevance_plan,
    write_relevance_outputs,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.walkthrough import (  # noqa: E402
    build_cases_a_e,
    write_walkthrough,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", type=Path, required=True)
    parser.add_argument(
        "--acquisition-snapshot-json",
        type=Path,
        action="append",
        dest="acquisition_snapshot_json",
        required=True,
        help="Repeatable PR5B/PR5C acquisition snapshot JSON (same inputs as PR5C).",
    )
    parser.add_argument("--as-of-utc", required=True)
    parser.add_argument(
        "--freshness-threshold-hours",
        type=int,
        default=DEFAULT_FRESHNESS_THRESHOLD_HOURS,
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--run-context",
        choices=sorted(
            c
            for c in VALID_RUN_CONTEXTS
            if c in {RUN_CONTEXT_LOCAL_FIXTURE, RUN_CONTEXT_PRODUCTION_DRY_RUN}
        ),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
    )
    parser.add_argument(
        "--labeling-queue-size",
        type=int,
        default=200,
        help="Size of gitignored proposed labeling queue (not gold).",
    )
    parser.add_argument(
        "--json-summary",
        action="store_true",
        help="Print machine-readable summary to stdout.",
    )
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
    try:
        result = build_product_relevance_plan(
            sqlite_path=args.sqlite_path,
            acquisition_snapshot_paths=list(args.acquisition_snapshot_json),
            as_of_utc=args.as_of_utc,
            freshness_threshold_hours=int(args.freshness_threshold_hours),
            run_context=args.run_context,
            labeling_queue_size=int(args.labeling_queue_size),
        )
        written = write_relevance_outputs(result, args.out_dir)
        walk_dir = Path(str(args.out_dir) + "_walkthrough")
        # Prefer sibling under same parent if out-dir already timestamped.
        if args.out_dir.name.startswith("commercial_procurement_product_relevance"):
            walk_dir = args.out_dir.parent / (args.out_dir.name + "_walkthrough")
        bundle = build_cases_a_e(result)
        walk_written = write_walkthrough(bundle, walk_dir)
        written.update({f"walkthrough:{k}": v for k, v in walk_written.items()})
    except (RelevanceReconciliationError, ReportOutputError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json_summary:
        summary = result.to_summary_dict()
        summary["written"] = written
        summary["generated_at_utc_wall_clock_excluded_from_fingerprints"] = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print(
            "PR5D product relevance plan written "
            f"(decisions={result.counts.get('relevance_decisions')}, "
            f"abstain={result.counts.get('abstention_or_ambiguous')}, "
            f"not_persisted=true)"
        )
        for key, path in sorted(written.items()):
            print(f"  {key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
