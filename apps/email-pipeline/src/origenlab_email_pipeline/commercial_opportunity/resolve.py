"""Pure deterministic opportunity-stage resolver (PR3).

Does not write SQLite. Does not reimplement PR2 identity matching — consumes
an IdentityResolution. Does not invent next-action, tender, or product-interest.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from origenlab_email_pipeline.commercial_identity.models import IdentityResolution
from origenlab_email_pipeline.commercial_identity.normalize import normalize_identity_email
from origenlab_email_pipeline.commercial_opportunity.constants import (
    BUILD_CONTRACT,
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
from origenlab_email_pipeline.commercial_opportunity.stage_map import (
    map_deal_status,
    map_document_type,
    map_event_type,
    select_stage,
)


def _json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


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
            "unresolved_identity_count": "Opportunities with identity_link_status in unresolved/ambiguous",
            "opportunity_conflict_count": "Conflict rows emitted",
            "missing_event_timestamp_count": "Deal events with empty event_at",
            "undated_signal_history_count": "Signals treated as history because event time unrecovered",
            "identity_fingerprint_match_status": "not_checked|matched|mismatched|missing",
            "opportunity_stage_fields_inferred": "Always true for PR3 stage fields",
            "next_action_fields_inferred": "Always false — PR3 does not generate next actions",
            "tender_fields_inferred": "Always false — tender is a separate dimension",
            "product_interest_fields_inferred": "Always false — product interest is out of scope",
        },
    }


def _identity_indexes(
    identity: IdentityResolution,
) -> tuple[dict[str, Any], dict[str, list[Any]], dict[str, list[Any]]]:
    """email → contact; domain → accounts; normalized_name → accounts."""
    by_email: dict[str, Any] = {}
    for c in identity.contacts:
        by_email[c.normalized_email] = c
    by_domain: dict[str, list[Any]] = defaultdict(list)
    by_name: dict[str, list[Any]] = defaultdict(list)
    for a in identity.accounts:
        if a.primary_domain:
            by_domain[a.primary_domain].append(a)
        for dom in a.domains:
            by_domain[dom].append(a)
        by_name[a.normalized_name].append(a)
    # Deduplicate account lists by account_id
    for d, lst in list(by_domain.items()):
        seen: set[str] = set()
        uniq = []
        for a in lst:
            if a.account_id not in seen:
                seen.add(a.account_id)
                uniq.append(a)
        by_domain[d] = uniq
    for n, lst in list(by_name.items()):
        seen = set()
        uniq = []
        for a in lst:
            if a.account_id not in seen:
                seen.add(a.account_id)
                uniq.append(a)
        by_name[n] = uniq
    return by_email, by_domain, by_name


def _link_identity(
    *,
    contact_email: str | None,
    client_domain: str | None,
    by_email: dict[str, Any],
    by_domain: dict[str, list[Any]],
) -> tuple[str | None, str | None, str, list[OpportunityConflictRecord], str | None]:
    """Return (account_id, contact_id, link_status, conflicts, opportunity_id_placeholder)."""
    conflicts: list[OpportunityConflictRecord] = []
    email = normalize_identity_email(contact_email) if contact_email else None
    contact = by_email.get(email) if email else None
    if contact is not None:
        if contact.identity_status == "internal_actor":
            return None, contact.contact_id, IDENTITY_LINK_WITHHELD, conflicts, None
        if contact.account_id:
            return contact.account_id, contact.contact_id, IDENTITY_LINK_LINKED, conflicts, None
        # Contact known but unlinked (consumer / withheld)
        if contact.identity_status in {"consumer_email", "unlinked", "withheld"}:
            return None, contact.contact_id, IDENTITY_LINK_WITHHELD, conflicts, None
        return None, contact.contact_id, IDENTITY_LINK_UNRESOLVED, conflicts, None

    domain = (client_domain or "").strip().lower() or (email.split("@", 1)[1] if email and "@" in email else "")
    if domain:
        accounts = by_domain.get(domain) or []
        if len(accounts) == 1:
            # Institutional domain fallback — contact may still be null
            return accounts[0].account_id, None, IDENTITY_LINK_LINKED, conflicts, None
        if len(accounts) > 1:
            return None, None, IDENTITY_LINK_AMBIGUOUS, conflicts, None
    return None, None, IDENTITY_LINK_UNRESOLVED, conflicts, None


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
    # build_time must never become stage evidence — retained only in metrics if provided.
    if build_time_iso:
        metrics["build_time_iso_metadata_only"] = build_time_iso

    by_email, by_domain, _by_name = _identity_indexes(identity)

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

    # Duplicate deal_key detection (should be unique; still guard).
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

    # Explicit deals → one opportunity each (stable by deal_key).
    seen_deal_keys: set[str] = set()
    for deal in sorted(deals, key=lambda d: (d.deal_key, d.deal_id)):
        if deal.deal_key in seen_deal_keys:
            continue
        seen_deal_keys.add(deal.deal_key)
        oid = opportunity_id_for_deal(deal.deal_key)

        account_id, contact_id, link_status, _, _ = _link_identity(
            contact_email=deal.client_contact_email,
            client_domain=deal.client_domain,
            by_email=by_email,
            by_domain=by_domain,
        )
        review = REVIEW_STATUS_OK
        if link_status in {IDENTITY_LINK_UNRESOLVED, IDENTITY_LINK_AMBIGUOUS}:
            review = REVIEW_STATUS_REQUIRED
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
                    "client_contact_email": deal.client_contact_email,
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
        # Contact/account mismatch: contact linked to different account than domain fallback
        if contact_id and deal.client_domain:
            contact = by_email.get(normalize_identity_email(deal.client_contact_email) or "")
            domain_accounts = by_domain.get(deal.client_domain) or []
            if (
                contact
                and contact.account_id
                and len(domain_accounts) == 1
                and domain_accounts[0].account_id != contact.account_id
            ):
                review = REVIEW_STATUS_REQUIRED
                _add_conflict(
                    conflicts,
                    reason_code="deal_contact_account_mismatch",
                    opportunity_id=oid,
                    subject_keys={
                        "deal_key": deal.deal_key,
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
        # Status candidate
        mapped, terminal = map_deal_status(deal.deal_status)
        status_ts = (deal.updated_at or "").strip() or None
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
            terminal = False
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
                confidence=deal.confidence,
                operator_confirmed=deal.confidence == "operator_confirmed",
                is_terminal=terminal,
                client_side=True,
                source_table="commercial_deal",
                source_record_id=str(deal.deal_id),
                evidence_id=status_eid,
                precedence_tier=1 if (terminal and status_ts) else 5,
            )
        )
        opp_evidence.append(
            OpportunityEvidenceRecord(
                evidence_id=status_eid,
                opportunity_id=oid,
                subject_kind="opportunity",
                source_table="commercial_deal",
                source_record_id=str(deal.deal_id),
                evidence_type="deal_status",
                evidence_at=status_ts,
                confidence=deal.confidence,
                reason_code="explicit_deal_status",
            )
        )

        # Events
        for ev in events_by_deal.get(deal.deal_key, []):
            stage, ev_terminal, client_side = map_event_type(ev.event_type)
            if not (ev.event_at or "").strip():
                metrics["missing_event_timestamp_count"] += 1
                _add_conflict(
                    conflicts,
                    reason_code="source_event_missing_timestamp",
                    opportunity_id=oid,
                    subject_keys={"deal_key": deal.deal_key, "event_id": ev.event_id},
                    evidence_pointers=[
                        {
                            "source_table": "commercial_deal_event",
                            "source_record_id": str(ev.event_id),
                        }
                    ],
                )
            if stage is None:
                # Still record the event row for timeline without stage weight.
                pass
            else:
                conf = "operator_confirmed" if ev.operator_confirmed else ev.confidence
                tier = 1 if (ev_terminal and (ev.event_at or "").strip()) else (
                    2 if ev.operator_confirmed else 3
                )
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
                        event_at=(ev.event_at or None),
                        confidence=conf,
                        operator_confirmed=ev.operator_confirmed,
                        is_terminal=ev_terminal,
                        client_side=client_side,
                        source_table="commercial_deal_event",
                        source_record_id=str(ev.event_id),
                        evidence_id=eid,
                        precedence_tier=tier,
                    )
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
                    event_at=(ev.event_at or None),
                    source_table="commercial_deal_event",
                    source_record_id=str(ev.event_id),
                    source_email_id=ev.source_email_id,
                    source_attachment_id=ev.source_attachment_id,
                    confidence=("operator_confirmed" if ev.operator_confirmed else ev.confidence),
                    operator_confirmed=ev.operator_confirmed,
                    detail_json=_json({"client_side": client_side}),
                )
            )

        # Documents
        for doc in docs_by_deal.get(deal.deal_key, []):
            stage, doc_terminal, client_side = map_document_type(doc.document_type)
            if stage is None:
                continue
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
                    event_at=(doc.issued_at or None),
                    confidence=doc.confidence,
                    operator_confirmed=doc.confidence == "operator_confirmed",
                    is_terminal=doc_terminal,
                    client_side=client_side,
                    source_table="commercial_deal_document",
                    source_record_id=str(doc.document_id),
                    evidence_id=eid,
                    precedence_tier=4,
                )
            )
            opp_evidence.append(
                OpportunityEvidenceRecord(
                    evidence_id=eid,
                    opportunity_id=oid,
                    subject_kind="opportunity",
                    source_table="commercial_deal_document",
                    source_record_id=str(doc.document_id),
                    evidence_type=doc.document_type,
                    evidence_at=(doc.issued_at or None),
                    confidence=doc.confidence,
                    reason_code="deal_document",
                    source_email_id=doc.source_email_id,
                    source_attachment_id=doc.source_attachment_id,
                )
            )

        # Payments
        for pay in pays_by_deal.get(deal.deal_key, []):
            stage = "won" if pay.direction == "inbound" else "fulfillment"
            terminal = pay.direction == "inbound"
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
                    event_at=(pay.paid_at or None),
                    confidence=pay.confidence,
                    operator_confirmed=pay.confidence == "operator_confirmed",
                    is_terminal=terminal,
                    client_side=pay.direction == "inbound",
                    source_table="commercial_deal_payment",
                    source_record_id=str(pay.payment_id),
                    evidence_id=eid,
                    precedence_tier=4,
                )
            )

        winner, stage_conflicts = select_stage(candidates)
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
            reason_code = "no_stage_evidence"
            conf = "unavailable"
            is_terminal = False
            is_current = False
            evidence_at = None
            evidence_id = None
        else:
            canonical = winner.canonical_stage
            source_stage = winner.source_stage
            reason_code = f"selected:{winner.source_table}:{winner.source_stage}"
            conf = winner.confidence
            is_terminal = winner.is_terminal and bool(winner.event_at)
            # Currentness: dated nonterminal explicit deal status OR dated terminal;
            # undated never current.
            has_ts = bool((winner.event_at or "").strip())
            if not has_ts:
                is_current = False
                if winner.source_table == "commercial_deal":
                    reason_code = "deal_status_without_usable_timestamp"
                    canonical = "unknown" if not winner.is_terminal else canonical
                    # Undated terminal status: stage known but not current.
                    if winner.is_terminal:
                        canonical = winner.canonical_stage
                    else:
                        canonical = "unknown"
                    conf = "unavailable"
            elif is_terminal:
                is_current = False  # terminal is not "current open"
            elif winner.source_table == "commercial_deal" and has_ts:
                is_current = True
            elif winner.source_table == "commercial_deal_event" and has_ts:
                is_current = True
            else:
                # Docs/payments refine stage; current only when tied to explicit deal.
                is_current = has_ts and winner.client_side
            evidence_at = winner.event_at
            evidence_id = winner.evidence_id

        activity_times = [
            t
            for t in [deal.created_at, deal.updated_at]
            + [e.event_at for e in events_by_deal.get(deal.deal_key, [])]
            + [d.issued_at for d in docs_by_deal.get(deal.deal_key, [])]
            + [p.paid_at for p in pays_by_deal.get(deal.deal_key, [])]
            if (t or "").strip()
        ]
        # Never use build_time_iso here.
        first_at = min(activity_times) if activity_times else None
        last_at = max(activity_times) if activity_times else None

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

    # Supplier-only events without a client deal: emit conflict, no opportunity.
    # (Events are always joined to deals in this model; standalone supplier signals
    # would come from non-deal sources — covered via history/candidate paths.)

    # Evidence candidates: dated typed signals with recovered email_date.
    deal_emails = {
        normalize_identity_email(d.client_contact_email)
        for d in deals
        if d.client_contact_email
    }
    deal_emails.discard(None)
    for sig in signals:
        recovered = (sig.email_date or "").strip() or None
        # created_at is never business event time.
        if recovered:
            # Typed dated non-deal evidence → evidence_candidate, not automatically current.
            email = normalize_identity_email(sig.contact_email) if sig.contact_email else None
            if email and email in deal_emails:
                continue  # already covered by explicit deal for this contact
            oid = stable_opportunity_id(
                source_kind="opportunity_signal",
                source_key=str(sig.signal_id),
            )
            account_id, contact_id, link_status, _, _ = _link_identity(
                contact_email=sig.contact_email,
                client_domain=None,
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
            opp_evidence.append(
                OpportunityEvidenceRecord(
                    evidence_id=eid,
                    opportunity_id=oid,
                    subject_kind="opportunity",
                    source_table="opportunity_signals",
                    source_record_id=str(sig.signal_id),
                    evidence_type=signal_type or "signal",
                    evidence_at=recovered,
                    confidence="extracted_low",
                    reason_code="dated_typed_signal",
                    source_email_id=sig.email_id,
                )
            )
        else:
            # Undated / mart-stamp-only → history conflict, not current opportunity.
            metrics["undated_signal_history_count"] += 1
            _add_conflict(
                conflicts,
                reason_code="undated_signal_history_only",
                opportunity_id=None,
                subject_keys={"signal_id": sig.signal_id},
                evidence_pointers=[
                    {
                        "source_table": "opportunity_signals",
                        "source_record_id": str(sig.signal_id),
                        "created_at_is_mart_stamp": True,
                    }
                ],
                detail={"note": "opportunity_signals.created_at is not business event time"},
            )

    # Lifetime counts → commercial_history only.
    for cm in contact_master:
        total = cm.quote_email_count + cm.invoice_email_count + cm.purchase_email_count
        if total <= 0 and (cm.gmail_sent_count + cm.gmail_received_count) <= 0:
            continue
        if not cm.email:
            continue
        # Skip if already have explicit deal for this email
        if normalize_identity_email(cm.email) in deal_emails:
            continue
        oid = stable_opportunity_id(source_kind="contact_master_history", source_key=cm.email)
        account_id, contact_id, link_status, _, _ = _link_identity(
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
                source_stage="lifetime_counts",
                stage_reason_code="lifetime_cumulative_counts",
                stage_confidence="historical",
                stage_is_current=False,
                stage_is_terminal=False,
                stage_evidence_at=None,
                stage_evidence_id=None,
                first_activity_at=None,
                last_activity_at=None,
                identity_link_status=link_status or IDENTITY_LINK_NOT_APPLICABLE,
                review_status=REVIEW_STATUS_OK,
            )
        )

    # Sort for determinism
    opportunities.sort(key=lambda o: o.opportunity_id)
    opp_events.sort(key=lambda e: e.event_id)
    opp_evidence.sort(key=lambda e: e.evidence_id)
    conflicts.sort(key=lambda c: c.conflict_id)

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
