#!/usr/bin/env python3
"""Migrator-only maintenance of shared sign-in. See `origenlab_api.v2.profile_auth_admin`.

    # prune session rows that expired more than a day ago (plan, then apply the reviewed plan)
    uv run python scripts/profile_auth_admin.py prune-sessions
    uv run python scripts/profile_auth_admin.py prune-sessions \\
        --apply --confirm-changes <N> --confirm-database <name> --confirm-cutoff <timestamp>

The DSN is read from the environment variable named by `--dsn-env` (default
ORIGENLAB_V2_PROVISIONING_DATABASE_URL), never the command line; it must be a login that may
`set role origenlab_owner` (the migrator), not a runtime login. This is never a route.
"""

from __future__ import annotations

import argparse
import os
import sys

from origenlab_api.v2.profile_auth_admin import (
    AuthAdminRefused,
    apply_prune,
    describe_prune,
    parse_cutoff,
    plan_prune,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dsn-env", default="ORIGENLAB_V2_PROVISIONING_DATABASE_URL")
    sub = p.add_subparsers(dest="command", required=True)
    prune = sub.add_parser("prune-sessions", help="delete session rows that expired more than N days ago")
    prune.add_argument("--older-than-days", type=int, default=1)
    prune.add_argument("--apply", action="store_true")
    prune.add_argument("--confirm-changes", type=int)
    prune.add_argument("--confirm-database")
    prune.add_argument("--confirm-cutoff")
    return p


def _run(conn, args) -> list[str]:
    if args.command == "prune-sessions":
        if args.apply:
            if args.confirm_changes is None or not args.confirm_database or not args.confirm_cutoff:
                raise AuthAdminRefused("--apply needs --confirm-changes, --confirm-database and "
                                       "--confirm-cutoff from a reviewed plan")
            result = apply_prune(conn, cutoff=parse_cutoff(args.confirm_cutoff),
                                 expected_changes=args.confirm_changes,
                                 expected_database=args.confirm_database)
        else:
            result = plan_prune(conn, older_than_days=args.older_than_days)
        return describe_prune(result)
    raise AuthAdminRefused(f"unknown command {args.command!r}")  # pragma: no cover - argparse


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dsn = os.environ.get(args.dsn_env, "").strip()
    if not dsn:
        print(f"refused: {args.dsn_env} is not set", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(dsn) as conn:
        try:
            lines = _run(conn, args)
        except AuthAdminRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        except psycopg.Error as exc:
            # The database's own refusal; the SQLSTATE is enough to act on.
            print(f"refused by the database: SQLSTATE {exc.sqlstate}", file=sys.stderr)
            return 2
    print("APPLIED" if args.apply else "PLAN (read-only, nothing written)")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
