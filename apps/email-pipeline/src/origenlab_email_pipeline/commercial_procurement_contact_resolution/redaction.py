"""Redaction helpers for PR5E shareable artifacts (no raw PII)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b", re.I
)


def stable_token(label: str, value: str | None, *, n: int = 12) -> str:
    raw = f"{label}|{(value or '').strip().casefold()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:n]


def redact_email(value: str | None) -> str | None:
    if not value:
        return None
    return f"email_{stable_token('email', value)}"


def redact_domain(value: str | None) -> str | None:
    if not value:
        return None
    return f"domain_{stable_token('domain', value)}"


def redact_org(value: str | None) -> str | None:
    if not value:
        return None
    return f"org_{stable_token('org', value)}"


def redact_id(label: str, value: str | None) -> str | None:
    if not value:
        return None
    return f"{label}_{stable_token(label, value)}"


def redact_text_blob(text: str) -> str:
    out = _EMAIL_RE.sub(lambda m: redact_email(m.group(0)) or "[email]", text)
    out = _DOMAIN_RE.sub(lambda m: redact_domain(m.group(0)) or "[domain]", out)
    return out


def assert_no_raw_pii(blob: str, *, forbidden_substrings: list[str] | None = None) -> None:
    if _EMAIL_RE.search(blob):
        raise AssertionError("raw email leaked into shareable artifact")
    for item in forbidden_substrings or []:
        if item and item in blob:
            raise AssertionError(f"forbidden substring leaked: {item[:32]}")


def redact_summary_for_share(summary: dict[str, Any]) -> dict[str, Any]:
    """Aggregate counts-only shareable view — strip identity-bearing fields."""
    allowed = {
        "ok",
        "as_of_utc",
        "run_context",
        "planner_version",
        "not_persisted",
        "counts",
        "fingerprints",
        "dependency_fingerprints",
        "reconciliation",
        "field_sufficiency_audit",
    }
    return {k: summary[k] for k in allowed if k in summary}
