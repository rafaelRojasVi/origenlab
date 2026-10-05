"""The worker's database boundary: one verified connection as `origenlab_worker`, the run lock, and
the only writes Phase 4a makes — `comms.message` (+ participants, attachments),
`evidence.source_record`, and the `comms.mailbox` authorization and cursor.

The remote rules are `apps/api`'s (`src/origenlab_api/v2/remote_database.py`:
`validate_remote_target` :74, `_PROBE` :173, `verify_runtime_connection` :192) for the worker's
own role: one named host, never an IP literal; the Supavisor session route or the direct
connection on 5432, never transaction mode on 6543; no query string, so no libpq keyword can
weaken TLS; `sslmode=verify-full` against the Supabase CA. After connecting, the session proves
from the inside that it is `origenlab_worker`, holds no elevated attribute or membership, holds
the grants and RLS policies the capture needs, and can write nothing in `crm.*` or `outbound.*`.
A target that cannot prove all of that is refused before the first statement of a run.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any
from urllib.parse import unquote, urlsplit

from origenlab_worker.errors import ConfigRefused

WORKER_ROLE = "origenlab_worker"
APP_NAME = "origenlab-worker-gmail-sync"
TRANSACTION_POOLER_PORT = 6543
SESSION_PORT = 5432
DISPOSABLE_DB = re.compile(r"origenlab_test_[0-9a-f]{8}")
KEEPALIVES = {"keepalives": "1", "keepalives_idle": "15", "keepalives_interval": "5", "keepalives_count": "3"}
_WORKER_LOGIN = re.compile(r"^origenlab_worker(\.[a-z0-9]{1,63})?$")
_HOST_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

#: (schema, table, policy) the capture writes through; missing any → refused.
REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("comms", "mailbox", "origenlab_worker_select"),
        ("comms", "mailbox", "origenlab_worker_update"),
        ("comms", "message", "origenlab_worker_select"),
        ("comms", "message", "origenlab_worker_insert"),
        ("comms", "message_participant", "origenlab_worker_insert"),
        ("comms", "attachment", "origenlab_worker_insert"),
        ("evidence", "source_record", "origenlab_worker_select"),
        ("evidence", "source_record", "origenlab_worker_insert"),
    }
)
REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("comms.mailbox", "SELECT"), ("comms.mailbox", "UPDATE"),
    ("comms.message", "SELECT"), ("comms.message", "INSERT"),
    ("comms.message_participant", "INSERT"), ("comms.attachment", "INSERT"),
    ("evidence.source_record", "SELECT"), ("evidence.source_record", "INSERT"),
)


class TargetRefused(ConfigRefused):
    """The database target or the session it reached is not the worker's; nothing was written."""


@dataclass(frozen=True)
class WorkerTarget:
    dsn: str = field(repr=False)  # carries the password
    database: str
    remote: bool
    connect_options: dict[str, str] = field(default_factory=dict)


def write_ca_file(pem: str) -> str:
    """The Supabase CA from its environment value to a private temporary file; its path."""
    text = (pem or "").strip()
    if "-----BEGIN CERTIFICATE-----" not in text or "-----END CERTIFICATE-----" not in text:
        raise TargetRefused("ca_pem_missing")
    if "PRIVATE KEY-----" in text:
        raise TargetRefused("ca_pem_has_private_key")
    try:
        data = (text + "\n").encode("ascii")
    except UnicodeEncodeError:
        raise TargetRefused("ca_pem_not_ascii") from None
    directory = tempfile.mkdtemp(prefix="origenlab-worker-ca-")  # mode 700
    atexit.register(shutil.rmtree, directory, True)  # registered first: a failed write leaves nothing
    path = os.path.join(directory, "ca.pem")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def _validated_ca_file(ca_file: str | None) -> str:
    raw = (ca_file or "").strip()
    if not raw or raw == "system" or not os.path.isabs(raw) or not os.path.isfile(raw):
        raise TargetRefused("ca_file_invalid")
    return raw


def remote_worker_target(url: str, *, expected_host: str | None, ca_file: str) -> WorkerTarget:
    raw = (url or "").strip()
    if not raw:
        raise TargetRefused("database_url_missing")
    expected = (expected_host or "").strip().lower().rstrip(".")
    if not _HOST_NAME.match(expected):
        raise TargetRefused("expected_host_invalid")
    parts = urlsplit(raw)
    if parts.scheme not in ("postgres", "postgresql"):
        raise TargetRefused("database_url_scheme")
    if parts.query or parts.fragment:
        raise TargetRefused("database_url_has_options")
    if "," in parts.netloc:
        raise TargetRefused("database_url_multiple_hosts")
    if parts.netloc.count("@") > 1:  # Python and libpq disagree on which part is the host
        raise TargetRefused("database_url_multiple_at")
    host = (parts.hostname or "").rstrip(".")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise TargetRefused("database_host_is_ip")
    if host != expected:
        raise TargetRefused("database_host_not_expected")
    try:
        port = parts.port
    except ValueError:
        raise TargetRefused("database_port_invalid") from None
    if port == TRANSACTION_POOLER_PORT:
        raise TargetRefused("transaction_pooler_refused")
    if port != SESSION_PORT:  # no port at all would let libpq fall back to PGPORT
        raise TargetRefused("database_port_invalid")
    if not _WORKER_LOGIN.match(unquote(parts.username or "")):
        raise TargetRefused("login_not_worker")
    database = unquote(parts.path.lstrip("/"))
    if not database or "/" in database or database in ("template0", "template1"):
        raise TargetRefused("database_name_invalid")
    ca = _validated_ca_file(ca_file)
    return WorkerTarget(
        dsn=raw,
        database=database,
        remote=True,
        connect_options={"sslmode": "verify-full", "sslrootcert": ca, "connect_timeout": "10", **KEEPALIVES},
    )


def local_test_target(dsn: str) -> WorkerTarget:
    """Tests only: a loopback `origenlab_test_<hex>` database the suite created and will drop."""
    parts = urlsplit(dsn)
    if parts.query or parts.fragment:
        raise TargetRefused("database_url_has_options")
    if parts.hostname not in _LOOPBACK:
        raise TargetRefused("not_loopback")
    database = unquote(parts.path.lstrip("/"))
    if not DISPOSABLE_DB.fullmatch(database):
        raise TargetRefused("not_a_disposable_test_database")
    return WorkerTarget(dsn=dsn, database=database, remote=False, connect_options={"connect_timeout": "10"})


_ROLE_PROBE = """
select current_user::text,
       current_database()::text,
       r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication,
       array(
         select m.rolname::text from pg_roles m
         where m.oid <> r.oid
           and pg_has_role(current_user, m.oid, 'MEMBER')
         order by 1
       )
from pg_roles r where r.rolname = current_user
"""

_POLICY_PROBE = """
select schemaname::text, tablename::text, policyname::text
  from pg_policies
 where %s::name = any(roles)
"""

# Table-level or column-level: has_any_column_privilege is true for either, so a column grant
# cannot slip past a check that only looks at the table. Views and materialized views count for
# the DML verbs (a view can be written through); TRIGGER lets the holder run code at the owner's
# next write.
_WRITE_PROBE = """
select n.nspname || '.' || c.relname, p.privilege
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 cross join (values ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), ('TRIGGER')) as p(privilege)
 where n.nspname in ('crm', 'outbound') and c.relkind in ('r', 'p', 'v', 'm')
   and (case when p.privilege in ('INSERT', 'UPDATE')
             then has_any_column_privilege(c.oid, p.privilege)
             else has_table_privilege(c.oid, p.privilege) end)
 order by 1, 2
"""

# A SECURITY DEFINER function runs with its owner's rights, whatever the worker's own grants are.
_DEFINER_PROBE = """
select n.nspname || '.' || p.proname
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where p.prosecdef and n.nspname not in ('pg_catalog', 'information_schema')
   and has_function_privilege(p.oid, 'EXECUTE')
 order by 1
"""

#: The one write the schema grants the worker outside its own schemas: it proposes 4c reply
#: rows (`select, insert` + an insert policy) and the API records the verdict. See
#: supabase/migrations/20260908120100_slice0_outbound_campaign_reply.sql. 4a never writes it.
ALLOWED_OUTSIDE_WRITES: frozenset[tuple[str, str]] = frozenset({("outbound.campaign_reply", "INSERT")})


def verify_worker_connection(conn: Any, target: WorkerTarget) -> None:
    """Prove the session is the worker and nothing more; raise :class:`TargetRefused` otherwise."""
    if target.remote:
        if not getattr(conn.pgconn, "ssl_in_use", False):
            raise TargetRefused("connection_not_tls")
        if conn.info.get_parameters().get("sslmode") != "verify-full":
            raise TargetRefused("connection_not_verify_full")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(_ROLE_PROBE)
        row = cur.fetchone()
        if row is None:
            raise TargetRefused("role_not_visible")
        user, database, *attributes, memberships = row
        if user != WORKER_ROLE:
            raise TargetRefused("not_worker_role")
        if database != target.database:
            raise TargetRefused("database_differs")
        if any(attributes):
            raise TargetRefused("worker_role_elevated")
        if memberships:
            raise TargetRefused("worker_role_member_of_privileged_role")
        for table, privilege in REQUIRED_PRIVILEGES:
            cur.execute("select has_table_privilege(%s, %s)", (table, privilege))
            if not cur.fetchone()[0]:
                raise TargetRefused("worker_grant_missing")
        cur.execute(_POLICY_PROBE, (WORKER_ROLE,))
        if not REQUIRED_POLICIES <= set(cur.fetchall()):
            raise TargetRefused("worker_policy_missing")
        cur.execute(_WRITE_PROBE)
        if set(cur.fetchall()) - ALLOWED_OUTSIDE_WRITES:
            raise TargetRefused("worker_can_write_crm_or_outbound")
        cur.execute(_DEFINER_PROBE)
        if cur.fetchall():
            raise TargetRefused("worker_can_execute_security_definer")


class WorkerDb:
    def __init__(self, conn: Any, statement_timeout_ms: int = 30_000) -> None:
        self._conn = conn
        self._timeout = int(statement_timeout_ms)

    @property
    def connection(self) -> Any:
        return self._conn

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(f"set local statement_timeout = {self._timeout}")
            yield cur


@contextmanager
def open_worker_db(target: WorkerTarget, connect: Callable[..., Any] | None = None) -> Iterator[WorkerDb]:
    """One autocommit connection (each write opens its own transaction), verified before use."""
    if connect is None:
        import psycopg

        connect = psycopg.connect
    conn = connect(target.dsn, autocommit=True, application_name=APP_NAME, **target.connect_options)
    try:
        verify_worker_connection(conn, target)
        yield WorkerDb(conn)
    finally:
        with suppress(Exception):
            conn.execute("select pg_advisory_unlock_all()")
        conn.close()


__all__ = [
    "ALLOWED_OUTSIDE_WRITES", "APP_NAME", "TargetRefused", "WORKER_ROLE", "WorkerDb", "WorkerTarget", "local_test_target",
    "open_worker_db", "remote_worker_target", "verify_worker_connection", "write_ca_file",
]
