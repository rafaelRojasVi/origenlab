#!/usr/bin/env python3
"""Emit the declared baseline as a probe stream, with optional overrides. Test fixture only.

`supabase/scripts/cleanroom_failure_tests.sh` uses this to prove that `compare.py` actually
fails on the things it claims to fail on — a drifted count, a probe nobody measured, a probe
nobody declared, and the exact fixture residue found in `origenlab_dev` on 2026-09-22.

It opens no database. It is not part of the build or the verification, and `cleanroom_db.sh`
never calls it: a verification that could be handed its own expected answer would prove
nothing.

Usage:
    emit_expected_for_tests.py [key=value ...] [--drop key] [--add key=value]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED = Path(__file__).resolve().parent / "expected_counts.json"


def main(argv: list[str]) -> int:
    probes = json.loads(EXPECTED.read_text())["probes"]
    values = {key: str(spec["expect"]) for key, spec in probes.items()}

    args = list(argv[1:])
    while args:
        arg = args.pop(0)
        if arg == "--drop":
            values.pop(args.pop(0), None)
        elif arg == "--add":
            key, _, value = args.pop(0).partition("=")
            values[key] = value
        else:
            key, _, value = arg.partition("=")
            if key not in values:
                print(f"no such probe: {key}", file=sys.stderr)
                return 2
            values[key] = value

    for key, value in values.items():
        print(f"{key}|{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
