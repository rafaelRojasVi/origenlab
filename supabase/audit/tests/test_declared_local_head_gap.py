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
#: The shared Workspace sign-in tables carry fewer policies than four: auth_principal and
#: operator_profile one each (api select; their throttle is written by the definer
#: platform.finish_pin_attempt), auth_event two (api select, insert).
SIGN_IN_TABLES = ("auth_principal", "operator_profile", "auth_event")
SIGN_IN_POLICY_COUNTS = {"auth_principal": 1, "operator_profile": 1, "auth_event": 2}
#: ...and the session table three (api select, insert, update).
SESSION_TABLE = "auth_session"
#: slice-6 tables: campaign_content and campaign_content_message get 2 policies each (api+worker
#: select); note and organization_product_line get 4 each (api select/insert/update, worker select).
SLICE6_SELECT_ONLY_TABLES = ("campaign_content", "campaign_content_message")
SLICE6_FULL_TABLES = ("note", "organization_product_line")
#: slice-7 catalog and evidence tables (the worker holds no policy on any): product_image,
#: supplier_terms and document_line three each (api select/insert/update); fx_rate and
#: cost_parameter two each (api select, insert: append-only).
SLICE7_POLICY_COUNTS = {"product_image": 3, "supplier_terms": 3,
                        "fx_rate": 2, "cost_parameter": 2, "document_line": 3}
SLICE7_TABLES = tuple(SLICE7_POLICY_COUNTS)


def _table(name):
    return json.dumps({"owner": "origenlab_owner", "rls_enabled": True, "rls_forced": False,
                       "schema": "crm", "table": name}, separators=(",", ":"), sort_keys=True)


def _policy(table, n):
    return json.dumps({"policy": f"p{n}", "schema": "crm", "table": table},
                      separators=(",", ":"), sort_keys=True)


def _removed(policy, command):
    return json.dumps({"command": command, "permissive": "PERMISSIVE", "policy": policy,
                       "roles": "{origenlab_api}", "schema": "platform", "table": "operator",
                       "using": "true", "with_check": "true"}, separators=(",", ":"), sort_keys=True)


REMOVED = "; ".join([_removed("origenlab_api_insert", "INSERT"), _removed("origenlab_api_update", "UPDATE")])


def _check(check_id, status="PASS", summary=None, findings=None, required=True):
    return {"id": check_id, "required": required, "status": status,
            "summary": summary or {}, "findings": findings or []}


def current_head_report():
    """The shape of a real local audit of current head (the 2026-10-05 slice-7 runs: catalog
    products +2 tables, +6 policies, +6 foreign keys; pricing inputs and document lines +3 tables,
    +7 policies, +4 foreign keys, +1 function — on top of the 2026-09-30 slice-6 tables, the
    2026-09-28 sign-in tables and the 2026-09-29 PIN attempt)."""
    policies = "; ".join([_policy(t, n) for t in TABLES for n in range(4)]
                         + [_policy(t, n) for t in SIGN_IN_TABLES for n in range(SIGN_IN_POLICY_COUNTS[t])]
                         + [_policy(SESSION_TABLE, n) for n in range(3)]
                         + [_policy(t, n) for t in SLICE6_SELECT_ONLY_TABLES for n in range(2)]
                         + [_policy(t, n) for t in SLICE6_FULL_TABLES for n in range(4)]
                         + [_policy(t, n) for t in SLICE7_TABLES for n in range(SLICE7_POLICY_COUNTS[t])])
    checks = [_check(f"a{i:02d}") for i in (1, 2, 3, 6, 7, 11, 12)] + [
        _check("s01"),
        _check("a13", status="CORROBORATED", required=False),
        _check("a04", "FAIL", {"relation_count": 51},
               ["relations in scope: observed 51, expected 34"]),
        _check("a05", "FAIL", {"function_count": 33, "security_definer_count": 3}, [
            "SECURITY DEFINER functions (the closed list of ARCHITECTURE.md §6.2): "
            "3 entr(y|ies) are present here and not in the baseline: " + gap.EXPECTED_SECURITY_DEFINER,
            "functions in scope: observed 33, expected 3"]),
        _check("a08", "FAIL", {"table_count": 50, "schema_count": 7}, [
            "tables: 17 entr(y|ies) are present here and not in the baseline: "
            + "; ".join(_table(t) for t in TABLES + SIGN_IN_TABLES + (SESSION_TABLE,)
                        + SLICE6_SELECT_ONLY_TABLES + SLICE6_FULL_TABLES + SLICE7_TABLES),
            "table count: observed 50, expected 33"]),
        _check("a09", "FAIL", {"policy_count": 173}, [
            "RLS policy count: observed 173, expected 127",
            "RLS policies: 2 entr(y|ies) in the baseline are absent here: " + REMOVED,
            "RLS policies: 48 entr(y|ies) are present here and not in the baseline: " + policies]),
        _check("a10", "FAIL",
               {"foreign_key_count": 157, "covered_count": 157, "covered_unconditionally": 124},
               ["foreign keys: observed 157, expected 102"]),
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
        self.assertEqual(33, gap.EXPECTED_FUNCTION_COUNT)

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

    def test_the_session_table_is_declared(self):
        self.assertIn(SESSION_TABLE, gap.EXPECTED_EXTRA_TABLES)
        self.assertIn("platform.auth_session_guard", gap.POST_SLICE0_FUNCTIONS)
        self.assertIn("platform.auth_event_actor_guard", gap.POST_SLICE0_FUNCTIONS)
        for fk in ("platform.auth_session.principal_id -> platform.auth_principal.id",
                   "platform.auth_session.operator_id -> platform.operator_profile.operator_id"):
            self.assertIn(fk, gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS)

    def test_a09_policies_on_an_unreviewed_table_are_refused(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        a09["findings"][2] = a09["findings"][2].replace('"table":"auth_event"', '"table":"other_table"', 1)
        self.assert_refused(report, "a09 names policies on unreviewed tables: ['other_table']")

    def test_a09_policies_on_an_unreviewed_table_with_a_digit_are_refused(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        a09["findings"][2] = a09["findings"][2].replace('"table":"auth_event"', '"table":"other_table2"', 1)
        self.assert_refused(report, "a09 names policies on unreviewed tables: ['other_table2']")

    def test_a_truncated_a09_list_need_not_name_every_table(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        head, _, entries = a09["findings"][2].partition(": ")
        a09["findings"][2] = head + ": " + "; ".join(entries.split("; ")[:12]) + " ..."
        self.assertEqual(0, self.run_gap(report))

    def test_an_untruncated_a09_list_must_name_every_table(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        a09["findings"][2] = "; ".join(e for e in a09["findings"][2].split("; ")
                                       if '"table":"auth_session"' not in e)
        self.assert_refused(report, "a09 does not name expected table auth_session")

    def test_only_the_two_revoked_operator_policies_may_be_absent(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        a09["findings"][1] = a09["findings"][1].replace('"table":"operator"', '"table":"organization"', 1)
        self.assert_refused(report, "a09 reports unreviewed absent policies")

    def test_a_third_absent_policy_is_refused(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        a09["findings"][1] = (a09["findings"][1].replace("2 entr(y|ies)", "3 entr(y|ies)")
                              + "; " + _removed("origenlab_api_select", "SELECT"))
        self.assert_refused(report, "a09 does not report exactly the two reviewed absent policies")

    def test_the_revoked_policies_must_really_be_absent(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        del a09["findings"][1]
        self.assert_refused(report, "a09 has unexpected findings")

    def test_a_head_without_the_session_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace('"table":"auth_session"', '"table":"other_table"')
        self.assert_refused(report, "a08 does not name expected table auth_session")

    def test_a_head_without_a_sign_in_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace('"table":"operator_profile"', '"table":"other_table"')
        self.assert_refused(report, "a08 does not name expected table operator_profile")

    @staticmethod
    def truncate_a08(report, rename=None):
        """Render a08's extra tables the way the audit does past twelve: sorted, the first
        twelve, then " ...". `rename` = (old, new) table name applied to a kept entry."""
        a08 = check_of(report, "a08")
        head, sep, entries = a08["findings"][0].partition("not in the baseline: ")
        kept = sorted(entries.split("; "))[:12]
        if rename:
            kept = [e.replace(f'"table":"{rename[0]}"', f'"table":"{rename[1]}"') for e in kept]
        a08["findings"][0] = head + sep + "; ".join(kept) + " ..."
        return kept

    def test_a_truncated_a08_list_need_not_name_every_table(self):
        # The audit lists at most twelve entries, sorted, then " ..." — seventeen do not fit.
        report = current_head_report()
        kept = self.truncate_a08(report)
        self.assertEqual(12, len(kept))
        self.assertNotIn('"table":"supplier_terms"', check_of(report, "a08")["findings"][0])
        self.assertEqual(0, self.run_gap(report))

    def test_a08_tables_on_an_unreviewed_table_are_refused_even_when_truncated(self):
        report = current_head_report()
        self.truncate_a08(report, rename=("auth_event", "other_table"))
        self.assert_refused(report, "a08 names unreviewed tables: ['other_table']")

    def test_a08_tables_with_a_digit_are_read_and_refused_when_truncated(self):
        report = current_head_report()
        self.truncate_a08(report, rename=("auth_event", "other_table2"))
        self.assert_refused(report, "a08 names unreviewed tables: ['other_table2']")

    def test_a_truncated_a08_list_must_name_exactly_twelve_tables(self):
        # A name the pattern cannot read is not skipped: the truncated list then names eleven.
        report = current_head_report()
        self.truncate_a08(report, rename=("auth_event", "Other-Table"))
        self.assert_refused(report, "a08 is truncated but names 11 tables, expected twelve")

    def test_a_truncated_a08_list_with_an_entry_dropped_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        head, sep, _ = a08["findings"][0].partition("not in the baseline: ")
        kept = self.truncate_a08(report)
        a08["findings"][0] = head + sep + "; ".join(kept[1:]) + " ..."
        self.assert_refused(report, "a08 is truncated but names 11 tables, expected twelve")

    def test_an_eighteenth_extra_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace("17 entr(y|ies)", "18 entr(y|ies)", 1)
        self.assert_refused(report, "a08 does not report exactly seventeen extra tables")

    def test_a_head_without_a_catalog_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace('"table":"supplier_terms"', '"table":"other_table"')
        self.assert_refused(report, "a08 does not name expected table supplier_terms")

    def test_a_head_without_the_campaign_block_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace('"table":"campaign_block"', '"table":"other_table"')
        self.assert_refused(report, "a08 does not name expected table campaign_block")

    def test_the_slice6_schema_is_declared(self):
        for table in SLICE6_SELECT_ONLY_TABLES + SLICE6_FULL_TABLES:
            self.assertIn(table, gap.EXPECTED_EXTRA_TABLES)
        for name in ("outbound.campaign_content_immutable", "crm.note_guard"):
            self.assertIn(name, gap.POST_SLICE0_FUNCTIONS)
        for fk in ("outbound.campaign_content.campaign_content_campaign_id_fkey",
                   "crm.note.note_author_operator_id_fkey",
                   "crm.organization_product_line.organization_product_line_organization_id_fkey"):
            self.assertIn(fk, gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS)

    def test_the_slice7_catalog_schema_is_declared(self):
        for table in SLICE7_TABLES:
            self.assertIn(table, gap.EXPECTED_EXTRA_TABLES)
        for fk in ("catalog.product.product_content_confirmed_by_operator_id_fkey",
                   "catalog.product_image.product_image_product_id_fkey",
                   "catalog.product_image.product_image_created_by_operator_id_fkey",
                   "catalog.supplier_product.supplier_product_recorded_by_operator_id_fkey",
                   "catalog.supplier_terms.supplier_terms_supplier_organization_id_fkey",
                   "catalog.supplier_terms.supplier_terms_updated_by_operator_id_fkey"):
            self.assertIn(fk, gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS)

    def test_the_slice7_pricing_and_document_line_schema_is_declared(self):
        for table in ("fx_rate", "cost_parameter", "document_line"):
            self.assertIn(table, gap.EXPECTED_EXTRA_TABLES)
        self.assertIn("evidence.document_line_review_guard", gap.POST_SLICE0_FUNCTIONS)
        for fk in ("catalog.fx_rate.fx_rate_recorded_by_operator_id_fkey",
                   "catalog.cost_parameter.cost_parameter_set_by_operator_id_fkey",
                   "evidence.document_line.document_line_source_record_id_fkey",
                   "evidence.document_line.document_line_reviewed_by_operator_id_fkey"):
            self.assertIn(fk, gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS)

    def test_a_head_without_a_document_line_table_is_refused(self):
        report = current_head_report()
        a08 = check_of(report, "a08")
        a08["findings"][0] = a08["findings"][0].replace('"table":"document_line"', '"table":"other_table"')
        self.assert_refused(report, "a08 does not name expected table document_line")

    def test_the_w10_functions_are_declared(self):
        for name in ("outbound.unsubscribe_permanent", "outbound.marketing_contact_refusals",
                     "outbound.add_contact_control"):
            self.assertIn(name, gap.POST_SLICE0_FUNCTIONS)
        self.assertIn('"name":"add_contact_control"', gap.EXPECTED_SECURITY_DEFINER)
        self.assertIn('"owner":"origenlab_owner"', gap.EXPECTED_SECURITY_DEFINER)
        self.assertIn('"proconfig":"search_path=pg_catalog"', gap.EXPECTED_SECURITY_DEFINER)

    def test_the_pin_attempt_definers_are_declared(self):
        self.assertNotIn("platform.record_pin_attempt", gap.POST_SLICE0_FUNCTIONS)
        for name in ("platform.begin_pin_attempt", "platform.finish_pin_attempt", "platform.hmac_sha256"):
            self.assertIn(name, gap.POST_SLICE0_FUNCTIONS)
        self.assertEqual(3, len(gap.EXPECTED_SECURITY_DEFINERS))
        self.assertEqual(sorted(gap.EXPECTED_SECURITY_DEFINERS), list(gap.EXPECTED_SECURITY_DEFINERS),
                         "in the order the audit renders them: sorted as JSON text")
        begin = next(e for e in gap.EXPECTED_SECURITY_DEFINERS if '"name":"begin_pin_attempt"' in e)
        finish = next(e for e in gap.EXPECTED_SECURITY_DEFINERS if '"name":"finish_pin_attempt"' in e)
        self.assertIn('"name":"begin_pin_attempt"', begin)
        self.assertIn('"arguments":"p_principal_id uuid, p_operator_id uuid, OUT attempt_id uuid, '
                      'OUT refused boolean, OUT memory_kib integer, OUT iterations integer, '
                      'OUT lanes integer, OUT salt text, OUT hash_length integer, OUT nonce bytea"', begin)
        self.assertIn('"name":"finish_pin_attempt"', finish)
        self.assertIn('"arguments":"p_attempt_id uuid, p_principal_id uuid, p_operator_id uuid, '
                      'p_candidate_proof bytea, OUT selected boolean, '
                      'OUT reason text"', finish)
        for entry in (begin, finish):
            self.assertIn('"schema":"platform"', entry)
            self.assertIn('"owner":"origenlab_owner"', entry)
            self.assertIn('"proconfig":"search_path=pg_catalog"', entry)
        self.assertNotIn("record_pin_attempt", gap.EXPECTED_SECURITY_DEFINER)

    def test_a_head_without_a_pin_attempt_definer_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["summary"]["security_definer_count"] = 2
        self.assert_refused(report, "a05.security_definer_count: observed 2, expected 3")

    def test_a_head_that_still_has_the_caller_declared_success_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["findings"][0] = a05["findings"][0].replace('"name":"finish_pin_attempt"', '"name":"record_pin_attempt"')
        self.assert_refused(report, "a05 findings changed")

    def test_the_throttle_update_policies_must_really_be_gone(self):
        report = current_head_report()
        a09 = check_of(report, "a09")
        a09["findings"][2] = a09["findings"][2].replace("48 entr(y|ies)", "50 entr(y|ies)")
        a09["summary"]["policy_count"] = 175
        self.assert_refused(report, "a09.policy_count: observed 175, expected 173")

    def test_the_historical_origin_foreign_key_is_declared(self):
        self.assertEqual(39, len(gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS))
        self.assertEqual(157, gap.EXPECTED_FOREIGN_KEY_COUNT)
        self.assertEqual(124, gap.EXPECTED_COVERED_UNCONDITIONALLY)
        self.assertIn("platform.auth_session.(account_operator_id, sign_in_kind)"
                      " -> platform.operator.(id, sign_in_kind)", gap.POST_COMMERCIAL_CASE_FOREIGN_KEYS)

    def test_the_previous_head_count_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["summary"]["function_count"] = 32
        self.assert_refused(report, "a05.function_count: observed 32, expected 33")

    def test_an_undeclared_thirty_fourth_function_is_refused(self):
        report = current_head_report()
        a05 = check_of(report, "a05")
        a05["summary"]["function_count"] = 34
        self.assert_refused(report, "a05.function_count: observed 34, expected 33")

    def test_a_fourth_security_definer_function_is_refused(self):
        report = current_head_report()
        check_of(report, "a05")["summary"]["security_definer_count"] = 4
        self.assert_refused(report, "a05.security_definer_count: observed 4, expected 3")

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
        a10["summary"].update(foreign_key_count=158, covered_count=158)
        a10["findings"] = ["foreign keys: observed 158, expected 102"]
        self.assert_refused(report, "a10.foreign_key_count: observed 158, expected 157")

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
