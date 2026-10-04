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
  Against that local target, every statement runs with the HOSTED login's privileges:
  ``connect_target`` logs in as the local superuser and immediately runs
  ``SET SESSION AUTHORIZATION origenlab_migrator`` (NOINHERIT, SET-only membership in
  origenlab_owner, no access to supabase_migrations — exactly the hosted login), and the
  container's pg_dump connects as origenlab_migrator over the container's own loopback. The
  source connection stays the local superuser, as it is in production.
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
#: The role the hosted login authenticates as (Supavisor strips the ``.<project ref>`` suffix).
HOSTED_LOGIN_ROLE = "origenlab_migrator"
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


def connect_target(t: Any) -> psycopg.Connection:
    """The target connection (autocommit: every transaction is an explicit BEGIN/COMMIT/ROLLBACK).

    TEST-ONLY branch: a local scratch target assumes the hosted login right after connecting, so the
    e2e proves every target statement under hosted privileges instead of the local superuser's."""
    conn = psycopg.connect(conninfo_for(t), autocommit=True)
    if t.mode == "local":
        try:
            conn.execute(f"SET SESSION AUTHORIZATION {HOSTED_LOGIN_ROLE}")
        except BaseException:
            conn.close()
            raise
    return conn


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
    """Identity columns only (``hp.ROSTER_IDENTITY_COLUMNS``): a failed PIN must not read as a changed roster."""
    return "".join(_hash_rows(cur, f"select {hp.qcols(cols)} from {table}")
                   for table, cols in hp.ROSTER_IDENTITY_COLUMNS.items())


def _ledger(cur) -> list[str]:
    cur.execute("select version from supabase_migrations.schema_migrations order by version")
    return [r[0] for r in cur.fetchall()]


#: Probed by OID only. Any name resolution through the schema (to_regclass, ::regclass, the text forms
#: of has_*_privilege) RAISES "permission denied for schema supabase_migrations" for a login without
#: USAGE — the hosted origenlab_migrator — and an error would abort the read transaction.
_LEDGER_READABLE = """select case when has_schema_privilege(n.oid, 'usage') then has_table_privilege(c.oid, 'select')
                                 else false end
                      from pg_class c join pg_namespace n on n.oid = c.relnamespace
                      where n.nspname = 'supabase_migrations' and c.relname = 'schema_migrations'"""


def _ledger_or_none(cur) -> list[str] | None:
    """The ledger, or None when it is absent (no row) or unreadable by this login (hosted: platform-owned)."""
    cur.execute(_LEDGER_READABLE)
    row = cur.fetchone()
    if row is None or not row[0]:
        return None
    return _ledger(cur)


# Each query yields rows of text columns; a line is the columns joined with "|". ``n`` = the schemas
# owned by origenlab_owner. No ACLs and no owners: hosted platform grants differ, the audit covers them.
_REL = "from pg_class c join pg_namespace n on n.oid = c.relnamespace"
_IN = "where n.nspname = any(%s)"
_FINGERPRINT_QUERIES: dict[str, str] = {
    "tables": f"""select n.nspname||'.'||c.relname, c.relkind::text, c.relrowsecurity::text, c.relforcerowsecurity::text
                  {_REL} {_IN} and c.relkind in ('r','p','v','m','f')""",
    "columns": f"""select n.nspname||'.'||c.relname, (row_number() over (partition by c.oid order by a.attnum))::text,
                          a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull::text, a.attidentity::text,
                          a.attgenerated::text, coalesce(pg_get_expr(d.adbin, d.adrelid), '')
                   {_REL}
                   join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
                   left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
                   {_IN} and c.relkind in ('r','p','v','m','f')""",
    "constraints": f"""select n.nspname||'.'||c.relname, k.conname, k.contype::text, pg_get_constraintdef(k.oid),
                              k.condeferrable::text, k.condeferred::text
                       {_REL} join pg_constraint k on k.conrelid = c.oid {_IN}""",
    "indexes": f"""select n.nspname||'.'||c.relname, pg_get_indexdef(i.indexrelid)
                   {_REL} join pg_index i on i.indrelid = c.oid {_IN}""",
    "triggers": f"""select n.nspname||'.'||c.relname, pg_get_triggerdef(t.oid), t.tgenabled::text
                    {_REL} join pg_trigger t on t.tgrelid = c.oid and not t.tgisinternal {_IN}""",
    "functions": """select n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')', p.prokind::text,
                           p.prosecdef::text, p.provolatile::text, coalesce(p.proconfig::text, ''), md5(p.prosrc)
                    from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = any(%s)""",
    "policies": f"""select n.nspname||'.'||c.relname, o.polname, o.polcmd::text, o.polpermissive::text,
                           coalesce((select string_agg(case when r = 0 then 'public' else (select rolname from pg_roles where oid = r) end,
                                                       ',' order by 1) from unnest(o.polroles) r), ''),
                           coalesce(pg_get_expr(o.polqual, o.polrelid), ''), coalesce(pg_get_expr(o.polwithcheck, o.polrelid), '')
                    {_REL} join pg_policy o on o.polrelid = c.oid {_IN}""",
    "views": f"""select n.nspname||'.'||c.relname, pg_get_viewdef(c.oid) {_REL} {_IN} and c.relkind in ('v','m')""",
    "sequences": """select n.nspname||'.'||c.relname, format_type(q.seqtypid, null), q.seqincrement::text, q.seqmin::text,
                           q.seqmax::text, q.seqcycle::text
                    from pg_sequence q join pg_class c on c.oid = q.seqrelid join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = any(%s)""",
    "types": """select n.nspname||'.'||t.typname, t.typtype::text,
                       case t.typtype
                         when 'e' then (select string_agg(e.enumlabel, ',' order by e.enumsortorder) from pg_enum e where e.enumtypid = t.oid)
                         when 'd' then format_type(t.typbasetype, t.typtypmod)||' '||t.typnotnull::text||' '||coalesce(pg_get_expr(t.typdefaultbin, 0), '')||' '||
                                       coalesce((select string_agg(k.conname||':'||pg_get_constraintdef(k.oid), ',' order by k.conname)
                                                 from pg_constraint k where k.contypid = t.oid), '')
                         else (select string_agg(a.attname||' '||format_type(a.atttypid, a.atttypmod), ',' order by a.attnum)
                               from pg_attribute a where a.attrelid = t.typrelid and a.attnum > 0 and not a.attisdropped)
                       end
                from pg_type t join pg_namespace n on n.oid = t.typnamespace
                where n.nspname = any(%s)
                  and (t.typtype in ('e','d','r') or (t.typtype = 'c' and exists
                       (select 1 from pg_class c where c.oid = t.typrelid and c.relkind = 'c')))""",
}


def schema_fingerprint(cur) -> dict:
    """What the migrations PRODUCED in every schema owned by ``origenlab_owner``, as one sha256 per
    category (the repo audit's own "object by object" evidence, instead of a version list).
    Cursor-level: no transaction control; ``set local search_path`` lasts to the caller's transaction end."""
    cur.execute("set local search_path = pg_catalog")
    cur.execute("select nspname from pg_namespace where nspowner = 'origenlab_owner'::regrole order by 1")
    schemas = [r[0] for r in cur.fetchall()]
    lines: dict[str, list[str]] = {}
    for name, query in _FINGERPRINT_QUERIES.items():
        cur.execute(query, (schemas,))
        lines[name] = sorted("|".join("" if v is None else str(v) for v in row) for row in cur.fetchall())
    cats = {k: hashlib.sha256("\n".join(v).encode("utf-8")).hexdigest() for k, v in lines.items()}
    return {"schemas": schemas, "categories": cats, "lines": lines}


def fingerprint_diff_lines(source_fp: Mapping, target_fp: Mapping, limit: int = 10) -> dict[str, list[str]]:
    """Per differing category, up to ``limit`` lines: ``-`` only on the source, ``+`` only on the target."""
    out: dict[str, list[str]] = {}
    s_lines, t_lines = source_fp.get("lines", {}), target_fp.get("lines", {})
    for cat in sorted(set(s_lines) | set(t_lines)):
        s, t = set(s_lines.get(cat, ())), set(t_lines.get(cat, ()))
        diff = [f"- {x}" for x in sorted(s - t)] + [f"+ {x}" for x in sorted(t - s)]
        if diff:
            out[cat] = diff[:limit]
    if source_fp.get("schemas") != target_fp.get("schemas"):
        out["schemas"] = [f"- {x}" for x in sorted(set(source_fp.get("schemas", ())) - set(target_fp.get("schemas", ())))][:limit] + \
                         [f"+ {x}" for x in sorted(set(target_fp.get("schemas", ())) - set(source_fp.get("schemas", ())))][:limit]
    return out


def _fp_fields(fp: dict) -> dict:
    return {"schema_fingerprint": {"schemas": fp["schemas"], "categories": fp["categories"]},
            "schema_fingerprint_lines": fp["lines"]}


def _operators(cur) -> list[list]:
    cur.execute("select id::text, display_name, role, status, sign_in_kind from platform.operator order by display_name")
    return [list(r) for r in cur.fetchall()]


def _other_sessions(cur, same_role_only: bool) -> list[dict]:
    """Source: every other client session on the database (nobody may write the clean room).

    Target: other NON-IDLE sessions of our own login role (``usename = session_user``). Must run
    BEFORE ``SET LOCAL ROLE origenlab_owner``: pg_stat_activity shows backend_type/state only for
    sessions whose role the CURRENT user has privileges of, so as the owner the login's sessions read
    NULL and nothing would match. ``idle`` backends are what the Supavisor session pool keeps between
    clients and never refuse; an open transaction (``idle in transaction``) or a running statement
    does. A NULL state or backend_type (hidden details) counts as busy: fail closed. The Render API's
    `origenlab_api` pool is always connected and only reads; it waits on the apply's locks instead of
    seeing half a load (spec §5 step 4, §6 'no other session as origenlab_migrator')."""
    if same_role_only:
        flt = ("coalesce(backend_type, 'client backend') = 'client backend' and usename = session_user "
               "and state is distinct from 'idle'")
    else:
        flt = "backend_type = 'client backend'"
    cur.execute(f"""select pid, usename, application_name, state from pg_stat_activity
                    where datname = current_database() and pid <> pg_backend_pid() and {flt}""")
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
                **_fp_fields(schema_fingerprint(cur)),
            }
        finally:
            _rollback(cur)


def read_target_facts(conn, target: Any, pg_dump_probe: bool) -> dict:
    """``conn`` must be autocommit. Reads as ``origenlab_owner`` (the login role has SET-only membership)."""
    with conn.cursor() as cur:
        cur.execute("begin isolation level repeatable read read only")
        try:
            # The ledger belongs to the login role (origenlab_owner has no access to supabase_migrations) and on
            # hosted it is platform-owned and unreadable even to the login: best effort, None if unreadable.
            # schema_fingerprint_identical is the mandatory check that replaces it.
            ledger = _ledger_or_none(cur)
            # Also as the login role: after the switch pg_stat_activity hides the login's sessions (I2).
            sessions = _other_sessions(cur, same_role_only=True)
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
                "advisory_lock_free": bool(free), "other_sessions": sessions,
                "pg_dump_probe_ok": bool(pg_dump_probe),
                **_fp_fields(schema_fingerprint(cur)),
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
    """argv carries libpq variable NAMES only (``docker exec -e NAME``); every connection value,
    hosted or not, travels in the returned extra environment (never in /proc/*/cmdline)."""
    if target.mode == "local":
        # TEST-ONLY: a disposable database on this same container, dumped as the hosted login over the
        # container's own loopback (pg_hba trust there), so the dump runs with hosted privileges.
        extra = {"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": HOSTED_LOGIN_ROLE, "PGDATABASE": target.database}
    else:
        extra = {"PGHOST": target.host, "PGHOSTADDR": target.hostaddr, "PGPORT": str(target.port),
                 "PGUSER": target.user, "PGDATABASE": target.database, "PGPASSWORD": target.password,
                 "PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/tmp/hosted-ca.crt"}
    argv = ["docker", "exec", *(a for name in extra for a in ("-e", name)), CONTAINER, "pg_dump",
            "--format=custom", "--role=origenlab_owner", "--no-owner", *(f"--schema={s}" for s in DUMP_SCHEMAS)]
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
