"""Email validation and duplicate-occurrence helpers for the commercial truth audit."""

from __future__ import annotations

from collections import Counter
from typing import Any

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.candidate_export_gate import normalize_export_email


def normalize_valid_email(value: Any) -> str | None:
    """Strict normalize: nonempty arbitrary strings are not treated as valid emails."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Prefer shared export normalizer (first mailbox via emails_in).
    em = normalize_export_email(raw)
    if em and "@" in em and "." in em.rsplit("@", 1)[-1]:
        return em.lower()
    # Fallback: emails_in may recover from angle-bracket forms.
    found = emails_in(raw)
    if not found:
        return None
    cand = found[0].lower()
    if "@" not in cand:
        return None
    local, _, dom = cand.partition("@")
    if not local or not dom or "." not in dom:
        return None
    return cand


def classify_email_input(value: Any) -> tuple[str, str | None]:
    """Return (kind, normalized) where kind is valid|missing|invalid."""
    if value is None or not str(value).strip():
        return "missing", None
    em = normalize_valid_email(value)
    if em:
        return "valid", em
    return "invalid", None


def duplicate_email_stats(values: list[Any]) -> dict[str, Any]:
    """Compute duplicate stats that never treat missing emails as duplicates.

    ``duplicate_occurrence_count`` = sum(max(count - 1, 0)) over valid emails.
    ``duplicate_rate_of_valid_email_rows`` uses valid_email_row_count as denominator.
    """
    kinds: list[str] = []
    valid: list[str] = []
    for v in values:
        kind, em = classify_email_input(v)
        kinds.append(kind)
        if kind == "valid" and em:
            valid.append(em)

    row_count = len(values)
    valid_email_row_count = len(valid)
    missing_email_count = sum(1 for k in kinds if k == "missing")
    invalid_email_count = sum(1 for k in kinds if k == "invalid")
    counts = Counter(valid)
    unique_valid_email_count = len(counts)
    duplicate_occurrence_count = sum(max(c - 1, 0) for c in counts.values())
    if valid_email_row_count > 0:
        duplicate_rate = round(100.0 * duplicate_occurrence_count / valid_email_row_count, 2)
    else:
        duplicate_rate = 0.0
    return {
        "row_count": row_count,
        "valid_email_row_count": valid_email_row_count,
        "missing_email_count": missing_email_count,
        "invalid_email_count": invalid_email_count,
        "unique_valid_email_count": unique_valid_email_count,
        "duplicate_occurrence_count": duplicate_occurrence_count,
        "duplicate_rate_of_valid_email_rows": duplicate_rate,
    }
