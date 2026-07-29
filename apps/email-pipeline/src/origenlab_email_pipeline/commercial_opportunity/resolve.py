"""Pure deterministic opportunity-stage resolver (PR3).

Does not write SQLite. Does not reimplement PR2 identity matching — consumes
an IdentityResolution. Does not invent next-action, tender, or product-interest.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from origenlab_email_pipeline.commercial_identity.models import IdentityResolution
from origenlab_email_pipeline.commercial_identity.normalize import (
    is_consumer_domain,
    is_institutional_domain,
    normalize_identity_email,
)
from origenlab_email_pipeline.commercial_opportunity.constants import (
    BUILD_CONTRACT,
    HARD_TERMINAL_STAGES,
    IDENTITY_LINK_ACCOUNT_VIA_DEAL_DOMAIN,
    IDENTITY_LINK_AMBIGUOUS,
    IDENTITY_LINK_LINKED,
    IDENTITY_LINK_NOT_APPLICABLE,
    IDENTITY_LINK_UNRESOLVED,
    IDENTITY_LINK_WITHHELD,
    RECORD_KIND_EVIDENCE_CANDIDATE,
    RECORD_KIND_EXPLICIT,
    RECORD_KIND_HISTORY,
    REVIEW_STATUS_OK,
    REVIEW_STATUS_REQUIRED,
    SCHEMA_VERSION,
    UNDATED_TERMINAL_SOURCE_STATUSES,
)
from origenlab_email_pipeline.commercial_opportunity.ids import (
    opportunity_id_for_deal,
    stable_conflict_id,
    stable_event_id,
    stable_evidence_id,
    stable_opportunity_id,
)
from origenlab_email_pipeline.commercial_opportunity.models import (
    OpportunityConflictRecord,
    OpportunityEventRecord,
    OpportunityEvidenceRecord,
    OpportunityRecord,
    OpportunityResolution,
    SourceContactMasterRow,
    SourceDealDocumentRow,
    SourceDealEventRow,
    SourceDealPaymentRow,
    SourceDealRow,
    SourceSignalRow,
    StageCandidate,
)
from origenlab_email_pipeline.commercial_opportunity.review_status import (
    derive_opportunity_review_status,
)
from origenlab_email_pipeline.commercial_opportunity.stage_map import (
    map_deal_status,
    map_document_type,
    map_event_type,
    select_stage,
)
from origenlab_email_pipeline.commercial_opportunity.timestamps import (
    MALFORMED_TIMESTAMP_REASON,
    parse_event_time,
)

# Closed provenance rows are kept as evidence but must not compete for stage
# or manufacture regression conflicts against supporting terminals.
_CLOSED_PROVENANCE_STAGES = frozenset({"closed", "deal_closed"})
_CLOSED_SUPPORT_STAGES = frozenset({"won", "post_sale", "lost"})
_TERMINAL_CLAIM_STAGES = frozenset({"won", "post_sale", "lost"})



def _json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _email_token(email: str | None) -> str | None:
    """Stable redacted token — never put raw email into conflict subject keys."""
    em = normalize_identity_email(email) if email else None
    if not em:
        return None
    return "em_" + hashlib.sha256(em.encode("utf-8")).hexdigest()[:16]


def _email_domain(email: str | None) -> str | None:
    em = normalize_identity_email(email) if email else None
    if not em or "@" not in em:
        return None
    return em.split("@", 1)[1]


def _empty_metrics() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "source_deals_inspected": 0,
        "source_events_inspected": 0,
        "source_documents_inspected": 0,
        "source_payments_inspected": 0,
        "source_signals_inspected": 0,
        "canonical_opportunity_count": 0,
        "explicit_deal_opportunity_count": 0,
        "evidence_candidate_count": 0,
        "commercial_history_count": 0,
        "current_opportunity_count": 0,
        "terminal_opportunity_count": 0,
        "linked_account_count": 0,
        "linked_contact_count": 0,
        "unresolved_identity_count": 0,
        "opportunity_conflict_count": 0,
        "missing_event_timestamp_count": 0,
        "undated_signal_history_count": 0,
        "stage_distribution": {},
        "confidence_distribution": {},
        "conflict_distribution": {},
        "identity_fingerprint_match_status": "not_checked",
        "opportunity_stage_fields_inferred": True,
        "next_action_fields_inferred": False,
        "tender_fields_inferred": False,
        "product_interest_fields_inferred": False,
        "metric_definitions": {
            "source_deals_inspected": "Rows read from commercial_deal",
            "source_events_inspected": "Rows read from commercial_deal_event",
            "source_documents_inspected": "Rows read from commercial_deal_document",
            "source_payments_inspected": "Rows read from commercial_deal_payment",
            "source_signals_inspected": "Rows read from opportunity_signals",
            "canonical_opportunity_count": "Total opportunity rows emitted",
            "explicit_deal_opportunity_count": "Opportunities with record_kind=explicit_opportunity",
            "evidence_candidate_count": "Opportunities with record_kind=evidence_candidate",
            "commercial_history_count": "Opportunities with record_kind=commercial_history",
            "current_opportunity_count": "Opportunities with stage_is_current=true",
            "terminal_opportunity_count": "Opportunities with stage_is_terminal=true",
            "linked_account_count": "Distinct non-null account_id on opportunities",
            "linked_contact_count": "Distinct non-null primary_contact_id on opportunities",
            "unresolved_identity_count": "Opportunities with unresolved/ambiguous identity links",
            "opportunity_conflict_count": "Conflict rows emitted",
            "missing_event_timestamp_count": "Stage-bearing events/docs/payments with empty timestamps",
            "undated_signal_history_count": "Signals treated as history because event time unrecovered",
            "identity_fingerprint_match_status": "matched|mismatched|missing|schema_mismatch|version_mismatch|not_checked",
            "opportunity_stage_fields_inferred": "Always true for PR3 stage fields",
            "next_action_fields_inferred": "Always false — PR3 does not generate next actions",
            "tender_fields_inferred": "Always false — tender is a separate dimension",
            "product_interest_fields_inferred": "Always false — product interest is out of scope",
        },
    }


def _identity_indexes(
    identity: IdentityResolution,
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    by_email: dict[str, Any] = {}
    for c in identity.contacts:
        by_email[c.normalized_email] = c
    by_domain: dict[str, list[Any]] = defaultdict(list)
    for a in identity.accounts:
        if a.primary_domain:
            by_domain[a.primary_domain].append(a)
        for dom in a.domains:
            by_domain[dom].append(a)
    for d, lst in list(by_domain.items()):
        seen: set[str] = set()
        uniq = []
        for a in lst:
            if a.account_id not in seen:
                seen.add(a.account_id)
                uniq.append(a)
        by_domain[d] = uniq
    return by_email, by_domain


def _link_identity(
    *,
    contact_email: str | None,
    client_domain: str | None,
    by_email: dict[str, Any],
    by_domain: dict[str, list[Any]],
) -> tuple[str | None, str | None, str]:
    """Return (account_id, contact_id, link_status).

    Consumer contact + unambiguous institutional deal domain → account via deal
    domain while contact membership remains withheld (PR2 unchanged).
    """
    email = normalize_identity_email(contact_email) if contact_email else None
    contact = by_email.get(email) if email else None
    domain = (client_domain or "").strip().lower() or None

    if contact is not None:
        if contact.identity_status == "internal_actor":
            return None, contact.contact_id, IDENTITY_LINK_WITHHELD
        if contact.account_id:
            return contact.account_id, contact.contact_id, IDENTITY_LINK_LINKED

        # Consumer / withheld contact: may still attach deal account via domain.
        if contact.identity_status in {"consumer_email", "unlinked", "withheld"} or (
            contact.email_domain and is_consumer_domain(contact.email_domain)
        ):
            if domain and is_institutional_domain(domain):
                accounts = by_domain.get(domain) or []
                if len(accounts) == 1:
                    return (
                        accounts[0].account_id,
                        contact.contact_id,
                        IDENTITY_LINK_ACCOUNT_VIA_DEAL_DOMAIN,
                    )
                if len(accounts) > 1:
                    return None, contact.contact_id, IDENTITY_LINK_AMBIGUOUS
            return None, contact.contact_id, IDENTITY_LINK_WITHHELD
        return None, contact.contact_id, IDENTITY_LINK_UNRESOLVED

    if domain:
        accounts = by_domain.get(domain) or []
        if len(accounts) == 1:
            return accounts[0].account_id, None, IDENTITY_LINK_LINKED
        if len(accounts) > 1:
            return None, None, IDENTITY_LINK_AMBIGUOUS
    return None, None, IDENTITY_LINK_UNRESOLVED


def _add_conflict(
    conflicts: list[OpportunityConflictRecord],
    *,
    reason_code: str,
    opportunity_id: str | None,
    subject_keys: dict[str, Any],
    evidence_pointers: list[dict[str, Any]],
    detail: dict[str, Any] | None = None,
) -> None:
    sk = _json(subject_keys)
    conflicts.append(
        OpportunityConflictRecord(
            conflict_id=stable_conflict_id(reason_code=reason_code, subject_keys=sk),
            opportunity_id=opportunity_id,
            conflict_type=reason_code,
            reason_code=reason_code,
            subject_keys_json=sk,
            evidence_pointers_json=_json(evidence_pointers),
            review_status=REVIEW_STATUS_REQUIRED,
            detail_json=_json(detail or {}),
        )
    )


def _append_evidence(
    opp_evidence: list[OpportunityEvidenceRecord],
    *,
    evidence_id: str,
    opportunity_id: str,
    source_table: str,
    source_record_id: str,
    evidence_type: str,
    evidence_at: str | None,
    confidence: str,
    reason_code: str,
    source_email_id: int | None = None,
    source_attachment_id: int | None = None,
) -> None:
    opp_evidence.append(
        OpportunityEvidenceRecord(
            evidence_id=evidence_id,
            opportunity_id=opportunity_id,
            subject_kind="opportunity",
            source_table=source_table,
            source_record_id=source_record_id,
            evidence_type=evidence_type,
            evidence_at=evidence_at,
            confidence=confidence,
            reason_code=reason_code,
            source_email_id=source_email_id,
            source_attachment_id=source_attachment_id,
        )
    )


def _deal_has_client_commitment(candidates: list[StageCandidate]) -> bool:
    committed = {"purchase_pending", "won", "fulfillment", "post_sale"}
    return any(
        c.canonical_stage in committed and c.event_instant is not None and c.client_side
        for c in candidates
    )


def _is_closed_provenance_candidate(c: StageCandidate) -> bool:
    return (c.source_stage or "").strip().lower() in _CLOSED_PROVENANCE_STAGES


def _is_undated_terminal_claim(winner: StageCandidate) -> bool:
    """True when a terminal stage claim lacks an orderable timestamp (any source)."""
    if winner.event_instant is not None:
        return False
    src = (winner.source_stage or "").strip().lower()
    return bool(
        winner.is_terminal
        or winner.is_hard_terminal
        or src in UNDATED_TERMINAL_SOURCE_STATUSES
        or src in {"client_payment_received", "deal_cancelled", "payment_inbound"}
        or winner.canonical_stage in HARD_TERMINAL_STAGES
        or winner.canonical_stage in _TERMINAL_CLAIM_STAGES
    )


def _note_timestamp(
    *,
    conflicts: list[OpportunityConflictRecord],
    metrics: dict[str, Any],
    opportunity_id: str,
    raw: str | None,
    subject_keys: dict[str, Any],
    evidence_pointers: list[dict[str, Any]],
    stage_bearing: bool,
) -> tuple[str | None, Any]:
    """Parse timestamp; emit missing/malformed conflicts for stage-bearing rows.

    Returns (original_string_or_none, utc_instant_or_none).
    """
    parsed = parse_event_time(raw)
    if stage_bearing and not (parsed.original or "").strip():
        metrics["missing_event_timestamp_count"] += 1
        _add_conflict(
            conflicts,
            reason_code="source_event_missing_timestamp",
            opportunity_id=opportunity_id,
            subject_keys=subject_keys,
            evidence_pointers=evidence_pointers,
        )
        return None, None
    if parsed.error == MALFORMED_TIMESTAMP_REASON:
        _add_conflict(
            conflicts,
            reason_code=MALFORMED_TIMESTAMP_REASON,
            opportunity_id=opportunity_id,
            subject_keys={**subject_keys, "raw_timestamp": parsed.original},
            evidence_pointers=evidence_pointers,
        )
        return parsed.original, None
    return parsed.original, parsed.instant


def _activity_bounds(raw_times: list[str | None]) -> tuple[str | None, str | None]:
    """First/last activity using UTC instants; preserve original source strings."""
    orderable = [
        p for p in (parse_event_time(raw) for raw in raw_times) if p.is_orderable and p.instant
    ]
    if not orderable:
        return None, None
    first = min(orderable, key=lambda p: p.instant)  # type: ignore[arg-type, return-value]
    last = max(orderable, key=lambda p: p.instant)  # type: ignore[arg-type, return-value]
    return first.original, last.original


def _assert_provenance(
    opportunities: list[OpportunityRecord],
    evidence: list[OpportunityEvidenceRecord],
) -> None:
    by_id = {e.evidence_id: e for e in evidence}
    for opp in opportunities:
        eid = opp.stage_evidence_id
        if not eid:
            continue
        row = by_id.get(eid)
        if row is None:
            raise RuntimeError(
                f"provenance_invariant_violated: stage_evidence_id={eid} missing evidence row"
            )
        if row.opportunity_id != opp.opportunity_id:
            raise RuntimeError(
                f"provenance_invariant_violated: evidence {eid} opportunity mismatch"
            )


def resolve_opportunities(
    *,
    identity: IdentityResolution,
    deals: list[SourceDealRow],
    events: list[SourceDealEventRow],
    documents: list[SourceDealDocumentRow],
    payments: list[SourceDealPaymentRow],
    signals: list[SourceSignalRow] | None = None,
    contact_master: list[SourceContactMasterRow] | None = None,
    identity_fingerprint: str | None = None,
    identity_fingerprint_match_status: str = "not_checked",
    build_time_iso: str | None = None,
) -> OpportunityResolution:
    """Resolve opportunity stages deterministically. Pure — no I/O."""
    signals = signals or []
    contact_master = contact_master or []
    metrics = _empty_metrics()
    metrics["source_deals_inspected"] = len(deals)
    metrics["source_events_inspected"] = len(events)
    metrics["source_documents_inspected"] = len(documents)
    metrics["source_payments_inspected"] = len(payments)
    metrics["source_signals_inspected"] = len(signals)
    metrics["identity_fingerprint_match_status"] = identity_fingerprint_match_status
    if identity_fingerprint:
        metrics["identity_fingerprint"] = identity_fingerprint
    if build_time_iso:
        metrics["build_time_iso_metadata_only"] = build_time_iso

    by_email, by_domain = _identity_indexes(identity)

    opportunities: list[OpportunityRecord] = []
    opp_events: list[OpportunityEventRecord] = []
    opp_evidence: list[OpportunityEvidenceRecord] = []
    conflicts: list[OpportunityConflictRecord] = []

    events_by_deal: dict[str, list[SourceDealEventRow]] = defaultdict(list)
    for ev in events:
        events_by_deal[ev.deal_key].append(ev)
    docs_by_deal: dict[str, list[SourceDealDocumentRow]] = defaultdict(list)
    for d in documents:
        docs_by_deal[d.deal_key].append(d)
    pays_by_deal: dict[str, list[SourceDealPaymentRow]] = defaultdict(list)
    for p in payments:
        pays_by_deal[p.deal_key].append(p)

    # Deal-linked source pointers for signal dedupe
    deal_email_ids: set[int] = set()
    deal_attachment_ids: set[int] = set()
    for ev in events:
        if ev.source_email_id is not None:
            deal_email_ids.add(ev.source_email_id)
        if ev.source_attachment_id is not None:
            deal_attachment_ids.add(ev.source_attachment_id)
    for doc in documents:
        if doc.source_email_id is not None:
            deal_email_ids.add(doc.source_email_id)
        if doc.source_attachment_id is not None:
            deal_attachment_ids.add(doc.source_attachment_id)

    deal_key_counts = Counter(d.deal_key for d in deals)
    for dk, cnt in deal_key_counts.items():
        if cnt > 1:
            _add_conflict(
                conflicts,
                reason_code="duplicate_deal_key_evidence",
                opportunity_id=opportunity_id_for_deal(dk),
                subject_keys={"deal_key": dk},
                evidence_pointers=[{"source_table": "commercial_deal", "deal_key": dk, "count": cnt}],
            )

    seen_deal_keys: set[str] = set()
    for deal in sorted(deals, key=lambda d: (d.deal_key, d.deal_id)):
        if deal.deal_key in seen_deal_keys:
            continue
        seen_deal_keys.add(deal.deal_key)
        oid = opportunity_id_for_deal(deal.deal_key)

        account_id, contact_id, link_status = _link_identity(
            contact_email=deal.client_contact_email,
            client_domain=deal.client_domain,
            by_email=by_email,
            by_domain=by_domain,
        )
        if link_status in {IDENTITY_LINK_UNRESOLVED, IDENTITY_LINK_AMBIGUOUS}:
            reason = (
                "opportunity_identity_ambiguous"
                if link_status == IDENTITY_LINK_AMBIGUOUS
                else "opportunity_identity_unresolved"
            )
            _add_conflict(
                conflicts,
                reason_code=reason,
                opportunity_id=oid,
                subject_keys={
                    "deal_key": deal.deal_key,
                    "contact_id": contact_id,
                    "contact_email_token": _email_token(deal.client_contact_email),
                    "email_domain": _email_domain(deal.client_contact_email) or deal.client_domain,
                    "client_domain": deal.client_domain,
                },
                evidence_pointers=[
                    {
                        "source_table": "commercial_deal",
                        "source_record_id": str(deal.deal_id),
                        "deal_key": deal.deal_key,
                    }
                ],
            )

        if contact_id and deal.client_domain:
            contact = by_email.get(normalize_identity_email(deal.client_contact_email) or "")
            domain_accounts = by_domain.get(deal.client_domain) or []
            if (
                contact
                and contact.account_id
                and len(domain_accounts) == 1
                and domain_accounts[0].account_id != contact.account_id
            ):
                _add_conflict(
                    conflicts,
                    reason_code="deal_contact_account_mismatch",
                    opportunity_id=oid,
                    subject_keys={
                        "deal_key": deal.deal_key,
                        "contact_id": contact_id,
                        "contact_account_id": contact.account_id,
                        "domain_account_id": domain_accounts[0].account_id,
                    },
                    evidence_pointers=[
                        {"source_table": "commercial_deal", "source_record_id": str(deal.deal_id)}
                    ],
                )
                account_id = None
                link_status = IDENTITY_LINK_AMBIGUOUS

        candidates: list[StageCandidate] = []

        # --- deal status ---
        mapped, lifecycle_terminal, hard_terminal = map_deal_status(deal.deal_status)
        if mapped is None:
            _add_conflict(
                conflicts,
                reason_code="unsupported_source_stage",
                opportunity_id=oid,
                subject_keys={"deal_key": deal.deal_key, "deal_status": deal.deal_status},
                evidence_pointers=[
                    {"source_table": "commercial_deal", "source_record_id": str(deal.deal_id)}
                ],
            )
            mapped = "unknown"
            lifecycle_terminal = False
            hard_terminal = False
        status_ts, status_instant = _note_timestamp(
            conflicts=conflicts,
            metrics=metrics,
            opportunity_id=oid,
            raw=deal.updated_at,
            subject_keys={"deal_key": deal.deal_key, "deal_id": deal.deal_id},
            evidence_pointers=[
                {"source_table": "commercial_deal", "source_record_id": str(deal.deal_id)}
            ],
            # Deal status may be undated; undated-terminal policy handles claim vs proof.
            stage_bearing=False,
        )
        status_eid = stable_evidence_id(
            opportunity_id=oid,
            source_table="commercial_deal",
            source_record_id=str(deal.deal_id),
            evidence_type="deal_status",
        )
        candidates.append(
            StageCandidate(
                canonical_stage=mapped,
                source_stage=deal.deal_status,
                event_at=status_ts,
                event_instant=status_instant,
                confidence=deal.confidence,
                operator_confirmed=deal.confidence == "operator_confirmed",
                is_terminal=lifecycle_terminal,
                is_hard_terminal=hard_terminal,
                client_side=True,
                source_table="commercial_deal",
                source_record_id=str(deal.deal_id),
                evidence_id=status_eid,
                precedence_tier=5,
            )
        )
        _append_evidence(
            opp_evidence,
            evidence_id=status_eid,
            opportunity_id=oid,
            source_table="commercial_deal",
            source_record_id=str(deal.deal_id),
            evidence_type="deal_status",
            evidence_at=status_ts,
            confidence=deal.confidence,
            reason_code="explicit_deal_status",
        )

        # --- events ---
        for ev in events_by_deal.get(deal.deal_key, []):
            stage, ev_terminal, ev_hard, client_side = map_event_type(ev.event_type)
            event_at, event_instant = _note_timestamp(
                conflicts=conflicts,
                metrics=metrics,
                opportunity_id=oid,
                raw=ev.event_at,
                subject_keys={"deal_key": deal.deal_key, "event_id": ev.event_id},
                evidence_pointers=[
                    {
                        "source_table": "commercial_deal_event",
                        "source_record_id": str(ev.event_id),
                    }
                ],
                stage_bearing=stage is not None,
            )
            eid = None
            if stage is not None:
                conf = "operator_confirmed" if ev.operator_confirmed else ev.confidence
                eid = stable_evidence_id(
                    opportunity_id=oid,
                    source_table="commercial_deal_event",
                    source_record_id=str(ev.event_id),
                    evidence_type=ev.event_type,
                )
                candidates.append(
                    StageCandidate(
                        canonical_stage=stage,
                        source_stage=ev.event_type,
                        event_at=event_at,
                        event_instant=event_instant,
                        confidence=conf,
                        operator_confirmed=ev.operator_confirmed,
                        is_terminal=ev_terminal,
                        is_hard_terminal=ev_hard,
                        client_side=client_side,
                        source_table="commercial_deal_event",
                        source_record_id=str(ev.event_id),
                        evidence_id=eid,
                        precedence_tier=2 if ev.operator_confirmed else 3,
                    )
                )
                _append_evidence(
                    opp_evidence,
                    evidence_id=eid,
                    opportunity_id=oid,
                    source_table="commercial_deal_event",
                    source_record_id=str(ev.event_id),
                    evidence_type=ev.event_type,
                    evidence_at=event_at,
                    confidence=conf,
                    reason_code="deal_event",
                    source_email_id=ev.source_email_id,
                    source_attachment_id=ev.source_attachment_id,
                )
            event_rec_id = stable_event_id(
                opportunity_id=oid,
                source_table="commercial_deal_event",
                source_record_id=str(ev.event_id),
                canonical_event_type=stage or ev.event_type,
            )
            opp_events.append(
                OpportunityEventRecord(
                    event_id=event_rec_id,
                    opportunity_id=oid,
                    canonical_event_type=stage or "note",
                    source_event_type=ev.event_type,
                    event_at=event_at,
                    source_table="commercial_deal_event",
                    source_record_id=str(ev.event_id),
                    source_email_id=ev.source_email_id,
                    source_attachment_id=ev.source_attachment_id,
                    confidence=("operator_confirmed" if ev.operator_confirmed else ev.confidence),
                    operator_confirmed=ev.operator_confirmed,
                    detail_json=_json({"client_side": client_side}),
                )
            )

        # provisional commitment check for supplier_proforma refinement
        provisional = list(candidates)

        # --- documents ---
        for doc in docs_by_deal.get(deal.deal_key, []):
            stage, doc_terminal, doc_hard, client_side = map_document_type(doc.document_type)
            # supplier_proforma may refine fulfillment only with prior client commitment
            if doc.document_type == "supplier_proforma":
                if _deal_has_client_commitment(provisional) or any(
                    c.canonical_stage in {"purchase_pending", "won", "fulfillment", "post_sale"}
                    for c in provisional
                    if c.event_instant is not None
                ):
                    stage = "fulfillment"
                    doc_terminal = False
                    doc_hard = False
                    client_side = False
                else:
                    stage = None
            if stage is None:
                # Still record provenance pointer without stage weight
                eid = stable_evidence_id(
                    opportunity_id=oid,
                    source_table="commercial_deal_document",
                    source_record_id=str(doc.document_id),
                    evidence_type=doc.document_type,
                )
                doc_at, _doc_instant = _note_timestamp(
                    conflicts=conflicts,
                    metrics=metrics,
                    opportunity_id=oid,
                    raw=doc.issued_at,
                    subject_keys={
                        "deal_key": deal.deal_key,
                        "document_id": doc.document_id,
                    },
                    evidence_pointers=[
                        {
                            "source_table": "commercial_deal_document",
                            "source_record_id": str(doc.document_id),
                        }
                    ],
                    stage_bearing=doc.document_type
                    not in {
                        "payment_voucher",
                        "payment_confirmation",
                        "other",
                        "supplier_proforma",
                    },
                )
                _append_evidence(
                    opp_evidence,
                    evidence_id=eid,
                    opportunity_id=oid,
                    source_table="commercial_deal_document",
                    source_record_id=str(doc.document_id),
                    evidence_type=doc.document_type,
                    evidence_at=doc_at,
                    confidence=doc.confidence,
                    reason_code="deal_document_non_stage",
                    source_email_id=doc.source_email_id,
                    source_attachment_id=doc.source_attachment_id,
                )
                continue
            doc_at, doc_instant = _note_timestamp(
                conflicts=conflicts,
                metrics=metrics,
                opportunity_id=oid,
                raw=doc.issued_at,
                subject_keys={"deal_key": deal.deal_key, "document_id": doc.document_id},
                evidence_pointers=[
                    {
                        "source_table": "commercial_deal_document",
                        "source_record_id": str(doc.document_id),
                    }
                ],
                stage_bearing=True,
            )
            eid = stable_evidence_id(
                opportunity_id=oid,
                source_table="commercial_deal_document",
                source_record_id=str(doc.document_id),
                evidence_type=doc.document_type,
            )
            candidates.append(
                StageCandidate(
                    canonical_stage=stage,
                    source_stage=doc.document_type,
                    event_at=doc_at,
                    event_instant=doc_instant,
                    confidence=doc.confidence,
                    operator_confirmed=doc.confidence == "operator_confirmed",
                    is_terminal=doc_terminal,
                    is_hard_terminal=doc_hard,
                    client_side=client_side,
                    source_table="commercial_deal_document",
                    source_record_id=str(doc.document_id),
                    evidence_id=eid,
                    precedence_tier=4,
                )
            )
            _append_evidence(
                opp_evidence,
                evidence_id=eid,
                opportunity_id=oid,
                source_table="commercial_deal_document",
                source_record_id=str(doc.document_id),
                evidence_type=doc.document_type,
                evidence_at=doc_at,
                confidence=doc.confidence,
                reason_code="deal_document",
                source_email_id=doc.source_email_id,
                source_attachment_id=doc.source_attachment_id,
            )

        # --- payments (direction-aware) ---
        for pay in pays_by_deal.get(deal.deal_key, []):
            if pay.direction == "inbound":
                stage = "won"
                terminal = True
                hard = False
                client_side = True
            elif pay.direction == "outbound":
                stage = "fulfillment"
                terminal = False
                hard = False
                client_side = False
            else:
                continue
            pay_at, pay_instant = _note_timestamp(
                conflicts=conflicts,
                metrics=metrics,
                opportunity_id=oid,
                raw=pay.paid_at,
                subject_keys={"deal_key": deal.deal_key, "payment_id": pay.payment_id},
                evidence_pointers=[
                    {
                        "source_table": "commercial_deal_payment",
                        "source_record_id": str(pay.payment_id),
                    }
                ],
                stage_bearing=True,
            )
            eid = stable_evidence_id(
                opportunity_id=oid,
                source_table="commercial_deal_payment",
                source_record_id=str(pay.payment_id),
                evidence_type=f"payment_{pay.direction}",
            )
            candidates.append(
                StageCandidate(
                    canonical_stage=stage,
                    source_stage=f"payment_{pay.direction}",
                    event_at=pay_at,
                    event_instant=pay_instant,
                    confidence=pay.confidence,
                    operator_confirmed=pay.confidence == "operator_confirmed",
                    is_terminal=terminal,
                    is_hard_terminal=hard,
                    client_side=client_side,
                    source_table="commercial_deal_payment",
                    source_record_id=str(pay.payment_id),
                    evidence_id=eid,
                    precedence_tier=4,
                )
            )
            _append_evidence(
                opp_evidence,
                evidence_id=eid,
                opportunity_id=oid,
                source_table="commercial_deal_payment",
                source_record_id=str(pay.payment_id),
                evidence_type=f"payment_{pay.direction}",
                evidence_at=pay_at,
                confidence=pay.confidence,
                reason_code="deal_payment",
            )

        # Closure claims (deal_status=closed and/or deal_closed events) are
        # provenance-only — never compete in ordinary stage selection.
        closed_provenance = [
            c for c in candidates if _is_closed_provenance_candidate(c)
        ]
        competition = [
            c for c in candidates if not _is_closed_provenance_candidate(c)
        ]
        header_closed = (deal.deal_status or "").strip().lower() == "closed"
        event_closed = any(
            (c.source_stage or "").strip().lower() == "deal_closed"
            for c in closed_provenance
        )
        has_closure_claim = header_closed or event_closed
        support = [
            c
            for c in competition
            if c.canonical_stage in _CLOSED_SUPPORT_STAGES and c.event_instant is not None
        ]

        if has_closure_claim and support:
            winner, stage_conflicts = select_stage(support)
        elif header_closed and not support:
            winner = None
            stage_conflicts = []
            _add_conflict(
                conflicts,
                reason_code="closed_without_supporting_evidence",
                opportunity_id=oid,
                subject_keys={
                    "deal_key": deal.deal_key,
                    "closure_claim_from_deal_status": True,
                    "closure_claim_from_deal_closed_event": event_closed,
                },
                evidence_pointers=[
                    {
                        "source_table": c.source_table,
                        "source_record_id": c.source_record_id,
                    }
                    for c in closed_provenance
                ]
                or [
                    {
                        "source_table": "commercial_deal",
                        "source_record_id": str(deal.deal_id),
                    }
                ],
            )
        elif event_closed and not support:
            # Header has a defensible nonclosed stage: keep it, flag the unsupported
            # deal_closed claim, and never regress merely because of deal_closed.
            winner, stage_conflicts = select_stage(competition)
            _add_conflict(
                conflicts,
                reason_code="closed_without_supporting_evidence",
                opportunity_id=oid,
                subject_keys={
                    "deal_key": deal.deal_key,
                    "closure_claim_from_deal_status": False,
                    "closure_claim_from_deal_closed_event": True,
                    "retained_header_status": deal.deal_status,
                },
                evidence_pointers=[
                    {
                        "source_table": c.source_table,
                        "source_record_id": c.source_record_id,
                    }
                    for c in closed_provenance
                    if (c.source_stage or "").strip().lower() == "deal_closed"
                ],
            )
        else:
            winner, stage_conflicts = select_stage(competition)

        for reason, left, right in stage_conflicts:
            _add_conflict(
                conflicts,
                reason_code=reason,
                opportunity_id=oid,
                subject_keys={
                    "deal_key": deal.deal_key,
                    "left_stage": left.canonical_stage,
                    "right_stage": right.canonical_stage,
                    "left_at": left.event_at,
                    "right_at": right.event_at,
                },
                evidence_pointers=[
                    {
                        "source_table": left.source_table,
                        "source_record_id": left.source_record_id,
                    },
                    {
                        "source_table": right.source_table,
                        "source_record_id": right.source_record_id,
                    },
                ],
            )

        if winner is None:
            canonical = "unknown"
            source_stage = deal.deal_status
            reason_code = (
                "closed_without_supporting_evidence"
                if header_closed
                else "no_stage_evidence"
            )
            conf = "unavailable"
            is_terminal = False
            is_current = False
            evidence_at = None
            evidence_id = closed_provenance[0].evidence_id if closed_provenance else None
            if header_closed and evidence_id is None:
                evidence_id = status_eid
        else:
            canonical = winner.canonical_stage
            source_stage = winner.source_stage
            reason_code = f"selected:{winner.source_table}:{winner.source_stage}"
            conf = winner.confidence
            has_ts = winner.event_instant is not None
            evidence_at = winner.event_at
            evidence_id = winner.evidence_id

            # Undated terminal policy — every source table, not just commercial_deal.
            if _is_undated_terminal_claim(winner):
                canonical = "unknown"
                is_terminal = False
                is_current = False
                conf = "unavailable"
                reason_code = "undated_terminal_unproven"
                evidence_at = None
                # Keep evidence_id for provenance of the unproven claim
                _add_conflict(
                    conflicts,
                    reason_code="undated_terminal_unproven",
                    opportunity_id=oid,
                    subject_keys={
                        "deal_key": deal.deal_key,
                        "source_stage": winner.source_stage,
                        "claimed_stage": winner.canonical_stage,
                        "source_table": winner.source_table,
                    },
                    evidence_pointers=[
                        {
                            "source_table": winner.source_table,
                            "source_record_id": winner.source_record_id,
                        }
                    ],
                )
            elif not has_ts:
                is_current = False
                is_terminal = False
                if winner.source_table == "commercial_deal":
                    reason_code = "deal_status_without_usable_timestamp"
                    canonical = "unknown"
                    conf = "unavailable"
                    _add_conflict(
                        conflicts,
                        reason_code="deal_status_without_usable_timestamp",
                        opportunity_id=oid,
                        subject_keys={
                            "deal_key": deal.deal_key,
                            "deal_status": deal.deal_status,
                        },
                        evidence_pointers=[
                            {
                                "source_table": winner.source_table,
                                "source_record_id": winner.source_record_id,
                            }
                        ],
                    )
            else:
                is_terminal = bool(
                    winner.is_hard_terminal
                    or (winner.is_terminal and winner.canonical_stage in _TERMINAL_CLAIM_STAGES)
                )
                if is_terminal:
                    is_current = False
                elif winner.source_table in {"commercial_deal", "commercial_deal_event"}:
                    is_current = True
                else:
                    is_current = has_ts and winner.client_side

        first_at, last_at = _activity_bounds(
            [deal.created_at, deal.updated_at]
            + [e.event_at for e in events_by_deal.get(deal.deal_key, [])]
            + [d.issued_at for d in docs_by_deal.get(deal.deal_key, [])]
            + [p.paid_at for p in pays_by_deal.get(deal.deal_key, [])]
        )

        review = derive_opportunity_review_status(
            opportunity_id=oid,
            conflicts=conflicts,
            deal_status=deal.deal_status,
        )

        opportunities.append(
            OpportunityRecord(
                opportunity_id=oid,
                record_kind=RECORD_KIND_EXPLICIT,
                account_id=account_id,
                primary_contact_id=contact_id,
                source_kind="commercial_deal",
                source_key=deal.deal_key,
                commercial_deal_id=deal.deal_id,
                deal_key=deal.deal_key,
                canonical_stage=canonical,
                source_stage=source_stage,
                stage_reason_code=reason_code,
                stage_confidence=conf,
                stage_is_current=is_current,
                stage_is_terminal=is_terminal,
                stage_evidence_at=evidence_at,
                stage_evidence_id=evidence_id,
                first_activity_at=first_at,
                last_activity_at=last_at,
                identity_link_status=link_status,
                review_status=review,
            )
        )

    # Evidence candidates from signals — dedupe only via shared email/attachment pointers
    for sig in signals:
        recovered = (sig.email_date or "").strip() or None
        if not recovered:
            metrics["undated_signal_history_count"] += 1
            _add_conflict(
                conflicts,
                reason_code="undated_signal_history_only",
                opportunity_id=None,
                subject_keys={"signal_id": sig.signal_id, "entity_kind": sig.entity_kind},
                evidence_pointers=[
                    {
                        "source_table": "opportunity_signals",
                        "source_record_id": str(sig.signal_id),
                        "created_at_is_mart_stamp": True,
                    }
                ],
                detail={"note": "opportunity_signals.created_at is not business event time"},
            )
            continue

        # Defensible dedupe only
        if sig.email_id is not None and sig.email_id in deal_email_ids:
            continue
        if sig.attachment_id is not None and sig.attachment_id in deal_attachment_ids:
            continue

        oid = stable_opportunity_id(
            source_kind="opportunity_signal",
            source_key=str(sig.signal_id),
        )
        account_id, contact_id, link_status = _link_identity(
            contact_email=sig.contact_email,
            client_domain=sig.organization_key if sig.entity_kind == "organization" else None,
            by_email=by_email,
            by_domain=by_domain,
        )
        signal_type = (sig.signal_type or "commercial_signal").strip().lower()
        stage = "quote_sent" if "quote" in signal_type else "qualifying"
        eid = stable_evidence_id(
            opportunity_id=oid,
            source_table="opportunity_signals",
            source_record_id=str(sig.signal_id),
            evidence_type=signal_type or "signal",
        )
        opportunities.append(
            OpportunityRecord(
                opportunity_id=oid,
                record_kind=RECORD_KIND_EVIDENCE_CANDIDATE,
                account_id=account_id,
                primary_contact_id=contact_id,
                source_kind="opportunity_signal",
                source_key=str(sig.signal_id),
                commercial_deal_id=None,
                deal_key=None,
                canonical_stage=stage,
                source_stage=signal_type,
                stage_reason_code="dated_typed_signal",
                stage_confidence="extracted_low",
                stage_is_current=False,
                stage_is_terminal=False,
                stage_evidence_at=recovered,
                stage_evidence_id=eid,
                first_activity_at=recovered,
                last_activity_at=recovered,
                identity_link_status=link_status or IDENTITY_LINK_UNRESOLVED,
                review_status=REVIEW_STATUS_REQUIRED,
            )
        )
        _append_evidence(
            opp_evidence,
            evidence_id=eid,
            opportunity_id=oid,
            source_table="opportunity_signals",
            source_record_id=str(sig.signal_id),
            evidence_type=signal_type or "signal",
            evidence_at=recovered,
            confidence="extracted_low",
            reason_code="dated_typed_signal",
            source_email_id=sig.email_id,
            source_attachment_id=sig.attachment_id,
        )

    # Typed commercial history only (not generic email totals)
    deal_emails = {
        normalize_identity_email(d.client_contact_email)
        for d in deals
        if d.client_contact_email
    }
    deal_emails.discard(None)
    for cm in contact_master:
        if not cm.has_typed_commercial_evidence:
            continue
        if not cm.email:
            continue
        if normalize_identity_email(cm.email) in deal_emails:
            continue
        oid = stable_opportunity_id(
            source_kind="contact_master_history",
            source_key=normalize_identity_email(cm.email) or cm.email,
        )
        account_id, contact_id, link_status = _link_identity(
            contact_email=cm.email,
            client_domain=None,
            by_email=by_email,
            by_domain=by_domain,
        )
        opportunities.append(
            OpportunityRecord(
                opportunity_id=oid,
                record_kind=RECORD_KIND_HISTORY,
                account_id=account_id,
                primary_contact_id=contact_id,
                source_kind="contact_master",
                source_key=cm.email,
                commercial_deal_id=None,
                deal_key=None,
                canonical_stage="commercial_history",
                source_stage="typed_lifetime_counts",
                stage_reason_code="lifetime_typed_commercial_counts",
                stage_confidence="historical",
                stage_is_current=False,
                stage_is_terminal=False,
                stage_evidence_at=None,
                stage_evidence_id=None,
                first_activity_at=cm.first_seen_at,
                last_activity_at=cm.last_seen_at,
                identity_link_status=link_status or IDENTITY_LINK_NOT_APPLICABLE,
                review_status=REVIEW_STATUS_OK,
            )
        )

    opportunities.sort(key=lambda o: o.opportunity_id)
    opp_events.sort(key=lambda e: e.event_id)
    opp_evidence.sort(key=lambda e: e.evidence_id)
    conflicts.sort(key=lambda c: c.conflict_id)

    _assert_provenance(opportunities, opp_evidence)

    metrics["canonical_opportunity_count"] = len(opportunities)
    metrics["explicit_deal_opportunity_count"] = sum(
        1 for o in opportunities if o.record_kind == RECORD_KIND_EXPLICIT
    )
    metrics["evidence_candidate_count"] = sum(
        1 for o in opportunities if o.record_kind == RECORD_KIND_EVIDENCE_CANDIDATE
    )
    metrics["commercial_history_count"] = sum(
        1 for o in opportunities if o.record_kind == RECORD_KIND_HISTORY
    )
    metrics["current_opportunity_count"] = sum(1 for o in opportunities if o.stage_is_current)
    metrics["terminal_opportunity_count"] = sum(1 for o in opportunities if o.stage_is_terminal)
    metrics["linked_account_count"] = len({o.account_id for o in opportunities if o.account_id})
    metrics["linked_contact_count"] = len(
        {o.primary_contact_id for o in opportunities if o.primary_contact_id}
    )
    metrics["unresolved_identity_count"] = sum(
        1
        for o in opportunities
        if o.identity_link_status in {IDENTITY_LINK_UNRESOLVED, IDENTITY_LINK_AMBIGUOUS}
    )
    metrics["opportunity_conflict_count"] = len(conflicts)
    metrics["stage_distribution"] = dict(Counter(o.canonical_stage for o in opportunities))
    metrics["confidence_distribution"] = dict(Counter(o.stage_confidence for o in opportunities))
    metrics["conflict_distribution"] = dict(Counter(c.reason_code for c in conflicts))

    return OpportunityResolution(
        opportunities=opportunities,
        events=opp_events,
        evidence=opp_evidence,
        conflicts=conflicts,
        metrics=metrics,
    )
