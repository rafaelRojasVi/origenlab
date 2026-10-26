"""R17 (owner 2026-10-26): the client answered on a case in «Enviada» after its quote went out → the
case moves to «Conversación». Pure planning, no database.

Every address, domain, name and number here is fictitious (`.test`).
"""

from __future__ import annotations

from origenlab_api.v2.mail_rules import (
    RULE_IDS,
    CaseQuote,
    CaseState,
    MailEvidence,
    Organization,
    QuoteRevision,
    Snapshot,
    plan,
)

QUOTE_SENT = "2026-10-22T19:00:00+00:00"
REPLIED = "2026-10-26T16:00:00+00:00"
SINCE = "2026-10-12T16:00:00+00:00"


def sent_quote(*, sent_at: str | None = QUOTE_SENT, status: str = "sent", superseded: bool = False) -> CaseQuote:
    return CaseQuote(id="q-1", quote_number="09876-26", revisions=(
        QuoteRevision(id="r-1", revision_no=1, status=status, version=4, superseded=superseded, sent_at=sent_at),))


def case(*quotes: CaseQuote, **over) -> CaseState:
    base = dict(id="c-1", title="Sonicador ficticio", stage="quoting", version=9, organization_id="o-1",
                closed=False, thread_ids=("t-1",), quotes=quotes or (sent_quote(),))
    base.update(over)
    return CaseState(**base)


def reply(**over) -> MailEvidence:
    base = dict(id="e-9", thread_id="t-1", direction="inbound", sender="Cliente <persona@cliente.test>",
                recipients=("contacto@origenlab.cl",), subject="Re: Cotización sonicador", sent_at=REPLIED,
                linked_case_ids=("c-1",))
    base.update(over)
    return MailEvidence(**base)


CLIENT = Organization(id="o-1", name="Universidad Ficticia", version=1, confirmation="confirmed",
                      domains=("cliente.test",))
SUPPLIER = Organization(id="o-2", name="Fabricante Ficticio", version=1, confirmation="confirmed",
                        domains=("fabricante.test",), is_supplier=True)


def r17(*evidence: MailEvidence, cases: tuple[CaseState, ...] | None = None, since: str | None = SINCE, **kw):
    snap = Snapshot(evidence=evidence, cases=cases or (case(),), organizations=(CLIENT, SUPPLIER),
                    reply_since=since, **kw)
    return [a for a in plan(snap) if a.rule_id == "R17"]


def test_r17_is_a_rule() -> None:
    assert RULE_IDS[-1] == "R17"


def test_the_client_answering_after_the_quote_moves_the_case_to_conversacion() -> None:
    # R1 linked the reply on the previous pass: its receipt is in, R17 still reads the email once.
    [a] = r17(reply(), applied_evidence_ids=frozenset({"e-9"}), applied_pairs=frozenset({("e-9", "R1")}))
    assert (a.mode, a.case_id, a.quote_number) == ("auto", "c-1", "09876-26")
    assert [c["command"] for c in a.commands] == ["advance_case_stage"]
    assert a.commands[0]["inputs"]["stage"] == "negotiating"
    assert a.context["from_stage"] == "quoting"
    assert "respondió" in a.reasons[0]


def test_once() -> None:
    assert r17(reply(), applied_evidence_ids=frozenset({"e-9"}),
               applied_pairs=frozenset({("e-9", "R1"), ("e-9", "R17")})) == []


def test_a_free_mail_client_counts_too() -> None:
    assert len(r17(reply(sender="persona.ficticia@gmail.com"))) == 1


def test_not_on_a_case_yet_waits_for_the_link() -> None:
    assert r17(reply(linked_case_ids=())) == []


def test_never_a_case_put_en_pausa() -> None:
    # «Se evaluará el año entrante» is why the case was paused: it stays «Enviada» until it comes back.
    assert r17(reply(), cases=(case(paused_until="2027-03-01T12:00:00+00:00"),)) == []


def test_only_from_enviada() -> None:
    for stage in ("lead", "qualified", "negotiating"):
        assert r17(reply(), cases=(case(stage=stage),)) == [], stage
    assert r17(reply(), cases=(case(stage="won", closed=True),)) == []


def test_a_reply_before_the_quote_went_out_is_not_an_answer_to_it() -> None:
    assert r17(reply(sent_at="2026-10-22T18:00:00+00:00")) == []
    assert r17(reply(), cases=(case(sent_quote(sent_at=None, status="approved")),)) == []
    assert r17(reply(), cases=(case(sent_quote(superseded=True)),)) == []


def test_never_our_mail_a_supplier_or_an_automatic_reply() -> None:
    assert r17(reply(direction="outbound", sender="contacto@origenlab.cl")) == []
    assert r17(reply(sender="ventas@fabricante.test")) == []
    for subject in ("Respuesta automática: Re: Cotización", "Out of Office: Re: Cotización",
                    "Fuera de la oficina hasta el lunes"):
        assert r17(reply(subject=subject)) == [], subject


def test_an_old_reply_is_left_to_a_person() -> None:
    assert r17(reply(sent_at="2026-10-01T12:00:00+00:00", ),
               cases=(case(sent_quote(sent_at="2026-09-20T12:00:00+00:00")),)) == []
    assert r17(reply(), since=None) == []


def test_waits_while_another_rule_acts_on_the_email() -> None:
    # A new reply on the case's thread: R1 links it on this pass; R17 moves the case on the next.
    snap = Snapshot(evidence=(reply(linked_case_ids=()),), cases=(case(),), organizations=(CLIENT,), reply_since=SINCE)
    assert [a.rule_id for a in plan(snap)] == ["R1"]
