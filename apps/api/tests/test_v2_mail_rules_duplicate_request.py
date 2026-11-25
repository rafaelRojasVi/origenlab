"""R23 (owner 2026-11-22): a quote made in the Cotizador as a new case went out to the client of an
open request — that request is closed as a duplicate. Pure planning, no database.

Every address, domain, name and number here is fictitious (`.test`).
"""

from __future__ import annotations

from dataclasses import replace

from origenlab_api.v2.mail_rules import (
    RULE_IDS,
    CaseQuote,
    CaseState,
    MailDocument,
    MailEvidence,
    Organization,
    QuoteRevision,
    Snapshot,
    plan,
)

SENT = "2026-11-17T15:57:42+00:00"
CLIENT = "compras@hospital-ficticio.test"


def rev(rid: str, status: str, *, no: int = 1) -> QuoteRevision:
    return QuoteRevision(id=rid, revision_no=no, status=status, version=4, superseded=False, origin="authored",
                         sent_at=SENT if status == "sent" else None,
                         approved_at="2026-11-17T12:00:00+00:00" if status in ("approved", "sent") else None)


def cotizador_case(**over) -> CaseState:
    """The case «Nueva cotización» opened, named after the person typed, its quote now sent."""
    base = dict(id="c-cotizador", title="Persona Ficticia — cotización", stage="quoting", version=9,
                organization_id="o-typed", closed=False, thread_ids=("t-1",),
                quotes=(CaseQuote(id="q-new", quote_number="01342-26", revisions=(rev("r-new", "sent"),)),),
                people=(CLIENT,), opened_with_quote_id="q-new", organization_typed=True)
    base.update(over)
    return CaseState(**base)


def request_case(**over) -> CaseState:
    """The client's request, opened from a forward, holding only a draft."""
    base = dict(id="c-request", title="Solicitud — Solicitud de cotización", stage="lead", version=5,
                organization_id="o-hospital", closed=False, thread_ids=(),
                quotes=(CaseQuote(id="q-draft", quote_number="01323-26", revisions=(rev("r-draft", "draft"),)),),
                people=(CLIENT,), participants=(CLIENT,))
    base.update(over)
    return CaseState(**base)


def mail(**over) -> MailEvidence:
    base = dict(id="e-1", thread_id="t-1", direction="outbound", sender="Ventas <contacto@origenlab.cl>",
                recipients=(f"{CLIENT}; otra@ejemplo.invalid",), subject="Re: Solicitud de cotización", sent_at=SENT,
                documents=(MailDocument(filename="Cotización CN01342 — Persona.pdf", sha256="a" * 64),),
                linked_case_ids=("c-cotizador",))
    base.update(over)
    return MailEvidence(**base)


ORGS = (Organization(id="o-hospital", name="Hospital Ficticio", version=1, confirmation="confirmed",
                     domains=("hospital-ficticio.test",)),
        Organization(id="o-typed", name="Persona Ficticia", version=1, confirmation="confirmed"))


def r23(*cases: CaseState, evidence: MailEvidence | None = None, after_r16: bool = True):
    pairs = frozenset({("e-1", "R16")}) if after_r16 else frozenset()
    snap = Snapshot(evidence=(evidence or mail(),), cases=cases, organizations=ORGS,
                    applied_evidence_ids=frozenset({"e-1"}) if after_r16 else frozenset(), applied_pairs=pairs)
    return [a for a in plan(snap) if a.rule_id == "R23"]


def test_r23_is_a_rule() -> None:
    assert "R23" in RULE_IDS


def test_the_request_beside_the_cotizador_case_closes_as_a_duplicate() -> None:
    # The 2026-11-17 shape: a forwarded request holding a draft; the quote made as a new case,
    # sent on the request's own thread; the client's address is one of the request's people.
    [a] = r23(cotizador_case(), request_case())
    assert (a.mode, a.case_id, a.quote_number, a.candidates) == ("auto", "c-request", "01342-26", ("c-cotizador",))
    assert a.context == {"case": "c-request", "case_version": 5, "from_stage": "lead"}
    [step] = a.commands
    assert step["command"] == "advance_case_stage"
    assert step["inputs"]["stage"] == "abandoned"
    assert step["inputs"]["close_reason"].startswith("Duplicado: la cotización 01342-26")
    assert "c-cotiza" in step["inputs"]["close_reason"]
    assert "R23" in step["inputs"]["note"]


def test_a_request_with_no_quote_at_all_and_no_institution_closes_too() -> None:
    [a] = r23(cotizador_case(), request_case(quotes=(), organization_id=None, stage="qualifying"))
    assert a.context["from_stage"] == "qualifying"


def test_it_waits_for_r16_and_acts_once() -> None:
    assert r23(cotizador_case(), request_case(), after_r16=False) == []
    snap = Snapshot(evidence=(mail(),), cases=(cotizador_case(), request_case()), organizations=ORGS,
                    applied_evidence_ids=frozenset({"e-1"}),
                    applied_pairs=frozenset({("e-1", "R16"), ("e-1", "R23")}))
    assert [a for a in plan(snap) if a.rule_id == "R23"] == []


def test_a_different_person_is_another_client() -> None:
    assert r23(cotizador_case(), request_case(people=("otra.persona@hospital-ficticio.test",), participants=())) == []


def test_a_closed_or_paused_request_is_never_touched() -> None:
    closed = request_case(closed=True, stage="lost")
    assert r23(cotizador_case(), closed) == []
    assert r23(cotizador_case(), request_case(paused_until="2026-12-01")) == []


def test_a_request_with_its_own_sent_or_approved_quote_is_its_own_deal() -> None:
    for status in ("sent", "approved"):
        own = CaseQuote(id="q-own", quote_number="01330-26", revisions=(rev("r-own", status),))
        assert r23(cotizador_case(), request_case(quotes=(own,), stage="qualified")) == [], status
    # Past «Enviada» it is not a request waiting either.
    assert r23(cotizador_case(), request_case(stage="quoting")) == []


def test_two_institutions_known_to_the_crm_are_two_clients() -> None:
    # The Cotizador case names an institution the CRM already had (not typed there): never merged.
    assert r23(cotizador_case(organization_typed=False), request_case()) == []
    # The same institution, or none on one side, is the same client.
    assert len(r23(cotizador_case(organization_typed=False, organization_id="o-hospital"), request_case())) == 1
    assert len(r23(cotizador_case(organization_typed=False, organization_id=None), request_case())) == 1


def test_only_a_case_the_cotizador_opened_and_only_one_request() -> None:
    # A case opened any other way (a request, a forward) is not «Nueva cotización» beside a request.
    assert r23(cotizador_case(opened_with_quote_id=None), request_case()) == []
    # Its opening quote not sent yet (another quote of it went out): nothing yet.
    other = CaseQuote(id="q-other", quote_number="01343-26", revisions=(rev("r-other", "sent"),))
    pending = CaseQuote(id="q-new", quote_number="01342-26", revisions=(rev("r-new", "approved"),))
    assert r23(cotizador_case(quotes=(pending, other)), request_case()) == []
    # Two open requests of that address: a person decides.
    assert r23(cotizador_case(), request_case(), request_case(id="c-request-2")) == []


def test_two_cases_on_the_same_email_are_left_alone() -> None:
    # The 2026-11-02 shapes: both cases' PDFs went out in one email, so both are on it; no rule here
    # closes a case that holds a sent quote, and a quote is never moved between cases.
    first = request_case(stage="quoting", quotes=(CaseQuote(id="q-1", quote_number="01305-26",
                                                            revisions=(rev("r-1", "sent"),)),))
    assert r23(cotizador_case(), first, evidence=mail(linked_case_ids=("c-cotizador", "c-request"))) == []


def test_only_our_outbound_mail() -> None:
    assert r23(cotizador_case(), request_case(), evidence=mail(direction="inbound", sender=CLIENT)) == []
    assert r23(cotizador_case(), request_case(), evidence=mail(sender="otra@origenlab.cl")) == []
    assert r23(cotizador_case(), request_case(), evidence=mail(direction_hint="internal_only")) == []


def test_the_revision_letter_is_the_sent_one() -> None:
    sent_b = CaseQuote(id="q-new", quote_number="01342-26",
                       revisions=(rev("r-1", "void"), rev("r-2", "sent", no=2)))
    [a] = r23(replace(cotizador_case(), quotes=(sent_b,)), request_case())
    assert a.quote_number == "01342A-26"


def test_a_request_answered_on_its_own_thread_is_closed_too() -> None:
    # Production 2026-11-25: the quote of the Cotizador case was sent as the reply to the request, so
    # the email is on both cases; the request, with no quote of its own, is the same purchase.
    [a] = r23(cotizador_case(), request_case(), evidence=mail(linked_case_ids=("c-cotizador", "c-request")))
    assert a.case_id == "c-request"
