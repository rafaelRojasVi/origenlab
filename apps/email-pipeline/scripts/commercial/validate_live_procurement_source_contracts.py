#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY: PR5B.1 bounded read-only live source-contract validation only.
# Requires BOTH:
#   --execute-live-contract-validation
#   --confirm-read-only-contract-validation
# Without those flags: no-network plan only.
# Rejects --apply / --persist / --database / --postgres / --gmail / --outreach /
# --schedule / --network-unbounded / --ticket CLI value.
# Ticket solely from CHILECOMPRA_API_TICKET (never CLI/config/output).
# Max 4 authenticated + 4 public requests; no automatic retries.
# Does not start PR5C, mutate SQLite/Gmail/Postgres, or download attachments.
# -----------------------------------------------------------------------------
"""Validate PR5B parsers against tightly bounded real Mercado Público responses."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.runner import (  # noqa: E402
    run_live_validation,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.live_contract.transport import (  # noqa: E402
    plan_requests,
)


def _default_out_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        _ROOT
        / "reports"
        / "out"
        / "active"
        / "current"
        / f"commercial_procurement_real_contract_validation_{stamp}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--execute-live-contract-validation",
        action="store_true",
        help="Required with --confirm-read-only-contract-validation to perform network I/O.",
    )
    parser.add_argument(
        "--confirm-read-only-contract-validation",
        action="store_true",
        help="Required confirmation that this run is read-only contract validation.",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--ticket-summary-limit-details",
        type=int,
        default=3,
        help="Max detail requests after summary (maximum 3).",
    )
    parser.add_argument(
        "--authenticated-request-budget",
        type=int,
        default=4,
        help="Fixed maximum authenticated Ticket API requests (maximum 4).",
    )
    parser.add_argument(
        "--public-request-budget",
        type=int,
        default=4,
        help="Fixed maximum public OCDS probe requests (maximum 4).",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--run-context", default="pr5b1_live_contract")

    # Explicitly reject unsafe modes (SUPPRESS keeps them out of --help noise).
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--network-unbounded", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--schedule", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--persist", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--database", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--postgres", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--gmail", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--outreach", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ticket", default=None, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    rejected = []
    if args.apply:
        rejected.append("--apply")
    if args.network_unbounded:
        rejected.append("--network-unbounded")
    if args.schedule:
        rejected.append("--schedule")
    if args.persist:
        rejected.append("--persist")
    if args.database is not None:
        rejected.append("--database")
    if args.postgres:
        rejected.append("--postgres")
    if args.gmail:
        rejected.append("--gmail")
    if args.outreach:
        rejected.append("--outreach")
    if args.ticket is not None:
        rejected.append("--ticket")
    if rejected:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "rejected_flags",
                    "flags": rejected,
                    "note": "ticket may only come from CHILECOMPRA_API_TICKET",
                }
            )
        )
        return 2

    if args.ticket_summary_limit_details > 3 or args.ticket_summary_limit_details < 0:
        print(json.dumps({"ok": False, "error": "ticket_summary_limit_details_max_3"}))
        return 2
    if args.authenticated_request_budget > 4 or args.authenticated_request_budget < 1:
        print(json.dumps({"ok": False, "error": "authenticated_request_budget_max_4"}))
        return 2
    if args.public_request_budget > 4 or args.public_request_budget < 0:
        print(json.dumps({"ok": False, "error": "public_request_budget_max_4"}))
        return 2

    plan = plan_requests(
        ticket_summary_limit_details=args.ticket_summary_limit_details,
        authenticated_request_budget=args.authenticated_request_budget,
        public_request_budget=args.public_request_budget,
    )

    execute = (
        args.execute_live_contract_validation
        and args.confirm_read_only_contract_validation
    )
    if not execute:
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "plan_only_no_network",
                    "message": (
                        "No network performed. Pass both "
                        "--execute-live-contract-validation and "
                        "--confirm-read-only-contract-validation to run."
                    ),
                    "plan": plan,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    out_dir = args.out_dir or _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_live_validation(
            out_dir=out_dir,
            ticket_summary_limit_details=args.ticket_summary_limit_details,
            authenticated_request_budget=args.authenticated_request_budget,
            public_request_budget=args.public_request_budget,
            timeout_seconds=args.timeout_seconds,
            run_context=args.run_context,
            reference_fixtures_dir=_ROOT
            / "tests"
            / "fixtures"
            / "commercial_procurement_acquisition",
        )
    except Exception as exc:  # noqa: BLE001 — never print str(exc) / payload content
        from origenlab_email_pipeline.chilecompra_api import ChileCompraTicketMissingError

        classification = "live_contract_validation_failed"
        if type(exc) is AssertionError and getattr(exc, "args", ()) == (
            "identifier_leak_detected",
        ):
            classification = "identifier_leak_detected"
        elif type(exc) is RuntimeError:
            arg0 = (getattr(exc, "args", ()) or (None,))[0]
            if isinstance(arg0, str) and arg0.endswith("_request_budget_exhausted"):
                classification = "request_budget_exhausted"
            else:
                classification = "runtime_error"
        elif isinstance(exc, ChileCompraTicketMissingError):
            classification = "ticket_env_missing_or_invalid"
        elif type(exc) is ValueError:
            arg0 = (getattr(exc, "args", ()) or (None,))[0]
            if arg0 == "unsafe_sanitized_query_fields":
                classification = "unsafe_sanitized_query_fields"
            else:
                classification = "validation_error"
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "live_contract_validation_failed",
                    "error_classification": classification,
                }
            )
        )
        return 1

    # Terminal output: digests/counts only — never tender codes or payloads.
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "live_contract_validation",
                "out_dir": result["out_dir"],
                "authenticated_attempted": result["budget"]["authenticated_attempted"],
                "public_attempted": result["budget"]["public_attempted"],
                "range_conclusion": result["range_conclusion"],
                "pr5b_correction_required": result["pr5b_correction_required"],
                "sanitized_fixture_count": len(result["sanitized_digests"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
