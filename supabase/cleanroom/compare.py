#!/usr/bin/env python3
"""Compare a clean-room probe stream against the declared expected state.

Reads `key|value` lines on stdin (produced by supabase/cleanroom/verify.sql) and checks them
against supabase/cleanroom/expected_counts.json. Exits 0 only if every probe matches.

The comparison is exact in both directions:

* a probe declared in the JSON and missing from the stream is a failure, because a verification
  that silently skips a check is worse than no verification;
* a probe present in the stream and undeclared in the JSON is also a failure, because a new
  check with no expected value proves nothing and would otherwise pass forever.

This script opens no database and takes no connection string. It is handed a text stream by a
caller that has already been through the clean-room guard, which is what keeps the target out
of Python's hands entirely.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED = Path(__file__).resolve().parent / "expected_counts.json"


def main(argv: list[str]) -> int:
    expected_path = Path(argv[1]) if len(argv) > 1 else EXPECTED
    probes = json.loads(expected_path.read_text())["probes"]

    seen: dict[str, str] = {}
    for raw in sys.stdin:
        line = raw.strip()
        if not line or "|" not in line:
            continue
        key, _, value = line.partition("|")
        key, value = key.strip(), value.strip()
        if key in seen:
            print(f"FAIL  {key}: emitted more than once", file=sys.stderr)
            return 1
        seen[key] = value

    failures: list[str] = []
    for key in sorted(probes):
        want = probes[key]["expect"]
        if key not in seen:
            failures.append(f"{key}: declared but never measured")
            continue
        got = seen[key]
        # Every probe is either an integer count or a migration version string. Comparing as
        # text after normalising the integer side keeps '21' and 21 from disagreeing.
        want_text = str(want)
        if got != want_text:
            failures.append(f"{key}: expected {want_text}, measured {got}")

    for key in sorted(set(seen) - set(probes)):
        failures.append(f"{key}: measured but not declared in {expected_path.name}")

    if failures:
        print(f"clean-room verification FAILED — {len(failures)} of {len(probes)} probes", file=sys.stderr)
        for line in failures:
            print(f"FAIL  {line}", file=sys.stderr)
            why = probes.get(line.split(":")[0], {}).get("why")
            if why:
                print(f"      why it matters: {why}", file=sys.stderr)
        return 1

    print(f"clean-room verification PASSED — {len(probes)} probes, all exact")
    for key in sorted(probes):
        print(f"ok    {key} = {seen[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
