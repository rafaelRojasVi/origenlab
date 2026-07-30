"""Deterministic redaction tokens for PR4 procurement data walkthroughs.

No salt is used. Tokens are ``kind_<sha256hex[:12]>`` over ``kind|normalized_value``.
The same input always yields the same token across tables and reruns.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
# Complete-looking domains (require a TLD); used only for leak checks.
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b",
    re.I,
)

REDACTION_ALGORITHM = "procurement_walkthrough_redact_v1"
REDACTION_ALGORITHM_DOC = (
    "SHA-256 over UTF-8 bytes of '{kind}|{normalized_value}' where "
    "normalized_value is strip+lower for org/domain/email/account/source/tender keys; "
    "token is '{kind}_' + first 12 hex chars of the digest. No salt."
)

_TOKEN_PREFIXES = (
    "org_",
    "domain_",
    "email_",
    "account_",
    "source_",
    "tender_",
    "title_",
)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def redact_token(kind: str, value: str | None) -> str:
    """Return a stable redacted token for a sensitive value."""
    material = f"{kind}|{_norm(value)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{kind}_{digest[:12]}"


def redact_org(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return redact_token("org", str(value))


def redact_domain(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return redact_token("domain", str(value))


def redact_email(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return redact_token("email", str(value))


def redact_account(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return redact_token("account", str(value))


def redact_source(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return redact_token("source", str(value))


def redact_tender(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return redact_token("tender", str(value))


def _looks_like_token(value: str) -> bool:
    return any(value.startswith(p) for p in _TOKEN_PREFIXES)


def _redact_subject_key(value: str) -> str:
    parts = value.split("|")
    if len(parts) >= 4 and parts[1] == "procurement-source":
        parts[-1] = redact_source(parts[-1]) or parts[-1]
        return "|".join(parts)
    if len(parts) >= 4 and parts[1] == "account" and parts[-1]:
        parts[-1] = redact_account(parts[-1]) or parts[-1]
        return "|".join(parts)
    return value


def _redact_json_blob(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        if _EMAIL_RE.search(value):
            return _EMAIL_RE.sub(
                lambda m: redact_email(m.group(0)) or "email_redacted", value
            )
        return value
    return json.dumps(_redact_json_value(parsed), sort_keys=True, separators=(",", ":"))


def _redact_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[k] = redact_maybe_email_or_domain(str(k), v)
        return out
    if isinstance(value, list):
        return [_redact_json_value(x) for x in value]
    return value


def redact_maybe_email_or_domain(field: str, value: Any) -> Any:
    """Redact known sensitive field names; leave non-PII scalars alone."""
    if value is None:
        return None
    name = field.lower()
    if isinstance(value, dict):
        return {k: redact_maybe_email_or_domain(k, v) for k, v in value.items()}
    if isinstance(value, list):
        if name in {"constituent_source_ids_json"} or name.endswith("source_ids"):
            return [redact_source(str(x)) for x in value]
        return [redact_maybe_email_or_domain(name, x) for x in value]

    s = str(value)
    if not s.strip():
        return value

    if name in {
        "detail_json",
        "candidate_account_ids_json",
        "constituent_source_ids_json",
    }:
        if name == "constituent_source_ids_json":
            try:
                ids = json.loads(s)
                if isinstance(ids, list):
                    return json.dumps(
                        [redact_source(str(x)) for x in ids],
                        separators=(",", ":"),
                    )
            except json.JSONDecodeError:
                pass
        if name == "candidate_account_ids_json":
            try:
                ids = json.loads(s)
                if isinstance(ids, list):
                    return json.dumps(
                        [redact_account(str(x)) for x in ids],
                        separators=(",", ":"),
                    )
            except json.JSONDecodeError:
                pass
        return _redact_json_blob(s)

    if name == "subject_key":
        return _redact_subject_key(s)

    if "email" in name and "@" in s:
        return redact_email(s)
    if name in {
        "buyer_domain",
        "buyer_domain_norm",
        "email_domain",
        "domain",
        "primary_domain",
        "lead_domain",
        "raw_domain",
    }:
        return redact_domain(s)
    if name in {
        "buyer_display",
        "buyer_name_raw",
        "buyer_name_norm",
        "org_name",
        "canonical_name",
        "normalized_name",
        "lead_org",
        "raw_org",
        "title",
    }:
        if name == "title":
            return redact_token("title", s) if s.strip() else None
        return redact_org(s)
    if name in {"account_id"} or name.endswith("account_id"):
        return redact_account(s)
    if name in {
        "source_record_id",
        "raw_source_record_id",
        "lead_source_record_id",
    }:
        return redact_source(s)
    if name in {"canonical_tender_key", "tender_key"}:
        return redact_tender(s)
    if name in {
        "procurement_id",
        "resolution_id",
        "evidence_id",
        "conflict_id",
        "candidate_id",
    }:
        return s
    if _EMAIL_RE.search(s) and not _looks_like_token(s):
        return _EMAIL_RE.sub(lambda m: redact_email(m.group(0)) or "email_redacted", s)
    if _DOMAIN_RE.fullmatch(s) and not _looks_like_token(s):
        return redact_domain(s)
    return value


def redact_mapping(row: dict[str, Any]) -> dict[str, Any]:
    """Deep-redact a flat dict of planned/persisted fields."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, dict):
            out[k] = redact_mapping(v)
        elif isinstance(v, list):
            out[k] = [
                redact_mapping(x)
                if isinstance(x, dict)
                else redact_maybe_email_or_domain(k, x)
                for x in v
            ]
        else:
            out[k] = redact_maybe_email_or_domain(k, v)
    return out


def assert_no_pii_leaks(
    text: str, *, forbidden_domains: frozenset[str] | None = None
) -> None:
    """Fail if raw emails or listed complete domains appear in committed text."""
    if _EMAIL_RE.search(text):
        raise AssertionError("raw email pattern found in walkthrough output")
    for d in forbidden_domains or frozenset():
        if d and d.lower() in text.lower():
            raise AssertionError(f"complete production domain leaked: {d}")
    # Synthetic fixture hostnames must not appear in artifacts either.
    for m in _DOMAIN_RE.finditer(text):
        token = m.group(0).lower()
        if _looks_like_token(token):
            continue
        if (
            token.startswith("synthetic-")
            or token.endswith(".cl")
            and "synthetic" in token
        ):
            raise AssertionError(f"synthetic domain leaked: {token}")


def contains_email_pattern(text: str) -> bool:
    return _EMAIL_RE.search(text) is not None


__all__ = [
    "REDACTION_ALGORITHM",
    "REDACTION_ALGORITHM_DOC",
    "assert_no_pii_leaks",
    "contains_email_pattern",
    "redact_account",
    "redact_domain",
    "redact_email",
    "redact_mapping",
    "redact_maybe_email_or_domain",
    "redact_org",
    "redact_source",
    "redact_tender",
    "redact_token",
]
