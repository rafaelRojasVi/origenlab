"""The guards that stand between the quotation CRM import and the persistent `origenlab_clean`.

A script-side module (it runs `docker`, `crontab` and reads `/proc`, which the API source may
never do), imported by its sibling `scripts/quote_crm_import.py`.

`scripts/quote_crm_import.py --apply` writes only into a disposable `origenlab_test_<8 hex>`
unless it is also given `--allow-cleanroom-production`. That flag does not relax anything: it
swaps the disposable-name check for this module's preflight, which is stricter, and refuses on
the first thing that does not match. Without `--apply` the same preflight runs read-only and
reports — the dry run the owner reads before authorising the write.

What the preflight proves, from the database and the host, never from a command's response:

* the target and the admin DSN name exactly `origenlab_clean`, on one loopback host and port,
  and the server behind that port is the clean-room container (its `system_identifier`);
* migration `20260925200000` is recorded and its column exists;
* the operator the confirmations name is registered, `active` and `admin` — production never
  registers one;
* every input the plan is computed from hashes to the value the owner approved, the plan too;
* nobody else can write: no other session on the database, no local process or crontab entry
  that names it, no second importer holding the import's advisory lock;
* every `qimport:` receipt already in the database belongs to this plan and completed (so a
  rerun resumes, and a foreign or half-failed import refuses);
* `pg_dump` in the clean-room container works, so a backup can be taken before the first write.

The facts are read by `read_database_facts` / `scan_processes` / `read_crontab` and judged by
`evaluate`, which is pure so every refusal has a test that needs no database.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from origenlab_api.v2 import quote_crm_import_plan as qp

PRODUCTION_DB = "origenlab_clean"
REQUIRED_MIGRATION = "20260925200000"
CONTAINER = "origenlab_dev_db"
CONTAINER_SUPERUSER = "postgres"
#: `pg_try_advisory_lock` key every quotation import holds for its whole apply.
ADVISORY_LOCK_KEY = int.from_bytes(hashlib.sha256(b"origenlab:quote_crm_import").digest()[:8], "big", signed=True)
#: Every input the plan is computed from, plus the policy the planner embeds in code. The owner
#: supplies an expected hash for each, and for nothing else.
REQUIRED_INPUTS = ("decisions.jsonl", "quote_document_identity.json", "evidence_source_records.json",
                   "blocking_report.csv", "organization_confirmations.jsonl", "policy")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NAMES_DB = re.compile(rb"origenlab_clean(?![A-Za-z0-9_])")
_SECRET_IN_URI = re.compile(r"(://[^:/@\s]+):[^@\s]*@")

EXIT_PREFLIGHT_REFUSED = 11
EXIT_BACKUP_FAILED = 12
EXIT_POST_APPLY_MISMATCH = 10


class ProductionRefused(Exception):
    """A production guard failed before any write."""


def redact(text: str) -> str:
    return _SECRET_IN_URI.sub(r"\1:***@", text)


# ─── arguments ─────────────────────────────────────────────────────────────────────────────


def parse_expected_inputs(pairs: Iterable[str]) -> dict[str, str]:
    """`NAME=<sha256>` for exactly the names in REQUIRED_INPUTS — none missing, none extra, none twice."""
    out: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not _HEX64.match(value):
            raise ProductionRefused(f"--expect-input-sha256 {pair!r}: want NAME=<64 lowercase hex>")
        if name in out:
            raise ProductionRefused(f"--expect-input-sha256 names {name!r} twice")
        out[name] = value
    missing = sorted(set(REQUIRED_INPUTS) - set(out))
    extra = sorted(set(out) - set(REQUIRED_INPUTS))
    if missing or extra:
        raise ProductionRefused(f"--expect-input-sha256 must name exactly {list(REQUIRED_INPUTS)}; "
                                f"missing {missing}, unknown {extra}")
    return out


def policy_fingerprint(excluded_documents: Mapping[str, Iterable[str]]) -> str:
    """The planner's code-embedded policy: its version, evidence scope and excluded documents."""
    return qp.sha256_text(qp.canonical_json({
        "plan_version": qp.PLAN_VERSION, "key_prefix": qp.KEY_PREFIX, "evidence_scope": qp.EVIDENCE_SCOPE_READY,
        "excluded_documents": {k: sorted(v) for k, v in sorted(excluded_documents.items())},
    }))


def dsn_parts(dsn: str) -> tuple[str, int | None, str]:
    parts = urlsplit(dsn)
    return parts.hostname or "", parts.port, parts.path.lstrip("/").split("?", 1)[0]


# ─── facts ─────────────────────────────────────────────────────────────────────────────────


def read_database_facts(admin_dsn: str, operator_email: str) -> dict[str, Any]:
    """Everything the preflight needs from the database, in one read-only transaction."""
    import psycopg

    email = operator_email.strip().lower()
    with psycopg.connect(admin_dsn, options="-c default_transaction_read_only=on") as conn:
        conn.read_only = True
        cur = conn.cursor()
        if cur.execute("show transaction_read_only").fetchone()[0] != "on":
            raise ProductionRefused("the preflight connection is not read-only; nothing read")
        facts: dict[str, Any] = {
            "current_database": cur.execute("select current_database()").fetchone()[0],
            "system_identifier": str(cur.execute("select system_identifier from pg_control_system()").fetchone()[0]),
            "server_version": cur.execute("show server_version").fetchone()[0],
        }
        # A database migrated without a ledger (every disposable test database) records nothing.
        has_ledger = cur.execute("select to_regclass('supabase_migrations.schema_migrations') is not null").fetchone()[0]
        facts["migrations"] = [r[0] for r in cur.execute(
            "select version from supabase_migrations.schema_migrations order by version")] if has_ledger else []
        facts["number_origin_column"] = cur.execute(
            "select count(*) from information_schema.columns where table_schema = 'crm' and table_name = 'quote' "
            "and column_name = 'number_origin'").fetchone()[0] == 1
        op = cur.execute("select id::text, role, status, display_name from platform.operator where email_norm = %s",
                         (email,)).fetchone()
        facts["operator"] = None if op is None else {"id": op[0], "role": op[1], "status": op[2], "display_name": op[3]}
        facts["other_sessions"] = [
            {"pid": r[0], "user": r[1], "application": r[2], "client": r[3], "state": r[4]}
            for r in cur.execute(
                "select pid, usename, application_name, client_addr::text, state from pg_stat_activity "
                "where datname = current_database() and pid <> pg_backend_pid() and backend_type = 'client backend' "
                "order by pid")]
        got = cur.execute("select pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,)).fetchone()[0]
        if got:
            cur.execute("select pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        facts["advisory_lock_free"] = bool(got)
        facts["qimport_receipts"] = {r[0]: {"status": r[1], "operator_id": r[2]} for r in cur.execute(
            "select idempotency_key, status, operator_id::text from platform.command_receipt "
            "where idempotency_key like 'qimport:%'")}
        facts["domain_event_max_position"] = cur.execute(
            "select coalesce(max(stream_position), 0) from crm.domain_event").fetchone()[0]
        conn.rollback()
    return facts


def fingerprint(admin_dsn: str) -> dict[str, Any]:
    """Row count and content md5 of every base table outside the system schemas, read-only."""
    import psycopg
    from psycopg import sql

    with psycopg.connect(admin_dsn, options="-c default_transaction_read_only=on") as conn:
        conn.read_only = True
        cur = conn.cursor()
        cur.execute("set transaction isolation level repeatable read")
        tables = [(s, t) for s, t in cur.execute(
            "select table_schema, table_name from information_schema.tables where table_type = 'BASE TABLE' "
            "and table_schema not in ('pg_catalog', 'information_schema') and table_schema not like 'pg\\_%' "
            "order by table_schema, table_name")]
        out: dict[str, dict[str, Any]] = {}
        for schema, table in tables:
            n, digest = cur.execute(sql.SQL(
                "select count(*), coalesce(md5(string_agg(t::text, chr(10) order by t::text)), '-') from {}.{} t"
            ).format(sql.Identifier(schema), sql.Identifier(table))).fetchone()
            out[f"{schema}.{table}"] = {"rows": n, "md5": digest}
        conn.rollback()
    lines = "\n".join(f"{k}|{v['rows']}|{v['md5']}" for k, v in out.items())
    return {"tables": out, "sha256": hashlib.sha256(lines.encode()).hexdigest(),
            "taken_at": datetime.now(UTC).isoformat()}


def fingerprint_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    b, a = before["tables"], after["tables"]
    return {k: {"rows_before": (b.get(k) or {}).get("rows"), "rows_after": (a.get(k) or {}).get("rows"),
                "delta": (a.get(k) or {}).get("rows", 0) - (b.get(k) or {}).get("rows", 0)}
            for k in sorted(set(a) | set(b)) if b.get(k) != a.get(k)}


def _ancestors(pid: int, proc: Path) -> set[int]:
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            stat = (proc / str(pid) / "stat").read_text()
        except OSError:
            break
        pid = int(stat.rsplit(")", 1)[1].split()[1])
    return seen


def scan_processes(proc: Path = Path("/proc"), self_pid: int | None = None) -> dict[str, Any]:
    """Local processes (other than this one and its ancestors) whose command line or environment
    names `origenlab_clean`. Database backends are left to `other_sessions`. Values are never
    reported — only the variable's name and a redacted command line."""
    exclude = _ancestors(self_pid or os.getpid(), proc)
    hits, unreadable = [], 0
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in exclude:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if cmdline.startswith(b"postgres:") or not cmdline:
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError:
            environ, unreadable = b"", unreadable + 1
        where = ["cmdline"] if _NAMES_DB.search(cmdline) else []
        where += ["env:" + v.split(b"=", 1)[0].decode(errors="replace")
                  for v in environ.split(b"\0") if _NAMES_DB.search(v)]
        if where:
            cmd = redact(cmdline.replace(b"\0", b" ").decode(errors="replace").strip())
            hits.append({"pid": int(entry.name), "cmd": cmd[:200], "where": where})
    return {"processes": sorted(hits, key=lambda h: h["pid"]), "environ_unreadable": unreadable}


def read_crontab(run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    done = run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return {"lines": [], "note": (done.stderr or "").strip()[:200]}
    lines = [ln for ln in done.stdout.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
             and (_NAMES_DB.search(ln.encode()) or "quote_crm_import" in ln)]
    return {"lines": [redact(ln)[:200] for ln in lines]}


def container_facts(port: int | None, run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """The clean-room container: which port it publishes, its cluster identity, and pg_dump."""
    def sh(argv: list[str]) -> tuple[int, str]:
        done = run(argv, capture_output=True, text=True, check=False)
        return done.returncode, (done.stdout or "").strip()

    rc_port, published = sh(["docker", "port", CONTAINER, "5432/tcp"])
    rc_id, ident = sh(["docker", "exec", CONTAINER, "psql", "-U", CONTAINER_SUPERUSER, "-d", PRODUCTION_DB, "-Atc",
                       "select system_identifier from pg_control_system()"])
    rc_dump, dump_version = sh(["docker", "exec", CONTAINER, "pg_dump", "--version"])
    return {"published": published.splitlines() if rc_port == 0 else [],
            "publishes_port": rc_port == 0 and f"127.0.0.1:{port}" in published.splitlines(),
            "system_identifier": ident if rc_id == 0 else None,
            "pg_dump_version": dump_version if rc_dump == 0 else None}


# ─── the judgement (pure) ──────────────────────────────────────────────────────────────────


def evaluate(*, target_dsn: str, admin_dsn: str, plan_sha: str, expected_plan: str, ledger_sha: str,
             expected_ledger: str, input_hashes: Mapping[str, str], expected_inputs: Mapping[str, str],
             db: Mapping[str, Any], container: Mapping[str, Any], processes: Mapping[str, Any],
             crontab: Mapping[str, Any], plan_keys: Iterable[str], confirmation_operator_ids: set[str],
             evidence_unclean: int, scope_violations: int, expected_db: str = PRODUCTION_DB) -> list[dict[str, Any]]:
    """One row per guard; the preflight passes only when every row is ok."""
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    t_host, t_port, t_db = dsn_parts(target_dsn)
    a_host, a_port, a_db = dsn_parts(admin_dsn)
    check("target_dsn_names_production_db", t_db == expected_db, t_db)
    check("admin_dsn_names_production_db", a_db == expected_db, a_db)
    check("dsns_share_host_and_port", (t_host, t_port) == (a_host, a_port), f"{t_host}:{t_port} / {a_host}:{a_port}")
    try:
        loopback = ipaddress.ip_address(t_host).is_loopback
    except ValueError:
        loopback = False
    check("host_is_loopback_literal", loopback, t_host)
    check("connected_database_is_production_db", db.get("current_database") == expected_db, db.get("current_database"))
    check("container_publishes_the_port", bool(container.get("publishes_port")), container.get("published"))
    check("container_is_the_server_behind_the_dsn",
          container.get("system_identifier") is not None
          and container.get("system_identifier") == db.get("system_identifier"),
          {"dsn": db.get("system_identifier"), "container": container.get("system_identifier")})
    check("pg_dump_available", bool(container.get("pg_dump_version")), container.get("pg_dump_version"))

    check("migration_recorded", REQUIRED_MIGRATION in (db.get("migrations") or []),
          {"required": REQUIRED_MIGRATION, "head": (db.get("migrations") or [None])[-1]})
    check("migration_schema_present", bool(db.get("number_origin_column")), "crm.quote.number_origin")

    op = db.get("operator")
    check("operator_registered", op is not None, op and op["id"])
    check("operator_active", bool(op) and op["status"] == "active", op and op["status"])
    check("operator_admin", bool(op) and op["role"] == "admin", op and op["role"])
    check("operator_is_the_confirming_operator", bool(op) and confirmation_operator_ids == {op["id"]},
          {"confirmations": sorted(confirmation_operator_ids), "registered": op and op["id"]})

    check("plan_sha256_matches", plan_sha == expected_plan, {"actual": plan_sha, "expected": expected_plan})
    check("ledger_sha256_matches", ledger_sha == expected_ledger, {"actual": ledger_sha, "expected": expected_ledger})
    for name in REQUIRED_INPUTS:
        check(f"input_sha256_matches:{name}", input_hashes.get(name) == expected_inputs.get(name),
              {"actual": input_hashes.get(name), "expected": expected_inputs.get(name)})

    check("no_other_database_sessions", not db.get("other_sessions"), db.get("other_sessions"))
    check("no_local_process_names_the_db", not processes.get("processes"), processes.get("processes"))
    check("no_crontab_entry_names_the_db", not crontab.get("lines"), crontab.get("lines"))
    check("import_advisory_lock_free", bool(db.get("advisory_lock_free")), ADVISORY_LOCK_KEY)

    keys = set(plan_keys)
    receipts = db.get("qimport_receipts") or {}
    foreign = sorted(k for k in receipts if k not in keys)
    unfinished = sorted(k for k, r in receipts.items() if k in keys and r["status"] != "completed")
    other_operator = sorted(k for k, r in receipts.items() if op and r["operator_id"] != op["id"])
    check("no_foreign_import_receipts", not foreign, foreign[:20])
    check("no_unfinished_import_receipts", not unfinished, unfinished[:20])
    check("import_receipts_belong_to_the_operator", not other_operator, other_operator[:20])

    check("evidence_reconciliation_clean", evidence_unclean == 0, evidence_unclean)
    check("evidence_scope_clean", scope_violations == 0, scope_violations)
    return checks


def refused(checks: Iterable[Mapping[str, Any]]) -> list[str]:
    return [c["check"] for c in checks if not c["ok"]]


# ─── the backup ────────────────────────────────────────────────────────────────────────────


def take_backup(backup_dir: Path, repo: Path, stamp: str, run: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """`pg_dump -Fc` of the production database, verified with `pg_restore --list`, before any write.

    The dump holds personal data, so it never lands inside the (public) repository and is
    written 0600. An existing file is never overwritten."""
    backup_dir = backup_dir.resolve()
    if backup_dir == repo.resolve() or repo.resolve() in backup_dir.parents:
        raise ProductionRefused(f"backup directory {backup_dir} is inside the repository")
    if not backup_dir.is_dir():
        raise ProductionRefused(f"backup directory {backup_dir} does not exist")
    path = backup_dir / f"{PRODUCTION_DB}-pre-quote-import-{stamp}.dump"
    started = datetime.now(UTC).isoformat()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        done = run(["docker", "exec", CONTAINER, "pg_dump", "-U", CONTAINER_SUPERUSER, "-Fc", "-d", PRODUCTION_DB],
                   stdout=out, stderr=subprocess.PIPE, check=False)
    if done.returncode != 0:
        raise ProductionRefused(f"pg_dump failed (rc {done.returncode}): {(done.stderr or b'')[-300:]!r}")
    data = path.read_bytes()
    if not data.startswith(b"PGDMP"):
        raise ProductionRefused(f"{path} is not a custom-format dump")
    listed = run(["docker", "exec", "-i", CONTAINER, "pg_restore", "--list"], input=data, capture_output=True,
                 check=False)
    toc = [ln for ln in (listed.stdout or b"").decode(errors="replace").splitlines() if ln and not ln.startswith(";")]
    if listed.returncode != 0 or not toc:
        raise ProductionRefused(f"pg_restore --list could not read {path}")
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "toc_entries": len(toc), "table_data_entries": sum(1 for ln in toc if " TABLE DATA " in ln),
            "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
            "restore": f"docker exec -i {CONTAINER} pg_restore -U {CONTAINER_SUPERUSER} --clean --if-exists "
                       f"-d {PRODUCTION_DB} < {path}"}


# ─── holding the lock for the apply ────────────────────────────────────────────────────────


class ImportLock:
    """A dedicated session holding the import advisory lock for the whole apply."""

    def __init__(self, admin_dsn: str) -> None:
        self.admin_dsn = admin_dsn
        self.conn: Any = None
        self.pid: int | None = None

    def __enter__(self) -> ImportLock:
        import psycopg

        self.conn = psycopg.connect(self.admin_dsn, autocommit=True, application_name="quote_crm_import:lock")
        self.pid = self.conn.execute("select pg_backend_pid()").fetchone()[0]
        if not self.conn.execute("select pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,)).fetchone()[0]:
            self.conn.close()
            raise ProductionRefused("another quotation import holds the import advisory lock")
        return self

    def other_sessions(self) -> list[dict[str, Any]]:
        return [{"pid": r[0], "user": r[1], "application": r[2], "client": r[3], "state": r[4]}
                for r in self.conn.execute(
                    "select pid, usename, application_name, client_addr::text, state from pg_stat_activity "
                    "where datname = current_database() and pid <> pg_backend_pid() "
                    "and backend_type = 'client backend' order by pid")]

    def __exit__(self, *exc: object) -> None:
        if self.conn is not None and not self.conn.closed:
            self.conn.execute("select pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
            self.conn.close()


def events_after(admin_dsn: str, position: int, receipt_ids: list[str]) -> dict[str, int]:
    """Events appended after `position`: owned by this run's receipts, and not."""
    import psycopg

    with psycopg.connect(admin_dsn, options="-c default_transaction_read_only=on") as conn:
        owned, other = conn.execute(
            "select count(*) filter (where command_receipt_id = any(%s::uuid[])), "
            "count(*) filter (where command_receipt_id is null or command_receipt_id <> all(%s::uuid[])) "
            "from crm.domain_event where stream_position > %s", (receipt_ids, receipt_ids, position)).fetchone()
    return {"owned_by_run": int(owned), "not_owned_by_run": int(other)}


def write_report(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
                    encoding="utf-8")
