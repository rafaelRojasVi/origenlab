"""Operator identity for the V2 read boundary — one port, two adapters.

`docs/ARCHITECTURE.md` makes Supabase Auth and JWKS verification the operator identity for
V2, and that is slice 1, which has not been built. The V2 read APIs are being built first,
under the hosted freeze (`docs/OPERATIONS.md` §1.1), so they need an identity that is honest
about which of those two facts applies today.

The answer is a port with two adapters:

* :class:`JwksVerifier` is the target. It is present, it is the default whenever a JWKS URL
  is configured, and it is **not configured anywhere** — no hosted credential is added while
  the freeze is in force.
* :class:`LocalDevIdentity` resolves an operator from a request header, and **refuses to
  load unless the V2 database target is loopback**. It is a development adapter that cannot
  be switched on against a hosted database by changing one environment variable, because the
  check is on the database it would be reading, not on a mode flag.

That refusal is the whole point of the design. A "dev mode" that is one variable away from
production is not a boundary; a dev mode that inspects the target and declines is. It also
satisfies the portability requirement: nothing here hardcodes localhost in a way that blocks
later hosted configuration — the hosted adapter is written, tested and simply unconfigured.
"""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER


class IdentityRefused(Exception):
    """The request carries no identity this boundary will accept."""


class IdentityMisconfigured(Exception):
    """The adapter cannot be constructed at all. Raised at startup, never per request."""


@dataclass(frozen=True)
class OperatorIdentity:
    """A resolved `platform.operator`."""

    operator_id: str
    email_norm: str
    display_name: str
    role: str
    status: str

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def require_active(self) -> "OperatorIdentity":
        # `docs/OPERATIONS.md` §2: a disabled operator is refused at the boundary even with a
        # valid, unexpired token.
        if not self.is_active:
            raise IdentityRefused("operator is disabled")
        return self

    def require_role(self, *allowed: str) -> "OperatorIdentity":
        if self.role not in allowed:
            raise IdentityRefused(
                f"operator role '{self.role}' is not permitted here (need one of {', '.join(allowed)})"
            )
        return self


class IdentityPort(ABC):
    """Resolve an operator from request headers, or refuse."""

    @abstractmethod
    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:  # pragma: no cover - ABC
        ...


def is_loopback_dsn(dsn: str) -> bool:
    """True only when the DSN names a literal loopback address.

    A host *name* is refused, `localhost` included: a name is resolved by something outside
    this process, and what it resolves to is not a property of the string we were given.
    This is the same rule the migration tooling applies, and it is deliberately the same
    rule — a boundary that is stricter in one place and laxer in another is one boundary,
    at its laxest point.
    """
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return False
    if parts.scheme not in {"postgres", "postgresql"}:
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalDevIdentity(IdentityPort):
    """Resolve an operator from the trusted operator header, for local development only.

    Refuses to be constructed unless the V2 database it would read is on a literal loopback
    address. There is no override.

    **It reads the same header the V1 command path reads, and that is not a convenience.**
    `apps/dashboard-proxy` deletes exactly one inbound operator header and reconstructs it
    from the Cloudflare Access authenticated identity — that header, by name. A V2 boundary
    that invented its own header name would be handed a **browser-supplied** value the proxy
    has no reason to strip, and any signed-in user could then impersonate any operator. The
    constant is imported rather than repeated so the two can never drift apart.
    """

    HEADER = OPERATOR_EMAIL_HEADER.lower()

    def __init__(self, database_url: str, lookup: "OperatorLookup") -> None:
        if not is_loopback_dsn(database_url):
            raise IdentityMisconfigured(
                "the local development identity adapter refuses to load: the V2 database is "
                "not on a literal loopback address. Configure JWKS verification instead; "
                "there is no override flag."
            )
        self._lookup = lookup

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        normalized = {k.lower(): v for k, v in headers.items()}
        email = (normalized.get(self.HEADER) or "").strip().lower()
        if not email:
            raise IdentityRefused(
                f"no operator identity: set the {self.HEADER} header, or configure JWKS "
                "verification"
            )
        operator = self._lookup.by_email(email)
        if operator is None:
            raise IdentityRefused("no platform.operator row matches that address")
        return operator.require_active()


class JwksVerifier(IdentityPort):
    """Verify a Supabase Auth JWT against the project's JWKS — the V2 target.

    Present and unconfigured. Slice 1 wires it up; until then constructing it without a
    JWKS URL raises at startup rather than silently degrading to something weaker.
    """

    def __init__(self, jwks_url: str | None, lookup: "OperatorLookup") -> None:
        if not (jwks_url or "").strip():
            raise IdentityMisconfigured(
                "JWKS verification requires a JWKS URL. None is configured, and none is "
                "added while the hosted phase is frozen (OPERATIONS.md 1.1)."
            )
        self._jwks_url = jwks_url
        self._lookup = lookup

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:  # pragma: no cover
        raise IdentityMisconfigured(
            "JWKS verification is not implemented yet: it arrives with Supabase Auth in "
            "migration slice 1. This adapter exists so the boundary has a target to be "
            "configured against, not so it can be used today."
        )


class OperatorLookup(ABC):
    """Read a `platform.operator` row. Implemented by the repository layer."""

    @abstractmethod
    def by_email(self, email_norm: str) -> OperatorIdentity | None:  # pragma: no cover - ABC
        ...


def build_identity_port(
    *, jwks_url: str | None, database_url: str, lookup: OperatorLookup
) -> IdentityPort:
    """Choose the adapter.

    JWKS wins whenever it is configured, so a configured deployment can never fall back to
    the development adapter by accident. Only when no JWKS URL exists at all is the local
    adapter considered — and it then applies its own loopback test.
    """
    if (jwks_url or "").strip():
        return JwksVerifier(jwks_url, lookup)
    return LocalDevIdentity(database_url, lookup)
