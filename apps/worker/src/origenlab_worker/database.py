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
from datetime import datetime, timedelta
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from origenlab_worker.errors import ConfigRefused

if TYPE_CHECKING:
    from origenlab_worker.capture import Capture, UnparsedMessage

WORKER_ROLE = "origenlab_worker"
APP_NAME = "origenlab-worker-gmail-sync"
LOCK_NAME = "origenlab-worker:gmail-sync"
LOCK_ACQUIRED, LOCK_HELD, LOCK_STUCK = "acquired", "held", "stuck"
#: A lock holder idle this long is a dead run whose session outlived it (spec §11.1): a live run
#: issues a statement every few seconds.
STALE_LOCK_AFTER = timedelta(minutes=30)
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
# Only functions the worker can actually call count: EXECUTE on it, USAGE on its schema, not a
# trigger function (uncallable directly), and not extension-owned (installing an extension needs a
# superuser, so the worker's reach cannot grow that way). Never skipped by schema name: a schema
# called `extensions` or `auth` that holds an ordinary definer function must still refuse.
_DEFINER_PROBE = """
select n.nspname || '.' || p.proname
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where p.prosecdef and n.nspname not in ('pg_catalog', 'information_schema')
   and has_function_privilege(p.oid, 'EXECUTE')
   and has_schema_privilege(n.oid, 'USAGE')
   and p.prorettype not in ('trigger'::regtype, 'event_trigger'::regtype)
   and not exists (select 1 from pg_depend d
                    where d.classid = 'pg_proc'::regclass and d.objid = p.oid and d.deptype = 'e')
 order by 1
"""

#: The one write the schema grants the worker outside its own schemas: it proposes 4c reply
#: rows (`select, insert` + an insert policy) and the API records the verdict. See
#: supabase/migrations/20260908120100_slice0_outbound_campaign_reply.sql. 4a never writes it.
ALLOWED_OUTSIDE_WRITES: frozenset[tuple[str, str]] = frozenset({("outbound.campaign_reply", "INSERT")})


#: What the mail triage reads and writes (`triage.py`): the captured message and its evidence, the
#: catalog it matches products against, the cases the thread is linked to (read only), and INSERT
#: on evidence.assertion for its proposals.
TRIAGE_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("comms.message", "SELECT"), ("evidence.source_record", "SELECT"),
    ("evidence.assertion", "SELECT"), ("evidence.assertion", "INSERT"), ("catalog.product", "SELECT"),
    ("crm.opportunity", "SELECT"), ("crm.opportunity_evidence", "SELECT"),
)
TRIAGE_REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("comms", "message", "origenlab_worker_select"),
        ("evidence", "source_record", "origenlab_worker_select"),
        ("evidence", "assertion", "origenlab_worker_select"),
        ("evidence", "assertion", "origenlab_worker_insert"),
        ("catalog", "product", "origenlab_worker_select"),
        ("crm", "opportunity", "origenlab_worker_select"),
        ("crm", "opportunity_evidence", "origenlab_worker_select"),
    }
)
#: The Procrastinate queue (supabase/migrations/20261006180000): every table, every verb, and one
#: named policy per verb.
QUEUE_TABLES: tuple[str, ...] = ("procrastinate_jobs", "procrastinate_events", "procrastinate_periodic_defers",
                                 "procrastinate_workers")
QUEUE_REQUIRED_PRIVILEGES: tuple[tuple[str, str], ...] = tuple(
    (f"procrastinate.{t}", verb) for t in QUEUE_TABLES for verb in ("SELECT", "INSERT", "UPDATE", "DELETE")
)
QUEUE_REQUIRED_POLICIES: frozenset[tuple[str, str, str]] = frozenset(
    ("procrastinate", t, f"origenlab_worker_{verb}") for t in QUEUE_TABLES
    for verb in ("select", "insert", "update", "delete")
)

# A table that does not exist answers false, not an error: the refusal is `worker_grant_missing`.
_PRIVILEGE_PROBE = "select coalesce(has_table_privilege(to_regclass(%s), %s), false)"


@dataclass(frozen=True)
class _Observed:
    privileges: tuple[bool, ...]
    policies: frozenset[tuple[str, str, str]]
    writes: frozenset[tuple[str, str]]
    definers: tuple[str, ...]


def _judge_role(role_row: Any, target: WorkerTarget) -> None:
    """Who the session is — judged before anything else is probed: a wrong session stops here."""
    if role_row is None:
        raise TargetRefused("role_not_visible")
    user, database, *attributes, memberships = role_row
    if user != WORKER_ROLE:
        raise TargetRefused("not_worker_role")
    if database != target.database:
        raise TargetRefused("database_differs")
    if any(attributes):
        raise TargetRefused("worker_role_elevated")
    if memberships:
        raise TargetRefused("worker_role_member_of_privileged_role")


def _judge_reach(seen: _Observed, required_policies: frozenset[tuple[str, str, str]]) -> None:
    """What the session can do, in a fixed order; raises :class:`TargetRefused`."""
    if not all(seen.privileges):
        raise TargetRefused("worker_grant_missing")
    if not required_policies <= seen.policies:
        raise TargetRefused("worker_policy_missing")
    if seen.writes - ALLOWED_OUTSIDE_WRITES:
        raise TargetRefused("worker_can_write_crm_or_outbound")
    if seen.definers:
        raise TargetRefused("worker_can_execute_security_definer")


def _check_tls(conn: Any, target: WorkerTarget) -> None:
    if target.remote:
        if not getattr(conn.pgconn, "ssl_in_use", False):
            raise TargetRefused("connection_not_tls")
        if conn.info.get_parameters().get("sslmode") != "verify-full":
            raise TargetRefused("connection_not_verify_full")


def verify_worker_connection(
    conn: Any,
    target: WorkerTarget,
    *,
    privileges: tuple[tuple[str, str], ...] = REQUIRED_PRIVILEGES,
    policies: frozenset[tuple[str, str, str]] = REQUIRED_POLICIES,
) -> None:
    """Prove the session is the worker and nothing more; raise :class:`TargetRefused` otherwise.

    `privileges` and `policies` are what the job about to run needs (the Gmail capture's by
    default; the triage and the queue pass their own). The role, crm/outbound-write and definer
    refusals are the same for every job."""
    _check_tls(conn, target)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(_ROLE_PROBE)
        _judge_role(cur.fetchone(), target)
        held = []
        for table, privilege in privileges:
            cur.execute(_PRIVILEGE_PROBE, (table, privilege))
            held.append(bool(cur.fetchone()[0]))
        cur.execute(_POLICY_PROBE, (WORKER_ROLE,))
        found = frozenset(cur.fetchall())
        cur.execute(_WRITE_PROBE)
        writes = frozenset(cur.fetchall())
        cur.execute(_DEFINER_PROBE)
        definers = tuple(r[0] for r in cur.fetchall())
    _judge_reach(_Observed(tuple(held), found, writes, definers), policies)


async def averify_worker_connection(
    conn: Any,
    target: WorkerTarget,
    *,
    privileges: tuple[tuple[str, str], ...],
    policies: frozenset[tuple[str, str, str]],
) -> None:
    """:func:`verify_worker_connection` for a psycopg `AsyncConnection` — the queue pool runs it on
    every connection it opens, before Procrastinate issues a statement."""
    _check_tls(conn, target)
    async with conn.transaction(), conn.cursor() as cur:
        await cur.execute("set transaction read only")
        await cur.execute(_ROLE_PROBE)
        _judge_role(await cur.fetchone(), target)
        held = []
        for table, privilege in privileges:
            await cur.execute(_PRIVILEGE_PROBE, (table, privilege))
            held.append(bool((await cur.fetchone())[0]))
        await cur.execute(_POLICY_PROBE, (WORKER_ROLE,))
        found = frozenset(await cur.fetchall())
        await cur.execute(_WRITE_PROBE)
        writes = frozenset(await cur.fetchall())
        await cur.execute(_DEFINER_PROBE)
        definers = tuple(r[0] for r in await cur.fetchall())
    _judge_reach(_Observed(tuple(held), found, writes, definers), policies)


@dataclass(frozen=True)
class Mailbox:
    id: str
    address: str
    authorization_state: str
    history_id: str | None
    last_synced_at: datetime | None


@dataclass(frozen=True)
class RecordOutcome:
    new_message: bool
    evidence_created: bool


_INSERT_MESSAGE = """
insert into comms.message
    (mailbox_id, provider_message_id, provider_thread_id, rfc822_message_id_norm, direction,
     internal_date, subject, labels, eml_storage_path, eml_sha256, size_bytes, parse_status, parse_error)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (mailbox_id, provider_message_id) do nothing
returning id::text
"""
_INSERT_PARTICIPANT = """
insert into comms.message_participant (message_id, role, address_norm, display_name)
values (%s, %s, %s, %s)
on conflict (message_id, role, address_norm) do nothing
"""
_INSERT_ATTACHMENT = """
insert into comms.attachment (message_id, part_index, filename, mime_type, size_bytes, sha256)
values (%s, %s, %s, %s, %s, %s)
on conflict (message_id, part_index) do nothing
"""
_INSERT_EVIDENCE = """
insert into evidence.source_record (kind, dedupe_key, payload, source_uri, review_status)
values ('gmail_message', %s, %s, %s, 'pending')
on conflict (dedupe_key) do nothing
returning id::text
"""
_OLD_HOLDERS = """
select l.pid, a.state
  from pg_locks l
  join pg_stat_activity a on a.pid = l.pid
 cross join (select hashtextextended(%s, 0) as key) k
 where l.locktype = 'advisory' and l.granted and l.objsubid = 1
   and l.classid = ((k.key >> 32) & 4294967295)::oid
   and l.objid = (k.key & 4294967295)::oid
   and l.database = (select oid from pg_database where datname = current_database())
   and l.pid <> pg_backend_pid()
   and a.usename = current_user
   and a.state_change < now() - make_interval(secs => %s)
"""


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


    # ------------------------------------------------------------------ the run lock

    def try_lock(self, stale_after: timedelta = STALE_LOCK_AFTER) -> str:
        """One run at a time, whatever Render does on overlap (spec §6): `LOCK_ACQUIRED`,
        `LOCK_HELD` (another live run) or `LOCK_STUCK`.

        A session-level advisory lock survives every per-message commit and dies with the session.
        If the holder is a session left *idle* past `stale_after` — a killed run whose pooled server
        session was not reset — it is terminated (a role may end its own sessions) and the lock taken.

        A holder of this role and database that is past `stale_after` in any other state
        (`idle in transaction`, `active`) is never ended — it may be mid-write — but it will not
        free itself either, so it is reported as `LOCK_STUCK` and the run fails loudly instead of
        answering `locked` forever. A live run is never in such a state that long: its statements
        time out at 30 s and it issues one every few seconds.
        """
        with self._conn.cursor() as cur:
            cur.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))", (LOCK_NAME,))
            if cur.fetchone()[0]:
                return LOCK_ACQUIRED
            cur.execute(_OLD_HOLDERS, (LOCK_NAME, stale_after.total_seconds()))
            holders = cur.fetchall()
            stale = [pid for pid, state in holders if state == "idle"]
            if not stale:
                return LOCK_STUCK if holders else LOCK_HELD
            for pid in stale:
                cur.execute("select pg_terminate_backend(%s, 5000)", (pid,))
            cur.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))", (LOCK_NAME,))
            if cur.fetchone()[0]:
                return LOCK_ACQUIRED
            return LOCK_STUCK if len(stale) < len(holders) else LOCK_HELD

    # ------------------------------------------------------------------ the mailbox

    def mailbox(self, address: str) -> Mailbox | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "select id::text, address_norm, authorization_state, history_id, last_synced_at "
                "from comms.mailbox where address_norm = %s",
                (address,),
            )
            row = cur.fetchone()
        return Mailbox(*row) if row else None

    def authorize(self, mailbox_id: str, *, baseline_history_id: str, scopes: list[str]) -> str:
        """`baseline` on the first authorization (capture starts now); `resumed` when a cursor
        exists — after a pause or a re-consent the capture continues from it, nothing is skipped."""
        with self._tx() as cur:
            cur.execute("select history_id from comms.mailbox where id = %s for update", (mailbox_id,))
            found = cur.fetchone()
            if found is None:
                raise LookupError("mailbox_not_found")
            (current,) = found
            cur.execute(
                """
                update comms.mailbox
                   set authorization_state = 'authorized', granted_scopes = %s,
                       history_id = coalesce(history_id, %s),
                       last_synced_at = coalesce(last_synced_at, now()),
                       updated_at = now()
                 where id = %s
                """,
                (scopes, baseline_history_id, mailbox_id),
            )
        return "baseline" if current is None else "resumed"

    def mark_revoked(self, mailbox_id: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "update comms.mailbox set authorization_state = 'revoked', updated_at = now() where id = %s",
                (mailbox_id,),
            )

    def advance_cursor(self, mailbox_id: str, history_id: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "update comms.mailbox set history_id = %s, last_synced_at = now(), updated_at = now() "
                "where id = %s",
                (history_id, mailbox_id),
            )

    # ------------------------------------------------------------------ messages

    def message_exists(self, mailbox_id: str, provider_message_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "select 1 from comms.message where mailbox_id = %s and provider_message_id = %s",
                (mailbox_id, provider_message_id),
            )
            return cur.fetchone() is not None

    def record(
        self, capture: Capture, *, mailbox_id: str, eml_path: str, eml_sha256: str, size_bytes: int
    ) -> RecordOutcome:
        """The message, its participants and attachments, and — unless it is a bulk send — its
        pending evidence, in one transaction."""
        from psycopg.types.json import Jsonb

        with self._tx() as cur:
            cur.execute(
                _INSERT_MESSAGE,
                (mailbox_id, capture.provider_message_id, capture.provider_thread_id,
                 capture.rfc822_message_id_norm, capture.direction, capture.internal_date,
                 capture.subject, list(capture.labels), eml_path, eml_sha256, size_bytes,
                 "parsed", None),
            )
            row = cur.fetchone()
            if row is None:
                return RecordOutcome(new_message=False, evidence_created=False)
            message_id = row[0]
            if capture.participants:
                cur.executemany(
                    _INSERT_PARTICIPANT,
                    [(message_id, p.role, p.address_norm, p.display_name) for p in capture.participants],
                )
            if capture.attachments:
                cur.executemany(
                    _INSERT_ATTACHMENT,
                    [(message_id, a.part_index, a.filename, a.mime_type, a.size_bytes, a.sha256)
                     for a in capture.attachments],
                )
            if capture.is_bulk_send:
                return RecordOutcome(new_message=True, evidence_created=False)
            cur.execute(
                _INSERT_EVIDENCE,
                (f"gmail_message:{capture.provider_message_id}", Jsonb(capture.payload),
                 f"gmail://msg/{capture.provider_message_id}"),
            )
            return RecordOutcome(new_message=True, evidence_created=cur.fetchone() is not None)

    def record_unparsed(
        self,
        row: UnparsedMessage,
        *,
        mailbox_id: str,
        eml_path: str | None,
        eml_sha256: str | None,
        size_bytes: int | None,
    ) -> bool:
        """A message kept without evidence (`parse_failed`, with its reason). True if new."""
        with self._tx() as cur:
            cur.execute(
                _INSERT_MESSAGE,
                (mailbox_id, row.provider_message_id, row.provider_thread_id,
                 row.rfc822_message_id_norm, row.direction, row.internal_date, row.subject,
                 list(row.labels), eml_path, eml_sha256, size_bytes, "parse_failed", row.reason),
            )
            return cur.fetchone() is not None


@contextmanager
def open_verified_connection(
    target: WorkerTarget,
    *,
    application_name: str,
    privileges: tuple[tuple[str, str], ...],
    policies: frozenset[tuple[str, str, str]],
    connect: Callable[..., Any] | None = None,
) -> Iterator[Any]:
    """One autocommit connection verified for the given requirements; closed on exit."""
    if connect is None:
        import psycopg

        connect = psycopg.connect
    conn = connect(target.dsn, autocommit=True, application_name=application_name, **target.connect_options)
    try:
        verify_worker_connection(conn, target, privileges=privileges, policies=policies)
        yield conn
    finally:
        with suppress(Exception):
            conn.execute("select pg_advisory_unlock_all()")
        conn.close()


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
    "QUEUE_REQUIRED_POLICIES", "QUEUE_REQUIRED_PRIVILEGES", "QUEUE_TABLES", "TRIAGE_REQUIRED_POLICIES",
    "TRIAGE_REQUIRED_PRIVILEGES", "averify_worker_connection", "open_verified_connection",
    "ALLOWED_OUTSIDE_WRITES", "ALLOWED_WORKER_DEFINERS", "APP_NAME", "LOCK_ACQUIRED", "LOCK_HELD", "LOCK_NAME", "LOCK_STUCK", "STALE_LOCK_AFTER", "Mailbox", "RecordOutcome",
    "TargetRefused", "WORKER_ROLE", "WorkerDb", "WorkerTarget", "local_test_target", "open_worker_db",
    "remote_worker_target", "verify_worker_connection", "write_ca_file",
]
