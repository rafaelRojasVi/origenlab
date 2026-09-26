"""The quotation CRM import planner (pure) and its CLI guards. Fictitious data throughout."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from origenlab_api.v2 import quote_crm_import_plan as qp

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
OPP_A = "11111111-1111-5111-8111-111111111111"
OPP_B = "22222222-2222-5222-8222-222222222222"
OPP_HELD = "33333333-3333-5333-8333-333333333333"


def sha(ch: str) -> str:
    return ch * 64


def row(doc: str, number: str, opp: str, *, email: int, sent: str = "2026-03-01T10:00:00-03:00",
        printed: list[str] | None = None, phase3: dict | None = None, decision: str = qp.CONFIRM) -> dict:
    return {
        "decision": decision, "decision_id": f"d-{doc[:4]}", "document_sha256": doc, "operator": "Rafael",
        "quote_number": number, "printed_quote_numbers": [number] if printed is None else printed,
        "opportunity": {"mode": "planned", "planned_opportunity_id": opp},
        "occurrences": [{"email_id": email, "sent_at": sent, "filename": f"{number}.pdf",
                         "gmail_message_id": f"g{email}", "gmail_thread_id": f"t{email}"}],
        **({"phase3": phase3} if phase3 else {}),
    }


def staged(email: int, docs: list[str], recipients: str = "compras@cliente.test") -> dict:
    return {"kind": "gmail_message", "dedupe_key": f"gmail_message:g{email}", "source_uri": f"gmail://msg/g{email}",
            "payload": {"source_email_id": email, "gmail_message_id": f"g{email}", "gmail_thread_id": f"t{email}",
                        "gmail_label_ids": ["SENT"], "recipients": recipients, "sender": "ventas@origenlab.cl",
                        "documents": [{"sha256": d} for d in docs]}}


def identity(org: str | None) -> dict:
    return {"clients": [{"organization": org, "addressee": {"value": f"Persona – {org}" if org else "Persona"}}]}


def build(rows, staged_list, *, blocked=None, blocked_opps=None, confirmations=None, identities=None, texts=None):
    return qp.build_plan(
        rows, identities or {}, {s["payload"]["source_email_id"]: s for s in staged_list},
        blocked or {}, blocked_opps or {}, confirmations or {}, texts,
        acquired_at="2026-09-24T16:44:00+00:00", note="prueba",
    )


def opp(intent, planned):
    return next(o for o in intent["opportunities"] if o["planned_opportunity_id"] == planned)


CONF_A = {"planned_opportunity_id": OPP_A, "action": "create_from_evidence", "confirmed_by": "Rafael",
          "confirmed_at": "2026-09-25T00:00:00Z", "note": "confirmada"}


def basic():
    rows = [row(sha("a"), "01001-26", OPP_A, email=1), row(sha("b"), "01002-26", OPP_HELD, email=2)]
    return rows, [staged(1, [sha("a")]), staged(2, [sha("b"), sha("c")])]


def test_a_blocked_document_holds_its_whole_opportunity_and_stages_nothing() -> None:
    rows, st = basic()
    intent = build(rows, st, blocked={sha("c"): ["G1"]}, blocked_opps={sha("c"): OPP_HELD},
                   confirmations={OPP_A: CONF_A}, identities={sha("a"): identity("Instituto Uno")})
    held = opp(intent, OPP_HELD)
    assert held["status"] == qp.S_HELD and held["steps"] == []
    assert any(r.endswith(qp.R_OPP_BLOCKED) for r in held["reasons"])
    staged_keys = {r["external_id"] for r in intent["evidence_manifest"]["records"]}
    assert staged_keys == {"g1"}


def pending(doc: str, number: str, opp_id: str, *, email: int, **kw) -> dict:
    return qp.build_document_pending(
        row(doc, number, opp_id, email=email), code=qp.PENDING_NO_ORGANIZATION,
        detail="El PDF sólo nombra a una persona, sin institución.", operator="Rafael",
        owner_instruction="Instrucción del owner (prueba).", recorded_at="2026-09-26T03:00:00+00:00", **kw)


def test_a_document_left_pending_holds_its_opportunity_explicitly_and_stages_nothing() -> None:
    rows = [row(sha("a"), "01001-26", OPP_A, email=1), pending(sha("b"), "01002-26", OPP_HELD, email=2)]
    intent = build(rows, [staged(1, [sha("a")]), staged(2, [sha("b")])], confirmations={OPP_A: CONF_A},
                   identities={sha("a"): identity("Instituto Uno")})
    held = opp(intent, OPP_HELD)
    assert held["status"] == qp.S_HELD and held["steps"] == [] and held["documents"] == [sha("b")]
    assert held["reasons"] == [f"{sha('b')[:12]}:{qp.R_LEFT_PENDING}"]
    assert intent["pending_documents"] == [{
        "document_sha256": sha("b"), "quote_number": "01002-26", "planned_opportunity_id": OPP_HELD,
        "code": qp.PENDING_NO_ORGANIZATION, "ledger_decision_id": rows[1]["decision_id"]}]
    assert intent["summary"]["pending_documents"] == 1
    assert {r["external_id"] for r in intent["evidence_manifest"]["records"]} == {"g1"}
    assert qp.evidence_scope_violations(intent) == []
    # The unheld (request-basis) scope leaves it out too: it is held, not waiting.
    unheld = qp.build_plan(rows, {}, {1: staged(1, [sha("a")]), 2: staged(2, [sha("b")])}, {}, {}, {}, None,
                           acquired_at="2026-09-24T16:44:00+00:00", note="prueba",
                           evidence_scope=qp.EVIDENCE_SCOPE_UNHELD)
    assert {r["external_id"] for r in unheld["evidence_manifest"]["records"]} == {"g1"}


def test_a_pending_record_carries_the_document_and_is_resolved_only_by_a_later_row() -> None:
    confirm = row(sha("b"), "01002-26", OPP_HELD, email=2)
    record = pending(sha("b"), "01002-26", OPP_HELD, email=2)
    assert qp.document_pending_problems(record) == []
    assert record["decision"] == "leave_pending" and record["target"] == "quote_document"
    assert record["pending"]["scope"] == "document" and record["decision_id"] != confirm["decision_id"]
    assert record["occurrences"] == confirm["occurrences"] and record["quote_number"] == "01002-26"
    # Deterministic: the same instruction yields the same row.
    assert record == pending(sha("b"), "01002-26", OPP_HELD, email=2)
    st = [staged(2, [sha("b")])]
    # Append-only: a later confirmation of the same document supersedes the pending record.
    later = build([record, confirm], st)
    assert opp(later, OPP_HELD)["status"] == qp.S_NEEDS_ORG and "pending_documents" not in later


def test_without_pending_records_the_intent_is_unchanged() -> None:
    rows, st = basic()
    intent = build(rows, st)
    assert "pending_documents" not in intent and "pending_documents" not in intent["summary"]
    # An email-review style leave_pending (no document scope) is still not a quotation.
    plain = dict(row(sha("a"), "01001-26", OPP_A, email=1), decision="leave_pending")
    assert build([plain], [staged(1, [sha("a")])])["documents"] == []


@pytest.mark.parametrize("breakage", [
    lambda r: r["pending"].update(code="porque_si"),
    lambda r: r["pending"].update(detail=" "),
    lambda r: r.update(operator=""),
    lambda r: r.update(occurrences=[]),
    lambda r: r.update(quote_number=None),
    lambda r: r["opportunity"].update(planned_opportunity_id=None),
    lambda r: r.update(shadow={"canonical_ledger_row": False}),
    lambda r: r.pop("recorded_at"),
])
def test_a_malformed_pending_record_refuses_the_plan(breakage) -> None:
    record = pending(sha("b"), "01002-26", OPP_HELD, email=2)
    breakage(record)
    assert qp.document_pending_problems(record)
    with pytest.raises(ValueError, match="leave_pending"):
        build([record], [staged(2, [sha("b")])])


def test_the_pending_builder_refuses_a_template_it_cannot_carry() -> None:
    with pytest.raises(ValueError, match="code"):
        qp.build_document_pending(row(sha("b"), "01002-26", OPP_HELD, email=2), code="porque_si", detail="x",
                                  operator="Rafael", owner_instruction="x", recorded_at="2026-09-26T03:00:00+00:00")
    rejected = row(sha("b"), "01002-26", OPP_HELD, email=2, decision="reject_non_quotation")
    with pytest.raises(ValueError, match="confirm_customer_quotation"):
        qp.build_document_pending(rejected, code=qp.PENDING_NO_ORGANIZATION, detail="x", operator="Rafael",
                                  owner_instruction="x", recorded_at="2026-09-26T03:00:00+00:00")


def test_labdelivery_only_material_never_enters() -> None:
    rows = [row(sha("a"), "01001-26", OPP_A, email=1, phase3={"questions": ["W095", "W116"]})]
    intent = build(rows, [staged(1, [sha("a")])])
    assert qp.R_LABDELIVERY in intent["documents"][0]["reasons"]
    assert opp(intent, OPP_A)["status"] == qp.S_HELD


def test_a_rejected_document_is_not_a_quotation() -> None:
    rows = [row(sha("a"), "01001-26", OPP_A, email=1, decision="reject_non_quotation")]
    assert build(rows, [staged(1, [sha("a")])])["documents"] == []


def test_the_number_must_be_printed_and_the_print_wins_over_a_dropped_zero() -> None:
    rows = [row(sha("a"), "1218-26", OPP_A, email=1, printed=["01218-26"]),
            row(sha("b"), "9999-26", OPP_B, email=2, printed=["01218-26"])]
    intent = build(rows, [staged(1, [sha("a")]), staged(2, [sha("b")])])
    a = opp(intent, OPP_A)
    assert a["quotes"][0]["quote_number"] == "01218-26"
    assert a["warnings"][0]["code"] == qp.W_LEADING_ZERO
    assert qp.R_NOT_PRINTED in next(d for d in intent["documents"] if d["document_sha256"] == sha("b"))["reasons"]


def test_a_number_the_parser_missed_counts_only_when_it_is_verbatim_in_the_text() -> None:
    text = "COTIZACIÓN N°011453AI-26          Valdivia, 3 de junio"
    assert qp.printed_numbers_in_text(text) == {"011453AI-26"}
    rows = [row(sha("a"), "011453AI-26", OPP_A, email=1, printed=[])]
    ok = build(rows, [staged(1, [sha("a")])], texts={sha("a"): ["011453AI-26"]})
    assert ok["documents"][0]["reasons"] == []
    missing = build(rows, [staged(1, [sha("a")])])
    assert qp.R_NOT_PRINTED in missing["documents"][0]["reasons"]


def test_versions_canonical_supersedes_prior_and_undetermined_is_a_warning() -> None:
    rows = [
        row(sha("a"), "01001-26", OPP_A, email=1, sent="2026-03-01T10:00:00-03:00",
            phase3={"version_role": "prior", "canonical_sha256": sha("b")}),
        row(sha("b"), "01001-26", OPP_A, email=2, sent="2026-03-05T10:00:00-03:00",
            phase3={"version_role": "canonical", "canonical_sha256": sha("b")}),
        row(sha("c"), "01003-26", OPP_A, email=3), row(sha("d"), "01003-26", OPP_A, email=4),
    ]
    st = [staged(1, [sha("a")]), staged(2, [sha("b")]), staged(3, [sha("c")]), staged(4, [sha("d")])]
    intent = build(rows, st, confirmations={OPP_A: CONF_A}, identities={sha("a"): identity("Instituto Uno")})
    a = opp(intent, OPP_A)
    q1 = next(q for q in a["quotes"] if q["quote_number"] == "01001-26")
    assert [r["document_sha256"] for r in q1["revisions"]] == [sha("a"), sha("b")]
    assert q1["revisions"][1]["supersedes_document_sha256"] == sha("a")
    assert {w["code"] for w in a["warnings"]} == {qp.W_CANONICAL_UNDETERMINED}


def test_a_canonical_older_than_its_prior_holds_the_opportunity() -> None:
    rows = [
        row(sha("a"), "01001-26", OPP_A, email=1, sent="2026-03-09T10:00:00-03:00",
            phase3={"version_role": "prior", "canonical_sha256": sha("b")}),
        row(sha("b"), "01001-26", OPP_A, email=2, sent="2026-03-05T10:00:00-03:00",
            phase3={"version_role": "canonical", "canonical_sha256": sha("b")}),
    ]
    intent = build(rows, [staged(1, [sha("a")]), staged(2, [sha("b")])])
    assert opp(intent, OPP_A)["status"] == qp.S_HELD


def test_versions_across_numbers_stay_two_quotes() -> None:
    rows = [
        row(sha("a"), "011024AI-26", OPP_A, email=1, phase3={"version_role": "prior", "canonical_sha256": sha("b")}),
        row(sha("b"), "011024AII-26", OPP_A, email=2, sent="2026-03-09T10:00:00-03:00",
            phase3={"version_role": "canonical", "canonical_sha256": sha("b")}),
    ]
    a = opp(build(rows, [staged(1, [sha("a")]), staged(2, [sha("b")])]), OPP_A)
    assert len(a["quotes"]) == 2
    assert all(r["supersedes_document_sha256"] is None for q in a["quotes"] for r in q["revisions"])
    assert a["warnings"][0]["code"] == qp.W_VERSION_ACROSS_NUMBERS


def test_nothing_is_ready_without_an_operator_confirmation() -> None:
    rows, st = basic()
    intent = build(rows[:1], st[:1], identities={sha("a"): identity("Instituto Uno")})
    assert opp(intent, OPP_A)["status"] == qp.S_NEEDS_ORG
    assert intent["summary"]["commands_ready_to_run"] == 0


def test_a_confirmed_opportunity_has_the_full_ordered_command_path() -> None:
    rows = [row(sha("a"), "01001-26", OPP_A, email=1), row(sha("a") , "01001-26", OPP_A, email=1)]
    rows[0]["occurrences"].append({"email_id": 2, "sent_at": "2026-03-02T10:00:00-03:00", "filename": "x.pdf",
                                   "gmail_message_id": "g2", "gmail_thread_id": "t1"})
    intent = build(rows[:1], [staged(1, [sha("a")]), staged(2, [sha("a")])], confirmations={OPP_A: CONF_A},
                   identities={sha("a"): identity("Instituto Uno")})
    a = opp(intent, OPP_A)
    assert a["status"] == qp.S_READY
    assert [s["command"] for s in a["steps"]] == [
        "open_commercial_case", "link_case_evidence", "create_organization", "add_case_organization",
        "advance_case_stage", "advance_case_stage", "advance_case_stage", "record_historical_quotation"]
    assert len({s["key"] for s in a["steps"]}) == len(a["steps"])
    assert all(s["key"].startswith("qimport:v1:") for s in a["steps"])


def test_two_confirmations_may_not_create_one_organization_twice() -> None:
    rows = [row(sha("a"), "01001-26", OPP_A, email=1), row(sha("b"), "01002-26", OPP_B, email=2)]
    conf_b = {**CONF_A, "planned_opportunity_id": OPP_B}
    intent = build(rows, [staged(1, [sha("a")]), staged(2, [sha("b")])],
                   confirmations={OPP_A: CONF_A, OPP_B: conf_b},
                   identities={sha("a"): identity("Instituto Uno"), sha("b"): identity("instituto  uno")})
    assert opp(intent, OPP_A)["status"] == qp.S_READY
    b = opp(intent, OPP_B)
    assert b["status"] == qp.S_NEEDS_ORG
    assert b["warnings"][-1]["code"] == qp.R_DUPLICATE_CREATION


def test_the_plan_is_deterministic_and_the_second_dry_run_writes_nothing() -> None:
    rows = [row(sha("a"), "01001-26", OPP_A, email=1)]
    args = (rows, [staged(1, [sha("a")])])
    kw = dict(confirmations={OPP_A: CONF_A}, identities={sha("a"): identity("Instituto Uno")})
    first, second = build(*args, **kw), build(*args, **kw)
    assert qp.plan_sha256(first) == qp.plan_sha256(second)
    empty = qp.plan_state(first, qp.TargetState())
    assert empty["totals"]["commands_would_write"] == 7
    done = qp.TargetState(
        source_records={"gmail_message:g1": "x"},
        assertions={("gmail_message:g1", "document_reference", f"sha256:{sha('a')}"),
                    ("gmail_message:g1", "organization_name", "instituto uno")},
        receipts={s["key"]: "completed" for s in opp(first, OPP_A)["steps"]}, operator_registered=True)
    after = qp.plan_state(first, done)["totals"]
    assert after == {"operator_would_write": 0, "source_records_would_write": 0, "assertions_would_write": 0,
                     "commands_would_write": 0, "commands_done": 7, "commands_waiting_for_confirmation": 0}


def waiting_and_ready():
    """OPP_A confirmed (ready), OPP_B unconfirmed (waiting), OPP_HELD held; one message carries A and B."""
    rows = [row(sha("a"), "01001-26", OPP_A, email=1), row(sha("b"), "01002-26", OPP_B, email=2),
            row(sha("c"), "01003-26", OPP_HELD, email=3)]
    rows[1]["occurrences"].append({"email_id": 1, "sent_at": "2026-03-02T10:00:00-03:00", "filename": "b.pdf",
                                   "gmail_message_id": "g1", "gmail_thread_id": "t1"})
    st = [staged(1, [sha("a"), sha("b")]), staged(2, [sha("b")]), staged(3, [sha("c"), sha("d")])]
    ids = {sha("a"): identity("Instituto Uno"), sha("b"): identity("Instituto Dos")}
    return rows, st, ids


def test_evidence_is_staged_only_for_ready_opportunities() -> None:
    rows, st, ids = waiting_and_ready()
    intent = build(rows, st, blocked={sha("d"): ["G1"]}, blocked_opps={sha("d"): OPP_HELD},
                   confirmations={OPP_A: CONF_A}, identities=ids)
    assert [opp(intent, p)["status"] for p in (OPP_A, OPP_B, OPP_HELD)] == [qp.S_READY, qp.S_NEEDS_ORG, qp.S_HELD]
    records = intent["evidence_manifest"]["records"]
    # g1 is shared with the waiting OPP_B: it is staged for OPP_A, without OPP_B's observations.
    assert [r["external_id"] for r in records] == ["g1"]
    assert {(o["kind"], o["value"]) for o in records[0]["observations"]} == {
        ("document_reference", f"sha256:{sha('a')}"), ("organization_name", "Instituto Uno")}
    assert intent["evidence_scope"] == qp.EVIDENCE_SCOPE_READY
    assert qp.evidence_scope_violations(intent) == []
    # With no confirmation at all, nothing is staged.
    assert build(rows, st, identities=ids)["evidence_manifest"]["records"] == []


def test_the_unheld_scope_reproduces_the_request_basis_and_is_never_applicable() -> None:
    rows, st, ids = waiting_and_ready()
    legacy = qp.build_plan(rows, ids, {s["payload"]["source_email_id"]: s for s in st}, {}, {}, {}, None,
                           acquired_at="2026-09-24T16:44:00+00:00", note="prueba",
                           evidence_scope=qp.EVIDENCE_SCOPE_UNHELD)
    assert "evidence_scope" not in legacy
    assert {r["external_id"] for r in legacy["evidence_manifest"]["records"]} == {"g1", "g2", "g3"}
    assert {v["code"] for v in qp.evidence_scope_violations(legacy)} == {qp.V_EVIDENCE_OUTSIDE_READY,
                                                                          qp.V_OBSERVATION_OUTSIDE_READY}


def test_the_validator_refuses_evidence_of_an_unconfirmed_opportunity() -> None:
    rows, st, ids = waiting_and_ready()
    intent = build(rows, st, confirmations={OPP_A: CONF_A}, identities=ids)
    # Smuggle OPP_B's own message back into the manifest …
    tampered = json.loads(json.dumps(intent))
    tampered["evidence_manifest"]["records"].append(
        {"external_id": "g2", "source_uri": "gmail://msg/g2", "payload": {}, "acquired_at": "x",
         "observations": [{"kind": "document_reference", "value": f"sha256:{sha('b')}", "value_payload": {}}]})
    codes = [(v["code"], v.get("planned_opportunity_id") or v.get("dedupe_key")) for v in
             qp.evidence_scope_violations(tampered)]
    assert (qp.V_EVIDENCE_OUTSIDE_READY, "gmail_message:g2") in codes
    assert (qp.V_OBSERVATION_OUTSIDE_READY, OPP_B) in codes
    # … or mark it ready without a confirmation.
    forged = json.loads(json.dumps(intent))
    opp(forged, OPP_B)["status"] = qp.S_READY
    assert {"code": qp.V_READY_WITHOUT_CONFIRMATION, "planned_opportunity_id": OPP_B} in \
        qp.evidence_scope_violations(forged)


def test_organization_suggestions_are_exact_never_similar() -> None:
    state = qp.TargetState(organizations=[("o1", "Instituto Uno", None, "machine_proposed", 1),
                                          ("o2", "Instituto Uno S.A.", None, "confirmed", 1)])
    assert [s["organization_id"] for s in qp.suggest_organizations("  instituto   UNO ", state)] == ["o1"]
    assert qp.suggest_organizations(None, state) == []


def test_own_mailboxes_are_never_contact_suggestions() -> None:
    assert qp.recipient_addresses("A <a@cliente.test>, ventas@origenlab.cl; x@labdelivery.cl") == ["a@cliente.test"]


# ------------------------------------------------------------------ the CLI's guards


def load_script():
    sys.path.insert(0, str(_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("quote_crm_import_script", _SCRIPTS / "quote_crm_import.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(_SCRIPTS))


@pytest.mark.parametrize("name", ["origenlab_clean", "origenlab_dev", "postgres", "origenlab_test_xyz"])
def test_apply_refuses_anything_but_a_disposable_database(name) -> None:
    mod = load_script()
    with pytest.raises(SystemExit) as err:
        mod.require_disposable(f"postgresql://u:p@127.0.0.1:54332/{name}", f"postgresql://u:p@127.0.0.1:54332/{name}")
    assert err.value.code == 5


def test_apply_refuses_two_different_databases() -> None:
    mod = load_script()
    with pytest.raises(SystemExit):
        mod.require_disposable("postgresql://u:p@127.0.0.1:1/origenlab_test_0000000a",
                               "postgresql://u:p@127.0.0.1:1/origenlab_test_0000000b")


def test_a_ledger_change_during_the_apply_aborts_it(tmp_path, monkeypatch) -> None:
    mod = load_script()
    ledger = tmp_path / "document_decisions.jsonl"
    ledger.write_text('{"x": 1}\n')
    expected = mod.sha256_file(ledger)
    ran: list[str] = []

    class FakeExecutor:
        def __init__(self, *a, **k) -> None: ...

        def run(self, o, log) -> None:
            ran.append(o["planned_opportunity_id"])
            ledger.write_text('{"x": 1}\n{"y": 2}\n')  # a concurrent writer appends a row

    monkeypatch.setattr(mod, "Executor", FakeExecutor)
    monkeypatch.setattr(mod, "register_operator", lambda *a: {"operator_id": "op", "registered": False})
    monkeypatch.setattr(mod, "domain_event_total", lambda dsn: 0)
    args = type("A", (), {"ledger": ledger, "expect_ledger_hash": expected, "admin_dsn": "x", "target_dsn": "x",
                          "operator_email": "op@example.invalid", "out": tmp_path})()
    intent = {"evidence_scope": qp.EVIDENCE_SCOPE_READY, "evidence_manifest": {"records": []}, "opportunities": [
        {"planned_opportunity_id": OPP_A, "status": qp.S_READY, "confirmation": CONF_A},
        {"planned_opportunity_id": OPP_B, "status": qp.S_READY, "confirmation": CONF_A}]}
    run: dict = {}
    assert mod.apply(args, intent, tmp_path / "m.json", ledger.read_bytes(), run) == 3
    assert ran == [OPP_A]
    assert run["aborted"] == "document ledger changed during the run"


def test_unconfirmed_opportunities_cannot_cause_evidence_writes(tmp_path, monkeypatch) -> None:
    """An intent carrying evidence of an unconfirmed opportunity is refused before any write."""
    mod = load_script()
    ledger = tmp_path / "document_decisions.jsonl"
    ledger.write_text('{"x": 1}\n')
    writes: list[str] = []
    monkeypatch.setattr(mod, "register_operator", lambda *a: writes.append("operator"))
    monkeypatch.setattr(mod, "stage_evidence", lambda *a, **k: writes.append("evidence"))
    monkeypatch.setattr(mod, "Executor", lambda *a, **k: writes.append("commands"))
    rows, st, ids = waiting_and_ready()
    intent = build(rows, st, confirmations={OPP_A: CONF_A}, identities=ids)
    intent["evidence_manifest"]["records"].append(
        {"external_id": "g2", "source_uri": "gmail://msg/g2", "payload": {}, "acquired_at": "x",
         "observations": [{"kind": "document_reference", "value": f"sha256:{sha('b')}", "value_payload": {}}]})
    args = type("A", (), {"ledger": ledger, "expect_ledger_hash": mod.sha256_file(ledger), "admin_dsn": "x",
                          "target_dsn": "x", "operator_email": "op@example.invalid", "out": tmp_path})()
    run: dict = {}
    assert mod.apply(args, intent, tmp_path / "m.json", ledger.read_bytes(), run) == 8
    assert writes == []
    assert run["evidence_scope_violations_detail"]
    # A plan built with the request-basis scope is refused the same way, even with nothing smuggled.
    legacy = qp.build_plan(rows, ids, {s["payload"]["source_email_id"]: s for s in st}, {}, {}, {OPP_A: CONF_A},
                           None, acquired_at="x", note="prueba", evidence_scope=qp.EVIDENCE_SCOPE_UNHELD)
    assert mod.apply(args, legacy, tmp_path / "m.json", ledger.read_bytes(), {}) == 8
    assert writes == []


def test_the_cli_refuses_an_existing_output_directory_and_a_wrong_ledger_hash(tmp_path) -> None:
    mod = load_script()
    assert mod.main(["--expect-ledger-hash", "0" * 64, "--out", str(tmp_path)]) == 2
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("")
    assert mod.main(["--expect-ledger-hash", "0" * 64, "--out", str(tmp_path / "new"), "--ledger", str(ledger),
                     "--email-ledger", str(ledger), "--identity", str(ledger), "--staging", str(ledger),
                     "--blocking", str(ledger)]) == 3
    assert not (tmp_path / "new").exists()
    assert json.dumps  # keep import honest


# ------------------------------------------------------------------ the events an apply reports


def _log_entry(receipt: str, event_ids: list[str], *, replayed: bool = False) -> dict:
    return {"planned_opportunity_id": OPP_A, "step": "s", "command": "c", "key": "k", "replayed": replayed,
            "created": [], "event_ids": event_ids, "command_receipt_id": receipt}


def _event(event_id: str, receipt: str, event_type: str, command: str) -> dict:
    return {"event_id": event_id, "event_type": event_type, "command_receipt_id": receipt, "command": command}


def test_the_reported_events_are_the_events_the_receipts_own_not_the_response_lists() -> None:
    mod = load_script()
    # create_organization lists organization.created but not the assertion.promoted it also appends.
    log = [_log_entry("r1", ["e1"]), _log_entry("r2", ["e3"]), _log_entry("r0", ["old"], replayed=True)]
    events = [_event("e1", "r1", "organization.created", "create_organization"),
              _event("e2", "r1", "assertion.promoted", "create_organization"),
              _event("e3", "r2", "quote.created", "record_historical_quotation")]
    report = mod.event_report(log, events, delta=3)
    assert report["events_written"] == 3 == report["domain_event_delta"]
    assert report["events_match_delta"] is True
    assert report["events_listed_in_responses"] == 2
    assert report["events_not_listed_in_responses"] == 1
    assert report["events_by_command"]["create_organization"] == {"assertion.promoted": 1, "organization.created": 1}
    assert report["new_receipts_without_events"] == []
    assert mod.event_report(log, events, delta=4)["events_match_delta"] is False


def test_an_apply_whose_events_do_not_account_for_the_table_growth_fails(tmp_path, monkeypatch) -> None:
    mod = load_script()
    ledger = tmp_path / "document_decisions.jsonl"
    ledger.write_text('{"x": 1}\n')
    totals = iter([10, 13])  # before and after the command loop: 3 new rows

    class FakeExecutor:
        def __init__(self, *a, **k) -> None: ...

        def run(self, o, log) -> None:
            log.append(_log_entry("r1", ["e1"]))

    monkeypatch.setattr(mod, "Executor", FakeExecutor)
    monkeypatch.setattr(mod, "register_operator", lambda *a: {"operator_id": "op", "registered": False})
    monkeypatch.setattr(mod, "domain_event_total", lambda dsn: next(totals))
    owned = [_event("e1", "r1", "organization.created", "create_organization"),
             _event("e2", "r1", "assertion.promoted", "create_organization")]
    monkeypatch.setattr(mod, "receipt_events", lambda dsn, ids: [e for e in owned if e["command_receipt_id"] in ids])
    args = type("A", (), {"ledger": ledger, "expect_ledger_hash": mod.sha256_file(ledger), "admin_dsn": "x",
                          "target_dsn": "x", "operator_email": "op@example.invalid", "out": tmp_path})()
    intent = {"evidence_scope": qp.EVIDENCE_SCOPE_READY, "evidence_manifest": {"records": []}, "opportunities": [
        {"planned_opportunity_id": OPP_A, "status": qp.S_READY, "confirmation": CONF_A}]}
    run: dict = {}
    # Two events owned, three rows appeared: somebody wrote an event no receipt of this run owns.
    assert mod.apply(args, intent, tmp_path / "m.json", ledger.read_bytes(), run) == 9
    assert run["apply"]["events_written"] == 2
    assert run["apply"]["domain_event_delta"] == 3


# ------------------------------------------------------------------ database-backed (opt-in)

from v2_command_harness import (  # noqa: E402
    build_disposable_database,
    needs_db as _needs_db,
    runtime_dsn as _runtime_dsn,
)


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@_needs_db
def test_create_organization_events_are_counted_and_tied_to_their_receipt(disposable_database) -> None:
    """The `assertion.promoted` a create_organization appends is counted, and its receipt owns it."""
    import uuid

    import psycopg

    from origenlab_api.v2.command_repository import V2CommandRepository
    from origenlab_api.v2.commands import CREATE_ORGANIZATION
    from origenlab_api.v2.identity import OperatorIdentity

    mod = load_script()
    tag = uuid.uuid4().hex[:12]
    name = f"Instituto Importación {tag}"
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, 'Pytest', 'admin', 'active') returning id::text",
                    (f"pytest-{tag}@example.invalid",))
        operator_id = cur.fetchone()[0]
        cur.execute("insert into evidence.source_record (kind, dedupe_key, payload, source_uri) "
                    "values ('gmail_message', %s, '{}'::jsonb, %s) returning id::text",
                    (f"pytest-import:{tag}", f"gmail://msg/{tag}"))
        record_id = cur.fetchone()[0]
        cur.execute("insert into evidence.assertion (source_record_id, kind, value_norm, value, resolution) "
                    "values (%s, 'organization_name', %s, %s::jsonb, 'unresolved') returning id::text",
                    (record_id, name.lower(), json.dumps({"observed_value": name})))
        assertion_id = cur.fetchone()[0]

    target = _runtime_dsn(disposable_database)
    operator = OperatorIdentity(operator_id=operator_id, email_norm=f"pytest-{tag}@example.invalid",
                                display_name="Pytest", role="admin", status="active")
    before = mod.domain_event_total(target)
    response = V2CommandRepository(psycopg.connect, target).execute(
        command_name=CREATE_ORGANIZATION, operator=operator, idempotency_key=f"org:{tag}", digest="a" * 64,
        fields={"source_record_id": record_id, "assertion_id": assertion_id, "kind": "unknown",
                "name_display": None, "note": "pytest"})
    receipt = response["command_receipt_id"]
    log = [{"replayed": False, "event_ids": response["event_ids"], "command_receipt_id": receipt}]
    events = mod.receipt_events(target, [receipt])
    report = mod.event_report(log, events, mod.domain_event_total(target) - before)

    assert report["events_match_delta"] is True
    assert report["events_written"] == report["domain_event_delta"]
    promoted = [e for e in events if e["event_type"] == "assertion.promoted"]
    assert len(promoted) == 1
    assert promoted[0]["command_receipt_id"] == receipt
    assert promoted[0]["command"] == CREATE_ORGANIZATION
    # The response list is exactly what the old counter summed, and it misses that event.
    assert promoted[0]["event_id"] not in response["event_ids"]
    assert report["events_written"] > report["events_listed_in_responses"]
    with psycopg.connect(disposable_database) as conn:
        assert conn.execute("select count(*) from crm.domain_event where event_type = 'assertion.promoted' "
                            "and aggregate_id = %s and command_receipt_id = %s",
                            (assertion_id, receipt)).fetchone()[0] == 1
