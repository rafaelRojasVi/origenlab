#!/usr/bin/env python3
"""Plan or apply the dashboard operator roster. See `origenlab_api.v2.operator_roster`.

    uv run python scripts/operator_roster.py --roster ~/data/origenlab-v2-local/operator_roster.json
    uv run python scripts/operator_roster.py --roster … --apply --confirm-changes <N>

`<N>` is the pending count the reviewed plan printed — never a number picked in advance. The DSN is read from the environment variable named by `--dsn-env` (never from the command
line, so it stays out of shell history and process listings). Without `--apply` nothing is
written. Addresses are masked in all output.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from origenlab_api.v2.operator_roster import RosterRefused, apply, describe, load_roster, plan

REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--roster", type=Path, required=True)
    p.add_argument("--domain", default="origenlab.cl")
    p.add_argument("--dsn-env", default="ORIGENLAB_V2_DATABASE_URL")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-changes", type=int)
    args = p.parse_args(argv)

    try:
        entries = load_roster(args.roster.expanduser(), workspace_domain=args.domain, repo_root=REPO_ROOT)
    except RosterRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if args.apply and args.confirm_changes is None:
        print("refused: --apply needs --confirm-changes N (the pending count from a reviewed plan)", file=sys.stderr)
        return 2
    dsn = os.environ.get(args.dsn_env, "").strip()
    if not dsn:
        print(f"refused: {args.dsn_env} is not set", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(dsn) as conn:
        try:
            result = (
                apply(conn, entries, expected_changes=args.confirm_changes)
                if args.apply
                else plan(conn, entries)
            )
        except RosterRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    print("APPLIED" if args.apply else "PLAN (read-only, nothing written)")
    print("\n".join(describe(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
