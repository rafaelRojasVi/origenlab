"""Deterministic multi-line tender aggregation (no silent conflict resolution)."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    REASON_LINE_FIELD_CONFLICT,
    REASON_LINE_ITEMS_COALESCED,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.status import (
    classify_procurement_context,
)


_MATERIAL_FIELDS = (
    "buyer_name_norm",
    "buyer_domain",
    "email_norm",
    "status_code",
    "status_name",
    "publication_date",
    "close_date",
)


def _nonzero_values(lines: list[dict[str, Any]], field: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        v = line.get(field)
        if v is None:
            continue
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return sorted(out)


def _pick_deterministic(values: list[str]) -> str | None:
    """Documented rule: lexicographically smallest non-empty when no conflict."""
    return values[0] if values else None


def coalesce_verified_tender_lines(
    *,
    tender_key: str,
    tender_key_kind: str,
    lines: list[dict[str, Any]],
    as_of_date,
) -> dict[str, Any]:
    """Aggregate all lines for one verified tender key.

    Preserves every constituent source_record_id. Emits conflict reason codes when
    material fields disagree. Selects canonical fields only via deterministic rules
    when a single distinct value exists; otherwise leaves the field null and flags
    conflict (no silent representative pick for conflicting material fields).
    """
    ordered = sorted(
        lines,
        key=lambda x: (
            str(x.get("source_record_id") or ""),
            str(x.get("first_seen_at") or ""),
            str(x.get("lead_id") or ""),
        ),
    )
    source_ids = sorted(
        {str(x["source_record_id"]) for x in ordered if x.get("source_record_id")}
    )
    first_seen_vals = [str(x["first_seen_at"]) for x in ordered if x.get("first_seen_at")]
    last_seen_vals = [str(x["last_seen_at"]) for x in ordered if x.get("last_seen_at")]

    conflicts: list[dict[str, Any]] = []
    canonical: dict[str, Any] = {
        "tender_key": tender_key,
        "tender_key_kind": tender_key_kind,
        "line_item_count": len(ordered),
        "constituent_source_record_ids": source_ids,
        "first_seen_at": min(first_seen_vals) if first_seen_vals else None,
        "last_seen_at": max(last_seen_vals) if last_seen_vals else None,
    }

    for field in _MATERIAL_FIELDS:
        values = _nonzero_values(ordered, field)
        if len(values) > 1:
            conflicts.append(
                {
                    "field": field,
                    "reason_code": REASON_LINE_FIELD_CONFLICT,
                    "distinct_values_n": len(values),
                    "source_record_ids": source_ids,
                }
            )
            canonical[field] = None
        else:
            canonical[field] = _pick_deterministic(values)

    # Display / weak-name / region / title: deterministic non-conflicting picks.
    displays = _nonzero_values(ordered, "buyer_display")
    canonical["buyer_display"] = _pick_deterministic(displays) if len(displays) <= 1 else None
    if len(displays) > 1:
        conflicts.append(
            {
                "field": "buyer_display",
                "reason_code": REASON_LINE_FIELD_CONFLICT,
                "distinct_values_n": len(displays),
                "source_record_ids": source_ids,
            }
        )

    regions = _nonzero_values(ordered, "region")
    canonical["region"] = _pick_deterministic(regions) if len(regions) <= 1 else None
    titles = _nonzero_values(ordered, "title")
    canonical["title"] = _pick_deterministic(titles) if len(titles) <= 1 else None

    email_domains = _nonzero_values(ordered, "email_domain")
    canonical["email_domain"] = _pick_deterministic(email_domains) if len(email_domains) <= 1 else None

    weak_flags = {bool(x.get("weak_public_unit_name")) for x in ordered}
    canonical["weak_public_unit_name"] = True if True in weak_flags else False

    ctx = classify_procurement_context(
        status_code=canonical.get("status_code"),
        status_name=canonical.get("status_name"),
        close_date=canonical.get("close_date"),
        publication_date=canonical.get("publication_date"),
        as_of_date=as_of_date,
    )
    canonical["procurement_context"] = ctx["procurement_context"]
    canonical["context_reason_code"] = ctx["reason_code"]
    canonical["close_date_parsed"] = ctx.get("close_date_parsed")
    canonical["publication_date_parsed"] = ctx.get("publication_date_parsed")

    evidence_reasons = [REASON_LINE_ITEMS_COALESCED] if len(ordered) > 1 else []
    if conflicts:
        evidence_reasons.append(REASON_LINE_FIELD_CONFLICT)
    canonical["line_conflicts"] = conflicts

    return {
        "signal": canonical,
        "conflicts": conflicts,
        "evidence_reason_codes": evidence_reasons,
    }


__all__ = ["coalesce_verified_tender_lines"]
