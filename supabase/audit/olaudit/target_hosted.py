"""The hosted target boundary.

Separate from the local one on purpose. `supabase/scripts/lib/local_target.sh` refuses everything
that is not loopback; this module refuses everything that *is*. Neither can be persuaded to do the
other's job, and neither shares a code path with it, so a mistake in one cannot widen the other.

A hosted target is assembled from a file the operator writes at one fixed, git-ignored path, never
from the environment, never from `supabase link` state, and never from a command-line host. Every
one of the following must hold before a connection is attempted:

* the target file exists at the approved path, is a regular file, is not a symlink, and is readable
  by nobody but its owner;
* the connection route is the supported one -- a direct `db.<ref>.supabase.co` connection on 5432.
  The Supavisor pooler is out of scope for this PR because it requires the login name to carry the
  project reference, which conflicts with the pinned audit identity;
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

from .target import Target, TargetError

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

SUPPORTED_PORTS = frozenset({5432})
HOST_PATTERN = re.compile(r"^db\.([a-z]{20})\.supabase\.co$")
PROJECT_REF_PATTERN = re.compile(r"^[a-z]{20}$")
REQUIRED_SSLMODE = "verify-full"

REQUIRED_KEYS = (
    "OL_HOSTED_HOST",
    "OL_HOSTED_PORT",
    "OL_HOSTED_DATABASE",
    "OL_HOSTED_USER",
    "OL_HOSTED_PROJECT_REF",
    "OL_HOSTED_SSLMODE",
    "OL_HOSTED_SSLROOTCERT",
    "OL_HOSTED_PASSWORD_ENV",
)

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


def resolve(
    repo_root: Path,
    environ: dict[str, str],
    *,
    target_file: Path | None = None,
    resolver=resolve_addresses,
) -> Target:
    """Build and validate a hosted target. Raises TargetError before any connection is possible."""
    path = repo_root / (target_file or TARGET_FILE)
    check_file_permissions(path)
    values = parse_target_file(path.read_text(encoding="utf-8"))

    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise TargetError(f"hosted target file is missing {', '.join(missing)}; refusing to connect")
    unknown = sorted(set(values) - set(REQUIRED_KEYS))
    if unknown:
        raise TargetError(f"hosted target file declares unknown key(s) {', '.join(unknown)}; refusing to connect")

    host = values["OL_HOSTED_HOST"]
    host_match = HOST_PATTERN.match(host)
    if not host_match:
        raise TargetError(
            "the hosted host is not a supported Supabase direct-connection name "
            "(db.<project-ref>.supabase.co); refusing to connect. The Supavisor pooler is out of "
            "scope for this audit."
        )

    project_ref = values["OL_HOSTED_PROJECT_REF"]
    if not PROJECT_REF_PATTERN.match(project_ref):
        raise TargetError("the declared project reference is not twenty lowercase letters; refusing to connect")
    if host_match.group(1) != project_ref:
        raise TargetError(
            "the hosted host name and the declared project reference disagree; refusing to connect"
        )

    try:
        port = int(values["OL_HOSTED_PORT"])
    except ValueError as exc:
        raise TargetError("the hosted port is not a number; refusing to connect") from exc
    if port not in SUPPORTED_PORTS:
        raise TargetError(
            f"port {port} is not a supported route; this audit supports the direct connection on "
            f"{sorted(SUPPORTED_PORTS)[0]} only"
        )

    user = values["OL_HOSTED_USER"]
    if user != AUDIT_IDENTITY:
        raise TargetError(
            f"the hosted login role is '{user}', not the configured audit identity "
            f"'{AUDIT_IDENTITY}'; refusing to connect"
        )

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
        user=user,
        database=values["OL_HOSTED_DATABASE"],
        sslmode=sslmode,
        sslrootcert=str(sslrootcert),
        password=password,
        project_ref=project_ref,
        guard_notes=[
            f"hosted target: file {path.relative_to(repo_root)} mode 0600, regular file",
            f"hosted target: route direct db.<project-ref>.supabase.co:{port}, "
            f"login {AUDIT_IDENTITY}",
            f"hosted target: sslmode {sslmode} against a CA file that exists",
            f"hosted target: {len(addresses)} resolved address(es), all globally routable, "
            "the first pinned as PGHOSTADDR so libpq does not resolve the name again",
        ],
    )
