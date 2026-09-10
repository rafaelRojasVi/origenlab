"""Reports: identical bytes for identical inputs, and never written unless proven clean."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from olaudit import checks, redact, report as report_mod
from olaudit.verdict import PASS, decide

PROJECT_REF = "abcdefghijklmnopqrst"
HOST = f"db.{PROJECT_REF}.supabase.co"
PASSWORD = "not-a-real-credential-value"
NOW = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)


def results(count=3):
    return [
        checks.Result(f"a{i:02d}", f"Check {i}", "MIGRATION.md §5.2", "proof", True, PASS)
        for i in range(count)
    ]


def build(mode="local", simulated=False, hosted_contacted=False, extra=None):
    all_results = results() + (extra or [])
    outcome = decide(all_results, mode=mode, simulated=simulated, hosted_contacted=hosted_contacted)
    return report_mod.build(
        results=all_results,
        outcome=outcome,
        target_description={
            "mode": mode,
            "host_class": "remote" if mode == "hosted" else "loopback",
            "host_shown": "[hosted-host-redacted]" if mode == "hosted" else "127.0.0.1",
            "port": 5432,
            "user": "origenlab_migrator",
            "database": "postgres",
            "sslmode": "verify-full",
            "tls_verified_against_hostname": True,
            "address_pinned_before_connect": True,
            "project_ref_present": True,
            "statement_timeout_ms": 15000,
            "connect_timeout_s": 15,
            "guard_notes": ["hosted target: sslmode verify-full against a CA file that exists"],
        },
        attestation_description={"present": False},
        run_started_at=NOW,
        repo_head="c9f8e87b7e6e38682a786687f7b489c26807404c",
        tree_clean=True,
        baseline_path=checks.BASELINE_PATH,
        scrubbed_env_names=["PGHOST", "SUPABASE_ACCESS_TOKEN"],
        sql_files=[("010_session_safety.sql", "0" * 64)],
    )


class TestDeterminism(unittest.TestCase):
    def test_identical_inputs_produce_identical_bytes(self):
        first, second = build(), build()
        self.assertEqual(report_mod.render_json(first), report_mod.render_json(second))
        self.assertEqual(report_mod.render_markdown(first), report_mod.render_markdown(second))

    def test_json_keys_are_sorted(self):
        text = report_mod.render_json(build())
        self.assertEqual(text, json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    def test_checks_keep_registry_order(self):
        document = build()
        self.assertEqual(["a00", "a01", "a02"], [c["id"] for c in document["checks"]])

    def test_the_baseline_is_identified_by_hash(self):
        self.assertEqual(64, len(build()["baseline"]["sha256"]))


class TestVerdictWording(unittest.TestCase):
    def test_a_simulated_run_level_verdict_is_never_the_bare_word_pass(self):
        document = build(mode="hosted", simulated=True)
        self.assertEqual("SIMULATED_PASS", document["verdict"]["verdict"])
        json_text = report_mod.render_json(document)
        markdown_text = report_mod.render_markdown(document)
        # No run-level field anywhere says PASS on its own, in either artefact.
        self.assertNotIn('"verdict": "PASS"', json_text)
        self.assertNotIn('"required_checks_outcome": "PASS"', json_text)
        self.assertNotIn("**PASS**", markdown_text)
        # And no line of either artefact is the standalone word.
        for text in (json_text, markdown_text):
            self.assertNotIn("PASS", [line.strip() for line in text.splitlines()])
            self.assertIn("SIMULATED_PASS", text)

    def test_a_per_check_status_may_still_be_pass(self):
        """The prefix guards the conclusion, not each check. A check that held, held."""
        document = build(mode="hosted", simulated=True)
        self.assertEqual({"PASS"}, {check["status"] for check in document["checks"]})

    def test_a_simulated_report_says_it_contacted_nothing(self):
        document = build(mode="hosted", simulated=True)
        self.assertFalse(document["run"]["hosted_contacted"])
        self.assertTrue(document["run"]["simulated"])
        self.assertFalse(document["verdict"]["gate_eligible"])
        self.assertIn("contacted no database", report_mod.render_markdown(document))

    def test_a_local_report_says_it_cannot_satisfy_the_gate(self):
        markdown = report_mod.render_markdown(build(mode="local"))
        self.assertIn("LOCAL_PASS", markdown)
        self.assertIn("not eligible to satisfy a migration gate", markdown)

    def test_a_real_hosted_pass_is_marked_eligible(self):
        document = build(mode="hosted", hosted_contacted=True)
        self.assertTrue(document["verdict"]["gate_eligible"])
        self.assertIn("Eligible to be cited", report_mod.render_markdown(document))

    def test_every_report_names_what_it_does_not_cover(self):
        markdown = report_mod.render_markdown(build())
        self.assertIn("Not covered by this audit", markdown)
        self.assertIn("restore drill", markdown)


class TestSanitisation(unittest.TestCase):
    def test_a_clean_report_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path, markdown_path = report_mod.write(build(), Path(tmp), (PASSWORD, PROJECT_REF, HOST))
            self.assertTrue(json_path.exists())
            self.assertEqual([], report_mod.verify_file(json_path))
            self.assertEqual([], report_mod.verify_file(markdown_path))

    def test_a_secret_that_reaches_the_structure_is_redacted_out_of_the_file(self):
        leaked = checks.Result(
            "a99", "leaky", "none", "proof", True, PASS,
            findings=(f"connected to {HOST} as origenlab_migrator with password {PASSWORD}",),
        )
        with tempfile.TemporaryDirectory() as tmp:
            json_path, _ = report_mod.write(
                build(extra=[leaked]), Path(tmp), (PASSWORD, PROJECT_REF, HOST)
            )
            text = json_path.read_text(encoding="utf-8")
            self.assertNotIn(PASSWORD, text)
            self.assertNotIn(PROJECT_REF, text)
            self.assertNotIn(HOST, text)
            self.assertEqual([], report_mod.verify_file(json_path))

    def test_nothing_is_written_when_the_leak_assertions_fail(self):
        """A structure carrying something redaction cannot remove must produce no file at all."""
        original = report_mod.redact.redact
        try:
            report_mod.redact.redact = lambda text, secrets=(): text  # defeat redaction
            leaked = checks.Result(
                "a99", "leaky", "none", "proof", True, PASS,
                findings=(f"host {HOST}",),
            )
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(redact.LeakError):
                    report_mod.write(build(extra=[leaked]), Path(tmp), (PROJECT_REF,))
                self.assertEqual([], list(Path(tmp).glob("*")))
        finally:
            report_mod.redact.redact = original

    def test_verify_file_reports_a_leak_in_an_existing_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dirty.json"
            path.write_text(json.dumps({"note": f"host {HOST}"}), encoding="utf-8")
            self.assertTrue(report_mod.verify_file(path))

    def test_reports_are_owner_readable_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path, markdown_path = report_mod.write(build(), Path(tmp))
            for path in (json_path, markdown_path):
                self.assertEqual(0o600, path.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
