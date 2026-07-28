"""Normalization helpers for commercial identity — reuse existing repository utilities."""

from __future__ import annotations

from origenlab_email_pipeline.business_mart import domain_of
from origenlab_email_pipeline.commercial_identity.constants import CONSUMER_EMAIL_DOMAINS
from origenlab_email_pipeline.org_normalize import (
    is_junk_org_name,
    normalize_domain,
    normalize_org_name,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.emails import (
    classify_email_input,
    normalize_valid_email,
)


def normalize_identity_email(value: object) -> str | None:
    """Strict valid email normalize (case + surrounding whitespace). No Gmail-dot/plus transforms."""
    return normalize_valid_email(value)


def email_input_kind(value: object) -> tuple[str, str | None]:
    return classify_email_input(value)


def domain_from_email(normalized_email: str | None) -> str | None:
    if not normalized_email:
        return None
    return domain_of(normalized_email) or normalize_domain(normalized_email)


def is_consumer_domain(domain: str | None) -> bool:
    d = (domain or "").strip().lower()
    return bool(d) and d in CONSUMER_EMAIL_DOMAINS


def is_institutional_domain(domain: str | None) -> bool:
    d = (domain or "").strip().lower()
    if not d or "." not in d:
        return False
    return d not in CONSUMER_EMAIL_DOMAINS


def safe_org_normalized(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or is_junk_org_name(raw):
        return None
    nn = normalize_org_name(raw)
    return nn or None


def prefer_display_name(current: str | None, candidate: str | None) -> str | None:
    cur = (current or "").strip() or None
    cand = (candidate or "").strip() or None
    if not cand:
        return cur
    if not cur:
        return cand
    return cand if len(cand) > len(cur) else cur


__all__ = [
    "normalize_identity_email",
    "email_input_kind",
    "domain_from_email",
    "is_consumer_domain",
    "is_institutional_domain",
    "safe_org_normalized",
    "normalize_org_name",
    "normalize_domain",
    "prefer_display_name",
]
