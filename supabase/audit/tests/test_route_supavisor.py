"""The Supavisor session route's execution contract.

`test_targets.py` proves which *destination* the pooler route will accept. This file proves what
the audit does once it is there: that the read-only transaction is opened before any audit query,
that its timeouts are transaction-local, that the read-only state is read back from the server and
required before the check bank is loaded, that the transaction ends in ROLLBACK on every path, and
that nothing carrying the project reference reaches a report.
"""

import datetime as dt
import re
import unittest
from pathlib import Path

from olaudit import checks, psqlrun, redact, report as report_mod, sqlbank, target_hosted, verdict
from olaudit.target import ROUTE_DIRECT, ROUTE_SUPAVISOR_SESSION, Target

PROJECT_REF = "abcdefghijklmnopqrst"
POOLER_HOST = "aws-0-sa-east-1.pooler.supabase.com"
POOLER_LOGIN = f"origenlab_migrator.{PROJECT_REF}"

HEALTHY_GUARD = {
    "transaction_read_only": "on",
    "default_transaction_read_only": "off",
    "statement_timeout": "15s",
    "lock_timeout": "5s",
    "idle_in_transaction_session_timeout": "15s",
    "guard": "read-only-established",
}


def pooler_target(**overrides) -> Target:
    fields = dict(
        mode="hosted",
        host=POOLER_HOST,
        hostaddr="93.184.216.34",
        port=5432,
        user=POOLER_LOGIN,
        database="postgres",
        sslmode="verify-full",
        sslrootcert="/tmp/ca.crt",
        password="not-a-real-credential",
        project_ref=PROJECT_REF,
        route=ROUTE_SUPAVISOR_SESSION,
        pooler_cluster="0",
        pooler_region="sa-east-1",
    )
    fields.update(overrides)
    return Target(**fields)


def pooled_script() -> str:
    return psqlrun.build_script(sqlbank.load(), route=ROUTE_SUPAVISOR_SESSION)


class TestTheTransactionBoundary(unittest.TestCase):
    def test_the_read_only_transaction_opens_before_anything_else(self):
        self.assertTrue(pooled_script().startswith("begin read only;\n"))

    def test_the_timeouts_are_transaction_local(self):
        script = pooled_script()
        for setting in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"):
            self.assertRegex(script, rf"set local {setting} = '\d+ms';")
        # `set`, not `set local`, would outlive this transaction on a backend the pooler hands to
        # somebody else afterwards. Every SET the pooler preamble issues must be transaction-local.
        preamble = psqlrun.pooled_preamble(15_000, 5_000)
        set_lines = [
            line for line in preamble.splitlines() if line.strip().lower().startswith("set ")
        ]
        self.assertTrue(set_lines)
        for line in set_lines:
            self.assertTrue(line.lower().startswith("set local "), line)

    def test_the_role_change_is_transaction_local_too(self):
        self.assertIn("set local role origenlab_owner;", pooled_script())

    def test_the_direct_route_is_unchanged(self):
        direct = psqlrun.build_script(sqlbank.load(), route=ROUTE_DIRECT)
        self.assertEqual(psqlrun.PREAMBLE + "".join(
            check.sql.rstrip() + "\n" for check in sqlbank.load()
        ) + psqlrun.EPILOGUE, direct)
        self.assertNotIn(psqlrun.GUARD_CHECK_ID, direct)


class TestTheReadOnlyGuard(unittest.TestCase):
    def test_the_guard_runs_before_the_first_check_file(self):
        script = pooled_script()
        guard_at = script.index(f"'{psqlrun.GUARD_CHECK_ID}'")
        first_check = min(script.index(check.sql.rstrip()) for check in sqlbank.load())
        self.assertLess(guard_at, first_check)

    def test_the_guard_reads_the_state_back_from_the_server(self):
        self.assertIn("current_setting('transaction_read_only')", psqlrun.GUARD_SQL)

    def test_the_failing_branch_is_not_a_constant_and_so_is_not_folded_at_plan_time(self):
        """A plain `'...'::int` would be constant-folded and would fail even a read-only run."""
        failing_branch = psqlrun.GUARD_SQL.split("else", 1)[1]
        self.assertIn("current_setting(", failing_branch)
        self.assertNotRegex(failing_branch, r"'[^']*'::int")

    def test_a_read_only_state_that_was_never_established_refuses_the_run(self):
        for state in ("off", None, ""):
            guard = dict(HEALTHY_GUARD, transaction_read_only=state)
            with self.assertRaises(psqlrun.RunError, msg=repr(state)):
                psqlrun.assert_read_only_guard(guard, psqlrun.GUARD_CHECK_ID)

    def test_a_missing_guard_refuses_the_run(self):
        """No guard output means the bank would have run before anything was verified."""
        for guard in (None, "on", []):
            with self.assertRaises(psqlrun.RunError, msg=repr(guard)):
                psqlrun.assert_read_only_guard(guard, psqlrun.GUARD_CHECK_ID)

    def test_missing_timeout_settings_refuse_the_run(self):
        for setting in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"):
            for value in ("0", "0ms"):
                guard = dict(HEALTHY_GUARD, **{setting: value})
                with self.assertRaises(psqlrun.RunError, msg=f"{setting}={value}"):
                    psqlrun.assert_read_only_guard(guard, psqlrun.GUARD_CHECK_ID)

    def test_a_healthy_guard_passes(self):
        psqlrun.assert_read_only_guard(HEALTHY_GUARD, psqlrun.GUARD_CHECK_ID)


class TestParsingWithTheGuard(unittest.TestCase):
    def line(self, check_id: str, data: dict) -> str:
        import json

        return json.dumps({"check": check_id, "data": data})

    def test_the_guard_is_consumed_and_never_reported_as_an_unknown_check(self):
        stdout = "\n".join(
            [self.line(psqlrun.GUARD_CHECK_ID, HEALTHY_GUARD), self.line("s01", {"ok": True})]
        )
        result = psqlrun.parse_output(stdout, ["s01"], psqlrun.GUARD_CHECK_ID)
        self.assertEqual({"s01"}, set(result.observations))
        self.assertEqual((), result.unexpected)
        self.assertEqual((), result.missing)

    def test_observations_are_discarded_when_the_guard_fails(self):
        stdout = "\n".join(
            [
                self.line(psqlrun.GUARD_CHECK_ID, dict(HEALTHY_GUARD, transaction_read_only="off")),
                self.line("s01", {"ok": True}),
            ]
        )
        with self.assertRaises(psqlrun.RunError):
            psqlrun.parse_output(stdout, ["s01"], psqlrun.GUARD_CHECK_ID)

    def test_the_direct_route_parses_exactly_as_before(self):
        stdout = self.line("s01", {"ok": True})
        result = psqlrun.parse_output(stdout, ["s01"])
        self.assertEqual({"s01"}, set(result.observations))


class TestRollback(unittest.TestCase):
    def code(self, script: str) -> str:
        """The executable shape: comments removed and string literals blanked."""
        return sqlbank.to_code(script).lower()

    def test_the_pooler_transaction_ends_in_rollback_on_success(self):
        self.assertTrue(pooled_script().rstrip().endswith("rollback;"))

    def test_the_pooler_script_contains_no_commit_on_any_path(self):
        """There is no branch that could commit, so an error path cannot leave one behind."""
        code = self.code(pooled_script())
        self.assertIsNone(re.search(r"\bcommit\b", code))
        self.assertEqual(1, len(re.findall(r"\brollback\b", code)))

    def test_the_role_is_reset_before_the_rollback(self):
        self.assertIn("reset role;\nrollback;\n", pooled_script())

    def test_an_aborted_script_cannot_reach_a_commit(self):
        """ON_ERROR_STOP aborts the file; with no COMMIT anywhere the server rolls back on close."""
        self.assertIn("ON_ERROR_STOP=1", Path(psqlrun.__file__).read_text(encoding="utf-8"))


class TestOnlyReviewedQueriesRun(unittest.TestCase):
    def test_every_statement_between_the_preamble_and_the_epilogue_is_a_reviewed_single_read(self):
        for check in sqlbank.load():
            self.assertEqual(check.check_id, sqlbank.validate(check.sql, name=check.name))

    def test_the_pooler_script_is_the_same_bank_as_the_direct_one(self):
        bank = sqlbank.load()
        body = "".join(check.sql.rstrip() + "\n" for check in bank)
        self.assertIn(body, psqlrun.build_script(bank, route=ROUTE_SUPAVISOR_SESSION))
        self.assertIn(body, psqlrun.build_script(bank, route=ROUTE_DIRECT))


class TestNothingIdentifyingEscapes(unittest.TestCase):
    def test_the_project_qualified_login_is_redacted_from_free_text(self):
        text = f"psql: error: connection to {POOLER_LOGIN}@{POOLER_HOST} failed"
        out = redact.redact(text, pooler_target().secrets)
        self.assertNotIn(PROJECT_REF, out)
        self.assertNotIn(POOLER_HOST, out)
        self.assertEqual([], redact.find_leaks(out, pooler_target().secrets))

    def test_a_pooler_dsn_is_redacted_whole(self):
        dsn = f"postgresql://{POOLER_LOGIN}:s3cret@{POOLER_HOST}:5432/postgres"
        self.assertEqual("postgresql://[redacted]", redact.redact(dsn))

    def test_the_login_is_redacted_even_without_the_known_secret_list(self):
        """Pattern redaction is the safety net for a login the process never held."""
        out = redact.redact(f"user={POOLER_LOGIN}")
        self.assertNotIn(PROJECT_REF, out)

    def test_a_generated_report_carries_no_project_reference(self):
        target = pooler_target()
        document = report_mod.build(
            results=[],
            outcome=verdict.Outcome(
                verdict="HOSTED_INCOMPLETE",
                base="INCOMPLETE",
                mode="hosted",
                simulated=False,
                hosted_contacted=True,
                gate_eligible=False,
                blocking=(),
                incomplete=("s01",),
            ),
            target_description=target.describe(),
            attestation_description={"present": False},
            run_started_at=dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc),
            repo_head="0" * 40,
            tree_clean=True,
            baseline_path=checks.BASELINE_PATH,
            scrubbed_env_names=[],
            sql_files=[],
        )
        for text in (report_mod.render_json(document), report_mod.render_markdown(document)):
            rendered = redact.redact(text, target.secrets)
            self.assertNotIn(PROJECT_REF, rendered)
            self.assertNotIn(POOLER_HOST, rendered)
            self.assertNotIn("not-a-real-credential", rendered)
            redact.assert_clean(rendered, target.secrets, what="the test report")

    def test_the_committed_fixtures_and_baseline_carry_no_project_reference(self):
        """A twenty-letter reference must never enter tracked audit data, in any route's shape."""
        root = Path(__file__).resolve().parents[1]
        suspicious = re.compile(r"\b(?:db\.[a-z]{20}\.supabase\.co|[a-z][a-z0-9_]*\.[a-z]{20})\b")
        for path in sorted(root.glob("fixtures/*.json")) + sorted(root.glob("baselines/*.json")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(suspicious.search(text), f"{path.name}: {suspicious.search(text)}")


class TestSessionEvaluationIsRouteAware(unittest.TestCase):
    def observation(self, **overrides) -> dict:
        data = {
            "transaction_read_only": "on",
            "default_transaction_read_only": "on",
            "session_user": "origenlab_migrator",
            "current_user": "origenlab_owner",
            "current_database": "postgres",
            "is_superuser": "off",
            "statement_timeout": "15s",
            "idle_in_transaction_session_timeout": "15s",
            "server_version_num": "170004",
            "server_version": "17.4",
        }
        data.update(overrides)
        return data

    def evaluate(self, data: dict, route: str):
        ctx = checks.Context(mode="hosted", simulated=False, route=route)
        return checks.eval_s01(data, {"audit_identity": "origenlab_migrator"}, ctx)

    def test_a_read_only_transaction_passes_on_both_routes(self):
        for route in (ROUTE_DIRECT, ROUTE_SUPAVISOR_SESSION):
            status, findings, _notes, _summary = self.evaluate(self.observation(), route)
            self.assertEqual([], findings, route)

    def test_a_transaction_that_is_not_read_only_fails_on_both_routes(self):
        for route in (ROUTE_DIRECT, ROUTE_SUPAVISOR_SESSION):
            _status, findings, _notes, _summary = self.evaluate(
                self.observation(transaction_read_only="off"), route
            )
            self.assertTrue(findings, route)

    def test_a_stripped_connection_default_is_a_finding_on_direct_and_a_note_on_the_pooler(self):
        data = self.observation(default_transaction_read_only="off")
        _s, direct_findings, _n, _sm = self.evaluate(data, ROUTE_DIRECT)
        self.assertTrue(direct_findings)
        _s, pooler_findings, pooler_notes, _sm = self.evaluate(data, ROUTE_SUPAVISOR_SESSION)
        self.assertEqual([], pooler_findings)
        self.assertTrue(any("Supavisor" in note for note in pooler_notes))

    def test_a_finding_about_the_hosted_login_never_prints_the_login(self):
        _s, findings, _n, _sm = self.evaluate(
            self.observation(session_user=POOLER_LOGIN), ROUTE_SUPAVISOR_SESSION
        )
        self.assertTrue(findings)
        self.assertNotIn(PROJECT_REF, " ".join(findings))


if __name__ == "__main__":
    unittest.main()
