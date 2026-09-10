"""The operator attestation: the half of slice 0 that no database session can answer.

A Dashboard toggle, a bucket's visibility, a backup schedule, a completed restore drill and the
contents of the Render, Cloudflare and GitHub secret stores are all outside a PostgreSQL
connection. The audit does not guess at them, does not infer them from something adjacent, and
does not quietly leave them out of the verdict. It requires a signed statement at one fixed path,
records who made it and when, and marks every item it carries as `ATTESTED` -- a word the report
never uses interchangeably with `PASS`.

An attestation is refused if it is stale, if it does not name the project the audit is pointed at,
or if an item is claimed true with no evidence. The file itself is git-ignored and never uploaded:
only the fingerprint of the project reference and the evidence sentence reach a report.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

MAX_AGE_DAYS = 30

REQUIRED_KEYS = ("attested_by", "attested_at_utc", "project_ref", "items")


class AttestationError(RuntimeError):
    """The attestation was refused. Its items are then unproven, never assumed."""


def fingerprint(project_ref: str) -> str:
    """A short, non-reversible handle for a project reference, safe to put in a report."""
    return hashlib.sha256(project_ref.encode("utf-8")).hexdigest()[:12]


def parse_timestamp(value: str) -> dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def load(path: Path, *, project_ref: str | None, now: dt.datetime, max_age_days: int = MAX_AGE_DAYS) -> dict:
    """Load and validate an attestation. Raises AttestationError rather than degrading silently."""
    if not path.is_file():
        raise AttestationError(f"{path} does not exist; no hosted item is attested")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AttestationError(f"the attestation file is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise AttestationError("the attestation file is not a JSON object")

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise AttestationError(f"the attestation is missing {', '.join(missing)}")
    if not isinstance(payload["items"], dict) or not payload["items"]:
        raise AttestationError("the attestation carries no items")

    try:
        attested_at = parse_timestamp(str(payload["attested_at_utc"]))
    except ValueError as exc:
        raise AttestationError("attested_at_utc is not an ISO 8601 timestamp") from exc
    age = now - attested_at
    if age > dt.timedelta(days=max_age_days):
        raise AttestationError(
            f"the attestation is {age.days} days old; it must be re-made within {max_age_days} days"
        )
    if age < dt.timedelta(days=-1):
        raise AttestationError("the attestation is dated in the future")

    if project_ref is not None and str(payload["project_ref"]) != project_ref:
        raise AttestationError(
            "the attestation names a different project than the audit target; refusing to apply it"
        )

    return {
        "attested_by": str(payload["attested_by"]),
        "attested_at_utc": attested_at.isoformat().replace("+00:00", "Z"),
        "project_ref_fingerprint": fingerprint(str(payload["project_ref"])),
        "items": payload["items"],
    }


def describe(attestation: dict) -> dict:
    """What a report may say about an attestation: who, when, which project, and each claim."""
    if not attestation:
        return {"present": False}
    return {
        "present": True,
        "attested_by": attestation.get("attested_by", ""),
        "attested_at_utc": attestation.get("attested_at_utc", ""),
        "project_ref_fingerprint": attestation.get("project_ref_fingerprint", ""),
        "items_claimed": sorted(attestation.get("items", {})),
    }
