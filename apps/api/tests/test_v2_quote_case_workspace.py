"""Case workspace: CRM status apart from archive status, pending reasons, links, collisions, hash-checked inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2 import quote_case_archive as qa
from origenlab_api.v2 import quote_case_workspace as w
from origenlab_api.v2.cockpit_routes import _current_operator
from origenlab_api.v2.quote_case_workspace_routes import case_archive_router

SA, SB, SC, SP, SX = (hashlib.sha256(t.encode()).hexdigest() for t in ("a", "b", "c", "pending", "legacy-x"))


def _plan() -> dict:
    return {
        "plan_version": "qimport/test",
        "opportunities": [
            {"planned_opportunity_id": "case-ready", "status": "ready", "printed_addressee": "Ana - Lab A",
             "printed_organization": "Lab A", "reasons": [], "warnings": [],
             "confirmation": {"action": "confirm_existing", "organization_id": "org-1"},
             "quotes": [{"quote_number": "01001-26", "revisions": [
                 {"document_sha256": SB, "sent_at": "2026-05-02T10:00:00-04:00", "origin_dedupe_key": "gmail_message:g-b"},
                 {"document_sha256": SA, "sent_at": "2026-05-01T10:00:00-04:00", "origin_dedupe_key": "gmail_message:g-a"}]}]},
            {"planned_opportunity_id": "case-org", "status": "needs_organization_confirmation", "printed_addressee": "Beto",
             "printed_organization": None, "reasons": [], "warnings": [], "confirmation": None,
             "quotes": [{"quote_number": "01009-26", "revisions": [
                 {"document_sha256": SC, "sent_at": "2026-05-03T10:00:00-04:00", "origin_dedupe_key": "gmail_message:g-c"}]}]},
            {"planned_opportunity_id": "case-held", "status": "held", "printed_addressee": None, "printed_organization": None,
             "reasons": ["abc:document_left_pending"], "warnings": [], "confirmation": None, "quotes": []},
        ],
        "pending_documents": [{"planned_opportunity_id": "case-held", "document_sha256": SP, "quote_number": "01243-26",
                               "code": "owner_hold_no_organization"}],
    }


def _inventory() -> dict:
    def item(fid, name, parent, path, **kw):
        return {"id": fid, "name": name, "parents": [parent], "path": path, "webViewLink": f"https://drive/{fid}", **kw}
    return {
        "finished_at": "2026-09-26T04:20:00Z",
        "trees": {
            "Casos": [
                item("F1", "Caso 01009 — Beto", "CASOS", "Cotizaciones/Casos/Caso 01009 — Beto",
                     mimeType=qa.FOLDER_MIME, appProperties={qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: "case-org"}),
                item("D1", "01009-26 r1 — x.pdf", "F1", "…", mimeType="application/pdf", sha256Checksum=SC,
                     appProperties={qa.PROP_KIND: qa.KIND_REVISION, qa.PROP_CASE_KEY: "case-org", qa.PROP_DOC_SHA: SC}),
                item("F2", "Caso 01243 — Pablo Álvarez", "CASOS", "Cotizaciones/Casos/Caso 01243 — Pablo Álvarez",
                     mimeType=qa.FOLDER_MIME, appProperties={qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: "case-held"}),
                item("D2", "01243-26 r1 — y.pdf", "F2", "…", mimeType="application/pdf", sha256Checksum=SP,
                     appProperties={qa.PROP_KIND: qa.KIND_REVISION, qa.PROP_CASE_KEY: "case-held", qa.PROP_DOC_SHA: SP,
                                    qa.PROP_GMAIL_MESSAGE: "g-p"}),
            ],
            "Enviadas": [
                item("L1", "CN01001-Ana.pdf", "E1", "Cotizaciones/Enviadas/CN01001 — Ana/CN01001-Ana.pdf",
                     mimeType="application/pdf", sha256Checksum=SA),
                item("L2", "misterio.pdf", "E1", "Cotizaciones/Enviadas/CN01001 — Ana/misterio.pdf",
                     mimeType="application/pdf", sha256Checksum=SX),
            ],
            "Pendientes": [],
        },
    }


def _manifest(inputs: dict[str, str]) -> dict:
    return {"inputs_sha256": inputs,
            "cases": [{"case_key": "case-ready", "folder_action": "create_case_folder", "documents": [{"x": 1}]}],
            "collisions": [{"number": "1009-26", "kind": "several_cases", "cases": ["case-org", "other"]}]}


def _write_dir(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "intent.json").write_text(json.dumps(_plan()))
    (src / "inventory.json").write_text(json.dumps(_inventory()))
    d = tmp_path / "dryrun"
    d.mkdir()
    inputs = {str(src / n): hashlib.sha256((src / n).read_bytes()).hexdigest() for n in ("intent.json", "inventory.json")}
    (d / "migration_manifest.json").write_text(json.dumps(_manifest(inputs)))
    (d / "reconciliation.json").write_text(json.dumps([
        {"drive_id": "L2", "path": "Cotizaciones/Enviadas/CN01001 — Ana/misterio.pdf", "classification": "unknown_document",
         "reason": "no local evidence", "printed_quote_number": None, "sha256": SX, "flags": [], "lifecycle": "unknown_needs_review",
         "action": "blocked", "case_key": None}]))
    (d / "SHA256SUMS").write_text(
        f"{hashlib.sha256((d / 'migration_manifest.json').read_bytes()).hexdigest()}  migration_manifest.json\n")
    report = tmp_path / "upload.json"
    report.write_text(json.dumps({"uploads": [{"file": {"id": "D2"}, "drive_sha256_matches": True,
                                               "downloaded_sha256_matches": True, "size_matches": True}]}))
    return d


@pytest.fixture
def view(tmp_path: Path) -> dict:
    d = _write_dir(tmp_path)
    return w.build_case_workspace(w.load_inputs(d, [str(tmp_path / "upload.json")]))


def case(view: dict, key: str) -> dict:
    return next(c for c in view["cases"] if c["case_key"] == key)


def test_one_case_shows_all_its_revisions_in_sent_order(view) -> None:
    c = case(view, "case-ready")
    [q] = c["quotes"]
    assert [(r["revision_no"], r["document_sha256"]) for r in q["revisions"]] == [(1, SA), (2, SB)]
    assert [r["lifecycle"] for r in q["revisions"]] == ["historical_sent", "revision"]
    assert c["folder_name"] == "Caso 01001 — Ana - Lab A" and c["archive"]["planned_folder_action"] == "create_case_folder"


def test_crm_status_comes_from_the_plan_and_says_so(view) -> None:
    for c in view["cases"]:
        assert c["crm"]["source"] == "import_plan"
    assert case(view, "case-ready")["crm"]["status"] == "ready_to_import"
    assert view["source"]["crm_status_source"].startswith("import_plan")


def test_an_archived_document_of_a_waiting_case_is_not_shown_as_ready(view) -> None:
    c = case(view, "case-org")
    rev = c["quotes"][0]["revisions"][0]
    assert rev["archive"]["status"] == "archived_unverified"  # in Casos, no download verification report
    assert c["crm"]["status"] == "pending_organization_confirmation"
    assert rev["lifecycle"] == "pending_organization_confirmation"
    assert c["crm"]["reasons"][0]["label"] == "Falta confirmar la institución"
    assert "missing_organization" in c["flags"]


def test_pending_document_keeps_its_case_real_folder_name_and_gmail_link(view) -> None:
    c = case(view, "case-held")
    assert c["folder_name"] == "Caso 01243 — Pablo Álvarez"
    rev = c["quotes"][0]["revisions"][0]
    assert rev["role"] == "pending_document" and rev["lifecycle"] == "held"
    assert rev["archive"]["status"] == "archived_verified"
    assert rev["gmail_url"] == "https://mail.google.com/mail/u/0/#all/g-p"
    assert {r["code"] for r in c["crm"]["reasons"]} >= {"abc:document_left_pending", "owner_hold_no_organization"}
    assert "pending_document" in c["flags"]


def test_legacy_only_documents_link_their_legacy_file(view) -> None:
    rev = case(view, "case-ready")["quotes"][0]["revisions"][0]
    assert rev["archive"]["status"] == "legacy_only"
    assert rev["archive"]["legacy_files"][0]["url"] == "https://drive/L1"
    assert rev["gmail_url"] == "https://mail.google.com/mail/u/0/#all/g-a"
    assert case(view, "case-ready")["quotes"][0]["revisions"][1]["archive"]["status"] == "not_archived"


def test_collisions_and_unplaced_documents_are_obvious(view) -> None:
    c = case(view, "case-org")
    assert "number_collision" in c["flags"] and c["quotes"][0]["collision"]["kind"] == "several_cases"
    assert view["counts"]["cases_with_collisions"] == 1
    [u] = view["unplaced_legacy_documents"]
    assert u["classification"] == "unknown_document" and u["url"] == "https://drive/L2"


def test_counts(view) -> None:
    assert view["counts"]["by_crm_status"] == {"held": 1, "pending_organization_confirmation": 1, "ready_to_import": 1}
    assert view["counts"]["by_archive_status"] == {"archived_unverified": 1, "archived_verified": 1,
                                                   "legacy_only": 1, "not_archived": 1}


def test_a_changed_input_refuses_to_load(tmp_path: Path) -> None:
    d = _write_dir(tmp_path)
    (tmp_path / "src" / "intent.json").write_text(json.dumps(_plan() | {"plan_version": "edited"}))
    with pytest.raises(w.WorkspaceRefused, match="intent.json"):
        w.load_inputs(d)


def test_a_changed_manifest_refuses_to_load(tmp_path: Path) -> None:
    d = _write_dir(tmp_path)
    (d / "migration_manifest.json").write_text("{}")
    with pytest.raises(w.WorkspaceRefused, match="SHA256SUMS"):
        w.load_inputs(d)


def test_route_is_get_only_and_serves_the_view(tmp_path: Path) -> None:
    d = _write_dir(tmp_path)
    app = FastAPI()
    app.state.case_archive = w.load_inputs(d)
    app.include_router(case_archive_router)
    app.dependency_overrides[_current_operator] = lambda: object()
    client = TestClient(app)
    body = client.get("/v2/cockpit/case-archive").json()
    assert body["counts"]["cases"] == 3
    assert client.post("/v2/cockpit/case-archive").status_code == 405
