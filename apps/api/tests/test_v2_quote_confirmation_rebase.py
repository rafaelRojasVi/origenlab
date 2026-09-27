"""Guarded rebase of operator organization confirmations onto a new plan. Fictitious data throughout."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from origenlab_api.v2 import quote_confirmation_rebase as rb
from origenlab_api.v2 import quote_crm_import_plan as qp
from origenlab_api.v2 import quote_organization_confirmation as oc

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
NOW = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)
OP_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OP_EMAIL = "operadora@origenlab.cl"
ORG_UNO = "0e000000-0000-4000-8000-000000000001"
ORG_OTRA = "0e000000-0000-4000-8000-000000000002"
FINGERPRINTS = {"review_policy": "1" * 64, "resolution_policy": "2" * 64, "gates": "3" * 64}
OLD_REQUESTS_SHA = "e" * 64
NEW_REQUESTS_SHA = "f" * 64


def pid(n: int) -> str:
    return f"{n:08d}-0000-5000-8000-000000000000"


def sha(ch: str) -> str:
    return ch * 64


# opportunity → (document char, number, printed organization)
OPPS = {
    pid(1): ("a", "01001-26", "Instituto Uno"),       # confirm_existing ORG_UNO (exact match)
    pid(2): ("b", "01002-26", "Laboratorio Nuevo"),   # create_from_evidence, name_display "Lab Nuevo SpA"
    pid(3): ("c", "01003-26", None),                  # person only, confirm_existing ORG_OTRA
}


def ledger_row(i: int, p: str, ch: str, number: str) -> dict:
    return {"decision": qp.CONFIRM, "decision_id": f"d-{ch}", "document_sha256": sha(ch), "operator": "Rafael",
            "quote_number": number, "printed_quote_numbers": [number], "reason": "revisado",
            "opportunity": {"mode": "planned", "planned_opportunity_id": p},
            "occurrences": [{"email_id": i, "sent_at": "2026-03-01T10:00:00-03:00", "filename": f"{number}.pdf",
                             "gmail_message_id": f"g{i}", "gmail_thread_id": f"t{i}",
                             "rfc822_message_id": f"<m{i}@origenlab.cl>"}]}


def staged(i: int, ch: str) -> dict:
    return {"kind": "gmail_message", "dedupe_key": f"gmail_message:g{i}", "source_uri": f"gmail://msg/g{i}",
            "payload": {"source_email_id": i, "gmail_message_id": f"g{i}", "gmail_thread_id": f"t{i}",
                        "gmail_label_ids": ["SENT"], "recipients": f"compras{i}@cliente.test",
                        "sender": "ventas@origenlab.cl", "documents": [{"sha256": sha(ch)}]}}


def identity(org: str | None, i: int) -> dict:
    return {"clients": [{"organization": org, "addressee": {"value": f"Persona {i}"}, "rut": None}]}


def inputs(extra: dict | None = None) -> tuple[list, dict, dict]:
    """Ledger rows, identities and staging; `extra` adds opportunities {pid: (ch, number, org)}."""
    rows, identities, stage = [], {}, {}
    for i, (p, (ch, number, org)) in enumerate(sorted({**OPPS, **(extra or {})}.items()), start=1):
        rows.append(ledger_row(i, p, ch, number))
        identities[sha(ch)] = identity(org, i)
        stage[i] = staged(i, ch)
    return rows, identities, stage


def crm(**versions: int) -> oc.CrmReading:
    return oc.CrmReading(
        database="origenlab_clean",
        organizations=[oc.CrmOrganization(ORG_UNO, "Instituto Uno", None, "machine_proposed", versions.get("uno", 1)),
                       oc.CrmOrganization(ORG_OTRA, "Otra Institución", None, "machine_proposed", versions.get("otra", 1)),
                       *[oc.CrmOrganization(f"0e000000-0000-4000-8000-00000000009{n}", name, None, "operator_confirmed", 1)
                         for n, name in enumerate(versions.get("extra_names", []))]],  # type: ignore[arg-type]
        operators={OP_ID: {"email": OP_EMAIL, "role": "admin", "status": "active", "display_name": "Operadora"}})


def basis(rows, identities, stage, reading, requests_sha, fingerprints=FINGERPRINTS, mutate=None) -> rb.Basis:
    intent = qp.build_plan(rows, identities, stage, {}, {}, {}, None, acquired_at="2026-09-24T16:44:00+00:00",
                           note="prueba", evidence_scope=qp.EVIDENCE_SCOPE_UNHELD)
    if mutate:
        mutate(intent)
    plan = qp.plan_sha256(intent)
    doc = oc.build_requests(intent, plan, rows, identities, stage, reading, {})
    return rb.Basis(plan_sha256=plan, intent=intent, requests_doc=doc, requests_sha256=requests_sha,
                    ledger_rows=rows, fingerprints=dict(fingerprints))


def signed(**v) -> dict:
    return {"decided_by_email": OP_EMAIL, "decided_by_operator_id": OP_ID, "decided_by_role": "admin",
            "decided_at": "2026-09-25T20:42:18+00:00", "decision_note": "revisado contra el PDF", **v}


def owner_confirmations(old: rb.Basis, reading: oc.CrmReading) -> list[dict]:
    rows = oc.request_csv_rows(old.requests_doc)
    decisions = {
        pid(1): signed(decision="confirm_existing", organization_id=ORG_UNO, organization_version="1"),
        pid(2): signed(decision="create_from_evidence", organization_kind="institution", name_display="Lab Nuevo SpA"),
        pid(3): signed(decision="confirm_existing", organization_id=ORG_OTRA, organization_version="1"),
    }
    for r in rows:
        r.update(decisions[r["planned_opportunity_id"]])
    result = oc.load_decisions(rows, old.requests_doc, old.requests_sha256, reading, now=NOW)
    assert result.accepted, result.problems
    return result.confirmations


def run(confs, old, new, reading, fingerprints=FINGERPRINTS) -> rb.RebaseResult:
    return rb.rebase_confirmations(confs, old, new, reading, approved_fingerprints=fingerprints,
                                   shadow_plan_sha256="a" * 64, rebased_at="2026-09-26T12:00:00+00:00")


def setup(extra=None, new_reading=None, mutate_rows=None, mutate_intent=None, new_fingerprints=FINGERPRINTS):
    reading = crm()
    old = basis(*inputs(), reading, OLD_REQUESTS_SHA)
    confs = owner_confirmations(old, reading)
    rows, identities, stage = inputs(extra)
    if mutate_rows:
        mutate_rows(rows)
    new = basis(rows, identities, stage, new_reading or reading, NEW_REQUESTS_SHA, new_fingerprints, mutate_intent)
    return confs, old, new, new_reading or reading


TAIL = {pid(9): ("t", "01246-26", "Institución Nueva de la Cola")}


def codes(result) -> dict[str, str]:
    return {r["planned_opportunity_id"]: r["code"] for r in result.refused}


# ─── the happy path ────────────────────────────────────────────────────────────────────────


def test_an_unrelated_new_document_moves_every_plan_reference() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    assert old.plan_sha256 != new.plan_sha256
    result = run(confs, old, new, reading)
    assert result.complete and len(result.rebased) == 3
    for r in result.rebased:
        assert r["evidence"]["plan_sha256"] == new.plan_sha256
        assert r["rebase"]["rebased_from_plan_hash"] == old.plan_sha256
        assert r["rebase"]["rebased_to_plan_hash"] == new.plan_sha256
        assert r["rebase"]["shadow_plan_sha256"] == "a" * 64


def test_the_rebase_changes_only_the_plan_reference_and_adds_the_rebase_record() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    before = {c["planned_opportunity_id"]: c for c in confs}
    for r in run(confs, old, new, reading).rebased:
        original = before[r["planned_opportunity_id"]]
        stripped = copy.deepcopy(r)
        del stripped["rebase"]
        stripped["evidence"]["plan_sha256"] = original["evidence"]["plan_sha256"]
        assert stripped == original  # operator, timestamp, note, organization, request sheet: untouched
        assert r["confirmed_by"] == {"email": OP_EMAIL, "operator_id": OP_ID, "role": "admin"}
        assert r["evidence"]["requests_sha256"] == OLD_REQUESTS_SHA


def test_the_input_confirmations_are_not_mutated() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    snapshot = json.dumps(confs, sort_keys=True)
    run(confs, old, new, reading)
    assert json.dumps(confs, sort_keys=True) == snapshot


def test_the_rebase_is_deterministic() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    assert run(confs, old, new, reading).rebased == run(confs, old, new, reading).rebased


def test_the_rebased_plan_keeps_every_step_and_key() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    rows, identities, stage = inputs(TAIL)
    rebased = {c["planned_opportunity_id"]: c for c in run(confs, old, new, reading).rebased}
    original = {c["planned_opportunity_id"]: c for c in confs}
    kw = {"acquired_at": "2026-09-24T16:44:00+00:00", "note": "prueba"}
    before = qp.build_plan(*inputs()[:3], {}, {}, original, None, **kw)
    after = qp.build_plan(rows, identities, stage, {}, {}, rebased, None, **kw)
    steps = lambda i: {o["planned_opportunity_id"]: o["steps"] for o in i["opportunities"] if o["status"] == qp.S_READY}  # noqa: E731
    assert steps(before) == steps(after) and len(steps(after)) == 3


# ─── refusals ──────────────────────────────────────────────────────────────────────────────


def test_a_new_document_printing_the_same_institution_changes_the_request_and_is_refused() -> None:
    confs, old, new, reading = setup(extra={pid(9): ("t", "01246-26", "Instituto Uno")})
    result = run(confs, old, new, reading)
    assert codes(result) == {pid(1): rb.R_REQUEST_CHANGED}
    assert "shared_with_opportunities" in result.refused[0]["detail"]
    assert {r["planned_opportunity_id"] for r in result.rebased} == {pid(2), pid(3)}


def test_a_confirmed_organization_at_a_new_version_is_refused() -> None:
    confs, old, new, reading = setup(extra=TAIL, new_reading=crm(otra=2))
    assert codes(run(confs, old, new, reading)) == {pid(3): rb.R_ORGANIZATION_VERSION}


def test_an_exact_match_at_a_new_version_changes_the_request() -> None:
    confs, old, new, reading = setup(extra=TAIL, new_reading=crm(uno=2))
    result = run(confs, old, new, reading)
    assert codes(result) == {pid(1): rb.R_REQUEST_CHANGED}
    assert "exact_crm_matches" in result.refused[0]["detail"]


def test_a_confirmed_organization_no_longer_live_is_refused() -> None:
    reading = crm()
    confs, old, new, _ = setup(extra=TAIL)
    gone = oc.CrmReading(database=reading.database, organizations=[o for o in reading.organizations
                                                                   if o.organization_id != ORG_OTRA],
                         operators=reading.operators)
    new = basis(*inputs(TAIL), gone, NEW_REQUESTS_SHA)
    assert codes(run(confs, old, new, gone)) == {pid(3): rb.R_ORGANIZATION_GONE}


def test_an_organization_to_create_that_now_exists_is_refused() -> None:
    confs, old, new, reading = setup(extra=TAIL, new_reading=crm(extra_names=["Lab Nuevo SpA"]))
    assert codes(run(confs, old, new, reading)) == {pid(2): rb.R_ORGANIZATION_NOW_EXISTS}


def test_a_changed_ledger_row_of_a_confirmed_document_is_refused() -> None:
    def edit(rows):
        next(r for r in rows if r["document_sha256"] == sha("a"))["reason"] = "otra cosa"
    confs, old, new, reading = setup(extra=TAIL, mutate_rows=edit)
    assert codes(run(confs, old, new, reading)) == {pid(1): rb.R_LEDGER_ROWS_CHANGED}


def test_a_changed_opportunity_plan_is_refused() -> None:
    def edit(intent):
        next(o for o in intent["opportunities"] if o["planned_opportunity_id"] == pid(2))["warnings"].append(
            {"code": "x"})
    confs, old, new, reading = setup(extra=TAIL, mutate_intent=edit)
    result = run(confs, old, new, reading)
    assert codes(result) == {pid(2): rb.R_OPPORTUNITY_CHANGED}
    assert "warnings" in result.refused[0]["detail"]


def test_an_opportunity_missing_from_the_new_plan_is_refused() -> None:
    def drop(rows):
        rows[:] = [r for r in rows if r["document_sha256"] != sha("c")]
    confs, old, new, reading = setup(extra=TAIL, mutate_rows=drop)
    assert codes(run(confs, old, new, reading)) == {pid(3): rb.R_NEW_REQUEST_MISSING}


@pytest.mark.parametrize("side", ["old", "new"])
def test_an_unapproved_fingerprint_refuses_every_confirmation(side) -> None:
    moved = {**FINGERPRINTS, "gates": "9" * 64}
    reading = crm()
    old = basis(*inputs(), reading, OLD_REQUESTS_SHA, moved if side == "old" else FINGERPRINTS)
    confs = owner_confirmations(old, reading)
    new = basis(*inputs(TAIL), reading, NEW_REQUESTS_SHA, moved if side == "new" else FINGERPRINTS)
    result = run(confs, old, new, reading)
    assert not result.rebased and set(codes(result).values()) == {rb.R_FINGERPRINT}


def test_a_confirmation_answering_another_plan_is_refused() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    confs[0]["evidence"]["plan_sha256"] = "0" * 64
    assert codes(run(confs, old, new, reading)) == {pid(1): rb.R_ANSWERS_OTHER_PLAN}


def test_a_tampered_old_request_row_is_refused() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    next(r for r in old.requests_doc["requests"] if r["planned_opportunity_id"] == pid(1))["printed_organization"] = "X"
    assert codes(run(confs, old, new, reading)) == {pid(1): rb.R_OLD_REQUEST_TAMPERED}


def test_a_confirmation_from_another_request_sheet_is_refused() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    confs[1]["evidence"]["requests_sha256"] = "0" * 64
    assert codes(run(confs, old, new, reading)) == {pid(2): rb.R_OLD_REQUESTS_MISMATCH}


def test_hand_written_simulated_and_already_rebased_lines_are_refused() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    rebased = run(confs, old, new, reading).rebased
    confs[0]["loader_version"] = "a-mano"
    confs[1]["simulation"] = True
    result = run([confs[0], confs[1], rebased[2]], old, new, reading)
    assert codes(result) == {pid(1): rb.R_NOT_LOADER_OUTPUT, pid(2): rb.R_NOT_LOADER_OUTPUT,
                             pid(3): rb.R_ALREADY_REBASED}


def test_a_duplicate_confirmation_is_refused() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    result = run([confs[0], copy.deepcopy(confs[0])], old, new, reading)
    assert len(result.rebased) == 1 and codes(result) == {pid(1): rb.R_DUPLICATE}


# ─── the importer's structural check ───────────────────────────────────────────────────────


def test_rebase_problem_accepts_a_rebased_line_and_ignores_an_original() -> None:
    confs, old, new, reading = setup(extra=TAIL)
    rebased = run(confs, old, new, reading).rebased
    assert rb.rebase_problem(confs[0]) is None
    assert all(rb.rebase_problem(r) is None for r in rebased)


@pytest.mark.parametrize("edit, fragment", [
    (lambda r: r["rebase"].update(rebase_version="otra"), "is not"),
    (lambda r: r["rebase"].update(rebased_from_plan_hash="corto"), "rebased_from_plan_hash"),
    (lambda r: r["evidence"].update(plan_sha256="0" * 64), "differs from evidence.plan_sha256"),
    (lambda r: r["rebase"].update(rebased_from_plan_hash=r["rebase"]["rebased_to_plan_hash"]), "must move"),
    (lambda r: r["rebase"].update(request_sha256="0" * 64), "request_sha256"),
])
def test_rebase_problem_refuses_a_malformed_rebase(edit, fragment) -> None:
    confs, old, new, reading = setup(extra=TAIL)
    r = copy.deepcopy(run(confs, old, new, reading).rebased[0])
    edit(r)
    assert fragment in (rb.rebase_problem(r) or "")


def _importer():
    spec = importlib.util.spec_from_file_location("quote_crm_import_cli_rebase", _SCRIPTS / "quote_crm_import.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_the_importer_reads_rebased_lines_and_refuses_a_malformed_one(tmp_path) -> None:
    cli = _importer()
    confs, old, new, reading = setup(extra=TAIL)
    rebased = run(confs, old, new, reading).rebased
    good = tmp_path / "good.jsonl"
    good.write_text("".join(json.dumps(r) + "\n" for r in rebased), encoding="utf-8")
    assert set(cli.read_confirmations(good, None)) == {pid(1), pid(2), pid(3)}
    bad = copy.deepcopy(rebased[0])
    bad["rebase"]["rebased_to_plan_hash"] = "0" * 64
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="rebased_to_plan_hash"):
        cli.read_confirmations(path, None)
