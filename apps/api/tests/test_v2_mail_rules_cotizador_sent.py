"""R16 (owner 2026-10-21): contacto@ emails the PDF of a quote the Cotizador approved → that revision is
sent and the case «Enviada». Pure planning, no database.

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

SHA_A, SHA_B = "a" * 64, "b" * 64
APPROVED = "2026-10-19T13:00:00+00:00"
SENT = "2026-10-19T15:00:00+00:00"


def authored(rid: str, no: int, *, status: str = "approved", superseded: bool = False,
             approved_at: str | None = APPROVED, origin: str = "authored", version: int = 3) -> QuoteRevision:
    return QuoteRevision(id=rid, revision_no=no, status=status, version=version, superseded=superseded,
                         origin=origin, approved_at=approved_at)


def quote(number: str, *revisions: QuoteRevision) -> CaseQuote:
    return CaseQuote(id=f"q-{number}", quote_number=number, revisions=revisions)


def case(*quotes: CaseQuote, **over) -> CaseState:
    base = dict(id="c-1", title="Caso ficticio", stage="qualified", version=7, organization_id="o-1",
                closed=False, thread_ids=("t-1",), quotes=quotes)
    base.update(over)
    return CaseState(**base)


def mail(*documents: MailDocument, **over) -> MailEvidence:
    base = dict(id="e-1", thread_id="t-1", direction="outbound", sender="Tatiana <contacto@origenlab.cl>",
                recipients=("compras@cliente.test",), subject="Cotizaciones", sent_at=SENT,
                documents=documents, linked_case_ids=("c-1",))
    base.update(over)
    return MailEvidence(**base)


def pdf(name: str, sha: str = SHA_A, tokens: tuple[str, ...] | None = None) -> MailDocument:
    # The capture keeps the digits only: «CN01274A» → «CN01274» (`capture.CN_TOKEN`).
    import re

    found = tokens if tokens is not None else tuple(f"CN{int(d):05d}" for d in re.findall(r"CN(\d+)", name))
    return MailDocument(filename=name, sha256=sha, cn_tokens=found)


ORG = Organization(id="o-1", name="Cliente Ficticio", version=1, confirmation="confirmed", domains=("cliente.test",))


def r16(*evidence: MailEvidence, cases: tuple[CaseState, ...], **kw):
    snap = Snapshot(evidence=evidence, cases=cases, organizations=(ORG,), **kw)
    return [a for a in plan(snap) if a.rule_id == "R16"]


def test_r16_is_a_rule() -> None:
    assert "R16" in RULE_IDS


def test_two_cotizador_pdfs_in_one_email_are_both_sent_and_the_case_walks_to_enviada() -> None:
    # The Ygaero shape: revision A of one quote and a second quote, in one email already on the case.
    first = quote("01274-26", authored("r-1", 1, status="sent", superseded=True), authored("r-2", 2))
    second = quote("01275-26", authored("r-3", 1))
    [a] = r16(mail(pdf("CN01274A-Cliente.pdf", SHA_A), pdf("CN01275-Cliente.pdf", SHA_B)),
              cases=(case(first, second),), applied_evidence_ids=frozenset({"e-1"}),
              applied_pairs=frozenset({("e-1", "R2")}))
    assert (a.mode, a.case_id, a.quote_number) == ("auto", "c-1", "01274A-26")
    assert [c["command"] for c in a.commands] == ["record_authored_quote_sent", "record_authored_quote_sent",
                                                  "advance_case_stage"]
    sent = [c["inputs"] for c in a.commands[:2]]
    assert [(s["quote_revision_id"], s["document_sha256"], s["quote_revision_version"]) for s in sent] == [
        ("r-2", SHA_A, 3), ("r-3", SHA_B, 3)]
    assert all(s["source_record_id"] == "e-1" for s in sent)
    assert a.commands[-1]["inputs"]["stage"] == "quoting"
    assert a.context == {"case": "c-1", "case_version": 7, "from_stage": "qualified"}
    assert "01274A-26, 01275-26" in a.reasons[0]


def test_the_revision_letter_must_match() -> None:
    # The file says revision 1; the approved one is revision 2 («A»): not the PDF of what was approved.
    q = quote("01274-26", authored("r-1", 1, status="void"), authored("r-2", 2))
    assert r16(mail(pdf("CN01274-Cliente.pdf")), cases=(case(q),)) == []
    # A letter followed by more letters is a word, not a revision.
    assert r16(mail(pdf("CN01274Acme.pdf", tokens=("CN01274",))), cases=(case(q),)) == []


def test_only_an_approved_cotizador_revision_not_yet_sent() -> None:
    for rev in (authored("r-1", 1, origin="historical_import", status="sent"),
                authored("r-1", 1, status="draft", approved_at=None),
                authored("r-1", 1, status="sent"),
                authored("r-1", 1, status="void"),
                authored("r-1", 1, superseded=True)):
        assert r16(mail(pdf("CN01276-Cliente.pdf")), cases=(case(quote("01276-26", rev)),)) == [], rev


def test_a_pdf_sent_before_the_approval_is_not_the_approved_one() -> None:
    q = quote("01276-26", authored("r-1", 1, approved_at="2026-10-19T16:00:00+00:00"))
    assert r16(mail(pdf("CN01276-Cliente.pdf")), cases=(case(q),)) == []
    assert r16(mail(pdf("CN01276-Cliente.pdf"), sent_at=None), cases=(case(quote("01276-26", authored("r-1", 1))),)) == []


def test_needs_contacto_outbound_on_the_case_and_an_open_stage() -> None:
    q = quote("01276-26", authored("r-1", 1))
    doc = pdf("CN01276-Cliente.pdf")
    assert r16(mail(doc, sender="otra@origenlab.cl"), cases=(case(q),)) == []
    assert r16(mail(doc, direction="inbound", sender="compras@cliente.test"), cases=(case(q),)) == []
    assert r16(mail(doc, direction_hint="internal_only"), cases=(case(q),)) == []
    assert r16(mail(doc, linked_case_ids=()), cases=(case(q),)) == []  # R1 links it first (its thread)
    assert r16(mail(doc, linked_case_ids=("c-9",)), cases=(case(q), case(id="c-9"))) == []
    assert r16(mail(doc), cases=(case(q, stage="won", closed=True),)) == []
    assert r16(mail(doc), cases=(case(q, stage="lost", closed=True),)) == []
    [already] = r16(mail(doc), cases=(case(q, stage="quoting"),))
    assert [c["command"] for c in already.commands] == ["record_authored_quote_sent"]  # no stage to walk
    [negotiating] = r16(mail(doc), cases=(case(q, stage="negotiating"),))
    assert [c["command"] for c in negotiating.commands] == ["record_authored_quote_sent"]


def test_pdfs_of_two_cases_move_both_the_ygaero_shape() -> None:
    # Production 2026-10-19: the reply on the request's thread (case A, holds 01274) carries 01274A and
    # 01275, which the Cotizador put on a second case B with no email. Each case gets its own.
    a_quote = quote("01274-26", authored("r-1", 1, superseded=True), authored("r-2", 2))
    b_quote = quote("01275-26", authored("r-3", 1))
    a_case = case(a_quote, id="c-a", title="Ygaero — solicitud", stage="qualified", thread_ids=("t-1",))
    b_case = case(b_quote, id="c-b", title="Ygaero — cotización", stage="lead", thread_ids=())
    e = mail(pdf("Cotización CN01274A — Cliente.pdf", SHA_A), pdf("Cotización CN01275 — Cliente.pdf", SHA_B),
             linked_case_ids=())
    [a] = r16(e, cases=(a_case, b_case))
    # B first (no thread, no link), then A (the thread's case), which is the action's case.
    assert [c["command"] for c in a.commands] == [
        "record_authored_quote_sent", "link_case_evidence", "advance_case_stage", "advance_case_stage",
        "advance_case_stage",
        "record_authored_quote_sent", "link_case_evidence", "advance_case_stage"]
    assert a.commands[0]["inputs"]["quote_revision_id"] == "r-3" and a.commands[5]["inputs"]["quote_revision_id"] == "r-2"
    assert a.context == {"case": "c-b", "case_version": 7, "from_stage": "lead"}
    assert (a.case_id, a.candidates) == ("c-a", ("c-b", "c-a"))
    assert "2 casos" in a.reasons[1]
    # One of them closed, or an email a person put on a third case → a person decides.
    assert r16(e, cases=(a_case, replace(b_case, stage="lost", closed=True))) == []
    assert r16(replace(e, linked_case_ids=("c-x",)), cases=(a_case, b_case, case(id="c-x"))) == []


def test_an_old_lost_thread_does_not_block_the_golden_omega_shape() -> None:
    # Production 2026-10-20: «Re: Cotización Vasos pp» on the thread of a lost case; 01276 is on a new case.
    q = quote("01276-26", authored("r-1", 1))
    new_case = case(q, id="c-new", stage="lead", thread_ids=())
    lost = case(id="c-old", stage="lost", closed=True, thread_ids=("t-1",))
    [a] = r16(mail(pdf("Cotización CN01276 — goldenomega.cl.pdf"), linked_case_ids=()), cases=(new_case, lost))
    assert a.case_id == "c-new" and [c["command"] for c in a.commands][:2] == ["record_authored_quote_sent",
                                                                               "link_case_evidence"]
    # The same reply on an OPEN case's thread that no PDF names stays with a person.
    open_other = replace(lost, stage="quoting", closed=False)
    assert r16(mail(pdf("Cotización CN01276 — goldenomega.cl.pdf"), linked_case_ids=()), cases=(new_case, open_other)) == []


def test_a_pdf_naming_two_quotes_leaves_it_to_a_person() -> None:
    one, two = quote("01276-26", authored("r-1", 1)), quote("01277-26", authored("r-2", 1))
    assert r16(mail(pdf("CN01276-CN01277.pdf")), cases=(case(one, two),)) == []
    # Not a PDF.
    assert r16(mail(MailDocument(filename="CN01276-a.xlsx", sha256=SHA_A, cn_tokens=("CN01276",))),
               cases=(case(one),)) == []


def test_once_per_email_and_never_beside_another_action_in_the_same_pass() -> None:
    q = quote("01276-26", authored("r-1", 1))
    doc = pdf("CN01276-Cliente.pdf")
    assert r16(mail(doc), cases=(case(q),), applied_evidence_ids=frozenset({"e-1"}),
               applied_pairs=frozenset({("e-1", "R2"), ("e-1", "R16")})) == []
    # A person linked it and nothing else acts: R16 now, alone.
    [a] = r16(mail(doc), cases=(case(q),))
    assert a.mode == "auto"
    # An email no rule has acted on and no one linked: R2 links it this pass, R16 the next.
    fresh = r16(mail(doc, linked_case_ids=(), thread_id="t-new"), cases=(case(q, thread_ids=()),))
    assert fresh == []
    actions = plan(Snapshot(evidence=(mail(doc, linked_case_ids=(), thread_id="t-new"),),
                            cases=(case(q, thread_ids=()),), organizations=(ORG,)))
    assert [(a.rule_id, a.mode) for a in actions] == [("R2", "auto")]


def test_a_historical_number_beside_a_cotizador_pdf_is_left_to_r14_first() -> None:
    # One linked, applied email: a Cotizador PDF and a new (unrecorded) number. R14 records the new one
    # this pass; R16 waits for the next, so the two never move the case in the same pass.
    q = quote("01276-26", authored("r-1", 1))
    e = mail(pdf("CN01276-Cliente.pdf", SHA_A), pdf("CN01290-Cliente.pdf", SHA_B))
    snap = Snapshot(evidence=(e,), cases=(case(q),), organizations=(ORG,), applied_evidence_ids=frozenset({"e-1"}),
                    applied_pairs=frozenset({("e-1", "R2")}), late_quote_since="2026-09-01T00:00:00+00:00")
    assert [a.rule_id for a in plan(snap)] == ["R14"]
    later = Snapshot(evidence=(e,), cases=(case(q, stage="quoting"),), organizations=(ORG,),
                     applied_evidence_ids=frozenset({"e-1"}),
                     applied_pairs=frozenset({("e-1", "R2"), ("e-1", "R14")}),
                     late_quote_since="2026-09-01T00:00:00+00:00")
    assert [a.rule_id for a in plan(later)] == ["R16"]


def test_the_browsers_own_file_name_counts_and_a_draft_print_does_not() -> None:
    # «Guardar como PDF» offers the print view's title, «Cotización CN01276A — Institución».
    q = quote("01276-26", authored("r-1", 1, status="void"), authored("r-2", 2))
    [a] = r16(mail(pdf("Cotización CN01276A — Cliente Ficticio.pdf")), cases=(case(q),))
    assert a.commands[0]["inputs"]["quote_revision_id"] == "r-2"
    assert r16(mail(pdf("Cotización CN01276A — Cliente Ficticio (borrador).pdf")), cases=(case(q),)) == []
    # A hand-renamed file in lower case is the same quote.
    assert len(r16(mail(pdf("cn01276a-cliente.pdf")), cases=(case(q),))) == 1


def test_a_december_quote_emailed_in_january() -> None:
    # The name carries no year: January's (26) is read first, then the year before.
    q = quote("01250-25", authored("r-1", 1, approved_at="2025-12-30T13:00:00+00:00"))
    january = "2026-01-05T15:00:00+00:00"
    [a] = r16(mail(pdf("Cotización CN01250 — Cliente.pdf"), sent_at=january), cases=(case(q),))
    assert a.commands[0]["inputs"]["quote_revision_id"] == "r-1"
    # The email's year wins when both exist.
    newer = quote("01250-26", authored("r-9", 1, approved_at="2026-01-02T13:00:00+00:00"))
    [b] = r16(mail(pdf("CN01250-Cliente.pdf"), sent_at=january), cases=(case(q, newer),))
    assert [c["inputs"]["quote_revision_id"] for c in b.commands if c["command"] == "record_authored_quote_sent"] == ["r-9"]


def test_named_revisions() -> None:
    from origenlab_api.v2.mail_rules import named_revisions

    assert named_revisions("Cotización CN01274A — Ygaero Chile SpA.pdf", SENT) == {((26, 1274), 2)}
    assert named_revisions("CN01275 Ygaero.pdf", SENT) == {((26, 1275), 1)}
    assert named_revisions("cn01274b-x.pdf", SENT) == {((26, 1274), 3)}
    assert named_revisions("CN01275Ygaero.pdf", SENT) == set()
    assert named_revisions("Cotización CN01274 — X (borrador).pdf", SENT) == set()
    assert named_revisions(None, SENT) == set()


def test_a_browser_named_pdf_on_a_new_thread_is_linked_by_r16_itself() -> None:
    # Nothing linked it (here: no CN token for R2, a new thread for R1). R16 marks it, then links it.
    q = quote("01276-26", authored("r-1", 1))
    doc = pdf("Cotización CN01276 — Cliente Ficticio.pdf", tokens=())
    fresh = mail(doc, linked_case_ids=(), thread_id="t-new")
    [a] = r16(fresh, cases=(case(q),))
    assert [c["command"] for c in a.commands] == ["record_authored_quote_sent", "link_case_evidence",
                                                  "advance_case_stage"]
    assert a.commands[1]["inputs"]["source_record_id"] == "e-1" and a.commands[1]["inputs"]["note"].startswith(
        "Sistema (correo) R16")
    # On another case's thread it stays with a person.
    assert r16(fresh, cases=(case(q), case(id="c-2", thread_ids=("t-new",)))) == []
