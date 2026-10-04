"""Hosted data load — the verified logical copy of origenlab_clean into origenlab-v2.

    uv run python scripts/hosted_data_load.py plan     --out ~/data/origenlab-v2-migration/hosted-load \
        --authorize-hosted-connection --authorize-supavisor-session-route
    uv run python scripts/hosted_data_load.py apply    --plan <file> --plan-sha256 <hex> \
        --authorize-hosted-connection --authorize-supavisor-session-route
    uv run python scripts/hosted_data_load.py verify   --plan <file> --plan-sha256 <hex> …
    uv run python scripts/hosted_data_load.py rollback --plan <file> --plan-sha256 <hex> --confirm-delete-loaded-rows …

The hosted credential is read from the environment variable named in supabase/.audit/hosted_target.env
and never from argv. Every log line is redacted, unexpected errors included (no traceback is printed;
Ctrl-C still interrupts). Exit codes:
  0  ok
  11 refused: preflight, plan file or a target that changed; nothing was written
  12 apply/rollback failed after its write transaction began (rolled back, or commit outcome
     unknown: run verify)
  13 verify mismatch: the target is not the planned load (the runbook's rollback trigger)
  14 verify incomplete: the target read, the post-load dump, the scratch mint or the restore drill
     could not run (infrastructure); nothing is known to be wrong with the target — fix and re-run
`rollback` empties the 16 loaded tables with DELETE (never TRUNCATE: 37 foreign keys from outside tables
reference them and CASCADE would wipe the campaign hold row).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosted_data_load_io as io_  # noqa: E402
from origenlab_api.v2 import hosted_data_load_plan as hp  # noqa: E402

EXIT_REFUSED, EXIT_APPLY_FAILED, EXIT_VERIFY_MISMATCH, EXIT_VERIFY_INCOMPLETE = 11, 12, 13, 14
#: What main() needs to report an unexpected exception: the hosted values to redact (known once the
#: target file resolves) and whether apply/rollback had opened their write transaction. Reset per run.
_RUN: dict = {"secrets": (), "write_began": False}
#: What pg_dump_target can raise: its own redacted RuntimeError, docker missing, docker cp failing.
_DUMP_ERRORS = (RuntimeError, OSError, subprocess.CalledProcessError)
OUT_DIR = Path("~/data/origenlab-v2-migration/hosted-load").expanduser()
DEFAULT_HOSTED_OPERATOR = "3b2cddb9-2c18-4d12-93ed-6714d3e8b57a"
SCRATCH_SH = Path(__file__).with_name("hosted_data_load_scratch.sh")
CHECKLIST = [("casos (crm.opportunity)", "crm.opportunity"), ("cotizaciones (crm.quote)", "crm.quote"),
             ("instituciones (crm.organization)", "crm.organization"), ("campañas archivadas", "outbound.campaign"),
             ("destinatarios", "outbound.campaign_recipient"), ("eventos", "crm.domain_event")]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_target(args):
    target = io_.hosted_target(io_.REPO_ROOT, os.environ, Path(args.target_file))
    _RUN["secrets"] = io_.target_secrets(target)
    return target


def connect_target(args):
    """The target alone: verify and rollback read nothing from the source."""
    target = _resolve_target(args)
    return io_.connect_target(target), target


def connect_both(args):
    target = _resolve_target(args)
    # autocommit=True: every transaction in this tool is opened and closed by an explicit
    # BEGIN / COMMIT / ROLLBACK statement, so its boundaries are visible in the code.
    src = psycopg.connect(io_.source_conninfo(), autocommit=True)
    try:
        tgt = io_.connect_target(target)
    except Exception:
        src.close()
        raise
    return src, tgt, target


class PlanRefused(Exception):
    """The plan file is not one this tool will act on (exit 11)."""


def load_plan(path: Path) -> tuple[dict, str]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    names = [t.get("name") for t in plan.get("tables", [])]
    if names != list(hp.TABLES):
        raise PlanRefused("plan refused: its table list is not exactly the 16 tables this tool loads, in order")
    return plan, hp.plan_sha256(plan)


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a UUID: {value!r}") from None


def print_checks(checks, secrets=()) -> None:
    for c in checks:
        mark = "ok " if c["ok"] else "REFUSED"
        detail = "" if c["ok"] else f"  {io_.redact(json.dumps(c['detail'], default=str), secrets)[:200]}"
        print(f"  {mark} {c['check']}{detail}")


def print_fingerprint_diff(checks, sf, tf, secrets=()) -> None:
    """On a schema_fingerprint_identical refusal: per differing category up to 10 catalogue lines
    (``-`` only on the source, ``+`` only on the target). Catalogue text only, never data; redacted."""
    if "schema_fingerprint_identical" not in hp.refused(checks) or not sf or not tf:
        return
    diff = io_.fingerprint_diff_lines({"schemas": sf.get("schema_fingerprint", {}).get("schemas"),
                                       "lines": sf.get("schema_fingerprint_lines", {})},
                                      {"schemas": tf.get("schema_fingerprint", {}).get("schemas"),
                                       "lines": tf.get("schema_fingerprint_lines", {})})
    print("schema differs from the clean room (- source only, + target only):")
    for cat, lines in diff.items():
        print(f"  [{cat}]")
        for ln in lines:
            print("    " + io_.redact(ln, secrets)[:300])
    if not diff:
        print("  (no line-level difference: the plan file's fingerprint is the one that differs)")


def _facts(args, src, tgt, target, hosted_operator: str, pg_dump_probe: bool):
    sf = io_.read_source_facts(src, io_.REPO_ROOT, hosted_operator)
    tf = io_.read_target_facts(tgt, target, pg_dump_probe=pg_dump_probe)
    hf = io_.read_host_facts(io_.REPO_ROOT, Path(args.target_file), target)
    return sf, tf, hf


def cmd_plan(args) -> int:
    src, tgt, target = connect_both(args)
    try:
        secrets = io_.target_secrets(target)
        out = Path(args.out).expanduser()
        # A refused run may leave the empty 0700 --out directory and nothing else.
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        probe = out / f"probe-{_stamp()}.schema.dump"
        try:
            io_.pg_dump_target(target, probe, schema_only=True)
            probe_ok = True
        except _DUMP_ERRORS as exc:
            print(io_.redact(str(exc), secrets))
            probe_ok = False
        finally:
            probe.unlink(missing_ok=True)
            Path(str(probe) + ".sha256").unlink(missing_ok=True)
        sf, tf, hf = _facts(args, src, tgt, target, args.hosted_operator, probe_ok)
        plan = hp.build_plan(source=sf, target=tf, hosted_operator=args.hosted_operator, generated_at=_now())
        sha = hp.plan_sha256(plan)
        checks = hp.evaluate(plan=plan, plan_sha=sha, expected_sha=sha, source=sf, target=tf, host=hf, now_iso=_now())
        print_checks(checks, secrets)
        print_fingerprint_diff(checks, sf, tf, secrets)
        if hp.refused(checks):
            print("\npreflight refused; no plan written")
            return EXIT_REFUSED
        path = out / f"plan-{_stamp()}.json"
        path.write_text(json.dumps(plan, indent=1, sort_keys=True), encoding="utf-8")
        path.chmod(0o600)
        print(f"\nplan: {path}\nsha256: {sha}")
        print(f"{'table':40} {'rows':>8}  hash")
        for t in plan["tables"]:
            print(f"{t['name']:40} {t['count']:>8}  {t['hash'][:16]}")
        print(f"payload rows remapped: {plan['payload_rows_with_local_uuid']}; sequence -> {plan['sequence']['target_last_value']}")
        return 0
    finally:
        src.close()
        tgt.close()


def _close_clean(conn) -> None:
    """Never leave a transaction open behind a close (a pooler may not abort it for us)."""
    try:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            with conn.cursor() as cur:
                io_.best_effort_rollback(cur)
    except psycopg.Error:
        pass
    finally:
        conn.close()


def _reread_target(tgt, secrets) -> None:
    """After a rollback: prove the target is back to empty, in a fresh read-only transaction."""
    try:
        with tgt.cursor() as cur:
            cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                cur.execute("SET LOCAL ROLE origenlab_owner")
                counts = io_.target_counts(cur)
            finally:
                io_.best_effort_rollback(cur)
    except psycopg.Error as exc:
        print("could not re-read target: " + io_.redact(str(exc), secrets)[:400])
        return
    dirty = {t: n for t, n in counts.items() if n}
    print(f"target after rollback: all {len(counts)} tables empty" if not dirty
          else f"target after rollback: NOT empty {dirty}")


def _reread_state(tgt, plan, secrets) -> None:
    """After a failed rollback: say only what a fresh read-only transaction observes."""
    try:
        with tgt.cursor() as cur:
            cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                cur.execute("SET LOCAL ROLE origenlab_owner")
                io_.apply_session_settings(cur, local=True)
                state = io_.classify_target_state(plan, io_.observed_rows(cur, plan))
                nxt = io_.next_sequence_value(cur)
            finally:
                io_.best_effort_rollback(cur)
    except psycopg.Error as exc:
        print("could not re-read target: " + io_.redact(str(exc), secrets)[:400])
        return
    print(f"target re-read: state {state}; {hp.SEQUENCE} next value {nxt}")


def _rolled_back(ok: bool) -> str:
    return "transaction rolled back" if ok else "rollback attempted (the ROLLBACK itself failed)"


def _begin_write(tc, plan) -> None:
    """Open apply's / rollback's one write transaction. Every wait is bounded (30 s per lock, 5 min per
    statement, 60 s idle inside the transaction, so a vanished client cannot hold the locks), one loader
    at a time (advisory lock), as the owner, with pinned settings, and the 16 tables locked against
    writers BEFORE anything is checked: nothing can commit between a check and the writes it guards."""
    tc.execute("BEGIN")
    tc.execute("SET LOCAL lock_timeout = '30s'")
    tc.execute("SET LOCAL statement_timeout = '5min'")
    tc.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
    tc.execute("select pg_advisory_xact_lock(%s)", (hp.ADVISORY_LOCK_KEY,))
    tc.execute("SET LOCAL ROLE origenlab_owner")
    io_.apply_session_settings(tc, local=True)
    io_.lock_tables(tc, [t["name"] for t in plan["tables"]])


def _abort_apply(sc, tc, tgt, secrets) -> int:
    ok = io_.best_effort_rollback(tc)
    io_.best_effort_rollback(sc)
    print(_rolled_back(ok))
    _reread_target(tgt, secrets)
    return EXIT_APPLY_FAILED


def cmd_apply(args) -> int:
    started = time.monotonic()
    plan, sha = load_plan(args.plan)
    src, tgt, target = connect_both(args)
    try:
        secrets = io_.target_secrets(target)
        sf, tf, hf = _facts(args, src, tgt, target, plan["remap"]["to"], pg_dump_probe=True)
        checks = hp.evaluate(plan=plan, plan_sha=sha, expected_sha=args.plan_sha256, source=sf, target=tf, host=hf,
                             now_iso=_now())
        print_checks(checks, secrets)
        print_fingerprint_diff(checks, sf, tf, secrets)
        if hp.refused(checks):
            print("preflight refused; nothing written")
            return EXIT_REFUSED
        out = Path(args.out).expanduser()
        try:
            pre = io_.pg_dump_target(target, out / f"pre-{_stamp()}.dump")
        except _DUMP_ERRORS as exc:
            print(io_.redact(str(exc), secrets))
            print("pre-load dump failed; nothing written")
            return EXIT_REFUSED
        print(f"pre-load dump: {pre['path']} ({pre['bytes']} bytes)")

        remap_to = plan["remap"]["to"]
        with src.cursor() as sc, tgt.cursor() as tc:
            # Phase 1: the source snapshot must still match the plan. Nothing is open on the target.
            sc.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            try:
                io_.apply_session_settings(sc, local=True)
                for t in plan["tables"]:
                    cols = [c[0] for c in t["columns"]]
                    sc.execute(hp.row_hash_sql(hp.remap_select_sql(t["name"], cols, remap_to), t["pk"]))
                    if tuple(sc.fetchone()) != (t["count"], t["hash"]):
                        io_.best_effort_rollback(sc)
                        print(f"source drifted since plan: {t['name']}")
                        return EXIT_REFUSED
            except psycopg.Error as exc:
                io_.best_effort_rollback(sc)
                print("source snapshot check failed: " + io_.redact(str(exc), secrets)[:400])
                return EXIT_REFUSED

            # Phase 2: the one write transaction. Any psycopg error rolls everything back (exit 12).
            _RUN["write_began"] = True
            try:
                _begin_write(tc, plan)
                # The preflight read is minutes old: under the locks, the target must still be pristine.
                # (target_counts covers exactly the 16, platform.command_receipt included; business_counts
                # covers the EMPTY_SCHEMAS tables, read through the same helper as rollback.)
                changed = {**{n: c for n, c in io_.target_counts(tc).items() if c != 0},
                           **io_.rows_outside_load(plan, io_.business_counts(tc))}
                if changed:
                    io_.best_effort_rollback(tc)
                    io_.best_effort_rollback(sc)
                    print(f"target changed since preflight (table: rows) {changed}; nothing written")
                    return EXIT_REFUSED
                for t in plan["tables"]:
                    io_.set_fks_deferrable(tc, t["name"], t["fks"], True)
                    io_.set_triggers(tc, t["name"], False)
                tc.execute("SET CONSTRAINTS ALL DEFERRED")
                copied: dict[str, int] = {}
                for t in plan["tables"]:
                    cols = [c[0] for c in t["columns"]]
                    copied[t["name"]] = n = io_.copy_table(sc, tc, t["name"], cols,
                                                           hp.remap_select_sql(t["name"], cols, remap_to))
                    print(f"  copied {t['name']:40} {n:>8}")
                short = {t["name"]: (copied[t["name"]], t["count"]) for t in plan["tables"]
                         if copied[t["name"]] != t["count"]}
                if short:
                    print(f"copied row counts differ from plan (copied, expected): {short}")
                    return _abort_apply(sc, tc, tgt, secrets)
                tc.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for t in plan["tables"]:
                    io_.set_triggers(tc, t["name"], True)
                    io_.set_fks_deferrable(tc, t["name"], t["fks"], False)
                io_.restart_sequence(tc, plan["sequence"]["target_last_value"] + 1)
                inv = io_.post_copy_invariants(tc, plan, remap_to)
                print_checks(inv, secrets)
                got, want = [c["check"] for c in inv], io_.invariant_names(plan)
                if got != want:
                    print(f"invariant set differs from the expected one: missing {sorted(set(want) - set(got))}, "
                          f"unexpected {sorted(set(got) - set(want))}")
                    return _abort_apply(sc, tc, tgt, secrets)
                if hp.refused(inv):
                    print("invariants failed")
                    return _abort_apply(sc, tc, tgt, secrets)
            except psycopg.Error as exc:
                print("apply failed: " + io_.redact(str(exc), secrets)[:400])
                return _abort_apply(sc, tc, tgt, secrets)
            except BaseException:
                ok = io_.best_effort_rollback(tc)
                io_.best_effort_rollback(sc)
                print(f"apply interrupted; {_rolled_back(ok)}")
                raise

            # Phase 3: COMMIT, judged on its own: a failure here leaves the outcome unknown.
            try:
                tc.execute("COMMIT")
            except psycopg.Error as exc:
                print("COMMIT raised: " + io_.redact(str(exc), secrets)[:400])
                print("commit outcome unknown; run `verify` before anything else")
                return EXIT_APPLY_FAILED
            except BaseException:
                print("commit outcome unknown — run `verify` before anything else")
                raise
            if tc.statusmessage != "COMMIT":
                print(f"commit outcome unknown (server answered {tc.statusmessage!r}); run `verify` before anything else")
                return EXIT_APPLY_FAILED
            io_.best_effort_rollback(sc)
        report = out / f"apply-{_stamp()}.json"
        try:
            report.write_text(json.dumps({"plan_sha256": sha, "pre_dump": pre, "committed_at": _now(),
                                          "tables": copied, "elapsed_seconds": round(time.monotonic() - started, 1)},
                                         indent=1))
            report.chmod(0o600)
            print(f"COMMITTED. report: {report}")
        except OSError as exc:
            print("COMMITTED, but the report could not be written: " + io_.redact(str(exc), secrets)[:200])
        return 0
    finally:
        _close_clean(src)
        _close_clean(tgt)


def _mismatch(msg: str) -> int:
    print(msg)
    return EXIT_VERIFY_MISMATCH


def _incomplete(msg: str) -> int:
    print(msg)
    print("VERIFY INCOMPLETE: nothing is known to be wrong with the target; fix the cause and re-run verify")
    return EXIT_VERIFY_INCOMPLETE


def _print_counts(plan, obs) -> None:
    print(f"  {'table':40} {'observed':>9} {'planned':>9}")
    for t in plan["tables"]:
        n = obs[t["name"]][0]
        mark = "" if (n, obs[t["name"]][1]) == (t["count"], t["hash"]) else "   <- differs"
        print(f"  {t['name']:40} {n:>9} {t['count']:>9}{mark}")


def cmd_verify(args) -> int:
    plan, sha = load_plan(args.plan)
    if sha != args.plan_sha256:
        print("plan sha256 mismatch")
        return EXIT_REFUSED
    tgt, target = connect_target(args)
    try:
        secrets = io_.target_secrets(target)
        # Read-only re-hash of the target, as the owner role (the login role is NOINHERIT).
        try:
            with tgt.cursor() as tc:
                tc.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                try:
                    tc.execute("SET LOCAL ROLE origenlab_owner")
                    io_.apply_session_settings(tc, local=True)
                    obs = io_.observed_rows(tc, plan)
                    state = io_.classify_target_state(plan, obs)
                    inv = io_.post_copy_invariants(tc, plan, plan["remap"]["to"], obs) if state == "loaded" else []
                finally:
                    io_.best_effort_rollback(tc)
        except psycopg.Error as exc:
            return _incomplete("verify could not read the target: " + io_.redact(str(exc), secrets)[:400])
        print(f"target state: {state}")
        if state != "loaded":
            _print_counts(plan, obs)
            if state == "empty":
                return _mismatch("VERIFY MISMATCH — empty: the load did not commit; apply may be re-run after a "
                                 "fresh plan")
            return _mismatch("VERIFY MISMATCH — partial state: treat as an incident, do not re-apply")
        print_checks(inv, secrets)
        if [c["check"] for c in inv] != io_.invariant_names(plan):
            return _mismatch("VERIFY MISMATCH: invariant set differs from the expected one")
        if hp.refused(inv):
            return _mismatch("VERIFY MISMATCH")
        try:
            post = io_.pg_dump_target(target, Path(args.out).expanduser() / f"post-{_stamp()}.dump")
        except _DUMP_ERRORS as exc:
            return _incomplete("post-load dump failed: " + io_.redact(str(exc), secrets)[:400])
        try:
            res = subprocess.run(["bash", str(SCRATCH_SH), "mint"], capture_output=True, text=True)
        except OSError as exc:
            return _incomplete("scratch mint could not run: " + io_.redact(str(exc), secrets))
        lines = res.stdout.strip().splitlines()
        if res.returncode != 0 or not lines:
            return _incomplete("scratch mint failed: " + io_.redact(res.stderr, secrets)[-400:])
        scratch = lines[-1].strip()
        if not io_.DISPOSABLE_DB.fullmatch(scratch):
            return _incomplete("scratch mint printed an unexpected name; refusing to use it (nothing dropped)")
        try:
            io_.restore_into_scratch(Path(post["path"]), scratch)
            with psycopg.connect(io_.scratch_conninfo(scratch), autocommit=True) as sconn, sconn.cursor() as cur:
                cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                try:
                    io_.apply_session_settings(cur, local=True)
                    restored = io_.classify_target_state(plan, io_.observed_rows(cur, plan))
                finally:
                    io_.best_effort_rollback(cur)
        except (RuntimeError, OSError, subprocess.CalledProcessError, psycopg.Error) as exc:
            return _incomplete("restore drill failed: " + io_.redact(str(exc), secrets)[:400])
        finally:
            try:
                dropped = subprocess.run(["bash", str(SCRATCH_SH), "drop", scratch], capture_output=True,
                                         check=False).returncode == 0
            except OSError:
                dropped = False
            if not dropped:
                print(f"WARNING: scratch database {scratch} could not be dropped; remove it manually")
        print(f"post-load dump: {post['path']}; restore into {scratch}: {restored}")
        if restored != "loaded":
            return _mismatch("VERIFY MISMATCH: the post-load dump does not restore to the planned rows")
        print("\nChecklist para el dashboard:")
        for label, table in CHECKLIST:
            print(f"  {label:36} {obs[table][0]}")
        print("  personas: 0 · flags de envío: false/false · retenciones activas: 1")
        return 0
    finally:
        _close_clean(tgt)


def _guard_checks(cur, plan) -> list[dict]:
    return [{"check": "roster_unchanged", "ok": io_.roster_hash(cur) == plan["roster_hash"], "detail": None},
            {"check": "send_control_unchanged",
             "ok": io_._hash_rows(cur, "select * from outbound.send_control") == plan["send_control_hash"], "detail": None},
            {"check": "campaign_block_unchanged",
             "ok": io_._hash_rows(cur, "select * from outbound.campaign_block") == plan["campaign_block_hash"],
             "detail": None}]


def _refuse_rollback(tc, msg: str) -> int:
    io_.best_effort_rollback(tc)
    print(msg)
    return EXIT_REFUSED


def cmd_rollback(args) -> int:
    plan, sha = load_plan(args.plan)
    if sha != args.plan_sha256:
        print("plan sha256 mismatch")
        return EXIT_REFUSED
    tgt, target = connect_target(args)
    try:
        secrets = io_.target_secrets(target)
        with tgt.cursor() as tc:
            _RUN["write_began"] = True
            try:
                _begin_write(tc, plan)
                if io_.classify_target_state(plan, io_.observed_rows(tc, plan)) != "loaded":
                    return _refuse_rollback(tc, "target does not match the plan exactly; refusing to delete anything. "
                                                "Stop and see `rollback --help` (incident procedure).")
                outside = io_.rows_outside_load(plan, io_.business_counts(tc))
                if outside:
                    return _refuse_rollback(tc, "rows exist outside the loaded tables (table: rows) "
                                                f"{outside}; the target is no longer exactly what was loaded. "
                                                "Stop and see `rollback --help` (incident procedure).")
                guards = _guard_checks(tc, plan)
                if hp.refused(guards):
                    print_checks(guards, secrets)
                    return _refuse_rollback(tc, "roster or singleton rows changed since the plan; refusing. "
                                                "Stop and see `rollback --help` (incident procedure).")
                for t in plan["tables"]:
                    io_.set_fks_deferrable(tc, t["name"], t["fks"], True)
                    io_.set_triggers(tc, t["name"], False)
                tc.execute("SET CONSTRAINTS ALL DEFERRED")
                for t in reversed(plan["tables"]):
                    tc.execute(f"DELETE FROM {t['name']}")
                tc.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for t in plan["tables"]:
                    io_.set_triggers(tc, t["name"], True)
                    io_.set_fks_deferrable(tc, t["name"], t["fks"], False)
                # Every check on fresh reads, then the sequence reset as the last statement, then COMMIT.
                dirty = {n: c for n, c in io_.target_counts(tc).items() if c != 0}
                outside = io_.rows_outside_load(plan, io_.business_counts(tc))
                guards = _guard_checks(tc, plan)
                print_checks(guards, secrets)
                if dirty or outside or hp.refused(guards):
                    print("after DELETE: " + (f"tables not empty {dirty}" if dirty else
                                              f"rows outside the load {outside}" if outside else
                                              "roster or singleton rows changed"))
                    print(_rolled_back(io_.best_effort_rollback(tc)))
                    _reread_state(tgt, plan, secrets)
                    return EXIT_APPLY_FAILED
                io_.restart_sequence(tc, 1)
            except psycopg.Error as exc:
                ok = io_.best_effort_rollback(tc)
                print("rollback failed: " + io_.redact(str(exc), secrets)[:400])
                print(_rolled_back(ok))
                _reread_state(tgt, plan, secrets)
                return EXIT_APPLY_FAILED
            except BaseException:
                ok = io_.best_effort_rollback(tc)
                print(f"rollback interrupted; {_rolled_back(ok)}; run `verify` before anything else")
                raise
            try:
                tc.execute("COMMIT")
            except psycopg.Error as exc:
                print("COMMIT raised: " + io_.redact(str(exc), secrets)[:400])
                print("commit outcome unknown; run `verify` before anything else")
                return EXIT_APPLY_FAILED
            except BaseException:
                print("commit outcome unknown — run `verify` before anything else")
                raise
            if tc.statusmessage != "COMMIT":
                print(f"commit outcome unknown (server answered {tc.statusmessage!r}); run `verify` before anything else")
                return EXIT_APPLY_FAILED
        print("ROLLED BACK: the 16 tables are empty again; roster and singletons untouched")
        return 0
    finally:
        _close_clean(tgt)


VERIFY_EPILOG = """\
Exit codes:
  0  loaded: every table, invariant, the post-load dump and its restore drill match the plan
  13 VERIFY MISMATCH: the target is not the planned load (empty: the load did not commit, apply may be
     re-run after a fresh plan; partial: an incident, do not re-apply)
  14 VERIFY INCOMPLETE: the target read, post-load dump, scratch mint or restore drill could not run;
     nothing is known to be wrong with the target: fix the cause and re-run verify (never roll back on 14)
"""

ROLLBACK_EPILOG = """\
Incident procedure (documented only; this tool runs none of it).
  * A rollback refusal means the target is no longer exactly the loaded state. Stop.
  * Run `verify` to see the state.
  * The pre-load dump records the pre-load roster and singleton rows. Inspect it with
    `pg_restore --list` or `pg_restore -f -` INSIDE the container (the host's pg_restore is 16 and
    cannot read a 17 archive): `docker cp <dump> origenlab_dev_db:/tmp/x.dump`, then
    `docker exec origenlab_dev_db pg_restore --list /tmp/x.dump`; do not restore it
    (a data-only restore cannot undo a load, collides with the roster and singletons, and
    --disable-triggers needs a superuser the hosted target does not offer).
  * Removing rows written after the load is a reviewed, manual transaction.
  * The Supabase daily project backup is the whole-project last resort.
"""


def _common(p: argparse.ArgumentParser, with_plan: bool) -> None:
    p.add_argument("--target-file", default=str(io_.TARGET_FILE))
    p.add_argument("--out", default=str(OUT_DIR), help="where plan, dumps and reports are written (0700/0600)")
    p.add_argument("--authorize-hosted-connection", action="store_true", required=True)
    p.add_argument("--authorize-supavisor-session-route", action="store_true", required=True)
    if with_plan:
        p.add_argument("--plan", required=True, type=Path)
        p.add_argument("--plan-sha256", required=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    _common(p, False)
    p.add_argument("--hosted-operator", default=DEFAULT_HOSTED_OPERATOR, type=_uuid)
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("apply")
    _common(p, True)
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("verify", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=VERIFY_EPILOG)
    _common(p, True)
    p.set_defaults(func=cmd_verify)
    p = sub.add_parser("rollback", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=ROLLBACK_EPILOG)
    _common(p, True)
    p.add_argument("--confirm-delete-loaded-rows", action="store_true", required=True,
                   help="confirm deleting the rows of the 16 loaded tables (DELETE, never TRUNCATE)")
    p.set_defaults(func=cmd_rollback)
    args = ap.parse_args(argv)
    _RUN.update(secrets=(), write_began=False)
    try:
        return int(args.func(args))
    except PlanRefused as exc:
        print(exc)
        return EXIT_REFUSED
    except Exception as exc:  # not BaseException: Ctrl-C still interrupts
        # Connect failures, docker/subprocess errors, a bad plan file …: one redacted line, no traceback
        # (a traceback would print the hosted host, address and login verbatim).
        print(f"{args.cmd}: unexpected error: " + io_.redact(f"{type(exc).__name__}: {exc}", _RUN["secrets"])[:600])
        if args.cmd == "verify":
            print("VERIFY INCOMPLETE: nothing is known to be wrong with the target; fix the cause and re-run verify")
            return EXIT_VERIFY_INCOMPLETE
        if _RUN["write_began"]:
            print("the write transaction had begun: run `verify` before anything else")
            return EXIT_APPLY_FAILED
        print("nothing was written")
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
