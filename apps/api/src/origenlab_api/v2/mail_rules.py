"""Email → cases: the rules (spec `2026-10-05-gmail-auto-cases-design.md` §3). Pure.

Input: the captured `gmail_message` evidence and a snapshot of the cases, quotes, institutions
and domains the CRM holds. Output: one :class:`PlannedAction` per email the rules have an
opinion about — the rule id, the reasons in Spanish, and the exact command inputs the executor
(`mail_rules_repository.py`) will hand to the **existing** command handlers. No database, no
clock, no network: the same snapshot always plans the same actions, in evidence-id order.

| Rule | When | Mode |
|---|---|---|
| R1 | same Gmail thread as an email already linked to exactly one open case | auto: link; a new sent CN PDF on that thread also records the quote when the case has a requester |
| R2 | a quote number — from a PDF name, or from the subject (CN token or number with year) when no PDF names one and the thread is new, or else from the sender's own text (`quote_refs:v1`) — equals a quote on exactly one open **or won** case (same year + correlative) | auto: link (a won case keeps its later mail — payment, order, delivery — without reopening) |
| R3 | outbound, a NEW CN number in a PDF, recipient domain = one known institution | auto: case + quote |
| R4 | as R3, recipient domain unknown and neither free-mail, supplier nor ours | auto: institution «por confirmar» + case + quote |
| R5 | inbound purchase order on a thread linked to one case (with one live quote revision) | auto: link + won |
| R6 | inbound «otro proveedor» on a thread linked to one open case | auto: link + lost |
| R7 | inbound quote request (quote words in the subject, or the AI reading's intent `quote_request`), no linked thread, not a reply; never when the AI reads a supplier's offer | numbered quotation references require review; otherwise open lead |
| R8 | everything else (supplier, a reply to our own supplier RFQ, campaign thread, Labdelivery, nothing matched) | none |
| R9 | outbound, a NEW CN number in a PDF, no recipient names an institution (free-mail), and the PDF's own name names exactly one institution (`CN09876-<institución>.pdf`, as the Cotizador writes it) | auto when that institution has exactly one open case: link + «Enviada» + quote; otherwise a proposal |
| R10 | as R9, but the institution the PDF names is not in the CRM | «Aplicar» only: institution «por confirmar» (named as the PDF names it, no domain) + case in «Enviada» + quote |
| R12 | inbound, no thread or quote number placed it, and the context matcher (`case_match:v1`, the worker's triage) is sure: layer 1 «exact», or the AI's pick with confidence «high» — also for a supplier writing about a client's deal | auto: link to that open case (a won case keeps it closed); a medium or low pick stays a suggestion |
| R13 | outbound, a NEW CN number in a PDF on a new thread, answering the client's open request with no quote yet (a recipient wrote on it or asked through a forward, a recipient's domain is its institution's, or the PDF names its institution) | auto: link + «Enviada» + quote on that request; two requests → a proposal |
| R11 | a request forwarded to contacto@ (`forwarded_request`, read by the worker), still pending | auto: joins the one open case whose emails come from that customer, or of the customer's institution; otherwise a request (`create_request_from_forward`) with the known institution, the company the forward names, or none. Never a supplier's domain; never links a Labdelivery-mailbox email |
| R14 | outbound from contacto@, sent in the last `LATE_QUOTE_WINDOW_DAYS`, already linked to exactly one open case (by a rule or a person), carrying exactly one CN PDF whose number no case records | auto: the R1 gates (requesting institution, open stage, correlative not under another year), then «Enviada» + quote on that case; no link (it is there). The one rule that still reads an email another rule acted on |
| R15 | an institution «por confirmar» that R4, R10 or «Completar con la cotización» created, not a supplier, no free-mail domain, with a quote on its case recorded ≥ `SETTLE_DAYS` ago and still sent | auto: confirmed — renamed first from the PDF's institution segment when it is named after its domain (exactly one distinct segment across its quotes, naming which institution, not only a kind; else a person); confirmed with no operator named. Keyed by the institution's id, not an email |
| R16 | outbound from contacto@, carrying the PDF of a Cotizador revision of that case that is approved and not yet sent — matched by the file name's number **and** revision letter (the browser's «Cotización CN01274A — ….pdf» or a hand-renamed «CN01274A-….pdf»; A = revision 2; a «(borrador)» print never; the email's year, else the year before), sent after the approval | auto: each such revision → «sent» (the email, its PDF's hash), the case → «Enviada». The PDF is never read: what it says is what the Cotizador approved. Several PDFs in one email: each its revision, on each case it belongs to, the email linked there. A closed case among them, an email a person put elsewhere, or an open case of its thread no PDF names → nothing. Reads an email another rule acted on, once |
| R17 | inbound, already on exactly one case in «Enviada» (`quoting`), sent in the last `REPLY_WINDOW_DAYS` and after that case's live quote was sent, from neither us nor a supplier, not an automatic reply | auto: the case → «Conversación» (`negotiating`). Reads an email another rule acted on (R1 links it a pass earlier), once |

**Order.** R5 and R6 are evaluated before R1: both require a linked thread, so R1 would
otherwise shadow them on every email that could trigger them. Each of them also links the
email. Otherwise the first rule that matches wins.

**Refusals, structural.** Two or more candidate cases (or institutions, or new numbers) never
act: the email becomes a proposal naming the candidates. A free-mail address is never treated as
an institution, but a clear inbound quote request still opens a real lead with no institution.
Supplier domains never create a case. A Labdelivery mention or a campaign thread is R8 before
any rule runs. A sent quote on an existing thread is recorded automatically only when that case
already has its requesting institution; otherwise it stays a proposal until a person names it.

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
from dataclasses import dataclass, field, replace
from datetime import datetime
from email.utils import getaddresses
from typing import Any
from zoneinfo import ZoneInfo

RULE_IDS: tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13",
                             "R14", "R15", "R16", "R17")
AUTO, PROPOSAL, NONE = "auto", "proposal", "none"

#: The header value that marks a self-sent web-quote-form message. The triage worker imports
#: this same constant (triage_rules.py), so both sides agree on the sentinel.
WEB_QUOTE_ORIGIN: str = "web-quote-form/v1"

#: OrigenLab's own domains: never an institution, never a recipient that counts.
OWN_DOMAINS: frozenset[str] = frozenset({"origenlab.cl"})

#: Free-mail domains. A person writing from one is not an institution. A clear quote request may
#: still open a lead with no institution; identity is resolved separately.
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
#: R2 on a won case: the one link a closed case accepts (`mail_rules_repository`).
LINK_AFTER_CLOSE = "link_case_evidence_after_close"

#: R7 — words that make an inbound email a quote request.
#: R7 on the AI reading alone: said in the reasons, so «Quitar» shows why.
AI_REQUEST_REASON = "la lectura del correo (IA) dice que pide precio"
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
    #: The worker's deterministic requester:v1 check has run. Free/unknown senders wait for this
    #: before R7 opens a case, so identity evidence cannot lose a race with the API timer.
    requester_checked: bool = False
    #: A unique, exact active CRM organization name found in the cleaned message body. This is
    #: evidence, not a fuzzy/model guess; the planner still verifies the id against its snapshot.
    requester_organization_id: str | None = None
    requester_organization_name: str | None = None
    #: Quote numbers the sender's own text names, as the worker read them (`quote_refs:v1`): R2
    #: reads them when neither a PDF nor the subject names a number.
    body_quote_tokens: tuple[str, ...] = ()
    #: The context matcher's reading (`case_match:v1`): decision, candidates and the AI's pick (R12).
    case_match: Mapping[str, Any] | None = None
    #: The AI reading's intent (`triage:vN` value.reading.intent), when the model read this email:
    #: «quote_request» lets R7 open a request whose subject has no quote word; «supplier_offer»
    #: keeps a supplier's own quotation from opening one.
    model_intent: str | None = None
    #: The same reading's stage (the CRM stage key it reads, e.g. «lost»), whether a person should
    #: answer the email, and its one-line summary: R18 and R19 read them (owner, 2026-10-29).
    model_stage: str | None = None
    model_needs_reply: bool | None = None
    model_summary: str | None = None
    #: The newest triage row's rule class (`bounce`, `auto_reply`, …), with or without a reading.
    triage_class: str | None = None
    #: Lower-case machine headers set by OrigenLab on self-sent messages (e.g. x-origenlab-origin
    #: for web-quote-form messages). Absent on all normal inbound/outbound mail.
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    #: Reply-To address (lower-cased bare address), set only on web-form self-sent messages.
    reply_to: str | None = None


@dataclass(frozen=True)
class QuoteRevision:
    id: str
    revision_no: int
    status: str
    version: int
    superseded: bool
    sent_at: str | None = None
    #: 'authored' (the Cotizador) or 'historical_import' (a sent PDF recorded as found). R16 reads it.
    origin: str = ""
    approved_at: str | None = None


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
    #: Lower-case addresses that wrote the inbound emails linked to this case (R11).
    people: tuple[str, ...] = ()
    #: «En pausa hasta…»: the due date of the case's open «Retomar: …» task still in the future (R17).
    paused_until: str | None = None


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
class ForwardRequest:
    """A request forwarded to contacto@, its copies merged (`forwarded_requests.group_forwards`)."""

    id: str
    ids: tuple[str, ...]
    customer_email: str | None
    customer_name: str | None = None
    company: str | None = None
    requested_item: str | None = None
    origins: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstitutionFacts:
    """An institution «por confirmar», as R15 reads it (`mail_rules_repository._INSTITUTIONS_SQL`)."""

    organization_id: str
    #: What created it: «R4», «R10», «R11» (the apply receipt's rule), «complete_case» («Completar
    #: con la cotización»), or None (a person, an import).
    created_by: str | None
    #: The PDF file name (None when unknown) of each quote revision on its cases that is still sent
    #: and was recorded at least `SETTLE_DAYS` ago — the repository applies the clock.
    settled_quote_files: tuple[str | None, ...] = ()


#: R17 reads replies sent in the last this many days: an old reply on a case left in «Enviada» is a
#: person's to judge, and the first pass after release must not move a backlog of cases at once.
REPLY_WINDOW_DAYS = 14
#: Subject words of an automatic reply (out of office, delivery receipt): never the client answering.
AUTO_REPLY_PHRASES: tuple[str, ...] = (
    "respuesta automatica", "automatic reply", "auto reply", "autoreply", "out of office", "fuera de la oficina",
    "fuera de oficina", "ausente", "vacaciones", "undeliverable", "no se puede entregar", "delivery status",
    "read receipt", "leido:", "acuse de recibo",
)


@dataclass(frozen=True)
class Snapshot:
    evidence: tuple[MailEvidence, ...]
    cases: tuple[CaseState, ...]
    organizations: tuple[Organization, ...]
    applied_evidence_ids: frozenset[str] = frozenset()
    #: Pending forwarded requests (R11).
    forwards: tuple[ForwardRequest, ...] = ()
    #: (evidence id, rule id) of every apply receipt, undone or not: R14 and R15 act once per subject.
    applied_pairs: frozenset[tuple[str, str]] = frozenset()
    #: Institutions «por confirmar» (R15).
    institutions: tuple[InstitutionFacts, ...] = ()
    #: R14 acts only on email sent at or after this instant (ISO; the repository sets it to
    #: `LATE_QUOTE_WINDOW_DAYS` ago, as Operaciones lists them). None: no email qualifies.
    late_quote_since: str | None = None
    #: R17 acts only on a reply sent at or after this instant (ISO; the repository sets it to
    #: `REPLY_WINDOW_DAYS` ago). None: no email qualifies.
    reply_since: str | None = None


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


def _instant(iso: str | None) -> datetime | None:
    """An aware instant from an ISO timestamp; None when absent, naive or malformed."""
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


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


#: An OrigenLab quote number as a person types it in a subject, read conservatively: a CN token
#: («CN01259», «CN 1259-26») or a number **with its year** («01259-26», «cotización N°01259-26»).
#: A bare correlative («Cotización 1259») is not read: without a year it would only ever be a
#: proposal, and R7 already files it for review. The correlative is four or five digits (a leading
#: zero optional) with up to three letters; a sixth digit («Cotización 123553», a supplier's own
#: number) disqualifies, and so does a `-` or `_` right before it («OC-01259-26» is an order).
_SUBJECT_QUOTE_NUMBER = re.compile(
    r"(?<![a-z0-9_-])cn\s*-?\s*(0?\d{4,5}[a-z]{0,3}(?:-(?:\d{4}|\d{2}))?)(?![\d-])"
    r"|(?<![a-z0-9_-])(0?\d{4,5}[a-z]{0,3}-(?:\d{4}|\d{2}))(?![\d-])",
    re.IGNORECASE,
)


def quote_number_tokens(text: str | None) -> list[str]:
    """Each quote number a line of text names (`_SUBJECT_QUOTE_NUMBER`), as typed, upper-cased, in
    order: «CN09259», «09259-26». The worker reads the email body with it (`quote_refs:v1`)."""
    return [(("CN" + m.group(1)) if m.group(1) else (m.group(2) or "")).upper()
            for m in _SUBJECT_QUOTE_NUMBER.finditer(text or "")]


def subject_quote_keys(subject: str | None, sent_at: str | None) -> dict[tuple[int, int], str]:
    """(year, correlative) → token for each quote number a subject line names
    (`_SUBJECT_QUOTE_NUMBER`), read like a PDF token (`gmail_quote_key`: the dropped leading
    zero of a CN token is restored; a CN token without a year takes the email's Santiago year)."""
    return token_quote_keys(quote_number_tokens(subject), sent_at)


def token_quote_keys(tokens: Iterable[str], sent_at: str | None) -> dict[tuple[int, int], str]:
    """(year, correlative) → token, for tokens as `quote_number_tokens` reads them."""
    out: dict[tuple[int, int], str] = {}
    for token in tokens:
        key = gmail_quote_key(token, sent_at)
        if key is not None:
            out.setdefault(key, token.upper())
    return out


def canonical_quote_number(key: tuple[int, int]) -> str:
    year, correlative = key
    return f"{correlative:05d}-{year:02d}"


def fold(text: str | None) -> str:
    """Lower-case, accent-free, whitespace collapsed."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", plain.lower()).strip()


#: «CN09876-Instituto … (SIGLA).pdf», «CN 09876-26 - Institución.pdf»: the quote token, then the name.
_QUOTE_FILE_NAME = re.compile(r"^\s*(?:CN|COT)\s*-?\s*\d+(?:\s*-\s*\d{2}(?!\d))?\s*[-_–]\s*(?P<name>.+?)\s*\.pdf\s*$",
                              re.IGNORECASE)
_PARENTHETICAL = re.compile(r"\(([^()]*)\)")


def quote_file_institution(filename: str | None) -> str | None:
    """The institution a Cotizador PDF names after its quote token, or None."""
    m = _QUOTE_FILE_NAME.match(filename or "")
    if not m:
        return None
    name = m.group("name").strip(" -_")
    return name if len(fold(name)) >= 3 else None


#: The Cotizador writes «<persona> - <institución>» more often than the institution alone, and may
#: end with a model («…-MB32»): the name is split into segments on dashes.
_SEGMENT_SPLIT = re.compile(r"\s*[-–—]\s*")
_MODEL_CODE = re.compile(r"^[A-Z]{1,5}\d[A-Z0-9]*$")
#: What makes a segment an institution rather than a person: a legal form, a kind of
#: institution, or «(SIGLA)». A person's name («Pedro Ficticio Pérez») has none of these.
_INSTITUTION_MARKER = re.compile(
    r"\b(?:s\.?\s?a\.?|spa|ltda\.?|limitada|eirl|inc\.?|llc|corp\.?|group|universidad|instituto|"
    r"laboratorios?|lab|labs|hospital|clinica|facultad|centro|servicio|ministerio|municipalidad|"
    r"corporacion|fundacion|empresas?|agricola|sociedad|chile|foods|industrial|comercial|asociacion|"
    r"cooperativa|escuela|colegio|liceo)(?=\s|$|[.,)])")


def quote_file_segments(name: str | None) -> list[str]:
    """The dash-separated parts of a quote file name, without model codes or 1–2 letter crumbs."""
    parts = [p.strip(" _,") for p in _SEGMENT_SPLIT.split(name or "")]
    return [p for p in parts if len(fold(p)) >= 3 and not _MODEL_CODE.match(p)]


def institution_segment(name: str | None) -> str | None:
    """The one segment that names an institution (R10), or None when none or several do.

    A name with no dash is one part that may still hold a person («Persona Universidad X»,
    «Persona (SIGLA)»): it counts only when it *starts* with a kind of institution or legal form."""
    parts = quote_file_segments(name)
    marked = [p for p in parts if _PARENTHETICAL.search(p) or _INSTITUTION_MARKER.search(fold(p))]
    if len(marked) != 1:
        return None
    if len(parts) == 1 and not _INSTITUTION_MARKER.match(fold(marked[0])):
        return None
    return marked[0]


def institution_keys(name: str | None) -> set[str]:
    """Folded forms one institution is recognised by: the whole name, the name without its
    parenthetical, and an acronym of three or more letters in parentheses («(SIGLA)»)."""
    full = fold(name)
    keys = {full} if full else set()
    base = fold(_PARENTHETICAL.sub(" ", name or ""))
    if base:
        keys.add(base)
    for inner in _PARENTHETICAL.findall(name or ""):
        acronym = fold(inner)
        if len(acronym) >= 3:
            keys.add(acronym)
    return keys


def addresses(values: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    for _, address in getaddresses([v for v in values if v]):
        norm = address.strip().lower()
        if "@" in norm and norm not in out:
            out.append(norm)
    return out


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].strip().lower()


#: SQL, for a `gmail_message` source record aliased `sr`: the enquirer's address when the email is
#: a web-quote-form submission — the triage worker saw the `x-origenlab-origin` marker (its reason
#: is the persisted proof: the raw headers are not stored) and the capture holds a `reply_to`
#: participant. Null on every other email. The rules' snapshot reads the same two facts
#: (mail_rules_repository._EVIDENCE_SQL); the case reads add this column as `web_form_reply_to`
#: and pass it to `mail_direction` / `web_form_enquirer`.
WEB_FORM_REPLY_TO_SQL = """(select p.address_norm
          from comms.message cm
          join comms.message_participant p on p.message_id = cm.id and p.role = 'reply_to'
         where cm.provider_message_id = sr.payload->>'gmail_message_id'
           and exists (select 1 from evidence.assertion a
                        where a.source_record_id = sr.id and a.kind = 'message_triage'
                          and a.value_norm ~ '^triage:v[0-9]+$'
                          and a.value -> 'reasons' ? 'header:x-origenlab-origin')
         order by p.address_norm limit 1)"""


def web_form_enquirer(sender: str | None, web_form_reply_to: str | None) -> str | None:
    """The visitor who wrote a web-quote-form submission, else None.

    The form makes contacto@ mail itself with the visitor as Reply-To, so the From is OrigenLab's.
    Same predicate as rule R7's (`plan_email`): the From is an own domain, the message carries the
    web-form marker and a Reply-To participant exists (`web_form_reply_to`, read with
    `WEB_FORM_REPLY_TO_SQL`, is non-null only with both). The case reads then treat the email as
    received from that address.
    """
    if not web_form_reply_to or "@" not in web_form_reply_to:
        return None
    found = addresses([sender])
    if not found or domain_of(found[0]) not in OWN_DOMAINS:
        return None
    return web_form_reply_to.strip().lower()


def mail_direction(sender: str | None, direction_hint: Any, comms_direction: str | None,
                   web_form_reply_to: str | None = None) -> str:
    """Whether a captured email was sent by OrigenLab (`outbound`) or to it (`inbound`).

    A web-quote-form submission is `inbound` (from its Reply-To, `web_form_enquirer`). Else the
    capture's `comms.message.direction` when there is one; else the staged record's
    `direction_hint` (only ever set on mail OrigenLab sent); else the sender's domain. One rule,
    used by the email → cases planner and by the case cards' «último contacto».
    """
    if web_form_enquirer(sender, web_form_reply_to):
        return "inbound"
    if comms_direction in ("outbound", "inbound"):
        return comms_direction
    if direction_hint:
        return "outbound"
    found = addresses([sender])
    return "outbound" if found and domain_of(found[0]) in OWN_DOMAINS else "inbound"


def _is_pdf(doc: MailDocument) -> bool:
    return (doc.filename or "").lower().endswith(".pdf") and bool(doc.sha256)


# ─────────────────────────────────────────────────────────────── the planner ──


class _Index:
    def __init__(self, snapshot: Snapshot) -> None:
        self.late_quote_since = snapshot.late_quote_since
        self.reply_since = snapshot.reply_since
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
        #: Folded name, name without its parenthetical, and «(ACRONYM)» → organizations (R9).
        self.by_name: dict[str, list[Organization]] = {}
        for o in snapshot.organizations:
            for k in institution_keys(o.name):
                self.by_name.setdefault(k, []).append(o)
        self.by_domain: dict[str, list[Organization]] = {}
        for o in snapshot.organizations:
            for d in o.domains:
                self.by_domain.setdefault(d.lower(), []).append(o)
        #: New quote keys and new domains an earlier email in this same plan already claims.
        self.claimed_keys: dict[tuple[int, int], str] = {}
        self.claimed_domains: dict[str, str] = {}
        #: Our own price requests to suppliers: (when sent, the supplier domains it went to). V1
        #: marks ANY outbound «RFQ / request for quotation» subject as `supplier_rfq` — a reply to
        #: a customer, a forward on a customer's thread — so only an RFQ that started its own
        #: thread counts (no inbound mail on that thread before it). An inbound email later than
        #: such an RFQ, from one of its domains, is that supplier answering us. Free-mail and
        #: customer domains never count: one supplier on gmail.com must not silence every
        #: gmail.com request, and a customer we also buy from keeps reaching R7.
        customer_domains = {d.lower() for o in snapshot.organizations if not o.is_supplier for d in o.domains}
        first_inbound: dict[str, datetime] = {}
        for ev in snapshot.evidence:
            at = _instant(ev.sent_at)
            if ev.direction == "inbound" and ev.thread_id and at is not None:
                if ev.thread_id not in first_inbound or at < first_inbound[ev.thread_id]:
                    first_inbound[ev.thread_id] = at
        rfqs: list[tuple[datetime, frozenset[str]]] = []
        for ev in snapshot.evidence:
            at = _instant(ev.sent_at)
            if ev.direction != "outbound" or (ev.direction_hint or "") != "supplier_rfq" or at is None:
                continue
            if ev.thread_id and ev.thread_id in first_inbound and first_inbound[ev.thread_id] < at:
                continue  # a reply or forward on someone else's thread, not a price request we started
            domains = frozenset(d for d in _external_domains(ev)
                                if d not in FREE_MAIL_DOMAINS and d not in customer_domains)
            if domains:
                rfqs.append((at, domains))
        self.rfqs: tuple[tuple[datetime, frozenset[str]], ...] = tuple(rfqs)
        #: Domains whose inbound mail the AI read as a supplier's offer (never free mail): a forwarded
        #: «request» from one of them is a supplier writing, not a customer (R11).
        self.ai_supplier_domains: frozenset[str] = frozenset(
            d for ev in snapshot.evidence if ev.direction == "inbound" and ev.model_intent == "supplier_offer"
            for d in _external_domains(ev) if d not in FREE_MAIL_DOMAINS)

    def answers_our_rfq(self, e: "MailEvidence", domains: list[str]) -> bool:
        """An inbound email later than one of our RFQs, from a domain that RFQ went to."""
        at = _instant(e.sent_at)
        if e.direction != "inbound" or at is None:
            return False
        return any(sent < at and any(d in to for d in domains) for sent, to in self.rfqs)

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
        action: PlannedAction | None = None
        if e.id in snapshot.applied_evidence_ids:
            # Another rule acted on this email already. Only R14 and R16 still read it — each once: a
            # sent quote the earlier action linked but did not record (owner 2026-10-19), a Cotizador
            # quote it carried (owner 2026-10-21).
            if (e.id, "R14") not in snapshot.applied_pairs and e.linked_case_ids:
                action = _late_quote_rule(e, index)
        else:
            action = _plan_one(e, index)
        if action is not None:
            out.append(action)
        # R16 waits while another rule acts on the same email in this pass (both would move its
        # case); it plans on the next pass, once that action's receipt is in.
        if (action is None or action.mode != AUTO) and (e.id, "R16") not in snapshot.applied_pairs:
            sent = _authored_sent_rule(e, index)
            if sent is not None:
                out.append(sent)
        # R17 likewise: the reply R1 links on one pass moves its case on the next.
        if (action is None or action.mode != AUTO) and (e.id, "R17") not in snapshot.applied_pairs:
            reply = _client_reply_rule(e, index)
            if reply is not None:
                out.append(reply)
    for f in sorted(snapshot.forwards, key=lambda x: x.id):
        if f.id not in snapshot.applied_evidence_ids and not (set(f.ids) & snapshot.applied_evidence_ids):
            out.append(_plan_forward(f, index))
    for inst in sorted(snapshot.institutions, key=lambda x: x.organization_id):
        if inst.organization_id in snapshot.applied_evidence_ids:
            continue
        confirmed = _confirm_institution_rule(inst, index)
        if confirmed is not None:
            out.append(confirmed)
    return out


ATTACH_FORWARD = "attach_forward_to_case"
CREATE_REQUEST_FROM_FORWARD_STEP = "create_request_from_forward"
#: Step added after ``open_commercial_case`` on R7 to wire the sender as a participant so
#: ``client_open_cases`` finds the request case when the operator opens «+ Nueva». (owner, 2026-10-22)
ADD_CASE_SENDER_PARTICIPANT = "add_case_sender_participant"


def _plan_forward(f: ForwardRequest, ix: _Index) -> PlannedAction:
    """R11 (owner, 2026-10-14: «the system acts, I remove what is wrong»). A forwarded request
    joins the one open case already speaking with that customer, or of the customer's institution;
    otherwise it becomes a request of its own. The Labdelivery-mailbox copy is never linked — the
    handlers (`forwarded_requests.py`) link our own copies only."""
    email = (f.customer_email or "").strip().lower()
    d = domain_of(email) if "@" in email else ""
    named = d and d not in FREE_MAIL_DOMAINS and d not in OWN_DOMAINS
    if named and ix.is_supplier_domain(d):
        return PlannedAction(f.id, "R8", NONE, (f"reenvío desde un dominio de proveedor ({d}): no se abre solicitud",))
    if named and d in ix.ai_supplier_domains:
        return PlannedAction(f.id, "R8", NONE, (
            f"la lectura (IA) de otros correos de {d} es una oferta de proveedor: no se abre solicitud",))
    who = f.customer_name or email or "cliente sin nombre"
    open_cases = [c for c in ix.cases.values() if not c.closed]
    by_person = [c for c in open_cases if email and email in c.people]
    domain_orgs = [o for o in ix.by_domain.get(d, []) if not o.is_supplier] if named else []
    by_institution = [c for c in open_cases if c.organization_id in {o.id for o in domain_orgs}]
    joined = by_person if len(by_person) == 1 else by_institution if not by_person and len(by_institution) == 1 else []
    if joined:
        c = joined[0]
        why = ("el cliente ya escribe en este caso" if by_person else "caso abierto de la institución del cliente")
        reasons = [f"solicitud reenviada de {who}", why]
        return PlannedAction(f.id, "R11", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                             organization_id=c.organization_id, commands=({"command": ATTACH_FORWARD, "inputs": {
                                 "opportunity_id": c.id, "forwarded_request_id": f.id,
                                 "note": _note("R11", reasons)}},))
    inputs: dict[str, Any] = {"forwarded_request_id": f.id}
    organization: Organization | None = domain_orgs[0] if len(domain_orgs) == 1 else None
    if organization is not None:
        inputs |= {"organization_id": organization.id, "organization_version": organization.version}
        where = f"institución conocida «{organization.name}»"
    elif f.company:
        inputs["new_organization_name"] = f.company
        where = f"institución «{f.company}» por confirmar"
    else:
        where = "sin institución hasta confirmarla"
    reasons = [f"solicitud reenviada de {who}" + (f": {f.requested_item}" if f.requested_item else ""), where]
    inputs["note"] = _note("R11", reasons)
    title = f"Solicitud — {f.requested_item}" if f.requested_item else "Solicitud reenviada"
    return PlannedAction(f.id, "R11", AUTO, tuple(reasons), case_title=title[:200],
                         organization_id=organization.id if organization else None,
                         organization_name=organization.name if organization else f.company,
                         commands=({"command": CREATE_REQUEST_FROM_FORWARD_STEP, "inputs": inputs},))


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
        signal = _signal_rules(e, linked if len(linked) == 1 else [], ix, already_linked=True)
        return signal if signal is not None else _late_quote_rule(e, ix)

    if supplier_domains and not thread_cases:
        # A supplier writing about a client's deal belongs on the client's case (owner, 2026-10-10)
        # — only when the context matcher is sure.
        placed = _context_rule(e, ix)
        if placed is not None:
            return placed
        return PlannedAction(e.id, "R8", NONE, (
            f"dominio de proveedor ({', '.join(supplier_domains)}): no se crea institución ni caso",))
    if not thread_cases and ix.answers_our_rfq(e, domains):
        return PlannedAction(e.id, "R8", NONE, (
            "proveedor al que OrigenLab pidió precio (sondeo de compra): no se abre caso de cliente",))

    signal = _signal_rules(e, thread_cases, ix, already_linked=False)
    if signal is not None:
        return signal

    keyed: dict[tuple[int, int], str] = {}
    for doc in e.documents:
        for token in doc.cn_tokens:
            key = gmail_quote_key(token, e.sent_at)
            if key is not None:
                keyed.setdefault(key, token)
    if not keyed and not thread_cases and not _po_subject(e):
        # No PDF names a quote and the thread is not already a case's: the subject may name one
        # («Consulta cotización N°01259-26»). A PDF's number always outranks the subject's («Re:
        # Cotización 01198-26» carrying CN01240 sends 01240); a thread R1 already knows keeps its
        # link whatever its inherited title says; a purchase order's subject is R5's, not R2's.
        keyed.update(subject_quote_keys(e.subject, e.sent_at))
    from_body = False
    if not keyed and not thread_cases and e.body_quote_tokens:
        # Nothing in a PDF or the subject: the sender's own text may name the quote («Transferencia
        # por cotización N°09259-26»). Lowest rank, and only for a thread no case holds yet.
        keyed.update(token_quote_keys(e.body_quote_tokens, e.sent_at))
        from_body = bool(keyed)
    exact: dict[str, CaseState] = {}
    matched_key: tuple[int, int] | None = None
    for key in keyed:
        for c in ix.by_quote_key.get(key, []):
            exact[c.id] = c
            matched_key = key

    if thread_cases:
        thread_ids = {c.id for c in thread_cases}
        if exact and not set(exact) <= thread_ids:
            # The thread says one case, the quote number another: never guess between them.
            return PlannedAction(e.id, "R1", PROPOSAL, (
                "el hilo de Gmail apunta a un caso y el número de cotización del adjunto a otro: un humano elige",),
                quote_number=canonical_quote_number(matched_key) if matched_key else None,
                candidates=tuple(sorted(thread_ids | set(exact))))
        # A sent quote on an already-known customer thread used to be swallowed by R1 as a mere
        # link. Treat that strong outbound signal as part of R1, but only for one open case whose
        # requesting institution is already resolved; free-mail must never invent an institution.
        # Numbers already on that case (`exact`) are only linked; the rule records the new ones.
        if len(thread_cases) == 1:
            sent_quote = _thread_sent_quote_rule(e, ix, thread_cases[0])
            if sent_quote is not None:
                return sent_quote
        return _link_rule(e, "R1", thread_cases, "mismo hilo de Gmail que un correo ya vinculado", None)
    if exact and matched_key is not None:
        number = canonical_quote_number(matched_key)
        where = " (nombrado en el texto del correo)" if from_body else ""
        return _link_rule(e, "R2", list(exact.values()), f"el número {number}{where} ya es una cotización del caso",
                          number, keep_won=True)
    for key in keyed:
        yearless = ix.by_yearless_key.get(key, [])
        if yearless:
            numbers = ", ".join(sorted({q.quote_number for _, q in yearless}))
            return PlannedAction(e.id, "R2", PROPOSAL, (
                f"el número {canonical_quote_number(key)} coincide con {numbers} del CRM, que no lleva año "
                "(se leyó el año de su envío): un humano confirma",),
                quote_number=canonical_quote_number(key),
                candidates=tuple(sorted({c.id for c, _ in yearless})))

    # Web-form self-sent message: the public form POSTed to contacto@, which sent itself the
    # submission. The sender domain is own, the x-origenlab-origin header identifies the source,
    # and the Reply-To carries the real enquirer. Route as an inbound request from that address.
    # Same predicate as the triage worker (triage_rules.py → WEB_QUOTE_ORIGIN).
    sender_addr = _sender(e) or ""
    if (domain_of(sender_addr) in OWN_DOMAINS
            and e.extra_headers.get("x-origenlab-origin") == WEB_QUOTE_ORIGIN
            and e.reply_to is not None):
        rt_domain = domain_of(e.reply_to)
        e_as_inbound = replace(e, direction="inbound", sender=e.reply_to,
                               model_intent="quote_request", requester_checked=True)
        return _inbound_rules(e_as_inbound, ix, [rt_domain] if rt_domain else [])

    if e.direction == "outbound":
        return _creation_rules(e, ix, domains)
    # Before a new request opens: the context matcher may know the case already (R12).
    placed = _context_rule(e, ix)
    if placed is not None:
        return placed
    return _inbound_rules(e, ix, domains)


_SIGNAL_ES = {"pdf_quote": "número de cotización del PDF", "thread_pdf_quote": "cotización del hilo",
              "text_quote": "número de cotización en el texto", "same_thread": "mismo hilo",
              "domain": "dominio de la institución", "institution_name": "nombre de la institución",
              "product": "producto del caso", "sender_on_case": "la persona ya escribe en el caso"}


def _context_rule(e: MailEvidence, ix: _Index) -> PlannedAction | None:
    """R12: the context matcher is sure — layer 1 «exact», or the AI's pick with confidence «high».
    The email joins that case (open, or won and kept closed). Anything less stays a suggestion."""
    m = e.case_match or {}
    if e.direction != "inbound" or not m:
        return None
    pick = m.get("pick") or {}
    candidates = m.get("candidates") or []
    case_id: str | None = None
    reasons: list[str] = []
    if pick.get("confidence") == "high" and pick.get("choice") in ix.cases:
        case_id = str(pick["choice"])
        reasons = [f"la IA lo reconoce como del caso: {pick.get('reason_es') or 'sin detalle'}"]
    elif m.get("decision") == "exact" and len(candidates) >= 1 and candidates[0].get("opportunity_id") in ix.cases:
        case_id = str(candidates[0]["opportunity_id"])
        signals = ", ".join(_SIGNAL_ES.get(x, x) for x in candidates[0].get("signals") or [])
        reasons = [f"el contexto del correo coincide con el caso ({signals})"]
    if case_id is None:
        return None
    c = ix.cases[case_id]
    if c.closed and c.stage != "won":
        return None  # a lost or abandoned case: a client writing again stays visible
    note = _note("R12", reasons)
    if c.closed:
        reasons.append("el caso está ganado: se archiva el correo sin reabrirlo")
        step = {"command": LINK_AFTER_CLOSE, "inputs": {"opportunity_id": "@case", "relation": "mentions",
                                                        "source_record_id": e.id, "note": note}}
    else:
        step = _link_step(c, e.id, "mentions", note)
    return PlannedAction(e.id, "R12", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                         organization_id=c.organization_id, commands=(step,),
                         context={"case": c.id, "case_version": c.version})


def _link_rule(e: MailEvidence, rule_id: str, cases: list[CaseState], why: str,
               quote_number: str | None, *, keep_won: bool = False) -> PlannedAction:
    open_cases = [c for c in cases if not c.closed]
    if len(cases) >= 2:
        return PlannedAction(e.id, rule_id, PROPOSAL, (
            f"{why}, pero {len(cases)} casos coinciden: un humano elige",),
            quote_number=quote_number, candidates=tuple(sorted(c.id for c in cases)))
    if not open_cases and keep_won and cases[0].stage == "won":
        # A won case's later mail (payment, order, delivery) belongs to it; it is filed, the case
        # stays closed. A lost or abandoned case is not: a client writing again must stay visible.
        c = cases[0]
        reasons = [why, "el caso está ganado: se archiva el correo sin reabrirlo"]
        note = _note(rule_id, reasons)
        return PlannedAction(e.id, rule_id, AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                             organization_id=c.organization_id, quote_number=quote_number,
                             commands=({"command": LINK_AFTER_CLOSE, "inputs": {
                                 "opportunity_id": "@case", "relation": "mentions",
                                 "source_record_id": e.id, "note": note}},),
                             context={"case": c.id, "case_version": c.version})
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


def _request_case_title(e: MailEvidence, organization: Organization | None = None) -> str:
    if organization is not None:
        return f"{organization.name} — solicitud de cotización"
    subject = " ".join((e.subject or "").split())
    web_form = e.extra_headers.get("x-origenlab-origin") == WEB_QUOTE_ORIGIN and e.reply_to is not None
    if web_form and subject.casefold().startswith("solicitud de cotización"):
        # The web form's subject already says so («Solicitud de cotización — sitio web —
        # <Institución>»): never «Solicitud de cotización — Solicitud de cotización …». Other
        # cases keep their titles.
        return subject[:160]
    if subject:
        return f"Solicitud de cotización — {subject[:120]}"
    return "Solicitud de cotización"


def _is_reply(e: MailEvidence) -> bool:
    return bool(re.match(r"^(?:re|rv|fw|fwd|aw|wg|sv|回复|回覆|答复|转发)\s*[:：]", fold(e.subject)))


def _is_quote_request(e: MailEvidence) -> bool:
    subject = fold(e.subject)
    # A reply/forward with «cotización» is much more likely to belong to an existing case whose
    # thread link is missing; never open a second case from that weak signal.
    if _is_reply(e):
        return False
    # «solicitud» by itself is too broad to create a commercial case automatically.
    strong = tuple(w for w in QUOTE_REQUEST_WORDS if w != "solicitud")
    return any(w in subject for w in strong)


def _thread_sent_quote_rule(e: MailEvidence, ix: _Index, c: CaseState) -> PlannedAction | None:
    """R1 upgrade: contacto@ sent one new CN PDF on a thread already belonging to one case.

    This is stronger evidence than a recipient domain (and works for Gmail/Hotmail customers).
    The case requesting institution must already be resolved before the rule may advance the
    case or record a quote. With no requester, return a proposal instead of fabricating identity.
    """
    sender = _sender(e)
    if e.direction != "outbound" or sender != CONTACT_SENDER or (e.direction_hint or "") in NOT_CUSTOMER_HINTS:
        return None
    if c.closed:
        return None

    pdf_keys: dict[tuple[int, int], tuple[str, MailDocument]] = {}
    for doc in sorted(e.documents, key=lambda d: (d.filename or "", d.sha256 or "")):
        if not _is_pdf(doc):
            continue
        for token in doc.cn_tokens:
            key = gmail_quote_key(token, e.sent_at)
            if key is not None:
                pdf_keys.setdefault(key, (token, doc))
    # Numbers the CRM already holds (or this pass already claimed) are not new: only the rest count.
    pdf_keys = {k: v for k, v in pdf_keys.items() if k not in ix.by_quote_key and k not in ix.claimed_keys}
    if not pdf_keys:
        return None
    if len(pdf_keys) >= 2:
        return _thread_sent_quotes(e, ix, c, pdf_keys)
    key, (token, doc) = next(iter(pdf_keys.items()))
    number = canonical_quote_number(key)
    if key in ix.by_quote_key or key in ix.claimed_keys:
        return None
    elsewhere = [n for n, y in ix.by_correlative.get(key[1], []) if y != key[0]]
    if elsewhere:
        return PlannedAction(e.id, "R1", PROPOSAL, (
            f"el hilo ya pertenece al caso, pero el correlativo de {number} existe como "
            f"{', '.join(sorted(elsewhere))}: un humano decide",),
            case_id=c.id, case_title=c.title, organization_id=c.organization_id, quote_number=number)
    if c.organization_id is None:
        return PlannedAction(e.id, "R1", PROPOSAL, (
            f"se envió la cotización {number} en el hilo del caso, pero falta confirmar la institución solicitante",
            "se vinculará y registrará como «Enviada» cuando una persona nombre la institución",
        ), case_id=c.id, case_title=c.title, quote_number=number)
    if c.stage not in ("lead", "qualifying", "qualified", "quoting", "negotiating"):
        return PlannedAction(e.id, "R1", PROPOSAL, (
            f"se envió la cotización {number}, pero el caso está en {c.stage}: un humano decide",),
            case_id=c.id, case_title=c.title, organization_id=c.organization_id, quote_number=number)

    derivation = _derivation(token, e.sent_at)
    reasons = [
        "mismo hilo de Gmail que el caso",
        f"cotización nueva {number} enviada por contacto@ (PDF «{doc.filename}»)",
    ]
    steps = _sent_quote_steps(c, e, doc, number, token, derivation, _note("R1", reasons))
    ix.claimed_keys[key] = e.id
    return PlannedAction(
        e.id, "R1", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
        organization_id=c.organization_id, quote_number=number, commands=tuple(steps),
        context={"case": c.id, "case_version": c.version, "from_stage": c.stage},
    )

def _thread_sent_quotes(e: MailEvidence, ix: _Index, c: CaseState,
                        pdf_keys: dict[tuple[int, int], tuple[str, MailDocument]]) -> PlannedAction:
    """R1 with several new CN PDFs in one email on the case's thread: alternatives for the same
    request (owner 2026-10-16). Each is recorded as its own sent quote on that case. Anything that
    would make one of them doubtful — its correlative under another year, no requester, a stage
    that is not quoting — leaves the whole email to a person, as for a single number."""
    keys = sorted(pdf_keys)
    numbers = [canonical_quote_number(k) for k in keys]
    listed = ", ".join(numbers)

    def ask(reason: str) -> PlannedAction:
        return PlannedAction(e.id, "R1", PROPOSAL, (reason,), case_id=c.id, case_title=c.title,
                             organization_id=c.organization_id, quote_number=numbers[0])

    for key, number in zip(keys, numbers):
        elsewhere = [n for n, y in ix.by_correlative.get(key[1], []) if y != key[0]]
        if elsewhere:
            return ask(f"el hilo ya pertenece al caso, pero el correlativo de {number} existe como "
                       f"{', '.join(sorted(elsewhere))}: un humano decide")
    if c.organization_id is None:
        return ask(f"se enviaron las cotizaciones {listed} en el hilo del caso, pero falta confirmar "
                   "la institución solicitante")
    if c.stage not in ("lead", "qualifying", "qualified", "quoting", "negotiating"):
        return ask(f"se enviaron las cotizaciones {listed}, pero el caso está en {c.stage}: un humano decide")

    reasons = ["mismo hilo de Gmail que el caso",
               f"cotizaciones nuevas {listed} enviadas juntas por contacto@ (alternativas del mismo pedido)"]
    note = _note("R1", reasons)
    first_token, first_doc = pdf_keys[keys[0]]
    steps = list(_sent_quote_steps(c, e, first_doc, numbers[0], first_token,
                                   _derivation(first_token, e.sent_at), note))
    steps[-1]["inputs"]["ledger_decision_id"] = f"mail-rule:{e.id}:{numbers[0]}"
    for key, number in zip(keys[1:], numbers[1:]):
        token, doc = pdf_keys[key]
        steps.append(_record_sent_quote_step(e, doc, number, token, _derivation(token, e.sent_at), note,
                                             ledger_decision_id=f"mail-rule:{e.id}:{number}"))
    for key in keys:
        ix.claimed_keys[key] = e.id
    return PlannedAction(
        e.id, "R1", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
        organization_id=c.organization_id, quote_number=numbers[0], commands=tuple(steps),
        context={"case": c.id, "case_version": c.version, "from_stage": c.stage},
    )


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
    request = _open_request_rule(e, ix, doc, token, key, number)
    if request is not None:
        return request
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
    if not known:
        named_in_file = _quote_file_rule(e, ix, doc, token, key, number, derivation, base_reasons)
        if named_in_file is not None:
            return named_in_file
    if free:
        return PlannedAction(e.id, "R8", NONE, (
            f"cotización {number} enviada a correo gratuito ({', '.join(free)}): no se crea institución",),
            quote_number=number)
    return PlannedAction(e.id, "R8", NONE, ("correo saliente sin destinatario externo",), quote_number=number)


#: Stages of a request still waiting for its first quote (R13).
_REQUEST_STAGES = ("lead", "qualifying", "qualified")


def _open_request_rule(e: MailEvidence, ix: _Index, doc: MailDocument, token: str, key: tuple[int, int],
                       number: str) -> PlannedAction | None:
    """R13 (owner, 2026-10-15): a quote sent on a NEW thread answers the client's open request —
    the case a person or the rules opened when they asked (R7, R11) and that has no quote yet. It is
    that request when a recipient wrote on it (or asked through a forward), when a recipient's domain
    is its institution's, or when the PDF's name names its institution. Exactly one → the email is
    filed there, the case walks to «Enviada» and the quote is recorded; two or more → a proposal; none
    → the creation rules (R3/R4/R9/R10) go on as before. Never a request without an institution:
    recording a quote needs the requester (as R1)."""
    recipients = {a.lower() for a in addresses(e.recipients)}
    domains = {domain_of(a) for a in recipients} - FREE_MAIL_DOMAINS - OWN_DOMAINS
    named_orgs: set[str] = set()
    name = quote_file_institution(doc.filename)
    if name:
        for part in (name, *quote_file_segments(name)):
            for k in institution_keys(part):
                named_orgs.update(o.id for o in ix.by_name.get(k, []))
    matches: dict[str, tuple[CaseState, str]] = {}
    for c in ix.cases.values():
        if c.closed or c.quotes or c.stage not in _REQUEST_STAGES or c.organization_id is None:
            continue
        org = ix.orgs.get(c.organization_id)
        if org is None or org.is_supplier:
            continue
        if recipients & set(c.people):
            matches[c.id] = (c, "el destinatario es quien pidió la cotización")
        elif domains & {d.lower() for d in org.domains}:
            matches[c.id] = (c, f"el destinatario es de «{org.name}»")
        elif org.id in named_orgs:
            matches[c.id] = (c, f"el PDF nombra «{org.name}»")
    if not matches:
        return None
    if len(matches) >= 2:
        return PlannedAction(e.id, "R13", PROPOSAL, (
            f"cotización {number}: {len(matches)} solicitudes abiertas de este cliente sin cotización: un humano elige",),
            quote_number=number, candidates=tuple(sorted(matches)))
    c, why = next(iter(matches.values()))
    reasons = [f"cotización nueva {number} (PDF «{doc.filename}»)", why,
               f"responde la solicitud abierta «{c.title}», que aún no tenía cotización"]
    ix.claimed_keys[key] = e.id
    return PlannedAction(
        e.id, "R13", AUTO, tuple(reasons), case_id=c.id, case_title=c.title, organization_id=c.organization_id,
        quote_number=number,
        commands=_sent_quote_steps(c, e, doc, number, token, _derivation(token, e.sent_at), _note("R13", reasons)),
        context={"case": c.id, "case_version": c.version, "from_stage": c.stage},
    )


def _late_quote_rule(e: MailEvidence, ix: _Index) -> PlannedAction | None:
    """R14 (owner, 2026-10-19): contacto@ sent a quote on an email that is already on exactly one open
    case — a rule linked it before it could record quotes, or a person did — and no case records its
    number. The same gates as R1's (`_thread_sent_quote_rule`): the case has its requesting
    institution and an open stage, the correlative is not the CRM's under another year. Then the case
    walks to «Enviada» and the quote is recorded; the email stays linked. Anything doubtful — no
    institution, another year, two new PDFs, a closed case — is left to a person in Operaciones."""
    if e.direction != "outbound" or _sender(e) != CONTACT_SENDER or (e.direction_hint or "") in NOT_CUSTOMER_HINTS:
        return None
    # A backfilled old email on a case still open must never record an old quote or move the stage.
    since, sent = _instant(ix.late_quote_since), _instant(e.sent_at)
    if since is None or sent is None or sent < since:
        return None
    open_cases = [ix.cases[c] for c in e.linked_case_ids if c in ix.cases and not ix.cases[c].closed]
    if len(open_cases) != 1:
        return None
    c = open_cases[0]
    pdf_keys: dict[tuple[int, int], tuple[str, MailDocument]] = {}
    for doc in sorted(e.documents, key=lambda d: (d.filename or "", d.sha256 or "")):
        if not _is_pdf(doc):
            continue
        for token in doc.cn_tokens:
            key = gmail_quote_key(token, e.sent_at)
            if key is not None and key not in ix.by_quote_key and key not in ix.claimed_keys:
                pdf_keys.setdefault(key, (token, doc))
    if len(pdf_keys) != 1:
        return None
    key, (token, doc) = next(iter(pdf_keys.items()))
    if any(y != key[0] for _, y in ix.by_correlative.get(key[1], [])):
        return None
    if c.organization_id is None or c.stage not in _TO_QUOTING:
        return None
    number = canonical_quote_number(key)
    reasons = ["el correo ya estaba en el caso",
               f"cotización {number} enviada por contacto@ (PDF «{doc.filename}») y aún sin registrar"]
    note = _note("R14", reasons)
    ix.claimed_keys[key] = e.id
    steps = (*(_advance_step(stage, note) for stage in _TO_QUOTING[c.stage]),
             _record_sent_quote_step(e, doc, number, token, _derivation(token, e.sent_at), note,
                                     ledger_decision_id=f"mail-rule:{e.id}:R14"))
    return PlannedAction(e.id, "R14", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                         organization_id=c.organization_id, quote_number=number, commands=steps,
                         context={"case": c.id, "case_version": c.version, "from_stage": c.stage})


#: R16's step (`quote_sent.record_authored_quote_sent`).
RECORD_AUTHORED_QUOTE_SENT = "record_authored_quote_sent"
#: A quote number in a PDF's file name with its revision letter («CN01274» is revision 1, «CN01274A»
#: revision 2: `numbering.cn_label`), read on the folded name (`fold`). The Cotizador's print view
#: titles the page «Cotización CN01274A — Institución» (`QuotePrintView.printTitle`, `quoteLabel`), so
#: that is the name «Guardar como PDF» offers; a hand-renamed «CN01274A-….pdf» reads the same. The
#: capture's `cn_tokens` keep the digits only, so R16 reads the letter here. A letter followed by more
#: letters («CN01275Ygaero») is not a revision letter and names nothing.
_CN_REVISION = re.compile(r"(?<![a-z0-9])cn[\s_-]?(\d{3,6})([a-z]?)(?![a-z0-9])")
#: The print view's title of a draft: that PDF is not the approved document.
_DRAFT_MARK = "borrador"


def named_revisions(filename: str | None, sent_at: str | None) -> set[tuple[tuple[int, int], int]]:
    """((year, correlative), revision number) for each quote a PDF's file name names, the year being
    the email's (the name carries none); none for a draft's print. R16 plans with it and `quote_sent`
    re-checks the stored email with it."""
    name = fold(filename)
    if _DRAFT_MARK in name:
        return set()
    out: set[tuple[tuple[int, int], int]] = set()
    for digits, letter in _CN_REVISION.findall(name):
        key = gmail_quote_key(f"CN{int(digits):05d}", sent_at)
        if key is not None:
            out.add((key, 1 if not letter else ord(letter.upper()) - ord("A") + 2))
    return out


def year_before(key: tuple[int, int]) -> tuple[int, int]:
    """The name carries no year, so the email's is assumed: a quote approved in December and emailed in
    January reads a year late. R16 also tries the year before when the email's names nothing — an
    exact number, revision letter and approved Cotizador revision bound the guess."""
    return ((key[0] - 1) % 100, key[1])


def _authored_sent_rule(e: MailEvidence, ix: _Index) -> PlannedAction | None:
    """R16 (owner, 2026-10-21): contacto@ emailed the PDF of a quote the Cotizador approved on the case
    the email is on. The revision becomes «sent» and the case «Enviada». Nothing is read from the PDF —
    the Cotizador holds what it says; the email proves it went out.

    Every PDF must name exactly one quote; each one naming a revision of this case that is the
    Cotizador's (`origin = 'authored'`), approved, not superseded, not yet sent and approved before the
    email left counts — on each case they belong to (a client's request and the case the Cotizador
    opened beside it), the email linked there if it is not. A closed case among them, a stage past
    «Enviada», an email a person put on another case, or an open case of its thread none of the PDFs
    names leave the email to a person. Historical quotes are R1/R14's, never this rule's."""
    if e.direction != "outbound" or _sender(e) != CONTACT_SENDER or (e.direction_hint or "") in NOT_CUSTOMER_HINTS:
        return None
    sent = _instant(e.sent_at)
    if sent is None:
        return None
    found: dict[str, tuple[CaseState, CaseQuote, QuoteRevision, MailDocument]] = {}
    for doc in sorted(e.documents, key=lambda d: (d.filename or "", d.sha256 or "")):
        named = named_revisions(doc.filename, e.sent_at)
        if not _is_pdf(doc) or not doc.sha256 or len(named) != 1:
            continue
        [(named_key, revision_no)] = named
        keys = [named_key, year_before(named_key)]
        for key in keys:
            cases = ix.by_quote_key.get(key, [])
            if len(cases) != 1:
                continue
            c = cases[0]
            matched = [(q, r) for q in c.quotes if quote_key(q.quote_number, None) == key for r in q.revisions
                       if r.revision_no == revision_no and r.origin == "authored" and r.status == "approved"
                       and not r.superseded and r.sent_at is None
                       and (approved := _instant(r.approved_at)) is not None and approved <= sent]
            for q, r in matched:
                found.setdefault(r.id, (c, q, r, doc))
            if matched:
                break  # the email's year first; the year before only when it names nothing
    if not found:
        return None
    cases = {c.id: c for c, _, _, _ in found.values()}
    if any(c.closed or c.stage not in _TO_QUOTING for c in cases.values()):
        return None
    if set(e.linked_case_ids) - set(cases):
        return None  # a person or a rule put it on a case none of its PDFs names
    # A reply on an old thread is fine when that thread's case is closed (a client asking again on a
    # lost quote's thread); an open case of the thread that none of the PDFs names is a person's call.
    if any(not t.closed and t.id not in cases for t in ix.by_thread.get(e.thread_id or "", [])):
        return None
    # Each PDF is its own case's evidence: two cases of one client (a request and the case «Nueva
    # cotización» opened beside it) each get their revision sent, the email and «Enviada». The case
    # the thread or a link already names goes last, so the action's case is that one.
    known = set(e.linked_case_ids) | {t.id for t in ix.by_thread.get(e.thread_id or "", [])}
    order = sorted(cases.values(), key=lambda c: (c.id in known, c.title, c.id))
    from_cotizador = sorted(found.values(), key=lambda f: (f[1].quote_number, f[2].revision_no))
    labels = [_revision_label(q.quote_number, r.revision_no) for _, q, r, _ in from_cotizador]
    reasons = [f"contacto@ envió el PDF de {'las cotizaciones' if len(labels) > 1 else 'la cotización'} "
               f"{', '.join(labels)}, aprobada{'s' if len(labels) > 1 else ''} en el Cotizador"]
    if len(order) > 1:
        reasons.append(f"son de {len(order)} casos del cliente: cada uno queda en «Enviada»")
    note = _note("R16", reasons)
    steps: list[dict[str, Any]] = []
    for c in order:
        # `record_authored_quote_sent` answers its case and version, so the steps after it act there.
        steps += [{"command": RECORD_AUTHORED_QUOTE_SENT, "inputs": {
            "quote_revision_id": r.id, "quote_revision_version": r.version, "source_record_id": e.id,
            "document_sha256": doc.sha256, "note": note}} for cc, _, r, doc in from_cotizador if cc.id == c.id]
        if c.id not in e.linked_case_ids:
            # Not on the case yet: R2 leaves an email naming two cases to a person, and R1 a thread
            # whose case is another (production 2026-10-19/20: Ygaero, Golden Omega); the number and
            # revision letter of an approved quote do not.
            steps.append(_link_step(c, e.id, "mentions", note))
        steps += [_advance_step(stage, note) for stage in _TO_QUOTING[c.stage]]
    first, main = order[0], order[-1]
    return PlannedAction(e.id, "R16", AUTO, tuple(reasons), case_id=main.id, case_title=main.title,
                         organization_id=main.organization_id, quote_number=labels[0], commands=tuple(steps),
                         candidates=tuple(c.id for c in order) if len(order) > 1 else (),
                         context={"case": first.id, "case_version": first.version, "from_stage": first.stage})


def _client_reply_rule(e: MailEvidence, ix: _Index) -> PlannedAction | None:
    """R17 (owner, 2026-10-26): the client answered on a case after its quote went out — «estamos
    evaluando la oferta» — so the case is in conversation, not waiting in «Enviada».

    Only an email already on exactly one open case in «Enviada» (R1, R12 or a person put it there),
    sent after that case's live quote and in the last `REPLY_WINDOW_DAYS`. Never our own mail, a
    supplier's, or an automatic reply, and never a case put «En pausa»: a reply like «se evaluará el
    año entrante» is why it was paused. «Quitar» moves the case back to «Enviada»."""
    if e.direction != "inbound" or len(e.linked_case_ids) != 1:
        return None
    at, since = _instant(e.sent_at), _instant(ix.reply_since)
    if at is None or since is None or at < since:
        return None
    c = ix.cases.get(e.linked_case_ids[0])
    if c is None or c.closed or c.stage != "quoting" or c.paused_until is not None:
        return None
    sender = _sender(e)
    if sender is None or domain_of(sender) in OWN_DOMAINS or ix.is_supplier_domain(domain_of(sender)):
        return None
    subject = fold(e.subject)
    if any(p in subject for p in AUTO_REPLY_PHRASES):
        return None
    answered = [(sent, q, r) for q in c.quotes for r in _live_revisions(q)
                if (sent := _instant(r.sent_at)) is not None and sent < at]
    if not answered:
        return None
    _, q, r = max(answered, key=lambda x: x[0])
    label = _revision_label(q.quote_number, r.revision_no)
    reasons = [f"el cliente respondió en el caso después de recibir la cotización {label}"]
    note = _note("R17", reasons)
    return PlannedAction(e.id, "R17", AUTO, tuple(reasons), case_id=c.id, case_title=c.title,
                         organization_id=c.organization_id, quote_number=label,
                         commands=(_advance_step("negotiating", note),),
                         context={"case": c.id, "case_version": c.version, "from_stage": c.stage})


def _revision_label(quote_number: str, revision_no: int) -> str:
    """«01274-26», then «01274A-26» for revision 2 (`numbering.revision_label`, kept pure here)."""
    if revision_no <= 1:
        return quote_number
    head, sep, year = quote_number.partition("-")
    return f"{head}{chr(ord('A') + revision_no - 2)}{sep}{year}"


#: R14: how far back a sent email may be (the same window as Operaciones' «sin registrar» list).
LATE_QUOTE_WINDOW_DAYS = 60
#: R15: a quote must have stood this long, not undone, before its institution is confirmed.
SETTLE_DAYS = 3
#: What may have created an institution R15 confirms: the mail rules R4 and R10, and «Completar con
#: la cotización» (`complete_case_from_sent_quote`). Never R11 (a forward's company), a person, an import.
MACHINE_CREATORS: frozenset[str] = frozenset({"R4", "R10", "complete_case"})
_DOMAIN_LIKE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
UPDATE_ORGANIZATION = "update-organization"
CONFIRM_ORGANIZATION_RECORD = "confirm-organization-record"
UNCONFIRM_ORGANIZATION_RECORD = "unconfirm-organization-record"


_NAME_STOPWORDS = frozenset({"de", "del", "la", "las", "el", "los", "y", "e", "en"})


def _names_an_institution(segment: str) -> bool:
    """R15 renames unattended, so a segment must say *which* institution, not only what kind:
    at least two meaningful words (no 1-letter crumbs, articles or the parenthetical), one of them
    not a kind/legal-form marker. «Laboratorio», «Laboratorio Chile», «Centro (CEN)» → False;
    «Acme Ejemplo SpA», «Instituto Ficticio (IFI)» → True."""
    words = [w for w in re.findall(r"[a-z0-9]+", fold(_PARENTHETICAL.sub(" ", segment)))
             if len(w) >= 2 and w not in _NAME_STOPWORDS]
    return len(words) >= 2 and any(not _INSTITUTION_MARKER.fullmatch(w) for w in words)


def _confirm_institution_rule(f: InstitutionFacts, ix: _Index) -> PlannedAction | None:
    """R15 (owner, 2026-10-19): an institution a machine created «por confirmar» is confirmed once a
    quote to it has stood `SETTLE_DAYS` days without being undone. It must not be a supplier, nor a
    free-mail domain. One named after its domain (R4: «nuevo-cliente.example») is first renamed to the
    institution its quote PDFs name (`institution_segment`) — only when exactly one distinct segment
    exists across them, and no other institution already has that name; otherwise a person decides."""
    o = ix.orgs.get(f.organization_id)
    if o is None or o.confirmation != "machine_proposed" or f.created_by not in MACHINE_CREATORS:
        return None
    if o.is_supplier:
        return None
    name = fold(o.name)
    domains = {d.lower() for d in o.domains}
    if domains & FREE_MAIL_DOMAINS or name in FREE_MAIL_DOMAINS or not f.settled_quote_files:
        return None
    rename: str | None = None
    if name in domains or _DOMAIN_LIKE.match(name):
        segments: dict[str, str] = {}
        for filename in f.settled_quote_files:
            segment = institution_segment(quote_file_institution(filename))
            if segment:
                segments.setdefault(fold(segment), segment)
        if len(segments) != 1:
            return None
        rename = next(iter(segments.values()))
        if not _names_an_institution(rename):
            return None
        if any(other.id != o.id for k in institution_keys(rename) for other in ix.by_name.get(k, [])):
            return None
    count = len(f.settled_quote_files)
    reasons = [(f"{count} cotizaciones enviadas" if count != 1 else "1 cotización enviada")
               + f" hace {SETTLE_DAYS} días o más, sin deshacer",
               "creada por el sistema desde el correo; no es proveedor ni correo gratuito"]
    if rename is not None:
        reasons.append(f"se llamaba como su dominio («{o.name}»): toma el nombre que da el PDF, «{rename}»")
    note = _note("R15", reasons)
    steps: list[dict[str, Any]] = []
    if rename is not None:
        steps.append({"command": UPDATE_ORGANIZATION, "inputs": {
            "organization_id": o.id, "expected_version": "@organization_version", "name": rename, "note": note}})
    steps.append({"command": CONFIRM_ORGANIZATION_RECORD, "inputs": {
        "organization_id": o.id, "expected_version": "@organization_version", "note": note}})
    context: dict[str, Any] = {"organization": o.id, "organization_version": o.version}
    if rename is not None:
        context["previous_name"] = o.name
    return PlannedAction(o.id, "R15", AUTO, tuple(reasons), organization_id=o.id,
                         organization_name=rename or o.name, commands=tuple(steps), context=context)


def _quote_file_rule(e: MailEvidence, ix: _Index, doc: MailDocument, token: str, key: tuple[int, int],
                     number: str, derivation: dict[str, Any], base_reasons: list[str]) -> PlannedAction | None:
    """R9: no recipient names an institution, but the PDF's own name does.

    The Cotizador names quotes `CN<number>-<institución>.pdf` or `CN<number>-<persona> -
    <institución>.pdf`, so a customer writing from gmail.com still names who the quote is for.
    The whole name and each dash-separated segment are matched against the CRM (whole name,
    name without parenthetical, «(ACRONYM)»); a supplier never matches. One open
    case of that one institution is recorded like R1 does on a thread; anything else is a proposal
    naming the candidates, never a guess. A name the CRM does not hold is R10: an institution
    «por confirmar» with the case and quote, applied only by a person («Aplicar»), like R4.
    """
    name = quote_file_institution(doc.filename)
    if name is None:
        return None
    found: dict[str, Organization] = {}
    for part in (name, *quote_file_segments(name)):
        for k in institution_keys(part):
            for o in ix.by_name.get(k, []):
                found[o.id] = o
    if any(o.is_supplier for o in found.values()):
        return None
    if not found:
        institution = institution_segment(name)
        if institution is None:
            return None
        return _new_institution_from_file_rule(e, ix, doc, token, key, number, derivation, base_reasons,
                                               institution)
    if len(found) >= 2:
        return PlannedAction(e.id, "R9", PROPOSAL, (
            *base_reasons, f"el PDF nombra «{name}», que coincide con {len(found)} instituciones: un humano elige",),
            quote_number=number, candidates=tuple(sorted(found)))
    o = next(iter(found.values()))
    open_cases = sorted((c for c in ix.cases.values() if c.organization_id == o.id and not c.closed),
                        key=lambda c: c.id)
    named = f"el PDF nombra la institución «{o.name}»"
    if not open_cases:
        return PlannedAction(e.id, "R9", PROPOSAL, (
            *base_reasons, named, "no tiene un caso abierto: un humano abre el caso o vincula el correo",),
            organization_id=o.id, organization_name=o.name, quote_number=number)
    if len(open_cases) >= 2:
        return PlannedAction(e.id, "R9", PROPOSAL, (
            *base_reasons, named, f"tiene {len(open_cases)} casos abiertos: un humano elige",),
            organization_id=o.id, organization_name=o.name, quote_number=number,
            candidates=tuple(c.id for c in open_cases))
    c = open_cases[0]
    if c.stage not in _TO_QUOTING:
        return PlannedAction(e.id, "R9", PROPOSAL, (
            *base_reasons, named, f"su único caso abierto está en {c.stage}: un humano decide",),
            case_id=c.id, case_title=c.title, organization_id=o.id, quote_number=number)
    reasons = [*base_reasons, named, f"único caso abierto de esa institución: «{c.title}»"]
    ix.claimed_keys[key] = e.id
    return PlannedAction(
        e.id, "R9", AUTO, tuple(reasons), case_id=c.id, case_title=c.title, organization_id=o.id,
        organization_name=o.name, quote_number=number,
        commands=_sent_quote_steps(c, e, doc, number, token, derivation, _note("R9", reasons)),
        context={"case": c.id, "case_version": c.version, "from_stage": c.stage},
    )


def _new_institution_from_file_rule(e: MailEvidence, ix: _Index, doc: MailDocument, token: str,
                                    key: tuple[int, int], number: str, derivation: dict[str, Any],
                                    base_reasons: list[str], name: str) -> PlannedAction:
    """R10: the PDF names an institution the CRM does not hold. Same steps as R4 — an institution
    «por confirmar» (`machine_proposed`), its case walked to «Enviada», the quote — but named as the
    PDF names it and with no domain (the recipient is free-mail). Never automatic."""
    claim = f"name:{fold(name)}"
    if claim in ix.claimed_domains:
        return PlannedAction(e.id, "R10", PROPOSAL, (
            *base_reasons, f"«{name}» ya crea una institución «por confirmar» con otro correo de esta pasada "
            f"({ix.claimed_domains[claim]}): se decide después",), organization_name=name, quote_number=number)
    reasons = [*base_reasons, f"el PDF nombra «{name}», que no está en el CRM: se crea «por confirmar»"]
    ix.claimed_keys[key] = e.id
    ix.claimed_domains[claim] = e.id
    note = _note("R10", reasons)
    steps = (
        {"command": "register_mail_organization", "inputs": {
            "name": name, "domain": None, "origin_source_record_id": e.id, "note": note}},
        *_case_with_quote_steps(e, doc, number, token, derivation, f"{name} — cotización {number}", note,
                                organization_id="@organization", organization_version="@organization_version"),
    )
    return PlannedAction(e.id, "R10", AUTO, tuple(reasons), case_title=f"{name} — cotización {number}",
                         organization_name=name, quote_number=number, commands=steps)


#: Open stages a sent quote may move to «Enviada» (`quoting`), and the edges it walks there.
_TO_QUOTING: dict[str, tuple[str, ...]] = {
    "lead": ("qualifying", "qualified", "quoting"),
    "qualifying": ("qualified", "quoting"),
    "qualified": ("quoting",),
    "quoting": (),
    "negotiating": (),
}


def _sent_quote_steps(c: CaseState, e: MailEvidence, doc: MailDocument, number: str, token: str,
                      derivation: dict[str, Any], note: str) -> tuple[dict[str, Any], ...]:
    """Link the email to an existing case, walk it to «Enviada», record the sent quote (R1, R9)."""
    return (
        _link_step(c, e.id, "mentions", note),
        *(_advance_step(stage, note) for stage in _TO_QUOTING[c.stage]),
        _record_sent_quote_step(e, doc, number, token, derivation, note),
    )


def _record_sent_quote_step(e: MailEvidence, doc: MailDocument, number: str, token: str,
                            derivation: dict[str, Any], note: str, *,
                            ledger_decision_id: str | None = None) -> dict[str, Any]:
    return {"command": "record_historical_quotation", "inputs": {
        "opportunity_id": "@case", "quote_number": number, "printed_quote_numbers": [token],
        "quote_number_derivation": derivation,
        "document_sha256": doc.sha256, "sent_at": e.sent_at, "origin_source_record_id": e.id,
        "ledger_decision_id": ledger_decision_id or f"mail-rule:{e.id}", "filename": doc.filename, "note": note,
    }}


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
    """R7 opens a real lead for an explicit inbound quote request; identity stays conservative."""
    if not domains:
        return PlannedAction(e.id, "R8", NONE, ("correo interno",))
    d = domains[0]
    if e.model_intent == "supplier_offer":
        return PlannedAction(e.id, "R8", NONE, ("la lectura del correo (IA) es una oferta de un proveedor: no se abre solicitud",))
    by_ai = False
    if not _is_quote_request(e):
        if e.model_intent != "quote_request" or _is_reply(e):
            return PlannedAction(e.id, "R8", NONE, ("ninguna regla aplica: queda en Revisión",))
        by_ai = True
    # A supplier may send its quotation on a new thread with no known supplier domain.
    # A quotation is not itself a customer's request. Preserve it for review rather than
    # opening a lead from this ambiguous keyword alone (Spanish requests/RFQ still qualify).
    subject = fold(e.subject)
    # A fresh Gmail thread can be a follow-up to a sent quotation that R1 has not
    # registered yet (for example, its requesting institution is still unresolved).
    # Absence from crm.quote is not evidence of a new commercial transaction.
    # Do not auto-link, infer identity, or open a second opportunity from this signal.
    numbered_reference = re.search(
        r"(?<![a-z0-9])(?:cn\s*[-#:]?\s*\d{4,6}(?:[-–/]\d{2,4})?"
        r"|0?\d{4}[-–/](?:\d{4}|\d{2})(?!\d)"
        r"|(?:cotizacion|quotation)\s*(?:n[°o]?\.?|no\.?|#)\s*\d{4,6}(?!\d))", subject,
    ) or any(doc.cn_tokens for doc in e.documents)
    if numbered_reference:
        return PlannedAction(e.id, "R7", PROPOSAL, (
            "menciona una cotización numerada: revisar el caso original antes de crear otro; "
            "puede estar enviada pero aún no registrada en el CRM",
        ))
    if "quotation" in subject and not any(w in subject for w in ("request", "rfq", "cotiz", "presupuesto", "precio")):
        return PlannedAction(e.id, "R7", PROPOSAL, (
            "cotización recibida: no demuestra una solicitud del cliente; requiere revisión",))

    domain_orgs = [] if d in FREE_MAIL_DOMAINS else ix.by_domain.get(d, [])
    requester = ix.orgs.get(e.requester_organization_id or "")
    if requester is not None and requester.is_supplier:
        requester = None
    if requester is not None and domain_orgs and requester.id not in {o.id for o in domain_orgs}:
        return PlannedAction(e.id, "R7", PROPOSAL, (
            f"la firma identifica «{requester.name}», pero el dominio {d} pertenece a otra institución: un humano elige",),
            candidates=tuple(sorted({requester.id, *(o.id for o in domain_orgs)})))
    orgs = [requester] if requester is not None else domain_orgs
    if len(orgs) >= 2:
        return PlannedAction(e.id, "R7", PROPOSAL, (
            f"solicitud de cotización desde {d}, dominio de {len(orgs)} instituciones: un humano elige",),
            candidates=tuple(sorted(o.id for o in orgs)))

    if len(orgs) == 1:
        o = orgs[0]
        how = ("institución identificada exactamente en el cuerpo del correo"
               if requester is not None else "remitente de una institución conocida")
        reasons = [f"solicitud de cotización de «{o.name}»", how, *([AI_REQUEST_REASON] if by_ai else [])]
        note = _note("R7", reasons)
        title = _request_case_title(e, o)
        sender_addr = _sender(e) or ""
        commands = (
            {"command": "open_commercial_case", "inputs": {
                "title": title, "origin_source_record_id": e.id, "note": note}},
            # Add the sender as a participant so client_open_cases finds this case. (owner, 2026-10-22)
            {"command": ADD_CASE_SENDER_PARTICIPANT, "inputs": {
                "opportunity_id": "@case", "email": sender_addr, "note": note}},
            {"command": "add_case_organization", "inputs": {
                "opportunity_id": "@case", "opportunity_version": "@case_version",
                "organization_id": o.id, "organization_version": o.version,
                "role": "requesting_institution", "note": note}},
            _advance_step("qualifying", note),
            _advance_step("qualified", note),
        )
        return PlannedAction(
            e.id, "R7", AUTO, tuple(reasons), case_title=title, organization_id=o.id,
            organization_name=o.name, commands=commands,
        )

    if not e.requester_checked:
        return PlannedAction(e.id, "R7", PROPOSAL, (
            "solicitud de cotización: esperando la comprobación determinística de institución en el cuerpo",
        ))
    reason = (f"solicitud de cotización desde correo gratuito ({d})"
              if d in FREE_MAIL_DOMAINS else f"solicitud de cotización desde dominio no reconocido ({d})")
    extra = [AI_REQUEST_REASON] if by_ai else []
    note = _note("R7", [reason, *extra, "se abre caso sin inventar institución solicitante"])
    title = _request_case_title(e)
    sender_addr = _sender(e) or ""
    return PlannedAction(
        e.id, "R7", AUTO, (reason, *extra, "caso abierto en «Solicitada» sin institución hasta confirmación"),
        case_title=title,
        commands=(
            {"command": "open_commercial_case", "inputs": {
                "title": title, "origin_source_record_id": e.id, "note": note}},
            # Add the sender as a participant so client_open_cases finds this case. (owner, 2026-10-22)
            {"command": ADD_CASE_SENDER_PARTICIPANT, "inputs": {
                "opportunity_id": "@case", "email": sender_addr, "note": note}},
        ),
    )


__all__ = [
    "AUTO", "CONTACT_SENDER", "FREE_MAIL_DOMAINS", "NOT_CUSTOMER_HINTS", "PURCHASE_ORDER_FILENAME", "LABDELIVERY_MARKERS", "LOST_CLOSE_REASON", "LOST_PHRASES", "NONE",
    "OWN_DOMAINS", "PROPOSAL", "PURCHASE_ORDER_NUMBER", "PURCHASE_ORDER_PHRASES", "QUOTE_REQUEST_WORDS",
    "RULE_IDS", "SYSTEM_LABEL", "WEB_QUOTE_ORIGIN", "ADD_CASE_SENDER_PARTICIPANT", "ATTACH_FORWARD",
    "CONFIRM_ORGANIZATION_RECORD", "InstitutionFacts",
    "LATE_QUOTE_WINDOW_DAYS", "MACHINE_CREATORS", "SETTLE_DAYS", "UNCONFIRM_ORGANIZATION_RECORD", "UPDATE_ORGANIZATION", "CREATE_REQUEST_FROM_FORWARD_STEP", "CaseQuote", "CaseState", "ForwardRequest", "MailDocument", "MailEvidence", "Organization",
    "PlannedAction", "QuoteRevision", "Snapshot", "addresses", "canonical_quote_number", "domain_of", "fold",
    "gmail_quote_key", "mail_direction", "plan", "quote_key", "quote_number_tokens", "santiago_year",
    "token_quote_keys",
]
