"""Shared redaction and leak assertions.

One module, used by every path that can emit text: the report writers, the terminal logger and the
child-process error handler. There is deliberately no second, looser sanitiser anywhere in the
audit, and no option that turns redaction off — a `--debug-no-redact` switch would make the
guarantee conditional on an operator remembering not to use it.

Two mechanisms, in this order:

1. **Literal redaction of known secrets.** Whatever the process actually holds — the database
   password, the project reference, the hosted host name, the resolved address — is replaced by an
   exact string match. This is the primary mechanism: it does not depend on a pattern being right.

2. **Pattern redaction.** A safety net for material the process never knew it had: connection
   strings, Supabase keys in every current and legacy format, JWTs, Supabase host names and
   non-loopback IP addresses.

`assert_clean` then re-reads the finished text and fails closed if either mechanism still finds
something. A report that cannot be proven clean is not written and is never uploaded.

Loopback literals are deliberately NOT redacted. `127.0.0.1` and `::1` are evidence — they are how
a local run proves it never left the machine — and they identify nothing.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable, NamedTuple

REDACTED = "[redacted]"

_LOOPBACK_LITERALS = frozenset({"127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})


class Pattern(NamedTuple):
    """A named redaction rule. `kind` is what a leak report calls it."""

    kind: str
    regex: re.Pattern[str]
    replacement: str


# Order matters: a connection string is redacted whole before its host or password can be matched
# in isolation and replaced piecemeal.
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "connection-string",
        re.compile(r"postgres(?:ql)?://[^\s\"'<>]*", re.IGNORECASE),
        "postgresql://[redacted]",
    ),
    Pattern(
        "password-assignment",
        re.compile(
            r"\b(password|pgpassword|passwd|pwd)\b(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)",
            re.IGNORECASE,
        ),
        r"\1\2[redacted]",
    ),
    Pattern(
        "supabase-secret-key",
        re.compile(r"\bsb_secret_[A-Za-z0-9_\-]+", re.IGNORECASE),
        "sb_secret_[redacted]",
    ),
    Pattern(
        "supabase-publishable-key",
        re.compile(r"\bsb_publishable_[A-Za-z0-9_\-]+", re.IGNORECASE),
        "sb_publishable_[redacted]",
    ),
    Pattern(
        "supabase-access-token",
        re.compile(r"\bsbp_[A-Za-z0-9]{16,}", re.IGNORECASE),
        "sbp_[redacted]",
    ),
    Pattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"),
        "[jwt-redacted]",
    ),
    Pattern(
        "supabase-host",
        re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._-]*\.supabase\.(?:co|com|net|in|red)\b", re.IGNORECASE),
        "[hosted-host-redacted]",
    ),
    Pattern(
        "project-ref",
        re.compile(
            r"\b(project[-_]?ref|project[-_]?id|ref)(\s*[=:]\s*)(\"?)([a-z]{20})(\"?)",
            re.IGNORECASE,
        ),
        r"\1\2\3[project-ref-redacted]\5",
    ),
)

# Matched separately so loopback addresses can be kept as evidence.
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b")


def _is_loopback_or_unroutable_text(text: str) -> bool:
    if text in _LOOPBACK_LITERALS:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _redact_addresses(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        literal = match.group(0)
        if _is_loopback_or_unroutable_text(literal):
            return literal
        try:
            ipaddress.ip_address(literal)
        except ValueError:
            return literal
        return "[ip-redacted]"

    return _IPV6.sub(replace, _IPV4.sub(replace, text))


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Redact `text`: known literals first, then patterns, then non-loopback addresses."""
    if not text:
        return text
    out = text
    # Longest first, so a project reference inside a host name is not half-replaced.
    for secret in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    for pattern in PATTERNS:
        out = pattern.regex.sub(pattern.replacement, out)
    return _redact_addresses(out)


class Leak(NamedTuple):
    kind: str
    excerpt: str


def find_leaks(text: str, secrets: Iterable[str] = ()) -> list[Leak]:
    """Return everything in `text` that redaction should have removed. Empty means clean.

    The excerpt is itself redacted, so a leak report never reprints the thing that leaked.
    """
    leaks: list[Leak] = []
    if not text:
        return leaks
    for secret in {s for s in secrets if s and len(s) >= 4}:
        if secret in text:
            leaks.append(Leak("known-secret", "<a value the audit holds appears verbatim>"))
    for pattern in PATTERNS:
        for match in pattern.regex.finditer(text):
            excerpt = match.group(0)
            if pattern.kind == "connection-string" and excerpt == "postgresql://[redacted]":
                continue
            if pattern.kind == "password-assignment" and REDACTED in excerpt:
                continue
            if pattern.kind == "project-ref" and "[project-ref-redacted]" in excerpt:
                continue
            leaks.append(Leak(pattern.kind, redact(excerpt, secrets)))
    for match in _IPV4.finditer(text):
        if not _is_loopback_or_unroutable_text(match.group(0)):
            try:
                ipaddress.ip_address(match.group(0))
            except ValueError:
                continue
            leaks.append(Leak("ip-address", "[ip-redacted]"))
    return leaks


def assert_clean(text: str, secrets: Iterable[str] = (), *, what: str = "output") -> None:
    """Raise if `text` still contains anything redaction should have removed."""
    leaks = find_leaks(text, secrets)
    if leaks:
        kinds = sorted({leak.kind for leak in leaks})
        raise LeakError(
            f"{what} failed the credential/project-reference leak assertions: "
            f"{len(leaks)} match(es) of kind(s) {', '.join(kinds)}"
        )


class LeakError(RuntimeError):
    """Raised when text that is about to be written or displayed fails the leak assertions."""


def tail(text: str, lines: int = 12, secrets: Iterable[str] = ()) -> str:
    """The redacted last `lines` of a captured log — the only shape child output may be shown in."""
    redacted = redact(text, secrets)
    return "\n".join(redacted.splitlines()[-lines:])
