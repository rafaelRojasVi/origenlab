#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: read-only plan generation only. Opens SQLite mode=ro + query_only.
# Rejects --apply/--persist/--network/--ticket/--gmail/--postgres/--outreach/
# --schedule. No Ticket/OCDS acquisition. No relevance classification.
# See docs/audits/COMMERCIAL_PROCUREMENT_CANDIDATE_PLANNER_PR5C_COALESCENCE.md
# -----------------------------------------------------------------------------
"""Build a deterministic PR5C coalescence / lifecycle candidate plan (read-only)."""

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
from origenlab_email_pipeline.commercial_identity.builder import (  # noqa: E402
    CommercialIdentityPathError,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (  # noqa: E402
    DEFAULT_FRESHNESS_THRESHOLD_HOURS,
    FORBIDDEN_CLI_FLAGS,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.planner import (  # noqa: E402
    ReconciliationError,
    build_candidate_plan,
    write_plan_outputs,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_a_pr4 import (  # noqa: E402
    Pr4PlaneError,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.plane_b_acquisition import (  # noqa: E402
    AcquisitionPlaneError,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.walkthrough import (  # noqa: E402
    build_walkthrough_bundle,
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
        help="Repeatable path to a serialized AcquisitionSnapshot JSON.",
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
        result = build_candidate_plan(
            sqlite_path=args.sqlite_path,
            acquisition_snapshot_paths=list(args.acquisition_snapshot_json),
            as_of_utc=args.as_of_utc,
            freshness_threshold_hours=int(args.freshness_threshold_hours),
            run_context=args.run_context,
        )
        written = write_plan_outputs(result, args.out_dir)
        bundle = build_walkthrough_bundle(result)
        written.update(write_walkthrough(bundle, args.out_dir))
    except (
        Pr4PlaneError,
        AcquisitionPlaneError,
        ReconciliationError,
        CommercialIdentityPathError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = {
        "ok": True,
        "run_context": result.run_context,
        "as_of_utc": result.as_of_utc,
        "freshness_threshold_hours": result.freshness_threshold_hours,
        "input_source_fingerprint": result.input_source_fingerprint,
        "build_plan_fingerprint": result.build_plan_fingerprint,
        "semantic_digest": result.semantic_digest,
        "counts": result.aggregate_reconciliation.get("counts"),
        "out_dir": str(args.out_dir),
        "written": sorted(written.keys()),
        "relevance_implemented": False,
    }
    if args.json_summary:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"ok=true out_dir={args.out_dir}")
        print(f"input_fp={result.input_source_fingerprint}")
        print(f"build_fp={result.build_plan_fingerprint}")
        print(f"semantic={result.semantic_digest}")
        counts = result.aggregate_reconciliation.get("counts") or {}
        print(f"coalesced={counts.get('coalesced_tenders')}")
        print(f"unresolved={counts.get('unresolved_evidence')}")
        print(f"conflicts={counts.get('conflicts')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
