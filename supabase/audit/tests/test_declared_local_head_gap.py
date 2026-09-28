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

TABLES = ("opportunity_evidence", "opportunity_interest", "opportunity_organization", "campaign_block")
#: The shared Workspace sign-in tables carry two policies each, not four.
SIGN_IN_TABLES = ("auth_principal", "operator_profile", "auth_event")


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
    """The shape of a real local audit of current head (a 2026-09-27 campaign-block run, moved by
    the 2026-09-28 sign-in tables: +3 tables, +3 functions, +6 policies, +5 foreign keys)."""
    policies = "; ".join([_policy(t, n) for t in TABLES for n in range(4)]
                         + [_policy(t, n) for t in SIGN_IN_TABLES for n in range(2)])
    checks = [_check(f"a{i:02d}") for i in (1, 2, 3, 6, 7, 11, 12)] + [
        _check("s01"),
        _check("a13", status="CORROBORATED", required=False),
        _check("a04", "FAIL", {"relation_count": 41},
               ["relations in scope: observed 41, expected 34"]),
        _check("a05", "FAIL", {"function_count": 25, "security_definer_count": 1}, [
            "SECURITY DEFINER functions (the closed list of ARCHITECTURE.md §6.2): "
            "1 entr(y|ies) are present here and not in the baseline: " + gap.EXPECTED_SECURITY_DEFINER,
            "functions in scope: observed 25, expected 3"]),
        _check("a08", "FAIL", {"table_count": 40, "schema_count": 7}, [
            "tables: 7 entr(y|ies) are present here and not in the baseline: "
            + "; ".join(_table(t) for t in TABLES + SIGN_IN_TABLES),
            "table count: observed 40, expected 33"]),
        _check("a09", "FAIL", {"policy_count": 149}, [
            "RLS policy count: observed 149, expected 127",
            "RLS policies: 22 entr(y|ies) are present here and not in the baseline: " + policies]),
        _check("a10", "FAIL",
               {"foreign_key_count": 128, "covered_count": 128, "covered_unconditionally": 95},
               ["foreign keys: observed 128, expected 102"]),
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
        self.assertEqual(25, gap.EXPECTED_FUNCTION_COUNT)

    def test_the_campaign_block_schema_is_declared(self):
        self.assertIn("campaign_block", gap.EXPECTED_EXTRA_TABLES)
        for name in ("outbound.campaign_block_guard", "outbound.campaign_hold_refusals",
                     "outbound.campaign_hold_guard"):
            self.assertIn(name, gap.POST_SLICE0_FUNCTIONS)
        for fk in ("outbound.campaign_block.campaign_id -> outbound.campaign.id",
                   "outbound.campaign_block.placed_by_operator_id -> platform.operator.id",
                   "outbound.campaign_block.lifted_by_operator_id -> platform.operator.id"):
            self.assertIn(fk, gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS)

    def test_the_sign_in_schema_is_declared(self):
        for table in SIGN_IN_TABLES:
            self.assertIn(table, gap.EXPECTED_EXTRA_TABLES)
        for name in ("platform.operator_security_version", "platform.auth_principal_security_version",
                     "platform.operator_profile_security_version"):
            self.assertIn(name, gap.POST_SLICE0_FUNCTIONS)
        self.assertIn("platform.operator_profile.principal_id -> platform.auth_principal.id",
                      gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS)

    def test_a_head_without_a_sign_in_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace('"table":"operator_profile"', '"table":"other_table"')
        self.assert_refused(report, "a08 does not name expected table operator_profile")

    def test_a_head_without_the_campaign_block_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace('"table":"campaign_block"', '"table":"other_table"')
        self.assert_refused(report, "a08 does not name expected table campaign_block")

    def test_the_w10_functions_are_declared(self):
        for name in ("outbound.unsubscribe_permanent", "outbound.marketing_contact_refusals",
                     "outbound.add_contact_control"):
            self.assertIn(name, gap.POST_SLICE0_FUNCTIONS)
        self.assertIn('"name":"add_contact_control"', gap.EXPECTED_SECURITY_DEFINER)
        self.assertIn('"owner":"origenlab_owner"', gap.EXPECTED_SECURITY_DEFINER)
        self.assertIn('"proconfig":"search_path=pg_catalog"', gap.EXPECTED_SECURITY_DEFINER)

    def test_the_historical_origin_foreign_key_is_declared(self):
        self.assertEqual(10, len(gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS))
        self.assertEqual(128, gap.EXPECTED_FOREIGN_KEY_COUNT)
        self.assertEqual(95, gap.EXPECTED_COVERED_UNCONDITIONALLY)

    def test_the_previous_head_count_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["summary"]["function_count"] = 22
        self.assert_refused(report, "a05.function_count: observed 22, expected 25")

    def test_an_undeclared_twenty_sixth_function_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["summary"]["function_count"] = 26
        self.assert_refused(report, "a05.function_count: observed 26, expected 25")

    def test_a_second_security_definer_function_is_refused(self):
        report = current_head_report()
        check_of(report, "a05")["summary"]["security_definer_count"] = 2
        self.assert_refused(report, "a05.security_definer_count: observed 2, expected 1")

    def test_a_different_security_definer_function_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["findings"][0] = a05["findings"][0].replace('"owner":"origenlab_owner"', '"owner":"postgres"')
        self.assert_refused(report, "a05 findings changed")

    def test_the_security_definer_finding_is_required(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["findings"] = a05["findings"][1:]
        self.assert_refused(report, "a05 findings changed")

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
        a10["summary"].update(foreign_key_count=129, covered_count=129)
        a10["findings"] = ["foreign keys: observed 129, expected 102"]
        self.assert_refused(report, "a10.foreign_key_count: observed 129, expected 128")

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
