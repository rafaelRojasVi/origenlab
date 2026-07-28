"""PII redaction helpers for committed fixtures and sanitized summaries."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b")


def normalize_email(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    return s or None


def email_domain(email: str | None) -> str | None:
    em = normalize_email(email)
    if not em or "@" not in em:
        return None
    return em.rsplit("@", 1)[-1].strip().lower() or None


def redact_email(email: str | None, *, salt: str = "commercial_truth_audit") -> str:
    """Deterministic redaction suitable for committed samples (never raw production PII)."""
    em = normalize_email(email)
    if not em:
        return ""
    dig = hashlib.sha256(f"{salt}:{em}".encode("utf-8")).hexdigest()[:12]
    dom = email_domain(em) or "unknown"
    local = em.split("@", 1)[0]
    local_hint = (local[:2] + "***") if len(local) >= 2 else "***"
    return f"{local_hint}@{dom}#{dig}"


def redact_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return _EMAIL_RE.sub(lambda m: redact_email(m.group(0)), text)


def redact_row(row: dict[str, Any], *, email_fields: tuple[str, ...] = ("email",)) -> dict[str, Any]:
    out = dict(row)
    for field in email_fields:
        if field in out and out[field]:
            out[field] = redact_email(str(out[field]))
    for key, val in list(out.items()):
        if isinstance(val, str) and "@" in val and key not in email_fields:
            out[key] = redact_text(val)
    return out


def assert_no_raw_production_emails(payload: str, *, forbidden_domains: frozenset[str] | None = None) -> None:
    """Raise if payload appears to contain unredacted addresses on forbidden domains."""
    domains = forbidden_domains or frozenset()
    for match in _EMAIL_RE.finditer(payload):
        em = match.group(0).lower()
        if "#" in em:
            continue  # already redacted form
        dom = email_domain(em)
        if dom and dom in domains:
            raise AssertionError(f"Unredacted production-like email domain in output: {dom}")
