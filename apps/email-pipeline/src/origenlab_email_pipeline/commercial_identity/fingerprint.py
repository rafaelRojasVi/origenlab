"""Deterministic identity-resolution fingerprint for PR3 compatibility gating.

Algorithm version: identity_fp_v2

Canonical JSON over linkage-relevant account/contact fields plus evidence/conflict
IDs. Order-independent. Does not change identity matching rules. Does not emit
raw PII to logs — callers should log only the hex digest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from origenlab_email_pipeline.commercial_identity.models import IdentityResolution

FINGERPRINT_ALGORITHM_VERSION = "identity_fp_v2"


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def identity_resolution_fingerprint(resolution: IdentityResolution) -> str:
    """Order-independent fingerprint of linkage-relevant identity state."""
    accounts = []
    for a in resolution.accounts:
        aliases = sorted(
            {
                str(alias.get("normalized_alias") or key)
                for key, alias in (a.aliases or {}).items()
            }
        )
        domains = sorted(str(d) for d in (a.domains or {}).keys())
        accounts.append(
            {
                "account_id": a.account_id,
                "normalized_name": a.normalized_name,
                "primary_domain": a.primary_domain,
                "identity_status": a.identity_status,
                "identity_confidence": a.identity_confidence,
                "aliases": aliases,
                "domains": domains,
            }
        )
    accounts.sort(key=lambda x: x["account_id"])

    contacts = []
    for c in resolution.contacts:
        contacts.append(
            {
                "contact_id": c.contact_id,
                "normalized_email": c.normalized_email,
                "account_id": c.account_id,
                "account_link_method": c.account_link_method,
                "email_domain": c.email_domain,
                "identity_status": c.identity_status,
                "identity_confidence": c.identity_confidence,
            }
        )
    contacts.sort(key=lambda x: x["contact_id"])

    evidence_ids = sorted(e.evidence_id for e in resolution.evidence)
    conflict_ids = sorted(c.conflict_id for c in resolution.conflicts)

    payload = {
        "algorithm": FINGERPRINT_ALGORITHM_VERSION,
        "accounts": accounts,
        "contacts": contacts,
        "evidence_ids": evidence_ids,
        "conflict_ids": conflict_ids,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
