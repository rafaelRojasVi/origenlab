"""Check evaluation: the shipped baseline, and what each evaluator does with a violation."""

import copy
import json
import unittest
from pathlib import Path

from olaudit import checks
from olaudit.verdict import ATTESTED, CORROBORATED, ERROR, FAIL, NOT_RUN, PASS, RECORDED

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "simulated_hosted.json"
ATTESTATION_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "attestation.example.json"


def observations() -> dict:
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["observations"])


def attestation() -> dict:
    payload = json.loads(ATTESTATION_FIXTURE.read_text(encoding="utf-8"))
    return {"attested_by": payload["attested_by"], "items": payload["items"]}


class TestBaseline(unittest.TestCase):
    def test_the_shipped_baseline_loads(self):
        baseline = checks.load_baseline()
        self.assertEqual(1, baseline["baseline_version"])
        self.assertEqual("origenlab_migrator", baseline["audit_identity"])
        self.assertEqual(7, len(baseline["application_schemas"]))

    def test_the_baseline_records_the_foundation_this_repository_ships(self):
        baseline = checks.load_baseline()
        self.assertEqual(33, baseline["a08"]["table_count"])
        self.assertEqual(127, baseline["a09"]["policy_count"])
        self.assertEqual(102, baseline["a10"]["foreign_key_count"])
        self.assertEqual(4, len(baseline["a01"]["roles"]))
        self.assertEqual([], baseline["a05"]["security_definer_functions"])

    def test_no_baseline_role_carries_bypassrls(self):
        for role in checks.load_baseline()["a01"]["roles"]:
            self.assertFalse(role["rolbypassrls"], role["rolname"])


class TestEvaluation(unittest.TestCase):
    def setUp(self):
        self.baseline = checks.load_baseline()
        self.obs = observations()

    def evaluate(self, obs=None, mode="hosted", attest=None):
        ctx = checks.Context(mode=mode, simulated=True, attestation=attest or {})
        return {r.check_id: r for r in checks.evaluate(obs or self.obs, self.baseline, ctx)}

    def test_the_shipped_fixture_satisfies_every_sql_proof(self):
        results = self.evaluate()
        for check_id in checks.SQL_CHECK_IDS:
            expected = {PASS, CORROBORATED, RECORDED}
            self.assertIn(results[check_id].status, expected, f"{check_id}: {results[check_id].findings}")

    def test_a_role_that_gains_bypassrls_fails_check_one(self):
        self.obs["a01"]["roles"][0]["rolbypassrls"] = True
        result = self.evaluate()["a01"]
        self.assertEqual(FAIL, result.status)
        self.assertTrue(any("BYPASSRLS" in f for f in result.findings))

    def test_service_role_bypassrls_is_recorded_and_does_not_fail(self):
        result = self.evaluate()["a02"]
        self.assertEqual(PASS, result.status)
        self.assertIn("service_role", result.summary["bypassrls_roles"])
        self.assertTrue(any("recorded, never failed on" in n for n in result.notes))

    def test_an_origenlab_role_inside_a_predefined_role_fails(self):
        self.obs["a02"]["forbidden_members"] = [
            {"role": "origenlab_api", "predefined": "pg_read_all_data"}
        ]
        self.assertEqual(FAIL, self.evaluate()["a02"].status)

    def test_a_different_hosted_platform_catalogue_is_recorded_not_failed(self):
        self.obs["a02"]["observed_members"].append(
            {"predefined": "pg_read_all_data", "member": "supabase_new_managed_role"}
        )
        result = self.evaluate()["a02"]
        self.assertEqual(PASS, result.status)
        self.assertTrue(any("recorded, not failed" in note for note in result.notes))

    def test_a_schema_grant_to_anon_fails(self):
        self.obs["a03"]["forbidden_schema_grants"] = [
            {"schema": "crm", "grantee": "anon", "privilege": "USAGE"}
        ]
        self.assertEqual(FAIL, self.evaluate()["a03"].status)

    def test_an_effective_privilege_reached_through_a_membership_fails(self):
        self.obs["a04"]["effective_table_privilege"] = [
            {"schema": "crm", "relation": "organization", "role": "service_role", "privilege": "SELECT"}
        ]
        self.assertEqual(FAIL, self.evaluate()["a04"].status)

    def test_a_column_grant_to_a_data_api_role_fails(self):
        self.obs["a04"]["forbidden_column_grants"] = [
            {"schema": "outbound", "relation": "campaign_reply", "column": "operator_class",
             "grantee": "authenticated", "privilege": "UPDATE"}
        ]
        self.assertEqual(FAIL, self.evaluate()["a04"].status)

    def test_a_security_definer_function_off_the_closed_list_fails(self):
        self.obs["a05"]["security_definer_functions"] = [
            {"schema": "outbound", "name": "send", "arguments": "", "owner": "origenlab_owner", "proconfig": ""}
        ]
        self.assertEqual(FAIL, self.evaluate()["a05"].status)

    def test_a_lost_default_privilege_fails_even_though_nothing_was_added(self):
        """The absent row is the finding: PostgreSQL's own default grants PUBLIC EXECUTE."""
        self.obs["a06"]["default_acl"] = [
            row for row in self.obs["a06"]["default_acl"] if row["objtype"] != "f"
        ]
        result = self.evaluate()["a06"]
        self.assertEqual(FAIL, result.status)
        self.assertTrue(any("absent here" in f for f in result.findings))

    def test_the_owner_regaining_database_create_fails(self):
        for row in self.obs["a07"]["database_privilege"]:
            if row["role"] == "origenlab_owner" and row["privilege"] == "CREATE":
                row["held"] = True
        result = self.evaluate()["a07"]
        self.assertEqual(FAIL, result.status)
        self.assertTrue(any("CREATE on the database" in f for f in result.findings))

    def test_a_table_with_rls_disabled_fails(self):
        self.obs["a08"]["tables_without_rls"] = ["crm.organization"]
        self.assertEqual(FAIL, self.evaluate()["a08"].status)

    def test_a_missing_table_and_an_extra_table_are_both_findings(self):
        self.obs["a08"]["tables"].pop()
        self.obs["a08"]["tables"].append(
            {"schema": "crm", "table": "surprise", "owner": "origenlab_owner",
             "rls_enabled": True, "rls_forced": False}
        )
        result = self.evaluate()["a08"]
        self.assertEqual(FAIL, result.status)
        self.assertTrue(any("absent here" in f for f in result.findings))
        self.assertTrue(any("not in the baseline" in f for f in result.findings))

    def test_a_widened_policy_predicate_fails_even_at_the_same_count(self):
        self.obs["a09"]["policies"][0]["using"] = "true"
        result = self.evaluate()["a09"]
        self.assertEqual(FAIL, result.status)
        self.assertEqual(127, result.summary["policy_count"])

    def test_an_uncovered_foreign_key_fails(self):
        self.obs["a10"]["uncovered"] = [
            {"schema": "crm", "table": "task", "constraint": "task_owner_fkey"}
        ]
        self.assertEqual(FAIL, self.evaluate()["a10"].status)

    def test_a_true_send_flag_fails(self):
        self.obs["a11"]["any_flag_true"] = True
        self.assertEqual(FAIL, self.evaluate()["a11"].status)

    def test_business_data_in_the_foundation_fails(self):
        self.obs["a12"]["non_empty"].append({"schema": "crm", "table": "organization", "rows": 4})
        result = self.evaluate()["a12"]
        self.assertEqual(FAIL, result.status)
        self.assertTrue(any("crm.organization" in f for f in result.findings))

    def test_the_send_control_row_is_not_treated_as_business_data(self):
        self.assertEqual(PASS, self.evaluate()["a12"].status)

    def test_a_missing_send_control_row_fails(self):
        self.obs["a12"]["non_empty"] = []
        self.assertEqual(FAIL, self.evaluate()["a12"].status)


class TestDataApi(unittest.TestCase):
    def setUp(self):
        self.baseline = checks.load_baseline()
        self.obs = observations()

    def evaluate(self, attest=None, mode="hosted"):
        ctx = checks.Context(mode=mode, simulated=True, attestation=attest or {})
        return {r.check_id: r for r in checks.evaluate(self.obs, self.baseline, ctx)}

    def test_sql_alone_only_corroborates(self):
        results = self.evaluate()
        self.assertEqual(CORROBORATED, results["a13"].status)
        self.assertEqual(NOT_RUN, results["d01"].status)
        self.assertFalse(results["d01"].summary["operator_attestation"])

    def test_attestation_alone_does_not_settle_it_either(self):
        self.obs["a13"]["application_schema_reachable_by"] = [{"schema": "crm", "role": "anon"}]
        results = self.evaluate(attest=attestation())
        self.assertEqual(FAIL, results["a13"].status)
        self.assertEqual(NOT_RUN, results["d01"].status)

    def test_both_halves_together_settle_it(self):
        results = self.evaluate(attest=attestation())
        self.assertEqual(CORROBORATED, results["a13"].status)
        self.assertEqual(ATTESTED, results["d01"].status)

    def test_a_pgrst_setting_naming_an_application_schema_fails(self):
        self.obs["a13"]["pgrst_role_settings"] = [
            {"role": "authenticator", "entry": "pgrst.db_schemas=public,crm"}
        ]
        self.assertEqual(FAIL, self.evaluate()["a13"].status)


class TestAttestedChecks(unittest.TestCase):
    def setUp(self):
        self.baseline = checks.load_baseline()
        self.obs = observations()

    def evaluate(self, attest, mode="hosted"):
        ctx = checks.Context(mode=mode, simulated=True, attestation=attest)
        return {r.check_id: r for r in checks.evaluate(self.obs, self.baseline, ctx)}

    def test_an_absent_attestation_is_not_run_and_required_in_hosted_mode(self):
        result = self.evaluate({})["t03"]
        self.assertEqual(NOT_RUN, result.status)
        self.assertTrue(result.required)

    def test_an_attested_item_is_attested_never_pass(self):
        result = self.evaluate(attestation())["t03"]
        self.assertEqual(ATTESTED, result.status)
        self.assertNotEqual(PASS, result.status)

    def test_an_item_attested_false_fails(self):
        attest = attestation()
        attest["items"]["backups_configured"]["value"] = False
        self.assertEqual(FAIL, self.evaluate(attest)["t04"].status)

    def test_an_item_attested_without_evidence_is_not_accepted(self):
        attest = attestation()
        attest["items"]["backups_configured"]["evidence"] = "   "
        self.assertEqual(NOT_RUN, self.evaluate(attest)["t04"].status)

    def test_attested_items_are_not_required_in_local_mode(self):
        results = self.evaluate({}, mode="local")
        for check_id in ("t01", "t02", "t03", "t04", "t05", "t06", "t07", "d01"):
            self.assertFalse(results[check_id].required, check_id)


class TestMissingObservations(unittest.TestCase):
    def test_a_check_with_no_observation_is_not_run_not_a_pass(self):
        baseline = checks.load_baseline()
        obs = observations()
        del obs["a09"]
        ctx = checks.Context(mode="hosted", simulated=True)
        results = {r.check_id: r for r in checks.evaluate(obs, baseline, ctx)}
        self.assertEqual(NOT_RUN, results["a09"].status)
        self.assertTrue(results["a09"].required)

    def test_an_unusable_observation_is_an_error_not_a_pass(self):
        baseline = checks.load_baseline()
        obs = observations()
        obs["a10"] = {"foreign_key_count": "not a number", "uncovered": None}
        ctx = checks.Context(mode="hosted", simulated=True)
        results = {r.check_id: r for r in checks.evaluate(obs, baseline, ctx)}
        self.assertIn(results["a10"].status, {FAIL, ERROR})
        self.assertNotEqual(PASS, results["a10"].status)


class TestApiKeyTypes(unittest.TestCase):
    def setUp(self):
        self.baseline = checks.load_baseline()

    def evaluate(self, obs):
        ctx = checks.Context(mode="hosted", simulated=True)
        return {r.check_id: r for r in checks.evaluate(obs, self.baseline, ctx)}["c01"]

    def test_uncollected_metadata_is_not_run_and_does_not_block(self):
        result = self.evaluate(observations())
        self.assertEqual(NOT_RUN, result.status)
        self.assertFalse(result.required)

    def test_a_legacy_service_role_key_is_recorded_not_failed(self):
        obs = observations()
        obs["c01"] = {"key_names": ["anon", "service_role", "publishable"]}
        result = self.evaluate(obs)
        self.assertEqual(RECORDED, result.status)
        self.assertEqual([], list(result.findings))
        self.assertIn("service_role", result.summary["key_names"])
        self.assertTrue(any("does not fail the audit" in note for note in result.notes))


if __name__ == "__main__":
    unittest.main()
