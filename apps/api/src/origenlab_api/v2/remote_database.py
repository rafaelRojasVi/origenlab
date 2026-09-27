"""The opt-in remote V2 database target: one named host, verified TLS, the runtime role only.

The default V2 target is a literal loopback DSN (`settings.assert_v2_target_is_local`), and
that rule is unchanged. This module is the one other shape the API will open, and only when
`ORIGENLAB_V2_DATABASE_REMOTE=true` is set deliberately together with the two values that make
a remote connection checkable:

* `ORIGENLAB_V2_DATABASE_EXPECTED_HOST` — the exact host name the DSN must name. The DSN comes
  from a secret store; this value comes from reviewed configuration, so a secret edited to
  point somewhere else refuses to start instead of connecting.
* `ORIGENLAB_V2_DATABASE_SSLROOTCERT` — a PEM file holding the CA the server certificate must
  chain to. Connections are opened with `sslmode=verify-full`, so libpq checks both the chain
  and that the certificate names the expected host. There is no weaker mode and no switch.

The DSN itself may carry no query string or fragment (so no libpq keyword can override the TLS
settings or redirect the host), must name the database, and must log in as `origenlab_api` —
the unprivileged runtime role — or its pooler spelling `origenlab_api.<project-ref>`. At
startup one connection then proves from the inside what the text only claims: the session is
`origenlab_api` on the named database, holds no superuser, BYPASSRLS, CREATEROLE, CREATEDB or
REPLICATION attribute, is not a member of the owner, migrator or `postgres` roles, and the
client connection is TLS with `verify-full`.

What this does **not** relax: the development header login still needs a loopback database
(`identity.LocalDevIdentity`), and the import, rehearsal and production-import tooling keeps
its own loopback-only guards (`migration.v2_import.target`, `scripts/quote_crm_import_production.py`),
which never read these settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import partial
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

RUNTIME_ROLE = "origenlab_api"
#: `origenlab_api`, or a connection pooler's `origenlab_api.<project-ref>` spelling.
_RUNTIME_LOGIN = re.compile(r"^origenlab_api(\.[a-z0-9]{1,63})?$")
_HOST_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
#: Roles whose membership would let the runtime login act as more than itself.
PRIVILEGED_ROLES = ("origenlab_owner", "origenlab_migrator", "postgres")
#: A hosted project may keep V2 in its `postgres` database, so only the templates are refused.
_REFUSED_DATABASES = {"template0", "template1"}


class RemoteTargetRefused(ValueError):
    """The remote V2 target is unsafe or incomplete; nothing was opened."""


@dataclass(frozen=True)
class V2DatabaseTarget:
    """A validated V2 target: the DSN plus the connection options every connect must use."""

    dsn: str
    remote: bool
    database: str | None = None
    connect_options: dict[str, str] = field(default_factory=dict)

    def connect_factory(self, connect: Callable[..., Any]) -> Callable[..., Any]:
        """`connect` with the TLS options bound, for the repositories' `connect(dsn)` calls.

        Keyword arguments to `psycopg.connect` override the DSN and the `PGSSL*` environment,
        so a stray `PGSSLMODE=disable` on the host cannot weaken a remote connection.
        """
        return partial(connect, **self.connect_options) if self.connect_options else connect


def validate_remote_target(url: str, *, expected_host: str | None, ca_file: str | None) -> V2DatabaseTarget:
    """Return the remote target, or raise :class:`RemoteTargetRefused`. Opens nothing."""
    raw = (url or "").strip()
    if not raw:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_URL is empty")
    expected = (expected_host or "").strip().lower().rstrip(".")
    if not expected:
        raise RemoteTargetRefused(
            "ORIGENLAB_V2_DATABASE_EXPECTED_HOST is required for a remote V2 database"
        )
    if not _HOST_NAME.match(expected):
        raise RemoteTargetRefused(
            "ORIGENLAB_V2_DATABASE_EXPECTED_HOST must be a DNS host name: verify-full checks the "
            "certificate against a name"
        )

    parts = urlsplit(raw)
    if parts.scheme not in ("postgres", "postgresql"):
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_URL must be a postgres:// or postgresql:// URI")
    if parts.query:
        raise RemoteTargetRefused(
            "ORIGENLAB_V2_DATABASE_URL must carry no query string: libpq reads one as connection "
            "keywords (sslmode, host, hostaddr) that could weaken or redirect the connection"
        )
    if parts.fragment:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_URL must carry no fragment")
    if "," in parts.netloc:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_URL must name exactly one host")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_URL names no host")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise RemoteTargetRefused(
            "a remote V2 database is named by host name, not an IP address; a loopback database "
            "uses the default local mode instead"
        )
    if host != expected:
        raise RemoteTargetRefused(
            "ORIGENLAB_V2_DATABASE_URL does not name ORIGENLAB_V2_DATABASE_EXPECTED_HOST"
        )
    try:
        parts.port
    except ValueError:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_URL has an invalid port") from None

    login = unquote(parts.username or "")
    if not _RUNTIME_LOGIN.match(login):
        raise RemoteTargetRefused(
            f"a remote V2 database must be opened as the runtime role {RUNTIME_ROLE!r} "
            "(or its pooler spelling origenlab_api.<project-ref>), never an owner, migrator, "
            "worker or superuser login"
        )
    database = unquote(parts.path.lstrip("/"))
    if not database or "/" in database:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_URL must name exactly one database")
    if database in _REFUSED_DATABASES:
        raise RemoteTargetRefused(f"ORIGENLAB_V2_DATABASE_URL names the {database!r} database")

    ca = _validated_ca_file(ca_file)
    return V2DatabaseTarget(
        dsn=raw,
        remote=True,
        database=database,
        connect_options={"sslmode": "verify-full", "sslrootcert": ca, "connect_timeout": "10"},
    )


def _validated_ca_file(ca_file: str | None) -> str:
    raw = (ca_file or "").strip()
    if not raw:
        raise RemoteTargetRefused(
            "ORIGENLAB_V2_DATABASE_SSLROOTCERT is required for a remote V2 database: the CA the "
            "server certificate must chain to"
        )
    path = Path(raw)
    if not path.is_absolute():
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_SSLROOTCERT must be an absolute path")
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_SSLROOTCERT is not a readable file") from None
    if "-----BEGIN CERTIFICATE-----" not in text:
        raise RemoteTargetRefused("ORIGENLAB_V2_DATABASE_SSLROOTCERT holds no PEM certificate")
    if "PRIVATE KEY-----" in text:
        raise RemoteTargetRefused(
            "ORIGENLAB_V2_DATABASE_SSLROOTCERT contains a private key; it must hold only CA certificates"
        )
    return str(path)


_PROBE = """
select current_user::text,
       current_database()::text,
       r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication,
       array(
         select p.rolname::text from unnest(%s::text[]) as p(rolname)
         where to_regrole(p.rolname) is not null
           and pg_has_role(current_user, to_regrole(p.rolname), 'MEMBER')
       )
from pg_roles r where r.rolname = current_user
"""


def verify_runtime_connection(target: V2DatabaseTarget, connect: Callable[..., Any]) -> None:
    """Open one connection and prove it is the restricted runtime role over verified TLS.

    Raises :class:`RemoteTargetRefused` with a reason that names no secret. Read-only.
    """
    with target.connect_factory(connect)(target.dsn) as conn:
        pgconn = conn.pgconn
        if not getattr(pgconn, "ssl_in_use", False):
            raise RemoteTargetRefused("the remote V2 connection is not using TLS")
        if conn.info.get_parameters().get("sslmode") != "verify-full":
            raise RemoteTargetRefused("the remote V2 connection is not sslmode=verify-full")
        with conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(_PROBE, (list(PRIVILEGED_ROLES),))
            row = cur.fetchone()
        conn.rollback()
    if row is None:
        raise RemoteTargetRefused("the remote V2 session role is not visible in pg_roles")
    user, database, superuser, bypassrls, createrole, createdb, replication, memberships = row
    if user != RUNTIME_ROLE:
        raise RemoteTargetRefused(
            f"the remote V2 session runs as {user!r}, not the runtime role {RUNTIME_ROLE!r}"
        )
    if database != target.database:
        raise RemoteTargetRefused("the remote V2 session reached a different database than the DSN names")
    attributes = {
        "SUPERUSER": superuser,
        "BYPASSRLS": bypassrls,
        "CREATEROLE": createrole,
        "CREATEDB": createdb,
        "REPLICATION": replication,
    }
    held = sorted(name for name, value in attributes.items() if value)
    if held:
        raise RemoteTargetRefused(f"the runtime role holds {', '.join(held)}")
    if memberships:
        raise RemoteTargetRefused(
            f"the runtime role is a member of {', '.join(sorted(memberships))}"
        )
