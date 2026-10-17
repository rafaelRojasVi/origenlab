"""Offline invariants for the production release; never contacts hosted systems."""
from __future__ import annotations

import importlib.util
import io
import json
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


def approved_service(ident, **changes):
    return dict(id=ident, ownerId=render.OWNER, repo=render.REPOSITORY,
                branch="main", autoDeployTrigger="off",
                suspended="suspended" if ident == render.LEGACY_CRON[1] else "not_suspended",
                serviceDetails={"envSpecificDetails":{"startCommand":render.WORKER_COMMAND}}, **changes)


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
        obj = approved_service(service_id)
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
                 "parameters": {"strict_required_status_checks_policy": True,
                                "required_status_checks": [
                                    {"context": x, "integration_id": 15368} for x in sorted(protection.REQUIRED_CHECKS)
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

    def test_explicit_solo_mode_keeps_checks_and_forbids_hidden_bypasses(self):
        base = dict(
            id=protection.RULESET_ID, enforcement="active", target="branch",
            conditions={"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
            bypass_actors=[],
            rules=[
                {"type": "pull_request",
                 "parameters": {"required_approving_review_count": 0,
                                "dismiss_stale_reviews_on_push": True}},
                {"type": "required_status_checks",
                 "parameters": {"strict_required_status_checks_policy": True,
                                "required_status_checks": [
                                    {"context": "gitleaks", "integration_id": 15368}]}}
            ],
        )
        protection.validate(base, solo_mode=True)
        # Real GitHub API uses strict_required_status_checks_policy, NOT
        # the fictitious strict_required_status_checks used by old fixtures.
        wrong_shape = dict(base, rules=[base["rules"][0], {
            "type": "required_status_checks",
            "parameters": {"strict_required_status_checks": True,
                           "required_status_checks": [
                               {"context": "gitleaks", "integration_id": 15368}]}
        }])
        with self.assertRaises(protection.Unprotected):
            protection.validate(wrong_shape, solo_mode=True)
        placeholder = dict(base, rules=[base["rules"][0], {
            "type": "required_status_checks",
            "parameters": {"strict_required_status_checks_policy": True,
                           "required_status_checks": [
                               {"context": "add checks"},
                               {"context": "add status checks"},
                               {"context": "gitleaks", "integration_id": 15368}]}
        }])
        with self.assertRaisesRegex(protection.Unprotected, "unbound mandatory checks"):
            protection.validate(placeholder, solo_mode=True)
        with self.assertRaises(protection.Unprotected):
            protection.validate(base, solo_mode=False)
        with self.assertRaises(protection.Unprotected):
            protection.validate(dict(base, bypass_actors=[{"actor_id": 10}]), solo_mode=True)
        with self.assertRaises(protection.Unprotected):
            protection.validate(dict(base, rules=base["rules"][:1]), solo_mode=True)
        with self.assertRaises(protection.Unprotected):
            protection.validate(dict(base, rules=[
                base["rules"][0],
                {"type": "required_status_checks", "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "gitleaks", "integration_id": 0}]}}
            ]), solo_mode=True)

    def test_solo_mode_requires_repository_owner_as_author_and_merger(self):
        approval = load(ROOT / "scripts/deploy/check_release_approval.py", "check_release_approval_solo")
        sha = "f" * 40
        owner = approval.REPOSITORY.split("/")[0]
        pr = dict(merged_at="2026-10-08", merge_commit_sha=sha,
                  base={"ref": "main", "repo": {"full_name": approval.REPOSITORY}},
                  head={"sha": "a" * 40, "repo": {"full_name": approval.REPOSITORY}},
                  user={"login": owner}, merged_by={"login": owner})
        self.assertTrue(approval.approved(pr, [], sha, solo_mode=True))
        self.assertFalse(approval.approved(pr, [], sha))
        for invalid in (
            dict(pr, user={"login": "other"}),
            dict(pr, merged_by={"login": "other"}),
            dict(pr, merge_commit_sha="0" * 40),
            dict(pr, head={"sha": "a" * 40, "repo": {"full_name": "other/repo"}}),
            dict(pr, base={"ref": "dev", "repo": {"full_name": approval.REPOSITORY}}),
            dict(pr, merged_at=None),
        ):
            self.assertFalse(approval.approved(invalid, [], sha, solo_mode=True))

    def test_api_readiness_reads_the_public_route_at_the_released_commit(self):
        render = load(ROOT / "scripts/deploy/render_release.py", "render_release_health")
        sha = "c" * 40
        self.assertEqual(render.API_HEALTH_URL, "https://dashboard.origenlab.cl/api/health")
        self.assertNotIn("onrender.com/health", (ROOT / "scripts/deploy/render_release.py").read_text())
        class Answer:
            def __init__(self, body): self.body = body
            def __enter__(self): return io.BytesIO(json.dumps(self.body).encode())
            def __exit__(self, *a): return False
        seen, waits = [], []
        answers = [Answer({"ok": True, "service": "origenlab-api", "version": "0" * 40}),
                   Answer({"ok": True, "service": "origenlab-api", "version": sha})]
        def opener(request, timeout):
            seen.append(request.full_url)
            return answers.pop(0)
        render.verify_api_http(sha, opener=opener, sleep=waits.append)
        self.assertEqual(seen, [render.API_HEALTH_URL] * 2)
        self.assertEqual(len(waits), 1)
        stale = lambda request, timeout: Answer({"ok": True, "service": "origenlab-api", "version": "0" * 40})
        with self.assertRaises(render.Refused):
            render.verify_api_http(sha, attempts=3, opener=stale, sleep=lambda s: None)

    def test_every_review_check_step_receives_the_selected_review_policy(self):
        # The post-backup recheck lacked OL_RELEASE_SOLO_MODE and refused the first release (2026-10-13).
        workflow = (ROOT / ".github/workflows/production-db-migrations.yml").read_text()
        steps = [s for s in workflow.split("      - name: ")
                 if "python3 scripts/deploy/check_branch_protection.py" in s
                 or "python3 scripts/deploy/check_release_approval.py" in s]
        self.assertGreaterEqual(len(steps), 3)
        for step in steps:
            self.assertIn("OL_RELEASE_SOLO_MODE: ${{ vars.OL_RELEASE_SOLO_MODE }}", step, step.splitlines()[0])

    def test_solo_mode_reads_the_merger_from_the_pull_request_not_the_commit_list(self):
        # GitHub's /commits/{sha}/pulls list omits merged_by: every solo release was refused (2026-10-13).
        approval = load(ROOT / "scripts/deploy/check_release_approval.py", "check_release_approval_merger")
        sha = "f" * 40
        owner = approval.REPOSITORY.split("/")[0]
        listed = dict(number=7, merged_at="2026-10-13", merge_commit_sha=sha,
                      base={"ref": "main", "repo": {"full_name": approval.REPOSITORY}},
                      head={"sha": "a" * 40, "repo": {"full_name": approval.REPOSITORY}},
                      user={"login": owner})
        full = dict(listed, merged_by={"login": owner})
        def fake_get(path, merger=full):
            return {f"/commits/{sha}/pulls?per_page=100": [listed], "/pulls/7/reviews?per_page=100": [],
                    "/pulls/7": merger}[path]
        with mock.patch.dict(os.environ, {"OL_RELEASE_SHA": sha, "OL_RELEASE_SOLO_MODE": "true"}):
            with mock.patch.object(approval, "get", fake_get):
                approval.main()
            with mock.patch.object(approval, "get", lambda path: fake_get(path, dict(full, merged_by={"login": "other"}))):
                with self.assertRaises(approval.Refused):
                    approval.main()
            with mock.patch.object(approval, "get", lambda path: fake_get(path, dict(full, number=8))):
                with self.assertRaises(approval.Refused):
                    approval.main()

    def test_atomic_migration_command_includes_ddl_and_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "supabase/migrations"
            folder.mkdir(parents=True)
            file = folder / "20261008121212_test.sql"
            file.write_text("set role origenlab_owner;\nselect 1;\nreset role;\n")
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
                "set role origenlab_owner;\nselect 1;\nreset role;\n")
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

    def test_postflight_requires_success_after_deploy(self):
        self.assertTrue(postflight.after_deploy(
            "2026-10-08T05:05:00Z", "2026-10-08T05:00:00Z"))
        self.assertFalse(postflight.after_deploy(
            "2026-10-08T04:55:00Z", "2026-10-08T05:00:00Z"))
        self.assertFalse(postflight.after_deploy(None, "2026-10-08T05:00:00Z"))
        self.assertFalse(postflight.after_deploy("2026-10-08T05:00:00Z", None))

    def test_postflight_refuses_missing_worker_heartbeat(self):
        import subprocess
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="SET\ntrue|false|true\nRESET\n", stderr="")
        with mock.patch.object(postflight.subprocess, "run", return_value=fake):
            self.assertFalse(postflight.healthy_worker({}))
        good = subprocess.CompletedProcess(args=[], returncode=0, stdout="SET\ntrue|true|true\nRESET\n", stderr="")
        with mock.patch.object(postflight.subprocess, "run", return_value=good):
            self.assertTrue(postflight.healthy_worker({}))

    def test_three_pinned_services_and_legacy_cron_guard(self):
        self.assertEqual(len({r for _, r in render.SERVICES}), 3)
        self.assertEqual(len(render.ALL_SERVICES), 4)
        self.assertNotIn(render.LEGACY_CRON, render.SERVICES)

    def test_release_ci_requires_all_applicable_workflows_green(self):
        def item(name, status="completed", conclusion="success", attempt=1):
            return dict(name=name, event="push", head_sha="a" * 40,
                        status=status, conclusion=conclusion,
                        created_at="2026-10-08T00:00:00Z", run_attempt=attempt)
        ok = [item(name) for name in ci.MANDATORY]
        self.assertEqual(ci.check_runs(ok)[0], "success")
        self.assertEqual(ci.check_runs(ok + [item("api", status="in_progress", attempt=2)])[0], "pending")
        with self.assertRaises(ci.CIRefused):
            ci.check_runs(ok + [item("dashboard", conclusion="failure", attempt=2)])
        self.assertEqual(ci.check_runs([item("supabase")])[0], "pending")
        with self.assertRaises(ci.CIRefused):
            ci.check_runs(ok + [item("email-pipeline", conclusion="cancelled", attempt=2)])


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

    def test_every_deploy_is_pinned_and_cron_is_never_deployed(self):
        sha = "a" * 40
        posts=[]
        def fake(method,path,token,payload=None):
            if method == "POST":
                posts.append(path)
                self.assertEqual(payload["commitId"],sha)
                self.assertNotIn(render.LEGACY_CRON[1],path)
                return {"id":"dep-fixture","commit":{"id":sha},"status":"created"}
            if path.endswith("/deploys/dep-fixture"):
                return {"id":"dep-fixture","commit":{"id":sha},"status":"live"}
            return approved_service(path.rsplit("/",1)[-1])
        env=dict(GITHUB_ACTIONS="true",GITHUB_REF="refs/heads/main",RENDER_API_KEY="test-only",OL_RELEASE_SHA=sha)
        with mock.patch.dict(os.environ,env), mock.patch.object(render,"request",side_effect=fake), \
             mock.patch.object(render,"latest_deploy",return_value=None), \
             mock.patch.object(render,"check_current_main"), mock.patch.object(render,"verify_api_http"):
            render.run("release")
        self.assertEqual(posts,[f"/services/{ident}/deploys" for _,ident in render.SERVICES])

    def test_active_legacy_cron_refuses_before_any_mutation(self):
        env=dict(GITHUB_ACTIONS="true",GITHUB_REF="refs/heads/main",RENDER_API_KEY="test-only",OL_RELEASE_SHA="a"*40)
        calls=[]
        def fake(method,path,token,payload=None):
            calls.append(method)
            obj=approved_service(render.LEGACY_CRON[1]); obj["suspended"]="not_suspended"
            return obj
        with mock.patch.dict(os.environ,env), mock.patch.object(render,"request",side_effect=fake):
            with self.assertRaisesRegex(render.Refused,"must be suspended"):
                render.run("release")
        self.assertEqual(calls,["GET"])

    def test_unapproved_worker_entrypoint_refuses_before_rollout(self):
        obj=approved_service(render.SERVICES[0][1])
        obj["serviceDetails"]["envSpecificDetails"]["startCommand"]="uv run --no-sync origenlab-worker unknown-worker"
        with self.assertRaisesRegex(render.Refused,"consolidated"):
            render.verify_service(obj,render.SERVICES[0][1])

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


class RegressionGates(unittest.TestCase):
    def test_transaction_escapes_hidden_by_comments_or_inline_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sql"
            for escape in ("select 1; COMMIT;", "/* comment */ END;", "COMMIT AND CHAIN;",
                           "START TRANSACTION;", "ABORT;", "PREPARE TRANSACTION 'x';",
                           "-- comment\nROLLBACK TO SAVEPOINT x;", "select 1;\\gexec", "SET SESSION AUTHORIZATION postgres;"):
                path.write_text("SET ROLE origenlab_owner; " + escape + " RESET ROLE;")
                with self.subTest(escape=escape), self.assertRaises(migrations.Refused):
                    migrations.require_reviewed_owner_transition(path)
            path.write_text("SET ROLE origenlab_owner; DO $$ BEGIN PERFORM 1; END $$; RESET ROLE;")
            migrations.require_reviewed_owner_transition(path)

    def test_comments_cannot_fake_role_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sql"
            path.write_text("-- SET ROLE origenlab_owner;\nSELECT 1; -- RESET ROLE;")
            with self.assertRaises(migrations.Refused):
                migrations.require_reviewed_owner_transition(path)

    def test_ruleset_omission_is_not_proof_of_no_bypass(self):
        rule = dict(id=protection.RULESET_ID, enforcement="active",
                    conditions={"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}})
        with self.assertRaisesRegex(protection.Unprotected, "hidden"):
            protection.validate(rule)

    def test_event_provenance_and_all_disabled_paths(self):
        context = load(ROOT / "scripts/deploy/release_context.py", "release_context")
        sha = "a" * 40
        base = {"repository": {"full_name": context.REPOSITORY}}
        run = {"event": "push", "head_branch": "main", "head_sha": sha, "conclusion": "success",
               "head_repository": {"full_name": context.REPOSITORY},
               "name": "secret-scan", "path": ".github/workflows/secret-scan.yml"}
        event = dict(base, workflow_run=run)
        for kind, payload in (("workflow_run", event), ("workflow_dispatch", base)):
            for enabled in ("", "false"):
                with self.assertRaises(ValueError):
                    context.validate(kind, payload, "refs/heads/main", sha, "release", enabled, "")
        context.validate("workflow_dispatch", base, "refs/heads/main", sha, "plan", "", "")
        context.validate("workflow_dispatch", base, "refs/heads/main", sha, "backup_only", "", "")
        with self.assertRaises(ValueError):
            context.validate("schedule", base, "refs/heads/main", sha, "backup_only", "", "")
        context.validate("schedule", base, "refs/heads/main", sha, "backup_only", "", "true")
        context.validate("workflow_run", event, "refs/heads/main", sha, "release", "true", "")
        for key, value in (("head_branch", "dev"), ("event", "pull_request"),
                           ("head_repository", {"full_name": "attacker/repo"}), ("head_sha", "b"*40),
                           ("name", "supabase"), ("path", ".github/workflows/supabase.yml")):
            with self.assertRaises(ValueError):
                context.validate("workflow_run", dict(base, workflow_run={**run, key:value}),
                                 "refs/heads/main", sha, "release", "true", "")

    def test_ci_binds_identity_and_rejects_job_skip(self):
        sha = "a" * 40
        runs = [dict(name=name, event="push", head_sha=sha, head_branch="main",
                     head_repository={"full_name":"rafaelRojasVi/origenlab"},
                     path=f".github/workflows/{name}.yml", status="completed", conclusion="success")
                for name in ci.MANDATORY]
        self.assertEqual(ci.check_runs(runs, sha)[0], "success")
        with self.assertRaises(ci.CIRefused):
            ci.check_runs([dict(runs[0], head_branch="dev")] + runs[1:], sha)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        with mock.patch.object(ci.urllib.request, "urlopen", return_value=response), \
             mock.patch.object(ci.json, "load", return_value={"total_count":1,"jobs":[{"status":"completed","conclusion":"skipped"}]}):
            with self.assertRaises(ci.CIRefused):
                ci.validate_jobs({"id":1,"name":"api"}, "test-only")

    def test_postflight_sql_requires_completed_specific_sweep(self):
        from subprocess import CompletedProcess
        with mock.patch.object(postflight.subprocess, "run", return_value=CompletedProcess([],0,"false|false|false\n")) as run:
            self.assertFalse(postflight.healthy_worker({}, "2026-10-08T12:00:00Z"))
        query = run.call_args.args[0][-1]
        self.assertIn("task_name = 'sweep_untriaged'", query)
        self.assertNotIn("procrastinate_events", query)
        self.assertIn("left join evidence.source_record", query)
        self.assertIn("2026-10-08T12:00:00+00:00", query)

    def test_postflight_rejects_old_revision_locked_or_failed_capture(self):
        import json
        from datetime import datetime, timezone
        now=datetime.now(timezone.utc).isoformat()
        sha="a"*40
        event={"event":"capture_cycle","outcome":"success","gmail_mode":"history",
               "drive_mode":"file","revision":sha}
        row={"message":json.dumps(event),"timestamp":now,
             "labels":[{"name":"resource","value":render.SERVICES[0][1]}]}
        with mock.patch.object(postflight,"request",return_value={"logs":[row]}):
            self.assertTrue(postflight.swept_after_deploy("test-only",render.SERVICES[0][1],now,capture=True,revision=sha))
            for changes in ({"revision":"b"*40},{"outcome":"locked"},{"gmail_mode":"paused"}):
                row["message"]=json.dumps({**event,**changes})
                self.assertFalse(postflight.swept_after_deploy("test-only",render.SERVICES[0][1],now,capture=True,revision=sha))

    def test_setup_uses_supported_github_cli_variable_read(self):
        helper = (ROOT/"scripts/deploy/configure_release_secrets.sh").read_text()
        self.assertIn('gh api "repos/$repo/actions/variables/OL_RELEASE_SOLO_MODE" --jq .value', helper)
        self.assertNotIn("$(gh variable get", helper)
        self.assertIn('Cannot read the OL_RELEASE_SOLO_MODE repository variable', helper)

    def test_readonly_plan_and_backup_do_not_require_cron_cutover(self):
        workflow=(ROOT/".github/workflows/production-db-migrations.yml").read_text()
        section=workflow.split("- name: Block any release if independent Render deployments remain enabled",1)[1].split("- name:",1)[0]
        self.assertIn("if: steps.provenance.outputs.mode == 'release'",section)

    def test_exact_merge_requires_current_independent_collaborator_review(self):
        approval = load(ROOT / "scripts/deploy/check_release_approval.py", "check_release_approval")
        sha = "a" * 40
        pr = dict(merged_at="2026-10-08", merge_commit_sha=sha,
                  base={"ref":"main", "repo":{"full_name":approval.REPOSITORY}},
                  head={"sha":"b"*40, "repo":{"full_name":approval.REPOSITORY}}, user={"login":"author"})
        review = dict(id=1, state="APPROVED", commit_id="b"*40,
                      user={"login":"reviewer"}, author_association="COLLABORATOR")
        self.assertTrue(approval.approved(pr, [review], sha))
        for invalid in (dict(review, commit_id="c"*40), dict(review, user={"login":"author"}),
                        dict(review, author_association="NONE"), dict(review, state="DISMISSED")):
            self.assertFalse(approval.approved(pr, [invalid], sha))
        self.assertFalse(approval.approved(pr, [review,dict(review,id=2,state="CHANGES_REQUESTED")], sha))
        self.assertFalse(approval.approved(dict(pr,merge_commit_sha="c"*40), [review], sha))

    def test_sweep_log_checks_resource_and_timestamp(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        row = dict(message='{"event":"triage_sweep","found":0,"deferred":0}', timestamp=now,
                   labels=[{"name":"resource","value":render.SERVICES[0][1]}])
        with mock.patch.object(postflight,"request",return_value={"logs":[row]}):
            self.assertTrue(postflight.swept_after_deploy("test-only",render.SERVICES[0][1],now))
        with mock.patch.object(postflight,"request",return_value={"logs":[dict(row,labels=[])]}):
            self.assertFalse(postflight.swept_after_deploy("test-only",render.SERVICES[0][1],now))
        with mock.patch.object(postflight,"request",return_value={"logs":[dict(row,timestamp="2020-01-01T00:00:00Z")]}):
            self.assertFalse(postflight.swept_after_deploy("test-only",render.SERVICES[0][1],now))

    def test_ambiguous_render_post_is_never_blindly_retried(self):
        import urllib.error
        with mock.patch.object(render.urllib.request,"urlopen",side_effect=urllib.error.URLError("synthetic")) as call:
            with self.assertRaises(render.Refused):
                render.request("POST","/services/fake/deploys","test-only",{})
            self.assertEqual(call.call_count,1)

    def test_render_read_retries_are_bounded_and_shape_is_validated(self):
        import urllib.error
        with mock.patch.object(render.urllib.request,"urlopen",side_effect=urllib.error.URLError("synthetic")) as call, \
             mock.patch.object(render.time,"sleep"):
            with self.assertRaises(render.Refused):
                render.request("GET","/services/fake","test-only")
            self.assertEqual(call.call_count,3)

    def test_provider_failure_stops_before_downstream_services(self):
        sha = "a" * 40
        env = dict(GITHUB_ACTIONS="true",GITHUB_REF="refs/heads/main",RENDER_API_KEY="test-only",OL_RELEASE_SHA=sha)
        for state, deployed_sha in (("build_failed",sha),("canceled",sha),("unknown",sha),
                                     ("live",None),("build_in_progress","b"*40)):
            posts=[]
            def fake(method,path,token,payload=None):
                if method == "POST":
                    posts.append(path)
                    return {"id":"dep-fixture","commit":{"id":sha},"status":"created"}
                if path.endswith("/deploys/dep-fixture"):
                    return {"id":"dep-fixture","commit":{"id":deployed_sha},"status":state}
                return approved_service(path.rsplit("/",1)[-1])
            with self.subTest(state=state), mock.patch.dict(os.environ,env), \
                 mock.patch.object(render,"request",side_effect=fake), \
                 mock.patch.object(render,"latest_deploy",return_value=None), \
                 mock.patch.object(render,"check_current_main"), \
                 mock.patch.object(render,"verify_api_http"), \
                 mock.patch.object(render.time,"sleep"):
                with self.assertRaises(render.Refused):
                    render.run("release")
                self.assertEqual(posts,[f"/services/{render.SERVICES[0][1]}/deploys"])

    def test_api_liveness_failure_blocks_dashboard_on_idempotent_rerun(self):
        sha="a"*40
        env=dict(GITHUB_ACTIONS="true",GITHUB_REF="refs/heads/main",RENDER_API_KEY="test-only",OL_RELEASE_SHA=sha)
        def fake(method,path,token,payload=None):
            self.assertEqual(method,"GET")
            return approved_service(path.rsplit("/",1)[-1])
        with mock.patch.dict(os.environ,env), mock.patch.object(render,"request",side_effect=fake), \
             mock.patch.object(render,"latest_deploy",return_value={"status":"live","commit":{"id":sha}}), \
             mock.patch.object(render,"check_current_main"), \
             mock.patch.object(render,"verify_api_http",side_effect=render.Refused("synthetic readiness failure")):
            with self.assertRaises(render.Refused):
                render.run("release")



plan_mod = load(ROOT / "scripts/deploy/release_plan.py", "release_plan_under_test")
result_mod = load(ROOT / "scripts/deploy/release_result.py", "release_result")
OLD, NEW = "1" * 40, "2" * 40


def fake_diff(paths_by_base):
    return lambda base, sha: paths_by_base.get(base) if base in paths_by_base else None


class SelectiveRelease(unittest.TestCase):
    """Skip only what a release provably does not need; everything uncertain does more."""

    def test_path_to_service_mapping(self):
        f = plan_mod.services_for_path
        self.assertEqual(f("apps/dashboard/src/crm/pages/CotizadorPage.tsx"), {"dashboard"})
        self.assertEqual(f("apps/dashboard/package-lock.json"), {"dashboard"})
        self.assertEqual(f("apps/worker/src/origenlab_worker/cli.py"), {"worker"})
        self.assertEqual(f("apps/worker/uv.lock"), {"worker"})
        # The worker imports apps/api and apps/email-pipeline; the API image copies the pipeline.
        self.assertEqual(f("apps/api/src/origenlab_api/main.py"), {"worker", "api"})
        self.assertEqual(f("apps/api/uv.lock"), {"worker", "api"})
        self.assertEqual(f("apps/api/Dockerfile"), {"worker", "api"})
        self.assertEqual(f("apps/email-pipeline/src/x.py"), {"worker", "api"})
        self.assertEqual(f("supabase/migrations/20261017000000_x.sql"), {"worker", "api"})
        self.assertEqual(f("supabase/roles.sql"), {"worker", "api"})
        for none in ("docs/OPERATIONS.md", "apps/web/src/index.astro", "apps/dashboard-proxy/src/index.ts",
                     "README.md", "AGENTS.md", "supabase/tests/database/x.sql"):
            self.assertEqual(f(none), set(), none)

    def test_unknown_paths_deploy_everything(self):
        for unknown in ("render.yaml", "Makefile", "ruff.toml", "uv.lock", "package.json",
                        "scripts/deploy/render_release.py", "scripts/run_pytest.sh",
                        ".github/workflows/api.yml", "deploy/anything", "supabase/config.toml",
                        "supabase/scripts/hosted_migrations.py", "supabase/roles.sql.bak",
                        "apps/dashboardx/y", "apps/newapp/main.py", "packages/shared/x.ts", "docs"):
            self.assertEqual(plan_mod.services_for_path(unknown), plan_mod.ALL, unknown)

    def test_dashboard_only_change_deploys_only_the_dashboard(self):
        bases = dict(worker=OLD, api=OLD, dashboard=OLD)
        plan = plan_mod.build_plan(NEW, bases, 0, diff=fake_diff({OLD: ["apps/dashboard/src/a.tsx"]}))
        self.assertEqual({n: r["deploy"] for n, r in plan["services"].items()},
                         dict(worker=False, api=False, dashboard=True))
        self.assertFalse(plan["require_supabase"])
        self.assertEqual(plan_mod.expected_commits(plan, NEW), dict(worker=OLD, api=OLD, dashboard=NEW))
        self.assertIs(plan_mod.parse_plan(json.dumps(plan), NEW)["require_supabase"], False)
        self.assertIn("keep 111111111111", plan_mod.summary_lines(plan, NEW)[0])

    def test_each_service_is_diffed_from_its_own_live_commit(self):
        # The API stayed on OLD when the previous release was dashboard-only.
        diffs = {OLD: ["apps/api/src/x.py", "apps/dashboard/src/a.tsx"], "3" * 40: ["apps/dashboard/src/b.tsx"]}
        plan = plan_mod.build_plan(NEW, dict(worker="3" * 40, api=OLD, dashboard="3" * 40), 0, diff=fake_diff(diffs))
        self.assertEqual({n: r["deploy"] for n, r in plan["services"].items()},
                         dict(worker=False, api=True, dashboard=True))

    def test_uncertainty_deploys_and_requires_supabase(self):
        bases = dict(worker=OLD, api=OLD, dashboard=OLD)
        only_dash = fake_diff({OLD: ["apps/dashboard/src/a.tsx"]})
        for pending in (1, None):
            plan = plan_mod.build_plan(NEW, bases, pending, diff=only_dash)
            self.assertTrue(all(r["deploy"] for r in plan["services"].values()), pending)
            self.assertTrue(plan["require_supabase"], pending)
        unknown_api = plan_mod.build_plan(NEW, dict(bases, api=None), 0, diff=only_dash)
        self.assertTrue(unknown_api["services"]["api"]["deploy"])
        self.assertIsNone(unknown_api["services"]["api"]["base"])
        self.assertTrue(unknown_api["require_supabase"])
        unknown_path = plan_mod.build_plan(NEW, bases, 0, diff=fake_diff({OLD: ["render.yaml"]}))
        self.assertTrue(all(r["deploy"] for r in unknown_path["services"].values()))
        same = plan_mod.build_plan(NEW, dict(worker=NEW, api=NEW, dashboard=NEW), 0, diff=fake_diff({NEW: []}))
        self.assertFalse(any(r["deploy"] for r in same["services"].values()))

    def test_supabase_ci_binds_on_what_it_tests(self):
        bases = dict(worker=OLD, api=OLD, dashboard=OLD)
        for path in ("supabase/tests/database/x.sql", "supabase/migrations/20261017000000_x.sql",
                     "docs/OPERATIONS.md", ".github/workflows/supabase.yml"):
            plan = plan_mod.build_plan(NEW, bases, 0, diff=fake_diff({OLD: [path]}))
            self.assertTrue(plan["require_supabase"], path)
        for path in ("apps/dashboard/src/a.tsx", "apps/api/src/x.py", "docs/STATUS.md"):
            plan = plan_mod.build_plan(NEW, bases, 0, diff=fake_diff({OLD: [path]}))
            self.assertFalse(plan["require_supabase"], path)

    def test_supabase_ci_paths_mirror_its_workflow(self):
        workflow = (ROOT / ".github/workflows/supabase.yml").read_text()
        block = workflow.split("  pull_request:\n    paths:\n", 1)[1].split("  workflow_dispatch:", 1)[0]
        paths = {line.strip().removeprefix("- ").strip('"') for line in block.splitlines() if line.strip().startswith("- ")}
        mirrored = set(plan_mod.SUPABASE_CI_FILES) | {p + "**" for p in plan_mod.SUPABASE_CI_PREFIXES}
        self.assertEqual(paths, mirrored)

    def test_changed_paths_from_real_git_history(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git = lambda *a: subprocess.run(["git", "-C", directory, *a], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q"); git("config", "user.email", "t@example.invalid"); git("config", "user.name", "t")
            (repo / "apps/api").mkdir(parents=True); (repo / "apps/api/a.py").write_text("x = 1\n" * 20)
            git("add", "."); git("commit", "-qm", "one"); first = git("rev-parse", "HEAD")
            (repo / "apps/dashboard").mkdir(parents=True)
            git("mv", "apps/api/a.py", "apps/dashboard/a.py"); git("commit", "-qm", "rename"); second = git("rev-parse", "HEAD")
            # A rename is both of its paths, so the API side still deploys.
            self.assertEqual(plan_mod.changed_paths(first, second, root=repo), ["apps/api/a.py", "apps/dashboard/a.py"])
            self.assertEqual(plan_mod.changed_paths(second, second, root=repo), [])
            self.assertIsNone(plan_mod.changed_paths(second, first, root=repo))  # not an ancestor
            self.assertIsNone(plan_mod.changed_paths("f" * 40, second, root=repo))  # unknown commit
            self.assertIsNone(plan_mod.changed_paths(None, second, root=repo))

    def test_malformed_plans_refuse(self):
        good = plan_mod.build_plan(NEW, dict(worker=OLD, api=OLD, dashboard=OLD), 0,
                                   diff=fake_diff({OLD: ["apps/dashboard/src/a.tsx"]}))
        self.assertIsNone(plan_mod.parse_plan("", NEW))
        self.assertEqual(plan_mod.expected_commits(None, NEW), dict(worker=NEW, api=NEW, dashboard=NEW))
        broken = json.loads(json.dumps(good)); broken["services"]["api"]["base"] = None
        missing = json.loads(json.dumps(good)); del missing["services"]["worker"]
        for text, sha in (("{", NEW), (json.dumps(good), OLD), (json.dumps(broken), NEW),
                          (json.dumps(missing), NEW), (json.dumps(dict(good, require_supabase="no")), NEW)):
            with self.assertRaises(plan_mod.PlanRefused):
                plan_mod.parse_plan(text, sha)

    def test_required_ci_set_by_changed_paths(self):
        def item(name, status="completed", conclusion="success"):
            return dict(name=name, event="push", head_sha="a" * 40, status=status, conclusion=conclusion,
                        created_at="2026-10-08T00:00:00Z", run_attempt=1)
        self.assertIn("lint", ci.MANDATORY); self.assertIn("repo-scripts", ci.MANDATORY)
        without = ci.required_workflows("false")
        self.assertEqual(without, ci.MANDATORY - {"supabase"})
        for value in (None, "", "true", "False", "0", " false"):
            self.assertEqual(ci.required_workflows(value), ci.MANDATORY, value)
        others = [item(n) for n in without]
        self.assertEqual(ci.check_runs(others, required=without)[0], "success")
        self.assertEqual(ci.check_runs(others + [item("supabase", status="in_progress")], required=without)[0], "success")
        self.assertEqual(ci.check_runs(others, required=ci.MANDATORY)[0], "pending")
        # A red supabase run refuses even when supabase does not bind; so does any other red check.
        with self.assertRaises(ci.CIRefused):
            ci.check_runs(others + [item("supabase", conclusion="failure")], required=without)
        with self.assertRaises(ci.CIRefused):
            ci.check_runs([item(n, conclusion="failure" if n == "api" else "success") for n in without], required=without)
        # Other main checks are never optional.
        self.assertEqual(ci.check_runs([r for r in others if r["name"] != "lint"], required=without)[0], "pending")
        with self.assertRaises(ci.CIRefused):
            ci.check_runs(others, required=without - {"api"})

    def test_release_result_skip_counts_only_with_zero_pending(self):
        ok = result_mod.outcome
        self.assertIn("skipped", ok("success", "0", "skipped", "skipped", "success", "success"))
        ok("success", "2", "success", "success", "success", "success")
        ok("success", "0", "success", "success", "success", "success")
        refused = [
            ("success", "1", "skipped", "skipped", "success", "success"),   # pending, skipped
            ("success", "", "skipped", "skipped", "success", "success"),    # unknown pending
            ("success", "0", "failure", "skipped", "success", "success"),
            ("success", "0", "skipped", "failure", "success", "success"),
            ("success", "0", "cancelled", "skipped", "success", "success"),
            ("success", "0", "", "", "success", "success"),                 # missing results
            ("success", "0", "skipped", "success", "success", "success"),   # migrate without backup
            ("success", "0", "skipped", "skipped", "skipped", "success"),
            ("success", "0", "skipped", "skipped", "success", "skipped"),
            ("success", "0", "skipped", "skipped", "success", "failure"),
            ("failure", "0", "skipped", "skipped", "success", "success"),
            ("success", "1", "success", "failure", "success", "success"),
            ("success", "1", "success", "success", "failure", "success"),
        ]
        for case in refused:
            with self.subTest(case=case), self.assertRaises(result_mod.ReleaseIncomplete):
                ok(*case)

    def test_workflow_skips_backup_and_migrate_only_on_zero_pending(self):
        workflow = (ROOT / ".github/workflows/production-db-migrations.yml").read_text()
        job = lambda name: workflow.split(f"\n  {name}:\n", 1)[1].split("\n\n  ", 1)[0]
        self.assertIn("needs.preflight.outputs.pending_migrations != '0'", job("backup"))
        self.assertIn("needs.preflight.outputs.mode == 'backup_only'", job("backup"))
        self.assertIn("needs.preflight.outputs.pending_migrations != '0'", job("migrate"))
        deploy = job("deploy")
        for needle in ("!cancelled()", "needs.preflight.result == 'success'", "needs.migrate.result == 'success'",
                       "needs.preflight.outputs.pending_migrations == '0'", "needs.backup.result == 'skipped'",
                       "needs.migrate.result == 'skipped'", "OL_RELEASE_PLAN: ${{ needs.preflight.outputs.release_plan }}",
                       "python3 scripts/deploy/check_release_ci.py"):
            self.assertIn(needle, deploy)
        self.assertIn("needs.deploy.result == 'success'", job("verify"))
        self.assertIn("OL_RELEASE_PLAN: ${{ needs.preflight.outputs.release_plan }}", job("verify"))
        result = job("release-result")
        self.assertIn("python3 scripts/deploy/release_result.py", result)
        self.assertIn("PENDING_MIGRATIONS: ${{ needs.preflight.outputs.pending_migrations }}", result)
        self.assertNotIn("test \"$BACKUP_RESULT\"", result)
        self.assertIn('workflows: ["secret-scan"]', workflow)
        preflight = job("preflight")
        self.assertLess(preflight.index("render_release.py plan"), preflight.index("run: python3 scripts/deploy/check_release_ci.py"))
        self.assertLess(preflight.index("hosted_migrations.py plan"), preflight.index("render_release.py plan"))
        self.assertIn("OL_RELEASE_REQUIRE_SUPABASE: ${{ steps.release_plan.outputs.require_supabase }}", preflight)
        self.assertIn("fetch-depth: 0", preflight)
        trigger = (ROOT / ".github/workflows/secret-scan.yml").read_text()
        self.assertTrue(trigger.startswith("name: secret-scan\n"))
        self.assertIn("push:\n    branches: [main", trigger)
        self.assertNotIn("paths", trigger.split("pull_request", 1)[0])

    def test_migration_plan_reports_its_pending_count_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "output"
            rows = migrations.chain(ROOT)
            answers = ["origenlab_migrator|postgres|true|170006|true|true|true",
                       "\n".join(v for v, _, _ in rows)]
            sessions = []
            def fake_psql(env, *args):
                sessions.append(env.get("PGOPTIONS", ""))
                return answers.pop(0)
            env = dict(GITHUB_REF="refs/heads/main", GITHUB_OUTPUT=str(out), RUNNER_TEMP=directory)
            with mock.patch.dict(os.environ, env), mock.patch.object(migrations, "database_env", return_value={}), \
                 mock.patch.object(migrations, "psql", side_effect=fake_psql):
                migrations.run("plan")
            self.assertEqual(out.read_text(), "pending_migrations=0\n")
            # Every plan session is read-only: plan cannot write even with a bug.
            self.assertEqual(len(sessions), 2)
            for options in sessions:
                self.assertIn("-c default_transaction_read_only=on", options)
        workflow = (ROOT / ".github/workflows/production-db-migrations.yml").read_text()
        step = workflow.split("- name: Read-only hosted migration plan", 1)[1].split("- name:", 1)[0]
        self.assertIn('PGOPTIONS: "-c default_transaction_read_only=on"', step)

    def test_lint_runs_the_public_repo_hygiene_scan(self):
        # supabase does not bind most releases; the always-required lint workflow carries the scan.
        lint_sh = (ROOT / "scripts/lint.sh").read_text()
        self.assertIn("\nscripts/security/check-public-repo-hygiene.sh\n", lint_sh)
        self.assertLess(lint_sh.index("\nscripts/security/check-public-repo-hygiene.sh"), lint_sh.index("exec pre-commit"))
        self.assertIn("run: make lint", (ROOT / ".github/workflows/lint.yml").read_text())
        self.assertIn("lint:\n\tscripts/lint.sh", (ROOT / "Makefile").read_text())
        self.assertIn("lint", ci.MANDATORY)

    def _render_env(self, plan):
        env = dict(GITHUB_ACTIONS="true", GITHUB_REF="refs/heads/main", RENDER_API_KEY="test-only", OL_RELEASE_SHA=NEW)
        if plan is not None:
            env["OL_RELEASE_PLAN"] = json.dumps(plan)
        return env

    def test_dashboard_only_release_posts_only_the_dashboard_and_checks_the_kept_api(self):
        plan = plan_mod.build_plan(NEW, dict(worker=OLD, api=OLD, dashboard=OLD), 0,
                                   diff=fake_diff({OLD: ["apps/dashboard/src/a.tsx"]}))
        posts, health = [], []
        def fake(method, path, token, payload=None):
            if method == "POST":
                posts.append(path); self.assertEqual(payload["commitId"], NEW)
                return {"id": "dep-fixture", "commit": {"id": NEW}, "status": "created"}
            if path.endswith("/deploys/dep-fixture"):
                return {"id": "dep-fixture", "commit": {"id": NEW}, "status": "live"}
            return approved_service(path.rsplit("/", 1)[-1])
        with mock.patch.dict(os.environ, self._render_env(plan)), mock.patch.object(render, "request", side_effect=fake), \
             mock.patch.object(render, "latest_deploy", return_value={"status": "live", "commit": {"id": OLD}}), \
             mock.patch.object(render, "check_current_main") as stale, \
             mock.patch.object(render, "verify_api_http", side_effect=health.append):
            render.run("release")
        self.assertEqual(posts, [f"/services/{render.SERVICES[2][1]}/deploys"])
        self.assertEqual(health, [OLD])  # the API it keeps, not the new SHA
        self.assertEqual(stale.call_count, 3)  # the stale-main guard still runs before every service

    def test_kept_service_that_moved_since_preflight_refuses_before_any_post(self):
        plan = plan_mod.build_plan(NEW, dict(worker=OLD, api=OLD, dashboard=OLD), 0,
                                   diff=fake_diff({OLD: ["apps/dashboard/src/a.tsx"]}))
        def fake(method, path, token, payload=None):
            self.assertEqual(method, "GET")
            return approved_service(path.rsplit("/", 1)[-1])
        latest = lambda token, ident: {"status": "live", "commit": {"id": "3" * 40 if ident == render.SERVICES[1][1] else OLD}}
        with mock.patch.dict(os.environ, self._render_env(plan)), mock.patch.object(render, "request", side_effect=fake), \
             mock.patch.object(render, "latest_deploy", side_effect=latest), \
             mock.patch.object(render, "check_current_main"), mock.patch.object(render, "verify_api_http"):
            with self.assertRaisesRegex(render.Refused, "changed since the preflight plan"):
                render.run("release")

    def test_malformed_plan_refuses_before_any_provider_call(self):
        env = self._render_env(None); env["OL_RELEASE_PLAN"] = '{"sha": "%s"}' % NEW
        with mock.patch.dict(os.environ, env), mock.patch.object(render, "request") as call:
            with self.assertRaises(render.Refused):
                render.run("release")
        call.assert_not_called()

    def test_plan_mode_writes_outputs_from_live_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "output"
            env = dict(self._render_env(None), GITHUB_OUTPUT=str(out), OL_RELEASE_PENDING_MIGRATIONS="0")
            with mock.patch.dict(os.environ, env), \
                 mock.patch.object(render, "request", side_effect=lambda m, p, t, payload=None: approved_service(p.rsplit("/", 1)[-1])), \
                 mock.patch.object(render, "latest_deploy", return_value={"status": "live", "commit": {"id": OLD}}), \
                 mock.patch.object(render.release_plan, "changed_paths", return_value=["apps/dashboard/src/a.tsx"]):
                render.run("plan")
            lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
            self.assertEqual(lines["require_supabase"], "false")
            plan = plan_mod.parse_plan(lines["release_plan"], NEW)
            self.assertEqual([n for n, r in plan["services"].items() if r["deploy"]], ["dashboard"])
            # A failed latest deploy is no proof of what runs: that service deploys, supabase binds.
            out.write_text("")
            failed = lambda token, ident: {"status": "build_failed" if ident == render.SERVICES[0][1] else "live", "commit": {"id": OLD}}
            with mock.patch.dict(os.environ, dict(env, OL_RELEASE_PENDING_MIGRATIONS="")), \
                 mock.patch.object(render, "request", side_effect=lambda m, p, t, payload=None: approved_service(p.rsplit("/", 1)[-1])), \
                 mock.patch.object(render, "latest_deploy", side_effect=failed), \
                 mock.patch.object(render.release_plan, "changed_paths", return_value=["apps/dashboard/src/a.tsx"]):
                render.run("plan")
            lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
            self.assertEqual(lines["require_supabase"], "true")
            self.assertTrue(all(r["deploy"] for r in json.loads(lines["release_plan"])["services"].values()))

    def test_postflight_checks_kept_services_at_their_live_commit(self):
        plan = plan_mod.build_plan(NEW, dict(worker=OLD, api=OLD, dashboard=OLD), 0,
                                   diff=fake_diff({OLD: ["apps/dashboard/src/a.tsx"]}))
        commits = {render.SERVICES[0][1]: OLD, render.SERVICES[1][1]: OLD, render.SERVICES[2][1]: NEW}
        latest = lambda token, ident: {"status": "live", "commit": {"id": commits[ident]}, "finishedAt": "2026-10-17T00:00:00Z"}
        revisions = []
        with mock.patch.dict(os.environ, self._render_env(plan)), \
             mock.patch.object(postflight, "request", side_effect=lambda m, p, t, payload=None: approved_service(p.rsplit("/", 1)[-1])), \
             mock.patch.object(postflight, "verify_legacy_cron"), \
             mock.patch.object(postflight, "latest_deploy", side_effect=latest), \
             mock.patch.object(postflight, "database_env", return_value={}), \
             mock.patch.object(postflight, "healthy_worker", return_value=True), \
             mock.patch.object(postflight, "swept_after_deploy", side_effect=lambda *a, **k: revisions.append(k["revision"]) or True):
            postflight.verify()
            self.assertEqual(revisions, [OLD, OLD])
            commits[render.SERVICES[2][1]] = OLD  # the dashboard was meant to deploy
            with self.assertRaisesRegex(postflight.Refused, "dashboard is not live"):
                postflight.verify()


if __name__ == "__main__":
    unittest.main()
