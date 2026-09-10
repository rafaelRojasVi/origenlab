"""The hosted role bootstrap: the shipped file is a closed role bootstrap, and everything else is
refused.

The shipped-file tests assert the properties the corrections require directly against
supabase/hosted_roles.sql, so they fail if the file is ever edited to acquire a password, a fifth
role, a platform-role grant or a second membership. The rejection tests do the same job from the
other side: they feed the analyser the specific mistakes that would matter and prove each one
refuses the whole file rather than being skipped.
"""

import contextlib
import io
import unittest
from pathlib import Path

from olaudit import bootstrap, bootstrap_cli

REPO_ROOT = Path(__file__).resolve().parents[3]

# A minimal file that satisfies every rule, used as the base for targeted mutations.
GOOD = """
do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_owner') then
    create role origenlab_owner
      nologin nosuperuser nobypassrls nocreatedb nocreaterole noreplication inherit;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_migrator') then
    create role origenlab_migrator
      login noinherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_api') then
    create role origenlab_api
      login inherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_worker') then
    create role origenlab_worker
      login inherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
end
$$;

alter role origenlab_owner    nologin inherit   nocreatedb nocreaterole;
alter role origenlab_migrator login   noinherit nocreatedb nocreaterole;
alter role origenlab_api      login   inherit   nocreatedb nocreaterole;
alter role origenlab_worker   login   inherit   nocreatedb nocreaterole;

grant origenlab_owner to origenlab_migrator with inherit false, set true, admin false;

revoke origenlab_owner from origenlab_api;
"""


class TestShippedBootstrap(unittest.TestCase):
    def setUp(self):
        self.text, self.model = bootstrap.load(REPO_ROOT)

    def test_the_shipped_file_is_a_closed_role_bootstrap(self):
        self.assertTrue(self.model.statements)

    def test_it_names_only_the_four_origenlab_roles(self):
        named = set()
        for statement in self.model.statements:
            named.update(statement.roles)
        self.assertEqual(named, set(bootstrap.MANAGED_ROLES))

    def test_it_creates_and_converges_every_managed_role(self):
        created = {s.roles[0] for s in self.model.statements if s.verb == "create"}
        altered = {s.roles[0] for s in self.model.statements if s.verb == "alter"}
        self.assertEqual(created, set(bootstrap.MANAGED_ROLES))
        self.assertEqual(altered, set(bootstrap.MANAGED_ROLES))

    def test_no_password_token_appears_in_the_code(self):
        code = bootstrap.to_code(self.text).lower()
        for token in ("password", "encrypted", "valid until"):
            self.assertNotIn(token, code)

    def test_the_shipped_file_carries_no_credential_shaped_text(self):
        bootstrap.assert_no_credential(self.text)

    def test_no_platform_role_is_named_anywhere_in_the_code(self):
        """The assertion queries mention no platform role as a mutation target, and the analyser
        would refuse one; this proves the file does not even name `postgres` as a grantee."""
        code = bootstrap.to_code(self.text).lower()
        for verb in ("grant", "revoke", "create role", "alter role"):
            for role in ("postgres", "service_role", "anon", "authenticated", "supabase_admin"):
                self.assertNotIn(f"{verb} {role}", code)
                self.assertNotIn(f"to {role}", code)

    def test_the_only_membership_is_the_migrator_in_the_owner(self):
        self.assertEqual(self.model.memberships, (bootstrap.REQUIRED_MEMBERSHIP,))

    def test_runtime_roles_are_converged_to_membership_in_nothing(self):
        revoked = set(self.model.revocations)
        for runtime in ("origenlab_api", "origenlab_worker"):
            self.assertIn(("origenlab_owner", runtime), revoked)
        self.assertIn(("origenlab_api", "origenlab_worker"), revoked)
        self.assertIn(("origenlab_worker", "origenlab_api"), revoked)

    def test_the_attribute_model_matches_the_architecture(self):
        for role in bootstrap.MANAGED_ROLES:
            held = self.model.attributes[role]
            for required in bootstrap.REQUIRED_ATTRIBUTES_ALL:
                self.assertIn(required, held, f"{role} must be {required.upper()}")
            for required in bootstrap.REQUIRED_ATTRIBUTES[role]:
                self.assertIn(required, held, f"{role} must be {required.upper()}")

    def test_the_owner_cannot_log_in_and_the_migrator_does_not_inherit(self):
        self.assertIn("nologin", self.model.attributes["origenlab_owner"])
        self.assertNotIn("login", self.model.attributes["origenlab_owner"])
        self.assertIn("noinherit", self.model.attributes["origenlab_migrator"])

    def test_it_differs_from_the_local_bootstrap_only_by_the_platform_grant(self):
        """The hosted file must not carry the local file's `grant origenlab_owner to postgres`."""
        local = (REPO_ROOT / "supabase" / "roles.sql").read_text(encoding="utf-8")
        self.assertIn("to postgres", bootstrap.to_code(local))
        self.assertNotIn("to postgres", bootstrap.to_code(self.text))


class TestRejection(unittest.TestCase):
    def reject(self, text, *, contains=None):
        with self.assertRaises(bootstrap.BootstrapError) as caught:
            bootstrap.analyse(text, name="probe")
        if contains:
            self.assertIn(contains, str(caught.exception).lower())

    def test_the_good_fixture_is_accepted(self):
        model = bootstrap.analyse(GOOD, name="probe")
        self.assertEqual(model.memberships, (bootstrap.REQUIRED_MEMBERSHIP,))

    def test_a_password_is_refused(self):
        self.reject(
            GOOD.replace(
                "alter role origenlab_migrator login   noinherit nocreatedb nocreaterole;",
                "alter role origenlab_migrator login noinherit password 'hunter2';",
            ),
            contains="password",
        )

    def test_an_encrypted_password_is_refused(self):
        self.reject(GOOD + "\nalter role origenlab_api encrypted password 'x';\n", contains="encrypted")

    def test_a_valid_until_clause_is_refused(self):
        self.reject(GOOD + "\nalter role origenlab_api valid until 'infinity';\n", contains="valid")

    def test_creating_a_platform_role_is_refused(self):
        self.reject(
            GOOD + "\ncreate role postgres nologin nosuperuser nobypassrls noreplication;\n",
            contains="supabase-managed",
        )

    def test_altering_a_platform_role_is_refused(self):
        self.reject(GOOD + "\nalter role postgres nocreatedb;\n", contains="supabase-managed")

    def test_granting_an_origenlab_role_to_a_platform_role_is_refused(self):
        for platform in ("postgres", "service_role", "supabase_admin", "authenticator"):
            self.reject(GOOD + f"\ngrant origenlab_owner to {platform};\n", contains="supabase-managed")

    def test_granting_a_platform_role_to_an_origenlab_role_is_refused(self):
        self.reject(GOOD + "\ngrant pg_read_all_data to origenlab_api;\n", contains="supabase-managed")

    def test_a_fifth_role_is_refused(self):
        self.reject(
            GOOD + "\ncreate role origenlab_readonly nologin nosuperuser nobypassrls noreplication;\n",
            contains="closed origenlab role set",
        )

    def test_a_second_membership_is_refused(self):
        self.reject(GOOD + "\ngrant origenlab_owner to origenlab_api;\n", contains="exactly one membership")

    def test_an_inheriting_owner_membership_is_refused(self):
        self.reject(
            GOOD.replace("with inherit false, set true, admin false", "with inherit true, set true, admin false"),
            contains="inherit false",
        )

    def test_an_admin_option_on_the_owner_membership_is_refused(self):
        self.reject(
            GOOD.replace("with inherit false, set true, admin false", "with inherit false, set true, admin true"),
            contains="admin false",
        )

    def test_a_superuser_attribute_is_refused(self):
        self.reject(GOOD.replace("nosuperuser", "superuser", 1), contains="superuser")

    def test_a_bypassrls_attribute_is_refused(self):
        self.reject(GOOD.replace("nobypassrls", "bypassrls", 1), contains="bypassrls")

    def test_a_replication_attribute_is_refused(self):
        self.reject(GOOD.replace("noreplication", "replication", 1), contains="replication")

    def test_a_missing_negative_attribute_is_refused(self):
        self.reject(GOOD.replace(" nobypassrls", "", 1), contains="nobypassrls")

    def test_a_login_owner_is_refused(self):
        """Changing only the convergence statement leaves the file self-contradictory, which is
        itself a refusal: the model must be stated once and deterministically."""
        self.reject(
            GOOD.replace("alter role origenlab_owner    nologin inherit", "alter role origenlab_owner    login inherit"),
            contains="both login and nologin",
        )

    def test_a_login_owner_declared_consistently_is_still_refused(self):
        self.reject(
            GOOD.replace("nologin nosuperuser", "login nosuperuser")
                .replace("alter role origenlab_owner    nologin inherit", "alter role origenlab_owner    login inherit"),
            contains="nologin",
        )

    def test_an_inheriting_migrator_is_refused(self):
        self.reject(
            GOOD.replace("login   noinherit nocreatedb", "login   inherit nocreatedb"),
            contains="both inherit and noinherit",
        )

    def test_an_inheriting_migrator_declared_consistently_is_still_refused(self):
        self.reject(
            GOOD.replace("login noinherit nosuperuser", "login inherit nosuperuser")
                .replace("login   noinherit nocreatedb", "login   inherit nocreatedb"),
            contains="noinherit",
        )

    def test_a_dropped_role_is_refused(self):
        self.reject(GOOD + "\ndrop role origenlab_api;\n", contains="drop")

    def test_data_modification_is_refused(self):
        for text in (
            "insert into crm.organization (name) values ('x')",
            "update outbound.send_control set marketing_enabled = true",
            "delete from crm.domain_event",
            "truncate crm.organization",
        ):
            self.reject(GOOD + "\n" + text + ";\n")

    def test_ddl_beyond_role_management_is_refused(self):
        for text in (
            "create table crm.sneaky (id int)",
            "create schema shadow",
            "alter database postgres set search_path = public",
            "create extension pgcrypto",
            "alter system set log_statement = 'none'",
        ):
            self.reject(GOOD + "\n" + text + ";\n")

    def test_reassigning_ownership_is_refused(self):
        self.reject(GOOD + "\nreassign owned by origenlab_owner to postgres;\n")

    def test_dynamic_execution_is_refused(self):
        self.reject(GOOD + "\nexecute 'create role origenlab_x';\n", contains="execute")

    def test_a_psql_meta_command_is_refused(self):
        self.reject(GOOD + "\n\\password origenlab_migrator\n")

    def test_named_dollar_quoting_is_refused(self):
        self.reject(GOOD.replace("$$", "$body$"), contains="dollar")

    def test_an_unrecognised_statement_shape_is_refused_rather_than_ignored(self):
        """The completeness rule. `grant all on schema crm to origenlab_api` is not one of the four
        recognised shapes; the analyser must refuse the file rather than silently skip it."""
        self.reject(
            GOOD + "\ngrant origenlab_api, origenlab_worker to origenlab_migrator;\n",
            contains="analyser does not understand",
        )

    def test_a_schema_grant_is_refused_by_the_forbidden_noun(self):
        self.reject(GOOD + "\ngrant usage on schema crm to origenlab_api;\n", contains="schema")

    def test_a_mutation_hidden_inside_a_do_block_is_still_seen(self):
        """A `do` body is analysed as code, not swallowed by the dollar markers."""
        self.reject(
            GOOD.replace(
                "    create role origenlab_worker\n      login inherit",
                "    grant origenlab_owner to postgres;\n    create role origenlab_worker\n      login inherit",
            ),
            contains="supabase-managed",
        )

    def test_a_keyword_hidden_in_a_comment_is_not_a_false_positive(self):
        bootstrap.analyse(GOOD + "\n-- this comment mentions password, drop and postgres\n", name="probe")

    def test_an_unterminated_string_literal_is_refused(self):
        self.reject(GOOD + "\n-- x\nselect 'unterminated;\n")


class TestEnvironmentClassification(unittest.TestCase):
    def test_staging_is_approved_and_carries_its_durability_decision(self):
        env = bootstrap.validate_environment("staging")
        self.assertEqual(env.classification, "staging")
        self.assertTrue(env.durability.decided)
        self.assertIn("seven-day", env.durability.summary)
        self.assertIn("declined", env.durability.summary.lower())

    def test_production_is_blocked_until_an_explicit_rpo_and_pitr_decision(self):
        with self.assertRaises(bootstrap.BootstrapError) as caught:
            bootstrap.validate_environment("production")
        message = str(caught.exception).lower()
        self.assertIn("blocked", message)
        self.assertIn("pitr", message)

    def test_an_empty_classification_is_refused_rather_than_defaulted(self):
        with self.assertRaises(bootstrap.BootstrapError) as caught:
            bootstrap.validate_environment("")
        self.assertIn("no default target", str(caught.exception))

    def test_an_unapproved_classification_is_refused(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.validate_environment("dev")

    def test_a_host_name_or_connection_string_is_refused_and_never_echoed(self):
        for value in (
            "db.abcdefghijklmnopqrst.supabase.co",
            "postgresql://origenlab_migrator:pw@db.abcdefghijklmnopqrst.supabase.co:5432/postgres",
            "abcdefghijklmnopqrst",
            "PRODUCTION",
        ):
            with self.assertRaises(bootstrap.BootstrapError) as caught:
                bootstrap.validate_environment(value)
            self.assertNotIn(value, str(caught.exception))

    def test_the_classification_never_reaches_the_emitted_sql(self):
        env = bootstrap.validate_environment("staging")
        sql, _ = bootstrap.dry_run(REPO_ROOT, env)
        self.assertNotIn("staging", sql.lower())
        bootstrap.assert_environment_absent(sql, env)

    def test_a_classification_leaking_into_the_sql_is_caught(self):
        env = bootstrap.validate_environment("staging")
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.assert_environment_absent("-- staging\ncreate role x;", env)


class TestDryRunOutput(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        # argparse writes its own usage errors to the real sys.stderr, so redirect that too --
        # otherwise a test that proves an argument is rejected prints a usage block into the suite.
        with contextlib.redirect_stderr(err):
            code = bootstrap_cli.main(argv, repo_root=REPO_ROOT, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_stdout_is_the_committed_file_byte_for_byte(self):
        code, out, _ = self.run_cli(["--environment", "staging", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(out, (REPO_ROOT / "supabase" / "hosted_roles.sql").read_text(encoding="utf-8"))

    def test_the_dry_run_is_deterministic(self):
        first = self.run_cli(["--environment", "staging", "--dry-run"])
        second = self.run_cli(["--environment", "staging", "--dry-run"])
        self.assertEqual(first, second)

    def test_stdout_discloses_no_target_of_any_kind(self):
        _, out, _ = self.run_cli(["--environment", "staging", "--dry-run"])
        lowered = out.lower()
        for forbidden in ("supabase.co", "postgresql://", "postgres://", "staging", "sslmode", "5432"):
            self.assertNotIn(forbidden, lowered)

    def test_neither_stream_carries_a_credential_or_a_project_reference(self):
        from olaudit import redact

        _, out, err = self.run_cli(["--environment", "staging", "--dry-run"])
        redact.assert_clean(out, what="bootstrap stdout")
        redact.assert_clean(err, what="bootstrap stderr")

    def test_the_summary_reports_the_matrix_and_the_durability_posture(self):
        _, _, err = self.run_cli(["--environment", "staging", "--dry-run"])
        for role in bootstrap.MANAGED_ROLES:
            self.assertIn(role, err)
        self.assertIn("NOLOGIN", err)
        self.assertIn("NOINHERIT", err)
        self.assertIn("seven-day", err)
        self.assertIn("Supabase-managed roles touched: none", err)

    def test_production_refuses_with_a_non_zero_status_and_emits_no_sql(self):
        code, out, err = self.run_cli(["--environment", "production", "--dry-run"])
        self.assertEqual(code, bootstrap_cli.EXIT_REFUSED)
        self.assertEqual(out, "")
        self.assertIn("REFUSED", err)
        self.assertIn("blocked", err.lower())

    def test_an_unapproved_environment_emits_no_sql(self):
        code, out, _ = self.run_cli(["--environment", "dev", "--dry-run"])
        self.assertEqual(code, bootstrap_cli.EXIT_REFUSED)
        self.assertEqual(out, "")

    def test_the_environment_argument_is_required(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["--dry-run"])

    def test_the_dry_run_flag_is_required_so_the_absence_of_apply_is_explicit(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["--environment", "staging"])

    def test_there_is_no_apply_mode(self):
        for flag in ("--apply", "--execute", "--host", "--target", "--connection-string", "--password"):
            with self.assertRaises(SystemExit):
                self.run_cli(["--environment", "staging", "--dry-run", flag, "x"])


class TestNoConnectionIsPossible(unittest.TestCase):
    """The strongest claim this tool makes is that it cannot connect. Prove it from the source."""

    def test_neither_bootstrap_module_imports_a_connection_capability(self):
        for name in ("bootstrap.py", "bootstrap_cli.py"):
            source = (REPO_ROOT / "supabase" / "audit" / "olaudit" / name).read_text(encoding="utf-8")
            for forbidden in ("import socket", "import subprocess", "psqlrun", "target_hosted", "target_local"):
                self.assertNotIn(forbidden, source, f"{name} must not be able to reach a database")

    def test_the_entry_point_only_wires_the_bootstrap_cli(self):
        source = (REPO_ROOT / "supabase" / "audit" / "run_bootstrap.py").read_text(encoding="utf-8")
        self.assertIn("olaudit.bootstrap_cli", source)
        for forbidden in ("psql", "subprocess", "socket"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
