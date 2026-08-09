"""Guards that keep opaque portal tokens out of shareable evidence artifacts."""

from __future__ import annotations

import re

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    FORBIDDEN_ARTIFACT_SUBSTRINGS,
)

_TOKEN_QUERY_RE = re.compile(r"(?i)\b(qs|enc|ticket)=[^&\s\"'<>]*")


class ArtifactRedactionError(AssertionError):
    """A shareable artifact still carried a portal token."""


def redact_portal_tokens(text: str) -> str:
    """Replace ``qs=``/``enc=``/``ticket=`` values anywhere in free text."""
    if not text:
        return text
    return _TOKEN_QUERY_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)


def assert_no_portal_tokens(serialized: str, *, where: str) -> None:
    """Fail loudly before publishing rather than leaking an opaque token."""
    lowered = serialized.lower()
    for needle in FORBIDDEN_ARTIFACT_SUBSTRINGS:
        index = lowered.find(needle)
        while index != -1:
            tail = lowered[index + len(needle) : index + len(needle) + 12]
            if not tail.startswith("<redacted>"):
                raise ArtifactRedactionError(
                    f"portal token {needle!r} found in {where}"
                )
            index = lowered.find(needle, index + 1)
