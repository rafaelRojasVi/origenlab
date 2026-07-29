"""Deterministic stable IDs for commercial opportunity stage (PR3)."""

from __future__ import annotations

import hashlib


def _sha32(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def stable_opportunity_id(*, source_kind: str, source_key: str) -> str:
    sk = (source_kind or "").strip()
    key = (source_key or "").strip()
    if not sk or not key:
        raise ValueError("source_kind and source_key are required for stable_opportunity_id")
    return "o_" + _sha32(f"v1|opportunity|{sk}|{key}")


def opportunity_id_for_deal(deal_key: str) -> str:
    return stable_opportunity_id(source_kind="commercial_deal", source_key=deal_key.strip())


def stable_event_id(
    *,
    opportunity_id: str,
    source_table: str,
    source_record_id: str,
    canonical_event_type: str,
) -> str:
    payload = "|".join(
        [
            "v1|opp_event",
            (opportunity_id or "").strip(),
            (source_table or "").strip(),
            (source_record_id or "").strip(),
            (canonical_event_type or "").strip(),
        ]
    )
    return "oe_" + _sha32(payload)


def stable_evidence_id(
    *,
    opportunity_id: str,
    source_table: str,
    source_record_id: str,
    evidence_type: str,
) -> str:
    payload = "|".join(
        [
            "v1|opp_evidence",
            (opportunity_id or "").strip(),
            (source_table or "").strip(),
            (source_record_id or "").strip(),
            (evidence_type or "").strip(),
        ]
    )
    return "ox_" + _sha32(payload)


def stable_conflict_id(*, reason_code: str, subject_keys: str) -> str:
    payload = f"v1|opp_conflict|{reason_code}|{subject_keys}"
    return "oc_" + _sha32(payload)
