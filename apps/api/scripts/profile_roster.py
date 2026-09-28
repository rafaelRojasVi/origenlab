#!/usr/bin/env python3
"""Plan or apply a shared Google sign-in and its operator profiles. See `origenlab_api.v2.profile_roster`.

    # 1. plan (read-only). PINs for new profiles: a hidden prompt per key, or a 0600 file.
    uv run python scripts/profile_roster.py --roster ~/data/origenlab-v2/profiles.json \\
        --prompt-pin ana --prompt-pin luis
    uv run python scripts/profile_roster.py --roster … --pin-file ~/data/origenlab-v2/pins.json

    # 2. apply exactly what the reviewed plan printed
    uv run python scripts/profile_roster.py --roster … --pin-file … \\
        --apply --confirm-changes <N> --confirm-database <name>

PINs are never accepted as arguments. The DSN is read from the environment variable named by
`--dsn-env` (default ORIGENLAB_V2_PROVISIONING_DATABASE_URL), never the command line; it must be
a login that may `set role origenlab_owner` (the migrator), not a runtime login. The pepper is
read from ORIGENLAB_PROFILE_PIN_PEPPER — the same value the API runs with, or its PINs will not
verify. Addresses are masked in all output; no PIN or hash is ever printed.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from origenlab_api.v2.profile_pin import PepperRefused, PinHasher, validate_pepper
from origenlab_api.v2.profile_roster import (
    ProfileRosterRefused,
    apply,
    check_pins,
    describe,
    load_pin_file,
    load_roster,
    plan,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _prompt_pins(keys: list[str]) -> dict[str, str]:
    if not sys.stdin.isatty():
        raise ProfileRosterRefused("--prompt-pin needs an interactive terminal; use --pin-file instead")
    pins: dict[str, str] = {}
    for key in keys:
        first = getpass.getpass(f"PIN for {key}: ")
        second = getpass.getpass(f"PIN for {key} (again): ")
        if first != second:
            raise ProfileRosterRefused(f"the two PINs entered for {key!r} differ")
        pins[key] = first
    return check_pins(pins)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--roster", type=Path, required=True)
    p.add_argument("--domain", default="origenlab.cl")
    p.add_argument("--dsn-env", default="ORIGENLAB_V2_PROVISIONING_DATABASE_URL")
    pin_source = p.add_mutually_exclusive_group()
    pin_source.add_argument("--prompt-pin", action="append", default=[], metavar="KEY",
                            help="prompt (hidden) for this profile's new PIN; repeatable")
    pin_source.add_argument("--pin-file", type=Path, help="0600 JSON file {key: PIN} outside the repository")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-changes", type=int)
    p.add_argument("--confirm-database")
    args = p.parse_args(argv)

    try:
        roster = load_roster(args.roster.expanduser(), workspace_domain=args.domain, repo_root=REPO_ROOT)
        if args.pin_file is not None:
            pins = load_pin_file(args.pin_file.expanduser(), repo_root=REPO_ROOT)
        else:
            pins = _prompt_pins(list(dict.fromkeys(args.prompt_pin))) if args.prompt_pin else {}
    except ProfileRosterRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if args.apply and (args.confirm_changes is None or not args.confirm_database):
        print("refused: --apply needs --confirm-changes N and --confirm-database NAME from a reviewed plan",
              file=sys.stderr)
        return 2
    try:
        pepper = validate_pepper(os.environ.get("ORIGENLAB_PROFILE_PIN_PEPPER"),
                                 session_secret=os.environ.get("ORIGENLAB_AUTH_SESSION_SECRET"))
    except PepperRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    dsn = os.environ.get(args.dsn_env, "").strip()
    if not dsn:
        print(f"refused: {args.dsn_env} is not set", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(dsn) as conn:
        try:
            if args.apply:
                result = apply(conn, roster, pins, hasher=PinHasher(pepper),
                               expected_changes=args.confirm_changes,
                               expected_database=args.confirm_database)
            else:
                result = plan(conn, roster, pins)
        except ProfileRosterRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        except psycopg.Error as exc:
            # The database's own refusal (a principal address that is an operator's, say). The
            # SQLSTATE is enough to act on; the message may quote a value, so it is not printed.
            print(f"refused by the database: SQLSTATE {exc.sqlstate}", file=sys.stderr)
            return 2
    print("APPLIED" if args.apply else "PLAN (read-only, nothing written)")
    print("\n".join(describe(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
