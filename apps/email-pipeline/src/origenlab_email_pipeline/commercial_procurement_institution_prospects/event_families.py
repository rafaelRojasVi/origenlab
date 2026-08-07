"""Deterministic procurement-event family resolution (retender ≠ new demand).

The ladder is intentionally conservative: an exact buyer + title match is *not*
evidence that two tenders describe the same procurement event. Only an explicit
relationship marker or a shared stable project identifier can collapse tenders
into one confirmed event; everything else that merely looks alike is surfaced as
an unresolved relationship for operator review.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from origenlab_email_pipeline.commercial_identity.normalize import safe_org_normalized
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)

RECURRENCE_OBSERVED_ONCE = "observed_once_confirmed"
RECURRENCE_REPEATED = "repeated_observed_demand_confirmed"
RECURRENCE_NOT_ESTABLISHED = "recurrence_not_established"
RECURRENCE_REPEATED_UNRESOLVED = "repeated_confirmed_with_unresolved_relationships"

RECURRENCE_STATUSES = frozenset(
    {
        RECURRENCE_OBSERVED_ONCE,
        RECURRENCE_REPEATED,
        RECURRENCE_NOT_ESTABLISHED,
        RECURRENCE_REPEATED_UNRESOLVED,
    }
)

FAMILY_CONFIRMED = "confirmed_family"
FAMILY_REVIEW = "retender_review_required"
FAMILY_SINGLE = "single_event"

# An explicit statement that a tender replaces or re-runs another procurement.
RELATIONSHIP_MARKER_RE = re.compile(
    r"\breissue\b"
    r"|\brelicitaci[oó]n\b|\brelicitacion\b"
    r"|\bsegunda\s+lic"
    r"|\ben\s+reemplazo\b"
    r"|\breemplaza\s+a\b"
    r"|\banula\b"
    r"|\bdeja\s+sin\s+efecto\b"
)

# Stable identifiers that survive a re-tender. Each pattern must capture a token
# containing digits so generic words ("proyecto CORFO") cannot collapse events.
PROJECT_IDENTIFIER_PATTERNS = (
    ("bip", re.compile(r"\bbip\s*[n°o]*\s*(\d{3,})")),
    ("proyecto", re.compile(r"\bproyecto\s+([a-z0-9][a-z0-9\-]*\d[a-z0-9\-]*)")),
    ("requisicion", re.compile(r"\brequisici[oó]n\s*[n°o]*\s*(\d{2,})")),
)

# Near-identical object thresholds.
MIN_EQUAL_OBJECT_LENGTH = 12
MIN_CONTAINMENT_LENGTH = 40
# Chronology windows (days).
REISSUE_WINDOW_DAYS = 120
INDEPENDENT_EVENT_WINDOW_DAYS = 300

# Generic procurement titles that must not create review components by themselves.
GENERIC_PROCUREMENT_OBJECTS = frozenset(
    {
        "adquisicion de equipos",
        "adquisicion de equipo",
        "compra de equipos",
        "compra de equipo",
        "adquisicion de bienes",
        "contratacion de equipos",
    }
)

RELATION_CONFIRMED = "confirmed_same_event"
RELATION_REVIEW = "unresolved_relationship"
RELATION_INDEPENDENT = "independent"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _strip_accents(text: str) -> str:
    nk = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nk if not unicodedata.combining(c))


def normalize_procurement_object(text: str | None) -> str | None:
    """Object fingerprint: accent-fold, casefold, collapse punctuation/whitespace."""
    if not text or not str(text).strip():
        return None
    raw = _strip_accents(str(text)).casefold()
    raw = re.sub(r"[^\w\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw or None


def _normalized_title(tender: CoalescedProcurementTender) -> str:
    return normalize_procurement_object(tender.title_selected) or ""


def has_relationship_marker(text: str | None) -> bool:
    """True when a title explicitly states it re-runs or annuls another tender."""
    return bool(RELATIONSHIP_MARKER_RE.search(normalize_procurement_object(text) or ""))


def project_identifiers(text: str | None) -> frozenset[str]:
    """Stable project / BIP / requisition identifiers present in a title."""
    norm = normalize_procurement_object(text) or ""
    found: set[str] = set()
    for kind, pattern in PROJECT_IDENTIFIER_PATTERNS:
        for match in pattern.finditer(norm):
            found.add(f"{kind}:{match.group(1)}")
    return frozenset(found)


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _observed_at(tender: CoalescedProcurementTender) -> datetime | None:
    return _parse_timestamp(tender.publication_timestamp_selected) or _parse_timestamp(
        tender.close_timestamp_selected
    )


def _gap_days(
    a: CoalescedProcurementTender, b: CoalescedProcurementTender
) -> int | None:
    a_dt, b_dt = _observed_at(a), _observed_at(b)
    if a_dt is None or b_dt is None:
        return None
    return abs((a_dt - b_dt).days)


def _near_identical(a_obj: str, b_obj: str) -> tuple[bool, str | None]:
    if not a_obj or not b_obj:
        return False, None
    if a_obj in GENERIC_PROCUREMENT_OBJECTS or b_obj in GENERIC_PROCUREMENT_OBJECTS:
        # Generic titles create review noise, not a defensible relationship.
        return False, None
    if a_obj == b_obj and len(a_obj) >= MIN_EQUAL_OBJECT_LENGTH:
        return True, "normalized_object_equality"
    shorter, longer = (a_obj, b_obj) if len(a_obj) <= len(b_obj) else (b_obj, a_obj)
    if len(shorter) >= MIN_CONTAINMENT_LENGTH and shorter in longer:
        return True, "normalized_object_containment"
    return False, None


def is_generic_procurement_object(text: str | None) -> bool:
    """True when the title is a generic procurement phrase with no product signal."""
    norm = normalize_procurement_object(text)
    return bool(norm and norm in GENERIC_PROCUREMENT_OBJECTS)


@dataclass(frozen=True)
class ProcurementRelationshipEdge:
    """One evaluated pair of tenders and the evidence behind its relation."""

    left_tender_id: str
    right_tender_id: str
    relation: str
    gap_days: int | None
    shared_project_identifiers: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_tender_id": self.left_tender_id,
            "right_tender_id": self.right_tender_id,
            "relation": self.relation,
            "gap_days": self.gap_days,
            "shared_project_identifiers": list(self.shared_project_identifiers),
            "reason_codes": list(self.reason_codes),
        }


def classify_tender_relationship(
    left: CoalescedProcurementTender,
    right: CoalescedProcurementTender,
) -> ProcurementRelationshipEdge:
    """Evidence ladder for one same-buyer tender pair."""
    left_obj = _normalized_title(left)
    right_obj = _normalized_title(right)
    gap = _gap_days(left, right)
    shared = tuple(
        sorted(
            project_identifiers(left.title_selected)
            & project_identifiers(right.title_selected)
        )
    )
    marker = has_relationship_marker(left.title_selected) or has_relationship_marker(
        right.title_selected
    )
    near, near_reason = _near_identical(left_obj, right_obj)
    chronology_known = gap is not None
    chronology_compatible = chronology_known and gap <= INDEPENDENT_EVENT_WINDOW_DAYS

    def edge(relation: str, *reasons: str) -> ProcurementRelationshipEdge:
        return ProcurementRelationshipEdge(
            left_tender_id=left.coalesced_tender_id,
            right_tender_id=right.coalesced_tender_id,
            relation=relation,
            gap_days=gap,
            shared_project_identifiers=shared,
            reason_codes=tuple(sorted(set(reasons))),
        )

    if shared and chronology_compatible:
        return edge(
            RELATION_CONFIRMED,
            "shared_stable_project_identifier",
            "chronology_compatible",
        )
    if shared and not chronology_known:
        return edge(
            RELATION_REVIEW,
            "shared_stable_project_identifier",
            "missing_publication_chronology",
        )
    if marker and near and chronology_compatible:
        return edge(
            RELATION_CONFIRMED,
            "explicit_reissue_or_replacement_marker",
            "chronology_compatible",
            near_reason or "near_identical_object",
        )
    if marker and near:
        return edge(
            RELATION_REVIEW,
            "explicit_reissue_or_replacement_marker",
            "missing_publication_chronology"
            if not chronology_known
            else "relationship_marker_outside_chronology_window",
            near_reason or "near_identical_object",
        )
    if not near:
        return edge(RELATION_INDEPENDENT, "distinct_procurement_object")
    if not chronology_known:
        return edge(
            RELATION_REVIEW,
            near_reason or "near_identical_object",
            "missing_publication_chronology",
            "independence_not_established",
        )
    if gap >= INDEPENDENT_EVENT_WINDOW_DAYS:
        return edge(
            RELATION_INDEPENDENT,
            near_reason or "near_identical_object",
            "repeat_beyond_independent_event_window",
        )
    return edge(
        RELATION_REVIEW,
        near_reason or "near_identical_object",
        "near_term_reissue_window"
        if gap < REISSUE_WINDOW_DAYS
        else "intermediate_reissue_window",
        "no_relationship_marker",
        "independence_not_established",
    )


class _UnionFind:
    """Order-independent connected components."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        # Deterministic representative: lowest id wins regardless of merge order.
        low, high = (a, b) if a <= b else (b, a)
        self._parent[high] = low

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for item in sorted(self._parent):
            out[self.find(item)].append(item)
        return dict(out)


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
    confirmed_same_event_family_count: int = 1
    confirmed_independent_event_count: int = 1
    independent_event_count_lower_bound: int = 1
    independent_event_count_upper_bound: int = 1
    unresolved_relationship_count: int = 0
    recurrence_status: str = RECURRENCE_OBSERVED_ONCE
    relationship_edges: tuple[ProcurementRelationshipEdge, ...] = ()

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
            "confirmed_same_event_family_count": (
                self.confirmed_same_event_family_count
            ),
            "confirmed_independent_event_count": (
                self.confirmed_independent_event_count
            ),
            "independent_event_count_lower_bound": (
                self.independent_event_count_lower_bound
            ),
            "independent_event_count_upper_bound": (
                self.independent_event_count_upper_bound
            ),
            "unresolved_relationship_count": self.unresolved_relationship_count,
            "recurrence_status": self.recurrence_status,
            "relationship_edges": [e.to_dict() for e in self.relationship_edges],
        }


def _tender_code(tender: CoalescedProcurementTender) -> str:
    key = tender.canonical_tender_key or tender.coalesced_tender_id
    return key.split(":", 1)[-1] if ":" in key else key


def _buyer_key(tender: CoalescedProcurementTender) -> str | None:
    buyer_id = (tender.buyer_source_id_selected or "").strip()
    purchasing_unit = ""
    provenance = tender.selected_field_provenance or {}
    if isinstance(provenance, dict):
        purchasing_unit = str(provenance.get("purchasing_unit") or "").strip()
    if not purchasing_unit and tender.buyer_display_selected:
        # Preserve distinct buying units encoded after a slash when present.
        display = str(tender.buyer_display_selected)
        if "/" in display:
            purchasing_unit = display.split("/", 1)[1].strip()
    unit_suffix = f"|unit:{safe_org_normalized(purchasing_unit)}" if purchasing_unit else ""
    if buyer_id:
        return f"buyer_id:{buyer_id}{unit_suffix}"
    name = safe_org_normalized(tender.buyer_display_selected or "")
    if name:
        return f"buyer_name:{name}{unit_suffix}"
    return None


def resolve_procurement_event_families(
    tenders: Iterable[CoalescedProcurementTender],
) -> tuple[list[ProcurementEventFamily], dict[str, str]]:
    """
    Resolve tenders into procurement-event families using a conservative ladder.

    * ``confirmed_family`` — an explicit reissue/replacement marker on a
      near-identical object, or a shared stable project identifier, with
      compatible chronology. Counts as one independent demand event.
    * ``retender_review_required`` — the objects look alike (or share an
      identifier) but nothing authoritative proves they are the same event.
      Independence is neither confirmed nor denied; the operator decides.
    * ``single_event`` — nothing links the tender to another one.

    Relationships are resolved as order-independent connected components, so
    input ordering never changes family membership.
    """
    ordered = sorted(tenders, key=lambda t: t.coalesced_tender_id)
    by_id = {t.coalesced_tender_id: t for t in ordered}
    by_buyer: dict[str, list[CoalescedProcurementTender]] = defaultdict(list)
    unkeyed: list[CoalescedProcurementTender] = []
    for tender in ordered:
        buyer = _buyer_key(tender)
        if buyer and _normalized_title(tender):
            by_buyer[buyer].append(tender)
        else:
            unkeyed.append(tender)

    confirmed_uf = _UnionFind()
    review_edges: list[ProcurementRelationshipEdge] = []
    confirmed_edges: list[ProcurementRelationshipEdge] = []
    buyer_of: dict[str, str] = {}
    for tender in ordered:
        confirmed_uf.add(tender.coalesced_tender_id)

    for buyer, members in sorted(by_buyer.items()):
        for tender in members:
            buyer_of[tender.coalesced_tender_id] = buyer
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                edge = classify_tender_relationship(left, right)
                if edge.relation == RELATION_CONFIRMED:
                    confirmed_edges.append(edge)
                    confirmed_uf.union(edge.left_tender_id, edge.right_tender_id)
                elif edge.relation == RELATION_REVIEW:
                    review_edges.append(edge)

    confirmed_groups = confirmed_uf.groups()
    group_of_tender = {
        tid: root for root, members in confirmed_groups.items() for tid in members
    }

    review_uf = _UnionFind()
    for root in confirmed_groups:
        review_uf.add(root)
    for edge in review_edges:
        review_uf.union(
            group_of_tender[edge.left_tender_id],
            group_of_tender[edge.right_tender_id],
        )
    review_clusters = review_uf.groups()

    edges_by_group_pair: dict[str, list[ProcurementRelationshipEdge]] = defaultdict(
        list
    )
    for edge in review_edges:
        edges_by_group_pair[review_uf.find(group_of_tender[edge.left_tender_id])].append(
            edge
        )
    confirmed_by_group: dict[str, list[ProcurementRelationshipEdge]] = defaultdict(list)
    for edge in confirmed_edges:
        confirmed_by_group[group_of_tender[edge.left_tender_id]].append(edge)

    families: list[ProcurementEventFamily] = []
    tender_to_family: dict[str, str] = {}

    for cluster_root in sorted(review_clusters):
        group_roots = sorted(review_clusters[cluster_root])
        member_ids = sorted(
            tid for root in group_roots for tid in confirmed_groups[root]
        )
        members = [by_id[tid] for tid in member_ids]
        codes = tuple(_tender_code(t) for t in members)
        buyer = buyer_of.get(member_ids[0])
        objects = sorted({_normalized_title(t) for t in members if _normalized_title(t)})
        confirmed_group_count = len(group_roots)
        cluster_edges = tuple(
            sorted(
                edges_by_group_pair.get(cluster_root, []),
                key=lambda e: (e.left_tender_id, e.right_tender_id),
            )
        )
        group_confirmed_edges = tuple(
            sorted(
                (e for root in group_roots for e in confirmed_by_group.get(root, [])),
                key=lambda e: (e.left_tender_id, e.right_tender_id),
            )
        )

        if confirmed_group_count > 1:
            reasons = sorted(
                {"retender_review_required", "independence_not_established"}
                | {c for e in cluster_edges for c in e.reason_codes}
            )
            # Unresolved relationships are not factual retenders.
            family = ProcurementEventFamily(
                family_id=_digest("event_family_review", *member_ids),
                member_tender_ids=tuple(member_ids),
                member_tender_codes=codes,
                raw_tender_count=len(member_ids),
                procurement_event_family_count=1,
                retender_or_reissue_count=0,
                independent_demand_event_count=0,
                family_resolution_status=FAMILY_REVIEW,
                family_reason_codes=tuple(reasons),
                buyer_key=buyer,
                object_fingerprint=objects[0] if objects else None,
                confirmed_same_event_family_count=confirmed_group_count,
                confirmed_independent_event_count=0,
                independent_event_count_lower_bound=1,
                independent_event_count_upper_bound=confirmed_group_count,
                unresolved_relationship_count=confirmed_group_count - 1,
                recurrence_status=RECURRENCE_NOT_ESTABLISHED,
                relationship_edges=cluster_edges + group_confirmed_edges,
            )
        elif len(member_ids) > 1:
            reasons = sorted(
                {"confirmed_same_procurement_event", "retender_or_reissue_collapsed"}
                | {c for e in group_confirmed_edges for c in e.reason_codes}
            )
            family = ProcurementEventFamily(
                family_id=_digest("event_family_confirmed", *member_ids),
                member_tender_ids=tuple(member_ids),
                member_tender_codes=codes,
                raw_tender_count=len(member_ids),
                procurement_event_family_count=1,
                retender_or_reissue_count=len(member_ids) - 1,
                independent_demand_event_count=1,
                family_resolution_status=FAMILY_CONFIRMED,
                family_reason_codes=tuple(reasons),
                buyer_key=buyer,
                object_fingerprint=objects[0] if objects else None,
                confirmed_same_event_family_count=1,
                confirmed_independent_event_count=1,
                independent_event_count_lower_bound=1,
                independent_event_count_upper_bound=1,
                unresolved_relationship_count=0,
                recurrence_status=RECURRENCE_OBSERVED_ONCE,
                relationship_edges=group_confirmed_edges,
            )
        else:
            family = ProcurementEventFamily(
                family_id=_digest("event_family_single", member_ids[0]),
                member_tender_ids=tuple(member_ids),
                member_tender_codes=codes,
                raw_tender_count=1,
                procurement_event_family_count=1,
                retender_or_reissue_count=0,
                independent_demand_event_count=1,
                family_resolution_status=FAMILY_SINGLE,
                family_reason_codes=("single_procurement_event",),
                buyer_key=buyer,
                object_fingerprint=objects[0] if objects else None,
                confirmed_same_event_family_count=1,
                confirmed_independent_event_count=1,
                independent_event_count_lower_bound=1,
                independent_event_count_upper_bound=1,
                unresolved_relationship_count=0,
                recurrence_status=RECURRENCE_OBSERVED_ONCE,
            )
        families.append(family)
        for tid in member_ids:
            tender_to_family[tid] = family.family_id

    for tender in unkeyed:
        tid = tender.coalesced_tender_id
        if tid in tender_to_family:
            continue
        family = ProcurementEventFamily(
            family_id=_digest("event_family_single", tid),
            member_tender_ids=(tid,),
            member_tender_codes=(_tender_code(tender),),
            raw_tender_count=1,
            procurement_event_family_count=1,
            retender_or_reissue_count=0,
            independent_demand_event_count=1,
            family_resolution_status=FAMILY_SINGLE,
            family_reason_codes=("single_procurement_event",),
            buyer_key=_buyer_key(tender),
            object_fingerprint=normalize_procurement_object(tender.title_selected),
        )
        families.append(family)
        tender_to_family[tid] = family.family_id

    families.sort(key=lambda f: f.family_id)
    return families, tender_to_family


def recurrence_from_independent_events(
    independent_demand_event_count: int,
    *,
    family_resolution_status: str | None = None,
    unresolved_relationship_count: int = 0,
) -> str:
    """Map confirmed independent events + unresolved links → recurrence status."""
    if independent_demand_event_count >= 2 and unresolved_relationship_count > 0:
        return RECURRENCE_REPEATED_UNRESOLVED
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
    "INDEPENDENT_EVENT_WINDOW_DAYS",
    "ProcurementEventFamily",
    "ProcurementRelationshipEdge",
    "RECURRENCE_NOT_ESTABLISHED",
    "RECURRENCE_OBSERVED_ONCE",
    "RECURRENCE_REPEATED",
    "RECURRENCE_REPEATED_UNRESOLVED",
    "RECURRENCE_STATUSES",
    "REISSUE_WINDOW_DAYS",
    "RELATION_CONFIRMED",
    "RELATION_INDEPENDENT",
    "RELATION_REVIEW",
    "classify_tender_relationship",
    "has_relationship_marker",
    "normalize_procurement_object",
    "project_identifiers",
    "recurrence_from_independent_events",
    "resolve_procurement_event_families",
]
