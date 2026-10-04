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
    for name, task in (("apply", "Task 5"), ("verify", "Task 6"), ("rollback", "Task 6")):
        p = sub.add_parser(name)
        _common(p, True)
        if name == "rollback":
            p.add_argument("--confirm-truncate", action="store_true", required=True)
        p.set_defaults(func=lambda a, n=name, t=task: sys.exit(f"{n}: {t}"))
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
