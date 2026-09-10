"""The attestation loader, and the command-line preconditions for a hosted run."""

import datetime as dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from olaudit import attestation as attest_mod
from olaudit import cli

PROJECT_REF = "abcdefghijklmnopqrst"
NOW = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "attestation.example.json"


def payload(**overrides) -> dict:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    document["project_ref"] = PROJECT_REF
    document.update(overrides)
    return document


def write(root: Path, document: dict) -> Path:
    path = root / "attestation.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class TestAttestation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_valid_attestation_loads(self):
        loaded = attest_mod.load(write(self.root, payload()), project_ref=PROJECT_REF, now=NOW)
        self.assertIn("data_api_disabled", loaded["items"])
        self.assertEqual(12, len(loaded["project_ref_fingerprint"]))

    def test_the_project_reference_never_reaches_the_loaded_form(self):
        loaded = attest_mod.load(write(self.root, payload()), project_ref=PROJECT_REF, now=NOW)
        self.assertNotIn(PROJECT_REF, json.dumps(loaded))
        self.assertNotIn(PROJECT_REF, json.dumps(attest_mod.describe(loaded)))

    def test_an_attestation_for_another_project_is_refused(self):
        document = payload(project_ref="tsrqponmlkjihgfedcba")
        with self.assertRaises(attest_mod.AttestationError):
            attest_mod.load(write(self.root, document), project_ref=PROJECT_REF, now=NOW)

    def test_a_stale_attestation_is_refused(self):
        document = payload(attested_at_utc="2026-01-01T00:00:00Z")
        with self.assertRaises(attest_mod.AttestationError):
            attest_mod.load(write(self.root, document), project_ref=PROJECT_REF, now=NOW)

    def test_an_attestation_dated_in_the_future_is_refused(self):
        document = payload(attested_at_utc="2027-01-01T00:00:00Z")
        with self.assertRaises(attest_mod.AttestationError):
            attest_mod.load(write(self.root, document), project_ref=PROJECT_REF, now=NOW)

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(attest_mod.AttestationError):
            attest_mod.load(self.root / "absent.json", project_ref=PROJECT_REF, now=NOW)

    def test_a_malformed_file_is_refused(self):
        path = self.root / "attestation.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(attest_mod.AttestationError):
            attest_mod.load(path, project_ref=PROJECT_REF, now=NOW)

    def test_missing_required_keys_are_refused(self):
        document = payload()
        del document["attested_by"]
        with self.assertRaises(attest_mod.AttestationError):
            attest_mod.load(write(self.root, document), project_ref=PROJECT_REF, now=NOW)

    def test_an_empty_item_set_is_refused(self):
        with self.assertRaises(attest_mod.AttestationError):
            attest_mod.load(write(self.root, payload(items={})), project_ref=PROJECT_REF, now=NOW)

    def test_describe_says_when_there_is_none(self):
        self.assertEqual({"present": False}, attest_mod.describe({}))


class TestCliSurface(unittest.TestCase):
    def test_there_is_no_baseline_writing_mode(self):
        text = cli.build_parser().format_help()
        for forbidden in ("--regenerate-baseline", "--write-baseline", "--update-baseline"):
            self.assertNotIn(forbidden, text)

    def test_there_is_no_way_to_disable_redaction(self):
        text = cli.build_parser().format_help()
        for forbidden in ("--debug-no-redact", "--no-redact", "--raw"):
            self.assertNotIn(forbidden, text)

    def test_there_is_no_way_to_pass_a_host_or_sql_on_the_command_line(self):
        text = cli.build_parser().format_help()
        for forbidden in ("--host", "--db-url", "--sql", "--query", "--linked"):
            self.assertNotIn(forbidden, text)

    def test_the_modes_are_explicit(self):
        text = cli.build_parser().format_help()
        self.assertIn("--mode", text)
        self.assertIn("--authorize-hosted-connection", text)


class TestHostedPreflight(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.root, check=True)
        (self.root / "supabase").mkdir()
        (self.root / "supabase" / "config.toml").write_text('project_id = "origenlab"\n', encoding="utf-8")
        (self.root / "supabase" / ".gitignore").write_text(".audit/\n.temp\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, **overrides):
        namespace = cli.build_parser().parse_args(["--mode", "hosted", "--authorize-hosted-connection"])
        for key, value in overrides.items():
            setattr(namespace, key, value)
        return namespace

    def test_a_clean_tree_passes_preflight(self):
        cli.hosted_preflight(self.root, self.args())

    def test_authorisation_is_required(self):
        with self.assertRaises(cli.PreflightError):
            cli.hosted_preflight(self.root, self.args(authorize_hosted_connection=False))

    def test_a_modified_tracked_file_stops_a_hosted_run(self):
        (self.root / "supabase" / "config.toml").write_text("project_id = \"other\"\n", encoding="utf-8")
        with self.assertRaises(cli.PreflightError):
            cli.hosted_preflight(self.root, self.args())

    def test_an_untracked_file_anywhere_else_stops_a_hosted_run(self):
        (self.root / "scratch.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(cli.PreflightError):
            cli.hosted_preflight(self.root, self.args())

    def test_the_approved_ignored_paths_do_not_stop_a_hosted_run(self):
        audit = self.root / "supabase" / ".audit"
        (audit / "reports").mkdir(parents=True)
        (audit / "hosted_target.env").write_text("x", encoding="utf-8")
        (audit / "attestation.json").write_text("{}", encoding="utf-8")
        (audit / "reports" / "slice0-audit-hosted.json").write_text("{}", encoding="utf-8")
        cli.hosted_preflight(self.root, self.args())

    def test_an_unexpected_file_in_the_audit_directory_stops_a_hosted_run(self):
        audit = self.root / "supabase" / ".audit"
        audit.mkdir(parents=True)
        (audit / "notes.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(cli.PreflightError):
            cli.hosted_preflight(self.root, self.args())

    def test_link_state_stops_a_hosted_run(self):
        temp = self.root / "supabase" / ".temp"
        temp.mkdir()
        (temp / "project-ref").write_text(PROJECT_REF, encoding="utf-8")
        with self.assertRaises(cli.PreflightError):
            cli.hosted_preflight(self.root, self.args())

    def test_a_project_ref_in_config_stops_a_hosted_run(self):
        config = self.root / "supabase" / "config.toml"
        config.write_text(f'project_id = "origenlab"\nproject_ref = "{PROJECT_REF}"\n', encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "linked"], cwd=self.root, check=True)
        with self.assertRaises(cli.PreflightError):
            cli.hosted_preflight(self.root, self.args())


class TestApiKeyCollection(unittest.TestCase):
    def test_no_access_token_means_nothing_is_collected_and_nothing_is_run(self):
        self.assertEqual({}, cli.collect_api_key_types(PROJECT_REF, {}, ()))

    def test_the_command_never_asks_for_a_key_value(self):
        """`--reveal` must never appear as an argv token. The prose that forbids it may say it."""
        source = Path(cli.__file__).read_text(encoding="utf-8")
        self.assertIn('"api-keys"', source)
        self.assertNotIn('"--reveal"', source)
        self.assertNotIn("'--reveal'", source)


if __name__ == "__main__":
    unittest.main()


class TestModeCombinations(unittest.TestCase):
    """A run either replays a fixture or contacts a project. It may never be ambiguous which."""

    def run_cli(self, argv):
        return cli.run(argv, Path.cwd(), {}, stream=open("/dev/null", "w"))

    def test_simulate_and_authorise_together_are_refused(self):
        self.assertEqual(
            2, self.run_cli(["--mode", "hosted", "--simulate", "--authorize-hosted-connection"])
        )

    def test_simulate_with_cli_metadata_is_refused(self):
        self.assertEqual(2, self.run_cli(["--mode", "hosted", "--simulate", "--cli-metadata"]))

    def test_simulate_is_hosted_only(self):
        self.assertEqual(2, self.run_cli(["--mode", "local", "--simulate"]))

    def test_cli_metadata_is_hosted_only(self):
        self.assertEqual(2, self.run_cli(["--mode", "local", "--cli-metadata"]))

    def test_a_mode_is_required(self):
        self.assertEqual(2, self.run_cli([]))
