"""R21 (owner 2026-11-11): who writes on an open case, or whom we write to from it, is a person of
the case. Pure planning, no database.

Every address, domain, name and number here is fictitious (`.test`, `example.test`).
"""

from __future__ import annotations

from origenlab_api.v2.mail_rules import (
    ADD_CASE_SENDER_PARTICIPANT,
    AUTO,
    CaseState,
    MailEvidence,
    Organization,
    Snapshot,
    case_people_key,
    plan_case_people,
)


def case(**over) -> CaseState:
    base = dict(id="c-1", title="Balanza de plataforma", stage="quoting", version=4, organization_id="o-1",
                closed=False, thread_ids=("t-1",), quotes=())
    base.update(over)
    return CaseState(**base)


def sent(**over) -> MailEvidence:
    base = dict(id="e-1", thread_id="t-1", direction="outbound", sender="Ventas <contacto@origenlab.cl>",
                recipients=("Carla <carla@cliente.test>",), subject="RE: Cotización balanza",
                sent_at="2026-11-09T12:39:00+00:00", linked_case_ids=("c-1",))
    base.update(over)
    return MailEvidence(**base)


def r21(snapshot: Snapshot) -> list:
    return plan_case_people(snapshot)


def test_a_person_who_wrote_on_the_case_becomes_one_of_its_people():
    actions = r21(Snapshot(evidence=(), cases=(case(people=("carla@cliente.test",)),), organizations=()))

    assert [a.evidence_id for a in actions] == [case_people_key("c-1", "carla@cliente.test")]
    action = actions[0]
    assert action.mode == AUTO and action.case_id == "c-1"
    assert [c["command"] for c in action.commands] == [ADD_CASE_SENDER_PARTICIPANT]
    assert action.commands[0]["inputs"]["email"] == "carla@cliente.test"
    assert action.context == {"case": "c-1", "case_version": 4}


def test_whom_we_wrote_to_from_the_case_counts_too():
    """The new address (a company renamed itself) is written to before it ever answers."""
    actions = r21(Snapshot(evidence=(sent(recipients=("Carla <carla@empresa-nueva.test>",)),),
                           cases=(case(),), organizations=()))

    assert [a.commands[0]["inputs"]["email"] for a in actions] == ["carla@empresa-nueva.test"]


def test_already_a_participant_is_not_added_again():
    snap = Snapshot(evidence=(sent(),), cases=(case(people=("carla@cliente.test",),
                                                    participants=("carla@cliente.test",)),),
                    organizations=())

    assert r21(snap) == []


def test_a_quitar_is_final_for_that_case_and_address():
    key = case_people_key("c-1", "carla@cliente.test")
    snap = Snapshot(evidence=(), cases=(case(people=("carla@cliente.test",)),), organizations=(),
                    applied_evidence_ids=frozenset({key}), applied_pairs=frozenset({(key, "R21")}))

    assert r21(snap) == []


def test_closed_cases_are_left_alone():
    snap = Snapshot(evidence=(), cases=(case(closed=True, stage="won", people=("carla@cliente.test",)),),
                    organizations=())

    assert r21(snap) == []


def test_own_labdelivery_supplier_and_robot_addresses_never_count():
    supplier = Organization(id="o-9", name="Fabricante Ficticio", version=1, confirmation="confirmed",
                            domains=("fabricante.test",), is_supplier=True)
    people = ("tatiana@origenlab.cl", "contacto@labdelivery.cl", "ventas@fabricante.test",
              "ana@eu.fabricante.test", "no-reply@cliente.test", "mailer-daemon@googlemail.com",
              "facturacion@cliente.test", "ana@cliente.test")
    actions = r21(Snapshot(evidence=(), cases=(case(people=people),), organizations=(supplier,)))

    assert [a.commands[0]["inputs"]["email"] for a in actions] == ["ana@cliente.test"]


def test_a_bulk_send_or_a_campaign_copy_names_no_one():
    many = tuple(f"p{i}@cliente.test" for i in range(6))
    snap = Snapshot(evidence=(sent(id="e-1", recipients=(", ".join(many),)),
                              sent(id="e-2", campaign_thread=True)),
                    cases=(case(),), organizations=())

    assert r21(snap) == []


def test_an_email_linked_to_two_cases_names_no_one():
    snap = Snapshot(evidence=(sent(linked_case_ids=("c-1", "c-2")),),
                    cases=(case(), case(id="c-2", thread_ids=())), organizations=())

    assert r21(snap) == []
