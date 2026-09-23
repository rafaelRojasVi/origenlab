#!/usr/bin/env python3
"""Assert the exact, reviewed gap between frozen Slice 0 and current local head.

The Slice 0 audit baseline intentionally describes the frozen hosted foundation:
33 tables / 127 policies / 102 FKs.

Current repository head is later and intentionally carries the commercial-case
schema. Therefore a local audit of current head must conclude LOCAL_FAIL — but
only for the exact reviewed post-Slice-0 additions.

This checker does not regenerate, weaken or modify the baseline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


EXPECTED_BLOCKING = ["a04", "a05", "a08", "a09", "a10"]

EXPECTED_EXTRA_TABLES = {
    "opportunity_organization",
    "opportunity_interest",
    "opportunity_evidence",
}

EXPECTED_SUMMARIES = {
    "a04": {
        "relation_count": 37,
    },
    "a05": {
        "function_count": 9,
        "security_definer_count": 0,
    },
    "a08": {
        "table_count": 36,
        "schema_count": 7,
    },
    "a09": {
        "policy_count": 139,
    },
    "a10": {
        "foreign_key_count": 118,
        "covered_count": 118,
        "covered_unconditionally": 86,
    },
}


def refuse(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def main() -> int:
    if len(sys.argv) != 2:
        refuse("usage: assert_declared_local_head_gap.py REPORT.json")

    path = Path(sys.argv[1])
    report = json.loads(path.read_text(encoding="utf-8"))

    run = report.get("run") or {}
    if run.get("mode") != "local":
        refuse(f"report mode is {run.get('mode')!r}, expected 'local'")
    if run.get("simulated") is not False:
        refuse("report is simulated")
    if run.get("hosted_contacted") is not False:
        refuse("a local-gap report claims a hosted project was contacted")

    verdict = report.get("verdict") or {}
    if verdict.get("verdict") != "LOCAL_FAIL":
        refuse(
            f"verdict is {verdict.get('verdict')!r}, "
            "expected the frozen baseline to reject current local head"
        )

    if verdict.get("blocking_checks") != EXPECTED_BLOCKING:
        refuse(
            f"blocking checks are {verdict.get('blocking_checks')!r}; "
            f"expected {EXPECTED_BLOCKING!r}"
        )

    if verdict.get("incomplete_required_checks"):
        refuse(
            "required checks are incomplete: "
            f"{verdict['incomplete_required_checks']!r}"
        )

    checks = {
        entry["id"]: entry
        for entry in report.get("checks", [])
        if isinstance(entry, dict) and "id" in entry
    }

    # Every required check outside the declared schema-drift set must still PASS.
    for entry in report.get("checks", []):
        if (
            entry.get("required") is True
            and entry.get("id") not in EXPECTED_BLOCKING
            and entry.get("status") != "PASS"
        ):
            refuse(
                f"required check {entry.get('id')} is "
                f"{entry.get('status')}, expected PASS"
            )

    for check_id, wanted in EXPECTED_SUMMARIES.items():
        if check_id not in checks:
            refuse(f"report has no {check_id}")

        summary = checks[check_id].get("summary") or {}
        for key, expected in wanted.items():
            observed = summary.get(key)
            if observed != expected:
                refuse(
                    f"{check_id}.{key}: observed {observed!r}, "
                    f"expected {expected!r}"
                )

    # The inventory delta must be precisely the three commercial-case tables.
    a08_findings = checks["a08"].get("findings") or []
    if len(a08_findings) != 2:
        refuse(f"a08 has unexpected findings: {a08_findings!r}")

    a08_text = "\n".join(a08_findings)
    if "baseline are absent here" in a08_text:
        refuse("a08 reports a table missing from current head")

    for table in EXPECTED_EXTRA_TABLES:
        if f'"table":"{table}"' not in a08_text:
            refuse(f"a08 does not name expected table {table}")

    if "3 entr(y|ies) are present here and not in the baseline" not in a08_text:
        refuse("a08 does not report exactly three extra tables")

    # The 12 policy additions must belong to those same three reviewed tables.
    a09_findings = checks["a09"].get("findings") or []
    if len(a09_findings) != 2:
        refuse(f"a09 has unexpected findings: {a09_findings!r}")

    a09_text = "\n".join(a09_findings)
    if "baseline are absent here" in a09_text:
        refuse("a09 reports a baseline policy missing from current head")

    if "12 entr(y|ies) are present here and not in the baseline" not in a09_text:
        refuse("a09 does not report exactly twelve extra policies")

    for table in EXPECTED_EXTRA_TABLES:
        if f'"table":"{table}"' not in a09_text:
            refuse(f"a09 does not name expected table {table}")

    # The other blockers are census changes only.
    expected_single_findings = {
        "a04": "relations in scope: observed 37, expected 34",
        "a05": "functions in scope: observed 9, expected 3",
        "a10": "foreign keys: observed 118, expected 102",
    }

    for check_id, expected in expected_single_findings.items():
        findings = checks[check_id].get("findings") or []
        if findings != [expected]:
            refuse(f"{check_id} findings changed: {findings!r}")

    print(
        "ok: current local head differs from frozen Slice 0 only by the "
        "reviewed commercial-case schema delta"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
