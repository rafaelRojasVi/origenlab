"""Proposed procurement source fingerprint (design + audit measurement)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    PROCUREMENT_SOURCE_FP_ALGORITHM,
)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def component_fingerprint(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(rows).encode("utf-8")).hexdigest()


def procurement_source_fingerprint(
    *,
    chilecompra_raw_keys: list[dict[str, Any]],
    chilecompra_lead_keys: list[dict[str, Any]],
    coalesced_tender_keys: list[dict[str, Any]],
) -> dict[str, Any]:
    """Order-independent fingerprint of PR4-relevant tender source planes.

    Does not include PR2 identity fingerprint (gated separately on apply).
    Does not include PR3 opportunity tables.
    """
    components = {
        "chilecompra_raw_keys": {
            "n": len(chilecompra_raw_keys),
            "sha256": component_fingerprint(sorted(chilecompra_raw_keys, key=lambda r: json.dumps(r, sort_keys=True))),
        },
        "chilecompra_lead_keys": {
            "n": len(chilecompra_lead_keys),
            "sha256": component_fingerprint(sorted(chilecompra_lead_keys, key=lambda r: json.dumps(r, sort_keys=True))),
        },
        "coalesced_tender_keys": {
            "n": len(coalesced_tender_keys),
            "sha256": component_fingerprint(sorted(coalesced_tender_keys, key=lambda r: json.dumps(r, sort_keys=True))),
        },
    }
    payload = {"algorithm": PROCUREMENT_SOURCE_FP_ALGORITHM, "components": components}
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return {
        "algorithm": PROCUREMENT_SOURCE_FP_ALGORITHM,
        "fingerprint": digest,
        "components": components,
    }


__all__ = ["component_fingerprint", "procurement_source_fingerprint"]
