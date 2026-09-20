"""The database boundary for the Wave 1A/1B → V2 import.

One rule, enforced here and nowhere else: **the importer may only ever open a disposable
local database.** `docs/STATUS.md` §2.5 records that no hosted Supabase project has been
adopted and that no data-plane connection has ever been made to one from this repository;
this module is what keeps that sentence true while an apply path exists at all.

It mirrors `supabase/scripts/lib/local_target.sh`, which refuses a non-loopback host for
every Slice 0 evidence script and unsets the inherited libpq environment before it runs
anything, and it shares no code with the separate hosted boundary in
`supabase/audit/olaudit/target_hosted.py`. There is deliberately no authorization flag that
widens it: the hosted path is a different tool, reviewed separately.

**Validating the authority of a URI is not enough.** libpq reads a PostgreSQL URI's query
string as connection *keywords*, so `postgresql://127.0.0.1/db?host=203.0.113.10` has a
loopback authority and a remote effective target; `hostaddr` and `service` redirect the same
way, and the `PGHOSTADDR`/`PGSERVICE` environment variables do it without touching the DSN
at all. The three defences below close that class as a whole rather than one keyword at a
time:

1. **Only a literal loopback IP address is accepted.** A name — including ``localhost`` —
   is refused, because what this module validates (the text) and what libpq resolves (an
   address, via whatever the host's name service says today) are two different things, and
   a boundary that can be moved by an `/etc/hosts` line is not a boundary. An IP literal
   has no such gap: ``127.0.0.1`` and ``::1`` mean the loopback interface to both.
2. **No query string, no fragment, no alternate routing form.** A URI carrying either is
   refused outright rather than filtered, so no keyword has to be enumerated to be safe,
   and the DSN handed to the driver is rebuilt from the parts that were actually checked.
3. **The libpq environment is neutralized for the duration of the connection**
   (:func:`neutralized_libpq_environment`), so no `PG*` variable can route around the DSN.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urlunsplit

#: URI schemes PostgreSQL accepts. A non-PostgreSQL scheme never reaches a driver.
POSTGRES_SCHEMES: frozenset[str] = frozenset({"postgres", "postgresql"})

#: Substrings that mark a managed/hosted provider. Refused even on a loopback host, because
#: their presence means the operator pasted a hosted DSN and edited it by hand.
HOSTED_PROVIDER_MARKERS: tuple[str, ...] = (
    "supabase.co",
    "supabase.com",
    "supabase.in",
    "pooler.supabase",
    "render.com",
    "neon.tech",
    "rds.amazonaws.com",
    "azure.com",
    "cloudflare",
)

#: libpq environment variables that can redirect, or supply a fallback for, a connection.
#: Every one of them is removed for the lifetime of a connection this module authorizes.
#: `PGHOSTADDR` is the sharpest of them: with it set, libpq connects to that address and
#: uses the DSN's host only for authentication, so the validated target is never contacted.
LIBPQ_ROUTING_ENVIRONMENT: tuple[str, ...] = (
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGSYSCONFDIR",
    "PGTARGETSESSIONATTRS",
    "PGCONNECT_TIMEOUT",
    "PGREQUIRESSL",
    "PGSSLMODE",
    "PGCHANNELBINDING",
    "PGGSSENCMODE",
    "PGKRBSRVNAME",
    "PGPASSFILE",
)


class TargetRefused(Exception):
    """The requested database target is not a disposable local database."""


@dataclass(frozen=True)
class LocalTarget:
    """A validated loopback PostgreSQL target.

    Attributes:
        dsn: the DSN handed to the driver. It is **rebuilt** from the validated parts, not
            the operator's original string, so it can carry no keyword this module did not
            check. See :func:`assert_local_target`.
        host: the validated loopback IP literal.
        port: the resolved port.
        database: the database name, for the report.
    """

    dsn: str
    host: str
    port: int
    database: str

    def redacted(self) -> str:
        """A form safe to print: no user, no password, no query string."""
        return f"postgresql://{self.host}:{self.port}/{self.database}"


def _loopback_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse ``host`` as a loopback IP literal, or refuse.

    Raises:
        TargetRefused: ``host`` is a name rather than an address, or is an address outside
            the loopback ranges (127.0.0.0/8 and ::1).
    """
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError as exc:
        raise TargetRefused(
            f"database target refused: host {host!r} is a name, not an IP address. "
            "This importer accepts a literal loopback address only (127.0.0.1 or ::1): a "
            "name is resolved by the host's name service after this check runs, so what is "
            "validated and what is connected to would not be the same thing"
        ) from exc
    if not address.is_loopback:
        raise TargetRefused(
            f"database target refused: address {address} is not loopback. "
            "No hosted or operator database may be written by this importer"
        )
    return address


def assert_local_target(dsn: str) -> LocalTarget:
    """Validate ``dsn`` as a disposable local database, or refuse.

    The returned :attr:`LocalTarget.dsn` is reassembled from the scheme, userinfo, validated
    loopback address, port and database name — and nothing else. Whatever the caller wrote
    after a ``?`` or a ``#`` is not filtered, corrected or passed on; it makes the whole DSN
    a refusal.

    Args:
        dsn: a PostgreSQL connection URI.

    Returns:
        The validated :class:`LocalTarget`.

    Raises:
        TargetRefused: the DSN is malformed, is not a PostgreSQL URI, carries a query string
            or a fragment, names more than one host, names a Unix socket, names a host that
            is not a literal loopback address, carries a hosted-provider marker, or omits a
            host or a database. A DSN that omits the host would connect over a Unix socket
            to whatever cluster the environment happens to point at, which is not a target
            we can vouch for.
    """
    if not dsn or not dsn.strip():
        raise TargetRefused("database target refused: empty DSN")

    raw = dsn.strip()
    try:
        parts = urlsplit(raw)
    except ValueError as exc:  # pragma: no cover - urlsplit rarely raises
        raise TargetRefused(f"database target refused: unparsable DSN ({exc})") from exc

    scheme = parts.scheme.lower()
    if scheme not in POSTGRES_SCHEMES:
        raise TargetRefused(
            f"database target refused: scheme {parts.scheme!r} is not postgresql. "
            "A key/value conninfo string is not accepted either; write a postgresql:// URI"
        )

    lowered = raw.lower()
    for marker in HOSTED_PROVIDER_MARKERS:
        if marker in lowered:
            raise TargetRefused(
                f"database target refused: DSN carries hosted-provider marker {marker!r}; "
                "this importer writes only to a disposable local database"
            )

    # libpq reads URI query parameters as connection keywords, so a query string can move
    # the effective target (`host`, `hostaddr`, `service`) regardless of what the authority
    # says. Nothing here needs one, so the whole query string is refused rather than
    # filtered — a filter has to be kept in step with libpq's keyword list; a refusal does
    # not.
    if "?" in raw or parts.query:
        raise TargetRefused(
            "database target refused: the DSN carries a query string. libpq reads URI "
            "query parameters as connection keywords — host, hostaddr and service among "
            "them — so a query string can redirect the connection away from the authority "
            "this check validated. A bare '?' is refused too: nothing in a DSN this "
            "importer accepts needs one. Pass postgresql://<loopback>:<port>/<db>"
        )
    if "#" in raw or parts.fragment:
        raise TargetRefused(
            "database target refused: the DSN carries a URI fragment; a connection URI "
            "has no fragment, so its presence means the string is not what it appears"
        )

    netloc_host = parts.netloc.rsplit("@", 1)[-1]
    if "," in netloc_host:
        raise TargetRefused(
            "database target refused: the DSN names more than one host. libpq tries each "
            "in turn, so only the first could ever be validated"
        )
    if "%" in netloc_host:
        raise TargetRefused(
            "database target refused: the DSN's host is percent-encoded. That is how a "
            "Unix-socket directory is written in a URI, and it is not a target this "
            "importer can vouch for"
        )

    host = unquote(parts.hostname or "")
    if not host:
        raise TargetRefused(
            "database target refused: DSN names no host. A host-less DSN resolves to a "
            "Unix socket on an unknown cluster; name a loopback address explicitly"
        )

    address = _loopback_literal(host)

    try:
        port = parts.port or 5432
    except ValueError as exc:
        raise TargetRefused(f"database target refused: invalid port ({exc})") from exc

    path = parts.path.lstrip("/")
    if "/" in path:
        raise TargetRefused(
            f"database target refused: DSN path {parts.path!r} names more than a database"
        )
    database = unquote(path)
    if not database:
        raise TargetRefused("database target refused: DSN names no database")

    # Rebuilt, never passed through: the userinfo is carried verbatim (it is already in the
    # percent-encoded form libpq expects), the host is the literal that was validated, and
    # there is no query and no fragment to carry.
    userinfo = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
    literal = f"[{address}]" if address.version == 6 else str(address)
    validated = urlunsplit((scheme, f"{userinfo}{literal}:{port}", f"/{path}", "", ""))

    return LocalTarget(dsn=validated, host=str(address), port=port, database=database)


@contextmanager
def neutralized_libpq_environment() -> Iterator[tuple[str, ...]]:
    """Remove every libpq routing variable for the duration of the block.

    A validated DSN is only half the boundary: `PGHOSTADDR` redirects a connection whose URI
    names a loopback host, and `PGSERVICE` supplies connection keywords from a file. The
    same reasoning as `supabase/scripts/lib/local_target.sh`, which unsets the `PG*` family
    before any Slice 0 script opens `psql`.

    Yields:
        The names that were removed — never their values, which may be credentials.
    """
    saved = {name: os.environ[name] for name in LIBPQ_ROUTING_ENVIRONMENT if name in os.environ}
    for name in saved:
        del os.environ[name]
    try:
        yield tuple(sorted(saved))
    finally:
        os.environ.update(saved)


__all__ = [
    "HOSTED_PROVIDER_MARKERS",
    "LIBPQ_ROUTING_ENVIRONMENT",
    "LocalTarget",
    "POSTGRES_SCHEMES",
    "TargetRefused",
    "assert_local_target",
    "neutralized_libpq_environment",
]
