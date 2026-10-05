"""Web-researched facts about CRM institutions — suggestions an operator may apply, never records.

The file is built outside the repository from a research run's `suggestions.json` by
`scripts/build_org_suggestions.py`, reviewed, and uploaded as a Render secret file named by
`ORIGENLAB_V2_ORG_SUGGESTIONS_FILE`. It names customers, so it never enters this public
repository, and there is no default path.

Read per request, like the V1-lane declaration (`v1_lane_campaigns.py`): no setting is silence;
a missing, oversized, unparsable or invalid file is a warning and no suggestions — never a failed
read. Nothing here writes: «Aplicar» is the operator calling an existing authoring command.

Shape, version 1 — every field but `confidence` may be null:

    {"version": 1, "organizations": {"<organization uuid>": {
        "display_name": str, "legal_name": str, "rut": "12345678-5",
        "rut_source": "official" | "directory", "website": "https://…",
        "email_domain": "example.cl", "type": "universidad", "city": str, "region": str,
        "confidence": "high" | "medium" | "low" | "not_found",
        "sources": [{"url": "https://…", "shows": str}], "notes": str}}}
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from origenlab_api.v2.quote_crm_promotion import FREE_MAIL_DOMAINS

log = logging.getLogger(__name__)

FILE_VERSION = 1
MAX_FILE_BYTES = 1024 * 1024
MAX_TEXT = 500
MAX_NOTES = 2000
MAX_SOURCES = 20
CONFIDENCES = ("high", "medium", "low", "not_found")
RUT_SOURCES = ("official", "directory")

_FIELDS = frozenset({
    "display_name", "legal_name", "rut", "rut_source", "website", "email_domain", "type",
    "city", "region", "confidence", "sources", "notes",
})
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: The CRM's own shapes — `organization_kind_shape` and `crm_authoring._DOMAIN_SHAPE` — so an
#: «Aplicar» is never refused for the shape of what the file offered.
_KIND = re.compile(r"^[a-z][a-z_]{0,39}$")
_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_RUT = re.compile(r"^[1-9][0-9]{0,8}-[0-9K]$")
_URL = re.compile(r"^https?://[^\s\"'<>]+$")


class OrgSuggestionsError(ValueError):
    """A suggestions file that may not be shown. Messages name fields, never values."""


def rut_key(raw: str) -> str:
    """`12.345.678-5`, `12345678-5` and ` 012345678 5` are one RUT: digits, a dash, the check
    character upper-cased — the normal form of `quote_document_identity.normalize_rut`."""
    s = re.sub(r"[^0-9kK]", "", raw).upper()
    return f"{s[:-1].lstrip('0')}-{s[-1]}" if len(s) >= 2 else s


def valid_rut(rut: str) -> bool:
    """A normalised RUT whose check character is right (módulo 11)."""
    if not _RUT.match(rut):
        return False
    body, _, check = rut.partition("-")
    total, factor = 0, 2
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    expected = 11 - total % 11
    return check == {11: "0", 10: "K"}.get(expected, str(expected))


def _text(entry: Mapping[str, Any], key: str, limit: int = MAX_TEXT) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise OrgSuggestionsError(f"{key} must be a string of at most {limit} characters")
    return value.strip() or None


def validate_entry(entry: object) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise OrgSuggestionsError("an entry must be an object")
    unknown = set(entry) - _FIELDS
    if unknown:
        raise OrgSuggestionsError(f"unknown fields: {sorted(unknown)}")
    confidence = entry.get("confidence")
    if confidence not in CONFIDENCES:
        raise OrgSuggestionsError(f"confidence must be one of {CONFIDENCES}")
    rut = _text(entry, "rut", 20)
    rut_source = entry.get("rut_source")
    if rut is not None:
        rut = rut_key(rut)
        if not valid_rut(rut):
            raise OrgSuggestionsError("rut is not a valid RUT (shape or check digit)")
        if rut_source not in RUT_SOURCES:
            raise OrgSuggestionsError("rut_source must be 'official' or 'directory' when rut is set")
    elif rut_source is not None:
        raise OrgSuggestionsError("rut_source must be null when rut is null")
    email_domain = _text(entry, "email_domain", 253)
    if email_domain is not None:
        email_domain = email_domain.lower()
        if not _DOMAIN.match(email_domain):
            raise OrgSuggestionsError("email_domain is not a host name")
        if email_domain in FREE_MAIL_DOMAINS:
            email_domain = None  # a mail provider's domain belongs to no institution
    kind = _text(entry, "type", 40)
    if kind is not None and not _KIND.match(kind):
        raise OrgSuggestionsError("type must be a lower_snake_case token (the CRM's kind shape)")
    website = _text(entry, "website")
    if website is not None and not _URL.match(website):
        raise OrgSuggestionsError("website must be an http(s) URL")
    raw_sources = entry.get("sources") or []
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES:
        raise OrgSuggestionsError(f"sources must be a list of at most {MAX_SOURCES}")
    sources = []
    for source in raw_sources:
        url = source.get("url") if isinstance(source, dict) else None
        if not isinstance(url, str) or not _URL.match(url.strip()):
            raise OrgSuggestionsError("every source needs an http(s) url")
        sources.append({"url": url.strip(), "shows": _text(source, "shows")})
    return {
        "display_name": _text(entry, "display_name"),
        "legal_name": _text(entry, "legal_name"),
        "rut": rut,
        "rut_source": rut_source if rut is not None else None,
        "website": website,
        "email_domain": email_domain,
        "type": kind,
        "city": _text(entry, "city"),
        "region": _text(entry, "region"),
        "confidence": confidence,
        "sources": sources,
        "notes": _text(entry, "notes", MAX_NOTES),
    }


def validate_file(data: object) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
        raise OrgSuggestionsError(f"the root must be an object with version {FILE_VERSION}")
    organizations = data.get("organizations")
    if not isinstance(organizations, dict):
        raise OrgSuggestionsError("organizations must be an object keyed by organization id")
    out: dict[str, dict[str, Any]] = {}
    for key, entry in organizations.items():
        if not isinstance(key, str) or not _UUID.match(key):
            raise OrgSuggestionsError("every organization key must be a lower-case UUID")
        try:
            out[key] = validate_entry(entry)
        except OrgSuggestionsError as exc:
            raise OrgSuggestionsError(f"{key}: {exc}") from exc
    return out


def load_org_suggestions(path: str | None) -> dict[str, dict[str, Any]]:
    """The suggestions by organization id, or {} — with a warning unless `path` is unset."""
    if not path:
        return {}
    try:
        p = Path(path)
        if p.stat().st_size > MAX_FILE_BYTES:
            raise OrgSuggestionsError(f"the file is larger than {MAX_FILE_BYTES} bytes")
        return validate_file(json.loads(p.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - a bad file is silence plus a warning, never a failed read
        log.warning("org_web_suggestions: no suggestions from %s — %s: %s", path, exc.__class__.__name__, exc)
        return {}


def suggestion_for(suggestions: Mapping[str, dict[str, Any]], organization_id: str) -> dict[str, Any] | None:
    return suggestions.get(organization_id.strip().lower())
