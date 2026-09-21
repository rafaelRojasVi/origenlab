"""The exact psql process the audit starts, and the environment it is given.

`test_targets.py` proves which destination is accepted and `test_route_supavisor.py` proves what
the SQL script says. This file proves the layer between them -- the process boundary itself:

* the argument vector suppresses **both** startup files (`-X` / `--no-psqlrc`), so neither
  `~/.psqlrc` nor `$PGSYSCONFDIR/psqlrc` can execute SQL before the generated `begin read only`;
* `ON_ERROR_STOP=1` is set on the command line, so it is already in force when the `-f` file is
  opened rather than being a statement inside it that a preceding failure could skip;
* nothing executes before that file -- there is no `-c`, no `--command`, no second `-f`;
* the child's environment is exactly the reconstructed one. Every libpq connection-routing and TLS
  variable is asserted against a deliberately hostile parent environment, key by key, and the whole
  key set is pinned so a future variable cannot be added by accident.

The end-to-end proof that a real malicious `~/.psqlrc` creates nothing is scenario M of
`supabase/scripts/audit_failure_tests.sh`, which runs against the disposable local database.
"""

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from olaudit import psqlrun, sqlbank
from olaudit.target import ROUTE_DIRECT, ROUTE_SUPAVISOR_SESSION, Target

# Every libpq variable that could redirect a connection, re-authenticate it, weaken its TLS or
# inject server settings, plus psql's own startup-file override. Each is given a value that would
# be visible in the assertions below if it ever reached the child.
HOSTILE_PARENT_ENV = {
    "PGSERVICE": "attacker",
    "PGSERVICEFILE": "/tmp/attacker-pg_service.conf",
    "PGSYSCONFDIR": "/tmp/attacker-sysconf",
    "PGHOST": "192.0.2.10",
    "PGHOSTADDR": "192.0.2.10",
    "PGPORT": "6543",
    "PGDATABASE": "attacker_db",
    "PGUSER": "attacker_role",
    "PGPASSWORD": "attacker-credential",
    "PGPASSFILE": "/tmp/attacker-pgpass",
    "PGOPTIONS": "-c default_transaction_read_only=off -c statement_timeout=0",
    "PGSSLMODE": "disable",
    "PGSSLROOTCERT": "/tmp/attacker-ca.crt",
    "PGSSLCERT": "/tmp/attacker.crt",
    "PGSSLKEY": "/tmp/attacker.key",
    "PGSSLCRL": "/tmp/attacker.crl",
    "PGREQUIRESSL": "0",
    "PGCHANNELBINDING": "disable",
    "PGTARGETSESSIONATTRS": "any",
    "PGCONNECT_TIMEOUT": "0",
    "PGAPPNAME": "attacker",
    "PGCLIENTENCODING": "SQL_ASCII",
    "PSQLRC": "/tmp/attacker.psqlrc",
    "PSQL_HISTORY": "/tmp/attacker.history",
    "SUPABASE_DB_URL": "postgresql://attacker@192.0.2.10:6543/attacker_db",
    "SUPABASE_ACCESS_TOKEN": "sbp_" + "EXAMPLENOTAREALTOKEN0000000000000000",
}

# The complete key set `child_env()` is allowed to produce for a hosted target that pins an
# address, ships a CA file and carries a password. Pinned as a set so a new variable cannot be
# introduced without a reviewer seeing it here.
EXPECTED_HOSTED_ENV_KEYS = {
    "PATH", "HOME", "LANG", "LC_ALL",
    "PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGSSLMODE", "PGCONNECT_TIMEOUT",
    "PGAPPNAME", "PGOPTIONS", "PGHOSTADDR", "PGSSLROOTCERT", "PGPASSWORD",
}

# What a healthy pooler guard looks like coming back from the server; the pooler route refuses to
# produce a result without it, so the capture below must supply one.
HEALTHY_GUARD = {
    "transaction_read_only": "on",
    "default_transaction_read_only": "off",
    "statement_timeout": "15s",
    "lock_timeout": "5s",
    "idle_in_transaction_session_timeout": "15s",
    "guard": "read-only-established",
}

VALID_HOST = "db.abcdefghijklmnopqrst.supabase.co"
VALID_ADDR = "93.184.216.34"


def hosted_target(**overrides) -> Target:
    fields = dict(
        mode="hosted",
        host=VALID_HOST,
        hostaddr=VALID_ADDR,
        port=5432,
        user="origenlab_migrator",
        database="postgres",
        sslmode="verify-full",
        sslrootcert="/tmp/ca.crt",
        password="not-a-real-credential",
        project_ref="abcdefghijklmnopqrst",
        route=ROUTE_DIRECT,
    )
    fields.update(overrides)
    return Target(**fields)


class TestTheArgumentVector(unittest.TestCase):
    """What `run()` actually execs. The process is captured; no psql is started."""

    def capture(self, target: Target):
        """Run `psqlrun.run` with psql and subprocess replaced. Returns (argv, env, cwd, script)."""
        captured = {}

        class Completed:
            returncode = 0
            stderr = ""

            def __init__(self, stdout=""):
                self.stdout = stdout

        def fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["env"] = dict(kwargs["env"])
            captured["cwd"] = kwargs["cwd"]
            # The workdir and the -f file still exist here; run() removes them afterwards.
            path = argv[argv.index("-f") + 1]
            captured["script"] = Path(path).read_text(encoding="utf-8")
            captured["script_mode"] = os.stat(path).st_mode & 0o777
            captured["cwd_mode"] = os.stat(kwargs["cwd"]).st_mode & 0o777
            # The pooler route refuses to return a result unless the server answered the guard.
            guard = ""
            if psqlrun.GUARD_CHECK_ID in captured["script"]:
                guard = json.dumps(
                    {"check": psqlrun.GUARD_CHECK_ID, "data": HEALTHY_GUARD}
                ) + "\n"
            return Completed(guard)

        with mock.patch.object(psqlrun.shutil, "which", return_value="/usr/bin/psql"), \
             mock.patch.object(psqlrun.subprocess, "run", side_effect=fake_run):
            # An empty bank: this test is about the process, not the checks.
            psqlrun.run([], target)
        return captured

    def test_both_startup_file_switches_are_present(self):
        """`-X` and its long form. Either alone suppresses ~/.psqlrc and the system psqlrc."""
        argv = self.capture(hosted_target())["argv"]
        self.assertIn("-X", argv)
        self.assertIn("--no-psqlrc", argv)

    def test_the_startup_file_switches_precede_the_script_file(self):
        """psql parses options left to right; the suppression must not sit after `-f`."""
        argv = self.capture(hosted_target())["argv"]
        f_at = argv.index("-f")
        self.assertLess(argv.index("-X"), f_at)
        self.assertLess(argv.index("--no-psqlrc"), f_at)

    def test_on_error_stop_is_set_on_the_command_line_before_the_script(self):
        argv = self.capture(hosted_target())["argv"]
        self.assertEqual("ON_ERROR_STOP=1", argv[argv.index("-v") + 1])
        self.assertLess(argv.index("-v"), argv.index("-f"))

    def test_no_sql_is_executed_before_the_script_file(self):
        """No `-c`, no `--command`, and exactly one `-f` — the generated bank and nothing else."""
        argv = self.capture(hosted_target())["argv"]
        for forbidden in ("-c", "--command", "-1", "--single-transaction", "-L", "--log-file"):
            self.assertNotIn(forbidden, argv)
        self.assertEqual(1, argv.count("-f"))
        self.assertEqual(len(argv) - 1, argv.index("-f") + 1, "the script file is the last argument")

    def test_no_argument_carries_the_target_or_the_credential(self):
        """The connection travels in the environment, so the process table shows no destination."""
        argv = self.capture(hosted_target())["argv"]
        joined = " ".join(argv)
        for secret in ("not-a-real-credential", "abcdefghijklmnopqrst", VALID_HOST, VALID_ADDR):
            self.assertNotIn(secret, joined)
        self.assertFalse([a for a in argv if a.startswith("postgres")], "no connection URI in argv")

    def test_the_first_server_side_statement_is_the_read_only_transaction(self):
        for route, opener in (
            (ROUTE_DIRECT, "begin read only;"),
            (ROUTE_SUPAVISOR_SESSION, "begin read only;"),
        ):
            script = self.capture(hosted_target(route=route))["script"]
            self.assertTrue(script.startswith(opener), f"{route}: {script[:40]!r}")

    def test_the_script_file_is_private_and_written_under_a_private_directory(self):
        captured = self.capture(hosted_target())
        self.assertEqual(0o600, captured["script_mode"])
        self.assertEqual(0o700, captured["cwd_mode"])


class TestTheChildEnvironmentUnderAHostileParent(unittest.TestCase):
    """Every routing and TLS variable is asserted against a parent that sets all of them."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, HOSTILE_PARENT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = hosted_target().child_env()

    def test_the_key_set_is_exactly_the_reconstructed_one(self):
        self.assertEqual(EXPECTED_HOSTED_ENV_KEYS, set(self.env))

    def test_service_lookup_cannot_reach_the_child(self):
        """A pg_service entry would replace host, port, user, dbname and options wholesale."""
        for name in ("PGSERVICE", "PGSERVICEFILE"):
            self.assertNotIn(name, self.env)

    def test_the_destination_is_the_validated_one_not_the_inherited_one(self):
        self.assertEqual(VALID_HOST, self.env["PGHOST"])
        self.assertEqual(VALID_ADDR, self.env["PGHOSTADDR"])
        self.assertEqual("5432", self.env["PGPORT"])
        self.assertEqual("origenlab_migrator", self.env["PGUSER"])
        self.assertEqual("postgres", self.env["PGDATABASE"])

    def test_inherited_options_cannot_reopen_the_session(self):
        """PGOPTIONS is rebuilt, so `default_transaction_read_only=off` never arrives."""
        options = self.env["PGOPTIONS"]
        self.assertNotIn("attacker", options)
        self.assertIn("default_transaction_read_only=on", options)
        self.assertNotIn("default_transaction_read_only=off", options)
        self.assertNotIn("statement_timeout=0 ", options + " ")

    def test_tls_cannot_be_weakened_by_the_environment(self):
        self.assertEqual("verify-full", self.env["PGSSLMODE"])
        self.assertEqual("/tmp/ca.crt", self.env["PGSSLROOTCERT"])
        for name in ("PGSSLCERT", "PGSSLKEY", "PGSSLCRL", "PGREQUIRESSL",
                     "PGCHANNELBINDING", "PGTARGETSESSIONATTRS"):
            self.assertNotIn(name, self.env)

    def test_the_credential_is_the_targets_own_and_no_password_file_is_reachable(self):
        self.assertEqual("not-a-real-credential", self.env["PGPASSWORD"])
        self.assertNotIn("PGPASSFILE", self.env)

    def test_psql_startup_file_overrides_cannot_reach_the_child(self):
        """Belt to `-X`'s braces: PSQLRC is not passed on either."""
        self.assertNotIn("PSQLRC", self.env)
        self.assertNotIn("PSQL_HISTORY", self.env)
        self.assertNotIn("PGSYSCONFDIR", self.env)

    def test_the_connect_timeout_and_application_name_are_the_targets_own(self):
        self.assertEqual("15", self.env["PGCONNECT_TIMEOUT"])
        self.assertEqual("origenlab-slice0-audit", self.env["PGAPPNAME"])

    def test_supabase_names_do_not_reach_the_child(self):
        for name in ("SUPABASE_DB_URL", "SUPABASE_ACCESS_TOKEN"):
            self.assertNotIn(name, self.env)

    def test_a_target_without_an_address_or_ca_omits_those_keys_rather_than_inheriting_them(self):
        env = hosted_target(hostaddr=None, sslrootcert=None, password="").child_env()
        for name in ("PGHOSTADDR", "PGSSLROOTCERT", "PGPASSWORD"):
            self.assertNotIn(name, env, f"{name} was inherited from the hostile parent")

    def test_the_pooler_route_is_given_the_same_reconstructed_environment(self):
        env = hosted_target(route=ROUTE_SUPAVISOR_SESSION,
                            user="origenlab_migrator.abcdefghijklmnopqrst").child_env()
        self.assertEqual(EXPECTED_HOSTED_ENV_KEYS, set(env))
        self.assertEqual("verify-full", env["PGSSLMODE"])
        self.assertNotIn("PGSERVICE", env)


class TestTheBankIsUnchangedByAnyOfThis(unittest.TestCase):
    def test_the_real_bank_still_opens_with_the_read_only_transaction(self):
        self.assertTrue(psqlrun.build_script(sqlbank.load()).startswith("begin read only;\n"))


if __name__ == "__main__":
    unittest.main()
