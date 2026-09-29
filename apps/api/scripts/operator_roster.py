#!/usr/bin/env python3
"""Plan or apply the dashboard operator roster. See `origenlab_api.v2.operator_roster`.

    uv run python scripts/operator_roster.py --roster ~/data/origenlab-v2-local/operator_roster.json
    uv run python scripts/operator_roster.py --roster … --apply --confirm-changes <N> --confirm-database <name>

`<N>` and `<name>` are what the reviewed plan printed — never values picked in advance. The DSN
is read from the environment variable named by `--dsn-env` (default
ORIGENLAB_V2_PROVISIONING_DATABASE_URL; never from the command line, so it stays out of shell
history and process listings). It must be a login that may `set role origenlab_owner` — the
migrator: the runtime API role cannot write `platform.operator` at all. Without `--apply` nothing
is written. Addresses are masked in all output.
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
    p.add_argument("--dsn-env", default="ORIGENLAB_V2_PROVISIONING_DATABASE_URL")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-changes", type=int)
    p.add_argument("--confirm-database")
    args = p.parse_args(argv)

    try:
        entries = load_roster(args.roster.expanduser(), workspace_domain=args.domain, repo_root=REPO_ROOT)
    except RosterRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if args.apply and (args.confirm_changes is None or not args.confirm_database):
        print("refused: --apply needs --confirm-changes N and --confirm-database NAME from a reviewed plan",
              file=sys.stderr)
        return 2
    dsn = os.environ.get(args.dsn_env, "").strip()
    if not dsn:
        print(f"refused: {args.dsn_env} is not set", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(dsn) as conn:
        try:
            result = (
                apply(conn, entries, expected_changes=args.confirm_changes,
                      expected_database=args.confirm_database)
                if args.apply
                else plan(conn, entries)
            )
        except RosterRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        except psycopg.Error as exc:
            # The database's own refusal (a runtime login that may not assume the owner, say).
            print(f"refused by the database: SQLSTATE {exc.sqlstate}", file=sys.stderr)
            return 2
    print("APPLIED" if args.apply else "PLAN (read-only, nothing written)")
    print("\n".join(describe(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
