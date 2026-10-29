"""R18 and R19 (owner 2026-10-29): what the triage's AI reading says about a client's email on a case.
R18 — «gracias, te aviso» needs no answer: a receipt only, the email leaves «Responder». R19 — «compramos
a otro proveedor», «se evaluará el año entrante»: the case is lost. Pure planning, no database.

Every address, domain, name and number here is fictitious (`.test`).
"""

from __future__ import annotations

from origenlab_api.v2.mail_rules import RULE_IDS, MailEvidence, Snapshot, plan

from test_v2_mail_rules_client_reply import CLIENT, SINCE, SUPPLIER, case, reply

AFTER_R1 = dict(applied_evidence_ids=frozenset({"e-9"}), applied_pairs=frozenset({("e-9", "R1")}))


def thanks(**over) -> MailEvidence:
    base = dict(model_intent="quote_followup", model_stage="negotiating", model_needs_reply=False,
                model_summary="Agradece la cotización y avisará.", triage_class="commercial")
    base.update(over)
    return reply(**base)


def planned(rule: str, *evidence: MailEvidence, cases=None, since: str | None = SINCE, **kw):
    snap = Snapshot(evidence=evidence, cases=cases or (case(),), organizations=(CLIENT, SUPPLIER),
                    reply_since=since, **({**AFTER_R1, **kw}))
    return [a for a in plan(snap) if a.rule_id == rule]


def test_r18_and_r19_are_rules() -> None:
    assert RULE_IDS[-2:] == ("R18", "R19")


def test_r18_marks_a_closing_reply() -> None:
    [a] = planned("R18", thanks())
    assert (a.mode, a.case_id, a.commands) == ("auto", "c-1", ())
    assert "Agradece la cotización y avisará." in a.reasons[0]
    assert a.reasons[-1] == "no requiere respuesta"
    assert a.context["case"] == "c-1"


def test_r18_on_a_won_case() -> None:
    assert len(planned("R18", thanks(), cases=(case(stage="won", closed=True),))) == 1


def test_r18_not_on_a_lost_case() -> None:
    assert planned("R18", thanks(), cases=(case(stage="lost", closed=True),)) == []


def test_r18_ignores_needs_reply_true() -> None:
    # «Gracias; ¿cuándo despachan?»: thanks and a question — a person answers it.
    assert planned("R18", thanks(model_needs_reply=True)) == []


def test_r18_ignores_a_missing_reading() -> None:
    assert planned("R18", thanks(model_needs_reply=None, model_stage=None, model_summary=None)) == []


def test_r18_ignores_old_mail() -> None:
    assert planned("R18", thanks(sent_at="2026-10-01T12:00:00+00:00")) == []
    assert planned("R18", thanks(), since=None) == []


def test_r18_ignores_a_bounce_or_an_automatic_reply() -> None:
    assert planned("R18", thanks(triage_class="bounce")) == []
    assert planned("R18", thanks(triage_class="auto_reply")) == []
    assert planned("R18", thanks(subject="Respuesta automática: Re: Cotización")) == []


def test_r18_never_our_mail_or_a_supplier() -> None:
    assert planned("R18", thanks(direction="outbound", sender="contacto@origenlab.cl")) == []
    assert planned("R18", thanks(sender="ventas@fabricante.test")) == []


def test_r18_needs_exactly_one_case() -> None:
    assert planned("R18", thanks(linked_case_ids=())) == []
    assert planned("R18", thanks(linked_case_ids=("c-1", "c-2")),
                   cases=(case(), case(id="c-2", thread_ids=("t-1",)))) == []


def test_r18_leaves_lost_to_r19() -> None:
    assert planned("R18", thanks(model_stage="lost")) == []


def test_r18_runs_once() -> None:
    assert planned("R18", thanks(), applied_pairs=frozenset({("e-9", "R1"), ("e-9", "R18")})) == []


def test_r18_alongside_r17() -> None:
    # A case in «Enviada»: R17 moves it to «Conversación», R18 records that the email needs no answer.
    snap = Snapshot(evidence=(thanks(),), cases=(case(),), organizations=(CLIENT, SUPPLIER), reply_since=SINCE,
                    **AFTER_R1)
    assert sorted(a.rule_id for a in plan(snap)) == ["R17", "R18"]
