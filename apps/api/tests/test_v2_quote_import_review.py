"""Quotation import review: the plan beside the database, and the two invariants it must prove."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from origenlab_api.v2.quote_crm_import_plan import plan_sha256
from origenlab_api.v2.quote_import_review import (
    PlanRefused,
    _header_view,
    build_review,
    load_plan,
    revision_document_path,
)

READY = "11111111-1111-5111-8111-111111111111"
WAIT = "22222222-2222-5222-8222-222222222222"
HELD = "33333333-3333-5333-8333-333333333333"
DB_OPP = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SHA_R = "a" * 64
SHA_R2 = "b" * 64
SHA_W = "c" * 64
SHA_H = "d" * 64
MSG_R = "gmail_message:0000000000000a01"
MSG_R2 = "gmail_message:0000000000000a02"
MSG_W = "gmail_message:0000000000000b01"


def _rev(sha, origin):
    return {"document_sha256": sha, "origin_dedupe_key": origin, "sent_at": "2026-05-07T12:00:00-04:00",
            "supersedes_document_sha256": None, "ledger_decision_id": "x"}


def _intent():
    conf = {"action": "confirm_existing", "organization_id": ORG, "confirmed_at": "2026-09-25T20:42:18+00:00",
            "confirmed_by": {"email": "contacto@origenlab.cl", "operator_id": "714dd8d0-9575-4769-8c12-cbc01909bd5e",
                             "role": "admin"}, "evidence": {"category": "exact_crm_match"}}
    return {
        "plan_version": "qimport/2026-09-25.1",
        "evidence_scope": "ready",
        "summary": {},
        "opportunities": [
            {"planned_opportunity_id": READY, "status": "ready", "confirmation": conf,
             "documents": [SHA_R, SHA_R2], "source_dedupe_keys": [MSG_R, MSG_R2],
             "quotes": [{"quote_number": "01001-26", "revisions": [_rev(SHA_R, MSG_R)]},
                        {"quote_number": "01002-26", "revisions": [_rev(SHA_R2, MSG_R2)]}],
             "steps": [{}, {}], "reasons": [], "warnings": []},
            {"planned_opportunity_id": WAIT, "status": "needs_organization_confirmation", "confirmation": None,
             "documents": [SHA_W], "source_dedupe_keys": [MSG_W],
             "quotes": [{"quote_number": "01003-26", "revisions": [_rev(SHA_W, MSG_W)]}],
             "steps": [{}], "reasons": [], "warnings": []},
            {"planned_opportunity_id": HELD, "status": "held", "confirmation": None,
             "documents": [SHA_H], "source_dedupe_keys": [], "quotes": [], "steps": [],
             "reasons": ["x:opportunity_has_blocked_document"], "warnings": []},
        ],
        "evidence_manifest": {"records": [
            {"external_id": "0000000000000a01", "observations": [
                {"kind": "document_reference", "value": f"sha256:{SHA_R}"}],
             "payload": {"documents": [{"sha256": SHA_R, "filename": "CN1001.pdf", "stored_path": "docs/r.pdf"}]}},
            {"external_id": "0000000000000a02", "observations": [
                {"kind": "document_reference", "value": f"sha256:{SHA_R2}"},
                {"kind": "document_reference", "value": f"sha256:{SHA_R}"}],
             "payload": {"documents": [{"sha256": SHA_R2, "filename": "CN1002.pdf", "stored_path": "docs/r2.pdf"}]}},
        ]},
    }


class _Plan:
    def __init__(self, intent):
        self.intent = intent
        self.sha256 = plan_sha256(intent)
        self.run = {"generated_at": "t", "target": "origenlab_clean"}
        self.directory = Path(".")


def _record(key, rid):
    return {"id": rid, "dedupe_key": key, "review_status": "pending", "is_quarantined": False, "created_at": "t",
            "headers": {"subject_raw": "Cotización", "sender": "OrigenLab", "recipients": "cliente",
                        "gmail_message_id": key.split(":")[1], "rfc822_message_id": "<m@x>"}}


def _db():
    return {
        "database": "origenlab_test_00000000",
        "receipts": [
            {"key": f"qimport:v1:{READY}:open", "command": "open_commercial_case", "status": "completed",
             "response": {"opportunity_id": DB_OPP}},
        ],
        "opportunities": [{"id": DB_OPP, "title": "Cotización 01001-26", "stage": "quoting", "version": 5,
                           "organization_id": None, "created_at": "t"}],
        "organization_links": [{"opportunity_id": DB_OPP, "organization_id": ORG, "name": "Universidad",
                                "role": "requesting_institution", "confirmation": "confirmed", "current": True}],
        "evidence_links": [
            {"opportunity_id": DB_OPP, "dedupe_key": MSG_R, "relation": "origin", "current": True},
            {"opportunity_id": DB_OPP, "dedupe_key": MSG_R2, "relation": "supports", "current": True},
        ],
        "revisions": [
            {"quote_id": "q1", "opportunity_id": DB_OPP, "quote_number": "01001-26", "number_origin": "printed_historical",
             "revision_id": "r1", "revision_no": 1, "status": "sent", "origin": "historical_import",
             "pdf_sha256": SHA_R, "sent_at": "t", "origin_dedupe_key": MSG_R, "superseded_by_revision_no": None},
            {"quote_id": "q2", "opportunity_id": DB_OPP, "quote_number": "01002-26", "number_origin": "printed_historical",
             "revision_id": "r2", "revision_no": 1, "status": "sent", "origin": "historical_import",
             "pdf_sha256": SHA_R2, "sent_at": "t", "origin_dedupe_key": MSG_R2, "superseded_by_revision_no": None},
        ],
        "records": [_record(MSG_R, "s1"), _record(MSG_R2, "s2")],
        "assertions": [
            {"dedupe_key": MSG_R, "id": "a1", "kind": "document_reference", "value_norm": f"sha256:{SHA_R}",
             "value": None, "resolution": "promoted"},
            {"dedupe_key": MSG_R2, "id": "a2", "kind": "document_reference", "value_norm": f"sha256:{SHA_R2}",
             "value": None, "resolution": "promoted"},
            {"dedupe_key": MSG_R2, "id": "a3", "kind": "document_reference", "value_norm": f"sha256:{SHA_R}",
             "value": None, "resolution": "unresolved"},
        ],
        "organizations": {ORG: "Universidad"},
        "operators": [],
    }


def _by(review, planned):
    return next(r for r in review["opportunities"] if r["planned_opportunity_id"] == planned)


def test_clean_rehearsal_is_complete_and_both_invariants_hold():
    review = build_review(_Plan(_intent()), _db())
    assert all(inv["ok"] for inv in review["invariants"].values())
    c = review["counts"]
    assert (c["ready"], c["waiting"], c["held"]) == (1, 1, 1)
    assert (c["quotes_in_database_for_ready"], c["revisions_in_database_for_ready"]) == (2, 2)
    assert (c["gmail_records_in_database"], c["assertions_on_ready_records"]) == (2, 3)
    ready = _by(review, READY)
    assert ready["completeness"] == "complete"
    assert ready["confirmation"]["confirmed_by"] == "contacto@origenlab.cl"
    assert _by(review, WAIT)["completeness"] == "not_imported"
    assert _by(review, WAIT)["confirmation"]["state"] == "pending"
    assert _by(review, HELD)["confirmation"]["state"] == "held"


def test_canonical_message_and_duplicate_carriers_are_separated():
    rev = _by(build_review(_Plan(_intent()), _db()), READY)["quotes"][0]["revisions"][0]
    assert rev["canonical_message"] == MSG_R
    assert rev["duplicate_messages"] == [MSG_R2]


def test_a_waiting_opportunity_with_a_receipt_is_reported_as_leaked():
    db = _db()
    db["receipts"].append({"key": f"qimport:v1:{WAIT}:open", "command": "open_commercial_case",
                           "status": "completed", "response": {"opportunity_id": "other"}})
    review = build_review(_Plan(_intent()), db)
    assert not review["invariants"]["waiting_or_held_in_database"]["ok"]
    assert _by(review, WAIT)["completeness"] == "leaked"


def test_a_held_document_revision_in_the_database_is_reported():
    db = _db()
    extra = dict(db["revisions"][0], quote_id="q9", revision_id="r9", pdf_sha256=SHA_H, opportunity_id="zz")
    db["revisions"].append(extra)
    review = build_review(_Plan(_intent()), db)
    leaks = review["invariants"]["waiting_or_held_in_database"]["violations"]
    assert [v["planned_opportunity_id"] for v in leaks] == [HELD]
    assert review["counts"]["unplanned_revisions_in_database"] == 1


def test_an_out_of_scope_record_linked_to_a_ready_opportunity_is_reported():
    db = _db()
    db["evidence_links"].append({"opportunity_id": DB_OPP, "dedupe_key": MSG_W, "relation": "supports", "current": True})
    inv = build_review(_Plan(_intent()), db)["invariants"]["unready_evidence_in_ready_opportunity"]
    assert not inv["ok"] and inv["violations"][0]["dedupe_key"] == MSG_W


def test_an_assertion_about_an_unready_document_is_reported():
    db = _db()
    db["assertions"].append({"dedupe_key": MSG_R, "id": "a9", "kind": "document_reference",
                             "value_norm": f"sha256:{SHA_W}", "value": None, "resolution": "unresolved"})
    inv = build_review(_Plan(_intent()), db)["invariants"]["unready_evidence_in_ready_opportunity"]
    assert not inv["ok"] and SHA_W in inv["violations"][0]["why"]


def test_a_missing_revision_makes_the_ready_opportunity_incomplete():
    db = _db()
    db["revisions"].pop()
    review = build_review(_Plan(_intent()), db)
    assert _by(review, READY)["completeness"] == "incomplete"
    assert review["invariants"]["ready_opportunity_incomplete"]["violations"][0]["failed"] == [
        "every_revision_in_database"
    ]


def test_organization_linked_to_another_role_does_not_count():
    db = _db()
    db["organization_links"][0]["role"] = "supplier"
    assert "confirmed_organization_linked" in build_review(_Plan(_intent()), db)["invariants"][
        "ready_opportunity_incomplete"]["violations"][0]["failed"]


def test_message_body_never_leaves_the_header_allowlist():
    view = _header_view({"subject_raw": "s", "body_text": "SECRET", "body_html": "<p>SECRET</p>", "snippet": "S"})
    assert "SECRET" not in json.dumps(view) and view["subject_raw"] == "s"
    blob = json.dumps(build_review(_Plan(_intent()), _db()))
    assert "body" not in blob


def _write_plan(tmp_path, intent, pin=None):
    (tmp_path / "intent.json").write_text(json.dumps(intent, indent=2), encoding="utf-8")
    (tmp_path / "intent.sha256").write_text((pin or plan_sha256(intent)) + "\n")
    return tmp_path


def test_load_plan_accepts_the_canonical_hash_and_refuses_a_mismatch(tmp_path):
    intent = _intent()
    assert load_plan(_write_plan(tmp_path, intent)).sha256 == plan_sha256(intent)
    _write_plan(tmp_path, intent, pin="0" * 64)
    with pytest.raises(PlanRefused):
        load_plan(tmp_path)


def test_revision_pdf_only_for_imported_documents_with_matching_bytes(tmp_path):
    body = b"%PDF-1.4 real"
    sha = hashlib.sha256(body).hexdigest()
    intent = _intent()
    intent["opportunities"][0]["documents"][0] = sha
    intent["opportunities"][0]["quotes"][0]["revisions"][0]["document_sha256"] = sha
    intent["evidence_manifest"]["records"][0]["observations"][0]["value"] = f"sha256:{sha}"
    intent["evidence_manifest"]["records"][0]["payload"]["documents"][0]["sha256"] = sha
    db = _db()
    db["revisions"][0]["pdf_sha256"] = sha
    db["assertions"][0]["value_norm"] = f"sha256:{sha}"
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "r.pdf").write_bytes(body)
    (tmp_path / "docs" / "r2.pdf").write_bytes(b"not the hashed bytes")
    plan = _Plan(intent)
    review = build_review(plan, db)
    assert revision_document_path(review, plan, tmp_path, sha) == (tmp_path / "docs" / "r.pdf").resolve()
    assert revision_document_path(review, plan, tmp_path, SHA_R2) is None  # bytes do not hash to it
    assert revision_document_path(review, plan, tmp_path, SHA_W) is None  # not imported
    assert revision_document_path(review, plan, tmp_path, "../etc") is None


def test_revision_pdf_refuses_a_stored_path_outside_the_root(tmp_path):
    intent = _intent()
    intent["evidence_manifest"]["records"][0]["payload"]["documents"][0]["stored_path"] = "../outside.pdf"
    plan = _Plan(copy.deepcopy(intent))
    assert revision_document_path(build_review(plan, _db()), plan, tmp_path / "root", SHA_R) is None
