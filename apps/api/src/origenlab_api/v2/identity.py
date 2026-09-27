"""Operator identity for the V2 read boundary — one port, two adapters.

`docs/ARCHITECTURE.md` makes Supabase Auth and JWKS verification the operator identity for
V2, and that is slice 1, which has not been built. The V2 read APIs are being built first,
under the hosted freeze (`docs/OPERATIONS.md` §1.1), so they need an identity that is honest
about which of those two facts applies today.

The answer is a port with two adapters:

* :class:`JwksVerifier` is the target. It is present, it is the default whenever a JWKS URL
  is configured, and it is **not configured anywhere** — no hosted credential is added while
  the freeze is in force.
* :class:`GoogleSessionIdentity` resolves an operator from the signed dashboard session
  cookie that Google Workspace sign-in sets (`auth_routes.py`). It is the only adapter a
  deployed environment may use while JWKS is unconfigured.
* :class:`LocalDevIdentity` resolves an operator from a request header, and **refuses to
  load unless the V2 database target is loopback**. It is a development adapter that cannot
  be switched on against a hosted database by changing one environment variable, because the
  check is on the database it would be reading, not on a mode flag. It is also **opt-in**:
  it exists only when `ORIGENLAB_DEV_LOGIN_ENABLED=true`, and that setting is refused
  outright when `ORIGENLAB_ENV=production`.

That refusal is the whole point of the design. A "dev mode" that is one variable away from
production is not a boundary; a dev mode that inspects the target and declines is. It also
satisfies the portability requirement: nothing here hardcodes localhost in a way that blocks
later hosted configuration — the hosted adapter is written, tested and simply unconfigured.
"""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.auth_session import CookieRefused, CookieSigner, read_cookie

if TYPE_CHECKING:
    from origenlab_api.v2.google_oidc import GoogleAuthConfig


class IdentityRefused(Exception):
    """The request carries no identity this boundary will accept."""


class IdentityAbsent(IdentityRefused):
    """The request carries no credential of this adapter's kind at all.

    Distinct from a credential that is present and wrong: :class:`ChainedIdentity` moves on
    to the next adapter only on this, so an expired session or a disabled operator is refused
    outright rather than quietly retried under a different identity.
    """


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
    #: Which adapter resolved this operator: `google_session`, `dev_header`, or `unknown`.
    auth_method: str = "unknown"

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

    def __init__(
        self, database_url: str, lookup: "OperatorLookup", *, production: bool = False
    ) -> None:
        if production:
            raise IdentityMisconfigured(
                "the local development identity adapter refuses to load when "
                "ORIGENLAB_ENV=production: a deployed API never trusts the operator header"
            )
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
            raise IdentityAbsent(
                f"no operator identity: set the {self.HEADER} header, or configure JWKS "
                "verification"
            )
        operator = self._lookup.by_email(email)
        if operator is None:
            raise IdentityRefused("no platform.operator row matches that address")
        return replace(operator, auth_method="dev_header").require_active()


class GoogleSessionIdentity(IdentityPort):
    """Resolve an operator from the signed dashboard session cookie.

    The cookie is only ever minted by the Google callback, after the ID token's claims were
    checked and the address was found in `platform.operator` (`auth_routes.py`). It is not
    trusted as a standing grant: every request re-reads the operator row, so a disabled
    operator is refused on the next request, and a row whose id no longer matches the one the
    session was issued for — an address reassigned to someone else — is refused too.
    """

    def __init__(self, signer: CookieSigner, cookie_name: str, lookup: "OperatorLookup") -> None:
        self._signer = signer
        self._cookie_name = cookie_name
        self._lookup = lookup

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        value = read_cookie(headers, self._cookie_name)
        if value is None:
            raise IdentityAbsent("no dashboard session: sign in with Google Workspace")
        try:
            session = self._signer.load_session(value)
        except CookieRefused as exc:
            raise IdentityRefused(
                "the dashboard session is invalid or expired: sign in again"
            ) from exc
        operator = self._lookup.by_email(session["email"])
        if operator is None:
            raise IdentityRefused("no platform.operator row matches the signed-in address")
        if operator.operator_id != session["operator_id"]:
            raise IdentityRefused(
                "the operator row for the signed-in address has changed: sign in again"
            )
        return replace(operator, auth_method="google_session").require_active()


class ChainedIdentity(IdentityPort):
    """Try each adapter in order; move on only when a credential is absent."""

    def __init__(self, adapters: list[IdentityPort]) -> None:
        if not adapters:
            raise IdentityMisconfigured("an identity chain needs at least one adapter")
        self._adapters = list(adapters)

    @property
    def adapters(self) -> tuple[IdentityPort, ...]:
        return tuple(self._adapters)

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        absent: list[str] = []
        for adapter in self._adapters:
            try:
                return adapter.resolve(headers)
            except IdentityAbsent as exc:
                absent.append(str(exc))
        raise IdentityAbsent("; ".join(absent))


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
    *,
    jwks_url: str | None,
    database_url: str,
    lookup: OperatorLookup,
    google: "GoogleAuthConfig | None" = None,
    dev_login_enabled: bool = False,
    production: bool = False,
) -> IdentityPort:
    """Choose the adapter.

    JWKS wins whenever it is configured, so a configured deployment can never fall back to
    the development adapter by accident. Otherwise the Google session adapter is used when
    Google sign-in is on, and the header adapter only when `dev_login_enabled` says so — it
    is never chosen by default, is refused in production, and then applies its own loopback
    test. Neither switched on is a startup failure, not an API that refuses every request.
    """
    if (jwks_url or "").strip():
        return JwksVerifier(jwks_url, lookup)
    if dev_login_enabled and production:
        raise IdentityMisconfigured(
            "ORIGENLAB_DEV_LOGIN_ENABLED is refused when ORIGENLAB_ENV=production: a "
            "deployed API never resolves an operator from a request header"
        )
    adapters: list[IdentityPort] = []
    if google is not None:
        adapters.append(GoogleSessionIdentity(google.signer, google.cookie_names.session, lookup))
    if dev_login_enabled:
        adapters.append(LocalDevIdentity(database_url, lookup, production=production))
    if not adapters:
        raise IdentityMisconfigured(
            "the V2 boundary has no operator identity: set ORIGENLAB_GOOGLE_AUTH_ENABLED=true "
            "(with its settings), or ORIGENLAB_DEV_LOGIN_ENABLED=true for local development "
            "against a loopback database"
        )
    return adapters[0] if len(adapters) == 1 else ChainedIdentity(adapters)
