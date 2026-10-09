"""Plan / apply / verify / rollback for every catalog importer: the CLI, the target guards, the manifest.

An importer (`import_price_lists.py`, `import_cost_parameters.py`, later the supplier-document and
quote-history importers) supplies only two things: the arguments its `plan` subcommand reads, and
a function that turns them into a plan (`origenlab_api.v2.catalog.importing`). Everything that
touches a database is here and in `_actions.py`, once.

Subcommands and exit codes (the shape of `hosted_data_load.py`):

    plan      --out DIR  + the importer's own inputs                  → DIR/plan.json, prints its sha256
    apply     --target-dsn --admin-dsn --operator-email --plan --plan-sha256 --out [--allow-cleanroom-production]
    verify    --target-dsn --plan --plan-sha256 --out                  (read-only)
    rollback  --target-dsn --admin-dsn --plan --plan-sha256 --out --confirm-delete-loaded-rows
              [--allow-cleanroom-production]

    0 done · 2 bad arguments · 11 refused · 12 apply/rollback failed (rolled back)
    · 13 verify found a missing or different row

    A refusal before the manifest is written writes nothing. Once the manifest has committed, a
    refusal (11) or failure (12) of the catalog transaction rolls back every catalog row and event
    but **the manifest stays**: the message and `apply-refused.json` / `apply-failed.json` say so
    (`manifest_kept`); a rerun reuses it and rollback removes it. A connection or query error
    outside the apply and rollback transactions (operator lookup, manifest, preflight, verify) is
    a refusal (11) reported by error class only.

**Outcomes and statuses.** apply reports each item as `inserted`, `already_present`,
`present_different` (a row this plan did not write already holds other values — an operator's
supplier terms, an earlier observation under the same key; never overwritten) or
`kept_later_value` (a cost parameter this plan set was set again later, or a quote document line
it wrote was reviewed since; never reverted). verify uses the same words — `present`,
`present_different`, `superseded_later`, `reviewed_later` — so the two agree, and
exits 13 only for `missing` or `different` (a row this plan wrote that no longer says what it
wrote). A rerun of apply therefore never fights an operator, and verify never fails forever on a
row the import was not allowed to change.

**The runtime login is proven, not assumed.** Before the manifest and again inside the catalog
transaction, `--target-dsn` must be session and current user `origenlab_api` with no elevated
attribute and no membership in owner, migrator, `postgres` or any elevated role (the probe of
`v2/remote_database.py`, 7fab57eb); an admin DSN given as `--target-dsn` is refused (11).

**Targets.** Every DSN must name a literal loopback IP (`v2/identity.py:is_loopback_dsn` and
`settings.assert_v2_target_is_local`; a host *name*, `localhost` included, is refused) — so a
hosted project is never reached through a DSN; it takes `--hosted-target` (below).
apply and rollback write only into a disposable `origenlab_test_<8 hex>` database, or into the
clean room `origenlab_clean` when `--allow-cleanroom-production` is given (then both DSNs must name
it and the server must say it is it — the checks of `quote_crm_import_production.py:271-274`).
`--target-dsn` and `--admin-dsn` must name one host, port and database.

**Hosted (`--hosted-target`).** The one exception to the loopback rule, and it takes no DSN:
`--hosted-target --authorize-hosted-connection --authorize-supavisor-session-route` (all three, or
refused) resolves the hosted project from PR #623's target file (`supabase/.audit/hosted_target.env`,
`olaudit.target_hosted.resolve`: Supavisor session route only, `verify-full` with the Supabase CA,
the address pinned after it is proven global). The admin side is that file's login (the migrator,
`SET LOCAL ROLE origenlab_owner` as everywhere here); the target side is the **same host, port,
database and CA** with the login swapped for `origenlab_api.<project ref>` and the password read
from `ORIGENLAB_API_DB_PASSWORD` — never from a command line. The runtime-login proof below runs
unchanged. Every printed line is redacted of the target's host, logins, address and passwords.
Rollback sets `session_replication_role`, which the hosted migrator may not be allowed to: if
so it fails closed (12, nothing deleted) and a hosted mistake is undone from the pre-load dump.

**Who writes what.** `--target-dsn` is the `origenlab_api` login: the catalog rows and their
domain events are written by it, in **one** transaction, under the same grants and RLS as the
running API. `--admin-dsn` is the owner-capable maintenance login. It records the manifest
(`evidence.source_record`, kind `migration_manifest`, `dedupe_key = 'catalog-import:' ||
plan_sha256`) in its own short transaction first, because the runtime role holds no INSERT on
`evidence.source_record` (Slice 0 grants). The manifest is idempotent: a rerun, or a rerun after
a failed apply, reuses it. Its `source_record.migration_manifest_recorded` event is appended in the
runtime transaction, with the rows it describes. The quote-history importer's documents (also
`evidence.source_record`) are recorded in the manifest's transaction for the same reason, their
payload naming the manifest; their lines are written by the runtime role. Rollback runs as
`origenlab_owner`.

**Plans never carry local paths**, and `--out` is refused inside this repository or any other git
checkout. Nothing here prints a value from a plan or a local path: only counts, statuses, item keys
and file basenames.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from origenlab_api.settings import V2TargetRefused, assert_v2_target_is_local
from origenlab_api.v2.catalog import importing
from origenlab_api.v2.catalog.keys import LabdeliveryRefused
from origenlab_api.v2.identity import OperatorIdentity, is_loopback_dsn
from origenlab_api.v2.remote_database import PRIVILEGED_ROLES

import _actions

EXIT_OK = 0
EXIT_REFUSED = 11
EXIT_APPLY_FAILED = 12
EXIT_MISMATCH = 13

REPO = Path(__file__).resolve().parents[4]
DISPOSABLE = re.compile(r"^origenlab_test_[0-9a-f]{8}$")
CLEANROOM_DB = "origenlab_clean"
#: Held (transaction-scoped) by every catalog apply and rollback: one at a time per database.
ADVISORY_LOCK_KEY = int.from_bytes(hashlib.sha256(b"origenlab:catalog_import").digest()[:8], "big", signed=True)
#: Operators who may load catalog data: the roles the catalog commands accept (`Deciding`).
WRITING_ROLES = ("sales", "admin")
RUNTIME_ROLE = "origenlab_api"
_SECRET_IN_URI = re.compile(r"(://[^:/@\s]+):[^@\s]*@")


class Refused(Exception):
    """A guard failed; nothing was written by the step that raised it."""


#: Values of the resolved hosted target that no printed line may carry (set by `resolve_hosted`).
_SECRETS: list[str] = []
HOSTED_DATABASE = "postgres"
RUNTIME_PASSWORD_ENV = "ORIGENLAB_API_DB_PASSWORD"


def redact(text: str) -> str:
    for value in sorted({v for v in _SECRETS if v}, key=len, reverse=True):
        text = text.replace(value, "***")
    return _SECRET_IN_URI.sub(r"\1:***@", text)


def _basename(exc: OSError) -> str:
    return Path(exc.filename).name if exc.filename else "an input"


def say(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True, default=str))


def refuse(message: str) -> int:
    print(f"refused: {redact(message)}", file=sys.stderr)
    return EXIT_REFUSED


# ------------------------------------------------------------------ targets (pure)

def dbname(dsn: str) -> str:
    if "://" not in dsn:  # a key=value conninfo (hosted mode builds only these)
        from psycopg.conninfo import conninfo_to_dict

        return str(conninfo_to_dict(dsn).get("dbname", ""))
    return urlsplit(dsn).path.lstrip("/")


def resolve_hosted(args: argparse.Namespace, environ: Any = None) -> None:
    """Fill `args.target_dsn` / `args.admin_dsn` from the hosted target file, or `Refused`."""
    import dataclasses

    if not (args.authorize_hosted_connection and args.authorize_supavisor_session_route):
        raise Refused("--hosted-target needs --authorize-hosted-connection and "
                      "--authorize-supavisor-session-route")
    if args.target_dsn or getattr(args, "admin_dsn", None) or getattr(args, "allow_cleanroom_production", False):
        raise Refused("--hosted-target takes no --target-dsn, --admin-dsn or --allow-cleanroom-production")
    environ = os.environ if environ is None else environ
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import hosted_data_load_io as io_
    from olaudit.target import TargetError

    try:
        admin = io_.hosted_target(io_.REPO_ROOT, environ, Path(args.target_file))
    except TargetError as exc:
        raise Refused(f"hosted target: {io_.redact(str(exc))}") from None
    _SECRETS.extend(io_.target_secrets(admin))
    if admin.mode != "hosted":
        raise Refused("--hosted-target resolves only a hosted target (the local scratch switch is not taken)")
    if admin.database != HOSTED_DATABASE:
        raise Refused(f"the hosted target must name the {HOSTED_DATABASE!r} database")
    password = environ.get(RUNTIME_PASSWORD_ENV, "")
    if not password:
        raise Refused(f"{RUNTIME_PASSWORD_ENV} is not set; the {RUNTIME_ROLE} password is read only from it")
    runtime = dataclasses.replace(admin, user=f"{RUNTIME_ROLE}.{admin.project_ref}", password=password)
    _SECRETS.extend(io_.target_secrets(runtime))
    target_info, admin_info = io_.conninfo_for(runtime), io_.conninfo_for(admin)
    args.target_dsn, args.admin_dsn, args.hosted = target_info, admin_info, True


def check_targets(args: argparse.Namespace, *, admin: bool, writes: bool) -> str | None:
    """Every target guard of a subcommand; returns the write mode (`hosted` when hosted)."""
    if getattr(args, "hosted_target", False):
        resolve_hosted(args)
        return "hosted"
    if (getattr(args, "authorize_hosted_connection", False)
            or getattr(args, "authorize_supavisor_session_route", False)):
        raise Refused("the --authorize-* flags only go with --hosted-target")
    if not args.target_dsn or (admin and not args.admin_dsn):
        raise Refused("--target-dsn" + (" and --admin-dsn are" if admin else " is") + " required")
    check_local_dsn(args.target_dsn, "--target-dsn")
    if admin:
        check_local_dsn(args.admin_dsn, "--admin-dsn")
        check_pair(args.target_dsn, args.admin_dsn)
    return writable_mode(args.target_dsn, args.allow_cleanroom_production) if writes else None


def check_local_dsn(dsn: str, flag: str) -> None:
    """A literal loopback IP and nothing a hosted provider would need, or `Refused`."""
    if not is_loopback_dsn(dsn):
        raise Refused(f"{flag} must name a literal loopback IP address; hosted and named hosts are refused "
                      "(a hosted project takes --hosted-target instead)")
    try:
        assert_v2_target_is_local(dsn)
    except V2TargetRefused as exc:
        raise Refused(f"{flag}: {exc}") from None
    if not dbname(dsn):
        raise Refused(f"{flag} names no database")


def check_pair(target_dsn: str, admin_dsn: str) -> None:
    t, a = urlsplit(target_dsn), urlsplit(admin_dsn)
    if (t.hostname, t.port, dbname(target_dsn)) != (a.hostname, a.port, dbname(admin_dsn)):
        raise Refused("--target-dsn and --admin-dsn must name the same host, port and database")


def writable_mode(target_dsn: str, allow_cleanroom: bool) -> str:
    """`disposable` or `cleanroom`, from the database name; anything else is refused."""
    name = dbname(target_dsn)
    if allow_cleanroom:
        if name != CLEANROOM_DB:
            raise Refused(f"--allow-cleanroom-production writes only into {CLEANROOM_DB!r}, not {name!r}")
        return "cleanroom"
    if not DISPOSABLE.match(name):
        raise Refused(f"writes go only into a disposable origenlab_test_<8 hex> database, not {name!r}; "
                      f"the clean room needs --allow-cleanroom-production")
    return "disposable"


# ------------------------------------------------------------------ output (outside the repository)

def _git_checkout(path: Path) -> bool:
    """Whether `path` or any ancestor holds a `.git` (a repository or a worktree)."""
    return any((p / ".git").exists() for p in (path, *path.parents))


def prepare_out(out: Path) -> Path:
    """The output directory, created 0700 — never inside this or any other git checkout."""
    resolved = Path(out).expanduser().resolve()
    repo = REPO.resolve()
    if resolved == repo or repo in resolved.parents or _git_checkout(resolved):
        raise Refused(f"--out {resolved.name!r} is inside a git checkout; reports and plans hold private data")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    return resolved


def write_new(directory: Path, name: str, data: bytes) -> Path:
    path = directory / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Refused(f"{path.name} exists in --out; every plan is written to a new place") from None
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def write_report(directory: Path, name: str, value: Any) -> Path:
    """`name`, or `name-2`, `name-3`… — a report is never overwritten."""
    stem, suffix = name.rsplit(".", 1)
    n = 1
    while True:
        candidate = name if n == 1 else f"{stem}-{n}.{suffix}"
        try:
            return write_new(directory, candidate, (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True,
                                                               default=str) + "\n").encode("utf-8"))
        except Refused:
            n += 1


# ------------------------------------------------------------------ database

def _connect(dsn: str, **kwargs: Any) -> Any:
    import psycopg

    if "application_name" not in dsn:  # a hosted conninfo already names itself
        kwargs.setdefault("application_name", "catalog_import")
    return psycopg.connect(dsn, **kwargs)


def _assert_reached(cur: Any, dsn: str, mode: str | None) -> None:
    """Ask the server which database this is; a name parsed from a string is not evidence."""
    reached = cur.execute("select current_database()").fetchone()[0]
    if reached != dbname(dsn):
        raise Refused(f"connected to {reached!r}, not {dbname(dsn)!r}")
    if mode == "disposable" and not DISPOSABLE.match(reached):
        raise Refused(f"{reached!r} is not a disposable database")
    if mode == "cleanroom" and reached != CLEANROOM_DB:
        raise Refused(f"{reached!r} is not the clean room")


_RUNTIME_PROBE = """
select current_user::text, session_user::text,
       r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication,
       array(select m.rolname::text from pg_roles m
              where m.oid <> r.oid and pg_has_role(current_user, m.oid, 'MEMBER')
                and (m.rolname = any(%s::text[]) or m.rolsuper or m.rolbypassrls
                     or m.rolcreaterole or m.rolcreatedb or m.rolreplication)
              order by 1)
  from pg_roles r where r.rolname = current_user
"""


def assert_runtime_login(cur: Any) -> None:
    """`--target-dsn` is the restricted runtime login, as `v2/remote_database.py` proves it (7fab57eb).

    Session and current user `origenlab_api`, no elevated attribute, and no membership in the
    owner, migrator or `postgres` roles or in any role holding an elevated attribute — otherwise
    the grants and RLS the import is meant to run under would not be the ones it runs under.
    """
    row = cur.execute(_RUNTIME_PROBE, (list(PRIVILEGED_ROLES),)).fetchone()
    if row is None:
        raise Refused("--target-dsn: the session role is not visible in pg_roles")
    current, session, *attributes, memberships = row
    if current != RUNTIME_ROLE or session != RUNTIME_ROLE:
        raise Refused(f"--target-dsn must log in as {RUNTIME_ROLE}, not {session!r} (acting as {current!r})")
    held = [name for name, value in zip(("SUPERUSER", "BYPASSRLS", "CREATEROLE", "CREATEDB", "REPLICATION"),
                                        attributes, strict=True) if value]
    if held:
        raise Refused(f"--target-dsn: {RUNTIME_ROLE} holds {', '.join(held)}")
    if memberships:
        raise Refused(f"--target-dsn: {RUNTIME_ROLE} is a member of {', '.join(memberships)}")


def read_operator(admin_dsn: str, email: str | None, mode: str, operator_id: str | None = None) -> OperatorIdentity:
    """The operator recorded as actor and creator: registered, active, and allowed to write the catalog.

    Found by email, or by id for a shared-login profile operator, which has no email."""
    with _connect(admin_dsn, options="-c default_transaction_read_only=on") as conn:
        cur = conn.cursor()
        _assert_reached(cur, admin_dsn, mode)
        cur.execute("set local role origenlab_owner")
        if operator_id:
            row = cur.execute("select id::text, email_norm, display_name, role, status from platform.operator "
                              "where id = %s::uuid", (operator_id,)).fetchone()
        else:
            row = cur.execute("select id::text, email_norm, display_name, role, status from platform.operator "
                              "where email_norm = %s", ((email or "").strip().lower(),)).fetchone()
        conn.rollback()
    if row is None:
        raise Refused("--operator-email / --operator-id names no registered operator")
    identity = OperatorIdentity(operator_id=row[0], email_norm=row[1], display_name=row[2], role=row[3],
                                status=row[4])
    if identity.status != "active" or identity.role not in WRITING_ROLES:
        raise Refused(f"the operator must be active with role {' or '.join(WRITING_ROLES)}")
    return identity


def record_manifest(admin_dsn: str, plan: importing.Plan, plan_sha: str, mode: str) -> tuple[str, bool, set[str]]:
    """The manifest row for this plan (created if absent), as the owner, in its own transaction.

    The same transaction records the plan's other evidence records the runtime role may not insert
    (`_actions.record_owner_evidence`: the quote documents), each naming the manifest; it returns
    the keys of those it created.
    """
    dedupe = importing.manifest_dedupe_key(plan_sha)
    payload = importing.canonical_json(importing.manifest_payload(plan, plan_sha))
    with _connect(admin_dsn) as conn:
        cur = conn.cursor()
        _assert_reached(cur, admin_dsn, mode)
        cur.execute("set local role origenlab_owner")
        row = cur.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload, payload_sha256, review_status) "
            "values ('migration_manifest', %s, %s::jsonb, %s, 'reviewed') "
            "on conflict (dedupe_key) do nothing returning id::text", (dedupe, payload, plan_sha)).fetchone()
        created = row is not None
        if row is None:
            row = cur.execute("select id::text from evidence.source_record where dedupe_key = %s and "
                              "kind = 'migration_manifest' and payload->>'plan_sha256' = %s",
                              (dedupe, plan_sha)).fetchone()
            if row is None:
                raise Refused(f"{dedupe} exists but is not this plan's manifest")
        evidence_created = _actions.record_owner_evidence(cur, plan, row[0])
        conn.commit()
    return row[0], created, evidence_created


def find_manifest(cur: Any, plan_sha: str) -> str | None:
    row = cur.execute("select id::text from evidence.source_record where dedupe_key = %s and "
                      "kind = 'migration_manifest'", (importing.manifest_dedupe_key(plan_sha),)).fetchone()
    return None if row is None else row[0]


# ------------------------------------------------------------------ the subcommands

def _load(args: argparse.Namespace, importer: str) -> tuple[importing.Plan, str]:
    data = Path(args.plan).read_bytes()  # an OSError is reported by basename in main()
    try:
        plan = importing.load_plan(data, args.plan_sha256, importer=importer)
        importing.refuse_labdelivery_in_plan(plan)
    except importing.PlanRefused as exc:
        raise Refused(str(exc)) from None
    except LabdeliveryRefused as exc:
        raise Refused(str(exc)) from None
    return plan, args.plan_sha256


def _counts(results: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """Outcomes per action: inserted, already_present, present_different, kept_later_value."""
    by_action: dict[str, dict[str, int]] = {}
    for action, outcome in results:
        per = by_action.setdefault(action, {})
        per[outcome] = per.get(outcome, 0) + 1
    total = lambda outcome: sum(v.get(outcome, 0) for v in by_action.values())  # noqa: E731
    return {"inserted": total("inserted"), "already_present": total("already_present"),
            "present_different": total("present_different"), "kept_later_value": total("kept_later_value"),
            "by_action": by_action}


def cmd_apply(args: argparse.Namespace, importer: str, print_keys: bool) -> int:
    mode = check_targets(args, admin=True, writes=True)
    out = prepare_out(args.out)
    plan, plan_sha = _load(args, importer)
    if bool(args.operator_email) == bool(args.operator_id):
        raise Refused("give exactly one of --operator-email and --operator-id")
    if args.operator_id and not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                                             args.operator_id):
        raise Refused("--operator-id is not a UUID")
    operator = read_operator(args.admin_dsn, args.operator_email, mode, args.operator_id)

    # Everything the apply would refuse, found before the manifest is written.
    with _connect(args.target_dsn, options="-c default_transaction_read_only=on") as conn:
        cur = conn.cursor()
        _assert_reached(cur, args.target_dsn, mode)
        assert_runtime_login(cur)
        _actions.preflight(cur, plan)
        conn.rollback()

    manifest_id, manifest_created, evidence_created = record_manifest(args.admin_dsn, plan, plan_sha, mode)
    started = datetime.now(UTC).isoformat()
    results: list[tuple[str, str]] = []
    keys: list[dict[str, str]] = []
    try:
        with _connect(args.target_dsn) as conn:
            cur = conn.cursor()
            _assert_reached(cur, args.target_dsn, mode)
            assert_runtime_login(cur)
            cur.execute("set local statement_timeout = '30min'")
            cur.execute("set local lock_timeout = '30s'")
            cur.execute("select pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            ctx = _actions.Context(cur=cur, operator=operator, manifest_id=manifest_id,
                                   evidence_created=evidence_created)
            for item in plan["items"]:
                outcome = _actions.apply_item(ctx, item)
                results.append((item["action"], outcome))
                keys.append({"key": item["key"], "outcome": outcome})
            events = ctx.events + _actions.record_manifest_event(ctx, plan, plan_sha)
            conn.commit()
    except (Refused, _actions.ImportRefused) as exc:
        # The manifest committed before this transaction: say so, and say how it is reused or removed.
        write_report(out, "apply-refused.json", {"refused": str(exc), "plan_sha256": plan_sha,
                                                 "manifest_source_record_id": manifest_id, "manifest_kept": True,
                                                 "exit_code": EXIT_REFUSED})
        print(f"refused (catalog rows rolled back; manifest {manifest_id} stays — a rerun reuses it, "
              f"rollback removes it): {redact(str(exc))}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # noqa: BLE001 - reported by class and SQLSTATE only, never by message
        sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None) or getattr(exc, "sqlstate", None)
        constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
        write_report(out, "apply-failed.json", {"error": type(exc).__name__, "sqlstate": sqlstate,
                                                "constraint": constraint, "plan_sha256": plan_sha,
                                                "manifest_source_record_id": manifest_id, "manifest_kept": True,
                                                "exit_code": EXIT_APPLY_FAILED})
        print(f"apply failed; catalog rows rolled back, manifest {manifest_id} stays (a rerun reuses it, "
              f"rollback removes it): {type(exc).__name__} sqlstate={sqlstate} constraint={constraint}",
              file=sys.stderr)
        return EXIT_APPLY_FAILED

    report = {"importer": importer, "plan_sha256": plan_sha, "target_database": dbname(args.target_dsn),
              "mode": mode, "operator_id": operator.operator_id, "manifest_source_record_id": manifest_id,
              "manifest_created": manifest_created, "started_at": started,
              "finished_at": datetime.now(UTC).isoformat(), "events_appended": events, **_counts(results)}
    write_report(out, "apply-report.json", report)
    summary = {k: report[k] for k in ("importer", "plan_sha256", "inserted", "already_present", "by_action",
                                      "events_appended")}
    if print_keys:
        summary["items"] = keys
    say(summary)
    return EXIT_OK


def _verify(cur: Any, plan: importing.Plan, plan_sha: str) -> list[dict[str, Any]]:
    manifest_id = find_manifest(cur, plan_sha)
    ctx = _actions.Context(cur=cur, operator=None, manifest_id=manifest_id)
    return [_actions.verify_item(ctx, item) for item in plan["items"]]


def _status_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def cmd_verify(args: argparse.Namespace, importer: str, print_keys: bool) -> int:
    check_targets(args, admin=False, writes=False)
    out = prepare_out(args.out)
    plan, plan_sha = _load(args, importer)
    with _connect(args.target_dsn, options="-c default_transaction_read_only=on") as conn:
        cur = conn.cursor()
        _assert_reached(cur, args.target_dsn, None)
        rows = _verify(cur, plan, plan_sha)
        conn.rollback()
    counts = _status_counts(rows)
    mismatched = [{"action": r["action"], "key": r["key"], "status": r["status"], "fields": r["diffs"]}
                  for r in rows if r["status"] in importing.MISMATCH_STATUSES]
    noted = [{"action": r["action"], "key": r["key"], "status": r["status"], "fields": r["diffs"]}
             for r in rows if r["status"] not in importing.MISMATCH_STATUSES and r["status"] != "present"]
    write_report(out, "verify-report.json", {"importer": importer, "plan_sha256": plan_sha,
                                             "target_database": dbname(args.target_dsn), "counts": counts,
                                             "mismatched": mismatched, "not_written_by_this_plan": noted,
                                             "checked_at": datetime.now(UTC).isoformat()})
    summary: dict[str, Any] = {"importer": importer, "plan_sha256": plan_sha, "counts": counts,
                               "mismatched_keys": [m["key"] for m in mismatched][:50]}
    if print_keys:
        summary["items"] = [{"key": r["key"], "status": r["status"]} for r in rows]
    say(summary)
    return EXIT_OK if not mismatched else EXIT_MISMATCH


def cmd_rollback(args: argparse.Namespace, importer: str, print_keys: bool) -> int:
    if not args.confirm_delete_loaded_rows:
        raise Refused("rollback deletes the rows this plan loaded; it needs --confirm-delete-loaded-rows")
    mode = check_targets(args, admin=True, writes=True)
    out = prepare_out(args.out)
    plan, plan_sha = _load(args, importer)

    base = {"importer": importer, "plan_sha256": plan_sha, "target_database": dbname(args.admin_dsn), "mode": mode}
    try:
        with _connect(args.admin_dsn) as conn:
            cur = conn.cursor()
            _assert_reached(cur, args.admin_dsn, mode)
            # The append-only and never-deleted triggers exist for the runtime roles; replica mode
            # (set as the login, which may; the owner role may not) is what lets a rollback remove
            # the rows it loaded. It also silences foreign keys, so the checks below are ours.
            cur.execute("set local session_replication_role = replica")
            cur.execute("set local role origenlab_owner")
            cur.execute("set local lock_timeout = '30s'")
            cur.execute("select pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            manifest_id = find_manifest(cur, plan_sha)
            if manifest_id is None:
                conn.rollback()
                write_report(out, "rollback-report.json", {**base, "refused": "no manifest for this plan"})
                return refuse("no manifest for this plan in the target; nothing to roll back")
            owned = _actions.collect_owned(cur, manifest_id)
            verified = _verify(cur, plan, plan_sha)
            blockers = _actions.rollback_blockers(cur, owned, verified)
            if blockers:
                conn.rollback()
                write_report(out, "rollback-report.json", {**base, "manifest_source_record_id": manifest_id,
                                                           "refused": "rows changed or referenced since apply",
                                                           "blockers": blockers})
                for b in blockers[:50]:
                    print(f"blocked: {b['table']} {b['id']}: {b['reason']}", file=sys.stderr)
                return refuse(f"{len(blockers)} loaded row(s) changed or are referenced since the apply")
            deleted = _actions.delete_owned(cur, owned)
            dangling = _actions.dangling_references(cur, owned)
            if dangling:
                raise _actions.ImportRefused(f"{len(dangling)} reference(s) would dangle after the delete")
            conn.commit()
    except _actions.ImportRefused as exc:
        write_report(out, "rollback-report.json", {**base, "refused": str(exc)})
        return refuse(str(exc))
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - reported by class and SQLSTATE only
        sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None)
        print(f"rollback failed and was rolled back: {type(exc).__name__} sqlstate={sqlstate}", file=sys.stderr)
        return EXIT_APPLY_FAILED
    report = {**base, "manifest_source_record_id": manifest_id, "deleted": deleted,
              "finished_at": datetime.now(UTC).isoformat()}
    write_report(out, "rollback-report.json", report)
    say({"importer": importer, "plan_sha256": plan_sha, "deleted": deleted})
    return EXIT_OK


# ------------------------------------------------------------------ the CLI

def _plan_and_write(args: argparse.Namespace, importer: str, make_plan: Callable[[argparse.Namespace],
                    importing.Plan], print_keys: bool) -> int:
    out = prepare_out(args.out)
    if (out / "plan.json").exists():
        raise Refused("plan.json exists in --out; every plan is written to a new place")
    try:
        plan = make_plan(args)
        importing.refuse_labdelivery_in_plan(plan)
    except LabdeliveryRefused as exc:
        raise Refused(str(exc)) from None
    except OSError as exc:
        raise Refused(f"cannot read {_basename(exc)}: {exc.strerror or type(exc).__name__}") from None
    except ValueError as exc:
        raise Refused(f"cannot plan: {exc}") from None
    data = importing.plan_bytes(plan)
    path = write_new(out, "plan.json", data)
    summary: dict[str, Any] = {"importer": importer, "plan": path.name, "plan_sha256": importing.sha256_bytes(data),
                               "counts": plan["counts"]}
    if print_keys:
        summary["keys"] = [i["key"] for i in plan["items"]]
    say(summary)
    return EXIT_OK


def main(argv: list[str] | None, *, importer: str, description: str,
         add_plan_arguments: Callable[[argparse.ArgumentParser], None],
         make_plan: Callable[[argparse.Namespace], importing.Plan], print_keys: bool = False) -> int:
    ap = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="read the inputs and write plan.json (no database)")
    p.add_argument("--out", type=Path, required=True, help="a directory outside the repository")
    add_plan_arguments(p)

    def common(p: argparse.ArgumentParser, *, admin: bool) -> None:
        p.add_argument("--target-dsn", help="the origenlab_api login of a loopback database")
        if admin:
            p.add_argument("--admin-dsn", help="the owner-capable maintenance login of the same database")
        p.add_argument("--hosted-target", action="store_true",
                       help="the hosted project of the target file instead of a DSN (see the module docstring)")
        p.add_argument("--target-file", default="supabase/.audit/hosted_target.env", help=argparse.SUPPRESS)
        p.add_argument("--authorize-hosted-connection", action="store_true")
        p.add_argument("--authorize-supavisor-session-route", action="store_true")
        p.add_argument("--plan", type=Path, required=True)
        p.add_argument("--plan-sha256", required=True)
        p.add_argument("--out", type=Path, required=True, help="a directory outside the repository")

    p = sub.add_parser("apply", help="load the plan in one transaction as origenlab_api")
    common(p, admin=True)
    p.add_argument("--operator-email", help="the operator recorded as actor and creator")
    p.add_argument("--operator-id", help="the operator's UUID instead (a shared-login profile has no email)")
    p.add_argument("--allow-cleanroom-production", action="store_true",
                   help=f"write into {CLEANROOM_DB} (loopback) instead of a disposable database")

    p = sub.add_parser("verify", help="re-read every plan item: present / missing / different (exit 13)")
    common(p, admin=False)

    p = sub.add_parser("rollback", help="delete exactly the rows this plan's manifest loaded")
    common(p, admin=True)
    p.add_argument("--confirm-delete-loaded-rows", action="store_true")
    p.add_argument("--allow-cleanroom-production", action="store_true")

    args = ap.parse_args(argv)
    import psycopg

    try:
        if args.command == "plan":
            return _plan_and_write(args, importer, make_plan, print_keys)
        if args.command == "apply":
            return cmd_apply(args, importer, print_keys)
        if args.command == "verify":
            return cmd_verify(args, importer, print_keys)
        return cmd_rollback(args, importer, print_keys)
    except (Refused, _actions.ImportRefused) as exc:
        return refuse(str(exc))
    except psycopg.Error as exc:
        # A connection or query outside the apply/rollback transactions (operator, manifest,
        # preflight, verify): nothing of this step was written. Class only — a server message can
        # carry a DSN, a role or a value.
        sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None)
        return refuse(f"database error before any catalog write: {type(exc).__name__} sqlstate={sqlstate}")
    except OSError as exc:
        return refuse(f"cannot read {_basename(exc)}: {exc.strerror or type(exc).__name__}")
