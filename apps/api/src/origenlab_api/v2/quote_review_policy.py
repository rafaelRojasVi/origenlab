"""The quotation-evidence review policy, codified from the owner's audited decisions.

This module is a specification and a dry run. It decides nothing and writes nothing. It says,
for every staged quotation document and every staged Gmail candidate, which outcome the
owner's decisions support and why, so that a human only sees the cases that need judgment.

**Where the rules come from.** Every rule is taken from a decision the owner recorded by hand
in `decisions.jsonl` or `document_decisions.jsonl`. `POLICY` names the case each rule rests
on. There is no rule without a precedent, and the policy makes no new inference. A client,
an opportunity or a quote number is never inferred from a name, a domain, a subject, a
filename or a resemblance. The only links used are exact: the same bytes (SHA-256), the same
Gmail thread id, the same printed quote serial, and the same normalized client key or RUT
printed in the document's own client block (`quote_document_identity`).

**Only high confidence may be proposed for automation.** A document is *high confidence*
only when nothing in the rules below sends it to a human. Everything else is left to human
review, with the rules that fired and the reason stated. Conflicts, generic clients,
documents without a client, suppliers, several quotations and version changes are all left
to human review, whatever the other evidence says.

**An owner decision is never overridden.** The policy is computed without looking at the
ledgers. They are read afterwards, only to compare (`compare_with_ledgers`).

The policy imports no database driver, calls no Gmail or Drive API, starts no process and
opens no file. The dry-run script reads the inputs and writes the report.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Any

from origenlab_api.v2.quote_document_identity import (
    CONFLICT,
    DIFFERENT,
    DOC_QUOTATION,
    REVIEW_CLIENT_CONFLICT,
    REVIEW_FILENAME_NUMBER_MISMATCH,
    REVIEW_NO_CLIENT_ANCHOR,
    REVIEW_NO_TEXT,
    REVIEW_QUOTE_NUMBER_CONFLICT,
    ClientAnchor,
    DocumentIdentity,
    compare_clients,
)
from origenlab_api.v2.quote_document_review import DocumentCandidate, DocumentReview, DocumentState
from origenlab_api.v2.quote_evidence_review import (
    DECISION_CONFIRM,
    DECISION_PENDING,
    OPPORTUNITY_CREATE_NEW,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    ItemState,
    ReviewItem,
    Staging,
)

# --- vocabulary ------------------------------------------------------------------------

CONFIDENCE_HIGH = "high"      # may be proposed for automation
CONFIDENCE_MEDIUM = "medium"  # human review: the evidence is clean but a grouping must be decided
CONFIDENCE_LOW = "low"        # human review: the evidence itself is missing or contradicts
CONFIDENCES = (CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW)

# Proposed classifications, document level.
CLS_CUSTOMER_QUOTATION = "customer_quotation"
CLS_SUPPLIER_DOCUMENT = "supplier_document"
CLS_GENERIC_CLIENT = "quotation_generic_client"
CLS_NO_CLIENT = "quotation_without_client"
CLS_NUMBER_UNRESOLVED = "quotation_number_unresolved"
CLS_CLIENT_CONFLICT = "client_conflict"
CLS_VERSION_CHANGE = "quotation_version"
CLS_SEVERAL = "one_of_several_quotations"
CLS_NEEDS_REVIEW = "customer_quotation_needs_review"
# Email level only.
CLS_NO_QUOTATION = "no_quotation_document"
CLS_SUPPLIER_CORRESPONDENCE = "supplier_correspondence"
CLS_DUPLICATE_OCCURRENCE = "duplicate_occurrence"
CLS_SEVERAL_IN_EMAIL = "several_quotations"
CLS_UNREADABLE_CN_FILE = "cn_file_not_readable_as_quotation"

# Review reasons.
R_FOREIGN_ISSUER = "issuer_is_not_origenlab"
R_ALTERNATE_ISSUER = "labdelivery_issuer_not_recognised"
R_ISSUER_UNREAD = "issuer_header_not_read"
R_SUPPLIER_DIRECTION = "supplier_direction"
R_NO_CLIENT = "no_client_anchor"
R_GENERIC_CLIENT = "generic_client_address_only"
R_NO_NUMBER = "no_single_printed_quote_number"
R_FILENAME_MISMATCH = "filename_number_mismatch"
R_CLIENT_CONFLICT = "client_conflict"
R_VERSION_CHANGE = "same_serial_on_other_document"
R_SEVERAL_IN_EMAIL = "several_quotations_in_email"
R_SEVERAL_IN_THREAD = "several_quotations_in_thread"
R_SAME_CLIENT_OTHER_THREAD = "same_client_in_other_thread"
R_DIRECTION_UNCLEAR = "direction_unclear"
R_CN_FILE_NOT_QUOTATION = "cn_named_file_not_readable_as_quotation"
R_SERIAL_IN_UNREAD_FILE = "serial_named_by_unreadable_cn_file"

# Reasons whose evidence is missing or contradicts itself: low confidence.
_LOW_REASONS = frozenset({
    R_FOREIGN_ISSUER, R_ISSUER_UNREAD, R_SUPPLIER_DIRECTION, R_NO_CLIENT, R_GENERIC_CLIENT,
    R_NO_NUMBER, R_FILENAME_MISMATCH, R_CLIENT_CONFLICT, R_CN_FILE_NOT_QUOTATION,
})

# Issuers, read from the document's own header (the lines above its quotation line).
ISSUER_ORIGENLAB = "origenlab"
ISSUER_LABDELIVERY = "labdelivery"
ISSUER_FOREIGN = "foreign"
ISSUER_UNKNOWN = "unknown"

DIRECTION_CUSTOMER = "customer_quote_candidate"
DIRECTION_SUPPLIER = "supplier_rfq"

OPPORTUNITY_HUMAN = "human_decision"
OPPORTUNITY_NONE = "none"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    outcome: str
    precedent: str


# The specification. Each rule names the audited decision that established it.
POLICY: tuple[Rule, ...] = (
    Rule("P01", "One document per exact SHA-256",
         "Identical bytes are one document. The earliest message is canonical. Every other "
         "message is duplicate evidence and never a new quote or opportunity.",
         "Bioeq 706492/706499 (email ledger): a later resend of CN01005 in the same thread. "
         "InstaFarm 706523/706524/706526 (1006-26): three sends of the same PDF after bounces."),
    Rule("P02", "The printed number is the quote number",
         "The number is the one the PDF prints. A filename token never supplies it. A document "
         "printing no number, several numbers, or a serial its filename contradicts goes to a "
         "human.",
         "MMoll 012092A-26: 'CN12092 era sólo una propuesta derivada del nombre del archivo'. "
         "Bioeq: CN01005 (filename) reconciled to the printed 1005-26."),
    Rule("P03", "Only OrigenLab's own quotations",
         "The issuer header above the quotation line must be OrigenLab's. Labdelivery is "
         "recognised as OrigenLab's commercial identity only when all of these hold: every "
         "message carrying the PDF is sent from an OrigenLab sender address and staged as an "
         "outbound customer quotation; the PDF is a customer quotation with an explicit client "
         "block that does not name OrigenLab or Labdelivery; and no staged message in its "
         "threads is supplier correspondence (manufacturer, supplier or distributor). A PDF "
         "from another issuer, one sent in a supplier-direction message, a Labdelivery PDF "
         "that fails any condition, or a header that cannot be read goes to a human.",
         "Owner decision 2026-09-24: Labdelivery is OrigenLab's commercial identity toward "
         "clients. Certlab 1338/2026 is addressed to OrigenLab staff (a supplier quotation). "
         "Ceaf 011728A-25 has a Labdelivery header and was confirmed as a customer quotation."),
    Rule("P04", "A client comes from the document's client block",
         "The client is the At./Cliente/Señores block the PDF prints, with its RUT. A document "
         "without one goes to a human, even when the email thread names the client.",
         "Universidad de Concepción 01042-26: 'El PDF no incluye destinatario, pero el hilo de "
         "Gmail confirma al cliente': confirmed by a human from the thread."),
    Rule("P05", "A bare email address is not a client",
         "A client block that is only an email address (no person, no organization) is a "
         "generic client and goes to a human.",
         "ONGO 710556 (the generic-address case): 'dirección genérica hola@ongo.cl … no hay nombre "
         "de cliente, organización … Mantener pendiente'."),
    Rule("P06", "Client conflicts stay with a human",
         "One printed serial on documents naming different explicit clients, or a document "
         "whose own client lines disagree, is a conflict. The policy picks no side.",
         "Universidad del Alba / PUC 011415A: the earlier PUC version was rejected, the "
         "corrected U. del Alba one confirmed. Adventista 01065-26 and Virbac 01046-26 share "
         "their printed numbers with other clients' documents."),
    Rule("P07", "A version change stays with a human",
         "Two different documents printing the same serial (whatever the letter suffix or "
         "year) are versions or resends of one quotation. Which one is canonical, and whether "
         "it is the same quote, is a human decision.",
         "Ceaf 011728A-25/-26 ×3: 'versión actualizada … No corresponde a una oportunidad "
         "nueva'. Universidad Autónoma 012395-26 ×2. MMoll 012092A-26 ×2. A staged file whose "
         "name carries the serial (CN…) but whose text cannot be read as a quotation (715527, "
         "CN01200/CN01201) is an unresolved collision too."),
    Rule("P08", "Several quotations are grouped by a human",
         "A document that shares its email or its Gmail thread with another quotation "
         "document goes to a human, who decides whether they are one opportunity or several.",
         "Eurofarma 1007-26 + 1010-26 in one email → one opportunity. Golden Omega 01112-26 + "
         "01118-26 in one thread → one opportunity."),
    Rule("P09", "The same client in another thread is not automatically the same opportunity",
         "If the document's exact client key (RUT, organization or addressee) is printed on "
         "a quotation in another thread, a human decides whether this is a new opportunity.",
         "Golden Omega 01150-26: same company and RUT as 01112-26/01118-26, but 'una solicitud "
         "distinta, con otro producto, otro contacto y otra fecha. Registrar como oportunidad "
         "separada'."),
    Rule("P10", "Direction must be an outbound customer quotation",
         "Every message carrying the document must be staged as a customer quotation. An "
         "unclear direction goes to a human.",
         "Staging queue Q3 (direction unclear). The owner decided no Q3 document without "
         "reading the thread."),
    Rule("P11", "An email with no quotation document creates nothing",
         "An email that carries only brochures, forms or other non-quotation files proposes "
         "no quote and no opportunity. A file named like a quotation (CN…) that cannot be "
         "read as one, or supplier correspondence, goes to a human.",
         "706525 (no attachment) was never a candidate. ALS Patagonia's catalogue emails "
         "710433/710450 sit beside the one quotation 01062-26."),
    Rule("P12", "Owner decisions are final",
         "The policy never overrides a recorded decision. It is computed without the ledgers "
         "and compared with them afterwards. Document decisions are canonical. Email decisions "
         "count only through reconciliation.",
         "Owner reconciliation policy 2026-09-24 (quote_confirmation_reconciliation)."),
    Rule("P13", "High confidence: the only case proposed for automation",
         "An OrigenLab quotation printing exactly one number that agrees with its filename, "
         "for one explicit, non-generic client with no conflict, the only quotation document "
         "in its emails and thread, sharing its serial with no other document and its client "
         "with no other thread, sent as an outbound customer quotation. Proposal: "
         "customer_quotation, the printed number, and a new opportunity for this document "
         "alone.",
         "Bioeq 1005-26, ALS Patagonia 01062-26, Synthon 01022-26: each confirmed by the "
         "owner as a new opportunity with the printed number."),
)
RULES = {r.id: r for r in POLICY}

# --- freeze ----------------------------------------------------------------------------

# P01–P13 as accepted by the owner on 2026-09-24, with the owner's P03 amendment (Labdelivery).
# The fingerprint covers every rule's id, title, outcome and precedent. Changing any of them
# fails `assert_policy_frozen` until the owner accepts a new version and both constants move.
POLICY_VERSION = "P01-P13/2026-09-24.2"
FROZEN_POLICY_SHA256 = "088f0fbb399df3ddb14100d815b5138458f55c4cad2c8022ae9b67b451c11a91"


class PolicyChanged(RuntimeError):
    """The rules differ from the frozen, owner-accepted version."""


def policy_fingerprint(policy: Iterable[Rule] = POLICY) -> str:
    canonical = json.dumps(
        [[r.id, r.title, r.outcome, r.precedent] for r in policy],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_policy_frozen(policy: Iterable[Rule] = POLICY) -> str:
    actual = policy_fingerprint(policy)
    if actual != FROZEN_POLICY_SHA256:
        raise PolicyChanged(
            f"policy {POLICY_VERSION} is frozen at {FROZEN_POLICY_SHA256}; the rules now hash to {actual}"
        )
    return actual

# Which rule each review reason comes from.
REASON_RULE = {
    R_FOREIGN_ISSUER: "P03", R_ALTERNATE_ISSUER: "P03", R_ISSUER_UNREAD: "P03", R_SUPPLIER_DIRECTION: "P03",
    R_NO_CLIENT: "P04", R_GENERIC_CLIENT: "P05", R_NO_NUMBER: "P02", R_FILENAME_MISMATCH: "P02",
    R_CLIENT_CONFLICT: "P06", R_VERSION_CHANGE: "P07", R_SEVERAL_IN_EMAIL: "P08",
    R_SEVERAL_IN_THREAD: "P08", R_SAME_CLIENT_OTHER_THREAD: "P09", R_DIRECTION_UNCLEAR: "P10",
    R_CN_FILE_NOT_QUOTATION: "P11", R_SERIAL_IN_UNREAD_FILE: "P07",
}

# Primary classification when several reasons fire: the first that applies.
_CLASS_PRIORITY: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({R_FOREIGN_ISSUER, R_SUPPLIER_DIRECTION}), CLS_SUPPLIER_DOCUMENT),
    (frozenset({R_CLIENT_CONFLICT}), CLS_CLIENT_CONFLICT),
    (frozenset({R_NO_CLIENT}), CLS_NO_CLIENT),
    (frozenset({R_GENERIC_CLIENT}), CLS_GENERIC_CLIENT),
    (frozenset({R_NO_NUMBER, R_FILENAME_MISMATCH}), CLS_NUMBER_UNRESOLVED),
    (frozenset({R_VERSION_CHANGE, R_SERIAL_IN_UNREAD_FILE}), CLS_VERSION_CHANGE),
    (frozenset({R_SEVERAL_IN_EMAIL, R_SEVERAL_IN_THREAD}), CLS_SEVERAL),
)

# --- issuer ----------------------------------------------------------------------------

_QUOTATION_LINE_RE = re.compile(r"(?i)\bCOTIZACI[OÓ]N\b")
_ORIGENLAB_RE = re.compile(r"(?i)\bOrigenLab\b")
_LABDELIVERY_RE = re.compile(r"(?i)\bLABDELIVERY\b")
_HEADER_MAX_LINES = 15
_OWN_IDENTITY_RE = re.compile(r"(?i)\b(OrigenLab|LABDELIVERY)\b")
_EMAIL_ONLY_RE = re.compile(r"^\S+@\S+\.\S+$")
_CN_SERIAL_RE = re.compile(r"(?i)\bCN\s*0*(?P<serial>\d{1,7})")


def document_issuer(text: str | None) -> str:
    """The issuer the document's header names: the lines above its first quotation line.

    Read from the header only, so a client block or an item description can never change it."""
    if not text or not text.strip():
        return ISSUER_UNKNOWN
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines) if _QUOTATION_LINE_RE.search(line)), None)
    header = "\n".join(lines[: _HEADER_MAX_LINES if end is None else min(end, _HEADER_MAX_LINES)])
    if _ORIGENLAB_RE.search(header):
        return ISSUER_ORIGENLAB
    if _LABDELIVERY_RE.search(header):
        return ISSUER_LABDELIVERY
    return ISSUER_FOREIGN if header.strip() else ISSUER_UNKNOWN


# --- helpers ---------------------------------------------------------------------------


def is_generic_client(anchor: ClientAnchor) -> bool:
    """A client block that is nothing but an email address: no person, no organization."""
    return bool(_EMAIL_ONLY_RE.match(anchor.addressee.value.strip())) and not anchor.organization


def client_keys(ident: DocumentIdentity) -> set[str]:
    """The exact keys a document's explicit client block prints. Generic addresses give none."""
    keys: set[str] = set()
    for c in ident.clients:
        if is_generic_client(c):
            continue
        if c.rut_key:
            keys.add(f"rut:{c.rut_key}")
        keys.add(f"name:{c.organization_key or c.addressee_key}")
    return keys


def serials(ident: DocumentIdentity) -> set[int]:
    return {q.serial for q in ident.quote_numbers_found}


_NUMBER_RE = re.compile(r"^(?:CN)?0*(?P<serial>\d{1,7})(?P<letter>[A-Z]?)(?:\s*[-/]\s*(?P<year>\d{2,4}))?$")


def quote_number_parts(raw: str | None) -> tuple[int, str, int | None] | None:
    """(serial, letter, two-digit year) of a printed or typed number, for comparison only."""
    m = _NUMBER_RE.match((raw or "").strip().upper())
    if not m:
        return None
    year = int(m.group("year")) % 100 if m.group("year") else None
    return int(m.group("serial")), m.group("letter"), year


def same_quote_number(a: str | None, b: str | None) -> bool:
    pa, pb = quote_number_parts(a), quote_number_parts(b)
    if pa is None or pb is None:
        return False
    if pa[:2] != pb[:2]:
        return False
    return pa[2] is None or pb[2] is None or pa[2] == pb[2]


def _confidence(reasons: Iterable[str]) -> str:
    reasons = set(reasons)
    if not reasons:
        return CONFIDENCE_HIGH
    return CONFIDENCE_LOW if reasons & _LOW_REASONS else CONFIDENCE_MEDIUM


def _primary_class(reasons: Iterable[str]) -> str:
    reasons = set(reasons)
    if not reasons:
        return CLS_CUSTOMER_QUOTATION
    for trigger, cls in _CLASS_PRIORITY:
        if reasons & trigger:
            return cls
    return CLS_NEEDS_REVIEW


def _rules_for(reasons: Iterable[str]) -> tuple[str, ...]:
    reasons = list(reasons)
    if not reasons:
        return ("P13",)
    return tuple(sorted({REASON_RULE[r] for r in reasons}))


# --- document verdicts -----------------------------------------------------------------


@dataclass(frozen=True)
class DocumentVerdict:
    sha256: str
    canonical_email_id: int
    email_ids: tuple[int, ...]
    thread_ids: tuple[str, ...]
    issuer: str
    printed_quote_numbers: tuple[str, ...]
    client_lines: tuple[str, ...]
    classification: str
    confidence: str
    proposed_quote_number: str | None
    proposed_opportunity: str
    rules: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    related_documents: tuple[str, ...]

    @property
    def automatable(self) -> bool:
        return self.confidence == CONFIDENCE_HIGH

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256, "canonical_email_id": self.canonical_email_id,
            "email_ids": list(self.email_ids), "thread_ids": list(self.thread_ids),
            "issuer": self.issuer, "printed_quote_numbers": list(self.printed_quote_numbers),
            "client_lines": list(self.client_lines), "classification": self.classification,
            "confidence": self.confidence, "automatable": self.automatable,
            "proposed_quote_number": self.proposed_quote_number,
            "proposed_opportunity": self.proposed_opportunity, "rules": list(self.rules),
            "reasons": list(self.reasons), "evidence": list(self.evidence),
            "related_documents": list(self.related_documents),
        }


def evaluate_documents(
    staging: Staging,
    review: DocumentReview,
    issuers: Mapping[str, str],
    own_senders: Iterable[str] = (),
) -> dict[str, DocumentVerdict]:
    """The policy verdict for every quotation-document candidate. Reads no ledger.

    `issuers` maps a SHA-256 to `document_issuer(text)`; a document missing from it is
    ISSUER_UNKNOWN and goes to a human. `own_senders` are OrigenLab's exact sender
    addresses; with none given, a Labdelivery header is never recognised (P03)."""
    items = {it.email_id: it for it in staging.items}
    own = frozenset(a.strip().lower() for a in own_senders if a.strip())
    thread_directions: dict[str, set[str]] = {}
    for it in staging.items:
        if it.gmail_thread_id:
            thread_directions.setdefault(it.gmail_thread_id, set()).add(it.direction_hint)
    cands = {c.sha256: c for c in review.candidates}

    by_thread: dict[str, set[str]] = {}
    by_serial: dict[int, set[str]] = {}
    by_client: dict[str, set[str]] = {}
    threads_of: dict[str, set[str]] = {}
    # Serials named only by the filename of a staged file that is not a quotation candidate
    # (its text could not be read as one). Never used as a number, only as a collision.
    by_file_serial: dict[int, set[str]] = {}
    for it in staging.items:
        for d in it.documents:
            if d.sha256 in cands:
                continue
            for m in _CN_SERIAL_RE.finditer(d.filename):
                by_file_serial.setdefault(int(m.group("serial")), set()).add(f"{it.email_id}:{d.filename}")
    for sha, c in cands.items():
        threads_of[sha] = {o.gmail_thread_id for o in c.occurrences if o.gmail_thread_id}
        for t in threads_of[sha]:
            by_thread.setdefault(t, set()).add(sha)
        for s in serials(c.identity):
            by_serial.setdefault(s, set()).add(sha)
        for k in client_keys(c.identity):
            by_client.setdefault(k, set()).add(sha)

    out: dict[str, DocumentVerdict] = {}
    for sha, c in cands.items():
        out[sha] = _evaluate_one(c, cands, items, issuers, by_thread, by_serial, by_client, threads_of,
                                 by_file_serial, own, thread_directions)
    return out


def _evaluate_one(
    c: DocumentCandidate,
    cands: dict[str, DocumentCandidate],
    items: dict[int, ReviewItem],
    issuers: Mapping[str, str],
    by_thread: dict[str, set[str]],
    by_serial: dict[int, set[str]],
    by_client: dict[str, set[str]],
    threads_of: dict[str, set[str]],
    by_file_serial: dict[int, set[str]],
    own_senders: frozenset[str] = frozenset(),
    thread_directions: Mapping[str, set[str]] | None = None,
) -> DocumentVerdict:
    ident = c.identity
    reasons: list[str] = []
    evidence: list[str] = []
    related: set[str] = set()

    def add(reason: str, why: str) -> None:
        if reason not in reasons:
            reasons.append(reason)
        evidence.append(why)

    # P03 — issuer and direction.
    issuer = issuers.get(c.sha256, ISSUER_UNKNOWN)
    if issuer == ISSUER_FOREIGN:
        add(R_FOREIGN_ISSUER, "the header above the quotation line does not name OrigenLab")
    elif issuer == ISSUER_LABDELIVERY:
        failed = _labdelivery_refusals(c, items, own_senders, thread_directions or {}, threads_of[c.sha256])
        if failed:
            add(R_ALTERNATE_ISSUER, "Labdelivery header not recognised: " + "; ".join(failed))
        else:
            evidence.append("Labdelivery header recognised as OrigenLab's commercial identity (P03)")
    elif issuer == ISSUER_UNKNOWN:
        add(R_ISSUER_UNREAD, "no document text to read the issuer header from")
    directions = {items[e].direction_hint for e in c.email_ids if e in items}
    if DIRECTION_SUPPLIER in directions:
        add(R_SUPPLIER_DIRECTION, "a message carrying it is staged as supplier correspondence")
    elif directions - {DIRECTION_CUSTOMER}:  # P10
        add(R_DIRECTION_UNCLEAR, f"message direction: {', '.join(sorted(directions))}")

    # P04 / P05 — the client block.
    if REVIEW_NO_TEXT in ident.review_reasons or REVIEW_NO_CLIENT_ANCHOR in ident.review_reasons \
            or ident.document_class != DOC_QUOTATION or not ident.clients:
        add(R_NO_CLIENT, "the document prints no client block (At./Cliente/Señores)")
    elif any(is_generic_client(a) for a in ident.clients):
        generic = [a.addressee.line for a in ident.clients if is_generic_client(a)]
        add(R_GENERIC_CLIENT, f"client block is only an email address: {generic[0]}")

    # P02 — the printed number.
    if REVIEW_QUOTE_NUMBER_CONFLICT in ident.review_reasons or not ident.quote_numbers_found:
        found = ", ".join(q.raw for q in ident.quote_numbers_found) or "none"
        add(R_NO_NUMBER, f"printed quote numbers: {found}")
    if REVIEW_FILENAME_NUMBER_MISMATCH in ident.review_reasons:
        add(R_FILENAME_MISMATCH, f"filename serial(s) {', '.join(map(str, ident.filename_serials))} "
                                 f"vs printed {', '.join(q.raw for q in ident.quote_numbers_found)}")

    # P06 — a document whose own client lines disagree.
    if REVIEW_CLIENT_CONFLICT in ident.review_reasons:
        add(R_CLIENT_CONFLICT, "the document's own client lines name different clients")

    # P06 / P07 — the same serial on another document.
    for s in sorted(serials(ident)):
        for other in sorted(by_serial.get(s, set()) - {c.sha256}):
            related.add(other)
            verdict = _client_verdict(ident, cands[other].identity)
            label = ", ".join(q.raw for q in cands[other].identity.quote_numbers_found)
            if verdict in (DIFFERENT, CONFLICT):
                add(R_CLIENT_CONFLICT, f"serial {s} is also printed ({label}) on {other[:12]} for another client")
            else:
                add(R_VERSION_CHANGE, f"serial {s} is also printed ({label}) on {other[:12]}")
        for named in sorted(by_file_serial.get(s, set())):
            add(R_SERIAL_IN_UNREAD_FILE, f"serial {s} is named by an unreadable file: email {named}")

    # P08 — several quotations in one email or one thread.
    for e in c.email_ids:
        others = [x.sha256 for x in cands.values() if e in x.email_ids and x.sha256 != c.sha256]
        if others:
            related.update(others)
            add(R_SEVERAL_IN_EMAIL, f"email {e} carries {len(others) + 1} quotation documents")
    for t in sorted(threads_of[c.sha256]):
        others = sorted(by_thread.get(t, set()) - {c.sha256})
        if others:
            related.update(others)
            add(R_SEVERAL_IN_THREAD, f"thread {t} holds {len(others) + 1} quotation documents")

    # P09 — the same exact client key in another thread.
    for k in sorted(client_keys(ident)):
        for other in sorted(by_client.get(k, set()) - {c.sha256}):
            if threads_of[other] & threads_of[c.sha256]:
                continue
            related.add(other)
            add(R_SAME_CLIENT_OTHER_THREAD, f"client key {k} is printed on {other[:12]} in another thread")

    reasons_t = tuple(reasons)
    confidence = _confidence(reasons_t)
    number = ident.quote_number.raw if ident.quote_number is not None and R_NO_NUMBER not in reasons else None
    return DocumentVerdict(
        sha256=c.sha256,
        canonical_email_id=c.canonical.email_id,
        email_ids=c.email_ids,
        thread_ids=tuple(sorted(threads_of[c.sha256])),
        issuer=issuer,
        printed_quote_numbers=c.printed_quote_numbers,
        client_lines=tuple(c.client_evidence_lines()),
        classification=_primary_class(reasons_t),
        confidence=confidence,
        proposed_quote_number=number if confidence == CONFIDENCE_HIGH else None,
        proposed_opportunity=OPPORTUNITY_CREATE_NEW if confidence == CONFIDENCE_HIGH else OPPORTUNITY_HUMAN,
        rules=_rules_for(reasons_t),
        reasons=reasons_t,
        evidence=tuple(dict.fromkeys(evidence)),
        related_documents=tuple(sorted(related)),
    )


def sender_address(sender: str) -> str:
    """The exact address of a From header, lower-cased; never a name or a domain."""
    return parseaddr(sender or "")[1].strip().lower()


def _labdelivery_refusals(
    c: DocumentCandidate,
    items: Mapping[int, ReviewItem],
    own_senders: frozenset[str],
    thread_directions: Mapping[str, set[str]],
    threads: set[str],
) -> list[str]:
    """Why a Labdelivery-headed PDF is not OrigenLab's customer quotation; empty when it is."""
    failed: list[str] = []
    for e in c.email_ids:
        it = items.get(e)
        if it is None:
            failed.append(f"email {e} is not staged")
            continue
        if sender_address(it.sender) not in own_senders:
            failed.append(f"email {e} is not sent from an OrigenLab address")
        if it.direction_hint != DIRECTION_CUSTOMER:
            failed.append(f"email {e} is not staged as an outbound customer quotation ({it.direction_hint})")
    ident = c.identity
    if ident.document_class != DOC_QUOTATION or not ident.clients:
        failed.append("the PDF is not a customer quotation with a client block")
    elif any(_OWN_IDENTITY_RE.search(a.addressee.value) for a in ident.clients):
        failed.append("the client block names OrigenLab or Labdelivery (addressed to us)")
    for t in sorted(threads):
        if DIRECTION_SUPPLIER in thread_directions.get(t, set()):
            failed.append(f"thread {t} holds supplier correspondence")
    return list(dict.fromkeys(failed))


def _client_verdict(a: DocumentIdentity, b: DocumentIdentity) -> str | None:
    ca, cb = a.client, b.client
    if ca is None or cb is None:
        return None
    return compare_clients(ca, cb)[0]


# --- email verdicts --------------------------------------------------------------------


@dataclass(frozen=True)
class EmailVerdict:
    email_id: int
    queue: str
    gmail_thread_id: str | None
    direction_hint: str
    documents: tuple[str, ...]  # quotation-document candidates it carries
    canonical_for: tuple[str, ...]  # of those, the ones whose canonical occurrence it is
    classification: str
    confidence: str
    proposed_quote_numbers: tuple[str, ...]
    proposed_opportunity: str
    rules: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def automatable(self) -> bool:
        return self.confidence == CONFIDENCE_HIGH

    def as_dict(self) -> dict[str, Any]:
        return {
            "email_id": self.email_id, "queue": self.queue, "gmail_thread_id": self.gmail_thread_id,
            "direction_hint": self.direction_hint, "documents": list(self.documents),
            "canonical_for": list(self.canonical_for), "classification": self.classification,
            "confidence": self.confidence, "automatable": self.automatable,
            "proposed_quote_numbers": list(self.proposed_quote_numbers),
            "proposed_opportunity": self.proposed_opportunity, "rules": list(self.rules),
            "reasons": list(self.reasons),
        }


def _lowest(confidences: Iterable[str]) -> str:
    cs = set(confidences)
    for level in (CONFIDENCE_LOW, CONFIDENCE_MEDIUM):
        if level in cs:
            return level
    return CONFIDENCE_HIGH


def evaluate_emails(
    staging: Staging,
    review: DocumentReview,
    documents: Mapping[str, DocumentVerdict],
) -> list[EmailVerdict]:
    """One verdict per staged Gmail candidate, built from its documents' verdicts."""
    out: list[EmailVerdict] = []
    for it in sorted(staging.items, key=lambda i: i.email_id):
        docs = [documents[c.sha256] for c in review.for_email(it.email_id)]
        base = dict(email_id=it.email_id, queue=it.queue, gmail_thread_id=it.gmail_thread_id,
                    direction_hint=it.direction_hint)
        if not docs:
            cn_files = [d.filename for d in it.documents if _CN_SERIAL_RE.search(d.filename)]
            if cn_files:
                reasons: tuple[str, ...] = (R_CN_FILE_NOT_QUOTATION,)
                cls = CLS_UNREADABLE_CN_FILE
            elif it.direction_hint == DIRECTION_SUPPLIER:
                reasons, cls = (R_SUPPLIER_DIRECTION,), CLS_SUPPLIER_CORRESPONDENCE
            else:
                reasons, cls = (), CLS_NO_QUOTATION
            out.append(EmailVerdict(
                **base, documents=(), canonical_for=(), classification=cls,
                confidence=_confidence(reasons), proposed_quote_numbers=(),
                proposed_opportunity=OPPORTUNITY_NONE if not reasons else OPPORTUNITY_HUMAN,
                rules=("P11",) if not reasons else _rules_for(reasons), reasons=reasons,
            ))
            continue
        canonical_for = tuple(d.sha256 for d in docs if d.canonical_email_id == it.email_id)
        reasons_all = tuple(dict.fromkeys(r for d in docs for r in d.reasons))
        confidence = _lowest(d.confidence for d in docs)
        if len(docs) >= 2:
            cls = CLS_SEVERAL_IN_EMAIL
        elif not canonical_for:
            cls = CLS_DUPLICATE_OCCURRENCE
        else:
            cls = docs[0].classification
        rules = tuple(sorted({r for d in docs for r in d.rules} | ({"P01"} if not canonical_for else set())))
        out.append(EmailVerdict(
            **base,
            documents=tuple(d.sha256 for d in docs),
            canonical_for=canonical_for,
            classification=cls,
            confidence=confidence,
            proposed_quote_numbers=tuple(d.proposed_quote_number for d in docs if d.proposed_quote_number),
            proposed_opportunity=(
                OPPORTUNITY_CREATE_NEW if confidence == CONFIDENCE_HIGH and canonical_for
                else f"attach to document {docs[0].sha256[:12]}" if confidence == CONFIDENCE_HIGH
                else OPPORTUNITY_HUMAN
            ),
            rules=rules,
            reasons=reasons_all,
        ))
    return out


# --- comparison with the owner's ledgers -----------------------------------------------

AGREE_AUTOMATABLE = "agree_automatable"
POLICY_STRICTER = "policy_sends_to_human"
DISAGREE_NUMBER = "disagree_quote_number"
DISAGREE_OPPORTUNITY = "disagree_opportunity"
DISAGREE_STATUS = "disagree_status"
AGREE_NO_QUOTATION = "agree_no_quotation"
OUTCOMES = (AGREE_AUTOMATABLE, AGREE_NO_QUOTATION, POLICY_STRICTER,
            DISAGREE_NUMBER, DISAGREE_OPPORTUNITY, DISAGREE_STATUS)


@dataclass(frozen=True)
class LedgerComparison:
    target: str  # document | email
    key: str
    human_decision: str
    human_quote_number: str | None
    human_opportunity_mode: str | None
    human_source: str
    policy_classification: str
    policy_confidence: str
    policy_quote_number: str | None
    policy_rules: tuple[str, ...]
    policy_reasons: tuple[str, ...]
    outcome: str
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target, "key": self.key, "human_decision": self.human_decision,
            "human_quote_number": self.human_quote_number,
            "human_opportunity_mode": self.human_opportunity_mode, "human_source": self.human_source,
            "policy_classification": self.policy_classification,
            "policy_confidence": self.policy_confidence, "policy_quote_number": self.policy_quote_number,
            "policy_rules": list(self.policy_rules), "policy_reasons": list(self.policy_reasons),
            "outcome": self.outcome, "note": self.note,
        }


def _outcome(
    confidence: str,
    policy_number: str | None,
    decision: str,
    human_number: str | None,
    human_mode: str | None,
    *,
    no_quotation: bool = False,
) -> tuple[str, str]:
    if confidence != CONFIDENCE_HIGH:
        return POLICY_STRICTER, "the policy leaves this to a human; the owner's decision stands"
    if no_quotation:
        if decision == DECISION_CONFIRM:
            return DISAGREE_STATUS, "the policy saw no quotation; the owner confirmed one"
        return AGREE_NO_QUOTATION, "no quotation document, and the owner confirmed none"
    if decision != DECISION_CONFIRM:
        return DISAGREE_STATUS, f"the policy proposed a quotation; the owner recorded {decision}"
    if not same_quote_number(policy_number, human_number):
        return DISAGREE_NUMBER, f"policy {policy_number} vs owner {human_number}"
    if human_mode != OPPORTUNITY_CREATE_NEW:
        return DISAGREE_OPPORTUNITY, f"the owner joined an opportunity ({human_mode}); the policy proposed a new one"
    return AGREE_AUTOMATABLE, "same classification, number and opportunity mode"


def compare_with_ledgers(
    documents: Mapping[str, DocumentVerdict],
    emails: Iterable[EmailVerdict],
    document_states: Mapping[str, DocumentState],
    email_states: Mapping[int, ItemState],
) -> list[LedgerComparison]:
    """Every owner decision next to the policy's verdict on the same target. Reads only.

    `document_states` should be the *effective* states (explicit document decisions plus
    reconciled email confirmations), so a reconciled confirmation is compared too."""
    rows: list[LedgerComparison] = []
    for sha, st in sorted(document_states.items()):
        if st.latest is None:
            continue
        v = documents.get(sha)
        entry = st.latest
        decision = entry.get("decision", DECISION_CONFIRM if st.status == STATUS_CONFIRMED else DECISION_PENDING)
        source = getattr(st, "source", None) or "document_decision"
        mode = (entry.get("opportunity") or {}).get("mode")
        if v is None:
            rows.append(LedgerComparison("document", sha, decision, entry.get("quote_number"), mode, source,
                                         "not_a_candidate", CONFIDENCE_LOW, None, (), (), DISAGREE_STATUS,
                                         "the decided document is not a current candidate"))
            continue
        outcome, note = _outcome(v.confidence, v.proposed_quote_number, decision,
                                 entry.get("quote_number"), mode)
        rows.append(LedgerComparison(
            "document", sha, decision, entry.get("quote_number"), mode, source, v.classification,
            v.confidence, v.proposed_quote_number, v.rules, v.reasons, outcome, note,
        ))
    by_email = {e.email_id: e for e in emails}
    for email_id, st in sorted(email_states.items()):
        if st.latest is None:
            continue
        v = by_email.get(email_id)
        entry = st.latest
        mode = (entry.get("opportunity") or {}).get("mode")
        if v is None:
            rows.append(LedgerComparison("email", str(email_id), entry["decision"], entry.get("quote_number"),
                                         mode, "email_decision", "not_staged", CONFIDENCE_LOW, None, (), (),
                                         DISAGREE_STATUS, "the decided email is not staged"))
            continue
        number = v.proposed_quote_numbers[0] if len(v.proposed_quote_numbers) == 1 else None
        outcome, note = _outcome(v.confidence, number, entry["decision"], entry.get("quote_number"), mode,
                                 no_quotation=v.classification == CLS_NO_QUOTATION)
        if v.classification == CLS_DUPLICATE_OCCURRENCE and entry["decision"] != DECISION_CONFIRM \
                and v.confidence == CONFIDENCE_HIGH:
            outcome, note = AGREE_AUTOMATABLE, "a duplicate occurrence: no second quote, as the owner said"
        rows.append(LedgerComparison(
            "email", str(email_id), entry["decision"], entry.get("quote_number"), mode, "email_decision",
            v.classification, v.confidence, number, v.rules, v.reasons, outcome, note,
        ))
    return rows


# --- what still needs a human ----------------------------------------------------------


@dataclass(frozen=True)
class HumanCase:
    target: str
    key: str
    email_ids: tuple[int, ...]
    classification: str
    confidence: str
    rules: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]


def human_intervention(
    documents: Mapping[str, DocumentVerdict],
    emails: Iterable[EmailVerdict],
    document_states: Mapping[str, DocumentState],
    email_states: Mapping[int, ItemState],
) -> list[HumanCase]:
    """Every document the policy leaves to a human and the owner has not yet decided, then
    every email left to a human that carries no quotation document (those have no document
    decision to settle them)."""
    decided = {sha for sha, st in document_states.items() if st.status in (STATUS_CONFIRMED, STATUS_REJECTED)}
    out: list[HumanCase] = []
    for sha, v in sorted(documents.items(), key=lambda kv: (kv[1].canonical_email_id, kv[0])):
        if v.automatable or sha in decided:
            continue
        out.append(HumanCase("document", sha, v.email_ids, v.classification, v.confidence,
                             v.rules, v.reasons, v.evidence))
    for e in emails:
        if e.documents or e.automatable:
            continue
        if email_states.get(e.email_id, ItemState()).status in (STATUS_CONFIRMED, STATUS_REJECTED):
            continue
        out.append(HumanCase("email", str(e.email_id), (e.email_id,), e.classification, e.confidence,
                             e.rules, e.reasons, ()))
    return out


@dataclass
class DryRun:
    documents: dict[str, DocumentVerdict]
    emails: list[EmailVerdict]
    comparison: list[LedgerComparison] = field(default_factory=list)
    human: list[HumanCase] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def count(values: Iterable[str]) -> dict[str, int]:
            out: dict[str, int] = {}
            for v in values:
                out[v] = out.get(v, 0) + 1
            return dict(sorted(out.items()))

        return {
            "documents": len(self.documents),
            "documents_by_confidence": count(v.confidence for v in self.documents.values()),
            "documents_by_classification": count(v.classification for v in self.documents.values()),
            "emails": len(self.emails),
            "emails_by_confidence": count(e.confidence for e in self.emails),
            "emails_by_classification": count(e.classification for e in self.emails),
            "ledger_comparison": count(c.outcome for c in self.comparison),
            "human_intervention": len(self.human),
            "human_intervention_by_classification": count(h.classification for h in self.human),
        }


def dry_run(
    staging: Staging,
    review: DocumentReview,
    issuers: Mapping[str, str],
    document_states: Mapping[str, DocumentState] | None = None,
    email_states: Mapping[int, ItemState] | None = None,
    own_senders: Iterable[str] = (),
) -> DryRun:
    """The policy over every candidate, compared with the ledgers' states if given."""
    docs = evaluate_documents(staging, review, issuers, own_senders)
    emails = evaluate_emails(staging, review, docs)
    run = DryRun(documents=docs, emails=emails)
    ds, es = document_states or {}, email_states or {}
    run.comparison = compare_with_ledgers(docs, emails, ds, es)
    run.human = human_intervention(docs, emails, ds, es)
    return run

