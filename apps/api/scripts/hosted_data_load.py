"""Hosted data load — the verified logical copy of origenlab_clean into origenlab-v2.

    uv run python scripts/hosted_data_load.py plan     --out ~/data/origenlab-v2-migration/hosted-load \
        --authorize-hosted-connection --authorize-supavisor-session-route
    uv run python scripts/hosted_data_load.py apply    --plan <file> --plan-sha256 <hex> \
        --authorize-hosted-connection --authorize-supavisor-session-route
    uv run python scripts/hosted_data_load.py verify   --plan <file> --plan-sha256 <hex> …
    uv run python scripts/hosted_data_load.py rollback --plan <file> --plan-sha256 <hex> --confirm-truncate …

The hosted credential is read from the environment variable named in supabase/.audit/hosted_target.env
and never from argv. Every log line is redacted. Exit codes: 0 ok, 11 preflight refused,
12 apply invariant failed (transaction rolled back, target untouched), 13 verify mismatch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosted_data_load_io as io_  # noqa: E402
from origenlab_api.v2 import hosted_data_load_plan as hp  # noqa: E402

EXIT_REFUSED, EXIT_APPLY_FAILED, EXIT_VERIFY_MISMATCH = 11, 12, 13
OUT_DIR = Path("~/data/origenlab-v2-migration/hosted-load").expanduser()
DEFAULT_HOSTED_OPERATOR = "3b2cddb9-2c18-4d12-93ed-6714d3e8b57a"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def connect_both(args):
    repo = io_.REPO_ROOT
    target = io_.hosted_target(repo, os.environ, Path(args.target_file))
    # autocommit=True: every transaction in this tool is opened and closed by an explicit
    # BEGIN / COMMIT / ROLLBACK statement, so its boundaries are visible in the code.
    src = psycopg.connect(io_.source_conninfo(), autocommit=True)
    try:
        tgt = psycopg.connect(io_.conninfo_for(target), autocommit=True)
    except Exception:
        src.close()
        raise
    return src, tgt, target


def load_plan(path: Path) -> tuple[dict, str]:
    plan = json.loads(path.read_text(encoding="utf-8"))
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
        except (RuntimeError, OSError) as exc:
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
                _best_effort_rollback(cur)
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
                _best_effort_rollback(cur)
    except psycopg.Error as exc:
        print("could not re-read target: " + io_.redact(str(exc), secrets)[:400])
        return
    dirty = {t: n for t, n in counts.items() if n}
    print(f"target after rollback: all {len(counts)} tables empty" if not dirty
          else f"target after rollback: NOT empty {dirty}")


def _best_effort_rollback(cur) -> None:
    try:
        cur.execute("ROLLBACK")
    except psycopg.Error:
        pass


def _abort_apply(sc, tc, tgt, secrets) -> int:
    _best_effort_rollback(tc)
    _best_effort_rollback(sc)
    print("transaction rolled back")
    _reread_target(tgt, secrets)
    return EXIT_APPLY_FAILED


def cmd_apply(args) -> int:
    plan, sha = load_plan(args.plan)
    src, tgt, target = connect_both(args)
    try:
        secrets = io_.target_secrets(target)
        sf, tf, hf = _facts(args, src, tgt, target, plan["remap"]["to"], pg_dump_probe=True)
        checks = hp.evaluate(plan=plan, plan_sha=sha, expected_sha=args.plan_sha256, source=sf, target=tf, host=hf,
                             now_iso=_now())
        print_checks(checks, secrets)
        if hp.refused(checks):
            print("preflight refused; nothing written")
            return EXIT_REFUSED
        out = Path(args.out).expanduser()
        try:
            pre = io_.pg_dump_target(target, out / f"pre-{_stamp()}.dump")
        except (RuntimeError, OSError) as exc:
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
                        _best_effort_rollback(sc)
                        print(f"source drifted since plan: {t['name']}")
                        return EXIT_REFUSED
            except psycopg.Error as exc:
                _best_effort_rollback(sc)
                print("source snapshot check failed: " + io_.redact(str(exc), secrets)[:400])
                return EXIT_REFUSED

            # Phase 2: the one write transaction. Any psycopg error rolls everything back (exit 12).
            try:
                tc.execute("BEGIN")
                tc.execute("SET LOCAL lock_timeout = '30s'")
                tc.execute("SET LOCAL statement_timeout = '15min'")
                tc.execute("select pg_advisory_xact_lock(%s)", (hp.ADVISORY_LOCK_KEY,))
                tc.execute("SET LOCAL ROLE origenlab_owner")
                io_.apply_session_settings(tc, local=True)
                for t in plan["tables"]:
                    io_.set_fks_deferrable(tc, t["name"], t["fks"], True)
                    io_.set_triggers(tc, t["name"], False)
                tc.execute("SET CONSTRAINTS ALL DEFERRED")
                short = {}
                for t in plan["tables"]:
                    cols = [c[0] for c in t["columns"]]
                    n = io_.copy_table(sc, tc, t["name"], cols, hp.remap_select_sql(t["name"], cols, remap_to))
                    print(f"  copied {t['name']:40} {n:>8}")
                    if n != t["count"]:
                        short[t["name"]] = (n, t["count"])
                if short:
                    print(f"copied row counts differ from plan (copied, expected): {short}")
                    return _abort_apply(sc, tc, tgt, secrets)
                tc.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for t in plan["tables"]:
                    io_.set_triggers(tc, t["name"], True)
                    io_.set_fks_deferrable(tc, t["name"], t["fks"], False)
                tc.execute("select setval(%s::regclass, %s, true)", (hp.SEQUENCE, plan["sequence"]["target_last_value"]))
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
                _best_effort_rollback(tc)
                _best_effort_rollback(sc)
                print("apply interrupted; transaction rolled back")
                raise

            # Phase 3: COMMIT, judged on its own: a failure here leaves the outcome unknown.
            try:
                tc.execute("COMMIT")
            except psycopg.Error as exc:
                print("COMMIT raised: " + io_.redact(str(exc), secrets)[:400])
                print("commit outcome unknown; run `verify` before anything else")
                return EXIT_APPLY_FAILED
            if tc.statusmessage != "COMMIT":
                print(f"commit outcome unknown (server answered {tc.statusmessage!r}); run `verify` before anything else")
                return EXIT_APPLY_FAILED
            _best_effort_rollback(sc)
        report = out / f"apply-{_stamp()}.json"
        try:
            report.write_text(json.dumps({"plan_sha256": sha, "pre_dump": pre, "committed_at": _now()}, indent=1))
            report.chmod(0o600)
            print(f"COMMITTED. report: {report}")
        except OSError as exc:
            print("COMMITTED, but the report could not be written: " + io_.redact(str(exc), secrets)[:200])
        return 0
    finally:
        _close_clean(src)
        _close_clean(tgt)


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
    for name, task in (("verify", "Task 6"), ("rollback", "Task 6")):
        p = sub.add_parser(name)
        _common(p, True)
        if name == "rollback":
            p.add_argument("--confirm-truncate", action="store_true", required=True)
        p.set_defaults(func=lambda a, n=name, t=task: sys.exit(f"{n}: {t}"))
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
