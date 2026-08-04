"""Redaction helpers for PR5C walkthrough outputs."""

from __future__ import annotations

from typing import Any

from origenlab_email_pipeline.commercial_procurement.walkthrough_redaction import (
    assert_no_pii_leaks,
    redact_account,
    redact_domain,
    redact_email,
    redact_org,
    redact_source,
    redact_tender,
    redact_token,
)


def redact_evidence_ref(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["canonical_tender_key"] = redact_tender(row.get("canonical_tender_key"))
    out["buyer_display_raw"] = redact_org(row.get("buyer_display_raw"))
    out["buyer_source_id"] = redact_domain(row.get("buyer_source_id"))
    out["title_raw"] = (
        redact_token("title", row["title_raw"]) if row.get("title_raw") else None
    )
    out["source_record_id"] = redact_source(row.get("source_record_id"))
    out["pr4_procurement_id"] = (
        redact_token("procurement", row["pr4_procurement_id"])
        if row.get("pr4_procurement_id")
        else None
    )
    out["pr4_account_resolution_id"] = (
        redact_account(row["pr4_account_resolution_id"])
        if row.get("pr4_account_resolution_id")
        else None
    )
    return out


def redact_coalesced_tender(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["canonical_tender_key"] = redact_tender(row.get("canonical_tender_key"))
    out["buyer_display_selected"] = redact_org(row.get("buyer_display_selected"))
    out["buyer_source_id_selected"] = redact_domain(row.get("buyer_source_id_selected"))
    out["title_selected"] = (
        redact_token("title", row["title_selected"]) if row.get("title_selected") else None
    )
    out["pr4_procurement_id"] = (
        redact_token("procurement", row["pr4_procurement_id"])
        if row.get("pr4_procurement_id")
        else None
    )
    return out


def redact_unresolved(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["canonical_tender_key_candidate"] = redact_tender(
        row.get("canonical_tender_key_candidate")
    )
    out["source_native_tender_key"] = redact_source(row.get("source_native_tender_key"))
    out["source_record_id"] = redact_source(row.get("source_record_id"))
    return out


__all__ = [
    "assert_no_pii_leaks",
    "redact_account",
    "redact_coalesced_tender",
    "redact_email",
    "redact_evidence_ref",
    "redact_org",
    "redact_source",
    "redact_tender",
    "redact_token",
    "redact_unresolved",
]
