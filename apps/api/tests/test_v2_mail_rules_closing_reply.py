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


def lost(**over) -> MailEvidence:
    return thanks(**{"model_intent": "lost", "model_stage": "lost",
                     "model_summary": "Compraron a otro proveedor.", **over})


def test_r19_closes_a_case_lost() -> None:
    # The case is already in «Conversación» (R17 moved it on an earlier email): nothing else acts.
    [a] = planned("R19", lost(), cases=(case(stage="negotiating"),))
    assert (a.mode, a.case_id) == ("auto", "c-1")
    [step] = a.commands
    assert step["command"] == "advance_case_stage"
    assert step["inputs"]["stage"] == "lost"
    assert step["inputs"]["close_reason"] == "el cliente no sigue (lectura del correo)"
    assert "Compraron a otro proveedor." in a.reasons[0]
    assert a.context["from_stage"] == "negotiating"


def test_r19_proposes_when_the_sender_cannot_speak_for_the_case() -> None:
    [a] = planned("R19", lost(sender="alguien@otra-institucion.test"), cases=(case(stage="negotiating"),))
    assert a.mode == "proposal"
    assert a.commands == ()


def test_r19_ignores_an_older_email() -> None:
    # The client wrote again after the «no»: the newest email decides, not the old one.
    newer = thanks(id="e-10", sent_at="2026-10-27T16:00:00+00:00", model_needs_reply=True)
    assert planned("R19", lost(), newer, cases=(case(stage="negotiating"),),
                   applied_evidence_ids=frozenset({"e-9", "e-10"}),
                   applied_pairs=frozenset({("e-9", "R1"), ("e-10", "R1")})) == []


def test_r19_skips_a_paused_case() -> None:
    assert planned("R19", lost(), cases=(case(stage="negotiating", paused_until="2027-03-01T12:00:00+00:00"),)) == []


def test_r19_skips_won_and_closed() -> None:
    assert planned("R19", lost(), cases=(case(stage="won", closed=True),)) == []
    assert planned("R19", lost(), cases=(case(stage="lost", closed=True),)) == []


def test_r19_needs_a_lost_reading() -> None:
    assert planned("R19", lost(model_stage="negotiating"), cases=(case(stage="negotiating"),)) == []
    assert planned("R19", lost(model_stage=None), cases=(case(stage="negotiating"),)) == []


def test_r19_never_a_bounce_or_a_supplier() -> None:
    assert planned("R19", lost(triage_class="bounce"), cases=(case(stage="negotiating"),)) == []
    assert planned("R19", lost(sender="ventas@fabricante.test"), cases=(case(stage="negotiating"),)) == []


def test_r19_waits_for_r17() -> None:
    # A case in «Enviada»: R17 moves it this pass; R19 closes it on the next, once R17's receipt is in.
    assert [a.rule_id for a in planned("R17", lost())] == ["R17"]
    assert planned("R19", lost()) == []
    [a] = planned("R19", lost(), cases=(case(stage="negotiating"),),
                  applied_pairs=frozenset({("e-9", "R1"), ("e-9", "R17")}))
    assert a.mode == "auto"


def test_r19_runs_once() -> None:
    assert planned("R19", lost(), cases=(case(stage="negotiating"),),
                   applied_pairs=frozenset({("e-9", "R1"), ("e-9", "R19")})) == []


def test_r19_a_later_bounce_does_not_hide_the_clients_no() -> None:
    bounce = thanks(id="e-10", sent_at="2026-10-27T16:00:00+00:00", sender="Mailer-Daemon@correo.test",
                    triage_class="bounce", model_needs_reply=None, model_stage=None)
    [a] = planned("R19", lost(), bounce, cases=(case(stage="negotiating"),),
                  applied_evidence_ids=frozenset({"e-9", "e-10"}),
                  applied_pairs=frozenset({("e-9", "R1"), ("e-10", "R1")}))
    assert a.mode == "auto"


def test_the_timer_runs_r18_and_r19() -> None:
    # Owner 2026-10-29: both act by themselves and show in «Hecho automáticamente» with «Quitar» —
    # R19 too, although R5/R6 (terminal) still wait for an admin.
    from origenlab_api.v2.mail_rules_auto import AUTOMATIC_RULES

    assert {"R18", "R19"} <= AUTOMATIC_RULES
