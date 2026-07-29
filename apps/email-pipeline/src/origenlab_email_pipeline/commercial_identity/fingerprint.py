"""Deterministic identity-resolution fingerprint for PR3 compatibility gating."""

from __future__ import annotations

import hashlib

from origenlab_email_pipeline.commercial_identity.models import IdentityResolution


def identity_resolution_fingerprint(resolution: IdentityResolution) -> str:
    """Order-independent fingerprint of canonical identity IDs + evidence IDs.

    Pure metadata for snapshot compatibility. Does not change matching rules.
    """
    accounts = ",".join(sorted(a.account_id for a in resolution.accounts))
    contacts = ",".join(sorted(c.contact_id for c in resolution.contacts))
    evidence = ",".join(sorted(e.evidence_id for e in resolution.evidence))
    conflicts = ",".join(sorted(c.conflict_id for c in resolution.conflicts))
    payload = f"v1|identity_fp|{accounts}|{contacts}|{evidence}|{conflicts}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
