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
    drive_archive_from,
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



def test_card_carries_the_newest_note_on_the_case() -> None:
    """«Hoy» treats a note written after the client's last email as the operator's answer
    («Atendido» on a won case), so the card carries the newest active note — when, by whom, and
    its first words — or null when the case has none."""
    notes = [
        {"opportunity_id": "o1", "created_at": "2026-10-09T18:00:00Z", "body": "Atendido por teléfono.",
         "author_display_name": "Ventas"},
        {"opportunity_id": "o1", "created_at": "2026-10-02T09:00:00Z", "body": "Primera nota.",
         "author_display_name": "Ventas"},
    ]
    with_note, without = compose_pipeline(
        [_opp("o1"), _opp("o2")], [], [], [], {}, [], {}, None, None, notes,
    )
    assert with_note["last_note"] == {
        "created_at": "2026-10-09T18:00:00Z", "body": "Atendido por teléfono.", "author": "Ventas",
    }
    assert without["last_note"] is None
    # An older caller that passes no notes still gets a card.
    assert compose_pipeline([_opp("o1")], [], [], [], {}, [], {})[0]["last_note"] is None


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
    # The «Asignar institución» suggestion rides on inbound as private keys (stripped from the card).
    assert out["o1"]["inbound"].pop("_requester_domain") == "ejemplo.invalid"
    assert out["o1"]["inbound"].pop("_requester_read") is None
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

def test_each_note_says_who_clears_it_and_only_a_persons_note_makes_a_case_pending() -> None:
    """Owner 2026-10-25: a «pendiente» with no button to fix it is noise. Only notes a person
    clears (`owner == "person"`) make a case «Pendiente»; the worker's and plain facts do not."""
    card = _one_quote_card({"src-1": {**SOURCES["src-1"], "has_eml": True}})
    owners = {a["code"]: a["owner"] for a in card["attention"]}
    assert owners == {"no_crm_contact": "info", "document_pending_drive": "system"}
    assert card["status"] == "ok"

    no_quote = compose_pipeline([_opp("o1")], [], [], [], {}, [], {})[0]
    assert {a["code"]: a["owner"] for a in no_quote["attention"]}["no_quote"] == "person"
    assert no_quote["status"] == "pending"


def test_a_staged_revision_with_no_email_is_only_a_fact_not_a_pending() -> None:
    card = _one_quote_card({})
    owners = {a["code"]: a["owner"] for a in card["attention"]}
    assert owners == {"no_crm_contact": "info", "document_not_in_drive": "info", "no_gmail_evidence": "info"}
    assert card["status"] == "ok"


def _cotizador_card(rev: dict) -> dict:
    return compose_pipeline(
        [_opp("o1")], [],
        [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "01300-26", "number_origin": "minted"}],
        [rev], {}, [], {},
    )[0]


def test_a_cotizador_draft_is_not_missing_its_email_or_its_drive_file() -> None:
    """Nothing has gone to the client yet: there is no email to link and no PDF to file."""
    draft = _rev("r1", "q1", 1, SHA_A, src=None, origin="authored", status="draft", sent_at=None)
    card = _cotizador_card(draft)
    codes = [a["code"] for a in card["attention"]]
    assert "document_not_in_drive" not in codes and "no_gmail_evidence" not in codes


def test_a_sent_cotizador_revision_links_the_email_it_went_in_and_waits_for_the_archiver() -> None:
    """Rule R16 records the email a Cotizador PDF was sent in; the worker files the PDF from it."""
    sent = _rev("r1", "q1", 1, SHA_A, src=None, origin="authored", sent_gmail_message_id="gm-9",
                sent_gmail_thread_id="th-9", sent_subject="Cotización 01300-26", sent_has_eml=True)
    card = _cotizador_card(sent)
    [r] = card["quotes"][0]["revisions"]
    assert r["gmail"]["message_id"] == "gm-9" and r["gmail"]["thread_id"] == "th-9"
    assert r["drive_pending"] is True
    codes = [a["code"] for a in card["attention"]]
    assert "no_gmail_evidence" not in codes and "document_not_in_drive" not in codes
    assert "document_pending_drive" in codes and card["status"] == "ok"


def test_a_new_cotizador_revision_in_progress_does_not_block_the_case() -> None:
    """Approving a revision is what replaces the earlier one: until then the draft is the work in
    progress, not a second vigente revision (it used to block the case as «canonical_undetermined»)."""
    revs = [_rev("r1", "q1", 1, SHA_A),
            _rev("r2", "q1", 2, None, src=None, origin="authored", status="draft", sent_at=None)]
    card = _cotizador_card_with(revs)
    assert card["status"] != "blocked"
    assert "canonical_undetermined" not in [a["code"] for a in card["attention"]]
    assert [r["is_active"] for r in card["quotes"][0]["revisions"]] == [True, False]


def _cotizador_card_with(revs: list[dict], **opp: object) -> dict:
    return compose_pipeline(
        [_opp("o1", **opp)], [],
        [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "01300-26", "number_origin": "minted"}],
        revs, SOURCES, [], {},
    )[0]


def test_a_sent_cotizador_revision_shows_its_own_amounts_and_a_draft_none() -> None:
    sent = _rev("r1", "q1", 1, SHA_A, src=None, origin="authored", quote_currency="CLP",
                authored_net="1000000", authored_tax="190000", authored_gross="1190000")
    card = _cotizador_card_with([sent])
    assert card["money"][0]["currency"] == "CLP" and card["money"][0]["net_total"] == "1000000"
    c = card["quotes"][0]["revisions"][0]["contents"]
    assert c["net_source"] == "cotizador" and c["tax_total"] == "190000" and c["gross_total"] == "1190000"

    sin_iva = _rev("r1", "q1", 1, SHA_A, src=None, origin="authored", quote_currency="CLP",
                   authored_net="500", authored_tax="0", authored_gross="500")
    c = _cotizador_card_with([sin_iva])["quotes"][0]["revisions"][0]["contents"]
    assert c["tax_total"] is None and c["gross_total"] is None

    draft = _rev("r1", "q1", 1, None, src=None, origin="authored", status="draft", sent_at=None)
    assert _cotizador_card_with([draft])["money"] == []


def test_a_first_quote_still_in_the_cotizador_is_the_next_step_not_a_reply() -> None:
    contacts = {"o1": {"inbound": {"at": "2026-10-20T10:00:00Z"}, "outbound": None}}
    for status, verb in (("draft", "Terminar y enviar"), ("approved", "Enviar")):
        rev = _rev("r1", "q1", 1, None, src=None, origin="authored", status=status, sent_at=None)
        card = compose_pipeline(
            [_opp("o1")], [],
            [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "01300-26", "number_origin": "minted"}],
            [rev], {}, [], {}, contacts,
        )[0]
        assert card["next_action"]["text"] == f"{verb} la cotización 01300-26"


def test_a_voided_quote_is_not_the_next_step() -> None:
    """Owner 2026-10-27: a case whose quotes were all voided said «Terminar y enviar la cotización …» of
    a voided draft. The next step follows the newest revision still alive, else there is none."""
    quotes = [{"quote_id": q, "opportunity_id": "o1", "quote_number": n, "number_origin": "minted"}
              for q, n in (("q1", "01300-26"), ("q2", "01301-26"))]
    void = _rev("r2", "q2", 1, None, src=None, origin="authored", status="void", sent_at=None)
    draft = _rev("r1", "q1", 1, None, src=None, origin="authored", status="draft", sent_at=None)
    both_void = compose_pipeline([_opp("o1")], [], quotes, [void, {**draft, "status": "void"}], {}, [], {})[0]
    assert both_void["next_action"]["text"] == "Registrar la cotización del caso"
    one_alive = compose_pipeline([_opp("o1")], [], quotes, [void, draft], {}, [], {})[0]
    assert one_alive["next_action"]["text"] == "Terminar y enviar la cotización 01300-26"


def test_a_closed_case_carries_no_notes() -> None:
    """Decision 2026-10-09: a closed case never blocks. Owner 2026-10-12: nor does it show notes —
    «whatever, it's already closed»: a won, lost or abandoned case is «al día» with no attention."""
    for stage in ("won", "lost", "abandoned"):
        cards = compose_pipeline(
            [_opp("o1", stage=stage, closed_at="2026-06-01T00:00:00+00:00")],
            [],
            [{"quote_id": "q1", "opportunity_id": "o1", "quote_number": "00001-26", "number_origin": "printed_historical"}],
            [_rev("r1", "q1", 1, SHA_A), _rev("r2", "q1", 2, SHA_B)],
            SOURCES,
            [],
            {},
        )
        card = cards[0]
        assert card["status"] == "ok", stage
        assert card["attention"] == [], stage
        assert card["next_action"]["text"] == "Caso cerrado — sin acción", stage


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


def test_drive_archive_counts_what_the_cron_filed_not_only_the_boot_ledgers(tmp_path: Path) -> None:
    """A quote the Drive cron filed after boot has a `drive_file` record and no ledger line. The
    page lists its folder and does not report the revision as «sin PDF en Drive»; a revision with
    neither is reported."""
    ledgers = load_drive_ledgers([_ledger(tmp_path, "run-1", [_row(SHA_A, "f-a")])])
    record = {"drive_file_id": "f-b", "drive_folder_id": "folder-b", "case_key": "case-b",
              "quote_number": "00002-26", "revision": 1, "original_filename": "CN00002-Ejemplo.pdf"}
    rows = [
        {"sha": SHA_A, "pdf_sha256": SHA_A, "revision_no": 1, "quote_number": "00001-26",
         "opportunity_id": "o1", "opportunity_title": "Caso 1", "organization_name": "Institución Ejemplo",
         "drive_record": None},
        {"sha": SHA_B, "pdf_sha256": SHA_B, "revision_no": 1, "quote_number": "00002-26",
         "opportunity_id": "o2", "opportunity_title": "Caso 2", "organization_name": "Otra Institución",
         "drive_record": json.dumps(record)},
        {"sha": SHA_C, "pdf_sha256": SHA_C, "revision_no": 1, "quote_number": "00003-26",
         "opportunity_id": "o3", "opportunity_title": "Caso 3", "organization_name": None,
         "drive_record": None},
    ]
    out = drive_archive_from(rows, ledgers, configured=True)
    assert out["totals"] == {"folders": 2, "documents": 2, "in_crm": 2, "not_in_crm": 0}
    assert sorted(out["ledgers"]) == ["crm", "run-1"]
    filed = next(f for f in out["folders"] if f["folder_id"] == "folder-b")
    assert filed["organization_name"] == "Otra Institución"
    assert "/file/d/f-b/" in filed["documents"][0]["file_url"]
    assert [r["quote_number"] for r in out["crm_revisions_without_drive_file"]] == ["00003-26"]
    assert out["configured"] is True
    # No boot ledger mounted at all (a fresh API), only the cron's records: still an archive.
    cron_only = drive_archive_from(rows, {}, configured=False)
    assert cron_only["totals"]["folders"] == 1 and cron_only["configured"] is True
    assert drive_archive_from([], {}, configured=False)["configured"] is False


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
        "/v2/workspace/opportunities/{opportunity_id}/threads",
        "/v2/workspace/lookup",
        "/v2/workspace/opportunities/{opportunity_id}/messages/{message_id}",
        "/v2/workspace/quote-readings",
        "/v2/workspace/opportunities/{opportunity_id}/quote-candidates",
        "/v2/workspace/opportunities/{opportunity_id}/purchase-order-candidates",
        "/v2/workspace/mail-sync",
        "/v2/workspace/operations",
        "/v2/workspace/mail-quote-numbers",
        "/v2/workspace/history",
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
        "/v2/workspace/cases/{opportunity_id}/memory",
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


# ── quote contents on the card (the case panel shows money and lines) ─────────────────────────

def test_quote_contents_reads_the_documents_neto_and_adds_iva_for_clp() -> None:
    from origenlab_api.v2.crm_workspace import quote_contents

    lines = [
        {"line_no": 1, "item": "1", "kind": "equipment", "brand": "Marca", "model": "X-100", "description": "Equipo",
         "qty": "1.000000", "unit_price": "1000000.0000", "line_total": "1000000.0000", "optional": False,
         "check_status": "verified"},
        {"line_no": 2, "item": "2", "kind": "accessory", "brand": None, "model": None, "description": "Opcional",
         "qty": "1.000000", "unit_price": "50000.0000", "line_total": "50000.0000", "optional": True,
         "check_status": "single_source"},
    ]
    c = quote_contents({"doc_currency": "CLP", "doc_net_total": "1000000", "doc_lines": json.dumps(lines)})
    assert c["currency"] == "CLP" and c["net_source"] == "document"
    assert (c["net_total"], c["tax_total"], c["gross_total"]) == ("1000000", "190000", "1190000")
    assert [l["model"] for l in c["lines"]] == ["X-100", None] and c["lines"][1]["optional"] is True


def test_quote_contents_sums_non_optional_lines_without_a_printed_neto_and_never_adds_iva_to_usd() -> None:
    from origenlab_api.v2.crm_workspace import quote_contents

    lines = [{"line_no": 1, "line_total": "1200.5", "optional": False},
             {"line_no": 2, "line_total": "99.25", "optional": False},
             {"line_no": 3, "line_total": "500", "optional": True}]
    c = quote_contents({"doc_currency": "USD", "doc_net_total": None, "doc_lines": lines})
    assert (c["net_total"], c["net_source"], c["tax_total"], c["gross_total"]) == ("1299.75", "lines", None, None)
    # A line without a total makes the sum unknowable: no neto rather than a wrong one.
    c = quote_contents({"doc_currency": "CLP", "doc_net_total": None,
                        "doc_lines": [{"line_no": 1, "line_total": None, "optional": False}]})
    assert (c["net_total"], c["net_source"], c["tax_total"]) == (None, "none", None)


def test_quote_contents_says_when_the_printed_neto_includes_the_optional_lines() -> None:
    """Reader v2 (2026-10-16) flags such a document; the panel shows the neto as printed, with a note."""
    from origenlab_api.v2.crm_workspace import quote_contents

    lines = [{"line_no": 1, "line_total": "1000", "optional": False}, {"line_no": 2, "line_total": "500", "optional": True}]
    flagged = quote_contents({"doc_currency": "CLP", "doc_net_total": "1500", "doc_net_includes_optional": True,
                              "doc_lines": lines})
    assert flagged["net_includes_optional"] is True and flagged["net_total"] == "1500"
    plain = quote_contents({"doc_currency": "CLP", "doc_net_total": "1000", "doc_lines": lines})
    assert plain["net_includes_optional"] is False
    assert quote_contents({"doc_currency": "CLP", "doc_net_total": "1000", "doc_net_includes_optional": "true",
                           "doc_lines": lines})["net_includes_optional"] is False  # only a real boolean counts


def test_case_money_is_the_last_sent_vigente_quotation_not_a_sum() -> None:
    """Owner 2026-10-12: a case with several quotations shows the last one sent, not their sum —
    later quotations usually replace earlier ones for the same request."""
    from origenlab_api.v2.crm_workspace import case_money, quote_contents

    assert quote_contents({"doc_currency": None, "doc_net_total": None, "doc_lines": None}) is None
    revs = [
        {"status": "sent", "quote_number": "00001-26", "revision_no": 1, "sent_at": "2026-09-07 10:00:00+00", "is_active": True,
         "contents": {"currency": "CLP", "net_total": "1000"}},
        {"status": "sent", "quote_number": "00003-26", "revision_no": 1, "sent_at": "2026-09-22 15:30:00+00", "is_active": True,
         "contents": {"currency": "CLP", "net_total": "250"}},
        {"status": "sent", "quote_number": "00002-26", "revision_no": 2, "sent_at": "2026-09-30 09:00:00+00", "is_active": False,
         "contents": {"currency": "CLP", "net_total": "999999"}},  # superseded: never the case's amount
    ]
    assert case_money(revs) == [{"currency": "CLP", "net_total": "250", "quote_number": "00003-26",
                                 "revision_no": 1, "sent_at": "2026-09-22 15:30:00+00", "quote_count": 2}]


def test_case_money_says_unread_when_the_last_sent_quotation_was_never_read() -> None:
    from origenlab_api.v2.crm_workspace import case_money

    revs = [
        {"status": "sent", "quote_number": "00001-26", "revision_no": 1, "sent_at": "2026-09-07 10:00:00+00", "is_active": True,
         "contents": {"currency": "USD", "net_total": "10.50"}},
        {"status": "sent", "quote_number": "00004-26", "revision_no": 1, "sent_at": "2026-10-01 10:00:00+00", "is_active": True,
         "contents": None},
    ]
    assert case_money(revs) == [{"currency": "unread", "net_total": "1", "quote_number": "00004-26",
                                 "revision_no": 1, "sent_at": "2026-10-01 10:00:00+00", "quote_count": 2}]
    assert case_money([{"is_active": False, "contents": None}]) == []


def test_case_threads_group_by_cadena_label_the_party_and_hide_addresses() -> None:
    from origenlab_api.v2.crm_workspace import case_threads

    rows = [
        # A client cadena: OrigenLab sends the quote, the client answers later (other time zone).
        {"thread_id": "t1", "message_id": "m1", "sent_at": "2026-09-01T10:00:00-03:00", "subject": "Cotización balanza",
         "sender": "Equipo OrigenLab <contacto@origenlab.cl>", "recipients": "Ana Ficticia <ana@lab-ficticio.example>",
         "direction": "outbound", "documents": [{"filename": "CN00999.pdf"}]},
        {"thread_id": "t1", "message_id": "m2", "sent_at": "2026-09-01T16:00:00Z", "subject": "RE: Cotización balanza",
         "sender": "Ana Ficticia <ana@lab-ficticio.example>", "recipients": "contacto@origenlab.cl",
         "direction": None, "documents": "[]"},
        # A supplier cadena, newest of all.
        {"thread_id": "t2", "message_id": "m3", "sent_at": "2026-09-03T09:00:00-03:00", "subject": "Fwd: Precio equipo",
         "sender": "<ventas@fabricante-ficticio.example>", "recipients": "contacto@origenlab.cl", "direction": "inbound",
         "documents": []},
        # Only OrigenLab: an internal note to self.
        {"thread_id": "t3", "message_id": "m4", "sent_at": "2026-08-01T09:00:00-03:00", "subject": "nota",
         "sender": "contacto@origenlab.cl", "recipients": "contacto@origenlab.cl", "direction": "outbound", "documents": []},
        {"thread_id": None, "message_id": "m5", "sent_at": "2026-09-04T00:00:00Z", "subject": "sin hilo", "sender": "", "documents": []},
    ]
    threads = case_threads(rows, supplier_domains=["fabricante-ficticio.example"])
    assert [t["thread_id"] for t in threads] == ["t2", "t1", "t3"]
    assert [t["kind"] for t in threads] == ["proveedor", "cliente", "interno"]
    t1 = threads[1]
    assert t1["subject"] == "Cotización balanza" and t1["count"] == 2
    assert [m["message_id"] for m in t1["messages"]] == ["m2", "m1"]  # newest first
    assert t1["messages"][0]["direction"] == "inbound" and t1["messages"][0]["from"] == "Ana Ficticia"
    assert t1["messages"][1]["has_pdf"] is True and t1["url"].endswith("m2")
    assert threads[0]["messages"][0]["from"] == "fabricante-ficticio.example"  # no name: domain, never the address
    assert "@" not in json.dumps(threads)


def test_quote_readings_name_the_quote_the_reason_and_the_week_never_text() -> None:
    from origenlab_api.v2.crm_workspace import quote_reading_rows

    rows = [
        {"value_norm": "quote_read:v1:2026-W41", "code": "net_mismatch", "recorded_at": "2026-10-12T10:00:00+00:00",
         "quote_number": "00999-26", "revision_no": 1, "sent_at": "2026-10-08T12:00:00+00:00",
         "opportunity_id": "o-1", "case_title": "Caso ficticio"},
        {"value_norm": "quote_read:v1:2026-W41", "code": "something_new", "recorded_at": "2026-10-12T09:00:00+00:00",
         "quote_number": "00998-26", "revision_no": 2, "sent_at": None, "opportunity_id": "o-2", "case_title": None},
    ]
    out = quote_reading_rows(rows)
    assert out[0] == {"quote_number": "00999-26", "revision_no": 1, "sent_at": "2026-10-08T12:00:00+00:00",
                      "code": "net_mismatch", "reader_version": "quote_read:v1", "recorded_at": "2026-10-12T10:00:00+00:00",
                      "opportunity_id": "o-1", "case_title": "Caso ficticio"}
    assert out[1]["code"] == "other"  # an unknown code is never passed through as text


def test_a_card_carries_its_attached_documents_and_a_case_without_any_an_empty_list() -> None:
    """The purchase order attached to a case (crm.opportunity_document) shows on its card."""
    from origenlab_api.v2.crm_workspace import compose_pipeline

    docs = [{"document_id": "d1", "opportunity_id": "o1", "quote_revision_id": "r1", "kind": "purchase_order",
             "document_number": "4500", "original_filename": "OC 4500.pdf", "content_type": "application/pdf",
             "byte_size": 1024, "source": "upload", "attached_at": "2026-10-12 12:00:00+00",
             "attached_by": "Operadora Ficticia"}]
    with_doc, without = compose_pipeline([_opp("o1"), _opp("o2")], [], [], [], {}, [], {}, documents=docs)
    assert with_doc["documents"] == [{k: v for k, v in docs[0].items() if k != "opportunity_id"}]
    assert without["documents"] == []


_WEB_FORM_ROW = {"opportunity_id": "o1", "sent_at": "2026-10-19T10:00:00-03:00",
                 "subject": "Solicitud de cotización — sitio web — Universidad Ficticia", "gmail_message_id": "mw",
                 "sender": "OrigenLab <contacto@origenlab.cl>", "direction_hint": None, "comms_direction": "outbound",
                 "web_form_reply_to": "ana@lab-ficticio.example.test"}


def test_a_web_form_submission_is_the_client_writing_on_the_case() -> None:
    """Hoy reads `last_contact.inbound`: the form is the visitor writing, not OrigenLab sending,
    and «OrigenLab» (the From's name) is never shown as the client's name."""
    out = last_contacts([_WEB_FORM_ROW])
    assert out["o1"]["outbound"] is None
    assert out["o1"]["inbound"]["at"] == "2026-10-19T10:00:00-03:00"
    assert "sender_name" not in out["o1"]["inbound"]

    card = compose_pipeline([_opp("o1", stage="lead", organization_id=None, organization_name=None)],
                            [], [], [], {}, [], {}, out)[0]
    assert card["contact"]["source"] == "web_form"
    assert card["contact"]["address"] == "ana@lab-ficticio.example.test"
    assert card["contact"]["address_ref"] == address_ref("ana@lab-ficticio.example.test")
    assert set(card["last_contact"]["inbound"]) == {"at", "subject", "url"}  # the address stays off it


def test_a_web_form_submission_is_listed_as_received_from_the_visitor() -> None:
    from origenlab_api.v2.crm_workspace import case_threads

    row = {"thread_id": "tw", "message_id": "mw", "sent_at": "2026-10-19T10:00:00-03:00",
           "subject": _WEB_FORM_ROW["subject"], "sender": "OrigenLab <contacto@origenlab.cl>",
           "recipients": "contacto@origenlab.cl", "direction": "outbound",
           "web_form_reply_to": "ana@lab-ficticio.example.test", "documents": []}
    [thread] = case_threads([row])
    assert thread["kind"] == "cliente"
    assert thread["messages"][0]["direction"] == "inbound"
    assert thread["messages"][0]["from"] == "lab-ficticio.example.test"  # the domain, never the address
    # The same self-sent mail without the marker is OrigenLab's own: «Interno».
    [plain] = case_threads([{**row, "web_form_reply_to": None}])
    assert plain["kind"] == "interno" and plain["messages"][0]["direction"] == "outbound"


def test_the_visitor_stays_the_contact_when_they_later_reply_by_email() -> None:
    """Their own reply is the newest inbound; the card keeps the form's visitor as its contact."""
    reply = {**_WEB_FORM_ROW, "sent_at": "2026-10-20T10:00:00-03:00", "subject": "Re: solicitud",
             "gmail_message_id": "mr", "sender": "Ana Ficticia <ana@lab-ficticio.example.test>",
             "comms_direction": "inbound", "web_form_reply_to": None}
    out = last_contacts([reply, _WEB_FORM_ROW])
    assert out["o1"]["inbound"]["at"] == "2026-10-20T10:00:00-03:00"
    assert out["o1"]["inbound"]["sender_name"] == "Ana Ficticia"
    card = compose_pipeline([_opp("o1", stage="lead", organization_id=None, organization_name=None)],
                            [], [], [], {}, [], {}, out)[0]
    assert (card["contact"]["source"], card["contact"]["address"]) == ("web_form", "ana@lab-ficticio.example.test")
    assert set(card["last_contact"]["inbound"]) == {"at", "subject", "url", "sender_name"}


# ── «Asignar institución»: what the AI read in the client's email (owner 2026-10-23) ─────────

_CLIENT_ROW = {"opportunity_id": "o9", "sent_at": "2026-10-23T09:30:00-03:00", "subject": "Solicitud de cotización",
               "gmail_message_id": "mc", "sender": "Bárbara Ficticia <bficticia@centro-ficticio.example.test>",
               "comms_direction": "inbound", "web_form_reply_to": None,
               "requester_read": "Centro Ficticio de Análisis de Laboratorio"}


def _card(rows, **opp):
    out = last_contacts(rows)
    base = {"stage": "lead", "organization_id": None, "organization_name": None, **opp}
    return compose_pipeline([_opp("o9", **base)], [], [], [], {}, [], {}, out)[0]


def test_a_case_without_institution_suggests_what_the_ai_read_and_the_sender_domain() -> None:
    card = _card([_CLIENT_ROW])
    assert card["requester_suggestion"] == {"name": "Centro Ficticio de Análisis de Laboratorio",
                                            "domain": "centro-ficticio.example.test"}
    assert set(card["last_contact"]["inbound"]) <= {"at", "subject", "url", "sender_name"}


def test_a_free_mail_sender_suggests_the_name_only() -> None:
    card = _card([{**_CLIENT_ROW, "sender": "Ana <ana.ficticia@gmail.com>"}])
    assert card["requester_suggestion"] == {"name": "Centro Ficticio de Análisis de Laboratorio", "domain": None}


def test_no_reading_still_suggests_the_institutional_domain() -> None:
    card = _card([{**_CLIENT_ROW, "requester_read": None}])
    assert card["requester_suggestion"] == {"name": None, "domain": "centro-ficticio.example.test"}


def test_nothing_to_suggest_from_free_mail_without_a_reading() -> None:
    card = _card([{**_CLIENT_ROW, "requester_read": None, "sender": "ana.ficticia@gmail.com"}])
    assert card["requester_suggestion"] is None


def test_a_case_with_an_institution_suggests_nothing() -> None:
    card = _card([_CLIENT_ROW], organization_id="org1", organization_name="Instituto Ficticio")
    assert card["requester_suggestion"] is None


def test_our_own_mail_never_suggests() -> None:
    ours = {**_CLIENT_ROW, "sender": "Tatiana <contacto@origenlab.cl>", "comms_direction": "outbound"}
    assert _card([ours])["requester_suggestion"] is None


def test_the_web_form_visitor_domain_is_suggested() -> None:
    card = _card([{**_WEB_FORM_ROW, "opportunity_id": "o9", "requester_read": None}])
    assert card["requester_suggestion"] == {"name": None, "domain": "lab-ficticio.example.test"}


def _mail_row(gid: str, at: str, sender: str, **over) -> dict:
    return {"opportunity_id": "o1", "sent_at": at, "subject": "Re: Cotización", "gmail_message_id": gid,
            "sender": sender, "direction_hint": None, "comms_direction": "inbound", **over}


def test_last_contacts_skips_automatic_mail() -> None:
    """A bounce of our cc or an out-of-office is not the client writing (owner 2026-10-29)."""
    rows = [
        _mail_row("m1", "2026-10-23T12:00:00Z", "Persona <persona@ejemplo.invalid>"),
        _mail_row("m2", "2026-10-26T15:02:00Z", "Mail Delivery System <Mailer-Daemon@correo.invalid>"),
        _mail_row("m3", "2026-10-26T16:00:00Z", "Persona <persona@ejemplo.invalid>", triage_class="auto_reply"),
        _mail_row("m4", "2026-10-26T17:00:00Z", "postmaster@correo.invalid", triage_class="bounce"),
    ]
    assert last_contacts(rows)["o1"]["inbound"]["at"] == "2026-10-23T12:00:00+00:00"


def test_no_reply_only_marks_the_email_r18_read() -> None:
    closing = _mail_row("m1", "2026-10-23T12:00:00Z", "Persona <persona@ejemplo.invalid>", no_reply=True)
    assert last_contacts([closing])["o1"]["inbound"]["no_reply"] is True
    later = _mail_row("m2", "2026-10-24T12:00:00Z", "Persona <persona@ejemplo.invalid>", no_reply=False)
    assert "no_reply" not in last_contacts([closing, later])["o1"]["inbound"]
