"""Layer 1 of the context matcher: which case an email belongs to, from signals only. Invented data."""

from __future__ import annotations

from origenlab_worker.case_match import (
    CaseCard, MatchInput, attachment_quote_keys, match_case,
)

SENT = "2026-10-06T15:00:00+00:00"


def card(oid, *, stage="quoting", closed_at=None, institution=None, domains=(), quotes=(), threads=(),
         addresses=(), interests=()):
    return CaseCard(oid, f"Caso {oid}", stage, closed_at, institution, tuple(domains), tuple(quotes),
                    tuple(threads), tuple(addresses), tuple(interests))


def mail(*, sender="ana@cliente.invalid", subject="Re: consulta", body="Hola", pdfs=(), thread="t-x",
         direction="inbound", recipients=("contacto@origenlab.cl",)):
    return MatchInput(sender, tuple(recipients), thread, direction, subject, body, tuple(pdfs), SENT)


def test_attachment_quote_keys_read_revision_letter_cn_and_year():
    keys = attachment_quote_keys(["Cotización 09153A.pdf", "CN09147-26.pdf", "CN1975.pdf", "logo.png"], SENT)
    assert set(keys) == {(26, 9153), (26, 9147), (26, 1975)}


def test_order_and_supplier_numbers_are_not_quotes():
    assert attachment_quote_keys(["OC 4600987654.pdf", "QX5521.pdf", "Factura 123553.pdf", "OC Nº25.pdf"], SENT) == {}


def test_pdf_quote_beats_other_signals_and_closed_case_counts():
    won = card("won", stage="won", closed_at="2026-10-01T00:00:00+00:00", quotes=("09153-26",))
    near = card("near", quotes=("09161-26",), domains=("cliente.invalid",), addresses=("ana@cliente.invalid",))
    r = match_case(mail(pdfs=("Cotización 09153A.pdf",)), [near, won])
    assert r.decision == "exact"
    assert [c.opportunity_id for c in r.candidates] == ["won"]


def test_same_thread_is_exact():
    r = match_case(mail(thread="t-1"), [card("a", threads=("t-1",)), card("b")])
    assert r.decision == "exact" and r.candidates[0].opportunity_id == "a"


def test_sender_address_alone_never_suggests():
    r = match_case(mail(sender="pedro@gmail.com"), [card("a", addresses=("pedro@gmail.com",))])
    assert r.decision == "none" and r.candidates == ()


def test_domain_and_product_propose_ranked():
    a = card("a", domains=("cliente.invalid",), interests=("Balanza XB3100",))
    b = card("b", domains=("cliente.invalid",))
    r = match_case(mail(body="necesitamos la XB3100"), [b, a])
    assert r.decision == "propose"
    assert [c.opportunity_id for c in r.candidates] == ["a", "b"]
    assert "product" in r.candidates[0].signals


def test_free_mail_domain_is_never_institution():
    r = match_case(mail(sender="x@gmail.com"), [card("a", domains=("gmail.com",))])
    assert r.decision == "none"


def test_institution_name_in_text_counts():
    r = match_case(mail(sender="jon@proveedor.invalid", body="cotización para Laboratorio Ejemplo Sur"),
                   [card("a", institution="Laboratorio Ejemplo Sur")])
    assert r.decision == "propose" and r.candidates[0].signals == ("institution_name",)


def test_unknown_quote_number_proposes_new_case():
    r = match_case(mail(pdfs=("CN09136.pdf",)), [card("a", quotes=("09135-26",), domains=("cliente.invalid",))])
    assert r.decision == "new_case" and r.new_case_quote == "09136-26"


def test_at_most_three_candidates():
    cases = [card(str(i), domains=("cliente.invalid",)) for i in range(6)]
    assert len(match_case(mail(), cases).candidates) == 3


def test_as_json_is_plain():
    r = match_case(mail(thread="t-1"), [card("a", threads=("t-1",))])
    assert r.as_json() == {"case_match_version": 1, "decision": "exact", "quote_numbers": [], "new_case_quote": None,
                           "candidates": [{"opportunity_id": "a", "score": 6, "signals": ["same_thread"]}]}


def thread_mail(thread_pdfs, **kw):
    m = mail(**kw)
    return MatchInput(m.sender, m.recipients, m.thread_id, m.direction, m.subject, m.body, m.attachment_names,
                      m.sent_at, tuple(thread_pdfs))


def test_quote_pdf_earlier_in_the_thread_links():
    r = match_case(thread_mail(["CN09142.pdf"]), [card("a", quotes=("09142-26",)), card("b")])
    assert r.decision == "exact" and r.candidates[0].signals == ("thread_pdf_quote",)


def test_quote_pdf_earlier_in_the_thread_with_no_case_proposes_new_case():
    r = match_case(thread_mail(["CN09136.pdf"]), [card("a", quotes=("09135-26",))])
    assert r.decision == "new_case" and r.new_case_quote == "09136-26"


def test_this_messages_pdf_beats_the_threads_pdf():
    a, b = card("a", quotes=("09153-26",)), card("b", quotes=("09161-26",))
    r = match_case(thread_mail(["CN09161.pdf"], pdfs=("Cotización 09153A.pdf",)), [a, b])
    assert [c.opportunity_id for c in r.candidates] == ["a"]


def test_a_tie_goes_to_the_open_case():
    closed = card("a", stage="abandoned", closed_at="2026-10-01T00:00:00+00:00", threads=("t-1",))
    open_ = card("z", quotes=("09159-26",))
    r = match_case(mail(thread="t-1", subject="Compra cotización N°09159-26"), [closed, open_])
    assert r.candidates[0].opportunity_id == "z" and r.decision == "exact"


def test_a_new_quote_on_a_case_thread_stays_on_that_case():
    r = match_case(thread_mail(["CN09148.pdf"], thread="t-1"), [card("a", quotes=("09140-26",), threads=("t-1",))])
    assert r.decision == "exact" and r.candidates[0].opportunity_id == "a"
    assert r.new_case_quote == "09148-26"  # kept: the quote is not in the CRM yet
