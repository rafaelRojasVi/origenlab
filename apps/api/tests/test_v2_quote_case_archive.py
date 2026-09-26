"""Case-first Drive archive: folder reuse, revisions, duplicates, verification, retries, guards."""

from __future__ import annotations

import hashlib
import itertools
from typing import Any

import pytest

from origenlab_api.v2 import quote_case_archive as qa

PRINCIPAL = "contacto@origenlab.cl"
COT, CASOS, PEND, ENV = "cotizaciones", "casos", "pendientes", "enviadas"


def pdf(tag: str) -> bytes:
    return b"%PDF-1.7\n" + tag.encode() + b"\n%%EOF"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeDrive:
    """In-memory Drive. `index_lag` hides newly created items from search; `fail_*` inject faults."""

    def __init__(self, *, principal: str = PRINCIPAL) -> None:
        self.who = principal
        self.items: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.ids = (f"id{n:04d}" for n in itertools.count(1))
        self.writes: list[str] = []
        self.index_lag = False
        self.hidden: set[str] = set()
        self.fail_upload_after_create = False
        self.fail_next_download = False
        self.corrupt_uploads = False
        self.wrong_reported_sha = False
        self.add(COT, "Cotizaciones", None)
        self.add(PEND, "Pendientes", COT)
        self.add(ENV, "Enviadas", COT)
        self.add(CASOS, "Casos", COT, {qa.PROP_KIND: qa.KIND_CASE_ROOT})

    def add(self, fid: str, name: str, parent: str | None, props: dict | None = None, *, mime: str = qa.FOLDER_MIME,
            data: bytes | None = None) -> dict:
        item = {"id": fid, "name": name, "mimeType": mime, "parents": [parent] if parent else [], "trashed": False,
                "appProperties": dict(props or {}), "webViewLink": f"https://drive.example/{fid}"}
        if data is not None:
            self.blobs[fid] = data
            item |= {"size": str(len(data)), "sha256Checksum": sha(data)}
        self.items[fid] = item
        return item

    # DrivePort
    def principal(self) -> str:
        return self.who

    def get(self, file_id: str) -> dict | None:
        item = self.items.get(file_id)
        return dict(item) if item else None

    def find_by_property(self, key: str, value: str) -> list[dict]:
        return [dict(i) for i in self.items.values()
                if not i["trashed"] and i["appProperties"].get(key) == value and i["id"] not in self.hidden]

    def children(self, folder_id: str) -> list[dict]:
        return [dict(i) for i in self.items.values() if folder_id in i["parents"] and not i["trashed"]]

    def create_folder(self, *, name: str, parent_id: str, app_properties) -> dict:
        fid = next(self.ids)
        self.writes.append(f"folder:{name}")
        item = self.add(fid, name, parent_id, dict(app_properties))
        if self.index_lag:
            self.hidden.add(fid)
        return dict(item)

    def upload_pdf(self, *, name: str, parent_id: str, app_properties, data: bytes) -> dict:
        fid = next(self.ids)
        self.writes.append(f"upload:{name}")
        stored = data + b"x" if self.corrupt_uploads else data
        item = self.add(fid, name, parent_id, dict(app_properties), mime=qa.PDF_MIME, data=stored)
        if self.wrong_reported_sha:
            item["sha256Checksum"] = "0" * 64
        if self.index_lag:
            self.hidden.add(fid)
        if self.fail_upload_after_create:
            self.fail_upload_after_create = False
            raise ConnectionError("connection dropped after Drive stored the file")
        return {"id": fid}

    def download(self, file_id: str) -> bytes:
        if self.fail_next_download:
            self.fail_next_download = False
            raise TimeoutError("download timed out")
        return self.blobs[file_id]


def doc(tag: str, quote: str = "01244-26", rev: int = 1, **kw) -> qa.ArchiveDocument:
    data = pdf(tag)
    return qa.ArchiveDocument(document_sha256=sha(data), quote_number=quote, revision=rev,
                              original_filename=f"CN{quote.split('-')[0]}-{tag}.pdf", data=data, **kw)


def case(key: str = "8ddf4bf9-ba11-5a03-8d1a-41977a074cd1", *docs: qa.ArchiveDocument,
         opening: str = "01244-26", addressee: str = "Sylvia Saavedra - Gemco") -> qa.ArchiveCase:
    return qa.ArchiveCase(case_key=key, opening_quote_number=opening, printed_addressee=addressee,
                          documents=tuple(docs) or (doc("gemco"),))


TARGET = qa.ArchiveTarget(principal=PRINCIPAL, casos_folder_id=CASOS, protected_folder_ids=frozenset({PEND, ENV}))


# ── Names ────────────────────────────────────────────────────────────────────


def test_names_match_the_2026_09_26_upload() -> None:
    assert qa.case_folder_name("01244-26", "Sylvia Saavedra - Gemco") == "Caso 01244 — Sylvia Saavedra - Gemco"
    assert qa.revision_file_name("01244-26", 1, "CN01244-Sylvia Saavedra - Gemco.pdf") == \
        "01244-26 r1 — CN01244-Sylvia Saavedra - Gemco.pdf"
    assert qa.case_folder_name("01243-26", None) == "Caso 01243 — sin destinatario impreso"
    assert qa.opening_serial("011453AI") == "011453AI"


@pytest.mark.parametrize("bad", ["nul\x00here", "del\x7fhere"])
def test_control_characters_are_refused(bad: str) -> None:
    with pytest.raises(qa.NameRefused):
        qa.case_folder_name("01001-26", bad)


def test_a_slash_in_a_printed_addressee_becomes_a_division_slash() -> None:
    name = qa.case_folder_name("01080-26", "Patricia Castaño - UDT / Universidad de Concepción")
    assert "/" not in name and "UDT \u2215 Universidad" in name


def test_names_never_carry_status_or_confirmed_organization() -> None:
    c = case()
    assert c.folder_name == "Caso 01244 — Sylvia Saavedra - Gemco"
    for status in qa.CrmStatus:
        assert status.value not in c.folder_name
    assert set(c.folder_properties()) == {qa.PROP_KIND, qa.PROP_CASE_KEY, qa.PROP_OPENING_QUOTE}


# ── Case-folder creation, reuse, several quotations, revisions ───────────────


def test_first_run_creates_one_folder_and_one_verified_file() -> None:
    d = FakeDrive()
    r = qa.archive_case(d, case(), TARGET)
    assert r.folder_outcome == "created" and len(d.writes) == 2
    assert r.all_verified and r.files[0].outcome == "uploaded"
    folder = d.items[r.folder_id]
    assert folder["parents"] == [CASOS] and folder["appProperties"][qa.PROP_CASE_KEY] == case().case_key


def test_case_folder_is_reused_for_a_later_quotation() -> None:
    d = FakeDrive()
    first = qa.archive_case(d, case(), TARGET)
    later = qa.archive_case(d, case(case().case_key, doc("gemco"), doc("gemco-2", quote="01250-26")), TARGET)
    assert later.folder_outcome == "reused" and later.folder_id == first.folder_id
    assert [f.outcome for f in later.files] == ["reused", "uploaded"]
    assert len([i for i in d.items.values() if i["mimeType"] == qa.FOLDER_MIME and qa.PROP_CASE_KEY in i["appProperties"]]) == 1


def test_multiple_quotations_share_one_case_folder() -> None:
    d = FakeDrive()
    docs = (doc("umayor-24", "01024-26"), doc("umayor-26", "01026-26"), doc("umayor-27", "01027-26"))
    r = qa.archive_case(d, case("03b94122", *docs, opening="01024-26", addressee="Claudia Sanhueza - Universidad Mayor"), TARGET)
    assert {d.items[f.file_id]["parents"][0] for f in r.files} == {r.folder_id}
    assert len(r.files) == 3 and r.all_verified


def test_revisions_are_new_files_never_overwrites() -> None:
    d = FakeDrive()
    r1 = qa.archive_case(d, case("k1", doc("v1", rev=1)), TARGET)
    r2 = qa.archive_case(d, case("k1", doc("v1", rev=1), doc("v2", rev=2)), TARGET)
    names = sorted(i["name"] for i in d.children(r1.folder_id))
    assert names == ["01244-26 r1 — CN01244-v1.pdf", "01244-26 r2 — CN01244-v2.pdf"]
    assert d.blobs[r1.files[0].file_id] == pdf("v1") and r2.files[0].outcome == "reused"


def test_two_revision_documents_cannot_claim_the_same_slot() -> None:
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(FakeDrive(), case("k", doc("a", rev=1), doc("b", rev=1)), TARGET)
    assert e.value.code == "duplicate_revision_slot"


# ── Duplicate quote numbers and duplicate hashes ─────────────────────────────


def test_same_printed_number_for_two_clients_stays_two_cases() -> None:
    d = FakeDrive()
    a = qa.archive_case(d, case("sag", doc("sag", "01242-26"), opening="01242-26", addressee="SAG"), TARGET)
    b = qa.archive_case(d, case("condecal", doc("condecal", "01242-26"), opening="01242-26", addressee="Condecal"), TARGET)
    assert a.folder_id != b.folder_id


def test_same_number_and_same_addressee_but_different_case_key_is_refused_by_name() -> None:
    d = FakeDrive()
    qa.archive_case(d, case("k1", doc("a", "01230-26"), opening="01230-26", addressee="UACh"), TARGET)
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case("k2", doc("b", "01230-26"), opening="01230-26", addressee="UACh"), TARGET)
    assert e.value.code == "case_name_taken" and not e.value.partial.writes


def test_duplicate_pdf_in_one_request_is_refused_before_any_call() -> None:
    d = FakeDrive()
    x = doc("same")
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case("k", x, qa.ArchiveDocument(**{**x.__dict__, "revision": 2})), TARGET)
    assert e.value.code == "duplicate_document_in_request" and d.writes == []


def test_pdf_already_filed_under_another_case_is_refused() -> None:
    d = FakeDrive()
    qa.archive_case(d, case("k1", doc("shared")), TARGET)
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case("k2", doc("shared"), addressee="Otro cliente"), TARGET)
    assert e.value.code == "document_filed_elsewhere"
    assert e.value.partial.writes == ["create_folder:Caso 01244 — Otro cliente"]


def test_two_tagged_files_with_the_same_hash_in_one_case_are_refused() -> None:
    d = FakeDrive()
    r = qa.archive_case(d, case("k"), TARGET)
    src = d.items[r.files[0].file_id]
    d.add("dup", "copy.pdf", r.folder_id, src["appProperties"], mime=qa.PDF_MIME, data=d.blobs[src["id"]])
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case("k"), TARGET)
    assert e.value.code == "document_duplicate_in_case"


def test_two_folders_with_one_case_key_are_refused() -> None:
    d = FakeDrive()
    d.add("f1", "Caso A", CASOS, {qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: "k"})
    d.add("f2", "Caso B", CASOS, {qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: "k"})
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case("k"), TARGET)
    assert e.value.code == "case_key_conflict" and d.writes == []


# ── Repeated runs, partial failure, retry ────────────────────────────────────


def test_repeated_run_writes_nothing_and_reverifies() -> None:
    d = FakeDrive()
    qa.archive_case(d, case(), TARGET)
    before = list(d.writes)
    again = qa.archive_case(d, case(), TARGET)
    assert d.writes == before and again.writes == []
    assert again.folder_outcome == "reused" and again.all_verified


def test_upload_lost_after_drive_stored_it_is_found_on_retry() -> None:
    d = FakeDrive()
    d.fail_upload_after_create = True
    with pytest.raises(ConnectionError):
        qa.archive_case(d, case(), TARGET)
    retry = qa.archive_case(d, case(), TARGET)
    assert retry.files[0].outcome == "reused" and retry.all_verified
    assert len([i for i in d.items.values() if i["mimeType"] == qa.PDF_MIME]) == 1


def test_interrupted_verification_is_completed_on_retry() -> None:
    d = FakeDrive()
    d.fail_next_download = True
    with pytest.raises(TimeoutError):
        qa.archive_case(d, case(), TARGET)
    retry = qa.archive_case(d, case(), TARGET)
    assert retry.writes == [] and retry.all_verified


def test_search_index_lag_is_covered_by_the_journal() -> None:
    d = FakeDrive()
    d.index_lag = True
    journal: dict[str, str] = {}
    d.fail_next_download = True
    with pytest.raises(TimeoutError):
        qa.archive_case(d, case(), TARGET, journal=journal)
    assert set(journal) == {f"case:{case().case_key}", f"doc:{case().documents[0].document_sha256}"}
    retry = qa.archive_case(d, case(), TARGET, journal=journal)
    assert retry.writes == [] and retry.all_verified
    assert len(d.writes) == 2


def test_without_the_journal_index_lag_would_duplicate_the_folder_name_and_is_refused() -> None:
    d = FakeDrive()
    d.index_lag = True
    d.fail_next_download = True
    with pytest.raises(TimeoutError):
        qa.archive_case(d, case(), TARGET)
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case(), TARGET)
    assert e.value.code == "case_name_taken"


# ── Guards: legacy folders, account, hashes ──────────────────────────────────


def test_legacy_folders_are_never_a_write_target() -> None:
    d = FakeDrive()
    for bad_casos in (PEND, ENV):
        target = qa.ArchiveTarget(PRINCIPAL, bad_casos, frozenset({PEND, ENV}))
        with pytest.raises(qa.ArchiveRefused) as e:
            qa.archive_case(d, case(), target)
        assert e.value.code == "protected_parent"
    assert d.writes == []


def test_a_case_folder_found_inside_a_legacy_folder_is_not_reused() -> None:
    d = FakeDrive()
    d.add("old", "Caso 01244 — Sylvia Saavedra - Gemco", ENV, {qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: case().case_key})
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case(), TARGET)
    assert e.value.code == "case_folder_elsewhere" and d.writes == []


def test_guard_refuses_a_write_under_a_folder_parented_by_legacy() -> None:
    d = FakeDrive()
    d.add("nested", "Casos", ENV, {qa.PROP_KIND: qa.KIND_CASE_ROOT})
    target = qa.ArchiveTarget(PRINCIPAL, "nested", frozenset({PEND, ENV}))
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case(), target)
    assert e.value.code == "protected_parent" and d.writes == []


def test_untagged_case_root_is_refused() -> None:
    d = FakeDrive()
    d.items[CASOS]["appProperties"] = {}
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case(), TARGET)
    assert e.value.code == "casos_invalid"


def test_wrong_account_is_refused_before_any_write() -> None:
    d = FakeDrive(principal="otra.cuenta@ejemplo.invalid")
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case(), TARGET)
    assert e.value.code == "wrong_account" and d.writes == []


def test_local_bytes_that_do_not_hash_to_the_declared_sha_are_refused() -> None:
    good = doc("a")
    bad = qa.ArchiveDocument(**{**good.__dict__, "data": pdf("tampered")})
    d = FakeDrive()
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case("k", bad), TARGET)
    assert e.value.code == "local_hash_mismatch" and d.writes == []


def test_non_pdf_bytes_are_refused() -> None:
    data = b"PK\x03\x04 spreadsheet"
    d = qa.ArchiveDocument(sha(data), "01185-26", 1, "CN1185.xlsx", data)
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(FakeDrive(), case("k", d), TARGET)
    assert e.value.code == "not_pdf"


def test_drive_storing_other_bytes_stops_the_run_and_leaves_the_file() -> None:
    d = FakeDrive()
    d.corrupt_uploads = True
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case("k", doc("a"), doc("b", rev=2)), TARGET)
    part = e.value.partial
    assert e.value.code == "hash_mismatch" and len(part.files) == 1
    assert part.archive_status(doc("a").document_sha256) is qa.ArchiveStatus.HASH_MISMATCH
    assert part.files[0].file_id in d.items  # never deleted
    assert not any(w.startswith("upload:01244-26 r2") for w in d.writes)


def test_drive_reported_checksum_mismatch_is_a_hash_mismatch() -> None:
    d = FakeDrive()
    d.wrong_reported_sha = True
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, case(), TARGET)
    assert e.value.code == "hash_mismatch"
    assert e.value.partial.files[0].downloaded_sha256_matches is True
    assert e.value.partial.files[0].drive_sha256_matches is False


# ── Lifecycle: archive status never becomes CRM status ───────────────────────


@pytest.mark.parametrize(("role", "crm", "rev", "want"), [
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.READY_TO_IMPORT, 1, qa.Lifecycle.HISTORICAL_SENT),
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.READY_TO_IMPORT, 2, qa.Lifecycle.REVISION),
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.PENDING_ORGANIZATION, 1, qa.Lifecycle.PENDING_ORGANIZATION),
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.HELD, 1, qa.Lifecycle.HELD),
    (qa.DocumentRole.PENDING_DOCUMENT, qa.CrmStatus.HELD, 1, qa.Lifecycle.HELD),
    (qa.DocumentRole.BLOCKED_DOCUMENT, qa.CrmStatus.READY_TO_IMPORT, 1, qa.Lifecycle.HELD),
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.OPEN_IN_CRM, 1, qa.Lifecycle.CONFIRMED_OPPORTUNITY),
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.CLOSED_WON, 1, qa.Lifecycle.CLOSED_WON),
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.CLOSED_LOST, 3, qa.Lifecycle.CLOSED_LOST),
    (qa.DocumentRole.REJECTED_NON_QUOTATION, qa.CrmStatus.READY_TO_IMPORT, 1, qa.Lifecycle.REJECTED_NON_QUOTATION),
    (qa.DocumentRole.UNREVIEWED, qa.CrmStatus.NOT_IN_PLAN, 1, qa.Lifecycle.UNKNOWN_NEEDS_REVIEW),
    (qa.DocumentRole.UNKNOWN, qa.CrmStatus.NOT_IN_PLAN, 1, qa.Lifecycle.UNKNOWN_NEEDS_REVIEW),
    (qa.DocumentRole.QUOTATION, qa.CrmStatus.NOT_IN_PLAN, 1, qa.Lifecycle.UNKNOWN_NEEDS_REVIEW),
])
def test_lifecycle_precedence(role, crm, rev, want) -> None:
    assert qa.document_lifecycle(role, crm, revision=rev) is want
    assert want in qa.LIFECYCLE_LABEL_ES


def test_a_verified_archive_of_a_pending_case_stays_pending() -> None:
    d = FakeDrive()
    c = case("069babde", doc("corteva", "01246-26"), opening="01246-26", addressee="Carla Peñailillo – Corteva")
    r = qa.archive_case(d, c, TARGET)
    [row] = qa.archive_link_rows(c, r, crm_status=qa.CrmStatus.PENDING_ORGANIZATION)
    assert row["archive_status"] == "archived_verified"
    assert row["crm_status"] == "pending_organization_confirmation"
    assert row["lifecycle"] == "pending_organization_confirmation"


def test_held_document_is_archived_but_reported_held() -> None:
    d = FakeDrive()
    c = case("daddb31b", doc("alvarez", "01243-26"), opening="01243-26", addressee="Pablo Álvarez")
    r = qa.archive_case(d, c, TARGET)
    [row] = qa.archive_link_rows(c, r, crm_status=qa.CrmStatus.HELD,
                                 roles={c.documents[0].document_sha256: qa.DocumentRole.PENDING_DOCUMENT})
    assert row["archive_status"] == "archived_verified" and row["lifecycle"] == "held"


def test_crm_status_from_plan() -> None:
    assert qa.crm_status_from_plan("ready") is qa.CrmStatus.READY_TO_IMPORT
    assert qa.crm_status_from_plan("needs_organization_confirmation") is qa.CrmStatus.PENDING_ORGANIZATION
    assert qa.crm_status_from_plan("held") is qa.CrmStatus.HELD
    assert qa.crm_status_from_plan(None) is qa.CrmStatus.NOT_IN_PLAN


# ── CRM ↔ Drive linkage intents ──────────────────────────────────────────────


def test_link_rows_carry_every_identifier() -> None:
    d = FakeDrive()
    c = case("k", doc("a", gmail_message_id="1a0d93b243b20f00"))
    r = qa.archive_case(d, c, TARGET)
    [row] = qa.archive_link_rows(c, r, crm_status=qa.CrmStatus.READY_TO_IMPORT)
    assert row["drive_folder_id"] == r.folder_id and row["drive_file_id"] == r.files[0].file_id
    assert row["gmail_message_id"] == "1a0d93b243b20f00" and row["quote_number"] == "01244-26"
    assert row["revision"] == 1 and row["document_sha256"] == c.documents[0].document_sha256
    assert d.items[r.files[0].file_id]["appProperties"][qa.PROP_GMAIL_MESSAGE] == "1a0d93b243b20f00"


def test_intents_for_a_case_in_the_crm() -> None:
    d = FakeDrive()
    c = case("k", doc("a"), doc("b", rev=2))
    r = qa.archive_case(d, c, TARGET)
    rows = qa.archive_link_rows(c, r, crm_status=qa.CrmStatus.OPEN_IN_CRM)
    rev_ids = {c.documents[0].document_sha256: "rev-1"}
    intents = qa.crm_link_intents(rows, crm_opportunity_id="opp-1", quote_revision_ids=rev_ids)
    assert [i["intent"] for i in intents] == ["external_identifier", "drive_file_evidence", "drive_file_evidence"]
    assert intents[0]["scheme"] == "drive_folder" and intents[0]["value_norm"] == r.folder_id
    assert intents[1]["assertion"]["resolution"] == "linked" and intents[1]["assertion"]["resolved_id"] == "rev-1"
    assert intents[2]["assertion"]["resolution"] == "unresolved"
    assert intents[1]["source_record"]["dedupe_key"] == f"drive_file:{r.files[0].file_id}"
    assert all(i.get("effect") == "archive_link_only" for i in intents)
    assert len({i["idempotency_key"] for i in intents}) == len(intents)
    assert qa.crm_link_intents(rows, crm_opportunity_id="opp-1", quote_revision_ids=rev_ids) == intents


def test_intents_are_deferred_while_the_opportunity_is_not_in_the_crm() -> None:
    d = FakeDrive()
    c = case()
    rows = qa.archive_link_rows(c, qa.archive_case(d, c, TARGET), crm_status=qa.CrmStatus.PENDING_ORGANIZATION)
    assert qa.crm_link_intents(rows, crm_opportunity_id=None) == [
        {"intent": "deferred", "case_key": c.case_key, "reason": "opportunity_not_in_crm",
         "idempotency_key": f"qdrive:deferred:{c.case_key}"}]


def test_unverified_files_are_never_linked() -> None:
    d = FakeDrive()
    d.corrupt_uploads = True
    c = case()
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(d, c, TARGET)
    rows = qa.archive_link_rows(c, e.value.partial, crm_status=qa.CrmStatus.OPEN_IN_CRM)
    intents = qa.crm_link_intents(rows, crm_opportunity_id="opp-1")
    assert [i["intent"] for i in intents] == ["external_identifier", "skipped"]


def test_run_id_marks_only_created_items_and_reuse_still_verifies() -> None:
    d = FakeDrive()
    first = qa.archive_case(d, case(), TARGET, run_id="run-1")
    stamped = {i["id"] for i in d.items.values() if i["appProperties"].get(qa.PROP_RUN) == "run-1"}
    assert stamped == {first.folder_id, first.files[0].file_id}
    again = qa.archive_case(d, case(case().case_key, doc("gemco"), doc("gemco-r2", rev=2)), TARGET, run_id="run-2")
    assert again.all_verified and again.files[0].outcome == "reused"
    assert {i["id"] for i in d.items.values() if i["appProperties"].get(qa.PROP_RUN) == "run-2"} == {again.files[1].file_id}


# ── Check mode: OverlayDrive ─────────────────────────────────────────────────


def test_overlay_check_writes_nothing_and_predicts_the_real_run() -> None:
    real = FakeDrive()
    overlay = qa.OverlayDrive(real)
    predicted = qa.archive_case(overlay, case("k", doc("a"), doc("b", rev=2)), TARGET)
    assert real.writes == [] and len(overlay.would_write) == 3 and predicted.all_verified
    actual = qa.archive_case(real, case("k", doc("a"), doc("b", rev=2)), TARGET)
    assert [w.split(":", 1)[0] for w in overlay.would_write] == [w.split(":", 1)[0] for w in actual.writes]


def test_overlay_sees_existing_real_items_and_predicts_zero_writes() -> None:
    real = FakeDrive()
    qa.archive_case(real, case(), TARGET)
    overlay = qa.OverlayDrive(real)
    again = qa.archive_case(overlay, case(), TARGET)
    assert overlay.would_write == [] and again.folder_outcome == "reused" and again.all_verified


def test_overlay_still_enforces_every_guard() -> None:
    overlay = qa.OverlayDrive(FakeDrive(principal="someone@else.cl"))
    with pytest.raises(qa.ArchiveRefused) as e:
        qa.archive_case(overlay, case(), TARGET)
    assert e.value.code == "wrong_account" and overlay.would_write == []
