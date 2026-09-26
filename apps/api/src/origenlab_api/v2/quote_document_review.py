"""Operator review of quotation *documents* — one candidate per exact file, one decision each.

`quote_evidence_review` reviews staged Gmail messages. A message is not a quotation, though:
one email can carry two quotations for the same client (706528 sends 1007-26 and 1010-26 to
Eurofarma), and one quotation can travel in several emails. This module moves the quotation
decision to the document itself:

- **One candidate per exact document.** Candidates are keyed by SHA-256. Identical bytes
  sent in several emails are one candidate; the earliest message is its canonical
  occurrence and every other message is attached to it as duplicate evidence, each with its
  own exact identifiers. Filenames never join or split anything.
- **What the document says, not what its filename says.** The client evidence and printed
  quote number come from the identity audit (`quote_document_identity`), with the verbatim
  lines they were read from. A candidate is *proposed* only when it prints exactly one
  number, names an explicit client and nothing contradicts it; everything else needs a
  human, with the reason stated.
- **A decision per document.** Confirming one quotation in an email says nothing about the
  other. Two quotations may be recorded against one opportunity, and stay two quotes.
- **Opportunities are typed, never suggested.** An existing opportunity is a UUID the
  operator types. `create_new` mints a *planned* opportunity id — an intent, nothing is
  created — which another document joins only when the operator types that exact id.
- **Nothing is created.** No database, no `crm.*` or `evidence.*` row. The only write is an
  append to the document decision ledger (JSONL), whose entries carry `applied: false`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from origenlab_api.v2.quote_document_identity import (
    CONFLICTING_CLIENT_EVIDENCE,
    DIFFERENT_EXPLICIT_CLIENT,
    DOC_INSUFFICIENT,
    DOC_QUOTATION,
    INSUFFICIENT_EVIDENCE,
    DocumentIdentity,
    classify_document_pair,
    document_from_dict,
)
from origenlab_api.v2.quote_evidence_review import (
    BASIS_CONFIRMED_PROPOSAL,
    DECISION_CONFIRM,
    DECISION_REJECT,
    DECISIONS,
    OPPORTUNITY_CREATE_NEW,
    OPPORTUNITY_EXISTING,
    QUOTE_NUMBER_BASES,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    STATUS_REJECTED,
    DecisionLedger,
    ItemState,
    ReviewItem,
    ReviewRefused,
    Staging,
    StagingInconsistent,
    _chronological_key,
    normalize_quote_number,
)

DOCUMENT_LEDGER_FILE = "document_decisions.jsonl"
TARGET_DOCUMENT = "quote_document"

# Document classes that can be a quotation. Brochures, generic files and non-PDFs cannot.
CANDIDATE_CLASSES = (DOC_QUOTATION, DOC_INSUFFICIENT)

PROPOSED_QUOTATION = "proposed_quotation"
NEEDS_REVIEW = "needs_review"

# Join the opportunity another document's `create_new` decision planned. The id is typed.
OPPORTUNITY_PLANNED = "planned"
DOCUMENT_OPPORTUNITY_MODES = (OPPORTUNITY_EXISTING, OPPORTUNITY_CREATE_NEW, OPPORTUNITY_PLANNED)

# Review reasons found by comparing documents with each other (the per-document ones come
# from the identity audit unchanged).
REVIEW_NUMBER_ON_OTHER_CLIENT = "printed_number_on_other_client_document"
REVIEW_NUMBER_ON_OTHER_DOCUMENT = "printed_number_on_other_document"
REVIEW_OTHER_CLIENT_SAME_EMAIL = "other_client_in_same_email"
REVIEW_NO_PRINTED_NUMBER = "no_printed_quote_number"

VIA_SAME_EMAIL = "same_email"
VIA_PRINTED_NUMBER = "printed_quote_number"


@dataclass(frozen=True)
class DocumentOccurrence:
    """One message that carries the document, with its exact identifiers."""

    email_id: int
    queue: str
    gmail_message_id: str
    gmail_thread_id: str | None
    rfc822_message_id: str | None
    source_record_sha256: str
    source_attachment_id: int
    filename: str
    sent_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "email_id": self.email_id,
            "queue": self.queue,
            "gmail_message_id": self.gmail_message_id,
            "gmail_thread_id": self.gmail_thread_id,
            "rfc822_message_id": self.rfc822_message_id,
            "source_record_sha256": self.source_record_sha256,
            "source_attachment_id": self.source_attachment_id,
            "filename": self.filename,
            "sent_at": self.sent_at,
        }


@dataclass(frozen=True)
class DocumentRelation:
    """Another candidate document this one was compared with, and why they were compared."""

    other_sha256: str
    via: tuple[str, ...]
    classification: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class DocumentCandidate:
    sha256: str
    occurrences: tuple[DocumentOccurrence, ...]  # chronological; the first is canonical
    identity: DocumentIdentity
    proposed_quote_number: str | None
    proposed_status: str
    review_reasons: tuple[str, ...]
    related: tuple[DocumentRelation, ...]

    @property
    def canonical(self) -> DocumentOccurrence:
        return self.occurrences[0]

    @property
    def duplicate_occurrences(self) -> tuple[DocumentOccurrence, ...]:
        return self.occurrences[1:]

    @property
    def email_ids(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys(o.email_id for o in self.occurrences))

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(o.filename for o in self.occurrences))

    @property
    def printed_quote_numbers(self) -> tuple[str, ...]:
        return tuple(q.raw for q in self.identity.quote_numbers_found)

    @property
    def needs_review(self) -> bool:
        return self.proposed_status == NEEDS_REVIEW

    def client_evidence_lines(self) -> list[str]:
        lines: list[str] = []
        for c in self.identity.clients:
            lines.append(c.addressee.line)
            if c.rut:
                lines.append(c.rut.line)
        return lines


@dataclass(frozen=True)
class DocumentReview:
    candidates: tuple[DocumentCandidate, ...]
    report_generated_at: str | None

    def candidate(self, sha256: str) -> DocumentCandidate:
        for c in self.candidates:
            if c.sha256 == sha256:
                return c
        raise ReviewRefused(f"document {sha256} is not a staged quotation-document candidate")

    def for_email(self, email_id: int) -> tuple[DocumentCandidate, ...]:
        return tuple(c for c in self.candidates if email_id in c.email_ids)

    def quote_document_count(self, email_id: int) -> int:
        return len(self.for_email(email_id))


def _occurrences(item: ReviewItem, sha256: str) -> list[DocumentOccurrence]:
    return [
        DocumentOccurrence(
            email_id=item.email_id,
            queue=item.queue,
            gmail_message_id=item.gmail_message_id,
            gmail_thread_id=item.gmail_thread_id,
            rfc822_message_id=item.rfc822_message_id,
            source_record_sha256=item.source_record_sha256,
            source_attachment_id=d.source_attachment_id,
            filename=d.filename,
            sent_at=item.sent_at,
        )
        for d in item.documents
        if d.sha256 == sha256 and d.bytes_hash_verified
    ]


def build_document_review(staging: Staging, report: dict[str, Any]) -> DocumentReview:
    """One candidate per exact quotation document, from the staging and an identity report.

    Refuses a report that does not describe this staging: a document the staging holds as
    verified but the report never read, or a report document naming an unstaged email."""
    identities = {d["sha256"]: document_from_dict(d) for d in report.get("documents", [])}
    staged_ids = {it.email_id for it in staging.items}
    for ident in identities.values():
        if not set(ident.email_ids) <= staged_ids:
            raise StagingInconsistent(
                f"identity report document {ident.sha256} names emails outside the staging; "
                "regenerate the report"
            )
    items_by_sha: dict[str, list[ReviewItem]] = {}
    for it in staging.items:
        for d in it.documents:
            if not d.bytes_hash_verified:
                continue
            if d.sha256 not in identities:
                raise StagingInconsistent(
                    f"staged document {d.sha256} (email {it.email_id}) is not in the identity "
                    "report; regenerate the report"
                )
            items_by_sha.setdefault(d.sha256, [])
            if it not in items_by_sha[d.sha256]:
                items_by_sha[d.sha256].append(it)

    base: dict[str, tuple[DocumentIdentity, tuple[DocumentOccurrence, ...]]] = {}
    for sha, items in items_by_sha.items():
        ident = identities[sha]
        if ident.document_class not in CANDIDATE_CLASSES:
            continue
        items = sorted(items, key=_chronological_key)
        occ = tuple(o for it in items for o in _occurrences(it, sha))
        base[sha] = (ident, occ)

    # Which candidates to compare: those in the same email, and those printing the same
    # quote number. Never by filename, domain, subject or resemblance.
    pairs: dict[tuple[str, str], set[str]] = {}
    by_email: dict[int, set[str]] = {}
    by_number: dict[str, set[str]] = {}
    for sha, (ident, occ) in base.items():
        for o in occ:
            by_email.setdefault(o.email_id, set()).add(sha)
        for q in ident.quote_numbers_found:
            by_number.setdefault(q.key, set()).add(sha)
    for via, groups in ((VIA_SAME_EMAIL, by_email.values()), (VIA_PRINTED_NUMBER, by_number.values())):
        for group in groups:
            for a in group:
                for b in group:
                    if a != b:
                        pairs.setdefault((a, b), set()).add(via)

    candidates = []
    for sha, (ident, occ) in base.items():
        related: list[DocumentRelation] = []
        reasons: list[str] = list(ident.review_reasons)
        for (a, b), vias in sorted(pairs.items()):
            if a != sha:
                continue
            other = base[b][0]
            if ident.document_class == DOC_QUOTATION and other.document_class == DOC_QUOTATION:
                pair = classify_document_pair(ident, other)
                label, evidence = pair.classification, pair.evidence
            else:
                label = INSUFFICIENT_EVIDENCE
                evidence = tuple(q.evidence.line for d in (ident, other) for q in d.quote_numbers_found)
            related.append(DocumentRelation(b, tuple(sorted(vias)), label, evidence))
            if VIA_PRINTED_NUMBER in vias:
                if label in (CONFLICTING_CLIENT_EVIDENCE, DIFFERENT_EXPLICIT_CLIENT):
                    reasons.append(REVIEW_NUMBER_ON_OTHER_CLIENT)
                else:
                    # The same client (a resend or a revision?) or nothing to compare: a
                    # human says whether these bytes are the same quotation.
                    reasons.append(REVIEW_NUMBER_ON_OTHER_DOCUMENT)
            if VIA_SAME_EMAIL in vias and label in (CONFLICTING_CLIENT_EVIDENCE, DIFFERENT_EXPLICIT_CLIENT):
                reasons.append(REVIEW_OTHER_CLIENT_SAME_EMAIL)
        number = ident.quote_number.key if ident.quote_number else None
        if number is None and not ident.quote_numbers_found:
            reasons.append(REVIEW_NO_PRINTED_NUMBER)
        reasons = list(dict.fromkeys(reasons))
        proposed = (
            ident.document_class == DOC_QUOTATION
            and number is not None
            and ident.client is not None
            and not reasons
        )
        candidates.append(DocumentCandidate(
            sha256=sha,
            occurrences=occ,
            identity=ident,
            proposed_quote_number=number if proposed else None,
            proposed_status=PROPOSED_QUOTATION if proposed else NEEDS_REVIEW,
            review_reasons=tuple(reasons),
            related=tuple(related),
        ))
    candidates.sort(key=lambda c: (_occurrence_key(c.canonical), c.sha256))
    return DocumentReview(candidates=tuple(candidates), report_generated_at=report.get("generated_at"))


def _occurrence_key(o: DocumentOccurrence) -> tuple[int, float, int]:
    try:
        return (0, datetime.fromisoformat(o.sent_at).timestamp(), o.email_id)
    except (TypeError, ValueError):
        return (1, 0.0, o.email_id)


# --- decisions -------------------------------------------------------------------------


@dataclass
class DocumentState:
    status: str = STATUS_PENDING
    latest: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def opportunity_key(self) -> str | None:
        """The opportunity a confirmed document is recorded against: typed or planned id."""
        if self.status != STATUS_CONFIRMED or not self.latest:
            return None
        opp = self.latest["opportunity"]
        return opp.get("opportunity_id") or opp.get("planned_opportunity_id")


class DocumentDecisionLedger(DecisionLedger):
    """Append-only JSONL of document decisions; the latest entry per SHA-256 is its status.

    A separate file from the email ledger, so neither reader ever meets the other's rows."""

    def states(self) -> dict[str, DocumentState]:  # type: ignore[override]
        states: dict[str, DocumentState] = {}
        for entry in self.entries():
            if entry.get("target") != TARGET_DOCUMENT:
                raise StagingInconsistent(f"{self.path} holds a row that is not a document decision")
            st = states.setdefault(entry["document_sha256"], DocumentState())
            st.history.append(entry)
            st.latest = entry
            st.status = {
                DECISION_CONFIRM: STATUS_CONFIRMED,
                DECISION_REJECT: STATUS_REJECTED,
            }.get(entry["decision"], STATUS_PENDING)
        return states


def planned_opportunity_ids(states: dict[str, DocumentState]) -> dict[str, tuple[str, ...]]:
    """Planned opportunity id → the documents currently confirmed against it."""
    out: dict[str, list[str]] = {}
    for sha, st in states.items():
        if st.status == STATUS_CONFIRMED and st.latest:
            pid = st.latest["opportunity"].get("planned_opportunity_id")
            if pid:
                out.setdefault(pid, []).append(sha)
    return {k: tuple(sorted(v)) for k, v in sorted(out.items())}


def _typed_uuid(raw: str | None, what: str) -> str:
    try:
        return str(uuid.UUID((raw or "").strip()))
    except ValueError:
        raise ReviewRefused(f"{what} must be a UUID typed by the operator") from None


def build_document_decision(
    candidate: DocumentCandidate,
    *,
    decision: str,
    operator: str,
    opportunity_mode: str | None = None,
    opportunity_id: str | None = None,
    quote_number: str | None = None,
    quote_number_basis: str | None = None,
    reason: str | None = None,
    current: dict[str, DocumentState] | None = None,
    email_level_confirmations: Iterable[int] = (),
    report_generated_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one decision about one document and return its ledger entry. Writes nothing.

    `current` is the ledger's present state, so a quote number or planned opportunity can
    be checked against the other documents' decisions."""
    current = current or {}
    operator = (operator or "").strip()
    if not operator:
        raise ReviewRefused("an operator must be named on every decision")
    if decision not in DECISIONS:
        raise ReviewRefused(f"decision must be one of {', '.join(DECISIONS)}")
    reason = (reason or "").strip() or None

    opportunity: dict[str, Any] | None = None
    number: str | None = None
    basis: str | None = None
    same_number_as: list[str] = []

    if decision == DECISION_CONFIRM:
        if opportunity_mode not in DOCUMENT_OPPORTUNITY_MODES:
            raise ReviewRefused(
                "confirming a quotation needs an explicit opportunity: an existing "
                "opportunity_id, create_new, or a planned opportunity id typed by the operator"
            )
        if opportunity_mode == OPPORTUNITY_EXISTING:
            opportunity = {"mode": OPPORTUNITY_EXISTING,
                           "opportunity_id": _typed_uuid(opportunity_id, "opportunity_id"),
                           "planned_opportunity_id": None}
        elif opportunity_mode == OPPORTUNITY_CREATE_NEW:
            if (opportunity_id or "").strip():
                raise ReviewRefused("create_new must not carry an opportunity_id")
            opportunity = {"mode": OPPORTUNITY_CREATE_NEW, "opportunity_id": None,
                           "planned_opportunity_id": str(uuid.uuid4())}
        else:
            pid = _typed_uuid(opportunity_id, "the planned opportunity id")
            holders = [s for s in planned_opportunity_ids(current).get(pid, ()) if s != candidate.sha256]
            if not holders:
                raise ReviewRefused(
                    f"no other confirmed document plans opportunity {pid}; "
                    "use create_new for the first document of a new opportunity"
                )
            opportunity = {"mode": OPPORTUNITY_PLANNED, "opportunity_id": None, "planned_opportunity_id": pid}

        if quote_number_basis not in QUOTE_NUMBER_BASES:
            raise ReviewRefused("quote_number_basis must be confirmed_proposal or operator_entered")
        number = normalize_quote_number(quote_number or "")
        if quote_number_basis == BASIS_CONFIRMED_PROPOSAL:
            if candidate.proposed_quote_number is None:
                raise ReviewRefused(
                    f"document {candidate.sha256[:12]} has no proposed number "
                    f"({', '.join(candidate.review_reasons) or 'needs review'}): type the "
                    "number (operator_entered) and give a reason"
                )
            if number != candidate.proposed_quote_number:
                raise ReviewRefused(
                    f"{number} is not the printed proposal {candidate.proposed_quote_number}; "
                    "record an edited number as operator_entered"
                )
        elif candidate.needs_review and not reason:
            raise ReviewRefused(
                f"document {candidate.sha256[:12]} needs review "
                f"({', '.join(candidate.review_reasons)}): say why this number is the right one"
            )
        basis = quote_number_basis

        # One quote number held by two different documents.
        key = opportunity["opportunity_id"] or opportunity["planned_opportunity_id"]
        for sha, st in sorted(current.items()):
            if sha == candidate.sha256 or st.status != STATUS_CONFIRMED or not st.latest:
                continue
            if st.latest["quote_number"] != number:
                continue
            if st.opportunity_key != key:
                raise ReviewRefused(
                    f"{number} is already confirmed for document {sha[:12]} under another "
                    "opportunity: one quote number cannot belong to two opportunities"
                )
            same_number_as.append(sha)
        if same_number_as and not reason:
            raise ReviewRefused(
                f"{number} is already confirmed for document(s) "
                f"{', '.join(s[:12] for s in same_number_as)}: say whether this is a resend or a "
                "revision of the same quotation"
            )
    else:
        if opportunity_mode or (opportunity_id or "").strip():
            raise ReviewRefused(f"{decision} does not take an opportunity")
        if (quote_number or "").strip() or quote_number_basis:
            raise ReviewRefused(f"{decision} does not take a quote number")
        if decision == DECISION_REJECT and not reason:
            raise ReviewRefused("a rejection needs a reason, so it stays auditable")

    ident = candidate.identity
    return {
        "decision_id": str(uuid.uuid4()),
        "recorded_at": (now or datetime.now(UTC)).isoformat(),
        "operator": operator,
        "target": TARGET_DOCUMENT,
        "decision": decision,
        # The document, by exact bytes, and every message that carries it.
        "document_sha256": candidate.sha256,
        "filenames": list(candidate.filenames),
        "document_class": ident.document_class,
        "canonical_email_id": candidate.canonical.email_id,
        "occurrences": [o.as_dict() for o in candidate.occurrences],
        # What the document said when the decision was taken.
        "identity_report_generated_at": report_generated_at,
        "printed_quote_numbers": list(candidate.printed_quote_numbers),
        "proposed_quote_number": candidate.proposed_quote_number,
        "proposed_status": candidate.proposed_status,
        "review_reasons_at_decision": list(candidate.review_reasons),
        "client_evidence_lines": candidate.client_evidence_lines(),
        "email_level_confirmations_at_decision": sorted(set(email_level_confirmations)),
        # The operator's decision.
        "opportunity": opportunity,
        "quote_number": number,
        "quote_number_basis": basis,
        "same_quote_number_as": same_number_as,
        "reason": reason,
        "applied": False,
    }


def record_document_decision(
    review: DocumentReview,
    ledger: DocumentDecisionLedger,
    sha256: str,
    *,
    email_states: dict[int, ItemState] | None = None,
    current: dict[str, DocumentState] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Validate against the candidate and the ledger, then append. The only write here.

    `current` defaults to this ledger's states; pass the effective states
    (`quote_confirmation_reconciliation`) so reconciled email confirmations count too."""
    candidate = review.candidate(sha256)
    confirmed_emails = [
        e for e in candidate.email_ids
        if (email_states or {}).get(e, ItemState()).status == STATUS_CONFIRMED
    ]
    entry = build_document_decision(
        candidate,
        current=ledger.states() if current is None else current,
        email_level_confirmations=confirmed_emails,
        report_generated_at=review.report_generated_at,
        **kwargs,
    )
    ledger.append(entry)
    return entry


@dataclass(frozen=True)
class PlannedQuote:
    quote_number: str
    document_sha256: str
    canonical_email_id: int
    filenames: tuple[str, ...]


@dataclass(frozen=True)
class OpportunityPlan:
    """The quotations currently confirmed against one opportunity — each its own quote."""

    opportunity_key: str
    mode: str  # existing | planned
    quotes: tuple[PlannedQuote, ...]

    @property
    def quote_numbers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(q.quote_number for q in self.quotes))


def opportunity_plans(
    review: DocumentReview,
    ledger: DocumentDecisionLedger,
    states: dict[str, DocumentState] | None = None,
) -> list[OpportunityPlan]:
    """Group current confirmations by the opportunity the operator named. A view only."""
    states = ledger.states() if states is None else states
    by_key: dict[str, list[PlannedQuote]] = {}
    modes: dict[str, str] = {}
    known = {c.sha256: c for c in review.candidates}
    for sha, st in states.items():
        key = st.opportunity_key
        if key is None or st.latest is None:
            continue
        opp = st.latest["opportunity"]
        modes[key] = OPPORTUNITY_EXISTING if opp.get("opportunity_id") else OPPORTUNITY_PLANNED
        c = known.get(sha)
        by_key.setdefault(key, []).append(PlannedQuote(
            quote_number=st.latest["quote_number"],
            document_sha256=sha,
            canonical_email_id=st.latest["canonical_email_id"],
            filenames=c.filenames if c else tuple(st.latest.get("filenames") or ()),
        ))
    return [
        OpportunityPlan(k, modes[k], tuple(sorted(v, key=lambda q: (q.quote_number, q.document_sha256))))
        for k, v in sorted(by_key.items())
    ]


def document_review_summary(
    review: DocumentReview,
    ledger: DocumentDecisionLedger,
    states: dict[str, DocumentState] | None = None,
) -> dict[str, Any]:
    states = ledger.states() if states is None else states
    by_status = {STATUS_PENDING: 0, STATUS_CONFIRMED: 0, STATUS_REJECTED: 0}
    for c in review.candidates:
        by_status[states.get(c.sha256, DocumentState()).status] += 1
    per_email: dict[int, int] = {}
    for c in review.candidates:
        for e in c.email_ids:
            per_email[e] = per_email.get(e, 0) + 1
    return {
        "document_candidates": len(review.candidates),
        "proposed": sum(1 for c in review.candidates if c.proposed_status == PROPOSED_QUOTATION),
        "needs_review": sum(1 for c in review.candidates if c.needs_review),
        "with_duplicate_emails": sum(1 for c in review.candidates if c.duplicate_occurrences),
        "emails_with_several_quote_documents": sum(1 for n in per_email.values() if n >= 2),
        "by_status": by_status,
        "decisions_recorded": sum(len(s.history) for s in states.values()),
    }

