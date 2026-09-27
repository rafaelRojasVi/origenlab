"""CRM workspace reads — ``/v2/workspace/*``.

In-process only: the composition is pure and the routes are checked for method, prefix and
identity. Every name, address, number and hash below is invented; the repository is public.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace import (
    ENTITY_NOTES,
    CrmWorkspaceRepository,
    DriveLedgerError,
    compose_drive_archive,
    compose_pipeline,
    load_drive_ledgers,
)
from origenlab_api.v2.crm_workspace_routes import workspace_router

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _ledger(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    d = tmp_path / name
    d.mkdir()
    p = d / "archive_links.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def _row(sha: str, file_id: str, folder: str = "folder-1", **extra: object) -> dict:
    return {
        "document_sha256": sha,
        "drive_file_id": file_id,
        "drive_folder_id": folder,
        "drive_web_view_link": f"https://drive.google.com/file/d/{file_id}/view",
        "case_key": "case-1",
        "quote_number": "00001-26",
        "revision": 1,
        "original_filename": "CN00001-Ejemplo.pdf",
        "archive_status": "archived_verified",
        "crm_status": "ready_to_import",
        "gmail_message_id": "gm-1",
        **extra,
    }


# ─────────────────────────────────────────────────────────────── ledgers ──


def test_ledger_indexes_by_sha(tmp_path: Path) -> None:
    p = _ledger(tmp_path, "run-1", [_row(SHA_A, "f-a"), _row(SHA_B, "f-b", folder="folder-2")])
    index = load_drive_ledgers([p])
    assert set(index) == {SHA_A, SHA_B}
    assert index[SHA_A].ledger == "run-1"
    assert index[SHA_A].as_dict()["folder_url"] == "https://drive.google.com/drive/folders/folder-1"
    assert index[SHA_A].as_dict()["source"] == "archive_ledger"


def test_ledger_refuses_two_files_for_one_document(tmp_path: Path) -> None:
    p1 = _ledger(tmp_path, "run-1", [_row(SHA_A, "f-a")])
    p2 = _ledger(tmp_path, "run-2", [_row(SHA_A, "f-other")])
    with pytest.raises(DriveLedgerError, match="two different Drive files"):
        load_drive_ledgers([p1, p2])


def test_ledger_same_file_twice_is_idempotent(tmp_path: Path) -> None:
    p1 = _ledger(tmp_path, "run-1", [_row(SHA_A, "f-a")])
    p2 = _ledger(tmp_path, "run-2", [_row(SHA_A, "f-a")])
    assert load_drive_ledgers([p1, p2])[SHA_A].ledger == "run-1"


def test_ledger_missing_file_refuses(tmp_path: Path) -> None:
    with pytest.raises(DriveLedgerError, match="not found"):
        load_drive_ledgers([tmp_path / "nope.jsonl"])


def test_ledger_malformed_row_refuses(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    (d / "archive_links.jsonl").write_text('{"drive_file_id": "x"}\n', encoding="utf-8")
    with pytest.raises(DriveLedgerError, match="malformed"):
        load_drive_ledgers([d / "archive_links.jsonl"])


def _upload_report(tmp_path: Path, *, mode: str = "upload", verified: bool = True) -> Path:
    d = tmp_path / f"upload-{mode}-{verified}"
    d.mkdir()
    p = d / "drive_case_upload_report.json"
    p.write_text(
        json.dumps(
            {
                "mode": mode,
                "all_verified": verified,
                "uploads": [
                    {
                        "quote_number": "00002-26",
                        "case_key": "case-2",
                        "folder": {"id": "folder-9"},
                        "file": {
                            "id": "f-c",
                            "name": "00002-26 r1 — CN00002.pdf",
                            "sha256Checksum": SHA_C,
                            "appProperties": {"origenlab_revision": "1", "origenlab_gmail_message_id": "gm-9"},
                        },
                        "downloaded_sha256_matches": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return p


def test_verified_upload_report_is_a_ledger(tmp_path: Path) -> None:
    index = load_drive_ledgers([_upload_report(tmp_path)])
    assert index[SHA_C].folder_id == "folder-9"
    assert index[SHA_C].revision == 1
    assert index[SHA_C].gmail_message_id == "gm-9"


@pytest.mark.parametrize(("mode", "verified"), [("check", True), ("upload", False)])
def test_unverified_or_check_report_refuses(tmp_path: Path, mode: str, verified: bool) -> None:
    with pytest.raises(DriveLedgerError, match="verified upload run"):
        load_drive_ledgers([_upload_report(tmp_path, mode=mode, verified=verified)])


# ──────────────────────────────────────────────────────────── composition ──


def _opp(oid: str, **extra: object) -> dict:
    return {
        "opportunity_id": oid,
        "title": f"Caso {oid}",
        "stage": "quoting",
        "organization_id": "org-1",
        "organization_name": "Institución Ejemplo",
        "organization_confirmation": "confirmed",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-02T00:00:00+00:00",
        "closed_at": None,
        "close_reason": None,
        **extra,
    }


def _rev(rid: str, qid: str, no: int, sha: str | None, src: str | None = "src-1", **extra: object) -> dict:
    return {
        "revision_id": rid,
        "quote_id": qid,
        "revision_no": no,
        "status": "sent",
        "origin": "historical_import",
        "sent_at": f"2026-0{no}-10T12:00:00+00:00",
        "pdf_sha256": sha,
        "superseded_by_revision_no": None,
        "origin_source_record_id": src,
        **extra,
    }


SOURCES = {
    "src-1": {
        "gmail_message_id": "gm-1",
        "gmail_thread_id": "th-1",
        "recipients": "persona@ejemplo.invalid, otra@ejemplo.invalid",
        "subject_raw": "Cotización",
        "documents": [{"sha256": SHA_A, "filename": "CN00001-Ejemplo.pdf"}],
    }
}


def test_card_carries_quote_revision_gmail_and_drive(tmp_path: Path) -> None:
    drive = load_drive_ledgers([_ledger(tmp_path, "run-1", [_row(SHA_A, "f-a")])])
    cards = compose_pipeline(
        [_opp("o1")],
        [],
        [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "00001-26", "number_origin": "printed_historical"}],
        [_rev("r1", "q1", 1, SHA_A, superseded_by_revision_no=2), _rev("r2", "q1", 2, SHA_A)],
        SOURCES,
        [],
        drive,
    )
    card = cards[0]
    assert card["quote_numbers"] == ["00001-26"]
    assert card["revision_count"] == 2
    assert card["latest_revision"]["revision_no"] == 2
    assert card["latest_revision"]["gmail"]["url"] == "https://mail.google.com/mail/u/0/#all/gm-1"
    assert card["latest_revision"]["document"]["filename"] == "CN00001-Ejemplo.pdf"
    assert card["drive_folder"]["folder_id"] == "folder-1"
    # The contact is the Gmail recipient, labelled as such — never a CRM person.
    assert card["contact"] == {
        "source": "gmail_recipient",
        "name": None,
        "address": "persona@ejemplo.invalid",
        "others": 1,
    }
    assert card["status"] == "ok"
    assert card["next_action"]["source"] == "suggested"
    assert "00001-26" in card["next_action"]["text"]
    assert "_recipients" not in card["latest_revision"]


def test_two_active_revisions_block_the_case() -> None:
    cards = compose_pipeline(
        [_opp("o1")],
        [],
        [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "00001-26", "number_origin": "printed_historical"}],
        [_rev("r1", "q1", 1, SHA_A), _rev("r2", "q1", 2, SHA_B)],
        SOURCES,
        [],
        {},
    )
    card = cards[0]
    assert card["status"] == "blocked"
    codes = [a["code"] for a in card["attention"]]
    assert "canonical_undetermined" in codes
    assert "document_not_in_drive" in codes
    assert card["drive_folder"] is None


def test_shared_printed_number_blocks_both_cases() -> None:
    quotes = [
        {"quote_id": "q1", "opportunity_id": "o1", "quote_number": "00005-26", "number_origin": "printed_historical"},
        {"quote_id": "q2", "opportunity_id": "o2", "quote_number": "00005-26", "number_origin": "printed_historical"},
    ]
    cards = compose_pipeline(
        [_opp("o1"), _opp("o2")], [], quotes,
        [_rev("r1", "q1", 1, SHA_A), _rev("r2", "q2", 1, SHA_B)], SOURCES, [], {},
    )
    assert all(c["status"] == "blocked" for c in cards)
    assert all(any(a["code"] == "shared_printed_number" for a in c["attention"]) for c in cards)


def test_lead_without_institution_or_quote() -> None:
    card = compose_pipeline(
        [_opp("o1", stage="lead", organization_id=None, organization_name=None)], [], [], [], {}, [], {}
    )[0]
    assert card["organization"] is None
    assert card["contact"] is None
    assert card["latest_revision"] is None
    assert card["status"] == "blocked"
    assert card["next_action"]["text"] == "Confirmar la institución solicitante"


def test_requesting_institution_from_case_role_and_other_roles_kept_apart() -> None:
    case_orgs = [
        {"opportunity_id": "o1", "organization_id": "org-9", "role": "requesting_institution", "name": "Compradora", "confirmation": "confirmed"},
        {"opportunity_id": "o1", "organization_id": "org-8", "role": "manufacturer", "name": "Fabricante", "confirmation": "confirmed"},
    ]
    card = compose_pipeline(
        [_opp("o1", stage="lead", organization_id=None, organization_name=None)], case_orgs, [], [], {}, [], {}
    )[0]
    assert card["organization"]["name"] == "Compradora"
    assert card["other_organizations"] == [{"organization_id": "org-8", "name": "Fabricante", "role": "manufacturer"}]


def test_crm_participant_wins_over_gmail_recipient() -> None:
    card = compose_pipeline(
        [_opp("o1")], [], [], [], {}, [{"opportunity_id": "o1", "name": "Persona Ejemplo"}], {}
    )[0]
    assert card["contact"]["source"] == "crm_participant"
    assert not any(a["code"] == "no_crm_contact" for a in card["attention"])


def test_drive_archive_separates_in_crm_from_not_imported(tmp_path: Path) -> None:
    drive = load_drive_ledgers(
        [_ledger(tmp_path, "run-1", [_row(SHA_A, "f-a"), _row(SHA_B, "f-b", crm_status="held")])]
    )
    out = compose_drive_archive(drive, {SHA_A: {"quote_number": "00001-26", "organization_name": "Institución Ejemplo"}})
    assert out["totals"] == {"folders": 1, "documents": 2, "in_crm": 1, "not_in_crm": 1}
    folder = out["folders"][0]
    assert folder["organization_name"] == "Institución Ejemplo"
    held = [d for d in folder["documents"] if not d["in_crm"]][0]
    assert held["ledger_crm_status"] == "held"


def test_every_counted_entity_has_a_provenance_note() -> None:
    from origenlab_api.v2.crm_workspace import _COUNT_SQL

    assert set(_COUNT_SQL) == set(ENTITY_NOTES)
    assert {n["provenance"] for n in ENTITY_NOTES.values()} <= {
        "imported", "partial", "not_imported", "no_write_path"
    }


# ──────────────────────────────────────────────────────────────── routes ──


def test_workspace_routes_are_get_only_under_prefix() -> None:
    paths = set()
    for route in workspace_router.routes:
        assert route.methods == {"GET"}, route.path
        assert route.path.startswith("/v2/workspace/")
        paths.add(route.path)
    assert paths == {
        "/v2/workspace/overview",
        "/v2/workspace/pipeline",
        "/v2/workspace/providers",
        "/v2/workspace/marketing",
        "/v2/workspace/drive",
        "/v2/workspace/review",
    }


class _RefusingIdentity:
    def resolve(self, headers):  # noqa: ANN001, ANN201
        from origenlab_api.v2.identity import IdentityRefused

        raise IdentityRefused("no operator credential")


def test_workspace_routes_require_an_operator() -> None:
    app = FastAPI()
    app.state.v2_identity = _RefusingIdentity()
    app.state.crm_workspace = CrmWorkspaceRepository(connect=None, dsn="unused")
    app.include_router(workspace_router)
    client = TestClient(app)
    for path in ("overview", "pipeline", "providers", "marketing", "drive", "review"):
        assert client.get(f"/v2/workspace/{path}").status_code == 401
        assert client.post(f"/v2/workspace/{path}").status_code == 405
