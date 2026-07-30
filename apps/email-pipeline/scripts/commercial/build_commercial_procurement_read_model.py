#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: default is dry-run (mode=ro + query_only). --apply is implemented for
# disposable / explicitly approved production paths only. This PR does not run
# production --apply. Never mutate Gmail or Postgres.
# See docs/audits/COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md.
# -----------------------------------------------------------------------------
"""Deterministic commercial procurement planner / apply (PR4).

Default dry-run produces the immutable build plan. --apply persists under
transaction contract B with stale-plan enforcement.

Exit codes::

  0  success
  2  path / run-context / mode validation
  3  missing or mismatched identity snapshot
  4  source or target schema incompatibility
  5  stale build plan / expected approval mismatch
  6  unsupported or unsafe invocation
  7  plan / persistence validation failure

Example (dry-run)::

  uv run python scripts/commercial/build_commercial_procurement_read_model.py \\
    --sqlite-path /explicit/path/to/emails.sqlite \\
    --as-of-date 2026-07-30 \\
    --run-context production_dry_run \\
    --json-summary

Example (fixture apply)::

  uv run python scripts/commercial/build_commercial_procurement_read_model.py \\
    --sqlite-path /tmp/fixture.sqlite \\
    --as-of-date 2026-07-30 \\
    --run-context local_fixture \\
    --apply \\
    --json-summary
"""

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
    RUN_CONTEXT_PRODUCTION_APPLY,
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
    VALID_RUN_CONTEXTS,
)
from origenlab_email_pipeline.commercial_procurement import (  # noqa: E402
    CommercialIdentityPathError,
    IdentityGateError,
    PersistenceValidationError,
    PlanValidationError,
    SchemaIncompatibilityError,
    SourceSchemaError,
    StaleProcurementBuildPlanError,
    TempSchemaValidationError,
    UnsafeInvocationError,
    require_explicit_sqlite_path,
    run_procurement_build,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        "--db",
        type=Path,
        dest="sqlite_path",
        required=True,
        help="Explicit SQLite path (required; no settings fallback).",
    )
    parser.add_argument(
        "--as-of-date",
        required=True,
        help="UTC calendar date YYYY-MM-DD used for procurement-context classification.",
    )
    parser.add_argument(
        "--run-context",
        choices=sorted(VALID_RUN_CONTEXTS),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
        help=(
            "Orchestrator-supplied run context. "
            f"Default: {RUN_CONTEXT_LOCAL_FIXTURE}. "
            f"Dry-run production checkpoint: {RUN_CONTEXT_PRODUCTION_DRY_RUN}. "
            f"Apply requires {RUN_CONTEXT_PRODUCTION_APPLY} (with expected fingerprints) "
            "or local_fixture / synthetic_fixture."
        ),
    )
    parser.add_argument(
        "--json-summary",
        action="store_true",
        help="Print machine-readable JSON summary to stdout.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist rebuildable commercial_procurement_* tables (transaction contract B).",
    )
    parser.add_argument(
        "--expected-source-fingerprint",
        default=None,
        help="Required for production_apply: approved dry-run source fingerprint.",
    )
    parser.add_argument(
        "--expected-identity-fingerprint",
        default=None,
        help="Required for production_apply: approved dry-run identity fingerprint.",
    )
    parser.add_argument(
        "--expected-build-plan-fingerprint",
        default=None,
        help="Required for production_apply: approved dry-run build-plan fingerprint.",
    )
    parser.add_argument(
        "--expected-semantic-plan-digest",
        default=None,
        help="Required for production_apply: approved dry-run semantic plan digest.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        sqlite_path = require_explicit_sqlite_path(args.sqlite_path)
        if args.apply and args.run_context == RUN_CONTEXT_PRODUCTION_DRY_RUN:
            raise CommercialIdentityPathError(
                "run-context production_dry_run is valid only without --apply"
            )
        if (not args.apply) and args.run_context == RUN_CONTEXT_PRODUCTION_APPLY:
            raise CommercialIdentityPathError(
                "run-context production_apply is valid only with --apply"
            )
        result = run_procurement_build(
            sqlite_path=sqlite_path,
            as_of_date=args.as_of_date,
            run_context=args.run_context,
            apply=bool(args.apply),
            expected_source_fingerprint=args.expected_source_fingerprint,
            expected_identity_fingerprint=args.expected_identity_fingerprint,
            expected_build_plan_fingerprint=args.expected_build_plan_fingerprint,
            expected_semantic_plan_digest=args.expected_semantic_plan_digest,
        )
    except UnsafeInvocationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 6
    except StaleProcurementBuildPlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except SchemaIncompatibilityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except CommercialIdentityPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except IdentityGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SourceSchemaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except (PlanValidationError, TempSchemaValidationError, PersistenceValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 7

    summary = result.summary
    if args.json_summary:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"mode={summary['mode']}")
        print(f"applied={summary['applied']}")
        print(f"sqlite_path={summary['sqlite_path']}")
        print(f"as_of_date={summary['as_of_date']}")
        print(f"run_context={summary['run_context']}")
        print(f"schema_version={summary['schema_version']}")
        print(f"build_contract={summary['build_contract']}")
        print(f"resolver={summary['resolver_build_contract_version']}")
        print(f"source_fingerprint={summary['source_fingerprint']}")
        print(f"identity_fingerprint={summary['identity_fingerprint']}")
        print(f"build_plan_fingerprint={summary['build_plan_fingerprint']}")
        print(f"semantic_plan_digest={summary['semantic_plan_digest']}")
        print(f"generated_at_utc={summary['generated_at_utc']}")
        print(f"signal_count={summary['signal_count']}")
        print(f"evidence_count={summary['evidence_count']}")
        print(f"resolution_distribution={summary['resolution_distribution']}")
        print(f"route_distribution={summary['route_distribution']}")
        print(f"operator_queue_eligible_count={summary['operator_queue_eligible_count']}")
        if summary.get("applied"):
            print(f"materialized_at_utc={summary.get('materialized_at_utc')}")
            print(f"written_rows={summary.get('written_rows')}")
            print(f"stale_plan_recheck_status={summary.get('stale_plan_recheck_status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
