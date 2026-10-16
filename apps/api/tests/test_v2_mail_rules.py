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
    quote_file_institution,
    RULE_IDS,
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


def test_r2_reads_the_quote_number_from_the_subject_when_no_pdf_names_one() -> None:
    """A client answering about «cotización N°01259-26» on a fresh thread, with no attachment:
    the number names exactly one open case, so the email links to it (previously an R7 review)."""
    action = only(Snapshot(
        evidence=(mail(direction="inbound", thread_id="t-new", sender="Irina <persona@gmail.com>",
                       subject="Consulta_Compra de productos_ cotización N°01259-26", documents=()),),
        cases=(case(quotes=(quote("01259-26"),)),),
        organizations=(org(),),
    ))
    assert action.rule_id == "R2"
    assert action.mode == "auto"
    assert action.case_id == "c-1"
    assert action.quote_number == "01259-26"


def test_a_pdf_quote_number_outranks_the_subject_number() -> None:
    """«Re: Cotización 01198-26» carrying CN01240: the PDF is the quote being sent; the subject
    is the thread's old title. Only the PDF's number is matched."""
    action = only(Snapshot(
        evidence=(mail(thread_id="t-new", subject="Re: Cotización 01198-26 — cliente",
                       documents=(MailDocument(filename="CN01240-cliente.pdf", sha256=PDF_SHA, cn_tokens=("CN1240",)),)),),
        cases=(case(quotes=(quote("01198-26"),)), case(id="c-2", quotes=(quote("01240-26"),))),
        organizations=(org(),),
    ))
    assert action.rule_id == "R2"
    assert action.case_id == "c-2"


def test_a_thread_r1_knows_keeps_its_link_whatever_the_inherited_subject_names() -> None:
    """A reply on case c-1's thread titled after another case's quote (no PDF): R1 links to c-1
    as before this rule; the subject is never read on a known thread."""
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="compras@cliente.test", thread_id="t-1",
                       subject="Re: Cotización 01260-26 — cliente", documents=()),),
        cases=(case(thread_ids=("t-1",), quotes=(quote("01259-26"),)),
               case(id="c-2", quotes=(quote("01260-26"),))),
        organizations=(org(),),
    ))
    assert action.rule_id == "R1"
    assert action.mode == "auto"
    assert action.case_id == "c-1"


def test_a_cn_subject_token_missing_its_leading_zero_links_the_0xxxx_quote() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", thread_id="t-new", sender="compras@cliente.test",
                       subject="Seguimiento CN12395", documents=()),),
        cases=(case(quotes=(quote("012395-26"),)),),
        organizations=(org(),),
    ))
    assert action.rule_id == "R2" and action.case_id == "c-1"


def test_a_subject_number_of_a_closed_case_or_an_unknown_number_does_not_link() -> None:
    closed = only(Snapshot(
        evidence=(mail(direction="inbound", thread_id="t-new", sender="Irina <persona@gmail.com>",
                       subject="Consulta cotización N°01259-26", documents=()),),
        cases=(case(stage="abandoned", closed=True, quotes=(quote("01259-26"),)),),
        organizations=(org(),),
    ))
    assert closed.rule_id != "R2"
    # A purchase-order reference is not a quote number, even when it carries one.
    po = only(Snapshot(
        evidence=(mail(direction="inbound", thread_id="t-new", sender="compras@cliente.test",
                       subject="OC-01259-26 aprobada", documents=()),),
        cases=(case(quotes=(quote("01259-26"),)),),
        organizations=(org(),),
    ))
    assert po.rule_id != "R2"
    # A bare correlative without a year stays an R7 review, as before.
    bare = only(Snapshot(
        evidence=(mail(direction="inbound", thread_id="t-new", sender="Irina <persona@gmail.com>",
                       subject="Consulta Cotización 1259", documents=()),),
        cases=(case(quotes=(quote("01259-26"),)),),
        organizations=(org(),),
    ))
    assert bare.rule_id == "R7" and bare.mode == "proposal"
    # A supplier-style six-digit document number is not an OrigenLab correlative.
    six = only(Snapshot(
        evidence=(mail(direction="inbound", thread_id="t-new", sender="ventas@proveedor.test",
                       subject="Cotización 123553", documents=()),),
        cases=(case(quotes=(quote("12355-26"),)),),
        organizations=(org(),),
    ))
    assert six.rule_id != "R2"


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


TWO_QUOTES = (
    MailDocument(filename="CN01270-cliente-T18.pdf", sha256="b" * 64, cn_tokens=("CN01270",)),
    MailDocument(filename="CN01271-cliente-T10.pdf", sha256="c" * 64, cn_tokens=("CN01271",)),
    MailDocument(filename="ficha-tecnica.pdf", sha256="d" * 64, cn_tokens=()),
)


def test_r1_records_every_new_sent_quote_of_one_email_on_the_thread_s_case() -> None:
    # Two alternatives (T18, T10) sent together on the case's own thread: both are recorded.
    action = only(Snapshot(
        evidence=(mail(thread_id="t-1", documents=TWO_QUOTES),),
        cases=(case(stage="lead", thread_ids=("t-1",)),),
        organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R1", "auto")
    names = [c["command"] for c in action.commands]
    assert names == ["link_case_evidence", "advance_case_stage", "advance_case_stage", "advance_case_stage",
                     "record_historical_quotation", "record_historical_quotation"]
    records = [c["inputs"] for c in action.commands if c["command"] == "record_historical_quotation"]
    assert [(r["quote_number"], r["document_sha256"]) for r in records] == [("01270-26", "b" * 64), ("01271-26", "c" * 64)]
    assert len({r["ledger_decision_id"] for r in records}) == 2
    assert action.quote_number == "01270-26"
    assert any("01270-26" in reason and "01271-26" in reason for reason in action.reasons)


def test_r1_records_only_the_new_numbers_when_one_of_them_is_already_known() -> None:
    action = only(Snapshot(
        evidence=(mail(thread_id="t-1", documents=TWO_QUOTES),),
        cases=(case(stage="quoting", thread_ids=("t-1",), quotes=(quote("01270-26"),)),),
        organizations=(org(),),
    ))
    assert (action.rule_id, action.mode, action.quote_number) == ("R1", "auto", "01271-26")
    records = [c["inputs"]["quote_number"] for c in action.commands if c["command"] == "record_historical_quotation"]
    assert records == ["01271-26"]


def test_r1_still_asks_when_one_of_several_numbers_exists_as_another_year() -> None:
    action = only(Snapshot(
        evidence=(mail(thread_id="t-1", documents=TWO_QUOTES),),
        cases=(case(stage="quoting", thread_ids=("t-1",)), case(id="c-2", quotes=(quote("01271-25"),))),
        organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R1", "proposal")
    assert action.commands == ()


def test_r1_with_several_new_numbers_waits_for_the_requester() -> None:
    action = only(Snapshot(
        evidence=(mail(thread_id="t-1", documents=TWO_QUOTES),),
        cases=(case(stage="lead", organization_id=None, thread_ids=("t-1",)),),
        organizations=(),
    ))
    assert (action.rule_id, action.mode) == ("R1", "proposal")
    assert any("institución solicitante" in reason for reason in action.reasons)


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


def test_r7_free_mail_waits_for_requester_check_then_opens_solicitada_without_inventing_an_institution() -> None:
    pending = only(Snapshot(
        evidence=(mail(direction="inbound", sender="Irina Example <irina@gmail.com>", thread_id="t-irina",
                       subject="Solicitud de cotización de productos", documents=()),),
        cases=(), organizations=(org(),),
    ))
    assert (pending.rule_id, pending.mode) == ("R7", "proposal")
    assert any("esperando" in reason for reason in pending.reasons)

    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="Irina Example <irina@gmail.com>", thread_id="t-irina",
                       subject="Solicitud de cotización de productos", documents=(),
                       requester_checked=True),),
        cases=(), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode) == ("R7", "auto")
    assert action.organization_id is None
    assert len(action.commands) == 1
    assert action.commands[0]["command"] == "open_commercial_case"
    assert "Solicitud de cotización" in action.case_title
    assert any("sin institución" in reason for reason in action.reasons)


def test_r7_free_mail_uses_a_unique_exact_requester_identity_from_the_worker() -> None:
    action = only(Snapshot(
        evidence=(mail(
            direction="inbound",
            sender="Irina Example <irina@gmail.com>",
            thread_id="t-irina",
            subject="Solicitud de cotización de productos",
            documents=(),
            requester_checked=True,
            requester_organization_id="o-1",
            requester_organization_name="Cliente Ficticio",
        ),),
        cases=(),
        organizations=(org(),),
    ))
    assert (action.rule_id, action.mode, action.organization_id) == ("R7", "auto", "o-1")
    assert [c["command"] for c in action.commands] == [
        "open_commercial_case", "add_case_organization", "advance_case_stage", "advance_case_stage",
    ]
    assert action.commands[-1]["inputs"]["stage"] == "qualified"


def test_r7_unknown_domain_quote_request_opens_solicitada_after_requester_check_without_guessing_identity() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="compras@nuevo.test", thread_id="t-new",
                       subject="RFQ centrífuga", documents=(), requester_checked=True),),
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


def test_multilingual_supplier_reply_does_not_open_a_new_request_case() -> None:
    for subject in ("回复：RFQ – Autoclave", "回覆: Cotización", "AW: RFQ balanza", "Re : Cotización"):
        action = only(Snapshot(
            evidence=(mail(direction="inbound", sender="sales@unknown.test", documents=(),
                           subject=subject, requester_checked=True),), cases=(), organizations=(),
        ))
        assert (action.rule_id, action.mode, action.commands) == ("R8", "none", ())


def _rfq(**over) -> MailEvidence:
    """Our own price request to a supplier, as the capture classifies it."""
    base = dict(id="e-0", thread_id="t-rfq", direction="outbound", direction_hint="supplier_rfq",
                sender="Ventas <contacto@origenlab.cl>", recipients=("export@fabrica.test",),
                subject="RFQ – 50 L autoclave", documents=())
    base.update(over)
    return mail(**base)


def test_a_reply_on_our_supplier_rfq_thread_never_opens_a_request_case() -> None:
    reply = mail(id="e-1", thread_id="t-rfq", direction="inbound", sender="ventas@fabrica.test",
                 subject="RFQ – 50 L autoclave quotation attached", documents=(), requester_checked=True,
                 sent_at="2026-10-02T09:00:00+00:00")
    actions = {a.evidence_id: a for a in plan(Snapshot(evidence=(_rfq(), reply), cases=(), organizations=()))}
    a = actions["e-1"]
    assert (a.rule_id, a.mode, a.commands) == ("R8", "none", ())
    assert a.reasons[0].startswith("proveedor al que OrigenLab pidió precio")


def test_a_new_thread_from_a_domain_we_sent_an_rfq_to_is_a_supplier_too() -> None:
    fresh = mail(id="e-1", thread_id="t-new", direction="inbound", sender="sales@fabrica.test",
                 subject="Request for quotation follow-up", documents=(), requester_checked=True,
                 sent_at="2026-10-02T09:00:00+00:00")
    actions = {a.evidence_id: a for a in plan(Snapshot(evidence=(_rfq(), fresh), cases=(), organizations=()))}
    assert (actions["e-1"].rule_id, actions["e-1"].mode) == ("R8", "none")


def test_an_rfq_to_a_free_mail_supplier_does_not_silence_free_mail_requests() -> None:
    rfq = _rfq(recipients=("proveedor@gmail.com",))
    request = mail(id="e-1", thread_id="t-req", direction="inbound", sender="Cliente <cliente@gmail.com>",
                   subject="Solicitud de cotización balanza", documents=(), requester_checked=True)
    actions = {a.evidence_id: a for a in plan(Snapshot(evidence=(rfq, request), cases=(), organizations=()))}
    assert (actions["e-1"].rule_id, actions["e-1"].mode) == ("R7", "auto")


def test_a_domain_that_is_also_a_customer_is_not_treated_as_a_supplier() -> None:
    rfq = _rfq(recipients=("compras@cliente.test",))
    request = mail(id="e-1", thread_id="t-req", direction="inbound", sender="Compras <compras@cliente.test>",
                   subject="Solicitud de cotización centrífuga", documents=(), requester_checked=True)
    actions = {a.evidence_id: a for a in plan(Snapshot(evidence=(rfq, request), cases=(), organizations=(org(),)))}
    assert (actions["e-1"].rule_id, actions["e-1"].mode) == ("R7", "auto")


def test_a_reply_on_an_rfq_thread_already_linked_to_a_case_still_links() -> None:
    reply = mail(id="e-1", thread_id="t-rfq", direction="inbound", sender="ventas@fabrica.test",
                 subject="Re: RFQ – 50 L autoclave", documents=())
    snap = Snapshot(evidence=(_rfq(), reply), cases=(case(thread_ids=("t-rfq",)),), organizations=(org(),))
    actions = {a.evidence_id: a for a in plan(snap)}
    assert actions["e-1"].rule_id == "R1"


def test_our_reply_titled_request_for_quotation_to_a_customer_never_silences_that_customer() -> None:
    """V1 marks any outbound «Request for quotation» subject as supplier_rfq, customer replies too.
    Only an RFQ that started its own thread counts, so the customer's next request still opens a case."""
    request = mail(id="e-1", thread_id="t-cli", direction="inbound", sender="Compras <compras@nuevo-cliente.test>",
                   subject="Request for quotation centrifuge", documents=(), requester_checked=True,
                   sent_at="2026-10-01T12:00:00+00:00")
    our_reply = mail(id="e-2", thread_id="t-cli", direction="outbound", direction_hint="supplier_rfq",
                     recipients=("compras@nuevo-cliente.test",), subject="Re: Request for quotation centrifuge",
                     documents=(), sent_at="2026-10-01T13:00:00+00:00")
    later = mail(id="e-3", thread_id="t-cli-2", direction="inbound", sender="Compras <compras@nuevo-cliente.test>",
                 subject="Solicitud de cotización balanza", documents=(), requester_checked=True,
                 sent_at="2026-10-05T12:00:00+00:00")
    actions = {a.evidence_id: a for a in plan(Snapshot(evidence=(request, our_reply, later), cases=(), organizations=()))}
    assert actions["e-1"].rule_id == "R7"
    assert actions["e-3"].rule_id == "R7"


def test_a_customer_request_older_than_our_rfq_to_its_domain_is_not_silenced() -> None:
    request = mail(id="e-1", thread_id="t-req", direction="inbound", sender="ventas@fabrica.test",
                   subject="Solicitud de cotización sonicador", documents=(), requester_checked=True,
                   sent_at="2026-09-30T12:00:00+00:00")
    actions = {a.evidence_id: a for a in plan(Snapshot(evidence=(_rfq(), request), cases=(), organizations=()))}
    assert actions["e-1"].rule_id == "R7"


def test_received_quotation_requires_review_but_explicit_request_still_opens_case() -> None:
    for subject, expected in (("Quotation for Vertical Autoclave", "proposal"),
                              ("Request for quotation for Vertical Autoclave", "auto")):
        action = only(Snapshot(
            evidence=(mail(direction="inbound", sender="person@unknown.test", documents=(),
                           subject=subject, requester_checked=True),), cases=(), organizations=(),
        ))
        assert (action.rule_id, action.mode) == ("R7", expected)
        assert bool(action.commands) == (expected == "auto")


# ------------------------------------------------------------------ idempotency, determinism


def test_r7_unregistered_numbered_quote_followup_never_opens_a_second_case() -> None:
    for subject in (
        "Compra de productos, cotización N°01259-26",
        "Solicitud de cotización 01259–26",
        "Request for quotation CN01259",
        "Cotización CN01259/26",
        "Solicitud de cotización 01259-2026",
        "Compra de productos, cotización N°01259",
        "Request for quotation No. 01259",
    ):
        for organizations in ((), (org(),)):
            action = only(Snapshot(
                evidence=(mail(direction="inbound", sender="person@cliente.test", documents=(),
                               subject=subject, requester_checked=True),),
                cases=(case(stage="lead", organization_id=None, thread_ids=("original",)),),
                organizations=organizations,
            ))
            assert (action.rule_id, action.mode, action.commands) == ("R7", "proposal", ())
            assert action.case_id is None  # no automatic reconciliation of either case


def test_r7_unregistered_attachment_number_requires_review_even_without_subject_number() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="person@cliente.test",
                       subject="Solicitud de cotización", requester_checked=True),),
        cases=(), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode, action.commands) == ("R7", "proposal", ())


def test_numbered_followup_keeps_existing_thread_link_priority() -> None:
    action = only(Snapshot(
        evidence=(mail(direction="inbound", sender="person@cliente.test", documents=(),
                       subject="Compra de productos, cotización N°01259-26"),),
        cases=(case(thread_ids=("t-1",)),), organizations=(org(),),
    ))
    assert (action.rule_id, action.mode, action.case_id) == ("R1", "auto", "c-1")


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


# ------------------------------------------------------------------ R9: the institution the PDF names
def r9_mail(filename="CN09876-Instituto Ficticio de Ensayos (IFDE).pdf", **over) -> MailEvidence:
    base = dict(thread_id="t-new", recipients=("Persona <persona.cliente@gmail.com>",),
                documents=(MailDocument(filename=filename, sha256=PDF_SHA, cn_tokens=("CN09876",)),))
    return mail(**{**base, **over})


IFDE = dict(id="o-ifde", name="IFDE", domains=("ifde.test",))


def test_quote_file_institution_reads_the_name_after_the_token() -> None:
    assert quote_file_institution("CN09876-Instituto Ficticio de Ensayos (IFDE).pdf") == \
        "Instituto Ficticio de Ensayos (IFDE)"
    assert quote_file_institution("CN 09876-26 - Universidad Ficticia.pdf") == "Universidad Ficticia"
    assert quote_file_institution("COT09876_Hospital Ficticio.PDF") == "Hospital Ficticio"
    for bare in ("CN09876.pdf", "CN09876-26.pdf", "Folleto_producto.pdf", "CN09876-ab.pdf", None):
        assert quote_file_institution(bare) is None, bare


def test_r9_records_a_sent_quote_on_the_one_open_case_of_the_institution_the_pdf_names() -> None:
    # The 2026-10-13 shape: contacto@ → a gmail.com address, new thread, new number, «(SIGLA)».
    action = only(Snapshot(
        evidence=(r9_mail(),),
        cases=(case(id="c-ifde", stage="qualified", organization_id="o-ifde"),),
        organizations=(org(**IFDE),),
    ))
    # A case with no quote yet is a request waiting for its first one: R13 (2026-10-15) takes it before
    # R9, with the same three steps.
    assert (action.rule_id, action.mode, action.case_id, action.quote_number) == ("R13", "auto", "c-ifde", "09876-26")
    assert action.context["from_stage"] == "qualified"
    assert [c["command"] for c in action.commands] == [
        "link_case_evidence", "advance_case_stage", "record_historical_quotation"]
    assert action.commands[1]["inputs"]["stage"] == "quoting"
    assert action.commands[-1]["inputs"]["quote_number"] == "09876-26"
    assert any("IFDE" in r for r in action.reasons)


def test_r9_matches_the_whole_name_or_the_name_without_its_parenthetical() -> None:
    for crm_name in ("Instituto Ficticio de Ensayos", "Instituto Ficticio de Ensayos (IFDE)"):
        action = only(Snapshot(evidence=(r9_mail(),), cases=(case(organization_id="o-ifde"),),
                               organizations=(org(**{**IFDE, "name": crm_name}),)))
        assert (action.rule_id, action.mode) == ("R9", "auto"), crm_name


def test_r9_never_guesses() -> None:
    two_cases = only(Snapshot(evidence=(r9_mail(),), organizations=(org(**IFDE),), cases=(
        case(id="c-1", organization_id="o-ifde"), case(id="c-2", organization_id="o-ifde"))))
    assert (two_cases.rule_id, two_cases.mode, two_cases.candidates) == ("R9", "proposal", ("c-1", "c-2"))
    no_case = only(Snapshot(evidence=(r9_mail(),), organizations=(org(**IFDE),),
                            cases=(case(organization_id="o-ifde", closed=True, stage="won"),)))
    assert (no_case.rule_id, no_case.mode) == ("R9", "proposal")
    two_orgs = only(Snapshot(evidence=(r9_mail(),), cases=(), organizations=(
        org(**IFDE), org(id="o-dup", name="Instituto Ficticio de Ensayos", domains=()))))
    assert (two_orgs.rule_id, two_orgs.mode, two_orgs.candidates) == ("R9", "proposal", ("o-dup", "o-ifde"))
    late = only(Snapshot(evidence=(r9_mail(),), organizations=(org(**IFDE),),
                         cases=(case(organization_id="o-ifde", stage="won"),)))
    assert (late.rule_id, late.mode) == ("R9", "proposal")


def test_r9_does_not_act_on_a_supplier_or_a_known_recipient_domain() -> None:
    supplier = only(Snapshot(evidence=(r9_mail(),), cases=(case(organization_id="o-ifde"),),
                             organizations=(org(**{**IFDE, "is_supplier": True}),)))
    assert supplier.rule_id == "R8"
    # A recipient domain that names an institution is R3's, whatever the file name says.
    by_domain = only(Snapshot(evidence=(r9_mail(recipients=("compras@cliente.test",)),),
                              cases=(case(organization_id="o-ifde"),), organizations=(org(), org(**IFDE))))
    assert by_domain.rule_id == "R3"


def test_r9_is_automatic_and_undone_like_r1() -> None:
    from origenlab_api.v2.mail_rules_auto import AUTOMATIC_RULES
    import inspect
    from origenlab_api.v2 import mail_rules_repository
    assert "R9" in AUTOMATIC_RULES and "R9" in RULE_IDS
    source = inspect.getsource(mail_rules_repository.MailRulesRepository._undo_steps)
    assert 'rule in ("R1", "R9", "R13") and created.get("quote_revision_id")' in source



# ------------------------------------------------------------------ R10: the PDF names an institution the CRM lacks
def test_r10_proposes_an_institution_por_confirmar_with_its_case_in_enviada() -> None:
    from origenlab_api.v2.mail_rules_auto import AUTOMATIC_RULES
    action = only(Snapshot(evidence=(r9_mail("CN09876-Instituto Otro (OTRA).pdf"),), cases=(), organizations=(org(),)))
    assert (action.rule_id, action.mode, action.organization_name) == ("R10", "auto", "Instituto Otro (OTRA)")
    assert "R10" not in AUTOMATIC_RULES  # a person presses «Aplicar», as for R4
    names = [c["command"] for c in action.commands]
    assert names == ["register_mail_organization", "open_commercial_case", "add_case_organization",
                     "advance_case_stage", "advance_case_stage", "advance_case_stage", "record_historical_quotation"]
    assert action.commands[0]["inputs"]["domain"] is None
    assert action.commands[0]["inputs"]["name"] == "Instituto Otro (OTRA)"
    assert action.commands[2]["inputs"]["organization_id"] == "@organization"
    assert action.commands[-2]["inputs"]["stage"] == "quoting"


def test_r10_never_claims_one_name_twice_in_a_pass_and_a_bare_file_name_stays_r8() -> None:
    a = r9_mail("CN09876-Instituto Otro (OTRA).pdf", id="e-a")
    b = r9_mail("CN09877-Instituto Otro (OTRA).pdf", id="e-b",
                documents=(MailDocument(filename="CN09877-Instituto Otro (OTRA).pdf", sha256=PDF_SHA, cn_tokens=("CN09877",)),))
    first, second = plan(Snapshot(evidence=(a, b), cases=(), organizations=()))
    assert (first.rule_id, first.mode) == ("R10", "auto")
    assert (second.rule_id, second.mode) == ("R10", "proposal")
    bare = only(Snapshot(evidence=(r9_mail("CN09876.pdf"),), cases=(), organizations=()))
    assert (bare.rule_id, bare.mode) == ("R8", "none")



def test_r10_creates_only_from_the_segment_that_names_an_institution() -> None:
    from origenlab_api.v2.mail_rules import institution_segment, quote_file_segments
    assert quote_file_segments("Persona Ficticia - Empresa Ficticia SA-MB32") == ["Persona Ficticia", "Empresa Ficticia SA"]
    cases = {
        "Instituto Ficticio de Ensayos (IFDE)": "Instituto Ficticio de Ensayos (IFDE)",
        "Persona Ficticia-Empresa Ficticia SA": "Empresa Ficticia SA",
        "Persona Ficticia - Comercial Ficticia S.A.": "Comercial Ficticia S.A.",
        "Persona Ficticia – Ficticia Chile": "Ficticia Chile",
        "Persona Ficticia - EMPRESA FICTICIA SPA-AB12": "EMPRESA FICTICIA SPA",
        # A person alone, a person and a model, filler, or two institutions: never created.
        "Pedro Ficticio Pérez": None, "Pedro Ficticio-AB12": None, "cliente": None, "cotizacion final": None,
        "Empresa Uno SA - Empresa Dos Ltda": None,
    }
    for name, expected in cases.items():
        assert institution_segment(name) == expected, name
    person_only = only(Snapshot(evidence=(r9_mail("CN09876-Pedro Ficticio Pérez.pdf"),), cases=(), organizations=()))
    assert (person_only.rule_id, person_only.mode) == ("R8", "none")
    person_and_company = only(Snapshot(evidence=(r9_mail("CN09876-Pedro Ficticio-Empresa Ficticia SA-AB12.pdf"),),
                                       cases=(), organizations=()))
    assert (person_and_company.rule_id, person_and_company.organization_name) == ("R10", "Empresa Ficticia SA")


def test_r9_matches_the_institution_segment_after_a_person() -> None:
    action = only(Snapshot(evidence=(r9_mail("CN09876-Persona Ficticia-Empresa Ficticia SA.pdf"),),
                           cases=(case(id="c-e", organization_id="o-e"),),
                           organizations=(org(id="o-e", name="Empresa Ficticia SA", domains=()),)))
    assert (action.rule_id, action.mode, action.case_id) == ("R9", "auto", "c-e")


def test_a_person_written_before_the_institution_in_one_part_is_never_the_institution() -> None:
    from origenlab_api.v2.mail_rules import institution_segment
    # No dash between the person and the institution: the part does not start with its marker.
    assert institution_segment("Pedro Ficticio Universidad Ficticia") is None
    assert institution_segment("Pedro Ficticio (UFI)") is None
    # One part that starts with what it is stays an institution; after a dash the person is split off.
    assert institution_segment("Instituto Ficticio de Ensayos (IFDE)") == "Instituto Ficticio de Ensayos (IFDE)"
    assert institution_segment("Pedro Ficticio - Empresa Ficticia SA") == "Empresa Ficticia SA"



# ------------------------------------------------------------------ 2026-10-14: body numbers and the AI reading


def inbound(**over) -> MailEvidence:
    base = dict(id="e-9", thread_id="t-9", direction="inbound", sender="Pagos <pagos@banco-ficticio.test>",
                recipients=("contacto@origenlab.cl",), subject="Aviso de transferencia de fondos",
                sent_at="2026-10-13T15:00:00+00:00", documents=(), requester_checked=True)
    base.update(over)
    return MailEvidence(**base)


def test_a_payment_notice_naming_its_quote_in_the_body_is_filed_on_that_case() -> None:
    a = only(Snapshot(evidence=(inbound(body_quote_tokens=("09259-26",)),),
                      cases=(case(quotes=(quote("09259-26"),)),), organizations=(org(),)))
    assert (a.rule_id, a.mode, a.case_id) == ("R2", "auto", "c-1")
    assert a.commands[0]["command"] == "link_case_evidence"
    assert any("texto del correo" in r for r in a.reasons)


def test_a_won_case_still_gets_its_payment_and_order_mail_without_reopening() -> None:
    won = case(stage="won", closed=True, quotes=(quote("09259-26"),))
    a = only(Snapshot(evidence=(inbound(body_quote_tokens=("09259-26",)),), cases=(won,), organizations=(org(),)))
    assert (a.rule_id, a.mode, a.case_id) == ("R2", "auto", "c-1")
    assert [c["command"] for c in a.commands] == ["link_case_evidence_after_close"]


def test_a_lost_case_is_not_filed_into_so_the_reply_stays_visible() -> None:
    lost = case(stage="lost", closed=True, quotes=(quote("09259-26"),))
    a = only(Snapshot(evidence=(inbound(body_quote_tokens=("09259-26",)),), cases=(lost,), organizations=(org(),)))
    assert (a.rule_id, a.mode) == ("R8", "none")


def test_the_subject_number_outranks_the_body_number() -> None:
    one, two = case(id="c-1", quotes=(quote("01200-26"),)), case(id="c-2", quotes=(quote("09259-26"),))
    a = only(Snapshot(evidence=(inbound(subject="Consulta cotización 01200-26", body_quote_tokens=("09259-26",)),),
                      cases=(one, two), organizations=(org(),)))
    assert (a.rule_id, a.case_id) == ("R2", "c-1")


def test_a_client_asking_for_a_price_in_the_body_opens_a_request_when_the_ai_reads_it_so() -> None:
    a = only(Snapshot(evidence=(inbound(sender="Ana <ana@laboratorio-ficticio.test>", subject="Consulta equipos",
                                        model_intent="quote_request"),),
                      cases=(), organizations=()))
    assert (a.rule_id, a.mode) == ("R7", "auto")
    assert a.commands[0]["command"] == "open_commercial_case"
    assert any("IA" in r for r in a.reasons)


def test_a_reply_is_never_a_new_request_even_when_the_ai_says_so() -> None:
    a = only(Snapshot(evidence=(inbound(sender="Ana <ana@laboratorio-ficticio.test>", subject="Re: Consulta equipos",
                                        model_intent="quote_request"),),
                      cases=(), organizations=()))
    assert (a.rule_id, a.mode) == ("R8", "none")


def test_a_supplier_offer_with_quotation_words_does_not_open_a_request() -> None:
    a = only(Snapshot(evidence=(inbound(sender="Ventas <ventas@fabrica-ficticia.test>",
                                        subject="Cotización equipos de laboratorio", model_intent="supplier_offer"),),
                      cases=(), organizations=()))
    assert (a.rule_id, a.mode) == ("R8", "none")
    assert any("proveedor" in r for r in a.reasons)


# ------------------------------------------------------------------ R11: forwarded requests


def forward(**over):
    from origenlab_api.v2.mail_rules import ForwardRequest

    base = dict(id="f-1", ids=("f-1",), customer_email="ana@laboratorio-ficticio.test", customer_name="Ana Ficticia",
                company=None, requested_item="Gradillas", origins=("origenlab",))
    base.update(over)
    return ForwardRequest(**base)


def test_a_forwarded_request_whose_customer_already_writes_on_an_open_case_joins_that_case() -> None:
    c = case(id="c-7", people=("ana@laboratorio-ficticio.test",))
    [a] = plan(Snapshot(evidence=(), cases=(c,), organizations=(), forwards=(forward(),)))
    assert (a.rule_id, a.mode, a.case_id, a.evidence_id) == ("R11", "auto", "c-7", "f-1")
    assert a.commands[0]["command"] == "attach_forward_to_case"


def test_a_forwarded_request_of_an_institution_with_one_open_case_joins_it() -> None:
    o = org(id="o-5", domains=("laboratorio-ficticio.test",))
    [a] = plan(Snapshot(evidence=(), cases=(case(id="c-5", organization_id="o-5"),), organizations=(o,),
                        forwards=(forward(),)))
    assert (a.rule_id, a.case_id) == ("R11", "c-5")


def test_a_new_customer_gets_a_request_named_after_the_known_institution_or_the_company_the_forward_names() -> None:
    o = org(id="o-5", domains=("laboratorio-ficticio.test",))
    [known] = plan(Snapshot(evidence=(), cases=(), organizations=(o,), forwards=(forward(),)))
    assert known.commands[0]["command"] == "create_request_from_forward"
    assert known.commands[0]["inputs"]["organization_id"] == "o-5"
    [named] = plan(Snapshot(evidence=(), cases=(), organizations=(),
                            forwards=(forward(customer_email="e@gmail.com", company="Laboratorio Ficticio EIRL"),)))
    assert named.commands[0]["inputs"]["new_organization_name"] == "Laboratorio Ficticio EIRL"
    [bare] = plan(Snapshot(evidence=(), cases=(), organizations=(), forwards=(forward(customer_email="e@gmail.com"),)))
    assert "organization_id" not in bare.commands[0]["inputs"] and "new_organization_name" not in bare.commands[0]["inputs"]


def test_a_forward_from_a_supplier_domain_opens_nothing_and_an_applied_one_is_not_planned_again() -> None:
    supplier = org(id="o-9", domains=("laboratorio-ficticio.test",), is_supplier=True)
    [a] = plan(Snapshot(evidence=(), cases=(), organizations=(supplier,), forwards=(forward(),)))
    assert (a.rule_id, a.mode) == ("R8", "none")
    assert plan(Snapshot(evidence=(), cases=(), organizations=(), forwards=(forward(),),
                         applied_evidence_ids=frozenset({"f-1"}))) == []


def test_a_forward_from_a_domain_the_ai_reads_as_offering_products_opens_nothing() -> None:
    offer = inbound(id="e-5", sender="Ventas <info@fabrica-ficticia.test>", subject="New products",
                    model_intent="supplier_offer")
    actions = {a.evidence_id: a for a in plan(Snapshot(
        evidence=(offer,), cases=(), organizations=(),
        forwards=(forward(customer_email="info@fabrica-ficticia.test", customer_name=None),)))}
    assert (actions["f-1"].rule_id, actions["f-1"].mode) == ("R8", "none")
    assert any("proveedor" in r for r in actions["f-1"].reasons)


# ------------------------------------------------------------------ R12: the context matcher places it


def _pick(case_id, confidence="high", reason="mismo producto y misma persona que el caso"):
    return {"decision": "propose", "candidates": [{"opportunity_id": case_id, "score": 5, "signals": ["domain", "product"]}],
            "pick_state": "ran", "pick": {"choice": case_id, "confidence": confidence, "reason_es": reason}}


def test_a_client_writing_on_a_new_thread_joins_the_case_the_ai_is_sure_of_instead_of_a_new_request() -> None:
    a = only(Snapshot(evidence=(inbound(sender="Ana <ana@cliente.test>", subject="Cotización centrífuga, ¿plazo?",
                                        case_match=_pick("c-1")),),
                      cases=(case(),), organizations=(org(),)))
    assert (a.rule_id, a.mode, a.case_id) == ("R12", "auto", "c-1")
    assert a.commands[0]["command"] == "link_case_evidence"
    assert any("mismo producto" in r for r in a.reasons)


def test_a_medium_pick_stays_a_suggestion_and_r7_still_opens_the_request() -> None:
    a = only(Snapshot(evidence=(inbound(sender="Ana <ana@cliente.test>", subject="Cotización centrífuga",
                                        case_match=_pick("c-1", "medium")),),
                      cases=(case(),), organizations=(org(),)))
    assert a.rule_id == "R7"


def test_layer_one_exact_also_places_it_and_a_won_case_keeps_it_closed() -> None:
    exact = {"decision": "exact", "candidates": [{"opportunity_id": "c-1", "score": 6, "signals": ["text_quote"]}],
             "pick_state": "not_needed"}
    a = only(Snapshot(evidence=(inbound(case_match=exact),), cases=(case(stage="won", closed=True),),
                      organizations=(org(),)))
    assert (a.rule_id, a.mode) == ("R12", "auto")
    assert a.commands[0]["command"] == "link_case_evidence_after_close"


def test_a_supplier_answering_about_a_clients_deal_goes_to_the_clients_case_when_the_ai_is_sure() -> None:
    supplier = org(id="o-9", domains=("fabrica.test",), is_supplier=True)
    a = only(Snapshot(evidence=(inbound(sender="Ventas <ventas@fabrica.test>", subject="Precio centrífuga",
                                        case_match=_pick("c-1")),),
                      cases=(case(),), organizations=(org(), supplier)))
    assert (a.rule_id, a.case_id) == ("R12", "c-1")
    no_pick = only(Snapshot(evidence=(inbound(sender="Ventas <ventas@fabrica.test>", subject="Precio centrífuga"),),
                            cases=(case(),), organizations=(org(), supplier)))
    assert no_pick.rule_id == "R8"


def test_a_pick_of_a_lost_case_or_a_case_not_in_the_snapshot_does_nothing() -> None:
    lost = case(stage="lost", closed=True)
    assert only(Snapshot(evidence=(inbound(case_match=_pick("c-1")),), cases=(lost,), organizations=(org(),))).rule_id != "R12"
    assert only(Snapshot(evidence=(inbound(case_match=_pick("c-404")),), cases=(case(),), organizations=(org(),))).rule_id != "R12"


# ------------------------------------------------------------------ R13: a sent quote finds the client's open request


def sent(to, filename="CN09266-Esteban Ficticio – Laboratorio Ficticio EIRL.pdf", token="CN09266", **over):
    return mail(id="e-13", thread_id="t-new", recipients=(to,), subject="Cotización tabletas",
                documents=(MailDocument(filename=filename, sha256=PDF_SHA, cn_tokens=(token,)),), **over)


def request_case(**over):
    base = dict(id="c-req", title="Solicitud — tabletas", stage="lead", organization_id="o-lab", quotes=(),
                people=("esteban@lab-ficticio.test",))
    base.update(over)
    return case(**base)


LAB = dict(id="o-lab", name="Laboratorio Ficticio EIRL", domains=(), confirmation="confirmed")


def test_a_quote_sent_to_the_person_who_asked_lands_on_their_request_instead_of_a_new_case() -> None:
    a = only(Snapshot(evidence=(sent("Esteban <esteban@lab-ficticio.test>"),), cases=(request_case(),),
                      organizations=(org(**LAB),)))
    assert (a.rule_id, a.mode, a.case_id, a.quote_number) == ("R13", "auto", "c-req", "09266-26")
    assert [c["command"] for c in a.commands] == [
        "link_case_evidence", "advance_case_stage", "advance_case_stage", "advance_case_stage", "record_historical_quotation"]


def test_the_institution_the_pdf_names_finds_the_request_when_the_address_is_new() -> None:
    a = only(Snapshot(evidence=(sent("otra@lab-ficticio.test"),), cases=(request_case(people=()),),
                      organizations=(org(**LAB),)))
    assert (a.rule_id, a.case_id) == ("R13", "c-req")


def test_a_request_that_already_has_a_quote_or_two_candidates_is_not_guessed() -> None:
    quoted = request_case(quotes=(quote("09100-26"),))
    a = only(Snapshot(evidence=(sent("Esteban <esteban@lab-ficticio.test>"),), cases=(quoted,),
                      organizations=(org(**LAB),)))
    assert a.rule_id != "R13"
    two = (request_case(), request_case(id="c-req2"))
    b = only(Snapshot(evidence=(sent("Esteban <esteban@lab-ficticio.test>"),), cases=two, organizations=(org(**LAB),)))
    assert (b.rule_id, b.mode) == ("R13", "proposal")
