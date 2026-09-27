"""The declared gap between frozen Slice 0 and current local head.

`assert_declared_local_head_gap.py` is the only thing that lets CI accept a LOCAL_FAIL. It must
accept exactly the reviewed post-Slice-0 delta and refuse everything else: one function too many
or too few, a function that gained an EXECUTE grant or became SECURITY DEFINER, a foreign key
nobody declared.
"""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import assert_declared_local_head_gap as gap

TABLES = ("opportunity_evidence", "opportunity_interest", "opportunity_organization")


def _table(name):
    return json.dumps({"owner": "origenlab_owner", "rls_enabled": True, "rls_forced": False,
                       "schema": "crm", "table": name}, separators=(",", ":"), sort_keys=True)


def _policy(table, n):
    return json.dumps({"policy": f"p{n}", "schema": "crm", "table": table},
                      separators=(",", ":"), sort_keys=True)


def _check(check_id, status="PASS", summary=None, findings=None, required=True):
    return {"id": check_id, "required": required, "status": status,
            "summary": summary or {}, "findings": findings or []}


def current_head_report():
    """The shape of a real local audit of current head (numbers from a 2026-09-26 run)."""
    policies = "; ".join(_policy(t, n) for t in TABLES for n in range(4))
    checks = [_check(f"a{i:02d}") for i in (1, 2, 3, 6, 7, 11, 12)] + [
        _check("s01"),
        _check("a13", status="CORROBORATED", required=False),
        _check("a04", "FAIL", {"relation_count": 37},
               ["relations in scope: observed 37, expected 34"]),
        _check("a05", "FAIL", {"function_count": 15, "security_definer_count": 0},
               ["functions in scope: observed 15, expected 3"]),
        _check("a08", "FAIL", {"table_count": 36, "schema_count": 7}, [
            "tables: 3 entr(y|ies) are present here and not in the baseline: "
            + "; ".join(_table(t) for t in TABLES),
            "table count: observed 36, expected 33"]),
        _check("a09", "FAIL", {"policy_count": 139}, [
            "RLS policy count: observed 139, expected 127",
            "RLS policies: 12 entr(y|ies) are present here and not in the baseline: " + policies]),
        _check("a10", "FAIL",
               {"foreign_key_count": 120, "covered_count": 120, "covered_unconditionally": 87},
               ["foreign keys: observed 120, expected 102"]),
    ]
    return {
        "run": {"mode": "local", "simulated": False, "hosted_contacted": False},
        "verdict": {"verdict": "LOCAL_FAIL", "blocking_checks": ["a04", "a05", "a08", "a09", "a10"],
                    "incomplete_required_checks": []},
        "checks": checks,
    }


def check_of(report, check_id):
    return next(c for c in report["checks"] if c["id"] == check_id)


class DeclaredGapTest(unittest.TestCase):
    def run_gap(self, report):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            argv = gap.sys.argv
            gap.sys.argv = ["assert_declared_local_head_gap.py", str(path)]
            try:
                with redirect_stdout(io.StringIO()):
                    return gap.main()
            finally:
                gap.sys.argv = argv

    def assert_refused(self, report, fragment):
        with self.assertRaises(SystemExit) as caught:
            self.run_gap(report)
        self.assertIn(fragment, str(caught.exception))

    def test_current_head_is_accepted(self):
        self.assertEqual(0, self.run_gap(current_head_report()))

    def test_the_historical_guard_is_a_declared_post_slice0_function(self):
        self.assertIn("crm.quote_revision_historical_guard", gap.POST_SLICE0_FUNCTIONS)
        self.assertEqual(3, gap.SLICE0_FUNCTION_COUNT)
        self.assertEqual(15, gap.EXPECTED_FUNCTION_COUNT)

    def test_the_historical_origin_foreign_key_is_declared(self):
        self.assertEqual(2, len(gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS))
        self.assertEqual(120, gap.EXPECTED_FOREIGN_KEY_COUNT)
        self.assertEqual(87, gap.EXPECTED_COVERED_UNCONDITIONALLY)

    def test_the_previous_head_count_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["summary"]["function_count"] = 14
        a05["findings"] = ["functions in scope: observed 14, expected 3"]
        self.assert_refused(report, "a05.function_count: observed 14, expected 15")

    def test_an_undeclared_sixteenth_function_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["summary"]["function_count"] = 16
        a05["findings"] = ["functions in scope: observed 16, expected 3"]
        self.assert_refused(report, "a05.function_count: observed 16, expected 15")

    def test_a_security_definer_function_is_refused(self):
        report = current_head_report()
        check_of(report, "a05")["summary"]["security_definer_count"] = 1
        self.assert_refused(report, "a05.security_definer_count: observed 1, expected 0")

    def test_a_forbidden_execute_grant_is_refused(self):
        report = current_head_report()
        check_of(report, "a05")["findings"].append(
            'a forbidden EXECUTE grant: {"function":"quote_revision_historical_guard","grantee":"PUBLIC"}')
        self.assert_refused(report, "a05 findings changed")

    def test_an_effective_execute_is_refused(self):
        report = current_head_report()
        check_of(report, "a05")["findings"].append(
            'a Data-API-facing role effectively holds EXECUTE: {"role":"anon"}')
        self.assert_refused(report, "a05 findings changed")

    def test_an_undeclared_foreign_key_is_refused(self):
        report = current_head_report()
        a10 = check_of(report, "a10")
        a10["summary"].update(foreign_key_count=121, covered_count=121)
        a10["findings"] = ["foreign keys: observed 121, expected 102"]
        self.assert_refused(report, "a10.foreign_key_count: observed 121, expected 120")

    def test_another_required_check_failing_is_refused(self):
        report = current_head_report()
        check_of(report, "a06")["status"] = "FAIL"
        self.assert_refused(report, "required check a06 is FAIL, expected PASS")

    def test_a_local_pass_is_refused(self):
        report = copy.deepcopy(current_head_report())
        report["verdict"]["verdict"] = "LOCAL_PASS"
        self.assert_refused(report, "expected the frozen baseline to reject current local head")


if __name__ == "__main__":
    unittest.main()
