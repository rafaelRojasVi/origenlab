"""Context matcher, layer 1: which existing case an email belongs to, from signals a person would use —
the quote number on the PDF, the thread, the institution's domain or name, the product, the people.

Pure: an email's features and the case snapshot in, ranked candidates out. Nothing is linked here: the
result is stored as a `message_triage` assertion (`case_match:v1`) and a person decides. Guardrails
(owner review 2026-10-10): the quote number on the PDF beats every other signal; the sender being on a
case is never enough alone; a quote number no case holds proposes a new case, never the nearest one.
People first (2026-10-27): the person behind the sender's address being on a case is a signal of its own
(`person_on_case`, also never enough alone), and when that person is on an open case, a case that only
shares the topic with the email — no quote number, no thread — and names other people is not offered,
by layer 1 or to the model («mismo tema, otra persona»). A case with no participants recorded, or one the
sender's own address already wrote on, is never dropped: nothing says it is another person's.

The snapshot holds sales cases only (`triage.CASE_CARDS_SQL`, `o.kind = 'sale'`, the API's
`case_kind.SALE_CASES`): a tender is never a candidate.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from origenlab_api.v2.mail_rules import (
    FREE_MAIL_DOMAINS, canonical_quote_number, fold, quote_key, santiago_year, subject_quote_keys,
)

CASE_MATCH_VERSION = 1
CASE_MATCH_VALUE_NORM = f"case_match:v{CASE_MATCH_VERSION}"
DECISIONS: tuple[str, ...] = ("exact", "propose", "new_case", "none")
MAX_CANDIDATES = 3
OWN_DOMAINS = frozenset({"origenlab.cl"})

#: Points per signal. `sender_on_case` is weak on purpose and never suggests alone; `person_on_case` (the
#: sender's person is a participant of the case) never suggests alone either, but with the product or the
#: institution's name it does.
WEIGHTS: dict[str, int] = {"pdf_quote": 10, "thread_pdf_quote": 8, "text_quote": 6, "same_thread": 6, "domain": 3,
                           "institution_name": 3, "product": 2, "sender_on_case": 1, "person_on_case": 4}
EXACT_SIGNALS = frozenset({"pdf_quote", "thread_pdf_quote", "text_quote", "same_thread"})
#: Signals about who writes, not about what: never enough alone to offer a case.
PEOPLE_SIGNALS = frozenset({"sender_on_case", "person_on_case"})
MIN_SCORE = 3

#: A quote number in an attachment name: a CN/COT token («CN09147», «CN1975») or a five-digit 0xxxx
#: number (with an optional revision letter and year: «09153A», «09147-26»). Never inside a longer
#: number («4600987654») and never another prefix («QX5521», «OC-09159»).
_PDF_QUOTE = re.compile(
    r"(?<![a-z0-9_-])(?:(?:cn|cot)\s*-?\s*(\d{4,5})|(0\d{4}))([a-z]{0,2})(?:-(\d{2}|\d{4}))?(?![\d])",
    re.IGNORECASE,
)
#: A bare 0xxxx number after one of these words in a file name is an order, invoice or dispatch
#: number, not a quote («OC 09159.pdf», «Factura 01234.pdf», «Guía de despacho 00045.pdf»).
_NOT_A_QUOTE_BEFORE = re.compile(r"\b(?:oc|o\.c|orden|po|factura|invoice|boleta|gu[ií]a|despacho|pedido)\b",
                                 re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class CaseCard:
    opportunity_id: str
    title: str
    stage: str
    closed_at: str | None
    institution: str | None
    domains: tuple[str, ...]
    quote_numbers: tuple[str, ...]
    threads: tuple[str, ...]
    addresses: tuple[str, ...]
    interests: tuple[str, ...]
    #: The case's participants' persons, each merged person followed to the person it was merged into.
    person_ids: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CaseCard:
        def t(key: str) -> tuple[str, ...]:
            return tuple(str(x) for x in (row.get(key) or ()) if x)
        return cls(str(row["opportunity_id"]), str(row.get("title") or ""), str(row.get("stage") or ""),
                   row.get("closed_at"), row.get("institution"), t("domains"), t("quote_numbers"),
                   t("threads"), t("addresses"), t("interests"), t("person_ids"))


@dataclass(frozen=True)
class MatchInput:
    sender: str | None
    recipients: tuple[str, ...]
    thread_id: str | None
    direction: str
    subject: str
    body: str
    attachment_names: tuple[str, ...]
    sent_at: str | None
    #: Attachment names of the other emails on the same Gmail thread (`comms.attachment`): the quote
    #: PDF is usually on the email OrigenLab sent, not on the client's reply.
    thread_attachment_names: tuple[str, ...] = ()
    #: The person behind the sender's address (`crm.contact_point`, merges followed), when the CRM knows
    #: one. None for a shared mailbox, an address whose owner is unknown, or an inactive address.
    sender_person_id: str | None = None


@dataclass(frozen=True)
class Candidate:
    opportunity_id: str
    score: int
    signals: tuple[str, ...]


@dataclass(frozen=True)
class MatchResult:
    decision: str
    candidates: tuple[Candidate, ...]
    quote_numbers: tuple[str, ...]
    new_case_quote: str | None
    #: Cases the person guard dropped: never offered to the model either. Not stored (`as_json`).
    excluded: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {"case_match_version": CASE_MATCH_VERSION, "decision": self.decision,
                "quote_numbers": list(self.quote_numbers), "new_case_quote": self.new_case_quote,
                "candidates": [{"opportunity_id": c.opportunity_id, "score": c.score, "signals": list(c.signals)}
                               for c in self.candidates]}


def attachment_quote_keys(names: Sequence[str], sent_at: str | None) -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    for name in names:
        for m in _PDF_QUOTE.finditer(name or ""):
            if m.group(2) and _NOT_A_QUOTE_BEFORE.search(name[:m.start()]):
                continue
            digits = m.group(1) or m.group(2)
            year = m.group(4)
            key = quote_key(f"{digits}-{year}" if year else digits, santiago_year(sent_at))
            if key is not None:
                out.setdefault(key, m.group(0).upper())
    return out


def _case_keys(numbers: Sequence[str]) -> tuple[set[tuple[int, int]], set[int]]:
    """(year, correlative) of each number with a year, and the bare correlative of each without one
    («CN09139» as filed in the CRM): the latter matches any year, as R2's yearless index does."""
    keyed: set[tuple[int, int]] = set()
    bare: set[int] = set()
    for q in numbers:
        k = quote_key(q, None)
        if k is not None:
            keyed.add(k)
        else:
            k0 = quote_key(q, 0)
            if k0 is not None:
                bare.add(k0[1])
    return keyed, bare


def _held(found: dict[tuple[int, int], str] | set[tuple[int, int]], keyed: set[tuple[int, int]], bare: set[int]) -> bool:
    return bool(keyed & set(found)) or any(k[1] in bare for k in found)


def _domain(address: str | None) -> str | None:
    return address.rsplit("@", 1)[1].lower() if address and "@" in address else None


def _institution_domains(inp: MatchInput) -> set[str]:
    people = [inp.sender] if inp.direction == "inbound" else list(inp.recipients)
    return {d for d in (_domain(a) for a in people) if d and d not in FREE_MAIL_DOMAINS and d not in OWN_DOMAINS}


def _words(text: str) -> set[str]:
    return set(_WORD.findall(fold(text)))


def _product_hit(interests: Sequence[str], text_words: set[str]) -> bool:
    for interest in interests:
        tokens = [w for w in _WORD.findall(fold(interest)) if any(ch.isdigit() for ch in w) and len(w) >= 3]
        if any(tok in text_words for tok in tokens):
            return True
    return False


def _name_hit(name: str | None, folded_text: str) -> bool:
    needle = " ".join(_WORD.findall(fold(name or "")))
    return len(needle) >= 10 and len(needle.split()) >= 2 and f" {needle} " in f" {folded_text} "


def match_case(inp: MatchInput, cases: Sequence[CaseCard], known_quote_numbers: Sequence[str] = ()) -> MatchResult:
    """`known_quote_numbers`: every quote number the CRM holds, also on cases outside the snapshot (closed
    long ago, discarded) — a number held anywhere is never proposed as a new deal."""
    pdf_keys = attachment_quote_keys(inp.attachment_names, inp.sent_at)
    thread_keys = {k: v for k, v in attachment_quote_keys(inp.thread_attachment_names, inp.sent_at).items()
                   if k not in pdf_keys}
    text_keys = subject_quote_keys(inp.subject, inp.sent_at)
    text_keys.update({k: v for k, v in subject_quote_keys(inp.body, inp.sent_at).items() if k not in text_keys})
    domains = _institution_domains(inp)
    text = f"{inp.subject} {inp.body}"
    text_words = _words(text)
    folded_text = " ".join(_WORD.findall(fold(text)))
    sender = (inp.sender or "").lower()
    person = inp.sender_person_id
    # The sender's person is on an open case: a case that only shares the topic is someone else's.
    person_guard = bool(person) and any(person in c.person_ids for c in cases if not c.closed_at)

    scored: list[Candidate] = []
    excluded: list[str] = []
    for case in cases:
        keyed, bare = _case_keys(case.quote_numbers)
        signals: list[str] = []
        if _held(pdf_keys, keyed, bare):
            signals.append("pdf_quote")
        elif _held(thread_keys, keyed, bare):
            signals.append("thread_pdf_quote")
        if _held(text_keys, keyed, bare):
            signals.append("text_quote")
        if inp.thread_id and inp.thread_id in case.threads:
            signals.append("same_thread")
        if domains & {d.lower() for d in case.domains}:
            signals.append("domain")
        if _name_hit(case.institution, folded_text):
            signals.append("institution_name")
        if _product_hit(case.interests, text_words):
            signals.append("product")
        if sender and sender in {a.lower() for a in case.addresses}:
            signals.append("sender_on_case")
        if person and person in case.person_ids:
            signals.append("person_on_case")
        elif (person_guard and case.person_ids and "sender_on_case" not in signals
              and not EXACT_SIGNALS & set(signals)):
            excluded.append(case.opportunity_id)  # «mismo tema, otra persona»
            continue
        score = sum(WEIGHTS[s] for s in signals)
        if score >= MIN_SCORE and set(signals) - PEOPLE_SIGNALS:
            scored.append(Candidate(case.opportunity_id, score, tuple(signals)))

    # The PDF names the case: nothing nearer competes with it — this email's PDF first, then the thread's.
    for pdf in ("pdf_quote", "thread_pdf_quote"):
        if any(pdf in c.signals for c in scored):
            scored = [c for c in scored if pdf in c.signals]
            break
    closed = {c.opportunity_id for c in cases if c.closed_at}

    def rank(c: Candidate) -> tuple[int, bool]:
        return (-c.score, c.opportunity_id in closed)  # a tie goes to the open case

    scored.sort(key=lambda c: (*rank(c), c.opportunity_id))
    top = tuple(scored[:MAX_CANDIDATES])
    numbers = tuple(canonical_quote_number(k) for k in sorted({*pdf_keys, *thread_keys, *text_keys}))

    known, known_bare = _case_keys([*(q for c in cases for q in c.quote_numbers), *known_quote_numbers])

    def is_known(k: tuple[int, int]) -> bool:
        return k in known or k[1] in known_bare

    unknown = sorted(k for k in pdf_keys if not is_known(k)) or sorted(k for k in thread_keys if not is_known(k))
    new_quote = canonical_quote_number(unknown[0]) if unknown else None
    # A quote no case holds is a new deal — unless the email's thread is already on a case: then it is a
    # new quote of that case (R1 records it there), and the number is kept so a person sees it.
    on_thread = any("same_thread" in c.signals for c in top)
    if new_quote and not on_thread and not any({"pdf_quote", "thread_pdf_quote"} & set(c.signals) for c in top):
        return MatchResult("new_case", top, numbers, new_quote, tuple(excluded))
    if top and set(top[0].signals) & EXACT_SIGNALS and (len(top) == 1 or rank(top[0]) < rank(top[1])):
        return MatchResult("exact", top[:1], numbers, new_quote if on_thread else None, tuple(excluded))
    if top:
        return MatchResult("propose", top, numbers, None, tuple(excluded))
    return MatchResult("none", (), numbers, None, tuple(excluded))


__all__ = ["CASE_MATCH_VALUE_NORM", "CASE_MATCH_VERSION", "DECISIONS", "Candidate", "CaseCard", "MatchInput",
           "MatchResult", "attachment_quote_keys", "match_case"]
