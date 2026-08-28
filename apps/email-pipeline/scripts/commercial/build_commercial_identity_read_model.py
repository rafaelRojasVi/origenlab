#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (break-glass): --apply DELETE+rewrites commercial_identity_* *data*.
# Default is dry-run. Requires explicit --sqlite-path (no production fallback).
# Transaction contract B: additive schema (executescript) may remain after a
# first-run failure; DELETE+INSERT data replacement is atomic with foreign_keys=ON.
# Do not run --apply against production SQLite without operator approval.
# See docs/SCRIPT_MAP.md.
# -----------------------------------------------------------------------------
"""Build the deterministic commercial account/contact identity read model (PR2).

Identity only — does not infer opportunity stage, next action, or product interest.
Does not mutate Gmail, suppressions, outreach state, or classifications.

Transaction contract B: schema ensure is additive; data replacement rolls back atomically.

Example (dry-run)::

  uv run python scripts/commercial/build_commercial_identity_read_model.py \\
    --sqlite-path /explicit/path/to/emails.sqlite

Example (apply to a non-production fixture)::

  uv run python scripts/commercial/build_commercial_identity_read_model.py \\
    --sqlite-path /tmp/fixture.sqlite --apply
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
from origenlab_email_pipeline.commercial_identity import (  # noqa: E402
    CommercialIdentityPathError,
    require_explicit_sqlite_path,
    run_identity_build,
)
from origenlab_email_pipeline.commercial_identity.constants import (  # noqa: E402
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
            "Write commercial_identity_* data (DELETE+rebuild in one transaction with "
            "foreign_keys=ON; additive schema may already exist — contract B)."
        ),
    )
    parser.add_argument(
        "--run-context",
        choices=sorted(VALID_RUN_CONTEXTS),
        default=RUN_CONTEXT_LOCAL_FIXTURE,
        help=(
            "Orchestrator-supplied run context label (metadata only; not commercial evidence). "
            f"Default: {RUN_CONTEXT_LOCAL_FIXTURE}. Use production_dry_run / production_apply "
            "explicitly for production authorization paths."
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
        summary = run_identity_build(
            sqlite_path=sqlite_path,
            apply=mode.apply,
            run_context=args.run_context,
        )
    except CommercialIdentityPathError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json_summary:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"mode={summary['mode']}")
        print(f"sqlite_path={summary['sqlite_path']}")
        print(f"run_context={summary.get('run_context')}")
        print(f"identity_fingerprint={summary.get('identity_fingerprint')}")
        print(f"transaction_contract={summary.get('transaction_contract')}")
        print("planned_writes:")
        for name, count in sorted(summary["planned_writes"].items()):
            print(f"  {name}: {count}")
        metrics = summary.get("metrics") or {}
        label = metrics.get("label") or summary.get("run_context")
        print(f"label={label}")
        print("metrics (fixture/local — not production unless explicitly authorized):")
        for key in (
            "source_identity_rows_inspected",
            "canonical_account_count",
            "canonical_contact_count",
            "contacts_linked_to_accounts",
            "unlinked_contacts",
            "institutional_domain_links",
            "consumer_domain_auto_link_refusals",
            "account_conflicts",
            "contact_conflicts",
            "records_without_usable_email",
            "records_without_usable_organization_identity",
            "origenlab_origin_source_assertion_rows",
            "labdelivery_origin_source_assertion_rows",
            "research_origin_source_assertion_rows",
            "canonical_contacts_with_origenlab_origin",
            "canonical_contacts_with_labdelivery_origin",
            "canonical_contacts_with_research_origin",
            "canonical_contacts_research_only",
        ):
            if key in metrics:
                print(f"  {key}: {metrics[key]}")
        if summary.get("applied"):
            print(f"written: {summary.get('written')}")
        else:
            print("dry-run: no SQLite writes performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
