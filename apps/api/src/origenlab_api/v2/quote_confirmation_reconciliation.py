"""Reconcile email-level quotation confirmations onto their canonical document.

The policy (owner, 2026-09-24):

1. **Document decisions are canonical** for quotation confirmation. A document is its exact
   bytes (SHA-256); `quote_document_review` holds one candidate per document.
2. **Email decisions are historical evidence.** They stay in their own append-only ledger,
   untouched: nothing here writes, deletes or rewrites an email decision.
3. An email-level confirmation is **reconciled automatically only** when the email carries
   exactly one quotation document, and that document prints one quote number and names one
   explicit client with nothing contradicting either. The number the operator typed on the
   email must also carry the printed number's serial (`CN01005` ↔ `1005-26`).
4. An email carrying several quotation documents is **refused** email-level confirmation.
5. A reconciled confirmation keeps every exact identifier of the email that was confirmed
   (email id, Gmail message id, RFC Message-ID, thread id, attachment id) and every other
   message carrying the same bytes as a duplicate occurrence.
6. **A duplicate email never makes a second quote.** Reconciliation is keyed by document,
   so two confirmed emails carrying the same bytes are one document confirmation; if they
   disagree on the opportunity or the number, neither is reconciled and a human decides the
   document.
7. An explicit document decision always wins over a reconciled one; the superseded email
   confirmations are still listed, never removed.

Everything here is computed on read from the two ledgers and the document review. It writes
nothing, creates no opportunity, quote or revision, and opens no database.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from origenlab_api.v2.quote_document_review import (
    PROPOSED_QUOTATION,
    TARGET_DOCUMENT,
    DocumentCandidate,
    DocumentDecisionLedger,
    DocumentReview,
    DocumentState,
)
from origenlab_api.v2.quote_evidence_review import (
    DECISION_CONFIRM,
    OPPORTUNITY_CREATE_NEW,
    OPPORTUNITY_EXISTING,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    STATUS_REJECTED,
    DecisionLedger,
    ItemState,
    ReviewRefused,
    Staging,
    record_decision,
)

BASIS_EMAIL_RECONCILIATION = "email_reconciliation"

SOURCE_DOCUMENT_DECISION = "document_decision"
SOURCE_EMAIL_RECONCILIATION = "email_reconciliation"

RECONCILED = "reconciled"
REFUSED_NO_QUOTE_DOCUMENT = "no_quotation_document_in_email"
REFUSED_SEVERAL_QUOTE_DOCUMENTS = "several_quotation_documents_in_email"
REFUSED_DOCUMENT_AMBIGUOUS = "document_client_or_number_not_unambiguous"
REFUSED_NUMBER_DISAGREES = "email_number_disagrees_with_printed_number"
REFUSED_DUPLICATES_DISAGREE = "duplicate_email_confirmations_disagree"

# A planned opportunity id derived from the email decision that asked for `create_new`, so
# the same ledger always reconciles to the same id and another document can join it by typing it.
_PLANNED_NAMESPACE = uuid.UUID("5b0e7c1e-4a4f-4f55-9a55-6f2e1d0c9a71")

_EMAIL_SERIAL_RE = re.compile(r"^(?:CN)?0*(?P<serial>\d{1,7})(?:[A-Z]?(?:-\d{2})?)?$")


@dataclass(frozen=True)
class EmailReconciliation:
    """What became of one email's current confirmation."""

    email_id: int
    outcome: str  # RECONCILED or one REFUSED_* reason
    document_sha256: str | None
    email_decision: dict[str, Any]
    detail: str

    @property
    def reconciled(self) -> bool:
        return self.outcome == RECONCILED


@dataclass
class EffectiveDocumentState(DocumentState):
    """A document's canonical status, and where it came from."""

    source: str | None = None
    # Email confirmations of this document — reconciled or superseded — kept as history.
    email_confirmations: list[dict[str, Any]] = field(default_factory=list)
    superseded_by_document_decision: bool = False


def _email_serial(number: str | None) -> int | None:
    m = _EMAIL_SERIAL_RE.match((number or "").strip().upper())
    return int(m.group("serial")) if m else None


def email_confirmation_refusal(
    review: DocumentReview, email_id: int, quote_number: str | None = None
) -> tuple[str, str] | None:
    """Why this email cannot be confirmed at email level, or None if it would reconcile.

    `quote_number` is the number the operator typed (or recorded) for the email."""
    cands = review.for_email(email_id)
    if not cands:
        return REFUSED_NO_QUOTE_DOCUMENT, (
            f"email {email_id} carries no quotation document: there is no document to confirm"
        )
    if len(cands) >= 2:
        return REFUSED_SEVERAL_QUOTE_DOCUMENTS, (
            f"email {email_id} carries {len(cands)} quotation documents: one email-level decision "
            "cannot choose one of them — confirm each document on its own"
        )
    c = cands[0]
    ident = c.identity
    if (
        c.proposed_status != PROPOSED_QUOTATION
        or len(ident.clients) != 1
        or len(ident.quote_numbers_found) != 1
        or ident.client is None
        or ident.quote_number is None
    ):
        return REFUSED_DOCUMENT_AMBIGUOUS, (
            f"document {c.sha256[:12]} in email {email_id} does not print one unambiguous client "
            f"and quote number ({', '.join(c.review_reasons) or 'needs review'}): decide the document"
        )
    if quote_number is not None and _email_serial(quote_number) != ident.quote_number.serial:
        return REFUSED_NUMBER_DISAGREES, (
            f"{quote_number} does not carry the serial of the printed number "
            f"{ident.quote_number.raw} on document {c.sha256[:12]}: decide the document"
        )
    return None


def reconcile_email_confirmations(
    review: DocumentReview, email_states: dict[int, ItemState]
) -> list[EmailReconciliation]:
    """Every email whose current email-level status is confirmed, and what it maps to."""
    out: list[EmailReconciliation] = []
    for email_id, st in sorted(email_states.items()):
        if st.status != STATUS_CONFIRMED or st.latest is None:
            continue
        refusal = email_confirmation_refusal(review, email_id, st.latest.get("quote_number"))
        if refusal is not None:
            out.append(EmailReconciliation(email_id, refusal[0], None, st.latest, refusal[1]))
            continue
        sha = review.for_email(email_id)[0].sha256
        out.append(EmailReconciliation(email_id, RECONCILED, sha, st.latest, f"document {sha}"))

    # A duplicate email is not a second quote: all confirmations of one document must agree,
    # or none of them is reconciled.
    by_sha: dict[str, list[EmailReconciliation]] = {}
    for r in out:
        if r.reconciled:
            by_sha.setdefault(r.document_sha256, []).append(r)  # type: ignore[arg-type]
    for sha, group in by_sha.items():
        if len({_opportunity_signature(r.email_decision) for r in group}) > 1:
            ids = ", ".join(str(r.email_id) for r in group)
            for r in group:
                out[out.index(r)] = EmailReconciliation(
                    r.email_id, REFUSED_DUPLICATES_DISAGREE, None, r.email_decision,
                    f"emails {ids} carry the same document {sha[:12]} but were confirmed against "
                    "different opportunities: decide the document",
                )
    return out


def _opportunity_signature(entry: dict[str, Any]) -> tuple[str, str | None]:
    opp = entry.get("opportunity") or {}
    return (opp.get("mode") or "", opp.get("opportunity_id"))


def _email_decision_ref(entry: dict[str, Any]) -> dict[str, Any]:
    """The email decision, as history, with its exact message identifiers."""
    return {
        "decision_id": entry.get("decision_id"),
        "recorded_at": entry.get("recorded_at"),
        "operator": entry.get("operator"),
        "email_id": entry.get("email_id"),
        "gmail_message_id": entry.get("gmail_message_id"),
        "rfc822_message_id": entry.get("rfc822_message_id"),
        "gmail_thread_id": entry.get("gmail_thread_id"),
        "quote_number": entry.get("quote_number"),
        "opportunity": entry.get("opportunity"),
    }


def reconciled_document_entry(
    candidate: DocumentCandidate, reconciliations: list[EmailReconciliation]
) -> dict[str, Any]:
    """The document confirmation an agreeing set of email confirmations stands for.

    Derived, never stored: shaped like a document ledger entry so every check that reads
    document states (a number on two opportunities, joining a planned opportunity) sees it."""
    first = min(reconciliations, key=lambda r: (r.email_decision.get("recorded_at") or "", r.email_id))
    opp = first.email_decision.get("opportunity") or {}
    if opp.get("mode") == OPPORTUNITY_EXISTING:
        opportunity = {"mode": OPPORTUNITY_EXISTING, "opportunity_id": opp.get("opportunity_id"),
                       "planned_opportunity_id": None}
    else:
        opportunity = {
            "mode": OPPORTUNITY_CREATE_NEW,
            "opportunity_id": None,
            "planned_opportunity_id": str(uuid.uuid5(_PLANNED_NAMESPACE, str(first.email_decision["decision_id"]))),
        }
    ident = candidate.identity
    assert ident.quote_number is not None  # guaranteed by email_confirmation_refusal
    return {
        "decision_id": None,
        "recorded_at": first.email_decision.get("recorded_at"),
        "operator": first.email_decision.get("operator"),
        "target": TARGET_DOCUMENT,
        "decision": DECISION_CONFIRM,
        "document_sha256": candidate.sha256,
        "filenames": list(candidate.filenames),
        "document_class": ident.document_class,
        "canonical_email_id": candidate.canonical.email_id,
        "occurrences": [o.as_dict() for o in candidate.occurrences],
        "duplicate_email_ids": [o.email_id for o in candidate.duplicate_occurrences],
        "printed_quote_numbers": list(candidate.printed_quote_numbers),
        "client_evidence_lines": candidate.client_evidence_lines(),
        "opportunity": opportunity,
        "quote_number": ident.quote_number.key,
        "quote_number_basis": BASIS_EMAIL_RECONCILIATION,
        "source_email_decisions": [_email_decision_ref(r.email_decision) for r in reconciliations],
        "reason": None,
        "reconciled": True,
        "applied": False,
    }


def effective_document_states(
    review: DocumentReview,
    document_ledger: DocumentDecisionLedger,
    email_states: dict[int, ItemState] | None,
) -> dict[str, EffectiveDocumentState]:
    """The canonical status of every document: its own decision if it has one, else the
    reconciled email confirmation, else pending."""
    explicit = document_ledger.states()
    reconciliations = reconcile_email_confirmations(review, email_states or {})
    by_sha: dict[str, list[EmailReconciliation]] = {}
    for r in reconciliations:
        if r.reconciled:
            by_sha.setdefault(r.document_sha256, []).append(r)  # type: ignore[arg-type]

    out: dict[str, EffectiveDocumentState] = {}
    for sha, st in explicit.items():
        out[sha] = EffectiveDocumentState(
            status=st.status, latest=st.latest, history=list(st.history), source=SOURCE_DOCUMENT_DECISION,
        )
    for sha, group in by_sha.items():
        refs = [_email_decision_ref(r.email_decision) for r in group]
        if sha in out:
            out[sha].email_confirmations = refs
            out[sha].superseded_by_document_decision = True
            continue
        entry = reconciled_document_entry(review.candidate(sha), group)
        out[sha] = EffectiveDocumentState(
            status=STATUS_CONFIRMED, latest=entry, history=[], source=SOURCE_EMAIL_RECONCILIATION,
            email_confirmations=refs,
        )
    return out


def record_email_decision(
    staging: Staging,
    ledger: DecisionLedger,
    review: DocumentReview | None,
    email_id: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """An email-level decision, refused when a confirmation could not reconcile to exactly one
    document. Without a document review loaded, the email rules alone apply."""
    if review is not None and kwargs.get("decision") == DECISION_CONFIRM:
        refusal = email_confirmation_refusal(review, email_id, kwargs.get("quote_number") or None)
        if refusal is not None:
            raise ReviewRefused(refusal[1])
        kwargs.setdefault("quote_document_count", review.quote_document_count(email_id))
    return record_decision(staging, ledger, email_id, **kwargs)


# --- the email queue's documentary status -------------------------------------------------
#
# The email decision and the document decision stay two separate facts. The queue shows both,
# and an email counts as pending review only when *neither* settles it: an email whose
# quotation documents are all decided (by a document decision or a reconciled email
# confirmation) is not pending just because it has no email-level decision of its own.

DOC_STATUS_NONE = "no_quotation_document"
DOC_STATUS_PENDING = "document_pending"
DOC_STATUS_PARTIAL = "documents_partially_decided"
DOC_STATUS_CONFIRMED = "confirmed_by_document"
DOC_STATUS_REJECTED = "rejected_by_document"
DOC_STATUSES = (DOC_STATUS_CONFIRMED, DOC_STATUS_REJECTED, DOC_STATUS_PARTIAL, DOC_STATUS_PENDING, DOC_STATUS_NONE)

# The email queue's own buckets: the email decision, plus "settled by its documents".
QUEUE_PENDING = STATUS_PENDING
QUEUE_SETTLED_BY_DOCUMENT = "settled_by_document"
QUEUE_BUCKETS = (QUEUE_PENDING, QUEUE_SETTLED_BY_DOCUMENT, STATUS_CONFIRMED, STATUS_REJECTED)


@dataclass(frozen=True)
class EmailDocumentStatus:
    """What the canonical document decisions say about one email's quotation documents."""

    email_id: int
    status: str  # one of DOC_STATUSES
    confirmed: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()

    @property
    def settled(self) -> bool:
        """Every quotation document in the email is decided."""
        return self.status in (DOC_STATUS_CONFIRMED, DOC_STATUS_REJECTED)


def email_document_status(
    review: DocumentReview, email_id: int, doc_states: dict[str, DocumentState]
) -> EmailDocumentStatus:
    """The documentary status of one email, from the effective document states.

    Confirmed when no document is pending and at least one is confirmed; rejected when every
    document is rejected; partial when some are decided and some are not."""
    by: dict[str, list[str]] = {STATUS_CONFIRMED: [], STATUS_REJECTED: [], STATUS_PENDING: []}
    for c in review.for_email(email_id):
        by[doc_states.get(c.sha256, DocumentState()).status].append(c.sha256)
    confirmed, rejected, pending = (tuple(by[k]) for k in (STATUS_CONFIRMED, STATUS_REJECTED, STATUS_PENDING))
    if not (confirmed or rejected or pending):
        status = DOC_STATUS_NONE
    elif pending:
        status = DOC_STATUS_PARTIAL if (confirmed or rejected) else DOC_STATUS_PENDING
    else:
        status = DOC_STATUS_CONFIRMED if confirmed else DOC_STATUS_REJECTED
    return EmailDocumentStatus(email_id, status, confirmed, rejected, pending)


def queue_bucket(email_state: ItemState, doc_status: EmailDocumentStatus | None) -> str:
    """Where an email sits in the queue. Its own decision, if any, is kept as is; without one,
    decided documents settle it instead of leaving it pending."""
    if email_state.status != STATUS_PENDING:
        return email_state.status
    if doc_status is not None and doc_status.settled:
        return QUEUE_SETTLED_BY_DOCUMENT
    return QUEUE_PENDING


def email_queue_statuses(
    staging: Staging,
    email_states: dict[int, ItemState],
    review: DocumentReview | None,
    doc_states: dict[str, DocumentState],
) -> dict[int, tuple[str, EmailDocumentStatus | None]]:
    """Per staged email: its queue bucket and its documentary status (None without a review)."""
    out: dict[int, tuple[str, EmailDocumentStatus | None]] = {}
    for it in staging.items:
        ds = email_document_status(review, it.email_id, doc_states) if review is not None else None
        out[it.email_id] = (queue_bucket(email_states.get(it.email_id, ItemState()), ds), ds)
    return out
