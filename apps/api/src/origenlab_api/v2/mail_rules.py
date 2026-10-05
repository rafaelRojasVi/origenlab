"""Email → cases: the rules (spec `2026-10-05-gmail-auto-cases-design.md` §3). Pure.

Input: the captured `gmail_message` evidence and a snapshot of the cases, quotes, institutions
and domains the CRM holds. Output: one :class:`PlannedAction` per email the rules have an
opinion about — the rule id, the reasons in Spanish, and the exact command inputs the executor
(`mail_rules_repository.py`) will hand to the **existing** command handlers. No database, no
clock, no network: the same snapshot always plans the same actions, in evidence-id order.

| Rule | When | Mode |
|---|---|---|
| R1 | same Gmail thread as an email already linked to exactly one open case | auto: link |
| R2 | a proposed quote number equals a quote on exactly one open case (same year + correlative) | auto: link |
| R3 | outbound, a NEW CN number in a PDF, recipient domain = one known institution | auto: case + quote |
| R4 | as R3, recipient domain unknown and neither free-mail, supplier nor ours | auto: institution «por confirmar» + case + quote |
| R5 | inbound purchase order on a thread linked to one case (with one live quote revision) | auto: link + won |
| R6 | inbound «otro proveedor» on a thread linked to one open case | auto: link + lost |
| R7 | inbound quote request from a known institution, no linked thread | proposal |
| R8 | everything else (free-mail, supplier, campaign thread, Labdelivery, nothing matched) | none |

**Order.** R5 and R6 are evaluated before R1: both require a linked thread, so R1 would
otherwise shadow them on every email that could trigger them. Each of them also links the
email. Otherwise the first rule that matches wins.

**Refusals, structural.** Two or more candidate cases (or institutions, or new numbers) never
act: the email becomes a proposal naming the candidates. Free-mail and supplier domains never
create an institution or a case (a free-mail sender can still link through R1/R2). A
Labdelivery mention or a campaign thread is R8 before any rule runs.

**Idempotency.** An email that any rule already acted on (`applied_evidence_ids`, read from
the receipts by the repository) or that a person already linked to a case plans nothing. The
executor keys each action's receipt by `(evidence id, rule id)`.

**What the rules read.** The capture stores no message body (`apps/worker/.../capture.py`), so
R5 and R6 read the subject and the attachment file names only. Their phrase lists are below,
by name, and nothing else triggers them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import getaddresses
from typing import Any
from zoneinfo import ZoneInfo

RULE_IDS: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")
AUTO, PROPOSAL, NONE = "auto", "proposal", "none"

#: OrigenLab's own domains: never an institution, never a recipient that counts.
OWN_DOMAINS: frozenset[str] = frozenset({"origenlab.cl"})

#: Free-mail domains. A person writing from one is not an institution; they may only link
#: through R1/R2.
FREE_MAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.cl", "hotmail.es", "outlook.com",
    "outlook.es", "outlook.cl", "live.com", "live.cl", "msn.com", "yahoo.com", "yahoo.es",
    "yahoo.cl", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "gmx.com", "mail.com", "zoho.com", "yandex.com",
})

#: Any of these anywhere in the sender, recipients, subject or a file name → R8 (spec §3).
LABDELIVERY_MARKERS: tuple[str, ...] = ("labdelivery",)

#: R5 — a purchase order. Matched on the folded (lower-case, accent-free) subject and each
#: attachment file name.
PURCHASE_ORDER_PHRASES: tuple[str, ...] = ("orden de compra", "orden_de_compra", "ordendecompra",
                                           "purchase order")
#: An attachment whose (folded) name starts with the OC token: «OC 4500123.pdf», «oc_991.pdf».
PURCHASE_ORDER_FILENAME = re.compile(r"^o\.?c(?![a-z])")
#: R3/R4: only contacto@ sending to a customer opens a case.
CONTACT_SENDER = "contacto@origenlab.cl"
NOT_CUSTOMER_HINTS: frozenset[str] = frozenset({"supplier_rfq", "internal_only"})
#: «OC 4500123», «OC-991», «OC_N°12», «O.C. 77» — the OC token followed by its number.
PURCHASE_ORDER_NUMBER = re.compile(r"(?<![a-z0-9])o\.?c\.?[\s_\-#:]*(?:n[°ºo]?\.?\s*)?(\d{2,})")

#: R6 — the V1 lost-signal phrases, folded.
LOST_PHRASES: tuple[str, ...] = (
    "compramos con otro proveedor",
    "ya fue gestionada por otro proveedor",
    "fue gestionada por otro proveedor",
    "adjudicada a otro proveedor",
    "adjudicado a otro proveedor",
    "se adjudico a otro proveedor",
    "optamos por otro proveedor",
)
LOST_CLOSE_REASON = "otro proveedor"

#: R7 — words that make an inbound email a quote request.
QUOTE_REQUEST_WORDS: tuple[str, ...] = ("cotiz", "solicitud", "presupuesto", "rfq", "quotation",
                                        "precio")

OPEN_STAGES_FOR_A_QUOTE: tuple[str, ...] = ("quoting", "negotiating")
SANTIAGO = ZoneInfo("America/Santiago")
SYSTEM_LABEL = "Sistema (correo)"


# ─────────────────────────────────────────────────────────────── the snapshot ──


@dataclass(frozen=True)
class MailDocument:
    filename: str | None
    sha256: str | None
    cn_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class MailEvidence:
    """One captured `gmail_message` record, as the rules read it."""

    id: str
    thread_id: str | None
    direction: str  # 'outbound' | 'inbound'
    sender: str | None
    recipients: tuple[str, ...]
    subject: str | None
    sent_at: str | None
    documents: tuple[MailDocument, ...] = ()
    #: Cases this email is already linked to (current links).
    linked_case_ids: tuple[str, ...] = ()
    #: The thread holds an outbound bulk send (a campaign copy): never processed.
    campaign_thread: bool = False
    #: V1 `classify_send_direction` on outbound mail (`customer_quote_candidate`, `ambiguous`,
    #: `supplier_rfq`, `internal_only`); None on inbound.
    direction_hint: str | None = None


@dataclass(frozen=True)
class QuoteRevision:
    id: str
    revision_no: int
    status: str
    version: int
    superseded: bool
    sent_at: str | None = None


@dataclass(frozen=True)
class CaseQuote:
    id: str
    quote_number: str
    revisions: tuple[QuoteRevision, ...] = ()


@dataclass(frozen=True)
class CaseState:
    id: str
    title: str
    stage: str
    version: int
    organization_id: str | None
    closed: bool
    #: Gmail threads of the emails currently linked to this case.
    thread_ids: tuple[str, ...] = ()
    quotes: tuple[CaseQuote, ...] = ()


@dataclass(frozen=True)
class Organization:
    id: str
    name: str
    version: int
    confirmation: str
    domains: tuple[str, ...] = ()
    #: Holds a current supplier/manufacturer relationship, or is a supplier by kind.
    is_supplier: bool = False


@dataclass(frozen=True)
class Snapshot:
    evidence: tuple[MailEvidence, ...]
    cases: tuple[CaseState, ...]
    organizations: tuple[Organization, ...]
    applied_evidence_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlannedAction:
    evidence_id: str
    rule_id: str
    mode: str
    reasons: tuple[str, ...]
    case_id: str | None = None
    case_title: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    proposed_domain: str | None = None
    quote_number: str | None = None
    candidates: tuple[str, ...] = ()
    #: `({"command": name, "inputs": {...}}, ...)`. An input value `"@case"`,
    #: `"@case_version"`, `"@organization"` or `"@organization_version"` is filled by the
    #: executor from the step that created it (or from `context`).
    commands: tuple[Mapping[str, Any], ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "rule_id": self.rule_id,
            "mode": self.mode,
            "reasons": list(self.reasons),
            "case_id": self.case_id,
            "case_title": self.case_title,
            "organization_id": self.organization_id,
            "organization_name": self.organization_name,
            "proposed_domain": self.proposed_domain,
            "quote_number": self.quote_number,
            "candidates": list(self.candidates),
            "commands": [{"command": c["command"], "inputs": dict(c["inputs"])} for c in self.commands],
        }


# ─────────────────────────────────────────────────────────────── helpers ──

_TYPED = re.compile(r"^(\d+)([A-Z]*)(?:-(\d{2}|\d{4}))?$")


def quote_key(raw: str | None, default_year: int | None) -> tuple[int, int] | None:
    """(two-digit year, correlative) of a number as typed or filed — `quoteNumbers.ts`'s
    `parseQuoteNumber`: the first five digits after padding; without a year, `default_year`."""
    text = re.sub(r"^(CN|COT)[\s-]*", "", (raw or "").strip().upper())
    m = _TYPED.match(text)
    if not m:
        return None
    digits, _, year_text = m.groups()
    correlative = int(digits.zfill(5)[:5])
    year = int(year_text[-2:]) if year_text else default_year
    if year is None or correlative <= 0:
        return None
    return (year, correlative)


def santiago_year(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(SANTIAGO).year % 100


def gmail_quote_key(token: str, sent_at: str | None) -> tuple[int, int] | None:
    """A capture token («CN12395») read like the dashboard reads it: the capture drops the
    series' leading zero (`int()` on «012395»), and every OrigenLab number is 0xxxx, so a
    token of five or more digits starting 1–9 gets its zero back. The year is the Santiago year
    of the email."""
    restored = re.sub(r"^(CN|COT)?\s*-?\s*([1-9]\d{4,})$", lambda m: f"{m.group(1) or ''}0{m.group(2)}",
                      token.strip(), flags=re.IGNORECASE)
    return quote_key(restored, santiago_year(sent_at))


def canonical_quote_number(key: tuple[int, int]) -> str:
    year, correlative = key
    return f"{correlative:05d}-{year:02d}"


def fold(text: str | None) -> str:
    """Lower-case, accent-free, whitespace collapsed."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain.lower()).strip()


def addresses(values: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    for _, address in getaddresses([v for v in values if v]):
        norm = address.strip().lower()
        if "@" in norm and norm not in out:
            out.append(norm)
    return out


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].strip().lower()


def _is_pdf(doc: MailDocument) -> bool:
    return (doc.filename or "").lower().endswith(".pdf") and bool(doc.sha256)


# ─────────────────────────────────────────────────────────────── the planner ──


class _Index:
    def __init__(self, snapshot: Snapshot) -> None:
        self.cases = {c.id: c for c in snapshot.cases}
        self.by_thread: dict[str, list[CaseState]] = {}
        for c in snapshot.cases:
            for t in c.thread_ids:
                self.by_thread.setdefault(t, []).append(c)
        #: CRM numbers that carry their year: (year, correlative) → cases.
        self.by_quote_key: dict[tuple[int, int], list[CaseState]] = {}
        #: CRM numbers with no year («CN01239»), keyed by each revision's Santiago sent year. A
        #: match through this index is never automatic.
        self.by_yearless_key: dict[tuple[int, int], list[tuple[CaseState, CaseQuote]]] = {}
        #: correlative → (number as written, year or None) of every CRM quote.
        self.by_correlative: dict[int, list[tuple[str, int | None]]] = {}
        for c in snapshot.cases:
            for q in c.quotes:
                key = quote_key(q.quote_number, None)
                if key is not None:
                    self.by_quote_key.setdefault(key, []).append(c)
                    self.by_correlative.setdefault(key[1], []).append((q.quote_number, key[0]))
                    continue
                years = {santiago_year(r.sent_at) for r in q.revisions} - {None}
                for y in years:
                    yk = quote_key(q.quote_number, y)
                    if yk is not None:
                        self.by_yearless_key.setdefault(yk, []).append((c, q))
                bare = quote_key(q.quote_number, 0)
                if bare is not None:
                    self.by_correlative.setdefault(bare[1], []).append((q.quote_number, None))
        self.orgs = {o.id: o for o in snapshot.organizations}
        self.by_domain: dict[str, list[Organization]] = {}
        for o in snapshot.organizations:
            for d in o.domains:
                self.by_domain.setdefault(d.lower(), []).append(o)
        #: New quote keys and new domains an earlier email in this same plan already claims.
        self.claimed_keys: dict[tuple[int, int], str] = {}
        self.claimed_domains: dict[str, str] = {}

    def is_supplier_domain(self, domain: str) -> bool:
        return any(o.is_supplier for o in self.by_domain.get(domain, []))


def _mail_text(e: MailEvidence) -> str:
    parts = [e.sender or "", " ".join(e.recipients), e.subject or ""]
    parts += [d.filename or "" for d in e.documents]
    return fold(" ".join(parts))


def _external_domains(e: MailEvidence) -> list[str]:
    """The other side's domains: recipients for outbound, the sender for inbound."""
    source = e.recipients if e.direction == "outbound" else (e.sender,)
    found: list[str] = []
    for a in addresses(source):
        d = domain_of(a)
        if d and d not in OWN_DOMAINS and d not in found:
            found.append(d)
    return found


def _sender(e: MailEvidence) -> str | None:
    found = addresses([e.sender])
    return found[0] if found else None


def _live_revisions(q: CaseQuote) -> list[QuoteRevision]:
    return [r for r in q.revisions if r.status == "sent" and not r.superseded]


def _po_attachment(e: MailEvidence) -> str | None:
    """`"OC <number>"` / `"orden de compra"` from an attachment named `OC*` or «orden de compra»."""
    for d in e.documents:
        name = fold(d.filename)
        if PURCHASE_ORDER_FILENAME.match(name) or any(p in name for p in PURCHASE_ORDER_PHRASES):
            m = PURCHASE_ORDER_NUMBER.search(name)
            return f"OC {m.group(1)}" if m else "orden de compra"
    return None


def _po_subject(e: MailEvidence) -> bool:
    subject = fold(e.subject)
    return bool(PURCHASE_ORDER_NUMBER.search(subject)) or any(p in subject for p in PURCHASE_ORDER_PHRASES)


def _lost_phrase(e: MailEvidence) -> str | None:
    subject = fold(e.subject)
    return next((p for p in LOST_PHRASES if p in subject), None)


def _sender_may_speak_for(e: MailEvidence, c: CaseState, ix: _Index) -> str | None:
    """None when the sender may close this case (won or lost); else why not.

    The sender's domain must be one of the case institution's, or a free-mail address on the
    case's own thread — never a supplier's and never ours."""
    if e.direction != "inbound":
        return "no es un correo recibido"
    sender = _sender(e)
    if sender is None:
        return "remitente ilegible"
    d = domain_of(sender)
    if d in OWN_DOMAINS:
        return "remitente interno (origenlab.cl)"
    if ix.is_supplier_domain(d):
        return f"remitente de un proveedor ({d})"
    if d in FREE_MAIL_DOMAINS:
        return None
    org = ix.orgs.get(c.organization_id or "")
    if org is not None and d in org.domains:
        return None
    return f"el remitente ({d}) no es de la institución del caso"


def _note(rule_id: str, reasons: list[str]) -> str:
    return f"{SYSTEM_LABEL} {rule_id}: " + "; ".join(reasons)


def _link_step(case: CaseState, evidence_id: str, relation: str, note: str) -> dict[str, Any]:
    return {"command": "link_case_evidence", "inputs": {
        "opportunity_id": "@case", "opportunity_version": "@case_version", "relation": relation,
        "source_record_id": evidence_id, "note": note}}


def _advance_step(stage: str, note: str, close_reason: str | None = None) -> dict[str, Any]:
    inputs: dict[str, Any] = {"opportunity_id": "@case", "opportunity_version": "@case_version",
                              "stage": stage, "note": note}
    if close_reason is not None:
        inputs["close_reason"] = close_reason
    return {"command": "advance_case_stage", "inputs": inputs}


def plan(snapshot: Snapshot) -> list[PlannedAction]:
    """One action per email the rules have an opinion about, in evidence-id order."""
    index = _Index(snapshot)
    out: list[PlannedAction] = []
    for e in sorted(snapshot.evidence, key=lambda x: x.id):
        if e.id in snapshot.applied_evidence_ids:
            continue
        action = _plan_one(e, index)
        if action is not None:
            out.append(action)
    return out


def _plan_one(e: MailEvidence, ix: _Index) -> PlannedAction | None:
    text = _mail_text(e)
    if any(m in text for m in LABDELIVERY_MARKERS):
        return PlannedAction(e.id, "R8", NONE, ("menciona Labdelivery: queda en Revisión",))
    if e.campaign_thread:
        return PlannedAction(e.id, "R8", NONE, ("hilo de una campaña masiva: no se procesa",))

    domains = _external_domains(e)
    supplier_domains = [d for d in domains if ix.is_supplier_domain(d)]
    thread_cases = [c for c in ix.by_thread.get(e.thread_id or "", []) if c.id not in e.linked_case_ids] \
        if e.thread_id else []
    if e.linked_case_ids:
        # A person (or an earlier run) already put this email on a case. Only a won/lost signal
        # on that same, single case may still act; nothing else is ours to decide.
        linked = [ix.cases[c] for c in e.linked_case_ids if c in ix.cases]
        return _signal_rules(e, linked if len(linked) == 1 else [], ix, already_linked=True)

    if supplier_domains and not thread_cases:
        return PlannedAction(e.id, "R8", NONE, (
            f"dominio de proveedor ({', '.join(supplier_domains)}): no se crea institución ni caso",))

    signal = _signal_rules(e, thread_cases, ix, already_linked=False)
    if signal is not None:
        return signal

    if thread_cases:
        return _link_rule(e, "R1", thread_cases, "mismo hilo de Gmail que un correo ya vinculado", None)

    keyed: dict[tuple[int, int], str] = {}
    for doc in e.documents:
        for token in doc.cn_tokens:
            key = gmail_quote_key(token, e.sent_at)
            if key is not None:
                keyed.setdefault(key, token)
    exact: dict[str, CaseState] = {}
    matched_key: tuple[int, int] | None = None
    for key in keyed:
        for c in ix.by_quote_key.get(key, []):
            exact[c.id] = c
            matched_key = key
    if exact and matched_key is not None:
        number = canonical_quote_number(matched_key)
        return _link_rule(e, "R2", list(exact.values()), f"el número {number} ya es una cotización del caso", number)
    for key in keyed:
        yearless = ix.by_yearless_key.get(key, [])
        if yearless:
            numbers = ", ".join(sorted({q.quote_number for _, q in yearless}))
            return PlannedAction(e.id, "R2", PROPOSAL, (
                f"el número {canonical_quote_number(key)} coincide con {numbers} del CRM, que no lleva año "
                "(se leyó el año de su envío): un humano confirma",),
                quote_number=canonical_quote_number(key),
                candidates=tuple(sorted({c.id for c, _ in yearless})))

    if e.direction == "outbound":
        return _creation_rules(e, ix, domains)
    return _inbound_rules(e, ix, domains)


def _link_rule(e: MailEvidence, rule_id: str, cases: list[CaseState], why: str,
               quote_number: str | None) -> PlannedAction:
    open_cases = [c for c in cases if not c.closed]
    if len(cases) >= 2:
        return PlannedAction(e.id, rule_id, PROPOSAL, (
            f"{why}, pero {len(cases)} casos coinciden: un humano elige",),
            quote_number=quote_number, candidates=tuple(sorted(c.id for c in cases)))
    if not open_cases:
        return PlannedAction(e.id, "R8", NONE, (f"{why}, pero el caso está cerrado",),
                             case_id=cases[0].id, case_title=cases[0].title, quote_number=quote_number)
    c = open_cases[0]
    reasons = [why]
    note = _note(rule_id, reasons)
    return PlannedAction(e.id, rule_id, AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                         organization_id=c.organization_id, quote_number=quote_number,
                         commands=(_link_step(c, e.id, "mentions", note),),
                         context={"case": c.id, "case_version": c.version})


def _signal_rules(e: MailEvidence, cases: list[CaseState], ix: _Index, *,
                  already_linked: bool) -> PlannedAction | None:
    """R5 and R6: a won or lost signal on a thread that belongs to exactly one case.

    R5 needs all three: an attachment named `OC*` or «orden de compra», a sender who may speak
    for the case (`_sender_may_speak_for`), and one live quote revision to win. R6 needs the
    phrase and the same sender rule. A signal that fails any of them is a proposal."""
    if not cases:
        return None
    po_file = _po_attachment(e)
    po_subject = _po_subject(e)
    lost = _lost_phrase(e)
    if po_file is None and not po_subject and lost is None:
        return None
    rule_id = "R5" if (po_file is not None or po_subject) else "R6"
    if len(cases) >= 2:
        return PlannedAction(e.id, rule_id, PROPOSAL, (
            f"señal de {'orden de compra' if rule_id == 'R5' else 'pérdida'} en un hilo con {len(cases)} casos",),
            candidates=tuple(sorted(c.id for c in cases)))
    c = cases[0]
    if c.closed:
        return PlannedAction(e.id, "R8", NONE, (f"señal en un hilo cuyo caso ya está cerrado ({c.stage})",),
                             case_id=c.id, case_title=c.title)
    context = {"case": c.id, "case_version": c.version, "from_stage": c.stage}
    sender_problem = _sender_may_speak_for(e, c, ix)

    def proposal(reasons: list[str]) -> PlannedAction:
        return PlannedAction(e.id, rule_id, PROPOSAL, (*reasons, "un humano decide"),
                             case_id=c.id, case_title=c.title, organization_id=c.organization_id)

    if rule_id == "R5":
        reasons = [f"orden de compra ({po_file or 'en el asunto'}) en el hilo del caso"]
        if po_file is None:
            return proposal([*reasons, "sin adjunto «OC…» u «orden de compra»"])
        if sender_problem is not None:
            return proposal([*reasons, sender_problem])
        live = [(q, r) for q in c.quotes for r in _live_revisions(q)]
        if c.stage not in OPEN_STAGES_FOR_A_QUOTE or len(live) != 1:
            why = ("el caso no está cotizando" if c.stage not in OPEN_STAGES_FOR_A_QUOTE
                   else f"el caso tiene {len(live)} revisiones vigentes")
            return proposal([*reasons, why])
        q, r = live[0]
        reasons.append(f"cotización ganada: {q.quote_number} revisión {r.revision_no}")
        note = _note("R5", reasons)
        steps: list[dict[str, Any]] = [] if already_linked else [_link_step(c, e.id, "mentions", note)]
        if c.stage == "quoting":
            steps.append(_advance_step("negotiating", note))
        steps.append({"command": "record_case_won", "inputs": {
            "opportunity_id": "@case", "opportunity_version": "@case_version",
            "quote_revision_id": r.id, "purchase_order": po_file, "note": note}})
        return PlannedAction(e.id, "R5", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                             organization_id=c.organization_id, quote_number=q.quote_number,
                             commands=tuple(steps), context=context)

    reasons = [f"el remitente indica «{lost}» en el hilo del caso"]
    if sender_problem is not None:
        return proposal([*reasons, sender_problem])
    note = _note("R6", reasons)
    link = () if already_linked else (_link_step(c, e.id, "contradicts", note),)
    return PlannedAction(e.id, "R6", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                         organization_id=c.organization_id,
                         commands=(*link, _advance_step("lost", note, LOST_CLOSE_REASON)),
                         context=context)


def _derivation(token: str, sent_at: str | None) -> dict[str, Any]:
    digits = re.sub(r"^(CN|COT)?\s*-?\s*", "", token.strip(), flags=re.IGNORECASE)
    return {"token": token, "restored_leading_zero": bool(re.fullmatch(r"[1-9]\d{4,}", digits)),
            "year_from_sent_at": santiago_year(sent_at)}


def _creation_rules(e: MailEvidence, ix: _Index, domains: list[str]) -> PlannedAction:
    """R3 and R4: contacto@ sending a NEW quote number in a PDF to a customer."""
    sender = _sender(e)
    if sender != CONTACT_SENDER or (e.direction_hint or "") in NOT_CUSTOMER_HINTS:
        why = (f"el remitente no es {CONTACT_SENDER}" if sender != CONTACT_SENDER
               else f"envío clasificado «{e.direction_hint}», no a un cliente")
        return PlannedAction(e.id, "R8", NONE, (f"{why}: no se abre caso",))
    pdf_keys: dict[tuple[int, int], tuple[str, MailDocument]] = {}
    for doc in sorted(e.documents, key=lambda d: (d.filename or "", d.sha256 or "")):
        if not _is_pdf(doc):
            continue
        for token in doc.cn_tokens:
            key = gmail_quote_key(token, e.sent_at)
            if key is not None:
                pdf_keys.setdefault(key, (token, doc))
    if not pdf_keys:
        return PlannedAction(e.id, "R8", NONE, ("correo saliente sin un PDF con número CN nuevo",))
    if len(pdf_keys) >= 2:
        numbers = ", ".join(canonical_quote_number(k) for k in sorted(pdf_keys))
        return PlannedAction(e.id, "R3", PROPOSAL, (f"varios números nuevos en un correo ({numbers}): un humano decide",))
    key, (token, doc) = next(iter(pdf_keys.items()))
    number = canonical_quote_number(key)
    if key in ix.claimed_keys:
        return PlannedAction(e.id, "R3", PROPOSAL, (
            f"misma cotización {number} que otro correo de esta pasada ({ix.claimed_keys[key]}): "
            "se aplica primero aquel y este se vincula después",), quote_number=number)
    elsewhere = [n for n, y in ix.by_correlative.get(key[1], []) if y != key[0]]
    if elsewhere:
        return PlannedAction(e.id, "R3", PROPOSAL, (
            f"el correlativo de {number} ya existe en el CRM como {', '.join(sorted(elsewhere))} "
            "(otro año o sin año): un humano decide si es la misma cotización",), quote_number=number)

    free = [d for d in domains if d in FREE_MAIL_DOMAINS]
    named = [d for d in domains if d not in FREE_MAIL_DOMAINS]
    known: dict[str, Organization] = {}
    unknown: list[str] = []
    for d in named:
        orgs = ix.by_domain.get(d, [])
        if orgs:
            for o in orgs:
                known[o.id] = o
        else:
            unknown.append(d)
    if any(o.is_supplier for o in known.values()):
        return PlannedAction(e.id, "R8", NONE, ("destinatario de un proveedor: no se crea caso",), quote_number=number)
    if len(known) >= 2:
        return PlannedAction(e.id, "R3", PROPOSAL, (
            f"cotización {number} enviada a {len(known)} instituciones conocidas: un humano elige",),
            quote_number=number, candidates=tuple(sorted(known)))
    derivation = _derivation(token, e.sent_at)
    base_reasons = [f"cotización nueva {number} (PDF «{doc.filename}»; número derivado del nombre del archivo: "
                    f"{token}{', cero inicial restituido' if derivation['restored_leading_zero'] else ''}, "
                    f"año del envío)"]

    if len(known) == 1:
        o = next(iter(known.values()))
        reasons = [*base_reasons, f"destinatario de la institución conocida «{o.name}»"]
        ix.claimed_keys[key] = e.id
        steps = _case_with_quote_steps(e, doc, number, token, derivation, f"{o.name} — cotización {number}",
                                       _note("R3", reasons), organization_id=o.id, organization_version=o.version)
        return PlannedAction(e.id, "R3", AUTO, tuple(reasons), case_title=f"{o.name} — cotización {number}",
                             organization_id=o.id, organization_name=o.name, quote_number=number,
                             commands=steps)
    if len(unknown) == 1:
        d = unknown[0]
        if d in ix.claimed_domains:
            return PlannedAction(e.id, "R4", PROPOSAL, (
                f"el dominio {d} ya crea una institución «por confirmar» con otro correo de esta pasada "
                f"({ix.claimed_domains[d]}): se decide después",), quote_number=number, proposed_domain=d)
        reasons = [*base_reasons, f"dominio sin institución ({d}): se crea «por confirmar»"]
        ix.claimed_keys[key] = e.id
        ix.claimed_domains[d] = e.id
        note = _note("R4", reasons)
        steps = (
            {"command": "register_mail_organization", "inputs": {
                "name": d, "domain": d, "origin_source_record_id": e.id, "note": note}},
            *_case_with_quote_steps(e, doc, number, token, derivation, f"{d} — cotización {number}", note,
                                    organization_id="@organization", organization_version="@organization_version"),
        )
        return PlannedAction(e.id, "R4", AUTO, tuple(reasons), case_title=f"{d} — cotización {number}",
                             organization_name=d, proposed_domain=d, quote_number=number, commands=steps)
    if len(unknown) >= 2:
        return PlannedAction(e.id, "R4", PROPOSAL, (
            f"cotización {number} enviada a varios dominios sin institución ({', '.join(unknown)}): un humano elige",),
            quote_number=number)
    if free:
        return PlannedAction(e.id, "R8", NONE, (
            f"cotización {number} enviada a correo gratuito ({', '.join(free)}): no se crea institución",),
            quote_number=number)
    return PlannedAction(e.id, "R8", NONE, ("correo saliente sin destinatario externo",), quote_number=number)


def _case_with_quote_steps(e: MailEvidence, doc: MailDocument, number: str, token: str,
                           derivation: dict[str, Any], title: str, note: str, *, organization_id: str,
                           organization_version: Any) -> tuple[dict[str, Any], ...]:
    return (
        {"command": "open_commercial_case", "inputs": {
            "title": title, "origin_source_record_id": e.id, "note": note}},
        {"command": "add_case_organization", "inputs": {
            "opportunity_id": "@case", "opportunity_version": "@case_version",
            "organization_id": organization_id, "organization_version": organization_version,
            "role": "requesting_institution", "note": note}},
        _advance_step("qualifying", note),
        _advance_step("qualified", note),
        _advance_step("quoting", note),
        {"command": "record_historical_quotation", "inputs": {
            "opportunity_id": "@case", "quote_number": number, "printed_quote_numbers": [token],
            "quote_number_derivation": derivation,
            "document_sha256": doc.sha256, "sent_at": e.sent_at, "origin_source_record_id": e.id,
            "ledger_decision_id": f"mail-rule:{e.id}", "filename": doc.filename, "note": note}},
    )


def _inbound_rules(e: MailEvidence, ix: _Index, domains: list[str]) -> PlannedAction:
    """R7 (proposal) or R8."""
    if not domains:
        return PlannedAction(e.id, "R8", NONE, ("correo interno",))
    d = domains[0]
    if d in FREE_MAIL_DOMAINS:
        return PlannedAction(e.id, "R8", NONE, (f"remitente con correo gratuito ({d}): sólo R1/R2 pueden vincularlo",))
    orgs = ix.by_domain.get(d, [])
    subject = fold(e.subject)
    if orgs and any(w in subject for w in QUOTE_REQUEST_WORDS):
        if len(orgs) == 1:
            o = orgs[0]
            return PlannedAction(e.id, "R7", PROPOSAL, (
                f"solicitud de cotización de «{o.name}» sin hilo vinculado: propuesta «Nuevo caso»",),
                organization_id=o.id, organization_name=o.name)
        return PlannedAction(e.id, "R7", PROPOSAL, (
            f"solicitud de cotización desde {d}, dominio de {len(orgs)} instituciones",),
            candidates=tuple(sorted(o.id for o in orgs)))
    return PlannedAction(e.id, "R8", NONE, ("ninguna regla aplica: queda en Revisión",))


__all__ = [
    "AUTO", "CONTACT_SENDER", "FREE_MAIL_DOMAINS", "NOT_CUSTOMER_HINTS", "PURCHASE_ORDER_FILENAME", "LABDELIVERY_MARKERS", "LOST_CLOSE_REASON", "LOST_PHRASES", "NONE",
    "OWN_DOMAINS", "PROPOSAL", "PURCHASE_ORDER_NUMBER", "PURCHASE_ORDER_PHRASES", "QUOTE_REQUEST_WORDS",
    "RULE_IDS", "SYSTEM_LABEL", "CaseQuote", "CaseState", "MailDocument", "MailEvidence", "Organization",
    "PlannedAction", "QuoteRevision", "Snapshot", "addresses", "canonical_quote_number", "domain_of", "fold",
    "gmail_quote_key", "plan", "quote_key", "santiago_year",
]
