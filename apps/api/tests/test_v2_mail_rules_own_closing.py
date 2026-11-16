"""R22 (owner 2026-11-16): OrigenLab's own closing email («lo dejo en pausa hasta marzo», «quedo atenta
para cuando…») pauses the case until the day it names, else 30 days after the email. The worker reads
the email (`outbound_intent:v1`); the rule decides. Pure planning, no database.

Every address, domain, name and number here is fictitious (`.test`).
"""

from __future__ import annotations

from origenlab_api.v2.mail_rules import OWN_CLOSING_PAUSE_REASON, RULE_IDS, MailEvidence, Snapshot, plan

from test_v2_mail_rules_client_reply import CLIENT, SINCE, SUPPLIER, case, reply

AFTER_R1 = dict(applied_evidence_ids=frozenset({"e-9"}), applied_pairs=frozenset({("e-9", "R1")}))


def ours(until: str | None = "2027-03-01", **over) -> MailEvidence:
    base = dict(direction="outbound", sender="OrigenLab <contacto@origenlab.cl>", recipients=("persona@cliente.test",),
                triage_class="outbound",
                own_closing={"closing": True, "until": until, "phrase": "lo dejo en pausa hasta marzo"})
    base.update(over)
    return reply(**base)


def planned(*evidence: MailEvidence, cases=None, since: str | None = SINCE, **kw):
    snap = Snapshot(evidence=evidence, cases=cases or (case(),), organizations=(CLIENT, SUPPLIER),
                    reply_since=since, **({**AFTER_R1, **kw}))
    return [a for a in plan(snap) if a.rule_id == "R22"]


def test_r22_is_a_rule() -> None:
    assert RULE_IDS[-1] == "R22"


def test_r22_pauses_the_case_until_the_day_the_email_names() -> None:
    [a] = planned(ours())
    assert (a.mode, a.case_id) == ("auto", "c-1")
    [step] = a.commands
    assert step["command"] == "set_case_pause"
    assert step["inputs"]["paused_until"] == "2027-03-01"
    assert step["inputs"]["reason"] == OWN_CLOSING_PAUSE_REASON
    assert "lo dejo en pausa hasta marzo" in a.reasons[0]
    assert a.context["case"] == "c-1"


def test_r22_without_a_day_pauses_thirty_days_after_the_email() -> None:
    [a] = planned(ours(until=None))  # sent 2026-10-26
    assert a.commands[0]["inputs"]["paused_until"] == "2026-11-25"


def test_r22_a_day_not_ahead_of_the_email_falls_back_to_thirty_days() -> None:
    [a] = planned(ours(until="2026-10-01"))
    assert a.commands[0]["inputs"]["paused_until"] == "2026-11-25"


def test_r22_needs_our_closing_reading() -> None:
    assert planned(ours(own_closing=None)) == []
    assert planned(ours(own_closing={"closing": False, "until": None, "phrase": ""})) == []


def test_r22_never_on_a_client_email() -> None:
    assert planned(ours(direction="inbound", sender="Cliente <persona@cliente.test>")) == []


def test_r22_skips_a_paused_or_closed_case() -> None:
    assert planned(ours(), cases=(case(paused_until="2027-01-04"),)) == []
    assert planned(ours(), cases=(case(stage="lost", closed=True),)) == []
    assert planned(ours(), cases=(case(stage="won", closed=True),)) == []


def test_r22_not_when_the_client_wrote_after_our_email() -> None:
    later = reply(id="e-10", sent_at="2026-10-27T16:00:00+00:00")
    assert planned(ours(), later, applied_evidence_ids=frozenset({"e-9", "e-10"}),
                   applied_pairs=frozenset({("e-9", "R1"), ("e-10", "R1")})) == []


def test_r22_ignores_old_mail_and_runs_once() -> None:
    assert planned(ours(sent_at="2026-10-01T12:00:00+00:00")) == []
    assert planned(ours(), applied_pairs=frozenset({("e-9", "R1"), ("e-9", "R22")})) == []


def test_r22_needs_exactly_one_case() -> None:
    assert planned(ours(linked_case_ids=(), thread_id="t-otro")) == []
    assert planned(ours(linked_case_ids=("c-1", "c-2")), cases=(case(), case(id="c-2", thread_ids=("t-1",)))) == []


def test_r22_works_on_a_case_thread_not_linked_yet() -> None:
    [a] = planned(ours(linked_case_ids=()))
    assert a.case_id == "c-1"
