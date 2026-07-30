"""Procurement source and build-plan fingerprints (design + audit measurement)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from origenlab_email_pipeline.commercial_procurement.constants import (
    PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
    PROCUREMENT_SOURCE_FP_ALGORITHM,
    RESOLVER_BUILD_CONTRACT_VERSION,
    SOURCE_CHILECOMPRA,
)
from origenlab_email_pipeline.commercial_procurement.normalize import (
    stable_token,
)
from origenlab_email_pipeline.commercial_procurement.status import (
    parse_tender_date,
)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def component_fingerprint(rows: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda r: _canonical_json(r))
    return hashlib.sha256(_canonical_json(ordered).encode("utf-8")).hexdigest()


def value_hash(value: str | None) -> str:
    """Stable non-PII hash for conflict detail (never expose raw emails)."""
    return stable_token("fpval", value or "", n=16)


def source_line_semantic_payload(line: dict[str, Any]) -> dict[str, Any]:
    """Level A: one deterministic row per ChileCompra source outcome.

    Excludes: lead_id, row order, build timestamps, as_of_date, identity FP,
    derived procurement_context, resolver version.
    """
    pub_raw = line.get("publication_date") or ""
    close_raw = line.get("close_date") or ""
    pub_parsed = line.get("publication_date_parsed")
    close_parsed = line.get("close_date_parsed")
    if pub_parsed is None and pub_raw:
        d = parse_tender_date(str(pub_raw))
        pub_parsed = d.isoformat() if d else ""
    if close_parsed is None and close_raw:
        d = parse_tender_date(str(close_raw))
        close_parsed = d.isoformat() if d else ""
    email_norm = line.get("email_norm") or ""
    return {
        "source_system": line.get("source_system") or SOURCE_CHILECOMPRA,
        "source_record_id": str(line.get("source_record_id") or ""),
        "raw_lead_join_status": line.get("raw_lead_join_status") or "matched",
        "raw_json_valid": bool(line.get("raw_json_valid", True)),
        "tender_key_verified": bool(line.get("verified", False)),
        "tender_key": line.get("tender_key") or "",
        "tender_key_kind": line.get("tender_key_kind") or "",
        "buyer_name_raw": line.get("buyer_display") or "",
        "buyer_name_norm": line.get("buyer_name_norm") or "",
        "buyer_domain": line.get("buyer_domain") or "",
        # Contact email hashed in payload (capable of changing enrichment); never store raw in FP reports.
        "contact_email_hash": value_hash(str(email_norm)) if email_norm else "",
        "email_domain": line.get("email_domain") or "",
        "region": line.get("region") or "",
        "title": line.get("title") or "",
        "status_code": line.get("status_code") or "",
        "status_name": line.get("status_name") or "",
        "publication_date_raw": str(pub_raw),
        "close_date_raw": str(close_raw),
        "publication_date_parsed": str(pub_parsed or ""),
        "close_date_parsed": str(close_parsed or ""),
        "first_seen_at": line.get("first_seen_at") or "",
        "last_seen_at": line.get("last_seen_at") or "",
        "weak_public_unit_name": bool(line.get("weak_public_unit_name", False)),
    }


def procurement_source_fingerprint(*, source_line_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Level B: order-independent hash of *all* source-line semantic payloads.

    Includes verified, unresolved, raw-only, lead-only, and malformed-raw outcomes.
    Does not include as_of_date, identity FP, procurement_context, or resolver version.
    """
    rows = [dict(r) for r in source_line_payloads]
    verified = [r for r in rows if r.get("tender_key_verified")]
    unresolved = [r for r in rows if not r.get("tender_key_verified")]
    by_join: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_join.setdefault(str(r.get("raw_lead_join_status") or "unknown"), []).append(r)

    components = {
        "all_source_lines": {"n": len(rows), "sha256": component_fingerprint(rows)},
        "verified_tender_key_lines": {
            "n": len(verified),
            "sha256": component_fingerprint(verified),
        },
        "unresolved_tender_key_lines": {
            "n": len(unresolved),
            "sha256": component_fingerprint(unresolved),
        },
        "by_join_status": {
            status: {"n": len(items), "sha256": component_fingerprint(items)}
            for status, items in sorted(by_join.items())
        },
    }
    payload = {"algorithm": PROCUREMENT_SOURCE_FP_ALGORITHM, "components": components}
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return {
        "algorithm": PROCUREMENT_SOURCE_FP_ALGORITHM,
        "fingerprint": digest,
        "components": components,
    }


def procurement_build_plan_fingerprint(
    *,
    source_fingerprint: str,
    identity_fingerprint: str | None,
    as_of_date: str,
    resolver_build_contract_version: str = RESOLVER_BUILD_CONTRACT_VERSION,
) -> dict[str, Any]:
    """Level C: source FP + identity FP + as_of_date + resolver version.

    Derived procurement_context and account-resolution results belong here
    (via as_of_date / resolver version), not in the source fingerprint.
    """
    payload = {
        "algorithm": PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
        "source_fingerprint": source_fingerprint,
        "identity_fingerprint": identity_fingerprint or "",
        "as_of_date": as_of_date,
        "resolver_build_contract_version": resolver_build_contract_version,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return {
        "algorithm": PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
        "fingerprint": digest,
        "inputs": payload,
    }


def conflict_id_for_source_row(
    *,
    source_system: str,
    source_record_id: str,
    reason_code: str,
) -> str:
    """Stable conflict ID with direct provenance (no procurement_id required)."""
    material = f"v1|procurement-conflict|{source_system}|{source_record_id}|{reason_code}"
    return "c_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "component_fingerprint",
    "conflict_id_for_source_row",
    "procurement_build_plan_fingerprint",
    "procurement_source_fingerprint",
    "source_line_semantic_payload",
    "value_hash",
]
