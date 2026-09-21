"""The hosted target boundary.

Separate from the local one on purpose. `supabase/scripts/lib/local_target.sh` refuses everything
that is not loopback; this module refuses everything that *is*. Neither can be persuaded to do the
other's job, and neither shares a code path with it, so a mistake in one cannot widen the other.

A hosted target is assembled from a file the operator writes at one fixed, git-ignored path, never
from the environment, never from `supabase link` state, and never from a command-line host. Every
one of the following must hold before a connection is attempted:

* the target file exists at the approved path, is a regular file, is not a symlink, and is readable
  by nobody but its owner;
* the connection route is one of the two supported ones, **named in the target file and
  separately authorised by the caller**. There is no fallback: a route that is not authorised is
  refused, and no code path tries one route and then the other;
  - `direct` -- `db.<ref>.supabase.co` on 5432, login `origenlab_migrator`. IPv6 unless the
    project holds the IPv4 add-on, which is why it can be unreachable from an IPv4-only operator
    network;
  - `supavisor-session` -- the official shared Supavisor pooler in session mode,
    `aws-<cluster>-<region>.pooler.supabase.com` on 5432, login `origenlab_migrator.<ref>`. Session
    mode is the only pooler mode this audit can use: transaction mode (port 6543) discards
    session state between transactions and therefore cannot carry `SET LOCAL ROLE` and the
    transaction-local timeouts across the bank, so it is refused by name rather than degraded to;
* the login role is the configured audit identity and nothing else;
* `sslmode` is exactly `verify-full`, with a CA file that exists. There is no downgrade path: a
  missing or unreadable CA file refuses the run rather than falling back to `require`;
* the password comes from a named environment variable, so the credential stays external to this
  repository and never appears in a file the audit reads or writes;
* the host resolves, immediately before the connection, to addresses that are all globally
  routable. A name that resolves to loopback, a private range, link-local, CGNAT, multicast or any
  other reserved block is refused -- that is how a hosted audit gets pointed at something local.

**Time of check to time of use.** Validating a name and then handing the *name* to libpq would let
the answer change between the two: libpq would resolve it again, and the second answer is the one
that gets dialled. So the resolved address is pinned. libpq receives `PGHOSTADDR` (the address this
module validated) together with `PGHOST` (the name), which means no second resolution happens and
`verify-full` still checks the server certificate against the name. The window closes because the
address is decided once, by the code that checked it.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import stat
from pathlib import Path

from .target import (
    ROUTE_DIRECT,
    ROUTE_SUPAVISOR_SESSION,
    ROUTES,
    Target,
    TargetError,
)

# The one approved path for each untracked local file. Nothing else is read, and a file anywhere
# else is not picked up by accident.
TARGET_FILE = Path("supabase/.audit/hosted_target.env")
ATTESTATION_FILE = Path("supabase/.audit/attestation.json")
REPORT_DIR = Path("supabase/.audit/reports")
APPROVED_PATHS = (TARGET_FILE, ATTESTATION_FILE)

# Decision: origenlab_migrator is the configured hosted audit identity for this initial audit. It
# is not a fifth role and no migration creates one; the credential is external and the session is
# constrained read-only before any catalogue query runs.
AUDIT_IDENTITY = "origenlab_migrator"

# Route endpoints, as the current Supabase connection documentation defines them. Verified
# 2026-09-20 against supabase.com/docs/guides/database/connecting-to-postgres, the Supavisor
# troubleshooting entries, and the 2025-01-13 changelog that made port 6543 transaction-mode only.
DIRECT_PORT = 5432
DIRECT_HOST_PATTERN = re.compile(r"^db\.([a-z]{20})\.supabase\.co$")

# `aws-<cluster>-<region>.pooler.supabase.com`. The cluster index is a Supavisor cluster ordinal,
# not something derivable from the region -- the documentation says to copy the host from the
# project's Connect dialog -- so the operator declares both and this module proves the host agrees
# with what was declared rather than reconstructing it.
POOLER_HOST_PATTERN = re.compile(
    r"^aws-(?P<cluster>[0-9]{1,2})-(?P<region>[a-z]{2}-[a-z]+-[0-9]{1,2})\.pooler\.supabase\.com$"
)
SUPAVISOR_SESSION_PORT = 5432
SUPAVISOR_TRANSACTION_PORT = 6543

PROJECT_REF_PATTERN = re.compile(r"^[a-z]{20}$")
POOLER_CLUSTER_PATTERN = re.compile(r"^[0-9]{1,2}$")
POOLER_REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]{1,2}$")
REQUIRED_SSLMODE = "verify-full"

# Kept for callers and tests that ask "which route does this port belong to".
SUPPORTED_PORTS = frozenset({DIRECT_PORT, SUPAVISOR_SESSION_PORT})

COMMON_KEYS = (
    "OL_HOSTED_ROUTE",
    "OL_HOSTED_HOST",
    "OL_HOSTED_PORT",
    "OL_HOSTED_DATABASE",
    "OL_HOSTED_USER",
    "OL_HOSTED_PROJECT_REF",
    "OL_HOSTED_SSLMODE",
    "OL_HOSTED_SSLROOTCERT",
    "OL_HOSTED_PASSWORD_ENV",
)
POOLER_KEYS = (
    "OL_HOSTED_POOLER_CLUSTER",
    "OL_HOSTED_POOLER_REGION",
)
REQUIRED_KEYS = COMMON_KEYS


def required_keys_for(route: str) -> tuple[str, ...]:
    """The exact key set a target file must declare for `route` -- no more and no fewer.

    A direct target that declares a pooler key, or a pooler target that omits one, is ambiguous
    about where it points. Both are refused rather than resolved by precedence.
    """
    if route == ROUTE_SUPAVISOR_SESSION:
        return COMMON_KEYS + POOLER_KEYS
    return COMMON_KEYS


def pooler_login(project_ref: str, identity: str | None = None) -> str:
    """The one login name the Supavisor route accepts: `<audit identity>.<project ref>`.

    Supavisor routes a connection to a tenant by the project reference in the user name. That
    makes the login the *only* place the pooler target names its project -- the host name does not
    -- so it is this route's target-identity binding and is checked exactly, against the reference
    the target file declares. The reference appearing in the login is therefore not a reason to
    relax the identity check; it is the reason the check has to be stricter.
    """
    return f"{identity or AUDIT_IDENTITY}.{project_ref}"

_KEY_VALUE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def parse_target_file(text: str) -> dict[str, str]:
    """Parse the `KEY=value` target file. Comments and blank lines are ignored; nothing is executed.

    The file is never sourced by a shell. A value is taken literally, with at most one layer of
    surrounding quotes removed, so a target file cannot run a command or expand a variable.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_VALUE.match(line)
        if not match:
            raise TargetError(f"hosted target file line {number} is not KEY=value; refusing to connect")
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in values:
            raise TargetError(f"hosted target file declares {key} twice; refusing to connect")
        values[key] = value
    return values


def check_file_permissions(path: Path) -> None:
    """Refuse a target file that is a symlink, not a regular file, or readable by anyone else."""
    if path.is_symlink():
        raise TargetError(f"{path} is a symlink; the hosted target file must be a regular file")
    if not path.is_file():
        raise TargetError(f"{path} is missing or is not a regular file; refusing to connect")
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise TargetError(
            f"{path} is readable or writable by group or other (mode {stat.S_IMODE(mode):04o}); "
            "refusing to connect. Use chmod 600."
        )


def classify_address(address: str) -> str:
    """Return 'global' or the reserved class that makes an address unusable as a hosted target."""
    parsed = ipaddress.ip_address(address)
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_link_local:
        return "link-local"
    if parsed.is_multicast:
        return "multicast"
    if parsed.is_unspecified:
        return "unspecified"
    if parsed.is_reserved:
        return "reserved"
    if parsed.version == 4 and parsed in ipaddress.ip_network("100.64.0.0/10"):
        return "carrier-grade-nat"
    if parsed.is_private:
        return "private"
    if not parsed.is_global:
        return "non-global"
    return "global"


def resolve_addresses(host: str, port: int) -> list[str]:
    """Resolve `host` now. Name lookup only; the addresses are judged by `assert_all_global`."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise TargetError(
            f"the hosted host name did not resolve ({exc.__class__.__name__}); refusing to connect"
        ) from exc

    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def assert_all_global(addresses: list[str]) -> None:
    """Refuse unless every answer is globally routable.

    Every answer, not merely the one that will be dialled: a name that resolves to one public
    address and one loopback address is a rebinding attempt, and picking the good answer would
    make the audit's refusal depend on resolver ordering. This lives in `resolve` rather than in
    the resolver so that a substituted resolver -- a test double, or any future caller -- cannot
    step around it.
    """
    if not addresses:
        raise TargetError("the hosted host name resolved to no address; refusing to connect")
    for address in addresses:
        classification = classify_address(address)
        if classification != "global":
            raise TargetError(
                f"the hosted host name resolved to a {classification} address; refusing to "
                "connect. A hosted audit must never be pointed at a local or private destination."
            )


def _route_of(values: dict[str, str]) -> str:
    """The declared route, validated against the closed set. Never inferred from the host."""
    route = values.get("OL_HOSTED_ROUTE", "")
    if route not in ROUTES:
        raise TargetError(
            f"the hosted target file declares route '{route}', which is not one of "
            f"{sorted(ROUTES)}; refusing to connect"
        )
    return route


def _resolve_direct(values: dict[str, str], project_ref: str, port: int) -> tuple[str, list[str]]:
    """Validate the direct route and return (host, guard notes). Unchanged behaviour."""
    host = values["OL_HOSTED_HOST"]
    match = DIRECT_HOST_PATTERN.match(host)
    if not match:
        raise TargetError(
            "the hosted host is not a supported Supabase direct-connection name "
            "(db.<project-ref>.supabase.co); refusing to connect. The Supavisor route is selected "
            "by OL_HOSTED_ROUTE, never by the host name."
        )
    if match.group(1) != project_ref:
        raise TargetError(
            "the hosted host name and the declared project reference disagree; refusing to connect"
        )
    if port != DIRECT_PORT:
        raise TargetError(
            f"port {port} is not the direct route; the direct connection is {DIRECT_PORT} only"
        )
    user = values["OL_HOSTED_USER"]
    if user != AUDIT_IDENTITY:
        raise TargetError(
            f"the hosted login role is '{user}', not the configured audit identity "
            f"'{AUDIT_IDENTITY}'; refusing to connect"
        )
    return host, [
        f"hosted target: route direct db.<project-ref>.supabase.co:{port}, login {AUDIT_IDENTITY}",
    ]


def _resolve_supavisor_session(
    values: dict[str, str], project_ref: str, port: int
) -> tuple[str, str, str, list[str]]:
    """Validate the Supavisor session route.

    Returns (host, cluster, region, guard notes). Every one of the endpoint's parts is declared by
    the operator and proven against the host name, so a target that is right about the host and
    wrong about the region -- or right about the region and pointed at another cluster -- is
    refused as ambiguous rather than silently accepted on whichever field happened to be read.
    """
    host = values["OL_HOSTED_HOST"]
    match = POOLER_HOST_PATTERN.match(host)
    if not match:
        raise TargetError(
            "the hosted host is not the official Supabase shared-pooler name "
            "(aws-<cluster>-<region>.pooler.supabase.com); refusing to connect"
        )
    host_cluster = match.group("cluster")
    host_region = match.group("region")

    declared_cluster = values["OL_HOSTED_POOLER_CLUSTER"]
    if not POOLER_CLUSTER_PATTERN.match(declared_cluster):
        raise TargetError("OL_HOSTED_POOLER_CLUSTER is not a Supavisor cluster index; refusing to connect")
    if declared_cluster != host_cluster:
        raise TargetError(
            "the pooler host names a different Supavisor cluster than OL_HOSTED_POOLER_CLUSTER "
            "declares; the target is ambiguous and is refused"
        )

    declared_region = values["OL_HOSTED_POOLER_REGION"]
    if not POOLER_REGION_PATTERN.match(declared_region):
        raise TargetError("OL_HOSTED_POOLER_REGION is not an AWS region name; refusing to connect")
    if declared_region != host_region:
        raise TargetError(
            "the pooler host names a different region than OL_HOSTED_POOLER_REGION declares; the "
            "target is ambiguous and is refused"
        )

    if port == SUPAVISOR_TRANSACTION_PORT:
        raise TargetError(
            f"port {SUPAVISOR_TRANSACTION_PORT} is the Supavisor *transaction* mode endpoint. "
            "Transaction mode does not keep session state between transactions, so it cannot "
            "carry this audit's SET LOCAL ROLE and transaction-local timeouts across the check "
            "bank; the read-only contract cannot be established through it and the route is "
            f"refused rather than degraded. Session mode is port {SUPAVISOR_SESSION_PORT}."
        )
    if port != SUPAVISOR_SESSION_PORT:
        raise TargetError(
            f"port {port} is not the Supavisor session-mode endpoint "
            f"({SUPAVISOR_SESSION_PORT}); refusing to connect"
        )

    user = values["OL_HOSTED_USER"]
    expected = pooler_login(project_ref)
    if user != expected:
        # The message never prints either login: both carry the project reference.
        raise TargetError(
            "the Supavisor login is not '<audit identity>.<declared project ref>' for the "
            f"configured audit identity '{AUDIT_IDENTITY}'; refusing to connect. The pooler login "
            "is this route's target-identity binding, because the pooler host names no project."
        )

    # The note names the cluster and the region but never reassembles the host: `describe()`
    # promises a report no host name, and a note that spells one out would break that promise from
    # the inside.
    return host, host_cluster, host_region, [
        f"hosted target: route supavisor-session, official shared pooler cluster "
        f"aws-{host_cluster} in {host_region}, session mode on {port} (transaction mode "
        f"{SUPAVISOR_TRANSACTION_PORT} is refused, never fallen back to)",
        f"hosted target: pooler login {AUDIT_IDENTITY}.<project-ref>, proven equal to the "
        "declared project reference",
    ]


def resolve(
    repo_root: Path,
    environ: dict[str, str],
    *,
    target_file: Path | None = None,
    resolver=resolve_addresses,
    authorized_routes: frozenset[str] = frozenset({ROUTE_DIRECT}),
) -> Target:
    """Build and validate a hosted target. Raises TargetError before any connection is possible.

    `authorized_routes` is the caller's explicit permission, not a preference. The default admits
    the direct route only, so a caller that says nothing can never reach the pooler: selecting the
    Supavisor route takes both a declaration in the target file and an authorisation here, and the
    two must agree.
    """
    path = repo_root / (target_file or TARGET_FILE)
    check_file_permissions(path)
    values = parse_target_file(path.read_text(encoding="utf-8"))

    route = _route_of(values)
    if route not in authorized_routes:
        raise TargetError(
            f"the hosted target file declares route '{route}', which this run did not authorise "
            f"(authorised: {sorted(authorized_routes)}). A route is selected explicitly; the audit "
            "never falls back from one route to another."
        )

    expected_keys = required_keys_for(route)
    missing = [key for key in expected_keys if not values.get(key)]
    if missing:
        raise TargetError(f"hosted target file is missing {', '.join(missing)}; refusing to connect")
    unknown = sorted(set(values) - set(expected_keys))
    if unknown:
        raise TargetError(
            f"hosted target file declares key(s) {', '.join(unknown)} that the '{route}' route "
            "does not take; the target is ambiguous and is refused"
        )

    project_ref = values["OL_HOSTED_PROJECT_REF"]
    if not PROJECT_REF_PATTERN.match(project_ref):
        raise TargetError("the declared project reference is not twenty lowercase letters; refusing to connect")

    try:
        port = int(values["OL_HOSTED_PORT"])
    except ValueError as exc:
        raise TargetError("the hosted port is not a number; refusing to connect") from exc

    pooler_cluster: str | None = None
    pooler_region: str | None = None
    if route == ROUTE_SUPAVISOR_SESSION:
        host, pooler_cluster, pooler_region, route_notes = _resolve_supavisor_session(
            values, project_ref, port
        )
    else:
        host, route_notes = _resolve_direct(values, project_ref, port)

    sslmode = values["OL_HOSTED_SSLMODE"]
    if sslmode != REQUIRED_SSLMODE:
        raise TargetError(
            f"sslmode is '{sslmode}'; a hosted audit requires '{REQUIRED_SSLMODE}' and has no "
            "downgrade path"
        )

    sslrootcert = Path(values["OL_HOSTED_SSLROOTCERT"]).expanduser()
    if not sslrootcert.is_file():
        raise TargetError(
            f"the TLS root certificate {sslrootcert} does not exist; refusing to connect rather "
            "than falling back to an unverified connection"
        )

    password_env = values["OL_HOSTED_PASSWORD_ENV"]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", password_env):
        raise TargetError("OL_HOSTED_PASSWORD_ENV must name an environment variable; refusing to connect")
    password = environ.get(password_env, "")
    if not password:
        raise TargetError(
            f"the environment variable {password_env} is unset or empty, so no credential is "
            "available; refusing to connect. The credential stays outside this repository."
        )

    addresses = resolver(host, port)
    assert_all_global(addresses)

    return Target(
        mode="hosted",
        host=host,
        hostaddr=addresses[0],
        port=port,
        user=values["OL_HOSTED_USER"],
        database=values["OL_HOSTED_DATABASE"],
        sslmode=sslmode,
        sslrootcert=str(sslrootcert),
        password=password,
        project_ref=project_ref,
        route=route,
        pooler_cluster=pooler_cluster,
        pooler_region=pooler_region,
        guard_notes=[
            f"hosted target: file {path.relative_to(repo_root)} mode 0600, regular file",
            *route_notes,
            f"hosted target: sslmode {sslmode} against a CA file that exists, so the server "
            "certificate and its host name are both verified",
            f"hosted target: {len(addresses)} resolved address(es), all globally routable, "
            "the first pinned as PGHOSTADDR so libpq does not resolve the name again",
        ],
    )
