"""Deterministic stable IDs for commercial identity (independent of input order)."""

from __future__ import annotations

import hashlib


def _sha32(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def stable_contact_id(normalized_email: str) -> str:
    """Canonical contact id from normalized exact email (strongest automatic key)."""
    em = (normalized_email or "").strip().lower()
    if not em:
        raise ValueError("normalized_email is required for stable_contact_id")
    return "c_" + _sha32(f"v1|contact|{em}")


def stable_account_id_for_domain(domain_norm: str) -> str:
    """Canonical account id keyed by institutional domain."""
    dom = (domain_norm or "").strip().lower()
    if not dom:
        raise ValueError("domain_norm is required for stable_account_id_for_domain")
    return "a_" + _sha32(f"v1|account|domain|{dom}")


def stable_account_id_for_name(normalized_name: str) -> str:
    """Canonical account id keyed by normalized org name when no institutional domain exists."""
    nn = (normalized_name or "").strip().lower()
    if not nn:
        raise ValueError("normalized_name is required for stable_account_id_for_name")
    return "a_" + _sha32(f"v1|account|name|{nn}")


def stable_evidence_id(
    *,
    source_table: str,
    source_record_id: str,
    evidence_type: str,
    subject_id: str,
) -> str:
    payload = "|".join(
        [
            "v1|evidence",
            (source_table or "").strip(),
            (source_record_id or "").strip(),
            (evidence_type or "").strip(),
            (subject_id or "").strip(),
        ]
    )
    return "e_" + _sha32(payload)


def stable_conflict_id(*, reason_code: str, subject_keys: str) -> str:
    payload = f"v1|conflict|{reason_code}|{subject_keys}"
    return "x_" + _sha32(payload)
