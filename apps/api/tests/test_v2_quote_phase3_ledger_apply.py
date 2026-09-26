"""Phase 3 ledger apply: what may be appended, the append itself, reruns. Invented fixtures only;
the ledgers written here live under tmp_path."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from origenlab_api.v2 import quote_phase3_ledger_apply as ap
from origenlab_api.v2 import quote_worksheet_resolution as w

A, B, C, D = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
INDEX = w.DocumentIndex([A, B, C, D])
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
OPTIONS = {
    "direction": "yes_sent_to_client | no_not_a_customer_quotation | unsure",
    "grouping": "one_opportunity | one_per_document | split (list the documents per opportunity)",
    "same_client_other_thread": "same_opportunity_as:<doc> | new_opportunity",
    "serial_collision": "keep_both_as_separate_quotations | this_one_not_sent | leave_pending",
    "canonical_version": "version_of_same_quotation (canonical:<doc>) | exact_resend | separate_quotations",
    "identify_generic_client": "client:<name / organization> | leave_pending",
    "labdelivery_issuer": "origenlab_quotation | labdelivery_quotation_not_origenlab | leave_pending",
}
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def row(qid, dtype, answer, docs, *, ctx=()):
    return {"question_id": qid, "decision_type": dtype, "answer": answer, "answer_options": OPTIONS.get(dtype, ""),
            "document_sha256": " ".join(docs), "context_documents": " ".join(ctx), "question": "",
            "case_ids": " ".join(f"doc:{d[:12]}" for d in docs)}


def case(sha, *, number="01001-26", sent="2026-05-01T10:00:00-04:00", needed=()):
    return w.Case(case_id=f"doc:{sha[:12]}", target="document", sha256=sha, email_id=1, sent_at=sent,
                  printed_number=number, widened_number=None, decisions_needed=tuple(needed))


def entry(intent: w.LedgerIntent) -> dict:
    """The ledger row the dry run builds for an intent, reduced to the fields the apply reads."""
    opp = None
    if intent.decision == ap.CONFIRM:
        opp = {"mode": intent.opportunity_mode, "opportunity_id": None, "planned_opportunity_id": intent.opportunity_id}
    return {"decision_id": intent.decision_id, "target": "quote_document", "document_sha256": intent.target,
            "decision": intent.decision, "quote_number": intent.quote_number, "opportunity": opp,
            "operator": ap.OPERATOR, "applied": False, "recorded_at": "2026-09-25T00:00:00+00:00",
            "reason": intent.reason,
            "phase3": dict(intent.provenance) | {"gates_version": w.GATES_VERSION}}


def resolve(rows, cases):
    resolution = w.resolve(w.normalize_worksheet(rows, INDEX), cases, {})
    entries = [entry(i) for i in w.ledger_intents(resolution, {})]
    numbers = {s: r.quote_number for s, r in resolution.documents.items() if r.outcome in w.CONFIRM_OUTCOMES}
    return resolution, entries, numbers


def plan(entries, numbers, base=(), extra=()):
    return ap.plan_apply(entries, entries, numbers, {ap.DOCUMENT_LEDGER: list(base)},
                         {ap.DOCUMENT_LEDGER: list(extra)})


def load_script():
    sys.path.insert(0, str(_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("quote_phase3_ledger_apply_script",
                                                      _SCRIPTS / "quote_phase3_ledger_apply.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(_SCRIPTS))


def write_ledger(path: Path, rows) -> str:
    path.write_bytes(b"".join(ap.serialize_row(r) for r in rows))
    return ap.sha256_bytes(path.read_bytes())


# ─── approvals and the retained list ───────────────────────────────────────────────────────────


def test_both_approved_fingerprints_hold():
    ap.assert_approved()
    assert w.FROZEN_POLICY_SHA256 == ap.APPROVED_POLICY_SHA256
    assert w.FROZEN_GATES_SHA256 == ap.APPROVED_GATES_SHA256


def test_a_changed_gate_refuses_the_apply(monkeypatch):
    monkeypatch.setattr(w, "GATE_RULES", (*w.GATE_RULES, ("G6", "invented")))
    with pytest.raises(w.PolicyChanged):
        ap.assert_approved()


def test_retained_list_is_the_owner_twenty():
    reasons = [r for rs in ap.EXCLUDED_DOCUMENTS.values() for r in rs]
    assert len(ap.EXCLUDED_DOCUMENTS) == 20
    assert all(len(s) == 64 for s in ap.EXCLUDED_DOCUMENTS)
    assert (reasons.count(ap.X_G1), reasons.count(ap.X_G2), reasons.count(ap.X_01242),
            reasons.count(ap.X_CLIENT), reasons.count(ap.X_W114)) == (13, 2, 2, 3, 2)


def test_resolution_blocking_other_documents_than_the_retained_list_refuses():
    assert ap.exclusion_problems(ap.EXCLUDED_DOCUMENTS) == []
    assert ap.exclusion_problems([*ap.EXCLUDED_DOCUMENTS, A])
    assert ap.exclusion_problems(list(ap.EXCLUDED_DOCUMENTS)[1:])


# ─── W103, W116, G1, G2, G3 ────────────────────────────────────────────────────────────────────


def test_w103_versions_of_one_quotation_append_on_one_opportunity_and_a_rerun_writes_nothing():
    rows = [
        row("W103", "serial_collision", "keep_both_same_quotation_versions", [A, B]),
        row("W111", "canonical_version", f"version_of_same_quotation (canonical:{B[:12]})", [A, B]),
    ]
    _, entries, numbers = resolve(rows, [case(A, number="012230A-26", sent="2026-07-13T10:00:00-04:00"),
                                         case(B, number="012230A-26", sent="2026-07-22T10:00:00-04:00")])
    assert [(e["document_sha256"], e["opportunity"]["mode"]) for e in entries] == [(A, "create_new"), (B, "planned")]
    assert entries[0]["opportunity"]["planned_opportunity_id"] == entries[1]["opportunity"]["planned_opportunity_id"]
    assert [e["phase3"]["version_role"] for e in entries] == ["prior", "canonical"]
    first = plan(entries, numbers)
    assert first.ok and first.writes == 2  # same number, one opportunity: G1 holds
    rerun = plan(entries, numbers, extra=[ap.stamp(e, NOW) for e in entries])
    assert rerun.ok and rerun.writes == 0 and len(rerun.ledgers[ap.DOCUMENT_LEDGER].already) == 2


def test_w116_labdelivery_quotation_is_rejected_without_an_opportunity_or_joining_its_context():
    rows = [
        row("W095", "labdelivery_issuer", "labdelivery_quotation_not_origenlab", [A]),
        row("W099", "direction", "yes_sent_to_client", [A]),
        row("W116", "same_client_other_thread", "not_applicable:labdelivery_quotation", [A], ctx=[B]),
        row("W200", "direction", "yes_sent_to_client", [B]),
    ]
    resolution, entries, numbers = resolve(rows, [case(A, number="12503A-26"), case(B, number="01230-26")])
    by_doc = {e["document_sha256"]: e for e in entries}
    assert by_doc[A]["decision"] == ap.REJECT and by_doc[A]["opportunity"] is None and by_doc[A]["quote_number"] is None
    assert by_doc[B]["decision"] == ap.CONFIRM
    assert resolution.documents[A].group != resolution.documents[B].group
    p = plan(entries, numbers)
    assert p.ok and p.writes == 2
    # A tampered row giving the rejection a number is refused.
    bad = [dict(by_doc[A], quote_number="12503A-26"), by_doc[B]]
    assert not plan(bad, numbers).ok


def test_g1_number_on_two_opportunities_is_never_appended():
    rows = [
        row("W1", "serial_collision", "keep_both_as_separate_quotations", [A, B]),
        row("W2", "same_client_other_thread", "new_opportunity", [A], ctx=[B]),
    ]
    resolution, entries, numbers = resolve(rows, [case(A, needed=["serial_collision"]),
                                                  case(B, needed=["serial_collision"])])
    assert entries == []
    assert all(w.B_NUMBER_OTHER_OPP in {b.code for b in r.blockers} for r in resolution.documents.values())
    # Rows forced in anyway: same number on two opportunities.
    forced = [
        {**entry(w.LedgerIntent("document_decisions.jsonl", s, ap.CONFIRM, f"id-{s[:4]}", "create_new",
                                opp, "01001-26", "x", {"policy_version": w.POLICY_VERSION, "questions": ["W1"]})),
         "phase3": {"policy_version": w.POLICY_VERSION, "gates_version": w.GATES_VERSION, "questions": ["W1"]}}
        for s, opp in ((A, "11111111-1111-1111-1111-111111111111"), (B, "22222222-2222-2222-2222-222222222222"))
    ]
    p = plan(forced, {A: "01001-26", B: "01001-26"})
    assert not p.ok and any(x.startswith("G1: 01001-26") for x in p.problems)
    # And a real retained collision document is refused by hash, whatever the row says.
    real = next(s for s, r in ap.EXCLUDED_DOCUMENTS.items() if r == (ap.X_G1,))
    row_real = {**forced[0], "document_sha256": real}
    assert any("documento retenido" in x for x in ap.entry_problems(row_real, {real: "01001-26"}))


def test_g2_file_name_number_is_never_recorded():
    rows = [row("W1", "direction", "yes_sent_to_client", [A])]
    resolution, entries, numbers = resolve(rows, [case(A, number=None)])
    assert entries == [] and w.B_NO_NUMBER in {b.code for b in resolution.documents[A].blockers}
    forced = {"decision_id": "x", "target": "quote_document", "document_sha256": A, "decision": ap.CONFIRM,
              "quote_number": "01200-26", "operator": ap.OPERATOR, "applied": False,
              "opportunity": {"mode": "create_new", "opportunity_id": None, "planned_opportunity_id": "p"},
              "phase3": {"policy_version": w.POLICY_VERSION, "gates_version": w.GATES_VERSION, "questions": ["W1"]}}
    assert any("(G2)" in x for x in ap.entry_problems(forced, numbers))
    for cn in (s for s, r in ap.EXCLUDED_DOCUMENTS.items() if ap.X_G2 in r):
        assert any("documento retenido" in x for x in ap.entry_problems({**forced, "document_sha256": cn}, {cn: "01200-26"}))


def test_g3_partial_opportunity_appends_its_ready_document_and_holds_only_the_crm():
    rows = [
        row("W1", "grouping", "one_opportunity", [A, B]),
        row("W2", "identify_generic_client", "leave_pending", [B]),
    ]
    resolution, entries, numbers = resolve(rows, [case(A, number="01125-26"), case(B, number="01127-26")])
    assert [e["document_sha256"] for e in entries] == [A]
    assert resolution.documents[A].automatable and not resolution.documents[B].automatable
    assert w.crm_holds(resolution) == {resolution.documents[A].group: (B,)}
    p = plan(entries, numbers)
    assert p.ok and p.writes == 1
    # W114 itself is retained: a row citing it is refused.
    cited = dict(entries[0], phase3=dict(entries[0]["phase3"], questions=["W114"]))
    assert any("W114" in x for x in ap.entry_problems(cited, numbers))


# ─── ledger state: no overwrite, no reorder, rerun ─────────────────────────────────────────────


def _two_entries():
    rows = [row("W1", "grouping", "one_opportunity", [A, B])]
    _, entries, numbers = resolve(rows, [case(A, number="01001-26"), case(B, number="01002-26",
                                                                          sent="2026-05-02T10:00:00-04:00")])
    return entries, numbers


def test_a_plan_row_on_a_document_that_already_has_a_decision_is_refused():
    entries, numbers = _two_entries()
    existing = {"decision_id": "old", "target": "quote_document", "document_sha256": A, "decision": "leave_pending"}
    p = plan(entries, numbers, base=[existing])
    assert not p.ok and any("no se sobrescribe" in x for x in p.ledgers[ap.DOCUMENT_LEDGER].problems)


def test_foreign_reordered_or_edited_rows_after_the_audit_refuse():
    entries, numbers = _two_entries()
    assert not plan(entries, numbers, extra=[entries[1]]).ok  # reordered
    assert not plan(entries, numbers, extra=[dict(entries[0], quote_number="09999-26")]).ok  # edited
    assert not plan(entries, numbers, extra=[{"decision_id": "z", "target": "quote_document",
                                              "document_sha256": C, "decision": "leave_pending"}]).ok  # foreign
    resumed = plan(entries, numbers, extra=[ap.stamp(entries[0], NOW)])  # stopped after row 1
    assert resumed.ok and [e["document_sha256"] for e in resumed.ledgers[ap.DOCUMENT_LEDGER].to_append] == [B]


def test_plan_must_match_the_recomputed_resolution():
    entries, numbers = _two_entries()
    p = ap.plan_apply(entries, entries[:1], numbers, {}, {})
    assert not p.ok


def test_audited_prefix_finds_the_audited_bytes_or_refuses():
    lines = [b'{"a":1}\n', b'{"a":2}\n', b'{"a":3}\n']
    assert ap.audited_prefix(lines, ap.sha256_bytes(b"".join(lines[:2]))) == 2
    assert ap.audited_prefix(lines, ap.sha256_bytes(b"")) == 0
    assert ap.audited_prefix(lines, ap.sha256_bytes(b"other")) is None


# ─── the script's writes ───────────────────────────────────────────────────────────────────────


def test_backup_then_append_writes_exactly_the_planned_bytes_and_a_rerun_nothing(tmp_path):
    s = load_script()
    entries, numbers = _two_entries()
    ledger = tmp_path / ap.DOCUMENT_LEDGER
    base = [{"decision_id": "old", "target": "quote_document", "document_sha256": C, "decision": "leave_pending"}]
    base_sha = write_ledger(ledger, base)
    base_bytes = ledger.read_bytes()

    dest = s.backup(ledger, "TS")
    assert dest.name == f"{ap.DOCUMENT_LEDGER}.bak-pre-phase3-TS" and dest.read_bytes() == base_bytes
    with pytest.raises(FileExistsError):
        s.backup(ledger, "TS")

    rows = [ap.stamp(e, NOW) for e in plan(entries, numbers, base=base).ledgers[ap.DOCUMENT_LEDGER].to_append]
    written, stop = s.append_rows(ledger, rows, base_sha)
    assert stop is None and written == rows
    assert ledger.read_bytes() == base_bytes + b"".join(ap.serialize_row(r) for r in rows)

    lines = ledger.read_bytes().splitlines(keepends=True)
    n = ap.audited_prefix(lines, base_sha)
    extra = [json.loads(x) for x in lines[n:]]
    rerun = plan(entries, numbers, base=base, extra=extra)
    assert rerun.ok and rerun.writes == 0
    before = ledger.read_bytes()
    written, stop = s.append_rows(ledger, [], ap.sha256_bytes(before))
    assert written == [] and stop is None and ledger.read_bytes() == before


def test_append_stops_when_someone_else_wrote_the_ledger(tmp_path):
    s = load_script()
    entries, _ = _two_entries()
    ledger = tmp_path / ap.DOCUMENT_LEDGER
    base_sha = write_ledger(ledger, [])
    ledger.write_bytes(b'{"decision_id":"foreign"}\n')
    written, stop = s.append_rows(ledger, [ap.stamp(e, NOW) for e in entries], base_sha)
    assert written == [] and stop and ledger.read_bytes() == b'{"decision_id":"foreign"}\n'


def test_apply_without_the_approved_plan_hash_and_out_is_refused(capsys):
    s = load_script()
    assert s.main(["--apply"]) == 2
    assert "--expect-plan-sha256" in capsys.readouterr().err
