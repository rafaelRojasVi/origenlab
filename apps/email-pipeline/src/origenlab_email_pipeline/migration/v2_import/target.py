"""The database boundary for the Wave 1A/1B → V2 import.

One rule, enforced here and nowhere else: **the importer may only ever open a disposable
local database.** `docs/STATUS.md` §2.5 records that no hosted Supabase project has been
adopted and that no data-plane connection has ever been made to one from this repository;
this module is what keeps that sentence true while an apply path exists at all.

It mirrors `supabase/scripts/lib/local_target.sh`, which refuses a non-loopback host for
every Slice 0 evidence script, and it shares no code with the separate hosted boundary in
`supabase/audit/olaudit/target_hosted.py`. There is deliberately no authorization flag that
widens it: the hosted path is a different tool, reviewed separately.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

#: Hosts a disposable local database may listen on. Anything else is refused.
LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost", "localhost.localdomain"})

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


class TargetRefused(Exception):
    """The requested database target is not a disposable local database."""


@dataclass(frozen=True)
class LocalTarget:
    """A validated loopback PostgreSQL target.

    Attributes:
        dsn: the original DSN, passed through to the driver unchanged.
        host: the resolved host literal, for the report.
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


def _is_loopback_host(host: str) -> bool:
    """True when ``host`` is a loopback name or a loopback IP literal."""
    lowered = host.lower()
    if lowered in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(lowered.strip("[]")).is_loopback
    except ValueError:
        return False


def assert_local_target(dsn: str) -> LocalTarget:
    """Validate ``dsn`` as a disposable local database, or refuse.

    Args:
        dsn: a PostgreSQL connection URI.

    Returns:
        The validated :class:`LocalTarget`.

    Raises:
        TargetRefused: the DSN is malformed, is not PostgreSQL, names a non-loopback
            host, carries a hosted-provider marker, or omits a host entirely. A DSN
            that omits the host would connect over a Unix socket to whatever cluster
            the environment happens to point at, which is not a target we can vouch for.
    """
    if not dsn or not dsn.strip():
        raise TargetRefused("database target refused: empty DSN")

    try:
        parts = urlsplit(dsn.strip())
    except ValueError as exc:  # pragma: no cover - urlsplit rarely raises
        raise TargetRefused(f"database target refused: unparsable DSN ({exc})") from exc

    if parts.scheme.lower() not in POSTGRES_SCHEMES:
        raise TargetRefused(
            f"database target refused: scheme {parts.scheme!r} is not postgresql"
        )

    lowered = dsn.lower()
    for marker in HOSTED_PROVIDER_MARKERS:
        if marker in lowered:
            raise TargetRefused(
                f"database target refused: DSN carries hosted-provider marker {marker!r}; "
                "this importer writes only to a disposable local database"
            )

    host = unquote(parts.hostname or "")
    if not host:
        raise TargetRefused(
            "database target refused: DSN names no host. A host-less DSN resolves to a "
            "Unix socket on an unknown cluster; name a loopback host explicitly"
        )

    if not _is_loopback_host(host):
        raise TargetRefused(
            f"database target refused: host {host!r} is not loopback. "
            "No hosted or operator database may be written by this importer"
        )

    try:
        port = parts.port or 5432
    except ValueError as exc:
        raise TargetRefused(f"database target refused: invalid port ({exc})") from exc

    database = unquote(parts.path.lstrip("/"))
    if not database:
        raise TargetRefused("database target refused: DSN names no database")

    return LocalTarget(dsn=dsn.strip(), host=host, port=port, database=database)
