"""The obvious decisions the system takes by itself (spec 2026-10-19): R14 records a sent quote
already on its case, R15 confirms an institution a sent quote settled. Pure planning, no database.

Every address, domain, name and number here is fictitious (`.test`, `example.test`).
"""

from __future__ import annotations

from origenlab_api.v2.mail_rules import (
    RULE_IDS,
    CaseQuote,
    CaseState,
    InstitutionFacts,
    MailDocument,
    MailEvidence,
    Organization,
    QuoteRevision,
    plan,
)
from origenlab_api.v2.mail_rules import Snapshot as _Snapshot

PDF_SHA = "a" * 64
#: R14's window start, as the repository sets it (60 days back); the test emails are sent after it.
SINCE = "2026-09-01T00:00:00+00:00"


def Snapshot(**kw) -> _Snapshot:  # noqa: N802 - the dataclass, with R14's window filled in
    kw.setdefault("late_quote_since", SINCE)
    return _Snapshot(**kw)


def mail(**over) -> MailEvidence:
    base = dict(
        id="e-1", thread_id="t-1", direction="outbound", sender="Ventas <contacto@origenlab.cl>",
        recipients=("Compras <compras@cliente.test>",), subject="RE: Cotización equipos",
        sent_at="2026-10-01T15:00:00+00:00",
        documents=(MailDocument(filename="CN012395-cliente.pdf", sha256=PDF_SHA, cn_tokens=("CN12395",)),),
        linked_case_ids=("c-1",),
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
                closed=False, thread_ids=("t-1",), quotes=())
    base.update(over)
    return CaseState(**base)


def quote(number: str) -> CaseQuote:
    return CaseQuote(id=f"q-{number}", quote_number=number,
                     revisions=(QuoteRevision(id="r-1", revision_no=1, status="sent", version=1, superseded=False),))


def r14(snapshot: Snapshot):
    return [a for a in plan(snapshot) if a.rule_id == "R14"]


# ------------------------------------------------------------------ R14


def test_rule_ids_name_r14_and_r15() -> None:
    assert RULE_IDS[-2:] == ("R14", "R15")


def test_r14_records_a_new_number_on_an_email_another_rule_already_linked() -> None:
    # The planner skipped every applied email; R14 is the one rule that still looks at it.
    snap = Snapshot(evidence=(mail(),), cases=(case(stage="qualified", quotes=(quote("01200-26"),)),),
                    organizations=(org(),), applied_evidence_ids=frozenset({"e-1"}),
                    applied_pairs=frozenset({("e-1", "R1")}))
    [a] = r14(snap)
    assert (a.mode, a.case_id, a.quote_number) == ("auto", "c-1", "01239-26")
    commands = [c["command"] for c in a.commands]
    # No link: the email is already on the case. The case walks to «Enviada», the quote is recorded.
    assert commands == ["advance_case_stage", "record_historical_quotation"]
    record = a.commands[-1]["inputs"]
    assert record["opportunity_id"] == "@case" and record["ledger_decision_id"] == "mail-rule:e-1:R14"
    assert a.context == {"case": "c-1", "case_version": 7, "from_stage": "qualified"}


def test_r14_records_on_an_email_a_person_linked_too() -> None:
    snap = Snapshot(evidence=(mail(),), cases=(case(),), organizations=(org(),))
    [a] = r14(snap)
    assert [c["command"] for c in a.commands] == ["record_historical_quotation"]  # already «Enviada»


def test_r14_runs_once_per_email_even_after_an_undo() -> None:
    snap = Snapshot(evidence=(mail(),), cases=(case(),), organizations=(org(),),
                    applied_evidence_ids=frozenset({"e-1"}), applied_pairs=frozenset({("e-1", "R1"), ("e-1", "R14")}))
    assert r14(snap) == []


def test_r14_leaves_a_case_without_institution_to_a_person() -> None:
    snap = Snapshot(evidence=(mail(),), cases=(case(organization_id=None),), organizations=(org(),))
    assert r14(snap) == []


def test_r14_leaves_a_correlative_of_another_year_to_a_person() -> None:
    other = case(id="c-2", thread_ids=(), quotes=(quote("01239-25"),))
    snap = Snapshot(evidence=(mail(),), cases=(case(), other), organizations=(org(),))
    assert r14(snap) == []


def test_r14_never_touches_a_closed_case_or_a_number_already_recorded() -> None:
    closed = Snapshot(evidence=(mail(),), cases=(case(stage="lost", closed=True),), organizations=(org(),))
    assert r14(closed) == []
    recorded = Snapshot(evidence=(mail(),), cases=(case(quotes=(quote("01239-26"),)),), organizations=(org(),))
    assert r14(recorded) == []


def test_r14_never_records_an_email_sent_before_its_window() -> None:
    # A backfilled old email on a case still open: no old quote recorded, no stage moved.
    old = Snapshot(evidence=(mail(sent_at="2026-08-01T15:00:00+00:00"),), cases=(case(stage="qualified"),),
                   organizations=(org(),))
    assert r14(old) == []
    no_window = Snapshot(evidence=(mail(),), cases=(case(),), organizations=(org(),), late_quote_since=None)
    assert r14(no_window) == []
    undated = Snapshot(evidence=(mail(sent_at=None),), cases=(case(),), organizations=(org(),))
    assert r14(undated) == []


def test_r14_needs_one_open_case_contacto_and_one_new_pdf() -> None:
    two = Snapshot(evidence=(mail(linked_case_ids=("c-1", "c-2")),),
                   cases=(case(), case(id="c-2")), organizations=(org(),))
    assert r14(two) == []
    someone_else = Snapshot(evidence=(mail(sender="ventas@otra.test"),), cases=(case(),), organizations=(org(),))
    assert r14(someone_else) == []
    inbound = Snapshot(evidence=(mail(direction="inbound", sender="compras@cliente.test"),), cases=(case(),),
                       organizations=(org(),))
    assert r14(inbound) == []
    pair = (MailDocument(filename="CN012395-a.pdf", sha256=PDF_SHA, cn_tokens=("CN12395",)),
            MailDocument(filename="CN012405-b.pdf", sha256="b" * 64, cn_tokens=("CN12405",)))
    several = Snapshot(evidence=(mail(documents=pair),), cases=(case(),), organizations=(org(),))
    assert r14(several) == []


# ------------------------------------------------------------------ R15


def proposed(**over) -> Organization:
    base = dict(id="o-9", name="acme-ejemplo.example", version=2, confirmation="machine_proposed",
                domains=("acme-ejemplo.example",), is_supplier=False)
    base.update(over)
    return Organization(**base)


def facts(**over) -> InstitutionFacts:
    base = dict(organization_id="o-9", created_by="R4",
                settled_quote_files=("CN01300-Persona Ficticia - Acme Ejemplo SpA.pdf",))
    base.update(over)
    return InstitutionFacts(**base)


def r15(snapshot: Snapshot):
    return [a for a in plan(snapshot) if a.rule_id == "R15"]


def test_r15_renames_a_domain_named_institution_from_its_quote_and_confirms_it() -> None:
    [a] = r15(Snapshot(evidence=(), cases=(), organizations=(proposed(),), institutions=(facts(),)))
    assert (a.mode, a.evidence_id, a.organization_id, a.organization_name) == (
        "auto", "o-9", "o-9", "Acme Ejemplo SpA")
    assert [c["command"] for c in a.commands] == ["update-organization", "confirm-organization-record"]
    rename, confirm = (c["inputs"] for c in a.commands)
    assert rename == {"organization_id": "o-9", "expected_version": "@organization_version",
                      "name": "Acme Ejemplo SpA", "note": rename["note"]}
    assert confirm["expected_version"] == "@organization_version"
    assert a.context == {"organization": "o-9", "organization_version": 2, "previous_name": "acme-ejemplo.example"}


def test_r15_confirms_an_institution_the_pdf_named_without_renaming() -> None:
    named = proposed(name="Instituto Ficticio (IFI)", domains=())
    [a] = r15(Snapshot(evidence=(), cases=(), organizations=(named,),
                       institutions=(facts(created_by="R10", settled_quote_files=("CN01300.pdf",)),)))
    assert [c["command"] for c in a.commands] == ["confirm-organization-record"]
    assert "previous_name" not in a.context


def test_r15_counts_one_segment_across_quotes_and_refuses_none_or_several() -> None:
    same = facts(settled_quote_files=("CN01300-Una Persona - Acme Ejemplo SpA.pdf", "CN01301-Otra Persona - Acme Ejemplo SpA.pdf"))
    assert len(r15(Snapshot(evidence=(), cases=(), organizations=(proposed(),), institutions=(same,)))) == 1
    several = facts(settled_quote_files=("CN01300-Una Persona - Acme Ejemplo SpA.pdf", "CN01301-Laboratorio Ficticio Ltda.pdf"))
    assert r15(Snapshot(evidence=(), cases=(), organizations=(proposed(),), institutions=(several,))) == []
    none = facts(settled_quote_files=("CN01300-Persona Ficticia.pdf", None))
    assert r15(Snapshot(evidence=(), cases=(), organizations=(proposed(),), institutions=(none,))) == []


def test_r15_refuses_free_mail_suppliers_people_and_unsettled_quotes() -> None:
    def planned(o: Organization, f: InstitutionFacts) -> list:
        return r15(Snapshot(evidence=(), cases=(), organizations=(o,), institutions=(f,)))

    assert planned(proposed(name="gmail.com", domains=("gmail.com",)), facts()) == []
    assert planned(proposed(is_supplier=True), facts()) == []
    assert planned(proposed(), facts(settled_quote_files=())) == []  # no quote sent 3 days ago
    assert planned(proposed(), facts(created_by="R11")) == []  # a forward's company: not R4/R10/complete-case
    assert planned(proposed(), facts(created_by=None)) == []  # registered by a person
    assert planned(proposed(confirmation="confirmed"), facts()) == []
    assert len(planned(proposed(), facts(created_by="complete_case"))) == 1


def test_r15_never_renames_to_a_generic_word() -> None:
    # The unattended rename must say which institution, not only what kind of one.
    for tail in ("Persona Ficticia - Laboratorio", "Persona Ficticia - Laboratorio Chile",
                 "Persona Ficticia - Centro (CEN)", "Persona Ficticia - Comercial"):
        f = facts(settled_quote_files=(f"CN01300-{tail}.pdf",))
        assert r15(Snapshot(evidence=(), cases=(), organizations=(proposed(),), institutions=(f,))) == [], tail
    ok = facts(settled_quote_files=("CN01300-Persona Ficticia - Laboratorio Ejemplo Ltda.pdf",))
    [a] = r15(Snapshot(evidence=(), cases=(), organizations=(proposed(),), institutions=(ok,)))
    assert a.organization_name == "Laboratorio Ejemplo Ltda"


def test_r15_leaves_a_rename_that_would_duplicate_another_institution() -> None:
    twin = org(id="o-2", name="Acme Ejemplo SpA", domains=())
    assert r15(Snapshot(evidence=(), cases=(), organizations=(proposed(), twin), institutions=(facts(),))) == []


def test_r15_never_acts_twice_on_one_institution() -> None:
    snap = Snapshot(evidence=(), cases=(), organizations=(proposed(),), institutions=(facts(),),
                    applied_evidence_ids=frozenset({"o-9"}), applied_pairs=frozenset({("o-9", "R15")}))
    assert r15(snap) == []
