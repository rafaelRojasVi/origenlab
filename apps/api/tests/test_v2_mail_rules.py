"""The email → cases rules (spec 2026-10-05 §3): pure planning, no database.

Every address, domain, name and number here is fictitious (`.test`, `.invalid`, `ejemplo.cl`).
"""

from __future__ import annotations

from origenlab_api.v2.mail_rules import (
    CaseQuote,
    CaseState,
    MailDocument,
    MailEvidence,
    Organization,
    QuoteRevision,
    Snapshot,
    canonical_quote_number,
    gmail_quote_key,
    plan,
    quote_key,
)

PDF_SHA = "a" * 64
OTHER_SHA = "b" * 64


def mail(**over) -> MailEvidence:
    base = dict(
        id="e-1",
        thread_id="t-1",
        direction="outbound",
        sender="Ventas <contacto@origenlab.cl>",
        recipients=("Compras <compras@cliente.test>",),
        subject="Cotización equipos",
        sent_at="2026-10-01T15:00:00+00:00",
        documents=(MailDocument(filename="CN012395-cliente.pdf", sha256=PDF_SHA, cn_tokens=("CN12395",)),),
    )
    base.update(over)
    return MailEvidence(**base)


def org(**over) -> Organization:
    base = dict(id="o-1", name="Cliente Ficticio", version=3, confirmation="confirmed",
                domains=("cliente.test",), is_supplier=False)
    base.update(over)
    return Organization(**base)


def case(**over) -> CaseState:
    base = dict(id="c-1", title="Caso ficticio", stage="quoting", version=7, organization_id="o-1",
                closed=False, thread_ids=(), quotes=())
    base.update(over)
    return CaseState(**base)


def quote(number="01200-26", *revisions: QuoteRevision) -> CaseQuote:
    revs = revisions or (QuoteRevision(id="r-1", revision_no=1, status="sent", version=1, superseded=False),)
    return CaseQuote(id=f"q-{number}", quote_number=number, revisions=revs)


def only(snapshot: Snapshot):
    actions = plan(snapshot)
    assert len(actions) == 1, actions
    return actions[0]


# ------------------------------------------------------------------ quote numbers


def test_quote_key_reads_typed_numbers_like_the_dashboard() -> None:
    assert quote_key("01246-26", None) == (26, 1246)
    assert quote_key("1013-26", None) == (26, 1013)
    assert quote_key("012392-26", None) == (26, 1239)
    assert quote_key("011728A-25", None) == (25, 1172)
    assert quote_key("CN01247", 26) == (26, 1247)
    assert quote_key("CN01247", None) is None
    assert quote_key("sin número", 26) is None


def test_a_gmail_token_missing_its_leading_zero_reads_as_the_0xxxx_series() -> None:
    # «CN012395-….pdf» is captured as CN12395: correlative 01239, revision 5.
    assert gmail_quote_key("CN12395", "2026-10-01T15:00:00+00:00") == (26, 1239)
    assert gmail_quote_key("CN01247", "2026-10-01T15:00:00+00:00") == (26, 1247)


def test_the_gmail_year_is_the_santiago_year() -> None:
    # 2027-01-01 02:00 UTC is still 31 December in Santiago.
    assert gmail_quote_key("CN01247", "2027-01-01T02:00:00+00:00") == (26, 1247)


def test_canonical_number_is_five_digits_and_a_year() -> None:
    assert canonical_quote_number((26, 1239)) == "01239-26"


# ------------------------------------------------------------------ R1 / R2


def test_r1_links_an_email_on_a_thread_already_linked_to_one_case() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@cliente.test", recipients=("contacto@origenlab.cl",),
                       documents=()),),
        cases=(case(thread_ids=("t-1",)),),
        organizations=(org(),),
    ))
    assert action.rule_id == "R1"
    assert action.mode == "auto"
    assert action.case_id == "c-1"
    assert [c["command"] for c in action.commands] == ["link_case_evidence"]
    assert action.commands[0]["inputs"]["source_record_id"] == "e-1"
    assert action.reasons


def test_r1_with_two_candidate_cases_is_only_a_proposal() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@cliente.test", documents=()),),
        cases=(case(thread_ids=("t-1",)), case(id="c-2", thread_ids=("t-1",))),
        organizations=(org(),),
    ))
    assert action.rule_id == "R1"
    assert action.mode == "proposal"
    assert set(action.candidates) == {"c-1", "c-2"}
    assert action.commands == ()


def test_r1_never_links_to_a_closed_case() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@cliente.test", documents=()),),
        cases=(case(stage="lost", closed=True, thread_ids=("t-1",)),),
        organizations=(org(),),
    ))
    assert action.mode == "none"
    assert action.rule_id == "R8"


def test_r2_links_by_quote_number_with_the_lost_leading_zero() -> None:
    action = only(Snapshot(
        evidence=(mail(thread_id="t-new"),),
        cases=(case(quotes=(quote("01239-26"),)),),
        organizations=(org(),),
    ))
    assert action.rule_id == "R2"
    assert action.mode == "auto"
    assert action.case_id == "c-1"
    assert action.quote_number == "01239-26"


def test_r2_on_two_cases_is_a_proposal() -> None:
    action = only(Snapshot(
        evidence=(mail(thread_id="t-new"),),
        cases=(case(quotes=(quote("01239-26"),)), case(id="c-2", quotes=(quote("1239-26"),))),
        organizations=(org(),),
    ))
    assert action.rule_id == "R2"
    assert action.mode == "proposal"


def test_a_free_mail_sender_may_still_link_through_r1() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="alguien@gmail.com", documents=()),),
        cases=(case(thread_ids=("t-1",)),),
        organizations=(),
    ))
    assert (action.rule_id, action.mode) == ("R1", "auto")


def test_r1_records_a_new_sent_quote_on_the_existing_thread() -> None:
    action = only(Snapshot(
        evidence=(mail(thread_id="t-1"),),
        cases=(case(stage="qualified", thread_ids=("t-1",)),),
        organizations=(org(),),
    ))
    assert (action.rule_id, action.mode, action.quote_number) == ("R1", "auto", "01239-26")
    assert action.context["from_stage"] == "qualified"
    assert [c["command"] for c in action.commands] == [
        "link_case_evidence", "advance_case_stage", "record_historical_quotation",
    ]
    assert action.commands[1]["inputs"]["stage"] == "quoting"
    assert action.commands[-1]["inputs"]["document_sha256"] == PDF_SHA


def test_r1_does_not_record_a_sent_quote_until_the_requester_is_resolved() -> None:
    action = only(Snapshot(
        evidence=(mail(thread_id="t-1"),),
        cases=(case(stage="lead", organization_id=None, thread_ids=("t-1",)),),
        organizations=(),
    ))
    assert (action.rule_id, action.mode, action.quote_number) == ("R1", "proposal", "01239-26")
    assert any("institución solicitante" in reason for reason in action.reasons)
    assert action.commands == ()


# ------------------------------------------------------------------ R3 / R4


def test_r3_opens_a_case_for_a_known_institution_with_a_new_number() -> None:
    action = only(Snapshot(evidence=(mail(),), cases=(), organizations=(org(),)))
    assert action.rule_id == "R3"
    assert action.mode == "auto"
    assert action.organization_id == "o-1"
    assert action.quote_number == "01239-26"
    names = [c["command"] for c in action.commands]
    assert names == [
        "open_commercial_case", "add_case_organization",
        "advance_case_stage", "advance_case_stage", "advance_case_stage",
        "record_historical_quotation",
    ]
    stages = [c["inputs"]["stage"] for c in action.commands if c["command"] == "advance_case_stage"]
    assert stages == ["qualifying", "qualified", "quoting"]
    record = action.commands[-1]["inputs"]
    assert record["quote_number"] == "01239-26"
    # Only the token is printed (in the file name); the canonical number is derived, and says so.
    assert record["printed_quote_numbers"] == ["CN12395"]
    assert record["quote_number_derivation"] == {
        "token": "CN12395", "restored_leading_zero": True, "year_from_sent_at": 26}
    assert record["document_sha256"] == PDF_SHA
    assert record["origin_source_record_id"] == "e-1"
    add = action.commands[1]["inputs"]
    assert add["role"] == "requesting_institution"
    assert add["organization_id"] == "o-1"
    assert add["organization_version"] == 3


def test_r3_needs_a_pdf() -> None:
    action = only(Snapshot(
        evidence=(mail(documents=(MailDocument(filename="CN012395.xlsx", sha256=PDF_SHA, cn_tokens=("CN12395",)),)),),
        cases=(), organizations=(org(),),
    ))
    assert action.mode == "none"


def test_r4_creates_an_institution_por_confirmar_from_an_unknown_domain() -> None:
    action = only(Snapshot(
        evidence=(mail(recipients=("Laboratorio <lab@nuevo-cliente.test>",)),),
        cases=(), organizations=(org(),),
    ))
    assert action.rule_id == "R4"
    assert action.mode == "auto"
    assert action.proposed_domain == "nuevo-cliente.test"
    names = [c["command"] for c in action.commands]
    assert names[0] == "register_mail_organization"
    assert action.commands[0]["inputs"]["domain"] == "nuevo-cliente.test"
    assert names[1] == "open_commercial_case"
    assert action.commands[2]["inputs"]["organization_id"] == "@organization"


def test_no_auto_create_for_free_mail_recipients() -> None:
    action = only(Snapshot(evidence=(mail(recipients=("alguien@hotmail.com",)),), cases=(), organizations=()))
    assert action.rule_id == "R8"
    assert action.mode == "none"
    assert any("gratuito" in r for r in action.reasons)


def test_no_auto_create_for_a_supplier_domain() -> None:
    action = only(Snapshot(
        evidence=(mail(recipients=("ventas@proveedor.test",)),),
        cases=(),
        organizations=(org(id="o-s", name="Proveedor", domains=("proveedor.test",), is_supplier=True),),
    ))
    assert action.rule_id == "R8"
    assert any("proveedor" in r for r in action.reasons)


def test_two_known_institutions_among_the_recipients_is_a_proposal() -> None:
    action = only(Snapshot(
        evidence=(mail(recipients=("a@cliente.test", "b@otro.test")),),
        cases=(),
        organizations=(org(), org(id="o-2", name="Otro", domains=("otro.test",))),
    ))
    assert action.rule_id == "R3"
    assert action.mode == "proposal"


def test_labdelivery_mentions_stay_in_review() -> None:
    action = only(Snapshot(
        evidence=(mail(subject="Fwd: pedido Labdelivery"),),
        cases=(), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R8", "none")
    assert any("Labdelivery" in r for r in action.reasons)


def test_a_campaign_thread_is_never_processed() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@cliente.test", documents=(), campaign_thread=True),),
        cases=(case(thread_ids=("t-1",)),), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R8", "none")


# ------------------------------------------------------------------ R5 / R6 / R7


def test_r5_wins_a_case_on_a_purchase_order() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="compras@cliente.test", subject="Orden de compra",
                       documents=(MailDocument(filename="OC 4500123.pdf", sha256=OTHER_SHA, cn_tokens=()),)),),
        cases=(case(thread_ids=("t-1",), quotes=(quote("01200-26"),)),),
        organizations=(org(),),
    ))
    assert action.rule_id == "R5"
    assert action.mode == "auto"
    names = [c["command"] for c in action.commands]
    assert names == ["link_case_evidence", "advance_case_stage", "record_case_won"]
    assert action.commands[1]["inputs"]["stage"] == "negotiating"
    won = action.commands[-1]["inputs"]
    assert won["quote_revision_id"] == "r-1"
    assert won["purchase_order"] == "OC 4500123"
    assert action.context["from_stage"] == "quoting"


def test_r5_with_two_quotes_on_the_case_is_a_proposal() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="compras@cliente.test", subject="Orden de compra", documents=()),),
        cases=(case(thread_ids=("t-1",), quotes=(quote("01200-26"), quote("01201-26"))),),
        organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R5", "proposal")


def test_r5_ignores_a_void_revision() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="compras@cliente.test", subject="OC 991",
                       documents=(MailDocument(filename="OC_991.pdf", sha256=OTHER_SHA, cn_tokens=()),)),),
        cases=(case(stage="negotiating", thread_ids=("t-1",), quotes=(quote(
            "01200-26",
            QuoteRevision(id="r-1", revision_no=1, status="void", version=2, superseded=False),
            QuoteRevision(id="r-2", revision_no=2, status="sent", version=1, superseded=False),
        ),)),),
        organizations=(org(),),
    ))
    assert action.rule_id == "R5"
    assert [c["command"] for c in action.commands] == ["link_case_evidence", "record_case_won"]
    assert action.commands[-1]["inputs"]["quote_revision_id"] == "r-2"


def test_r6_loses_a_case_to_another_supplier() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@cliente.test", documents=(),
                       subject="RE: Cotización — ya fue gestionada por otro proveedor"),),
        cases=(case(thread_ids=("t-1",)),), organizations=(org(),),
    ))
    assert action.rule_id == "R6"
    assert action.mode == "auto"
    assert [c["command"] for c in action.commands] == ["link_case_evidence", "advance_case_stage"]
    assert action.commands[0]["inputs"]["relation"] == "contradicts"
    assert action.commands[1]["inputs"]["stage"] == "lost"
    assert action.commands[1]["inputs"]["close_reason"] == "otro proveedor"


def test_r7_a_quote_request_from_a_known_institution_opens_an_en_estudio_case() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@cliente.test", thread_id="t-9",
                       subject="Solicitud de cotización balanza", documents=()),),
        cases=(), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R7", "auto")
    assert action.organization_id == "o-1"
    assert [c["command"] for c in action.commands] == [
        "open_commercial_case", "add_case_organization", "advance_case_stage", "advance_case_stage",
    ]
    assert [c["inputs"]["stage"] for c in action.commands if c["command"] == "advance_case_stage"] == [
        "qualifying", "qualified",
    ]


def test_r7_free_mail_quote_request_opens_solicitada_without_inventing_an_institution() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="Irina Example <irina@gmail.com>", thread_id="t-irina",
                       subject="Solicitud de cotización de productos", documents=()),),
        cases=(), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R7", "auto")
    assert action.organization_id is None
    assert len(action.commands) == 1
    assert action.commands[0]["command"] == "open_commercial_case"
    assert "Solicitud de cotización" in action.case_title
    assert any("sin institución" in reason for reason in action.reasons)


def test_r7_unknown_domain_quote_request_also_opens_solicitada_without_guessing_identity() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="compras@nuevo.test", thread_id="t-new",
                       subject="RFQ centrífuga", documents=()),),
        cases=(), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R7", "auto")
    assert action.organization_id is None
    assert [c["command"] for c in action.commands] == ["open_commercial_case"]


def test_anything_else_is_r8() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@desconocido.test", thread_id="t-9",
                       subject="Hola", documents=()),),
        cases=(), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R8", "none")


# ------------------------------------------------------------------ idempotency, determinism


def test_an_email_already_handled_by_a_rule_plans_nothing() -> None:
    snapshot = Snapshot(evidence=(mail(),), cases=(), organizations=(org(),), applied_evidence_ids=frozenset({"e-1"}))
    assert plan(snapshot) == []


def test_an_email_already_linked_by_a_person_plans_nothing() -> None:
    snapshot = Snapshot(evidence=(mail(linked_case_ids=("c-1",)),), cases=(case(),), organizations=(org(),))
    assert plan(snapshot) == []


def test_planning_is_deterministic() -> None:
    snapshot = Snapshot(
        evidence=(mail(id="e-2", thread_id="t-2"), mail()),
        cases=(), organizations=(org(),),
    )
    first = plan(snapshot)
    assert [a.evidence_id for a in first] == ["e-1", "e-2"]
    assert plan(snapshot) == first


def test_two_new_emails_with_the_same_new_number_open_one_case_and_propose_the_other() -> None:
    snapshot = Snapshot(evidence=(mail(), mail(id="e-2", thread_id="t-2")), cases=(), organizations=(org(),))
    first, second = plan(snapshot)
    assert (first.rule_id, first.mode) == ("R3", "auto")
    assert second.mode == "proposal"
    assert any("misma cotización" in r for r in second.reasons)


# ------------------------------------------------------------------ review fixes (C2, M1, M3, I2)

LINKED = dict(cases=(case(thread_ids=("t-1",), quotes=(quote("01200-26"),)),),
              organizations=(org(), org(id="o-s", name="Proveedor", domains=("proveedor.test",), is_supplier=True)))
OC_PDF = (MailDocument(filename="OC 4500123.pdf", sha256=OTHER_SHA, cn_tokens=()),)


def test_r5_subject_only_is_a_proposal() -> None:
    action = only(Snapshot(evidence=(mail(direction="inbound", sender="compras@cliente.test",
                                          subject="Orden de compra 4500123", documents=()),), **LINKED))
    assert (action.rule_id, action.mode) == ("R5", "proposal")
    assert any("adjunto" in r for r in action.reasons)


def test_r5_a_supplier_confirming_an_oc_is_a_proposal() -> None:
    action = only(Snapshot(evidence=(mail(direction="inbound", sender="ventas@proveedor.test",
                                          subject="Confirmacion OC 4500123", documents=OC_PDF),), **LINKED))
    assert (action.rule_id, action.mode) == ("R5", "proposal")


def test_r5_an_internal_forward_of_an_oc_is_a_proposal() -> None:
    action = only(Snapshot(evidence=(mail(direction="inbound", sender="ventas@origenlab.cl",
                                          subject="RV: OC 4500123", documents=OC_PDF),), **LINKED))
    assert (action.rule_id, action.mode) == ("R5", "proposal")


def test_r5_from_another_institution_is_a_proposal() -> None:
    action = only(Snapshot(evidence=(mail(direction="inbound", sender="x@otra.test",
                                          subject="OC", documents=OC_PDF),), **LINKED))
    assert (action.rule_id, action.mode) == ("R5", "proposal")


def test_r5_a_free_mail_sender_on_the_linked_thread_with_the_oc_attached_wins() -> None:
    action = only(Snapshot(evidence=(mail(direction="inbound", sender="persona@gmail.com",
                                          subject="te mando la oc", documents=OC_PDF),), **LINKED))
    assert (action.rule_id, action.mode) == ("R5", "auto")


def test_r6_from_a_supplier_is_a_proposal() -> None:
    action = only(Snapshot(evidence=(mail(direction="inbound", sender="ventas@proveedor.test", documents=(),
                                          subject="ya fue gestionada por otro proveedor"),), **LINKED))
    assert (action.rule_id, action.mode) == ("R6", "proposal")


def test_r3_needs_contacto_as_sender() -> None:
    action = only(Snapshot(evidence=(mail(sender="ventas@origenlab.cl"),), cases=(), organizations=(org(),)))
    assert action.mode == "none"


def test_r3_a_supplier_rfq_or_internal_hint_is_not_outbound() -> None:
    for hint in ("supplier_rfq", "internal_only"):
        action = only(Snapshot(evidence=(mail(direction_hint=hint),), cases=(), organizations=(org(),)))
        assert action.mode == "none", hint


def test_r4_claims_the_domain_within_one_pass() -> None:
    first, second = plan(Snapshot(evidence=(
        mail(recipients=("a@nuevo.test",)),
        mail(id="e-2", thread_id="t-2", recipients=("b@nuevo.test",),
             documents=(MailDocument(filename="CN01300.pdf", sha256=OTHER_SHA, cn_tokens=("CN01300",)),)),
    ), cases=(), organizations=()))
    assert (first.rule_id, first.mode) == ("R4", "auto")
    assert second.mode == "proposal"


def test_a_yearless_crm_number_matches_by_its_revision_year_only_as_a_proposal() -> None:
    rev = QuoteRevision(id="r-1", revision_no=1, status="sent", version=1, superseded=False,
                        sent_at="2026-03-01T12:00:00+00:00")
    action = only(Snapshot(evidence=(mail(thread_id="t-new"),),
                           cases=(case(quotes=(quote("CN01239", rev),)),), organizations=(org(),)))
    assert (action.rule_id, action.mode) == ("R2", "proposal")


def test_a_correlative_already_used_last_year_is_a_proposal_not_a_new_case() -> None:
    action = only(Snapshot(evidence=(mail(thread_id="t-new"),),
                           cases=(case(quotes=(quote("01239-25"),)),), organizations=(org(),)))
    assert action.rule_id in ("R2", "R3")
    assert action.mode == "proposal"
    assert any("2025" in r or "-25" in r for r in action.reasons)


def test_cn12395_and_cn01239_are_the_same_quote_in_one_pass() -> None:
    first, second = plan(Snapshot(evidence=(
        mail(),
        mail(id="e-2", thread_id="t-2",
             documents=(MailDocument(filename="CN01239.pdf", sha256=OTHER_SHA, cn_tokens=("CN01239",)),)),
    ), cases=(), organizations=(org(),)))
    assert (first.mode, first.quote_number) == ("auto", "01239-26")
    assert (second.mode, second.quote_number) == ("proposal", "01239-26")


def test_r1_defers_to_a_quote_number_that_belongs_to_another_case() -> None:
    # Golden-set finding: a thread linked to one case carried the quote of a sibling case.
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="x@cliente.test", thread_id="t-1"),),
        cases=(case(thread_ids=("t-1",)), case(id="c-2", quotes=(quote("01239-26"),))),
        organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R1", "proposal")
    assert set(action.candidates) == {"c-1", "c-2"}
