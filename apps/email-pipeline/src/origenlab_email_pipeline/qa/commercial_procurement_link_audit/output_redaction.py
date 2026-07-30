"""Redaction helpers for PR4 audit outputs."""

from __future__ import annotations

import re
from typing import Any

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.normalize import stable_token

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.I)
_ABS_PATH_RE = re.compile(r"(?:/home/|/Users/|/mnt/|[A-Za-z]:\\)")


def redact_email(value: object) -> str:
    em = str(value or "").strip()
    if not em:
        return ""
    return f"email:{stable_token('email', em, n=10)}"


def redact_buyer_name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    return f"buyer:{stable_token('buyer', name, n=10)}"


def redact_account_id(value: object) -> str:
    aid = str(value or "").strip()
    if not aid:
        return ""
    # Keep prefix for type signal (a_…) but truncate.
    if aid.startswith("a_") and len(aid) > 6:
        return aid[:6] + "…" + stable_token("acct", aid, n=6)
    return f"acct:{stable_token('acct', aid, n=10)}"


def assert_no_leakage(text: str) -> None:
    if _EMAIL_RE.search(text):
        raise ValueError("audit output leaked an email address")
    if _ABS_PATH_RE.search(text):
        raise ValueError("audit output leaked an absolute local path")


def scrub_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None:
            out[k] = ""
            continue
        if isinstance(v, (int, float, bool)):
            out[k] = v
            continue
        s = str(v)
        s = _EMAIL_RE.sub(lambda m: redact_email(m.group(0)), s)
        s = _ABS_PATH_RE.sub("<ABS_PATH>", s)
        out[k] = s
    return out


__all__ = [
    "assert_no_leakage",
    "redact_account_id",
    "redact_buyer_name",
    "redact_email",
    "scrub_row",
]
