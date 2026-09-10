"""The local target: the existing bash guard, plus an independent second opinion.

`supabase/scripts/lib/local_target.sh` already decides what "local" means, and it is the only thing
in this repository allowed to: four independent facts must agree (this project's `config.toml`, a
null `linked_project`, a container started from *this* working tree, and a loopback URL on this
project's port) before it will produce a URL. This module does not reimplement any of that. It runs
the guard through `resolve_local_target.sh` and takes the URL the guard produced.

It then parses that URL and refuses it a second time if the host is not a loopback address. That is
deliberate duplication. The bash guard proves it to bash; this proves it in the process that will
actually build the connection, and it is the assertion the unit tests exercise -- so "local mode
cannot reach a remote host" is a property of the audit engine itself, not only of a script it
happens to call.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import redact
from .target import Target, TargetError, is_loopback

_URL = re.compile(
    r"^postgres(?:ql)?://"
    r"(?P<user>[^:@/?#]+)"
    r"(?::(?P<password>[^@/]*))?"
    r"@(?P<host>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+)"
    r":(?P<port>[0-9]{1,5})"
    r"/(?P<database>[A-Za-z0-9_$-]+)"
    r"(?:\?.*)?$"
)

BRIDGE = Path("supabase/scripts/lib/resolve_local_target.sh")


def parse_url(url: str) -> dict[str, str]:
    """Strictly parse a local database URL. Anything unmatched is refused, never guessed at."""
    match = _URL.match(url.strip())
    if not match:
        raise TargetError("the local target URL did not parse; refusing to connect")
    parts = match.groupdict()
    return {
        "user": parts["user"],
        "password": parts["password"] or "",
        "host": parts["host"],
        "port": parts["port"],
        "database": parts["database"],
    }


def resolve(repo_root: Path, *, timeout: int = 120) -> Target:
    """Run the bash guard and return a validated loopback target.

    Raises TargetError before any connection is possible if the guard fails or if the URL it
    produced is not loopback.
    """
    bridge = repo_root / BRIDGE
    if not bridge.is_file():
        raise TargetError(f"{BRIDGE} is missing; the local target cannot be resolved")

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["/usr/bin/env", "bash", str(bridge)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TargetError("the local target guard timed out; refusing to connect") from exc

    guard_output = redact.tail(completed.stderr or "", lines=8)
    if completed.returncode != 0:
        raise TargetError(
            "the local target guard refused this target; nothing was executed.\n" + guard_output
        )

    parts = parse_url(completed.stdout)

    if not is_loopback(parts["host"]):
        raise TargetError(
            f"local mode resolved a non-loopback host ({parts['host']}); refusing to connect"
        )

    return Target(
        mode="local",
        host=parts["host"],
        hostaddr=None,
        port=int(parts["port"]),
        user=parts["user"],
        database=parts["database"],
        sslmode="disable",
        sslrootcert=None,
        password=parts["password"],
        project_ref=None,
        guard_notes=[line for line in guard_output.splitlines() if line.strip()],
    )
