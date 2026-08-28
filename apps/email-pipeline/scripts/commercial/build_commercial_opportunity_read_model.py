#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (break-glass): --apply DELETE+rewrites commercial_opportunity_* *data*.
# Default is dry-run. Requires explicit --sqlite-path (no production fallback).
# Apply refuses unless persisted PR2 identity snapshot fingerprint matches.
# Transaction contract B: additive schema may remain; DELETE+INSERT is atomic
# with foreign_keys=ON. Does not mutate commercial_deal* or commercial_identity_*.
# Do not run --apply against production SQLite without operator approval.
# -----------------------------------------------------------------------------
"""Build the deterministic commercial opportunity stage read model (PR3).

Stage + evidence only — does not infer next action, tender, or product interest.
Does not mutate Gmail, suppressions, outreach state, classifications, or deals.

Exit codes (expected operator-contract failures — no Python traceback)::

  0  success
  2  path / run-context / mode validation (CommercialIdentityPathError)
  3  missing or mismatched identity snapshot (IdentitySnapshotError)
  4  incompatible source schema (SourceSchemaError)
  5  stale build plan / source race (StaleBuildPlanError)

Example (dry-run)::

  uv run python scripts/commercial/build_commercial_opportunity_read_model.py \\
    --sqlite-path /explicit/path/to/emails.sqlite \\
    --run-context local_fixture
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.cli_modes import (  # noqa: E402
    add_apply_dry_run_flags,
    resolve_apply_dry_run_mode,
)
from origenlab_email_pipeline.commercial_opportunity import (  # noqa: E402
    CommercialIdentityPathError,
    IdentitySnapshotError,
    SourceSchemaError,
    StaleBuildPlanError,
    require_explicit_sqlite_path,
    run_opportunity_build,
)
from origenlab_email_pipeline.commercial_opportunity.constants import (  # noqa: E402
    RUN_CONTEXT_LOCAL_FIXTURE,
    VALID_RUN_CONTEXTS,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        "--db",
        type=Path,
        dest="sqlite_path",
        required=True,
        help="Explicit SQLite path (required; no settings / ORIGENLAB_SQLITE_PATH fallback).",
    )
    add_apply_dry_run_flags(
        parser,
        apply_help=(
            "Write commercial_opportunity_* data (DELETE+rebuild in one transaction with "
            "foreign_keys=ON; additive schema may already exist — contract B). "
            "Requires matching persisted PR2 identity fingerprint."
        ),
    )
    parser.add_argument(
        "--run-context",
        choices=sorted(VALID_RUN_CONTEXTS),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
        help=(
            "Orchestrator-supplied run context label (metadata only; not commercial evidence). "
            f"Default: {RUN_CONTEXT_LOCAL_FIXTURE}."
        ),
    )
    parser.add_argument(
        "--json-summary",
        action="store_true",
        help="Print machine-readable JSON summary to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    mode = resolve_apply_dry_run_mode(parser, args)
    try:
        sqlite_path = require_explicit_sqlite_path(args.sqlite_path)
        summary = run_opportunity_build(
            sqlite_path=sqlite_path,
            apply=mode.apply,
            run_context=args.run_context,
        )
    except CommercialIdentityPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except IdentitySnapshotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SourceSchemaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except StaleBuildPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5

    if args.json_summary:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"mode={summary['mode']}")
        print(f"sqlite_path={summary['sqlite_path']}")
        print(f"run_context={summary.get('run_context')}")
        print(f"identity_fingerprint={summary.get('identity_fingerprint')}")
        print(
            f"identity_fingerprint_match_status="
            f"{summary.get('identity_fingerprint_match_status')}"
        )
        print(f"transaction_contract={summary.get('transaction_contract')}")
        print("planned_writes:")
        for name, count in sorted(summary["planned_writes"].items()):
            print(f"  {name}: {count}")
        metrics = summary.get("metrics") or {}
        print(f"label={metrics.get('label') or summary.get('run_context')}")
        print("metrics (fixture/local — not production unless explicitly authorized):")
        for key in (
            "source_deals_inspected",
            "source_events_inspected",
            "canonical_opportunity_count",
            "explicit_deal_opportunity_count",
            "evidence_candidate_count",
            "commercial_history_count",
            "current_opportunity_count",
            "terminal_opportunity_count",
            "linked_account_count",
            "linked_contact_count",
            "unresolved_identity_count",
            "opportunity_conflict_count",
            "missing_event_timestamp_count",
            "undated_signal_history_count",
            "opportunity_stage_fields_inferred",
            "next_action_fields_inferred",
            "tender_fields_inferred",
            "product_interest_fields_inferred",
        ):
            if key in metrics:
                print(f"  {key}: {metrics[key]}")
        if "stage_distribution" in metrics:
            print(f"  stage_distribution: {metrics['stage_distribution']}")
        if "conflict_distribution" in metrics:
            print(f"  conflict_distribution: {metrics['conflict_distribution']}")
        if summary.get("applied"):
            print(f"written: {summary.get('written')}")
        else:
            print("dry-run: no SQLite writes performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
