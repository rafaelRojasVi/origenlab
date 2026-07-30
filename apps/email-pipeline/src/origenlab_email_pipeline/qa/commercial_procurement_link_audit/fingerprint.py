"""Procurement source and build-plan fingerprints (design + audit measurement)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
    PROCUREMENT_SOURCE_FP_ALGORITHM,
    RESOLVER_BUILD_CONTRACT_VERSION,
)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def component_fingerprint(rows: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda r: _canonical_json(r))
    return hashlib.sha256(_canonical_json(ordered).encode("utf-8")).hexdigest()


def semantic_signal_fingerprint_row(signal: dict[str, Any]) -> dict[str, Any]:
    """Fields capable of changing a persisted PR4 row or link (no surrogates)."""
    return {
        "source_system": signal.get("source_system") or "chilecompra",
        "canonical_tender_key": signal.get("tender_key") or "",
        "tender_key_kind": signal.get("tender_key_kind") or "",
        "constituent_source_record_ids": list(signal.get("constituent_source_record_ids") or []),
        "buyer_name_raw": signal.get("buyer_display") or "",
        "buyer_name_norm": signal.get("buyer_name_norm") or "",
        "buyer_domain": signal.get("buyer_domain") or "",
        "email_norm": signal.get("email_norm") or "",
        "email_domain": signal.get("email_domain") or "",
        "region": signal.get("region") or "",
        "title": signal.get("title") or "",
        "status_code": signal.get("status_code") or "",
        "status_name": signal.get("status_name") or "",
        "publication_date_raw": signal.get("publication_date") or "",
        "close_date_raw": signal.get("close_date") or "",
        "publication_date_parsed": signal.get("publication_date_parsed") or "",
        "close_date_parsed": signal.get("close_date_parsed") or "",
        "first_seen_at": signal.get("first_seen_at") or "",
        "last_seen_at": signal.get("last_seen_at") or "",
        "procurement_context": signal.get("procurement_context") or "",
        "context_reason_code": signal.get("context_reason_code") or "",
        "line_item_count": int(signal.get("line_item_count") or 0),
        "material_conflict_fields": sorted(
            {c.get("field") for c in (signal.get("line_conflicts") or []) if c.get("field")}
        ),
    }


def procurement_source_fingerprint(*, semantic_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Order-independent fingerprint of PR4-relevant tender *source data only*.

    Excludes: autoincrement lead_id, build timestamps, row ordering, as_of_date,
    identity fingerprint, and resolver version (those belong in build-plan FP).
    """
    rows = [semantic_signal_fingerprint_row(r) for r in semantic_rows]
    components = {
        "semantic_procurement_signals": {
            "n": len(rows),
            "sha256": component_fingerprint(rows),
        }
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


__all__ = [
    "component_fingerprint",
    "procurement_build_plan_fingerprint",
    "procurement_source_fingerprint",
    "semantic_signal_fingerprint_row",
]
