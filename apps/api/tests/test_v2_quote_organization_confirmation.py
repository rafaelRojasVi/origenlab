"""Organization confirmation requests and the operator-decision loader. Fictitious data throughout."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from origenlab_api.v2 import quote_crm_import_plan as qp
from origenlab_api.v2 import quote_organization_confirmation as oc

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
NOW = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)
OP_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OP_EMAIL = "operadora@origenlab.cl"
ORG_EXACT = "0e000000-0000-4000-8000-000000000001"
ORG_LOOSE = "0e000000-0000-4000-8000-000000000002"
ORG_TWIN_1 = "0e000000-0000-4000-8000-000000000003"
ORG_TWIN_2 = "0e000000-0000-4000-8000-000000000004"


def pid(n: int) -> str:
    return f"{n:08d}-0000-5000-8000-000000000000"


def sha(ch: str) -> str:
    return ch * 64


# opportunity → (document char, number, printed organization, rut)
OPPS = {
    pid(1): ("a", "01001-26", "Instituto Uno", None),                 # one exact CRM match
    pid(2): ("b", "01002-26", "Laboratorio Nuevo", "76.111.111-1"),   # nothing in the CRM
    pid(3): ("c", "01003-26", None, "10.222.222-2"),                  # person only
    pid(4): ("d", "01004-26", "Golden Tres S.A.", None),              # CRM has "Golden Tres SA"
    pid(5): ("e", "01005-26", "Centro Compartido", None),             # shared with 6
    pid(6): ("f", "01006-26", "Centro Compartido Ltda.", None),
    pid(7): ("g", "01007-26", "Gemelos", None),                        # two exact CRM matches
    pid(8): ("h", "01008-26", "Retenida", None),                       # held by a blocked document
}


def fixture():
    rows, staged, identities = [], [], {}
    for i, (p, (ch, number, org, rut)) in enumerate(sorted(OPPS.items()), start=1):
        rows.append({"decision": qp.CONFIRM, "decision_id": f"d-{ch}", "document_sha256": sha(ch), "operator": "Rafael",
                     "quote_number": number, "printed_quote_numbers": [number],
                     "opportunity": {"mode": "planned", "planned_opportunity_id": p},
                     "occurrences": [{"email_id": i, "sent_at": "2026-03-01T10:00:00-03:00", "filename": f"{number}.pdf",
                                      "gmail_message_id": f"g{i}", "gmail_thread_id": f"t{i}",
                                      "rfc822_message_id": f"<m{i}@origenlab.cl>"}]})
        staged.append({"kind": "gmail_message", "dedupe_key": f"gmail_message:g{i}", "source_uri": f"gmail://msg/g{i}",
                       "payload": {"source_email_id": i, "gmail_message_id": f"g{i}", "gmail_thread_id": f"t{i}",
                                   "gmail_label_ids": ["SENT"], "recipients": f"compras{i}@cliente.test",
                                   "sender": "ventas@origenlab.cl", "documents": [{"sha256": sha(ch)}]}})
        identities[sha(ch)] = {"clients": [{"organization": org, "addressee": {"value": f"Persona {i}"},
                                            "rut": {"value": rut} if rut else None}]}
    staged_by_email = {s["payload"]["source_email_id"]: s for s in staged}
    intent = qp.build_plan(rows, identities, staged_by_email, {sha("z"): ["G1"]}, {sha("z"): pid(8)}, {}, None,
                           acquired_at="2026-09-24T16:44:00+00:00", note="prueba")
    crm = oc.CrmReading(
        database="origenlab_clean",
        organizations=[oc.CrmOrganization(ORG_EXACT, "Instituto Uno", None, "machine_proposed", 3),
                       oc.CrmOrganization(ORG_LOOSE, "Golden Tres SA", None, "machine_proposed", 1),
                       oc.CrmOrganization(ORG_TWIN_1, "Gemelos", None, "machine_proposed", 1),
                       oc.CrmOrganization(ORG_TWIN_2, "gemelos", None, "machine_proposed", 1)],
        operators={OP_ID: {"email": OP_EMAIL, "role": "admin", "status": "active", "display_name": "Operadora"},
                   "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": {"email": "lectora@origenlab.cl", "role": "viewer",
                                                            "status": "active", "display_name": "Lectora"}},
    )
    doc = oc.build_requests(intent, qp.plan_sha256(intent), rows, identities, staged_by_email, crm,
                            {sha("a"): "/tmp/a.pdf"})
    return intent, doc, crm


def req(doc, n):
    return next(r for r in doc["requests"] if r["planned_opportunity_id"] == pid(n))


def sheet(doc, **decisions):
    """The issued CSV rows; `decisions` maps opportunity number → decision columns."""
    rows = oc.request_csv_rows(doc)
    for n, values in decisions.items():
        row = next(r for r in rows if r["planned_opportunity_id"] == pid(int(n[1:])))
        row.update(values)
    return rows


def signed(**v):
    return {"decided_by_email": OP_EMAIL, "decided_by_operator_id": OP_ID, "decided_by_role": "admin",
            "decided_at": "2026-09-26T10:00:00-03:00", "decision_note": "revisado contra el PDF", **v}


def load(doc, crm, rows):
    return oc.load_decisions(rows, doc, "f" * 64, crm, now=NOW)


def codes(result):
    return {p["code"] for p in result.problems}


# ─── requests ──────────────────────────────────────────────────────────────────────────────


def test_each_opportunity_lands_in_exactly_one_category() -> None:
    _, doc, _ = fixture()
    got = {r["planned_opportunity_id"]: r["category"] for r in doc["requests"]}
    assert got == {pid(1): oc.C_EXACT, pid(2): oc.C_CREATE, pid(3): oc.C_PERSON, pid(4): oc.C_DUPLICATE,
                   pid(5): oc.C_DUPLICATE, pid(6): oc.C_DUPLICATE, pid(7): oc.C_DUPLICATE, pid(8): oc.C_BLOCKED}
    assert doc["summary"]["total_opportunities"] == 8


def test_the_request_carries_the_evidence_and_never_a_decision() -> None:
    _, doc, _ = fixture()
    r = req(doc, 1)
    assert r["exact_crm_matches"].startswith(ORG_EXACT) and "v3" in r["exact_crm_matches"]
    assert r["recommended_action"] == f"confirm_existing:{ORG_EXACT}:v3"
    assert r["recipient_emails"] == "compras1@cliente.test" and r["quote_numbers"] == "01001-26"
    assert "rfc822msgid%3Am1%40origenlab.cl" in r["evidence_links"] and "pdf:/tmp/a.pdf" in r["evidence_links"]
    assert req(doc, 2)["printed_ruts"] == "76111111-1"
    assert all(v == "" for row in oc.request_csv_rows(doc) for k, v in row.items() if k in oc.DECISION_COLUMNS)


def test_a_candidate_key_is_shown_but_is_never_an_exact_match() -> None:
    _, doc, _ = fixture()
    r = req(doc, 4)
    assert r["exact_crm_matches"] == "" and r["other_crm_candidates"].startswith(ORG_LOOSE)
    assert oc.candidate_key("Razón social: Golden Tres S.A.") == oc.candidate_key("GOLDEN TRES SA") == "golden tres"
    assert req(doc, 5)["shared_with_opportunities"] == pid(6)


def test_person_only_and_blocked_recommend_no_creation() -> None:
    _, doc, _ = fixture()
    assert req(doc, 3)["recommended_action"] == oc.LEAVE_PENDING
    assert req(doc, 8)["recommended_action"] == "none_until_documents_resolved"


def test_requests_are_deterministic() -> None:
    _, a, _ = fixture()
    _, b, _ = fixture()
    assert [r["request_sha256"] for r in a["requests"]] == [r["request_sha256"] for r in b["requests"]]


# ─── loader ────────────────────────────────────────────────────────────────────────────────


def test_a_blank_sheet_is_accepted_and_confirms_nothing() -> None:
    _, doc, crm = fixture()
    result = load(doc, crm, sheet(doc))
    assert result.accepted and result.confirmations == [] and len(result.blank) == 8


def test_explicit_confirmations_record_operator_timestamp_and_evidence() -> None:
    _, doc, crm = fixture()
    rows = sheet(doc, o1=signed(decision="confirm_existing", organization_id=ORG_EXACT, organization_version="3"),
                 o2=signed(decision="create_from_evidence", organization_kind="institution"),
                 o3=signed(decision="leave_pending"))
    result = load(doc, crm, rows)
    assert result.accepted, result.problems
    one, two = result.confirmations
    assert one["action"] == "confirm_existing" and one["organization_version"] == 3
    assert one["confirmed_by"] == {"email": OP_EMAIL, "operator_id": OP_ID, "role": "admin"}
    assert one["confirmed_at"] == "2026-09-26T10:00:00-03:00"
    assert one["evidence"]["request_sha256"] == req(doc, 1)["request_sha256"]
    assert one["evidence"]["plan_sha256"] == doc["plan_sha256"] and one["evidence"]["documents"] == [sha("a")]
    assert two["kind"] == "institution" and "organization_id" not in two
    assert [p["planned_opportunity_id"] for p in result.pending] == [pid(3)]


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda rows: rows.append({**rows[0], "planned_opportunity_id": pid(99)}), "unknown_opportunity"),
    (lambda rows: rows.append(dict(rows[0])), "duplicate_opportunity_row"),
    (lambda rows: rows[0].update(printed_organization="Otra"), "request_row_edited"),
])
def test_unknown_duplicate_and_edited_rows_refuse_the_sheet(mutate, code) -> None:
    _, doc, crm = fixture()
    rows = sheet(doc, o1=signed(decision="confirm_existing", organization_id=ORG_EXACT, organization_version="3"))
    mutate(rows)
    result = load(doc, crm, rows)
    assert code in codes(result) and result.confirmations == [] and not result.accepted


@pytest.mark.parametrize(("n", "values", "code"), [
    (1, signed(decision="confirm_existing", organization_id=ORG_EXACT, organization_version="2"), "organization_version_stale"),
    (1, signed(decision="confirm_existing", organization_id="0e000000-0000-4000-8000-0000000000ff",
               organization_version="1"), "organization_not_found"),
    (1, signed(decision="confirm_existing"), "organization_reference_invalid"),
    (1, signed(decision="create_from_evidence", organization_kind="institution"), "organization_already_exists"),
    (3, signed(decision="create_from_evidence", organization_kind="institution"), "no_printed_organization"),
    (2, signed(decision="create_from_evidence"), "organization_kind_required"),
    (2, signed(decision="create_from_evidence", organization_kind="university"), "organization_kind_required"),
    (2, signed(decision="create_from_evidence", organization_kind="institution", organization_id=ORG_EXACT),
     "fields_do_not_belong_to_decision"),
    (8, signed(decision="confirm_existing", organization_id=ORG_EXACT, organization_version="3"),
     "opportunity_blocked_by_documents"),
    (1, signed(decision="yes"), "decision_not_allowed"),
    (1, signed(decision="leave_pending", decision_note=""), "decision_note_required"),
])
def test_decisions_the_evidence_cannot_carry_are_refused(n, values, code) -> None:
    _, doc, crm = fixture()
    result = load(doc, crm, sheet(doc, **{f"o{n}": values}))
    assert code in codes(result) and not result.accepted


def test_two_rows_creating_one_institution_conflict() -> None:
    _, doc, crm = fixture()
    rows = sheet(doc, o5=signed(decision="create_from_evidence", organization_kind="institution"),
                 o6=signed(decision="create_from_evidence", organization_kind="institution"))
    result = load(doc, crm, rows)
    assert codes(result) == {"conflicting_creation"} and result.confirmations == []
    ok = load(doc, crm, sheet(doc, o5=signed(decision="create_from_evidence", organization_kind="institution"),
                              o6=signed(decision="leave_pending")))
    assert ok.accepted


@pytest.mark.parametrize(("values", "code"), [
    ({"decided_by_email": "operador.local@example.invalid"}, "operator_email_placeholder"),
    ({"decided_by_email": "Operadora@origenlab.cl"}, "operator_email_invalid"),
    ({"decided_by_email": "otra@origenlab.cl"}, "operator_email_mismatch"),
    ({"decided_by_operator_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}, "operator_not_registered"),
    ({"decided_by_operator_id": "SIMULATION"}, "operator_id_invalid"),
    ({"decided_by_role": "viewer"}, "operator_role_not_writer"),
    ({"decided_by_role": "sales"}, "operator_role_mismatch"),
    ({"decided_at": "2026-09-26 10:00"}, "decided_at_invalid"),
    ({"decided_at": "2026-09-27T10:00:00+00:00"}, "decided_at_in_future"),
])
def test_the_operator_identity_must_be_real_registered_and_a_writer(values, code) -> None:
    _, doc, crm = fixture()
    result = load(doc, crm, sheet(doc, o3=signed(decision="leave_pending", **values)))
    assert code in codes(result) and not result.accepted


def test_a_viewer_or_disabled_operator_cannot_confirm() -> None:
    _, doc, crm = fixture()
    crm.operators[OP_ID]["status"] = "disabled"
    assert "operator_not_active" in codes(load(doc, crm, sheet(doc, o3=signed(decision="leave_pending"))))


def test_one_sheet_one_operator() -> None:
    _, doc, crm = fixture()
    crm.operators["dddddddd-dddd-4ddd-8ddd-dddddddddddd"] = {"email": "otro@origenlab.cl", "role": "sales",
                                                             "status": "active", "display_name": "Otro"}
    rows = sheet(doc, o3=signed(decision="leave_pending"),
                 o2=signed(decision="leave_pending", decided_by_email="otro@origenlab.cl",
                           decided_by_operator_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd", decided_by_role="sales"))
    assert "several_operators" in codes(load(doc, crm, rows))


# ─── the planner accepts only loader output ────────────────────────────────────────────────


def load_import_script():
    sys.path.insert(0, str(_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("quote_crm_import_script_oc", _SCRIPTS / "quote_crm_import.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(_SCRIPTS))


def test_the_planner_refuses_a_hand_written_confirmation(tmp_path) -> None:
    mod = load_import_script()
    f = tmp_path / "c.jsonl"
    f.write_text(json.dumps({"planned_opportunity_id": pid(1), "action": "create_from_evidence",
                             "confirmed_by": "Rafael", "confirmed_at": "2026-09-26T10:00:00Z", "note": "x"}) + "\n")
    with pytest.raises(SystemExit, match="not produced by"):
        mod.read_confirmations(f, None)


def test_the_planner_reads_loader_output_and_refuses_two_for_one_opportunity(tmp_path) -> None:
    mod = load_import_script()
    _, doc, crm = fixture()
    result = load(doc, crm, sheet(doc, o2=signed(decision="create_from_evidence", organization_kind="institution")))
    f = tmp_path / "c.jsonl"
    f.write_text("".join(json.dumps(c) + "\n" for c in result.confirmations))
    assert set(mod.read_confirmations(f, None)) == {pid(2)}
    f.write_text("".join(json.dumps(c) + "\n" for c in result.confirmations * 2))
    with pytest.raises(SystemExit, match="second confirmation"):
        mod.read_confirmations(f, None)


def test_a_simulation_is_still_refused_outside_a_disposable_database(tmp_path) -> None:
    mod = load_import_script()
    f = tmp_path / "c.jsonl"
    f.write_text(json.dumps({"planned_opportunity_id": pid(1), "action": "create_from_evidence", "simulation": True,
                             "confirmed_by": "SIMULATION", "confirmed_at": "x", "note": "x"}) + "\n")
    with pytest.raises(SystemExit, match="disposable"):
        mod.read_confirmations(f, "postgresql://u:p@127.0.0.1:54332/origenlab_clean")
