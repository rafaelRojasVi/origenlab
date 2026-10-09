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
    last_contacts,
    live_drive_overview,
    load_drive_ledgers,
)
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.marketing_audience import address_ref

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
        # Joins the card to its equipment interests when a viewer sees the address masked.
        "address_ref": address_ref("persona@ejemplo.invalid"),
    }
    assert card["status"] == "ok"
    assert card["next_action"]["source"] == "suggested"
    assert "00001-26" in card["next_action"]["text"]
    assert "_recipients" not in card["latest_revision"]


def test_card_carries_the_versions_the_drawer_actions_compare_against() -> None:
    """«Cambiar etapa» / «Marcar ganada» send the case version; «Confirmar institución» the
    organization's. Both come from the card, so the drawer never guesses one."""
    card = compose_pipeline(
        [_opp("o1", version=4, organization_version=2, organization_confirmation="machine_proposed")],
        [], [], [], {}, [], {},
    )[0]
    assert card["version"] == 4
    assert card["organization"] == {
        "organization_id": "org-1",
        "name": "Institución Ejemplo",
        "confirmation": "machine_proposed",
        "version": 2,
    }



def test_confirmed_requester_is_independent_of_organization_profile_confirmation() -> None:
    """An unconfirmed organization record does not undo a human-confirmed case role."""
    role = {
        "opportunity_organization_id": "relation-1",
        "opportunity_id": "o1",
        "organization_id": "org-1",
        "role": "requesting_institution",
        "name": "Institución Ejemplo",
        "confirmation": "confirmed",
        "organization_version": 2,
    }
    card = compose_pipeline(
        [_opp("o1", organization_confirmation="machine_proposed")],
        [role], [], [], {}, [], {},
    )[0]
    assert card["organization"]["confirmation"] == "machine_proposed"
    assert card["requesting_institution_confirmation"] == "confirmed"


def test_machine_proposed_mention_stays_unassigned_but_can_be_reviewed() -> None:
    """A machine proposal stays 'mentioned' and carries an exact reviewable row ID."""
    mention = {
        "opportunity_organization_id": "relation-2",
        "opportunity_id": "o1",
        "organization_id": "org-2",
        "role": "mentioned",
        "name": "Institución Mencionada",
        "confirmation": "machine_proposed",
        "organization_version": 1,
    }
    card = compose_pipeline(
        [_opp("o1", stage="lead", organization_id=None, organization_name=None)],
        [mention], [], [], {}, [], {},
    )[0]
    assert card["organization"] is None
    assert card["requesting_institution_confirmation"] is None
    assert card["pending_institution_mentions"] == [{
        "opportunity_organization_id": "relation-2",
        "organization_id": "org-2",
        "name": "Institución Mencionada",
    }]
    assert any(x["code"] == "no_requesting_institution" for x in card["attention"])


def test_no_unreviewed_row_id_is_fabricated_for_a_mentioned_institution() -> None:
    """Older input projections without relationship IDs cannot expose a write target."""
    card = compose_pipeline(
        [_opp("o1", stage="lead", organization_id=None, organization_name=None)],
        [{
            "opportunity_id": "o1", "organization_id": "org-2",
            "role": "mentioned", "name": "Institución Mencionada",
            "confirmation": "machine_proposed",
        }], [], [], {}, [], {},
    )[0]
    assert card["pending_institution_mentions"] == []


def test_last_contact_is_the_newest_email_each_way_on_the_case_threads() -> None:
    """«Último contacto»: the newest email OrigenLab sent and the newest it received."""
    rows = [
        # Ours, by the capture's direction; then a later one, by the sender's domain.
        {"opportunity_id": "o1", "sent_at": "2026-09-01T10:00:00-03:00", "subject": "Cotización",
         "gmail_message_id": "m1", "sender": "Ventas <contacto@origenlab.cl>", "direction_hint": None,
         "comms_direction": "outbound"},
        {"opportunity_id": "o1", "sent_at": "2026-09-20T10:00:00-03:00", "subject": "Re: seguimiento",
         "gmail_message_id": "m3", "sender": "contacto@origenlab.cl", "direction_hint": None,
         "comms_direction": None},
        # The client's reply, staged with no capture row.
        {"opportunity_id": "o1", "sent_at": "2026-09-10T09:00:00Z", "subject": "Re: Cotización",
         "gmail_message_id": "m2", "sender": "Persona <persona@ejemplo.invalid>", "direction_hint": None,
         "comms_direction": None},
        # The same message reached through a second thread of the case: counted once.
        {"opportunity_id": "o1", "sent_at": "2026-09-20T10:00:00-03:00", "subject": "Re: seguimiento",
         "gmail_message_id": "m3", "sender": "contacto@origenlab.cl", "direction_hint": None,
         "comms_direction": None},
        # A staged record of ours carries `direction_hint`; a date that is not a date is skipped.
        {"opportunity_id": "o2", "sent_at": "2026-08-01T12:00:00+00:00", "subject": None,
         "gmail_message_id": "m4", "sender": None, "direction_hint": "sent", "comms_direction": None},
        {"opportunity_id": "o2", "sent_at": "ayer", "subject": None, "gmail_message_id": "m5",
         "sender": "persona@ejemplo.invalid", "direction_hint": None, "comms_direction": None},
    ]
    out = last_contacts(rows)
    assert out["o1"] == {
        "outbound": {"at": "2026-09-20T10:00:00-03:00", "subject": "Re: seguimiento",
                     "url": "https://mail.google.com/mail/u/0/#all/m3"},
        "inbound": {"at": "2026-09-10T09:00:00+00:00", "subject": "Re: Cotización",
                    "url": "https://mail.google.com/mail/u/0/#all/m2", "sender_name": "Persona"},
    }
    assert out["o2"]["outbound"]["at"] == "2026-08-01T12:00:00+00:00"
    assert out["o2"]["inbound"] is None

    cards = compose_pipeline([_opp("o1"), _opp("o3")], [], [], [], {}, [], {}, out)
    assert cards[0]["last_contact"] == out["o1"]
    assert cards[1]["last_contact"] == {"outbound": None, "inbound": None}


def test_mail_display_name_never_contains_an_address() -> None:
    for sender in ('person@example.invalid', '"person@example.invalid" <person@example.invalid>'):
        touch = last_contacts([{"opportunity_id": "o", "sent_at": "2026-10-01T12:00:00Z",
                                "sender": sender, "comms_direction": "inbound"}])["o"]["inbound"]
        assert "sender_name" not in touch


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


def _one_quote_card(sources: dict) -> dict:
    return compose_pipeline(
        [_opp("o1")], [],
        [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "00001-26", "number_origin": "printed_historical"}],
        [_rev("r1", "q1", 1, SHA_A)], sources, [], {},
    )[0]


def test_a_pdf_the_archiver_can_file_is_pending_not_missing() -> None:
    card = _one_quote_card({"src-1": {**SOURCES["src-1"], "has_eml": True}})
    codes = [a["code"] for a in card["attention"]]
    assert "document_pending_drive" in codes and "document_not_in_drive" not in codes
    assert card["latest_revision"]["drive_pending"] is True
    pending = [a for a in card["attention"] if a["code"] == "document_pending_drive"]
    assert pending and pending[0]["blocking"] is False


def test_a_pdf_with_no_captured_email_still_needs_a_manual_upload() -> None:
    card = _one_quote_card(SOURCES)
    codes = [a["code"] for a in card["attention"]]
    assert "document_not_in_drive" in codes and "document_pending_drive" not in codes
    assert card["latest_revision"]["drive_pending"] is False


def test_a_void_revision_or_one_without_a_pdf_is_never_pending_archive() -> None:
    """The archiver skips void revisions and revisions with no PDF hash; so must the label."""
    sources = {"src-1": {**SOURCES["src-1"], "has_eml": True}}
    for rev in (_rev("r1", "q1", 1, SHA_A, status="void"), _rev("r1", "q1", 1, None)):
        card = compose_pipeline(
            [_opp("o1")], [],
            [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "00001-26", "number_origin": "printed_historical"}],
            [rev], sources, [], {},
        )[0]
        [r] = card["quotes"][0]["revisions"]
        assert r["drive_pending"] is False


def test_a_pending_archive_alone_does_not_make_a_case_pending() -> None:
    card = _one_quote_card({"src-1": {**SOURCES["src-1"], "has_eml": True}})
    others = [a["code"] for a in card["attention"] if a["code"] not in ("no_crm_contact", "document_pending_drive")]
    assert "document_pending_drive" in [a["code"] for a in card["attention"]]
    assert others == [] and card["status"] == "ok"


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


def test_drive_folders_are_newest_quote_number_first_not_text_order(tmp_path: Path) -> None:
    """Typed numbers vary: a dropped leading zero, a revision digit glued on, a letter suffix.

    Text order put "1013-26" above "01246-26"; the number, read as the five-digit correlative it
    is meant to be, puts it where it belongs. The year comes first, so another year's number is
    never mixed into this one's.
    """
    numbers = ["01024-26", "1013-26", "011728A-25", "01246-26", "012392-26"]
    rows = [
        _row(chr(ord("a") + i) * 64, f"f-{i}", folder=f"folder-{i}", quote_number=n, case_key=f"case-{i}")
        for i, n in enumerate(numbers)
    ]
    out = compose_drive_archive(load_drive_ledgers([_ledger(tmp_path, "run-1", rows)]), {})
    assert [f["quote_numbers"][0] for f in out["folders"]] == [
        "01246-26",
        "012392-26",
        "01024-26",
        "1013-26",
        "011728A-25",
    ]


def test_overview_drive_counts_live_worker_links_even_when_not_in_boot_ledger() -> None:
    """The first quote after boot is archived asynchronously, without a Render API restart."""
    old_ledger = {SHA_A: object()}
    status = live_drive_overview(
        {SHA_A, SHA_B, SHA_C},
        {SHA_A, SHA_B},  # SHA_B was just filed by the worker after API startup.
        old_ledger,  # no refreshed archive_links.jsonl on the API container.
    )
    assert status == {
        "configured": True, "documents": 2,
        "revisions_with_drive_file": 2, "revisions_total": 3,
    }
    assert live_drive_overview({SHA_A}, set(), {}) == {
        "configured": False, "documents": 0,
        "revisions_with_drive_file": 0, "revisions_total": 1,
    }


def test_overview_provenance_matches_running_v2_system() -> None:
    assert ENTITY_NOTES["messages"]["provenance"] == "partial"
    assert "sincronizador Gmail V2" in ENTITY_NOTES["messages"]["note"]
    assert ENTITY_NOTES["products"]["provenance"] == "imported"
    assert ENTITY_NOTES["tasks"]["provenance"] == "partial"
    assert ENTITY_NOTES["persons"]["provenance"] == "partial"
    assert ENTITY_NOTES["drive_links_in_crm"]["provenance"] == "imported"
    assert "evidence.source_record" in ENTITY_NOTES["drive_links_in_crm"]["note"]


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
        "/v2/workspace/equipment-interests",
        "/v2/workspace/people/{person_id}",
        "/v2/workspace/people/merge-preview",
        "/v2/workspace/organizations/{organization_id}/authoring",
        "/v2/workspace/person-suggestions",
        "/v2/workspace/opportunities/{opportunity_id}/notes",
        "/v2/workspace/opportunities/{opportunity_id}/mail-documents",
        "/v2/workspace/opportunities/{opportunity_id}/quote-candidates",
        "/v2/workspace/opportunities/{opportunity_id}/purchase-order-candidates",
        "/v2/workspace/mail-sync",
        "/v2/workspace/mail-quote-numbers",
        "/v2/workspace/marketing",
        "/v2/workspace/marketing/taxonomy",
        "/v2/workspace/marketing/audience",
        "/v2/workspace/marketing/campaigns/{campaign_id}",
        "/v2/workspace/marketing/campaigns/{campaign_id}/freeze-preview",
        "/v2/workspace/marketing/campaigns/{campaign_id}/recipients",
        "/v2/workspace/marketing/campaigns/{campaign_id}/archive",
        "/v2/workspace/marketing/campaigns/{campaign_id}/history/recipients",
        "/v2/workspace/marketing/campaigns/{campaign_id}/history/replies",
        "/v2/workspace/marketing/campaigns/{campaign_id}/history/audit",
        "/v2/workspace/marketing/suppressions",
        "/v2/workspace/marketing/campaign-blocks",
        "/v2/workspace/drive",
        "/v2/workspace/review",
        "/v2/workspace/fx",
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


def test_first_address_keeps_a_quoted_display_name_with_a_comma_whole() -> None:
    from origenlab_api.v2.crm_workspace import _first_address

    assert _first_address('"Ruiz, Ana" <ana@example.cl>, otro@example.cl') == ("Ruiz, Ana <ana@example.cl>", 2)
    assert _first_address("uno@example.cl; dos@example.cl") == ("uno@example.cl", 2)
    assert _first_address(None) == (None, 0)


def test_drive_file_records_link_revisions_and_a_ledger_link_wins() -> None:
    """The worker's `drive_file` records (apps/worker drive_filing.py) reach the cards through the
    revisions read; a document the laptop ledger also names keeps the ledger's link."""
    from origenlab_api.v2.crm_workspace import DriveLink, drive_links_from_records

    sha_a, sha_b, sha_c = "a" * 64, "b" * 64, "c" * 64
    ledger = {sha_b: DriveLink(document_sha256=sha_b, file_id="ledger-file", file_url="https://drive.example/l",
                               folder_id="lf", case_key="k", quote_number="01244-26", revision=1,
                               original_filename="viejo.pdf", archive_status="archived_verified",
                               ledger_crm_status=None, lifecycle=None, gmail_message_id=None, ledger="run-1")}
    revisions = [
        {"pdf_sha256": sha_a, "drive_record": json.dumps({
            "drive_file_id": "f-a", "drive_folder_id": "folder-a", "case_key": "case-1",
            "file_name": "01250-26 r1 — Cliente.pdf", "quote_number": "01250-26", "revision": 1})},
        {"pdf_sha256": sha_b, "drive_record": {"drive_file_id": "other", "drive_folder_id": "x"}},
        {"pdf_sha256": sha_c, "drive_record": None},
        {"pdf_sha256": None, "drive_record": {"drive_file_id": "orphan"}},
    ]
    links = drive_links_from_records(revisions, ledger)
    assert set(links) == {sha_a, sha_b}
    assert links[sha_b].file_id == "ledger-file"
    a = links[sha_a].as_dict()
    assert (a["file_id"], a["folder_id"], a["original_filename"]) == ("f-a", "folder-a", "01250-26 r1 — Cliente.pdf")
    assert a["file_url"] == "https://drive.google.com/file/d/f-a/view"
    assert a["folder_url"] == "https://drive.google.com/drive/folders/folder-a"
