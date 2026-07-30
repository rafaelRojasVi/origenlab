"""Deterministic multi-line tender aggregation (no silent conflict resolution)."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement.constants import (
    REASON_LINE_FIELD_CONFLICT,
    REASON_LINE_ITEMS_COALESCED,
)
from origenlab_email_pipeline.commercial_procurement.fingerprint import (
    component_fingerprint,
    source_line_semantic_payload,
    value_hash,
)
from origenlab_email_pipeline.commercial_procurement.status import (
    classify_procurement_context,
    parse_tender_date,
)


# Every persisted field where disagreement matters.
_CONFLICT_FIELDS = (
    "buyer_display",
    "buyer_name_norm",
    "buyer_domain",
    "email_norm",
    "email_domain",
    "region",
    "title",
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


def _conflict_detail(
    *,
    field: str,
    values: list[str],
    source_ids: list[str],
) -> dict[str, Any]:
    # Never expose raw emails — hash contact email values.
    if field == "email_norm":
        value_detail = [value_hash(v) for v in values]
        detail_kind = "value_hashes"
    else:
        value_detail = list(values)
        detail_kind = "normalized_values"
    return {
        "field": field,
        "reason_code": REASON_LINE_FIELD_CONFLICT,
        "distinct_values_n": len(values),
        detail_kind: value_detail,
        "value_hashes": [value_hash(v) for v in values],
        "source_record_ids": list(source_ids),
    }


def coalesce_verified_tender_lines(
    *,
    tender_key: str,
    tender_key_kind: str,
    lines: list[dict[str, Any]],
    as_of_date,
) -> dict[str, Any]:
    """Aggregate all lines for one verified tender key.

    Preserves a stable ordered list of constituent semantic source-line payloads.
    Emits structured conflicts when material fields disagree. Selects canonical
    fields only when a single distinct value exists — never by lead_id.
    """
    # Stable order: source_record_id then first_seen_at — never lead_id.
    ordered = sorted(
        lines,
        key=lambda x: (
            str(x.get("source_record_id") or ""),
            str(x.get("first_seen_at") or ""),
        ),
    )
    source_ids = [str(x["source_record_id"]) for x in ordered if x.get("source_record_id")]
    # Deduplicate preserving order
    seen_ids: set[str] = set()
    source_ids_unique: list[str] = []
    for sid in source_ids:
        if sid not in seen_ids:
            seen_ids.add(sid)
            source_ids_unique.append(sid)

    constituent_payloads = [source_line_semantic_payload(x) for x in ordered]
    constituent_lines_fp = component_fingerprint(constituent_payloads)

    first_seen_vals = [str(x["first_seen_at"]) for x in ordered if x.get("first_seen_at")]
    last_seen_vals = [str(x["last_seen_at"]) for x in ordered if x.get("last_seen_at")]

    conflicts: list[dict[str, Any]] = []
    canonical: dict[str, Any] = {
        "tender_key": tender_key,
        "tender_key_kind": tender_key_kind,
        "line_item_count": len(ordered),
        "constituent_source_record_ids": source_ids_unique,
        "constituent_semantic_line_payloads": constituent_payloads,
        "constituent_semantic_lines_fingerprint": constituent_lines_fp,
        "first_seen_at": min(first_seen_vals) if first_seen_vals else None,
        "last_seen_at": max(last_seen_vals) if last_seen_vals else None,
    }

    for field in _CONFLICT_FIELDS:
        values = _nonzero_values(ordered, field)
        if len(values) > 1:
            conflicts.append(
                _conflict_detail(field=field, values=values, source_ids=source_ids_unique)
            )
            canonical[field] = None
        else:
            canonical[field] = _pick_deterministic(values)

    weak_flags = {bool(x.get("weak_public_unit_name")) for x in ordered}
    canonical["weak_public_unit_name"] = True if True in weak_flags else False

    # Parsed dates from selected (or absent) canonical raw dates — build-plan uses as_of.
    pub = canonical.get("publication_date")
    close = canonical.get("close_date")
    pub_d = parse_tender_date(pub)
    close_d = parse_tender_date(close)
    canonical["publication_date_parsed"] = pub_d.isoformat() if pub_d else None
    canonical["close_date_parsed"] = close_d.isoformat() if close_d else None

    ctx = classify_procurement_context(
        status_code=canonical.get("status_code"),
        status_name=canonical.get("status_name"),
        close_date=canonical.get("close_date"),
        publication_date=canonical.get("publication_date"),
        as_of_date=as_of_date,
    )
    # Derived context belongs to build plan — still recorded on signal for persistence design.
    canonical["procurement_context"] = ctx["procurement_context"]
    canonical["context_reason_code"] = ctx["reason_code"]

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
