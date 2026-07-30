#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: dry-run only. Opens production SQLite with mode=ro + query_only=ON.
# Does not mutate SQLite, Gmail, or Postgres. --apply is refused in this PR.
# See docs/audits/COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md.
# -----------------------------------------------------------------------------
"""Deterministic commercial procurement planner dry-run (PR4).

Produces the complete immutable build plan a later persistence PR would insert.
Does not create commercial_procurement_* tables in production.

Exit codes::

  0  success
  2  path / run-context / mode validation
  3  missing or mismatched identity snapshot
  4  incompatible source schema
  6  --apply refused / not implemented
  7  temporary schema validation failure

Example::

  uv run python scripts/commercial/build_commercial_procurement_read_model.py \\
    --sqlite-path /explicit/path/to/emails.sqlite \\
    --as-of-date 2026-07-30 \\
    --run-context production_dry_run \\
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
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
    VALID_RUN_CONTEXTS,
)
from origenlab_email_pipeline.commercial_procurement import (  # noqa: E402
    ApplyNotImplementedError,
    CommercialIdentityPathError,
    IdentityGateError,
    PlanValidationError,
    SourceSchemaError,
    TempSchemaValidationError,
    require_explicit_sqlite_path,
    run_procurement_dry_run,
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
        choices=sorted(VALID_RUN_CONTEXTS - {"production_apply"}),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
        help=(
            "Orchestrator-supplied run context (metadata only). "
            f"Default: {RUN_CONTEXT_LOCAL_FIXTURE}. "
            f"Production checkpoint: {RUN_CONTEXT_PRODUCTION_DRY_RUN}."
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
        help="Not implemented in this PR — always refused.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        sqlite_path = require_explicit_sqlite_path(args.sqlite_path)
        result = run_procurement_dry_run(
            sqlite_path=sqlite_path,
            as_of_date=args.as_of_date,
            run_context=args.run_context,
            apply=bool(args.apply),
        )
    except ApplyNotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 6
    except CommercialIdentityPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except IdentityGateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SourceSchemaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except (PlanValidationError, TempSchemaValidationError) as exc:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
