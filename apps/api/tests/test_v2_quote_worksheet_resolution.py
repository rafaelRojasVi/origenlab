"""Phase 3 worksheet resolution: normalisation, outcomes, grouping, intents. Invented fixtures only."""

from __future__ import annotations

import uuid

import pytest

from origenlab_api.v2 import quote_worksheet_resolution as w

A = "a" * 64
B = "b" * 64
C = "c" * 64
D = "d" * 64
R = "e" * 64  # a document already on the ledger
OPP_R = "11111111-2222-3333-4444-555555555555"

OPTIONS = {
    "direction": "yes_sent_to_client | no_not_a_customer_quotation | unsure",
    "grouping": "one_opportunity | one_per_document | split (list the documents per opportunity)",
    "same_client_other_thread": "same_opportunity_as:<doc> | new_opportunity",
    "serial_collision": "keep_both_as_separate_quotations | this_one_not_sent | leave_pending",
    "canonical_version": "version_of_same_quotation (canonical:<doc>) | exact_resend | separate_quotations",
    "identify_generic_client": "client:<name / organization> | leave_pending",
    "labdelivery_issuer": "origenlab_quotation | labdelivery_quotation_not_origenlab | leave_pending",
    "confirm_supplier_correspondence": "supplier_correspondence (no opportunity) | customer_quotation",
}


def row(qid, dtype, answer, docs, *, ctx=(), question="", cases=None):
    return {
        "question_id": qid, "decision_type": dtype, "answer": answer,
        "answer_options": OPTIONS.get(dtype, ""), "document_sha256": " ".join(docs),
        "context_documents": " ".join(ctx), "question": question,
        "case_ids": " ".join(cases if cases is not None else [f"doc:{d[:12]}" for d in docs]),
    }


def case(sha, needed=(), *, number="01001-26", sent="2026-05-01T10:00:00-04:00", same_serial=()):
    return w.Case(case_id=f"doc:{sha[:12]}", target="document", sha256=sha, email_id=1,
                  sent_at=sent, printed_number=number, widened_number=None,
                  decisions_needed=tuple(needed), same_serial_documents=tuple(same_serial))


INDEX = w.DocumentIndex([A, B, C, D, R])


def run(rows, cases, recorded=None, by_email=None):
    answers = w.normalize_worksheet(rows, INDEX)
    return w.resolve(answers, cases, recorded or {}, by_email)


# ─── normalisation ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rule", w.ANSWER_TABLE, ids=lambda r: f"{r.decision_type}:{r.raw}")
def test_every_table_form_normalises_to_its_value(rule):
    answer = rule.raw + ("x" * 12 + ")" if "(" in rule.raw else "Someone" if rule.param else "")
    found, _ = w.normalize_answer(rule.decision_type, answer)
    assert found.value == rule.value


def test_parenthetical_option_label_accepts_its_bare_token():
    rule, _ = w.normalize_answer("confirm_supplier_correspondence", "supplier_correspondence",
                                 OPTIONS["confirm_supplier_correspondence"])
    assert rule.value == w.V_SUPPLIER and not rule.extension


def test_unknown_answer_is_refused_not_guessed():
    with pytest.raises(w.UnknownAnswer):
        w.normalize_answer("direction", "probably_sent", OPTIONS["direction"])


def test_form_outside_options_needs_a_listed_extension():
    # W116 form: allowed only because it is a listed extension
    rule, _ = w.normalize_answer("same_client_other_thread", "not_applicable:labdelivery_quotation",
                                 OPTIONS["same_client_other_thread"])
    assert rule.value == w.V_NOT_ORIGENLAB and rule.extension.startswith("W116")
    rule, _ = w.normalize_answer("serial_collision", "keep_both_same_quotation_versions",
                                 OPTIONS["serial_collision"])
    assert rule.value == w.V_SAME_QUOTATION_VERSIONS and rule.extension.startswith("W103")
    # a table form of another question type is not borrowed
    with pytest.raises(w.UnknownAnswer):
        w.normalize_answer("direction", "new_opportunity", OPTIONS["direction"])


def test_extensions_are_reported_as_outside_answer_options():
    answers = w.normalize_worksheet([
        row("W1", "same_client_other_thread", "not_applicable:labdelivery_quotation", [A]),
        row("W2", "grouping", f"one_opportunity (canonical:{A[:12]})", [A, B]),
        row("W3", "grouping", "one_opportunity", [A, B]),
    ], INDEX)
    assert [a.in_answer_options for a in answers] == [False, False, True]
    assert answers[1].param == A


def test_document_references_must_resolve_to_exactly_one_hash():
    index = w.DocumentIndex(["abcdefabcdef" + "0" * 52, "abcdefabcdef" + "1" * 52])
    with pytest.raises(w.UnknownAnswer):
        index.resolve("abcdefabcdef")
    with pytest.raises(w.UnknownAnswer):
        index.resolve("ffffffffffff")


def test_policy_fingerprint_is_stable_and_tracks_the_table(monkeypatch):
    first = w.policy_fingerprint()
    assert first == w.policy_fingerprint()
    monkeypatch.setattr(w, "ANSWER_TABLE", w.ANSWER_TABLE[:-1])
    assert w.policy_fingerprint() != first


def test_policy_is_frozen_at_the_owner_approved_fingerprint():
    assert w.POLICY_VERSION == "W001-W117/2026-09-25.1"
    assert w.policy_fingerprint() == w.FROZEN_POLICY_SHA256
    assert w.FROZEN_POLICY_SHA256.startswith("41ea7f3b")
    w.assert_policy_frozen()


def test_any_change_to_the_resolution_rules_breaks_the_freeze(monkeypatch):
    monkeypatch.setattr(w, "OUTCOME_RULES", (*w.OUTCOME_RULES, ("R11", "new")))
    with pytest.raises(w.PolicyChanged):
        w.assert_policy_frozen()


def test_any_change_to_the_gates_breaks_the_freeze(monkeypatch):
    assert w.gates_fingerprint() == w.FROZEN_GATES_SHA256
    monkeypatch.setattr(w, "CRM_ONLY_BLOCKERS", frozenset())
    with pytest.raises(w.PolicyChanged):
        w.assert_policy_frozen()


# ─── outcomes ──────────────────────────────────────────────────────────────────────────────────


def test_grouped_documents_share_a_deterministic_planned_opportunity():
    rows = [row("W1", "grouping", "one_opportunity", [A, B])]
    first = run(rows, [case(A, ["grouping"]), case(B, ["grouping"], number="01002-26")])
    again = run(rows, [case(A, ["grouping"]), case(B, ["grouping"], number="01002-26")])
    key = w.planned_opportunity_id([A, B])
    assert first.documents[A].group == first.documents[B].group == key
    assert str(uuid.UUID(key)) == key
    assert w.resolution_digest(first, w.ledger_intents(first, {})) == \
        w.resolution_digest(again, w.ledger_intents(again, {}))
    assert all(r.automatable for r in first.documents.values())


def test_new_opportunity_joined_to_its_context_document_is_blocked():
    rows = [
        row("W1", "grouping", "one_opportunity", [A, B]),
        row("W2", "same_client_other_thread", "new_opportunity", [A], ctx=[B]),
    ]
    res = run(rows, [case(A, ["grouping", "same_client_other_thread"]), case(B, ["grouping"], number="01002-26")])
    assert [b.code for b in res.documents[A].blockers] == [w.B_NEW_OPP_JOINED]


def test_generic_client_pending_holds_the_crm_not_the_ready_documents_ledger_entry():
    # G3: the unblocked document is recorded on its own; the opportunity is not promoted.
    rows = [
        row("W1", "grouping", "one_opportunity", [A, B]),
        row("W2", "identify_generic_client", "leave_pending", [B]),
    ]
    res = run(rows, [case(A, ["grouping"]), case(B, ["grouping", "identify_generic_client"], number="01002-26")])
    assert res.documents[B].outcome == w.O_PENDING
    assert [b.code for b in res.documents[B].blockers] == [w.B_CLIENT_PENDING]
    assert [b.code for b in res.documents[A].blockers] == [w.B_GROUP_PENDING]
    assert res.documents[A].automatable and not res.documents[B].automatable
    (intent,) = w.ledger_intents(res, {})
    assert (intent.target, intent.opportunity_mode) == (A, "create_new")
    assert intent.opportunity_id == w.planned_opportunity_id([A, B])
    assert w.crm_holds(res) == {w.planned_opportunity_id([A, B]): (B,)}


def test_collision_blocked_member_does_not_hold_its_siblings_ledger_entries():
    rows = [
        row("W1", "grouping", "one_opportunity", [A, C]),
        row("W2", "serial_collision", "keep_both_as_separate_quotations", [A, B]),
        row("W3", "same_client_other_thread", "new_opportunity", [B], ctx=[A]),
    ]
    res = run(rows, [case(A, ["grouping", "serial_collision"]), case(B, ["serial_collision"]),
                     case(C, ["grouping"], number="01003-26")])
    assert not res.documents[A].automatable and res.documents[C].automatable
    assert [i.target for i in w.ledger_intents(res, {})] == [C]
    holds = w.crm_holds(res)
    assert holds[res.documents[C].group] == (A,)


def test_kept_serial_collision_blocks_both_numbers_for_the_owner():
    rows = [
        row("W1", "serial_collision", "keep_both_as_separate_quotations", [A, B]),
        row("W2", "same_client_other_thread", "new_opportunity", [A], ctx=[B]),
    ]
    res = run(rows, [case(A, ["serial_collision"]), case(B, ["serial_collision"])])
    assert res.documents[A].group != res.documents[B].group
    for sha in (A, B):
        assert w.B_NUMBER_OTHER_OPP in {b.code for b in res.documents[sha].blockers}


def test_collision_keeps_the_printed_number_with_a_per_opportunity_identity():
    # G1: same printed number, two opportunities → two distinct identities, no suffix, still blocked.
    rows = [
        row("W1", "serial_collision", "keep_both_as_separate_quotations", [A, B]),
        row("W2", "same_client_other_thread", "new_opportunity", [A], ctx=[B]),
    ]
    res = run(rows, [case(A, ["serial_collision"]), case(B, ["serial_collision"])])
    ids = {w.quote_identity(res.documents[s]) for s in (A, B)}
    assert {i[0] for i in ids} == {"01001-26"}
    assert len({(i[0], i[1]) for i in ids}) == 2
    assert w.ledger_intents(res, {}) == []
    assert w.BLOCKER_GATE[w.B_NUMBER_OTHER_OPP][0] == "G1"
    assert w.B_NUMBER_OTHER_OPP not in w.CRM_ONLY_BLOCKERS


def test_document_without_printed_number_stays_in_human_review():
    # G2: nothing reads a number from the file name.
    res = run([row("W1", "read_new_template", "real_sent_quotations", [A])],
              [case(A, ["read_new_template"], number=None)])
    assert [b.code for b in res.documents[A].blockers] == [w.B_NO_NUMBER]
    assert res.documents[A].quote_number is None and w.quote_identity(res.documents[A]) is None
    assert w.ledger_intents(res, {}) == []


def test_collision_is_covered_by_the_counterpart_answer():
    rows = [row("W1", "serial_collision", "keep_both_as_separate_quotations", [A])]
    res = run(rows, [case(A, ["serial_collision"]), case(B, ["serial_collision"], number="01009-26", same_serial=[A])])
    assert w.B_UNANSWERED not in {b.code for b in res.documents[B].blockers}


def test_needed_decision_without_answer_blocks():
    res = run([row("W1", "direction", "yes_sent_to_client", [A])], [case(A, ["direction", "grouping"])])
    assert [(b.code, b.detail) for b in res.documents[A].blockers] == [(w.B_UNANSWERED, "grouping")]


def test_version_family_reaches_the_recorded_document_through_the_named_email():
    recorded = {R: w.Recorded("confirm_customer_quotation", "01001-26", OPP_R, "create_new", "rec-1")}
    rows = [row("W1", "canonical_version", f"version_of_same_quotation (canonical:{A[:12]})", [A],
                question="Is it a revision of the confirmed 01001-26 for the same client (email 700001)?")]
    res = run(rows, [case(A, ["canonical_version"])], recorded, {700001: [R]})
    doc = res.documents[A]
    assert (doc.outcome, doc.version_role, doc.group) == (w.O_CONFIRM, "canonical", OPP_R)
    assert doc.automatable
    (intent,) = w.ledger_intents(res, {OPP_R: "planned"})
    assert (intent.opportunity_mode, intent.opportunity_id) == ("planned", OPP_R)


def test_prior_version_is_confirmed_on_the_canonical_opportunity():
    rows = [row("W1", "canonical_version", f"version_of_same_quotation (canonical:{B[:12]})", [A, B])]
    res = run(rows, [case(A, ["canonical_version"], sent="2026-05-01T10:00:00-04:00"),
                     case(B, ["canonical_version"], sent="2026-05-02T10:00:00-04:00")])
    assert res.documents[A].outcome == w.O_CONFIRM_PRIOR
    assert res.documents[B].outcome == w.O_CONFIRM
    first, second = w.ledger_intents(res, {})
    assert (first.target, first.opportunity_mode) == (A, "create_new")
    assert (second.target, second.opportunity_mode) == (B, "planned")
    assert first.opportunity_id == second.opportunity_id


def test_two_canonicals_in_one_family_block():
    rows = [
        row("W1", "canonical_version", f"version_of_same_quotation (canonical:{A[:12]})", [A, B]),
        row("W2", "canonical_version", f"version_of_same_quotation (canonical:{B[:12]})", [A, B]),
    ]
    res = run(rows, [case(A, ["canonical_version"]), case(B, ["canonical_version"])])
    assert all(w.B_CANONICAL_CONFLICT in {b.code for b in r.blockers} for r in res.documents.values())


def test_delivered_labdelivery_quotation_is_rejected_not_contradictory():
    rows = [
        row("W1", "labdelivery_issuer", "labdelivery_quotation_not_origenlab", [A]),
        row("W2", "direction", "yes_sent_to_client", [A]),
        row("W3", "same_client_other_thread", "not_applicable:labdelivery_quotation", [A], ctx=[B]),
    ]
    res = run(rows, [case(A, ["labdelivery_issuer", "direction", "same_client_other_thread"])])
    assert res.documents[A].outcome == w.O_REJECT_NOT_ORIGENLAB and res.documents[A].automatable
    (intent,) = w.ledger_intents(res, {})
    assert intent.decision == "reject_non_quotation" and intent.opportunity_id is None


def test_not_origenlab_with_a_kept_number_is_a_contradiction():
    rows = [
        row("W1", "labdelivery_issuer", "labdelivery_quotation_not_origenlab", [A]),
        row("W2", "serial_collision", "keep_both_as_separate_quotations", [A]),
    ]
    res = run(rows, [case(A, ["labdelivery_issuer", "serial_collision"])])
    assert res.documents[A].outcome == w.O_CONTRADICTION and not res.documents[A].automatable


def test_value_without_phase3_semantics_blocks_instead_of_being_read():
    res = run([row("W1", "canonical_version", "exact_resend", [A, B])],
              [case(A, ["canonical_version"]), case(B, ["canonical_version"])])
    assert all(w.B_NO_SEMANTICS in {b.code for b in r.blockers} for r in res.documents.values())


def test_supplier_email_case_becomes_an_email_ledger_rejection():
    email_case = w.Case(case_id="email:700009", target="email", sha256=None, email_id=700009,
                        sent_at="", printed_number=None, widened_number=None,
                        decisions_needed=("confirm_supplier_correspondence",))
    res = run([row("W1", "confirm_supplier_correspondence", "supplier_correspondence", [],
                   cases=["email:700009"])], [email_case])
    (intent,) = w.ledger_intents(res, {})
    assert (intent.ledger, intent.target, intent.decision) == ("decisions.jsonl", "700009", "reject_non_quotation")


def test_replay_after_apply_proposes_nothing():
    rows = [row("W1", "grouping", "one_opportunity", [A, B]),
            row("W2", "labdelivery_issuer", "labdelivery_quotation_not_origenlab", [C])]
    cases = [case(A, ["grouping"]), case(B, ["grouping"], number="01002-26"), case(C, ["labdelivery_issuer"])]
    res = run(rows, cases)
    intents = w.ledger_intents(res, {})
    assert len(intents) == 3 and len({i.decision_id for i in intents}) == 3
    applied = {
        i.target: w.Recorded(
            "confirm_customer_quotation" if i.decision.startswith("confirm") else i.decision,
            i.quote_number, i.opportunity_id, i.opportunity_mode, i.decision_id)
        for i in intents
    }
    again = run(rows, cases, applied)
    assert w.ledger_intents(again, {i.opportunity_id: "planned" for i in intents if i.opportunity_id}) == []


def test_ledger_disagreement_blocks_the_document():
    recorded = {A: w.Recorded("reject_non_quotation", None, None, None, "rec-2")}
    res = run([row("W1", "direction", "yes_sent_to_client", [A])], [case(A, ["direction"])], recorded)
    assert w.B_RECORDED_DIFFERS in {b.code for b in res.documents[A].blockers}
