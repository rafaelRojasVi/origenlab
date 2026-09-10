"""The common target type, and the environment the child process is given.

A `Target` is the only thing `psqlrun` will connect to, and both target boundaries -- local and
hosted -- must produce one. Nothing else constructs it.

Two properties matter more than the fields:

**The password never reaches an argument vector.** `describe()` is what the report is allowed to
see, and it contains no password, no host name, no address and no project reference -- only their
classification. The connection itself is passed to psql entirely through environment variables, so
the process table shows `psql -X -q ... -f <script>` and no target at all.

**The child environment is built, not inherited.** `child_env()` starts from nothing and adds
exactly PATH, HOME, LANG and the libpq variables this target needs. An inherited `PGHOST`,
`PGSERVICE`, `SUPABASE_DB_URL` or `SUPABASE_ACCESS_TOKEN` therefore cannot redirect or
re-authenticate the connection: it is not in the child's environment at all. This is the same
refusal `supabase/scripts/lib/local_target.sh` makes by unsetting those names, arrived at from the
other direction.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field


class TargetError(RuntimeError):
    """A target was refused. Raised before any connection is attempted, in every case."""


LOOPBACK_NAMES = frozenset({"localhost"})


def is_loopback(host: str) -> bool:
    """True for a loopback name or a loopback IP literal, in either notation."""
    if host in LOOPBACK_NAMES:
        return True
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return ipaddress.ip_address(literal).is_loopback
    except ValueError:
        return False


# Kept out of the child environment even when present in this process.
SCRUBBED_PREFIXES = ("PG", "SUPABASE_")
SCRUBBED_NAMES = frozenset(
    {
        "SUPABASE_ACCESS_TOKEN",
        "SUPABASE_ANON_KEY",
        "SUPABASE_DB_PASSWORD",
        "SUPABASE_DB_URL",
        "SUPABASE_PROJECT_ID",
        "SUPABASE_PROJECT_REF",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_URL",
    }
)


@dataclass(frozen=True)
class Target:
    """A validated destination. Constructed only by target_local or target_hosted."""

    mode: str
    host: str
    hostaddr: str | None
    port: int
    user: str
    database: str
    sslmode: str
    sslrootcert: str | None
    password: str
    project_ref: str | None
    guard_notes: list[str] = field(default_factory=list)
    statement_timeout_ms: int = 15_000
    connect_timeout_s: int = 15

    @property
    def secrets(self) -> tuple[str, ...]:
        """Every literal this process holds that must never appear in output.

        Hosted mode contributes the password, the project reference, the host name and the resolved
        address. Local mode contributes nothing, deliberately: its credential is the Supabase CLI's
        fixed development password for a loopback container that `supabase/roles.sql` creates
        without any operator secret, and it is the string `postgres` -- also the role name, the
        database name and half of every `postgresql://` scheme in the output. Redacting it as a
        literal would blank legitimate evidence from every local report while protecting nothing.

        The protection that does apply to local mode is stronger than redaction: the password is
        never placed in a report in the first place. Only `describe()` reaches the report and it has
        no password field, and the connection-string pattern in `redact` removes any URL form that
        appears in captured output regardless of mode.
        """
        if self.mode != "hosted":
            return ()
        values = [self.password, self.project_ref, self.host, self.hostaddr]
        return tuple(v for v in values if v)

    def describe(self) -> dict[str, object]:
        """What the report may record about the target. No secret, no identifier, no address."""
        return {
            "mode": self.mode,
            "host_class": "loopback" if is_loopback(self.host) else "remote",
            "host_shown": self.host if is_loopback(self.host) else "[hosted-host-redacted]",
            "port": self.port,
            "user": self.user,
            "database": self.database,
            "sslmode": self.sslmode,
            "tls_verified_against_hostname": self.sslmode == "verify-full",
            "address_pinned_before_connect": self.hostaddr is not None,
            "project_ref_present": self.project_ref is not None,
            "statement_timeout_ms": self.statement_timeout_ms,
            "connect_timeout_s": self.connect_timeout_s,
            "guard_notes": list(self.guard_notes),
        }

    def child_env(self) -> dict[str, str]:
        """A libpq environment built from scratch. Nothing inherited can redirect this connection."""
        env: dict[str, str] = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": "C",
            "LC_ALL": "C",
            "PGHOST": self.host,
            "PGPORT": str(self.port),
            "PGUSER": self.user,
            "PGDATABASE": self.database,
            "PGSSLMODE": self.sslmode,
            "PGCONNECT_TIMEOUT": str(self.connect_timeout_s),
            "PGAPPNAME": "origenlab-slice0-audit",
            # The read-only default is set on the connection, so it is already in force when the
            # first statement of the transaction is parsed -- not applied by a statement the
            # transaction could have been opened without.
            "PGOPTIONS": (
                f"-c default_transaction_read_only=on "
                f"-c statement_timeout={self.statement_timeout_ms} "
                f"-c idle_in_transaction_session_timeout={self.statement_timeout_ms} "
                f"-c lock_timeout=5000"
            ),
        }
        if self.hostaddr:
            # host stays the name, so TLS verify-full checks the certificate against it; hostaddr
            # is the address this process validated, so libpq does not resolve the name again.
            env["PGHOSTADDR"] = self.hostaddr
        if self.sslrootcert:
            env["PGSSLROOTCERT"] = self.sslrootcert
        if self.password:
            env["PGPASSWORD"] = self.password
        return env


def inherited_scrubbed_names() -> list[str]:
    """Names present in this process that `child_env` deliberately does not pass on."""
    found = [
        name
        for name in os.environ
        if name in SCRUBBED_NAMES or name.startswith(SCRUBBED_PREFIXES)
    ]
    return sorted(found)
