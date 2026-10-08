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
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="SET\nnot_ready\nRESET\n", stderr="")
        with mock.patch.object(postflight.subprocess, "run", return_value=fake):
            self.assertFalse(postflight.healthy_worker({}))
        good = subprocess.CompletedProcess(args=[], returncode=0, stdout="SET\nhealthy\nRESET\n", stderr="")
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
               "head_repository": {"full_name": context.REPOSITORY}}
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
                           ("head_repository", {"full_name": "attacker/repo"}), ("head_sha", "b"*40)):
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
        with mock.patch.object(postflight.subprocess, "run", return_value=CompletedProcess([],0,"not_ready\n")) as run:
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


if __name__ == "__main__":
    unittest.main()
