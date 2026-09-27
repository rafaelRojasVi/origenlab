"""Which high-confidence quotation documents a later load could confirm, and which stay suggestions.

This module prepares an application report. It applies nothing: it opens no file, writes no
ledger and creates no opportunity or quote. It takes the frozen review policy's dry run
(`quote_review_policy`) and splits its high-confidence documents in two:

* **confirmación segura** — the quotation facts the document itself proves under P13: it is an
  OrigenLab customer quotation, and its quote number is the one it prints. A later load could
  record these without asking the operator again.
* **solo sugerencia** — anything that still needs the operator. For *every* document this
  includes the opportunity (a new one is only proposed: the operator types an opportunity id or
  chooses `create_new`) and the link from the printed client to a CRM organization or contact,
  which the policy never infers. A whole document is a suggestion when one of the reasons below
  fires, whatever P13 says.

Reasons that turn a high-confidence document into a suggestion:

* `labdelivery_number_series` — a Labdelivery header. P03 recognises it only through the own-sender
  addresses the dry-run script configures (the module default recognises none), and its number
  belongs to the legacy Labdelivery space, which quote-number adoption refuses.
* `serial_at_or_beyond_seed` — the printed serial is at or beyond the seed the report runs with: the
  first serial the forward series would allocate. Adoption refuses such a serial, because the series
  would later allocate it again. The owner approved 1235 on 2026-09-21 and it is applied nowhere;
  1235–1241 were printed by hand since, so a report may run with a proposed seed instead.
* `reserved_serial` — a serial reserved from allocation (1500).
* `ledger_decision_differs` — the ledgers hold a decision that is not this confirmation. P12: an
  owner decision is final, the policy never overrides it.

Documents below high confidence are not candidates. They stay in human review, unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from origenlab_api.v2 import quote_review_policy as qp
from origenlab_api.v2.quote_document_review import DocumentReview, DocumentState
from origenlab_api.v2.quote_evidence_review import (
    DECISION_CONFIRM,
    OPPORTUNITY_CREATE_NEW,
    STATUS_CONFIRMED,
)

SEPARATION_SAFE = "confirmacion_segura"
SEPARATION_SUGGESTION = "solo_sugerencia"

ACTION_CONFIRM = "confirmar_cotizacion"
ACTION_NONE_ALREADY_CONFIRMED = "ninguna_ya_confirmada"
ACTION_OPERATOR = "revision_operador"

LEDGER_UNDECIDED = "sin_decision"
LEDGER_CONFIRMED_AGREES = "confirmada_coincide"
LEDGER_DIFFERS = "decision_distinta"

S_LABDELIVERY_SERIES = "labdelivery_number_series"
S_AT_OR_BEYOND_SEED = "serial_at_or_beyond_seed"
S_RESERVED_SERIAL = "reserved_serial"
S_LEDGER_DIFFERS = "ledger_decision_differs"

APPROVED_SEED_NEXT_SERIAL = 1235  # owner-approved 2026-09-21; stale, see the module docstring
RESERVED_SERIALS = frozenset({1500})


@dataclass(frozen=True)
class ApplyCandidate:
    sha256: str
    canonical_email_id: int
    duplicate_email_ids: tuple[int, ...]
    thread_ids: tuple[str, ...]
    sent_at: str
    filenames: tuple[str, ...]
    issuer: str
    printed_number: str
    number_key: str
    serial: int
    document_date: str | None
    client_line: str
    client_name: str
    contact: str | None
    organization: str | None
    rut: str | None
    classification: str
    rules: tuple[str, ...]
    proposed_opportunity: str
    ledger_state: str
    ledger_decision: str
    ledger_quote_number: str | None
    planned_opportunity_id: str | None
    separation: str
    action: str
    suggestion_reasons: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def safe_fields(self) -> tuple[str, ...]:
        """What a later load may record without the operator. Empty for a suggestion."""
        if self.separation != SEPARATION_SAFE:
            return ()
        return (f"clasificacion={self.classification}", f"numero={self.printed_number}",
                f"correo_canonico={self.canonical_email_id}")

    @property
    def suggestion_fields(self) -> tuple[str, ...]:
        """What only the operator decides, for every document."""
        out = ["oportunidad=" + (f"planificada {self.planned_opportunity_id}" if self.planned_opportunity_id
                                 else "nueva (create_new) propuesta")]
        if self.organization:
            out.append(f"organizacion_crm={self.organization}")
            out.append("contacto_crm=" + (self.contact or "sin persona impresa"))
        else:
            out.append(f"cliente_crm={self.client_name} (persona u organización: lo decide el operador)")
        if self.separation == SEPARATION_SUGGESTION:
            out.insert(0, f"cotizacion=customer_quotation {self.printed_number}")
        return tuple(out)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256, "canonical_email_id": self.canonical_email_id,
            "duplicate_email_ids": list(self.duplicate_email_ids), "thread_ids": list(self.thread_ids),
            "sent_at": self.sent_at, "filenames": list(self.filenames), "issuer": self.issuer,
            "printed_number": self.printed_number, "number_key": self.number_key, "serial": self.serial,
            "document_date": self.document_date, "client_line": self.client_line, "client_name": self.client_name, "contact": self.contact,
            "organization": self.organization, "rut": self.rut, "classification": self.classification,
            "rules": list(self.rules), "proposed_opportunity": self.proposed_opportunity,
            "ledger_state": self.ledger_state, "ledger_decision": self.ledger_decision,
            "ledger_quote_number": self.ledger_quote_number,
            "planned_opportunity_id": self.planned_opportunity_id, "separation": self.separation,
            "action": self.action, "suggestion_reasons": list(self.suggestion_reasons),
            "notes": list(self.notes), "safe_fields": list(self.safe_fields),
            "suggestion_fields": list(self.suggestion_fields),
        }


def _ledger(state: DocumentState | None, printed: str) -> tuple[str, str, str | None, str | None]:
    if state is None or state.latest is None:
        return LEDGER_UNDECIDED, "", None, None
    e = state.latest
    number = e.get("quote_number")
    mode = (e.get("opportunity") or {}).get("mode")
    agrees = (state.status == STATUS_CONFIRMED and e.get("decision") == DECISION_CONFIRM
              and qp.same_quote_number(number, printed) and mode == OPPORTUNITY_CREATE_NEW)
    summary = f"{e.get('decision')} {number or ''} {mode or ''}".strip()
    return (LEDGER_CONFIRMED_AGREES if agrees else LEDGER_DIFFERS), summary, number, state.opportunity_key


def build_apply_candidates(
    run: qp.DryRun,
    review: DocumentReview,
    document_states: Mapping[str, DocumentState],
    *,
    seed_next_serial: int = APPROVED_SEED_NEXT_SERIAL,
    reserved_serials: Iterable[int] = RESERVED_SERIALS,
) -> list[ApplyCandidate]:
    """One candidate per high-confidence document, sorted by canonical email. Nothing else."""
    reserved = frozenset(reserved_serials)
    cands = {c.sha256: c for c in review.candidates}
    out: list[ApplyCandidate] = []
    for v in run.documents.values():
        if not v.automatable:
            continue
        c = cands[v.sha256]
        ident = c.identity
        qn = ident.quote_number
        if qn is None or v.proposed_quote_number is None or len(ident.clients) != 1:
            raise ValueError(f"{v.sha256}: high confidence without one printed number and one client")
        client = ident.clients[0]
        ledger_state, ledger_summary, ledger_number, planned = _ledger(document_states.get(v.sha256), qn.raw)

        reasons: list[str] = []
        if v.issuer == qp.ISSUER_LABDELIVERY:
            reasons.append(S_LABDELIVERY_SERIES)
        if qn.serial >= seed_next_serial:
            reasons.append(S_AT_OR_BEYOND_SEED)
        if qn.serial in reserved:
            reasons.append(S_RESERVED_SERIAL)
        if ledger_state == LEDGER_DIFFERS:
            reasons.append(S_LEDGER_DIFFERS)

        notes: list[str] = []
        if not client.organization:
            notes.append("el bloque de cliente no separa persona de organización")
        if client.rut is None:
            notes.append("sin RUT impreso")
        if ledger_number and ledger_number != qn.raw and ledger_state == LEDGER_CONFIRMED_AGREES:
            notes.append(f"el ledger guarda {ledger_number}; el PDF imprime {qn.raw} (mismo número)")
        if c.duplicate_occurrences:
            notes.append("reenvíos del mismo PDF: evidencia duplicada, no otra cotización (P01)")

        if reasons:
            separation, action = SEPARATION_SUGGESTION, ACTION_OPERATOR
        elif ledger_state == LEDGER_CONFIRMED_AGREES:
            separation, action = SEPARATION_SAFE, ACTION_NONE_ALREADY_CONFIRMED
        else:
            separation, action = SEPARATION_SAFE, ACTION_CONFIRM

        rules = tuple(v.rules) + (("P03",) if v.issuer == qp.ISSUER_LABDELIVERY else ())
        out.append(ApplyCandidate(
            sha256=v.sha256, canonical_email_id=v.canonical_email_id,
            duplicate_email_ids=tuple(o.email_id for o in c.duplicate_occurrences),
            thread_ids=v.thread_ids, sent_at=c.canonical.sent_at, filenames=c.filenames,
            issuer=v.issuer, printed_number=qn.raw, number_key=qn.key, serial=qn.serial,
            document_date=ident.document_date.value if ident.document_date else None,
            client_line=client.addressee.line, client_name=client.addressee.value, contact=client.contact, organization=client.organization,
            rut=client.rut.value if client.rut else None, classification=v.classification,
            rules=tuple(sorted(set(rules))), proposed_opportunity=v.proposed_opportunity,
            ledger_state=ledger_state, ledger_decision=ledger_summary, ledger_quote_number=ledger_number,
            planned_opportunity_id=planned, separation=separation, action=action,
            suggestion_reasons=tuple(reasons), notes=tuple(notes),
        ))
    return sorted(out, key=lambda a: (a.canonical_email_id, a.sha256))


CURRENT_SERIES_BELOW = 10_000  # the current 0NNNN-YY series; 01NNNN-YY forms (011728A, 012395) are older


def max_printed_serial(review: DocumentReview, issuers: Mapping[str, str], issuer: str = qp.ISSUER_ORIGENLAB,
                       below: int = CURRENT_SERIES_BELOW) -> int | None:
    """The highest serial of the current series any staged document of this issuer prints, whatever
    its confidence."""
    serials = [q.serial for c in review.candidates if issuers.get(c.sha256) == issuer
               for q in c.identity.quote_numbers_found if q.serial < below]
    return max(serials, default=None)


def held_for_human(run: qp.DryRun, candidates: Iterable[ApplyCandidate]) -> list[qp.HumanCase]:
    """The policy's human-review cases, checked to share no document with the candidates."""
    shas = {a.sha256 for a in candidates}
    emails = {e for a in candidates for e in (a.canonical_email_id, *a.duplicate_email_ids)}
    for h in run.human:
        if (h.target == "document" and h.key in shas) or (h.target == "email" and int(h.key) in emails):
            raise ValueError(f"{h.target} {h.key} is both a candidate and held for a human")
    return list(run.human)
