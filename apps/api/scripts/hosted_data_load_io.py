"""Facts and bytes for the hosted data load: connections, catalogue facts, dumps, host facts.

Script-side on purpose (runs docker, reads /proc and the crontab). The decisions live in
``origenlab_api.v2.hosted_data_load_plan``; this module only observes and moves.

Two TEST-ONLY switches exist for the end-to-end rehearsal (``tests/test_v2_hosted_data_load_e2e.py``).
Neither can name a database that is not disposable: both accept only ``^origenlab_test_[0-9a-f]{8}$``
(the pattern the scratch script enforces) and silently fall back to the real behaviour otherwise.

* ``OL_HOSTED_LOAD_SOURCE_DB`` replaces the source ``origenlab_clean`` (``source_conninfo``).
* ``OL_HOSTED_LOCAL_SCRATCH`` makes ``hosted_target`` return a loopback target on the dev
  container, but only when the target file's single non-comment key is
  ``OL_HOSTED_LOCAL_SCRATCH`` with that same value. ``read_host_facts`` then reports the TLS and
  route facts as satisfied; the target-file mode and the process/crontab scans stay real.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from origenlab_api.v2 import hosted_data_load_plan as hp

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "supabase" / "audit"))
from olaudit.target import Target  # noqa: E402
from olaudit.target_hosted import ROUTE_SUPAVISOR_SESSION, TARGET_FILE, resolve  # noqa: E402

CONTAINER = "origenlab_dev_db"
SOURCE_DB = "origenlab_clean"
SOURCE_PORT = 54332
DUMP_SCHEMAS = ("crm", "evidence", "outbound", "comms", "catalog", "platform")
_APP_NAME = "origenlab-hosted-data-load"
_DISPOSABLE_DB = re.compile(r"origenlab_test_[0-9a-f]{8}")
_SECRET_IN_URI = re.compile(r"(://[^:/@\s]+):[^@\s]*@")
_PASSWORD_KV = re.compile(r"(password\s*=\s*)\S+", re.I)
_POOLER_HOST = re.compile(r"[a-z0-9.-]*pooler\.supabase\.com|db\.[a-z]{20}\.supabase\.co")


def target_secrets(t: Any) -> tuple[str, ...]:
    """Values of a hosted target that must never be printed. Local targets carry only the
    public dev-container defaults, so nothing is secret there."""
    if t.mode == "local":
        return ()
    vals = (getattr(t, a, None) for a in ("password", "user", "project_ref", "host", "hostaddr"))
    return tuple(v for v in vals if v)


def redact(text: str, extra: Iterable[str] = ()) -> str:
    for v in sorted({e for e in extra if e}, key=len, reverse=True):
        text = text.replace(v, "***")
    text = _SECRET_IN_URI.sub(r"\1:***@", text)
    text = _PASSWORD_KV.sub(r"\1***", text)
    return _POOLER_HOST.sub("<hosted-host>", text)


def scratch_conninfo(db: str, port: int = SOURCE_PORT) -> str:
    return (f"host=127.0.0.1 port={port} dbname={db} user=supabase_admin password=postgres "
            f"application_name={_APP_NAME}")


def source_conninfo(port: int = SOURCE_PORT) -> str:
    override = os.environ.get("OL_HOSTED_LOAD_SOURCE_DB", "")
    return scratch_conninfo(override if _DISPOSABLE_DB.fullmatch(override) else SOURCE_DB, port)


def _local_scratch_target(environ: Mapping[str, str], target_file: Path) -> Target | None:
    """TEST-ONLY: a loopback Target on the dev container, for a disposable database named in the
    environment AND as the only key of the target file. Anything else returns None."""
    name = environ.get("OL_HOSTED_LOCAL_SCRATCH", "")
    if not _DISPOSABLE_DB.fullmatch(name):
        return None
    try:
        lines = [ln.strip() for ln in Path(target_file).read_text().splitlines()]
    except OSError:
        return None
    keys = [ln for ln in lines if ln and not ln.startswith("#")]
    if keys != [f"OL_HOSTED_LOCAL_SCRATCH={name}"]:
        return None
    return Target(mode="local", host="127.0.0.1", hostaddr=None, port=SOURCE_PORT, user="supabase_admin",
                  database=name, sslmode="disable", sslrootcert=None, password="postgres", project_ref="e2e",
                  route=ROUTE_SUPAVISOR_SESSION)


def hosted_target(repo_root: Path, environ: Mapping[str, str], target_file: Path = TARGET_FILE) -> Target:
    local = _local_scratch_target(environ, repo_root / target_file)
    if local is not None:
        return local
    return resolve(repo_root, dict(environ), target_file=target_file,
                   authorized_routes=frozenset({ROUTE_SUPAVISOR_SESSION}))


def conninfo_for(t: Any) -> str:
    if t.mode == "local":
        return scratch_conninfo(t.database) + " sslmode=disable"
    return (f"host={t.host} hostaddr={t.hostaddr} port={t.port} dbname={t.database} user={t.user} "
            f"sslmode={t.sslmode} sslrootcert={t.sslrootcert} password={t.password} "
            f"application_name={_APP_NAME} connect_timeout=20")


def apply_session_settings(cur, local: bool = False) -> None:
    # SET does not take bind parameters (psycopg 3 binds server-side); set_config does.
    for k, v in hp.SESSION_SETTINGS:
        cur.execute("select set_config(%s, %s, %s)", (k, v, local))


def write_sha256_sidecar(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    side = path.with_name(path.name + ".sha256")
    side.write_text(f"{digest}  {path.name}\n")
    side.chmod(0o600)
    return str(side)


def _columns(cur, table: str) -> list[list[str]]:
    cur.execute("""select a.attname, t.typname from pg_attribute a join pg_type t on t.oid = a.atttypid
                   where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped order by a.attnum""",
                (table,))
    return [[n, ty] for n, ty in cur.fetchall()]


def table_facts(cur, table: str, select_sql: str) -> dict:
    columns = _columns(cur, table)
    cur.execute("""select a.attname from pg_index i join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
                   where i.indrelid = %s::regclass and i.indisprimary order by array_position(i.indkey, a.attnum)""",
                (table,))
    pk = [r[0] for r in cur.fetchall()]
    cur.execute(hp.row_hash_sql(select_sql, pk))
    count, digest = cur.fetchone()
    cur.execute("select tgname from pg_trigger where tgrelid = %s::regclass and not tgisinternal order by 1", (table,))
    triggers = [r[0] for r in cur.fetchall()]
    cur.execute("select conname, condeferrable from pg_constraint where conrelid = %s::regclass and contype = 'f' order by 1",
                (table,))
    fks = [[n, bool(d)] for n, d in cur.fetchall()]
    return {"columns": columns, "pk": pk, "count": int(count), "hash": digest, "triggers": triggers, "fks": fks}


def _hash_rows(cur, sql: str) -> str:
    cur.execute(f"select coalesce(md5(string_agg(md5(t::text), '' order by t::text)), '') from ({sql}) t")
    return cur.fetchone()[0]


def roster_hash(cur) -> str:
    return (_hash_rows(cur, "select * from platform.operator")
            + _hash_rows(cur, "select * from platform.operator_profile")
            + _hash_rows(cur, "select * from platform.auth_principal"))


def _ledger(cur) -> list[str]:
    cur.execute("select version from supabase_migrations.schema_migrations order by version")
    return [r[0] for r in cur.fetchall()]


def _operators(cur) -> list[list]:
    cur.execute("select id::text, display_name, role, status, sign_in_kind from platform.operator order by display_name")
    return [list(r) for r in cur.fetchall()]


def _other_sessions(cur, same_role_only: bool) -> list[dict]:
    """Source: every other client session on the database (nobody may write the clean room).
    Target: only other sessions of our own login role (``session_user``; ``current_user`` is
    ``origenlab_owner`` after SET LOCAL ROLE). The Render API's `origenlab_api` pool is always
    connected and only reads; it waits on the apply's locks instead of seeing half a load
    (spec §5 step 4, §6 'no other session as origenlab_migrator')."""
    role_filter = "and usename = session_user" if same_role_only else ""
    cur.execute(f"""select pid, usename, application_name, state from pg_stat_activity
                    where datname = current_database() and pid <> pg_backend_pid()
                      and backend_type = 'client backend' {role_filter}""")
    return [dict(zip(("pid", "user", "app", "state"), r)) for r in cur.fetchall()]


def _sequences(cur) -> dict[str, int]:
    cur.execute("select schemaname||'.'||sequencename, coalesce(last_value, 0) from pg_sequences where schemaname = any(%s)",
                (list(DUMP_SCHEMAS),))
    return {n: int(v) for n, v in cur.fetchall()}


def _rollback(cur) -> None:
    """Roll back; if the body already raised, a dead connection must not mask that error."""
    if sys.exc_info()[0] is None:
        cur.execute("rollback")
        return
    try:
        cur.execute("rollback")
    except psycopg.Error:
        pass


def _count(cur, table: str) -> int:
    cur.execute(sql.SQL("select count(*) from {}").format(sql.Identifier(*table.split(".", 1))))
    return int(cur.fetchone()[0])


def target_counts(cur) -> dict[str, int]:
    """Row count of each of the 16 loaded tables. No transaction control: the caller opens and closes."""
    return {name: _count(cur, name) for name in hp.TABLES}


def business_counts(cur, schemas: Iterable[str] = hp.EMPTY_SCHEMAS) -> dict[str, int]:
    """Row count of every ordinary table in ``schemas`` (the 16 loaded tables included)."""
    cur.execute("""select n.nspname||'.'||c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
                   where c.relkind = 'r' and n.nspname = any(%s) order by 1""", (list(schemas),))
    names = [t for (t,) in cur.fetchall()]
    return {t: _count(cur, t) for t in names}


def _qcols(names: list[str]) -> str:
    return ", ".join('"' + c.replace('"', '""') + '"' for c in names)


def read_source_facts(conn, repo_root: Path, hosted_operator: str) -> dict:
    """``conn`` must be autocommit; one REPEATABLE READ snapshot covers every count and hash."""
    with conn.cursor() as cur:
        cur.execute("begin isolation level repeatable read read only")
        try:
            apply_session_settings(cur, local=True)
            cur.execute("select system_identifier::text from pg_control_system()")
            sysid = cur.fetchone()[0]
            tables = {}
            for name in hp.TABLES:
                cols = [c[0] for c in _columns(cur, name)]
                tables[name] = table_facts(cur, name, hp.remap_select_sql(name, cols, hosted_operator))
            cur.execute("select count(*) from crm.domain_event where payload::text ~ %s", ("|".join(hp.LOCAL_OPERATOR_IDS),))
            payload_rows = cur.fetchone()[0]
            return {
                "system_identifier": sysid, "ledger": _ledger(cur),
                "migrations_on_disk": sorted(p.name.split("_", 1)[0] for p in (repo_root / "supabase" / "migrations").glob("*.sql")),
                "tables": tables, "operators": _operators(cur),
                "other_sessions": _other_sessions(cur, same_role_only=False),
                "sequences": _sequences(cur), "payload_rows_with_local_uuid": int(payload_rows),
            }
        finally:
            _rollback(cur)


def read_target_facts(conn, target: Any, pg_dump_probe: bool) -> dict:
    """``conn`` must be autocommit. Reads as ``origenlab_owner`` (the login role has SET-only membership)."""
    with conn.cursor() as cur:
        cur.execute("begin isolation level repeatable read read only")
        try:
            # The ledger belongs to the login role: origenlab_owner has no access to supabase_migrations.
            ledger = _ledger(cur)
            cur.execute("set local role origenlab_owner")
            apply_session_settings(cur, local=True)
            cur.execute("select current_setting('server_version_num')::int")
            ver = cur.fetchone()[0]
            tables = {}
            for name in hp.TABLES:
                cols = [c[0] for c in _columns(cur, name)]
                tables[name] = table_facts(cur, name, f"select {_qcols(cols)} from {name}")
            counts = business_counts(cur)
            cur.execute("select count(*) from platform.auth_principal")
            principals = cur.fetchone()[0]
            cur.execute("select count(*) from platform.operator_profile")
            profiles = cur.fetchone()[0]
            cur.execute("select count(*) from platform.command_receipt")
            receipts = cur.fetchone()[0]
            cur.execute("select marketing_enabled, transactional_enabled from outbound.send_control")
            sc_row = cur.fetchone() or (None, None)
            cur.execute("select scope, legacy_campaign_key, lifted_at::text from outbound.campaign_block")
            cb_row = cur.fetchone() or (None, None, None)
            cur.execute("select pg_try_advisory_xact_lock(%s)", (hp.ADVISORY_LOCK_KEY,))
            free = cur.fetchone()[0]
            return {
                "project_ref": target.project_ref, "pooler_host": target.host, "server_version_num": int(ver),
                "ledger": ledger, "tables": tables, "all_business_counts": counts,
                "operators": _operators(cur), "principals": int(principals), "profiles": int(profiles),
                "command_receipts": int(receipts),
                "send_control": {
                    "marketing_enabled": None if sc_row[0] is None else bool(sc_row[0]),
                    "transactional_enabled": None if sc_row[1] is None else bool(sc_row[1]),
                    "hash": _hash_rows(cur, "select * from outbound.send_control")},
                "campaign_block": {"scope": cb_row[0], "legacy_campaign_key": cb_row[1], "lifted_at": cb_row[2],
                                   "hash": _hash_rows(cur, "select * from outbound.campaign_block")},
                "roster_hash": roster_hash(cur),
                "advisory_lock_free": bool(free), "other_sessions": _other_sessions(cur, same_role_only=True),
                "sequences": _sequences(cur), "pg_dump_probe_ok": bool(pg_dump_probe),
            }
        finally:
            _rollback(cur)


def _scan_processes(proc: Path) -> list[str]:
    """Host processes naming the source database. Postgres backends (``postgres: ...``) are
    skipped: Docker runs natively here, so the container's backends, including this loader's own
    connection, appear in /proc; database sessions are covered by ``other_sessions``."""
    found: list[str] = []
    for pid in (p for p in os.listdir(proc) if p.isdigit()):
        if int(pid) == os.getpid():
            continue
        try:
            cmd = (proc / pid / "cmdline").read_bytes().replace(b"\0", b" ").strip()
        except OSError:
            continue
        if cmd.startswith(b"postgres:"):
            continue
        if SOURCE_DB.encode() in cmd and b"hosted_data_load" not in cmd:
            found.append(redact(cmd.decode(errors="replace"))[:120])
    return found


def read_host_facts(repo_root: Path, target_file: Path, target: Any, proc: Path = Path("/proc")) -> dict:
    path = repo_root / target_file
    st = path.stat()
    try:
        cron_out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        cron_out = ""
    cron_lines = [ln for ln in cron_out.splitlines() if SOURCE_DB in ln and not ln.lstrip().startswith("#")]
    label = subprocess.run(["docker", "inspect", CONTAINER, "--format", '{{index .Config.Labels "com.origenlab.workdir"}}'],
                           capture_output=True, text=True).stdout.strip()
    facts = {
        "target_file_mode": st.st_mode & 0o777, "target_file_regular": path.is_file(),
        "route": target.route, "port": int(target.port), "sslmode": target.sslmode,
        "ca_exists": Path(target.sslrootcert).is_file(), "processes": _scan_processes(proc), "crontab": cron_lines,
        "container_label_ok": bool(label) and Path(label).resolve() == repo_root.resolve(),
    } if target.mode != "local" else {
        # TEST-ONLY local scratch target: the hosted-only facts hold by construction; the rest stays real.
        "target_file_mode": st.st_mode & 0o777, "target_file_regular": path.is_file(),
        "route": "supavisor-session", "port": 5432, "sslmode": "verify-full", "ca_exists": True,
        "processes": _scan_processes(proc), "crontab": cron_lines, "container_label_ok": True,
    }
    return facts


def _pg_dump_command(target: Any, schema_only: bool) -> tuple[list[str], dict[str, str]]:
    """argv carries variable NAMES only; secrets travel in the returned extra environment."""
    schemas = [f"--schema={s}" for s in DUMP_SCHEMAS]
    if target.mode == "local":  # Task 7 e2e: a disposable database on this same container
        argv = ["docker", "exec", CONTAINER, "pg_dump", "-U", "supabase_admin", "--dbname", target.database,
                "--format=custom", "--role=origenlab_owner", "--no-owner", *schemas]
        extra: dict[str, str] = {}
    else:
        argv = ["docker", "exec", "-e", "PGPASSWORD", "-e", "PGSSLMODE", "-e", "PGSSLROOTCERT", "-e", "PGHOSTADDR",
                CONTAINER, "pg_dump", "--host", target.host, "--port", str(target.port), "--username", target.user,
                "--dbname", target.database, "--format=custom", "--role=origenlab_owner", "--no-owner", *schemas]
        extra = {"PGPASSWORD": target.password, "PGSSLMODE": "verify-full",
                 "PGSSLROOTCERT": "/tmp/hosted-ca.crt", "PGHOSTADDR": target.hostaddr}
    if schema_only:
        argv.append("--schema-only")
    return argv, extra


def pg_dump_target(target: Any, out: Path, *, schema_only: bool = False) -> dict:
    """pg_dump 17 from inside the container (the host's 16 refuses a 17 server)."""
    if target.mode != "local":
        subprocess.run(["docker", "cp", target.sslrootcert, f"{CONTAINER}:/tmp/hosted-ca.crt"], check=True)
    argv, extra = _pg_dump_command(target, schema_only)
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(out, "wb") as fh:
        res = subprocess.run(argv, stdout=fh, stderr=subprocess.PIPE, env={**os.environ, **extra})
    if res.returncode != 0 or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError("pg_dump failed: " + redact(res.stderr.decode(errors="replace"), target_secrets(target))[-400:])
    out.chmod(0o600)
    return {"path": str(out), "sha256": write_sha256_sidecar(out), "bytes": out.stat().st_size}


def set_fks_deferrable(cur, table: str, fk_names: list[str], deferrable: bool) -> None:
    mode = "DEFERRABLE INITIALLY DEFERRED" if deferrable else "NOT DEFERRABLE"
    for name in fk_names:
        cur.execute(sql.SQL("ALTER TABLE {} ALTER CONSTRAINT {} " + mode).format(
            sql.Identifier(*table.split(".", 1)), sql.Identifier(name)))


def set_triggers(cur, table: str, enable: bool) -> None:
    cur.execute(f"ALTER TABLE {table} {'ENABLE' if enable else 'DISABLE'} TRIGGER USER")


def copy_table(src_cur, tgt_cur, table: str, columns: list[str], select_sql: str) -> int:
    """Binary COPY source SELECT -> target table; returns the rows the target reports written."""
    cols = _qcols(columns)
    with src_cur.copy(f"COPY ({select_sql}) TO STDOUT (FORMAT binary)") as out, \
         tgt_cur.copy(f"COPY {table} ({cols}) FROM STDIN (FORMAT binary)") as inp:
        for chunk in out:
            inp.write(chunk)
    return tgt_cur.rowcount


def invariant_names(plan: Mapping) -> list[str]:
    names = [f"rows_match:{t['name']}" for t in plan["tables"]]
    return names + ["payload_hosted_uuid_rows", "payload_local_uuid_rows_zero", "no_foreign_operator_refs",
                    "roster_unchanged", "send_control_unchanged", "campaign_block_unchanged", "send_flags_false",
                    "all_user_triggers_enabled", "no_deferrable_fks", "sequence_set"]


def post_copy_invariants(cur, plan: Mapping, hosted_operator: str) -> list[dict]:
    """Re-hash everything inside the write transaction, before COMMIT."""
    checks: list[dict] = []

    def check(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    for t in plan["tables"]:
        cur.execute(hp.row_hash_sql(f"select {_qcols([c[0] for c in t['columns']])} from {t['name']}", t["pk"]))
        count, digest = cur.fetchone()
        check(f"rows_match:{t['name']}", (int(count), digest) == (t["count"], t["hash"]),
              {"count": int(count), "expected": t["count"]})
    cur.execute("select count(*) from crm.domain_event where payload::text like %s", (f"%{hosted_operator}%",))
    got = cur.fetchone()[0]
    check("payload_hosted_uuid_rows", got == plan["payload_rows_with_local_uuid"],
          {"rows": got, "expected": plan["payload_rows_with_local_uuid"]})
    cur.execute("select count(*) from crm.domain_event where payload::text ~ %s", ("|".join(plan["remap"]["from"]),))
    left = cur.fetchone()[0]
    check("payload_local_uuid_rows_zero", left == 0, left)
    foreign = 0
    for table, cols in hp.OPERATOR_COLUMNS.items():
        for c in cols:
            cur.execute(f'select count(*) from {table} where "{c}" is not null and "{c}" not in (select id from platform.operator)')
            foreign += cur.fetchone()[0]
    check("no_foreign_operator_refs", foreign == 0, foreign)
    check("roster_unchanged", roster_hash(cur) == plan["roster_hash"])
    check("send_control_unchanged", _hash_rows(cur, "select * from outbound.send_control") == plan["send_control_hash"])
    check("campaign_block_unchanged", _hash_rows(cur, "select * from outbound.campaign_block") == plan["campaign_block_hash"])
    cur.execute("select count(*) = 1 and coalesce(bool_and(not marketing_enabled and not transactional_enabled), false) "
                "from outbound.send_control")
    check("send_flags_false", cur.fetchone()[0] is True)
    cur.execute("select count(*) from pg_trigger where not tgisinternal and tgenabled <> 'O' and tgrelid = any(%s::regclass[])",
                (list(hp.TABLES),))
    check("all_user_triggers_enabled", cur.fetchone()[0] == 0)
    cur.execute("select count(*) from pg_constraint where contype = 'f' and condeferrable and conrelid = any(%s::regclass[])",
                (list(hp.TABLES),))
    check("no_deferrable_fks", cur.fetchone()[0] == 0)
    cur.execute(f"select last_value from {hp.SEQUENCE}")
    check("sequence_set", int(cur.fetchone()[0]) == plan["sequence"]["target_last_value"])
    return checks


def classify_target_state(plan: Mapping, observed: Mapping[str, tuple[int, str]]) -> str:
    """``loaded``: every table's (count, hash) equals the plan; ``empty``: every plan table has
    0 rows; anything else is ``partial``. Observed tables outside the plan are ignored."""
    if all(tuple(observed[t["name"]]) == (t["count"], t["hash"]) for t in plan["tables"]):
        return "loaded"
    if all(observed[t["name"]][0] == 0 for t in plan["tables"]):
        return "empty"
    return "partial"


def observed_rows(cur, plan: Mapping) -> dict[str, tuple[int, str]]:
    out = {}
    for t in plan["tables"]:
        cur.execute(hp.row_hash_sql(f"select {_qcols([c[0] for c in t['columns']])} from {t['name']}", t["pk"]))
        count, digest = cur.fetchone()
        out[t["name"]] = (int(count), digest)
    return out


def rows_outside_load(plan: Mapping, counts: Mapping[str, int]) -> dict[str, int]:
    """Tables other than the planned ones whose count is not the pristine one (0, or the singleton's 1)."""
    own = {t["name"] for t in plan["tables"]}
    bad = {n: c for n, c in counts.items() if n not in own and c != hp.SINGLETONS.get(n, 0)}
    bad.update({n: 0 for n in hp.SINGLETONS if n not in counts})
    return bad


def restore_into_scratch(dump: Path, scratch_db: str) -> None:
    """Restore only the 16 loaded tables into a freshly minted scratch database. A fresh scratch
    already carries the seeded singletons, so restoring the whole dump would collide on them; the
    16 table names are unique across the schemas, so bare ``-t`` names are unambiguous.
    ``--disable-triggers`` (superuser in the container) also skips the FK to platform.operator,
    which the scratch does not carry: the drill proves the loaded rows, not the roster."""
    subprocess.run(["docker", "cp", str(dump), f"{CONTAINER}:/tmp/post.dump"], check=True)
    try:
        tables = [arg for t in hp.TABLES for arg in ("-t", t.split(".", 1)[1])]
        res = subprocess.run(["docker", "exec", CONTAINER, "pg_restore", "-U", "supabase_admin", "-d", scratch_db,
                              "--data-only", "--disable-triggers", "--no-owner", "--exit-on-error", *tables,
                              "/tmp/post.dump"], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("pg_restore failed: " + redact(res.stderr)[-400:])
    finally:
        try:
            subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", "/tmp/post.dump"], capture_output=True)
        except OSError:
            pass
