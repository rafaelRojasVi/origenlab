"""Source-native vs source-neutral canonical tender identity helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Documented Mercado Público CodigoExterno shape used for cross-source candidates.
# Examples: 9999-1-LE26, 1003473-64-LP26
MERCADO_PUBLICO_CODIGO_EXTERNO_RE = re.compile(
    r"^\d{1,12}-\d{1,6}-[A-Za-z]{1,4}\d{2}$"
)


@dataclass(frozen=True)
class CanonicalTenderCandidate:
    source_native_tender_key: str
    canonical_tender_key_candidate: str | None
    canonical_candidate_kind: str
    canonical_candidate_reason: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source_native_tender_key": self.source_native_tender_key,
            "canonical_tender_key_candidate": self.canonical_tender_key_candidate,
            "canonical_candidate_kind": self.canonical_candidate_kind,
            "canonical_candidate_reason": self.canonical_candidate_reason,
        }


def normalize_mercado_publico_codigo(raw: str | None) -> str | None:
    """Trim and casefold a Mercado Público code; None if empty after trim."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Collapse internal whitespace then casefold.
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def is_mercado_publico_codigo_shape(normalized: str | None) -> bool:
    if not normalized:
        return False
    # Pattern is case-insensitive via casefold input; match against upper-friendly form.
    return bool(MERCADO_PUBLICO_CODIGO_EXTERNO_RE.match(normalized.casefold()))


def ticket_source_native_key(codigo: str) -> str:
    norm = normalize_mercado_publico_codigo(codigo) or (codigo or "").strip().casefold()
    return f"ticket_api:codigo_externo:{norm}"


def ticket_canonical_candidate(codigo: str | None) -> CanonicalTenderCandidate:
    native = ticket_source_native_key(codigo or "")
    norm = normalize_mercado_publico_codigo(codigo)
    if not norm:
        return CanonicalTenderCandidate(
            source_native_tender_key=native,
            canonical_tender_key_candidate=None,
            canonical_candidate_kind="none",
            canonical_candidate_reason="missing_codigo_externo",
        )
    if not is_mercado_publico_codigo_shape(norm):
        return CanonicalTenderCandidate(
            source_native_tender_key=native,
            canonical_tender_key_candidate=None,
            canonical_candidate_kind="none",
            canonical_candidate_reason="malformed_codigo_externo_unresolved",
        )
    return CanonicalTenderCandidate(
        source_native_tender_key=native,
        canonical_tender_key_candidate=norm,
        canonical_candidate_kind="mercado_publico_codigo_externo",
        canonical_candidate_reason="ticket_codigo_externo",
    )


def ocds_source_native_key(
    *,
    ocid: str | None,
    release_id: str | None,
    tender_id: str | None,
    release_kind: str,
) -> str:
    parts = [
        f"ocid:{(ocid or '').strip().casefold()}",
        f"release:{(release_id or '').strip().casefold()}",
        f"tender:{(tender_id or '').strip().casefold()}",
        f"kind:{release_kind}",
    ]
    return "ocds:" + "|".join(parts)


def ocds_canonical_candidate(
    *,
    ocid: str | None,
    release_id: str | None,
    tender_id: str | None,
    release_kind: str,
) -> CanonicalTenderCandidate:
    native = ocds_source_native_key(
        ocid=ocid, release_id=release_id, tender_id=tender_id, release_kind=release_kind
    )
    norm = normalize_mercado_publico_codigo(tender_id)
    if norm and is_mercado_publico_codigo_shape(norm):
        return CanonicalTenderCandidate(
            source_native_tender_key=native,
            canonical_tender_key_candidate=norm,
            canonical_candidate_kind="mercado_publico_codigo_externo",
            canonical_candidate_reason="ocds_tender_id_matches_mp_codigo_shape",
        )
    if tender_id and str(tender_id).strip():
        return CanonicalTenderCandidate(
            source_native_tender_key=native,
            canonical_tender_key_candidate=None,
            canonical_candidate_kind="none",
            canonical_candidate_reason="ocds_tender_id_not_mp_codigo_shape",
        )
    if ocid and str(ocid).strip():
        return CanonicalTenderCandidate(
            source_native_tender_key=native,
            canonical_tender_key_candidate=None,
            canonical_candidate_kind="none",
            canonical_candidate_reason="unresolved_ocid_only_no_mp_codigo",
        )
    return CanonicalTenderCandidate(
        source_native_tender_key=native,
        canonical_tender_key_candidate=None,
        canonical_candidate_kind="none",
        canonical_candidate_reason="unresolved_missing_ocds_identifiers",
    )
