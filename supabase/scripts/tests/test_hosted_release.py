"""Offline invariants for the production release; never contacts hosted systems."""
from __future__ import annotations

import importlib.util
import os
from unittest import mock
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT / "scripts/deploy"))

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance

migrations = load(SCRIPT_DIR / "hosted_migrations.py", "hosted_migrations")
render = load(ROOT / "scripts/deploy/render_release.py", "render_release")
ci = load(ROOT / "scripts/deploy/check_release_ci.py", "check_release_ci")
activator = load(ROOT / "scripts/deploy/set_render_autodeploy_off.py", "set_render_autodeploy_off")
protection = load(ROOT / "scripts/deploy/check_branch_protection.py", "check_branch_protection")
postflight = load(ROOT / "scripts/deploy/verify_workers.py", "verify_workers")


class ProductionGates(unittest.TestCase):
    def test_ledger_is_exact_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            rows = [("20261007160000", "first", p / "one.sql"),
                    ("20261008120000", "second", p / "two.sql")]
            self.assertEqual(migrations.pending_files(rows, ["20261007160000"]), rows[1:])
            self.assertEqual(migrations.pending_files(rows, []), rows)
            self.assertEqual(migrations.pending_files(rows, [row[0] for row in rows]), [])
            for unexpected in (["20261008120000"], ["20261007160000", "unexpected"]):
                with self.assertRaises(Exception):
                    migrations.pending_files(rows, unexpected)

    def test_migration_requires_explicit_owner_role(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "supabase/migrations"
            p.mkdir(parents=True)
            f = p / "20261007160000_test.sql"
            f.write_text("select 1;")
            self.assertEqual(len(migrations.chain(Path(directory))), 1)
            with self.assertRaises(Exception):
                migrations.require_reviewed_owner_transition(f)
            f.write_text("set role origenlab_owner;\nselect 1;\nreset role;\n")
            migrations.require_reviewed_owner_transition(f)

    def test_render_autodeploy_gate(self):
        name, service_id = render.SERVICES[0]
        obj = dict(id=service_id, ownerId=render.OWNER,
                   repo=render.REPOSITORY, branch="main", autoDeployTrigger="off")
        render.verify_service(obj, service_id)
        for key, value in (("autoDeployTrigger", "commit"), ("branch", "other"),
                           ("ownerId", "other")):
            with self.assertRaises(Exception):
                render.verify_service(dict(obj, **{key: value}), service_id)

    def test_activation_refuses_another_service_or_account(self):
        name, service_id = render.SERVICES[0]
        allowed = dict(id=service_id, ownerId=render.OWNER, repo=render.REPOSITORY,
                       branch="main", autoDeployTrigger="commit")
        activator.check_identity(allowed, service_id)
        with self.assertRaises(Exception):
            activator.check_identity(dict(allowed, ownerId="untrusted"), service_id)
        with self.assertRaises(Exception):
            activator.check_identity(dict(allowed, id="different"), service_id)

    def test_unreviewed_main_ruleset_must_refuse_release(self):
        approved = dict(
            id=protection.RULESET_ID, enforcement="active",
            conditions={"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            bypass_actors=[],
            rules=[
                {"type": "pull_request",
                 "parameters": {"required_approving_review_count": 1,
                                "dismiss_stale_reviews_on_push": True}},
                {"type": "required_status_checks",
                 "parameters": {"strict_required_status_checks": True,
                                "required_status_checks": [
                                    {"context": x} for x in sorted(protection.REQUIRED_CHECKS)
                                ]}},
            ],
        )
        protection.validate(approved)
        with self.assertRaises(protection.Unprotected):
            protection.validate({**approved, "bypass_actors": [{"actor_id": 1}]})
        for bad in [0, None]:
            rule = dict(approved)
            rule["rules"] = [dict(approved["rules"][0],
                parameters={"required_approving_review_count": bad,
                            "dismiss_stale_reviews_on_push": True}), approved["rules"][1]]
            with self.assertRaises((protection.Unprotected, TypeError)):
                protection.validate(rule)
        with self.assertRaises(protection.Unprotected):
            protection.validate({**approved, "rules": approved["rules"][:1]})

    def test_atomic_migration_command_includes_ddl_and_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "supabase/migrations"
            folder.mkdir(parents=True)
            file = folder / "20261008121212_test.sql"
            file.write_text("set role origenlab_owner;\\nselect 1;\\nreset role;\\n")
            executed = []
            def fake_psql(env, *args):
                if "--single-transaction" in args:
                    executed.append(args)
                    return "SET"
                sql = args[-1]
                if "pg_has_role" in sql:
                    return "origenlab_migrator|postgres|true|170006|true|true|true"
                if "select version from supabase_migrations.schema_migrations" in sql:
                    return "20261008121212" if executed else ""
                raise AssertionError(f"Unrecognized SQL command shape: {args}")
            with mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main",
                                              "OL_PROD_BACKUP_VERIFIED": "true"}), \
                 mock.patch.object(migrations, "ROOT", root), \
                 mock.patch.object(migrations, "database_env", return_value={}), \
                 mock.patch.object(migrations, "psql", side_effect=fake_psql):
                migrations.run("apply")
            self.assertEqual(len(executed), 1)
            argv = executed[0]
            self.assertIn("--single-transaction", argv)
            self.assertIn("-f", argv)
            self.assertIn(str(file), argv)
            self.assertIn("insert into supabase_migrations.schema_migrations", argv[-1])
            self.assertIn("pg_advisory_xact_lock", argv[argv.index("-c") + 1])

    def test_failed_migration_does_not_run_postcommit_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "supabase/migrations"
            folder.mkdir(parents=True)
            (folder / "20261008121212_test.sql").write_text(
                "set role origenlab_owner;\\nselect 1;\\nreset role;\\n")
            sql_calls = []
            def fake_psql(env, *args):
                sql_calls.append(args)
                if "--single-transaction" in args:
                    raise migrations.Refused("simulated atomic rollback")
                if "pg_has_role" in args[-1]:
                    return "origenlab_migrator|postgres|true|170006|true|true|true"
                return ""
            with mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main",
                                              "OL_PROD_BACKUP_VERIFIED": "true"}), \
                 mock.patch.object(migrations, "ROOT", root), \
                 mock.patch.object(migrations, "database_env", return_value={}), \
                 mock.patch.object(migrations, "psql", side_effect=fake_psql):
                with self.assertRaises(migrations.Refused):
                    migrations.run("apply")
            self.assertEqual(sum("--single-transaction" in x for x in sql_calls), 1)

    def test_postflight_requires_cron_success_after_deploy(self):
        self.assertTrue(postflight.after_deploy(
            "2026-10-08T05:05:00Z", "2026-10-08T05:00:00Z"))
        self.assertFalse(postflight.after_deploy(
            "2026-10-08T04:55:00Z", "2026-10-08T05:00:00Z"))
        self.assertFalse(postflight.after_deploy(None, "2026-10-08T05:00:00Z"))
        self.assertFalse(postflight.after_deploy("2026-10-08T05:00:00Z", None))

    def test_postflight_refuses_missing_worker_heartbeat(self):
        import subprocess
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="SET\\nnot_ready\\nRESET\\n", stderr="")
        with mock.patch.object(postflight.subprocess, "run", return_value=fake):
            self.assertFalse(postflight.healthy_worker({}))
        good = subprocess.CompletedProcess(args=[], returncode=0, stdout="SET\\nhealthy\\nRESET\\n", stderr="")
        with mock.patch.object(postflight.subprocess, "run", return_value=good):
            self.assertTrue(postflight.healthy_worker({}))

    def test_all_four_services(self):
        self.assertEqual(len({r for _, r in render.SERVICES}), 4)

    def test_release_ci_requires_all_applicable_workflows_green(self):
        def item(name, status="completed", conclusion="success", attempt=1):
            return dict(name=name, event="push", head_sha="a" * 40,
                        status=status, conclusion=conclusion,
                        created_at="2026-10-08T00:00:00Z", run_attempt=attempt)
        ok = [item("supabase"), item("secret-scan"), item("worker"), item("api"), item("dashboard")]
        self.assertEqual(ci.check_runs(ok)[0], "success")
        self.assertEqual(ci.check_runs(ok + [item("api", status="in_progress", attempt=2)])[0], "pending")
        with self.assertRaises(ci.CIRefused):
            ci.check_runs(ok + [item("dashboard", conclusion="failure", attempt=2)])
        self.assertEqual(ci.check_runs([item("supabase")])[0], "pending")
        with self.assertRaises(ci.CIRefused):
            ci.check_runs(ok + [item("email-pipeline", conclusion="cancelled")])


    def test_pending_sql_refuses_transaction_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Path(directory) / "20261008121212_test.sql"
            safe = "set role origenlab_owner;\nselect 1;\nreset role;\n"
            f.write_text(safe)
            migrations.require_reviewed_owner_transition(f)
            for forbidden in ("COMMIT;", "ROLLBACK;", "BEGIN;", r"\i bad.sql"):
                f.write_text(safe + forbidden + "\n")
                with self.assertRaises(migrations.Refused):
                    migrations.require_reviewed_owner_transition(f)

    def test_render_cron_deploy_never_sends_unsupported_commit_id(self):
        sha = "a" * 40
        cron = "crn-db1tk02jnfac73eir6fg"
        def fake_request(method, path, token, payload=None):
            if method == "GET" and path.startswith("/services/") and "/deploys" not in path:
                service_id = path.split("/")[-1]
                return dict(id=service_id, ownerId=render.OWNER,
                            repo=render.REPOSITORY, branch="main",
                            autoDeployTrigger="off")
            if method == "POST" and cron in path:
                self.assertNotIn("commitId", payload)
                return dict(id="dep-test", commit=dict(id=sha), status="created")
            if method == "GET" and path.endswith("/deploys/dep-test"):
                return dict(id="dep-test", commit=dict(id=sha), status="live")
            raise AssertionError(f"Unexpected API call {method} {path}")
        def fake_latest(_, service_id):
            previous = "b" * 40 if service_id == cron else sha
            return dict(status="live", commit=dict(id=previous))
        env = dict(GITHUB_ACTIONS="true", GITHUB_REF="refs/heads/main",
                   RENDER_API_KEY="test-only", OL_RELEASE_SHA=sha)
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(render, "request", side_effect=fake_request), \
             mock.patch.object(render, "latest_deploy", side_effect=fake_latest), \
             mock.patch.object(render, "check_current_main") as git_check:
            render.run("release")
            git_check.assert_called_once_with(sha)

    def test_render_refuses_auto_deploy_before_any_deployment(self):
        sha = "a" * 40
        calls = []
        def fake_request(method, path, token, payload=None):
            calls.append((method, path))
            return dict(id=path.split("/")[-1], ownerId=render.OWNER,
                        repo=render.REPOSITORY, branch="main",
                        autoDeployTrigger="commit")
        env = dict(GITHUB_ACTIONS="true", GITHUB_REF="refs/heads/main",
                   RENDER_API_KEY="test-only", OL_RELEASE_SHA=sha)
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(render, "request", side_effect=fake_request):
            with self.assertRaises(render.Refused):
                render.run("release")
        self.assertEqual([method for method,_ in calls], ["GET"])


if __name__ == "__main__":
    unittest.main()
