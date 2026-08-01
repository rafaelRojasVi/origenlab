"""Acquisition redaction and ticket-safe sanitization."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

SECRET_KEY_FRAGMENTS = (
    "ticket",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
)

_SECRET_IN_STRING_RE = re.compile(
    r"(?i)(ticket|token|api[_-]?key|secret|authorization)\s*[=:]\s*[^\s&,;\"']+"
)


def _looks_secret_key(key: str) -> bool:
    k = key.strip().lower().replace("-", "_")
    return any(frag in k for frag in SECRET_KEY_FRAGMENTS)


def redact_token(kind: str, value: str | None) -> str:
    material = f"{kind}|{(value or '').strip().casefold()}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{kind}_{digest[:12]}"


def endpoint_path_only(url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if not text:
        return None
    try:
        if "://" not in text and text.startswith("/"):
            return text.split("?", 1)[0]
        parsed = urlparse(text if "://" in text else f"https://placeholder.local{text}")
        return urlunparse(("", "", parsed.path or "", "", "", ""))
    except ValueError:
        return None


def sanitize_mapping(value: Any) -> Any:
    """Recursively drop secret keys and credential-bearing URL query strings."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if _looks_secret_key(key):
                continue
            if key.lower() in {"url", "query", "request_url", "href", "endpoint"}:
                if isinstance(v, str):
                    path = endpoint_path_only(v)
                    if path:
                        out["endpoint_path"] = path
                continue
            scrubbed = sanitize_mapping(v)
            if scrubbed is None:
                continue
            out[key] = scrubbed
        return out
    if isinstance(value, list):
        return [sanitize_mapping(v) for v in value]
    if isinstance(value, str):
        if _SECRET_IN_STRING_RE.search(value) or "ticket=" in value.lower():
            path = endpoint_path_only(value)
            return path
        return value
    return value


def sanitize_error_message(message: str) -> str:
    text = _SECRET_IN_STRING_RE.sub(r"\1=<redacted>", message)
    return re.sub(r"(?i)([?&]ticket=)[^&\s]+", r"\1<redacted>", text)


def assert_no_ticket_leak(text: str, *, forbidden: set[str] | frozenset[str] | None = None) -> None:
    lower = text.lower()
    if "ticket=" in lower and "ticket=<redacted>" not in lower and "ticket_configured" not in lower:
        # Allow boolean field names; reject credential-bearing query forms.
        if re.search(r"ticket=[^<\s\"']+", text, re.IGNORECASE):
            raise AssertionError("ticket query value leaked")
    for raw in forbidden or ():
        if raw and len(raw) >= 6 and raw in text:
            raise AssertionError(f"forbidden value leaked: {raw[:48]}")


def is_ticket_configured_boolean_only(env_value: str | None) -> bool:
    """Shared boolean-only ticket configuration check — never returns the value."""
    return bool(env_value and str(env_value).strip())


def ticket_safety_flags(*, ticket_configured: bool) -> dict[str, bool]:
    """Accurate PR5B ticket safety assertions (no ticket_value_accessed)."""
    return {
        "ticket_configured": bool(ticket_configured),
        "ticket_used_for_request": False,
        "ticket_persisted": False,
        "ticket_logged": False,
        "authenticated_request_performed": False,
    }
