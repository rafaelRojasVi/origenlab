"""Deterministic procurement-event family resolution (retender ≠ new demand)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_identity.normalize import safe_org_normalized

RECURRENCE_OBSERVED_ONCE = "observed_once"
RECURRENCE_REPEATED = "repeated_observed_demand"
RECURRENCE_NOT_ESTABLISHED = "recurrence_not_established"

FAMILY_CONFIRMED = "confirmed_family"
FAMILY_REVIEW = "retender_review_required"
FAMILY_SINGLE = "single_event"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _strip_accents(text: str) -> str:
    nk = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nk if not unicodedata.combining(c))


def normalize_procurement_object(text: str | None) -> str | None:
    """Strict object fingerprint: accent-fold, casefold, collapse whitespace/punct."""
    if not text or not str(text).strip():
        return None
    raw = _strip_accents(str(text)).casefold()
    raw = re.sub(r"[^\w\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    # Drop volatile retender suffixes (municipality name echo, year tags) lightly.
    raw = re.sub(r"\bde\s+rengo\b", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw or None


@dataclass(frozen=True)
class ProcurementEventFamily:
    family_id: str
    member_tender_ids: tuple[str, ...]
    member_tender_codes: tuple[str, ...]
    raw_tender_count: int
    procurement_event_family_count: int
    retender_or_reissue_count: int
    independent_demand_event_count: int
    family_resolution_status: str
    family_reason_codes: tuple[str, ...]
    buyer_key: str | None
    object_fingerprint: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "member_tender_ids": list(self.member_tender_ids),
            "member_tender_codes": list(self.member_tender_codes),
            "raw_tender_count": self.raw_tender_count,
            "procurement_event_family_count": self.procurement_event_family_count,
            "retender_or_reissue_count": self.retender_or_reissue_count,
            "independent_demand_event_count": self.independent_demand_event_count,
            "family_resolution_status": self.family_resolution_status,
            "family_reason_codes": list(self.family_reason_codes),
            "buyer_key": self.buyer_key,
            "object_fingerprint": self.object_fingerprint,
        }


def _tender_code(tender: CoalescedProcurementTender) -> str:
    key = tender.canonical_tender_key or tender.coalesced_tender_id
    return key.split(":", 1)[-1] if ":" in key else key


def _buyer_key(tender: CoalescedProcurementTender) -> str | None:
    buyer_id = (tender.buyer_source_id_selected or "").strip()
    if buyer_id:
        return f"buyer_id:{buyer_id}"
    name = safe_org_normalized(tender.buyer_display_selected or "")
    if name:
        return f"buyer_name:{name}"
    return None


def resolve_procurement_event_families(
    tenders: Iterable[CoalescedProcurementTender],
) -> tuple[list[ProcurementEventFamily], dict[str, str]]:
    """
    High-confidence families only:

    * Same authoritative buyer key + exact normalized procurement-object fingerprint
      → confirmed retender/reissue family (counts as one independent demand event).
    * Otherwise each tender is its own single-event family.
    * Near-matches without exact fingerprint → ``retender_review_required`` when
      buyer matches and object fingerprints share a long common prefix (≥ 48 chars
      after normalization) — still not counted as independent recurrence.
    """
    by_key: dict[tuple[str, str], list[CoalescedProcurementTender]] = defaultdict(list)
    singles: list[CoalescedProcurementTender] = []
    for tender in sorted(tenders, key=lambda t: t.coalesced_tender_id):
        buyer = _buyer_key(tender)
        obj = normalize_procurement_object(tender.title_selected)
        if buyer and obj:
            by_key[(buyer, obj)].append(tender)
        else:
            singles.append(tender)

    families: list[ProcurementEventFamily] = []
    tender_to_family: dict[str, str] = {}

    for (buyer, obj), members in sorted(by_key.items(), key=lambda kv: kv[0]):
        members = sorted(members, key=lambda t: t.coalesced_tender_id)
        ids = tuple(t.coalesced_tender_id for t in members)
        codes = tuple(_tender_code(t) for t in members)
        fid = _digest("event_family", buyer, obj)
        raw = len(members)
        if raw == 1:
            fam = ProcurementEventFamily(
                family_id=fid,
                member_tender_ids=ids,
                member_tender_codes=codes,
                raw_tender_count=1,
                procurement_event_family_count=1,
                retender_or_reissue_count=0,
                independent_demand_event_count=1,
                family_resolution_status=FAMILY_SINGLE,
                family_reason_codes=("single_procurement_event",),
                buyer_key=buyer,
                object_fingerprint=obj,
            )
        else:
            fam = ProcurementEventFamily(
                family_id=fid,
                member_tender_ids=ids,
                member_tender_codes=codes,
                raw_tender_count=raw,
                procurement_event_family_count=1,
                retender_or_reissue_count=raw - 1,
                independent_demand_event_count=1,
                family_resolution_status=FAMILY_CONFIRMED,
                family_reason_codes=(
                    "exact_buyer_and_procurement_object_fingerprint",
                    "retender_or_reissue_collapsed",
                ),
                buyer_key=buyer,
                object_fingerprint=obj,
            )
        families.append(fam)
        for tid in ids:
            tender_to_family[tid] = fid

    # Review-required: same buyer, near-identical object (prefix), not exact.
    by_buyer: dict[str, list[tuple[str, CoalescedProcurementTender]]] = defaultdict(
        list
    )
    claimed = set(tender_to_family)
    for tender in sorted(tenders, key=lambda t: t.coalesced_tender_id):
        if tender.coalesced_tender_id in claimed:
            continue
        buyer = _buyer_key(tender)
        obj = normalize_procurement_object(tender.title_selected)
        if buyer and obj:
            by_buyer[buyer].append((obj, tender))

    for buyer, items in sorted(by_buyer.items()):
        # Pairwise near-match clustering (deterministic).
        used: set[str] = set()
        items = sorted(items, key=lambda x: (x[0], x[1].coalesced_tender_id))
        for i, (obj_i, t_i) in enumerate(items):
            if t_i.coalesced_tender_id in used:
                continue
            cluster = [t_i]
            used.add(t_i.coalesced_tender_id)
            for obj_j, t_j in items[i + 1 :]:
                if t_j.coalesced_tender_id in used:
                    continue
                # High-confidence near match: share ≥48-char prefix or containment.
                if (
                    len(obj_i) >= 48
                    and len(obj_j) >= 48
                    and (
                        obj_i[:48] == obj_j[:48]
                        or obj_i in obj_j
                        or obj_j in obj_i
                    )
                ):
                    cluster.append(t_j)
                    used.add(t_j.coalesced_tender_id)
            if len(cluster) == 1:
                singles.append(cluster[0])
                continue
            cluster = sorted(cluster, key=lambda t: t.coalesced_tender_id)
            ids = tuple(t.coalesced_tender_id for t in cluster)
            codes = tuple(_tender_code(t) for t in cluster)
            objs = sorted(
                {
                    normalize_procurement_object(t.title_selected) or ""
                    for t in cluster
                }
            )
            fid = _digest("event_family_review", buyer, *objs)
            fam = ProcurementEventFamily(
                family_id=fid,
                member_tender_ids=ids,
                member_tender_codes=codes,
                raw_tender_count=len(cluster),
                procurement_event_family_count=1,
                retender_or_reissue_count=len(cluster) - 1,
                independent_demand_event_count=0,  # not established
                family_resolution_status=FAMILY_REVIEW,
                family_reason_codes=(
                    "suspected_retender_near_object_match",
                    "retender_review_required",
                    "independence_not_established",
                ),
                buyer_key=buyer,
                object_fingerprint=objs[0] if objs else None,
            )
            families.append(fam)
            for tid in ids:
                tender_to_family[tid] = fid

    for tender in sorted(singles, key=lambda t: t.coalesced_tender_id):
        if tender.coalesced_tender_id in tender_to_family:
            continue
        tid = tender.coalesced_tender_id
        code = _tender_code(tender)
        fid = _digest("event_family_single", tid)
        fam = ProcurementEventFamily(
            family_id=fid,
            member_tender_ids=(tid,),
            member_tender_codes=(code,),
            raw_tender_count=1,
            procurement_event_family_count=1,
            retender_or_reissue_count=0,
            independent_demand_event_count=1,
            family_resolution_status=FAMILY_SINGLE,
            family_reason_codes=("single_procurement_event",),
            buyer_key=_buyer_key(tender),
            object_fingerprint=normalize_procurement_object(tender.title_selected),
        )
        families.append(fam)
        tender_to_family[tid] = fid

    families.sort(key=lambda f: f.family_id)
    return families, tender_to_family


def recurrence_from_independent_events(
    independent_demand_event_count: int,
    *,
    family_resolution_status: str | None = None,
) -> str:
    if family_resolution_status == FAMILY_REVIEW:
        return RECURRENCE_NOT_ESTABLISHED
    if independent_demand_event_count >= 2:
        return RECURRENCE_REPEATED
    if independent_demand_event_count == 1:
        return RECURRENCE_OBSERVED_ONCE
    return RECURRENCE_NOT_ESTABLISHED


__all__ = [
    "FAMILY_CONFIRMED",
    "FAMILY_REVIEW",
    "FAMILY_SINGLE",
    "ProcurementEventFamily",
    "RECURRENCE_NOT_ESTABLISHED",
    "RECURRENCE_OBSERVED_ONCE",
    "RECURRENCE_REPEATED",
    "normalize_procurement_object",
    "recurrence_from_independent_events",
    "resolve_procurement_event_families",
]
