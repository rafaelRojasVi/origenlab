"""Filing recorded quote PDFs in Drive: the pure parts, the Drive client, the CLI, and the whole
run against a disposable database as the real `origenlab_worker` login with an in-memory Drive.
Every name, address and number is invented; the repository is public."""

from __future__ import annotations

import hashlib
import json
import uuid
from email.message import EmailMessage
from typing import Any

import psycopg
import pytest

from dbhelp import business_rows, owner_rows, seed_mailbox
from mailfixtures import INTERNAL_MS, MAILBOX
from origenlab_api.v2 import quote_case_archive as qa
from origenlab_worker import cli
from origenlab_worker.capture import build_capture
from origenlab_worker.database import local_test_target, open_worker_db
from origenlab_worker.drive_client import DRIVE_SCOPE, DriveAuthError, DriveCredentials, DriveRest
from origenlab_worker.drive_filing import (
    DriveFiler,
    DriveTarget,
    attachment_bytes,
    import_ledger,
    ledger_records,
    storage_key,
)
from test_v2_quote_case_archive import CASOS, ENV, PEND, PRINCIPAL, FakeDrive
from v2_command_harness import build_disposable_database, needs_worker_db, runtime_dsn, worker_dsn

TARGET = DriveTarget(principal=PRINCIPAL, casos_folder_id=CASOS, protected_folder_ids=frozenset({PEND, ENV}))


def _pdf(tag: str) -> bytes:
    return f"%PDF-1.4\n% {tag} — invented for a test\n".encode()


def _raw(pdf: bytes, name: str = "CN01244-26 Cliente.pdf") -> bytes:
    msg = EmailMessage()
    msg["From"] = MAILBOX
    msg["To"] = "Compras <compras@cliente.invalid>"
    msg["Subject"] = "Cotización"
    msg["Date"] = "Mon, 12 Oct 2026 10:00:00 -0300"
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@origenlab.invalid>"
    msg.set_content("Adjunto la cotización.\n")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=name)
    msg.add_attachment(b"\x89PNG logo", maintype="image", subtype="png", filename="logo.png")
    return msg.as_bytes()


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get(self, key: str) -> bytes:
        return self.objects[key]


# ─────────────────────────────────────────────────────────────── pure parts ──


def test_the_attachment_is_found_by_its_hash_and_nothing_else() -> None:
    pdf = _pdf("a")
    raw = _raw(pdf)
    assert attachment_bytes(raw, hashlib.sha256(pdf).hexdigest()) == pdf
    assert attachment_bytes(raw, "0" * 64) is None


def test_the_storage_key_drops_the_bucket_prefix() -> None:
    assert storage_key("mail/contacto@origenlab.cl/2026/10/abc.eml") == "contacto@origenlab.cl/2026/10/abc.eml"
    assert storage_key("contacto@origenlab.cl/2026/10/abc.eml") == "contacto@origenlab.cl/2026/10/abc.eml"


def test_ledger_rows_become_one_record_per_verified_document() -> None:
    sha_a, sha_b = "a" * 64, "b" * 64
    rows = [
        {"document_sha256": sha_a, "drive_file_id": "f1", "drive_folder_id": "c1", "case_key": "k1",
         "archive_status": "archived_verified", "quote_number": "01244-26", "revision": 1},
        {"document_sha256": sha_a, "drive_file_id": "f1", "archive_status": "archived_verified"},
        {"document_sha256": sha_b, "drive_file_id": "f2", "archive_status": "hash_mismatch"},
        {"document_sha256": "short", "drive_file_id": "f3"},
    ]
    records = ledger_records(rows)
    assert [(k, p["drive_file_id"], p["case_key"]) for k, p, _ in records] == [(f"drive_file:{sha_a}", "f1", "k1")]
    with pytest.raises(ValueError):
        ledger_records([{"document_sha256": sha_a, "drive_file_id": "f1"},
                        {"document_sha256": sha_a, "drive_file_id": "f9"}])


# ─────────────────────────────────────────────────────────────── the Drive client ──


class _Transport:
    def __init__(self, scope: str = DRIVE_SCOPE) -> None:
        self.scope = scope
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes, timeout: float):
        self.calls.append((method, url.split("?")[0]))
        if "oauth2" in url:
            return 200, json.dumps({"access_token": "t", "expires_in": 3600, "scope": self.scope}).encode()
        if url.endswith("/about") or "/about?" in url:
            return 200, json.dumps({"user": {"emailAddress": "Contacto@origenlab.cl"}}).encode()
        if "/files/missing" in url:
            return 404, b"{}"
        if "upload" in url:
            assert b"application/pdf" in body and headers["Content-Type"].startswith("multipart/related")
            return 200, b'{"id": "new1"}'
        return 200, b'{"files": []}'


CREDS = DriveCredentials(client_id="cid", client_secret="secret", refresh_token="refresh")


def test_the_client_reads_the_principal_and_uploads_a_pdf() -> None:
    t = _Transport()
    drive = DriveRest(CREDS, transport=t)
    assert drive.principal() == "contacto@origenlab.cl"
    assert drive.get("missing") is None
    assert drive.find_by_property(qa.PROP_DOC_SHA, "x'y") == []
    assert drive.upload_pdf(name="n.pdf", parent_id="p", app_properties={"k": "v"}, data=b"%PDF") == {"id": "new1"}
    assert sum(1 for m, u in t.calls if "oauth2" in u) == 1  # one token for the run


@pytest.mark.parametrize("scope", [
    "https://www.googleapis.com/auth/drive https://mail.google.com/",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.readonly",
])
def test_a_token_that_is_not_exactly_drive_is_refused_before_any_request(scope: str) -> None:
    t = _Transport(scope)
    with pytest.raises(DriveAuthError) as refused:
        DriveRest(CREDS, transport=t).principal()
    assert refused.value.kind == "scope_not_drive"
    assert all("oauth2" in u for _, u in t.calls)


# ─────────────────────────────────────────────────────────────── the CLI ──


def test_drive_file_is_paused_unless_switched_on(capsys) -> None:
    env = {"ORIGENLAB_WORKER_DRIVE_CLIENT_ID": "c", "ORIGENLAB_WORKER_DRIVE_CLIENT_SECRET": "s",
           "ORIGENLAB_WORKER_DRIVE_REFRESH_TOKEN": "r", "ORIGENLAB_WORKER_DRIVE_CASOS_FOLDER_ID": "casos",
           "ORIGENLAB_WORKER_STORAGE_S3_ENDPOINT": "https://ref.storage.supabase.co/storage/v1/s3",
           "ORIGENLAB_WORKER_STORAGE_S3_REGION": "us-west-2", "ORIGENLAB_WORKER_STORAGE_S3_ACCESS_KEY_ID": "k",
           "ORIGENLAB_WORKER_STORAGE_S3_SECRET_ACCESS_KEY": "s"}

    def boom(_config):
        raise AssertionError("a paused run opens nothing")

    assert cli.main(["drive-file"], env, drive_components=boom) == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (line["event"], line["mode"]) == ("drive_file", "paused")


def test_drive_file_refuses_missing_credentials(capsys) -> None:
    assert cli.main(["drive-file"], {"ORIGENLAB_WORKER_DRIVE_FILING_ENABLED": "true"}) == 3
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (line["mode"], line["error"]) == ("config_refused", "drive_credentials_missing")


# ─────────────────────────────────────────────────────────────── against a database ──


@pytest.fixture(scope="module")
def dsn():
    yield from build_disposable_database()


def _seed_quote(dsn: str, mailbox_id: str, *, pdf: bytes, quote_number: str, store: _Store,
                opportunity_id: str | None = None, revision_no: int = 1) -> dict[str, Any]:
    """A captured outbound email with the PDF, and a case whose quote revision was recorded from
    it — as «Registrar cotización» leaves them."""
    gid = uuid.uuid4().hex[:16]
    raw = _raw(pdf, f"CN{quote_number} Cliente.pdf")
    c = build_capture(raw, gmail_message_id=gid, gmail_thread_id=f"t-{gid}", label_ids=("SENT",),
                      label_names={}, internal_date_ms=INTERNAL_MS, mailbox_address=MAILBOX)
    path = f"{MAILBOX}/2026/10/{gid}.eml"
    store.objects[path] = raw
    with open_worker_db(local_test_target(worker_dsn(dsn))) as db:
        db.record(c, mailbox_id=mailbox_id, eml_path=f"mail/{path}", eml_sha256=hashlib.sha256(raw).hexdigest(),
                  size_bytes=len(raw))
    evidence = owner_rows(dsn, "select id::text from evidence.source_record where dedupe_key = %s",
                          (f"gmail_message:{gid}",))[0][0]
    sha = hashlib.sha256(pdf).hexdigest()
    if opportunity_id is None:
        operator = owner_rows(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                                   "values (gen_random_uuid(), %s, 'Op', 'sales', 'active') returning id::text",
                              (f"op-{gid}@example.test",))[0][0]
        org = owner_rows(dsn, "insert into crm.organization (kind, name, confirmation) "
                              "values ('institution', %s, 'confirmed') returning id::text",
                         (f"Cliente Ficticio {gid[:6]}",))[0][0]
        opportunity_id = owner_rows(dsn, "insert into crm.opportunity (title, stage, owner_operator_id) "
                                         "values ('Caso ficticio', 'lead', %s) returning id::text",
                                    (operator,))[0][0]
        # One transaction: the case's organization and its confirmed requesting institution agree
        # at commit (`crm.opportunity_requesting_institution_agrees`).
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                        "confirmation, confirmed_by_operator_id) "
                        "values (%s, %s, 'requesting_institution', current_date, 'confirmed', %s)",
                        (opportunity_id, org, operator))
            cur.execute("update crm.opportunity set organization_id = %s where id = %s", (org, opportunity_id))
        quote = owner_rows(dsn, "insert into crm.quote (opportunity_id, quote_number, number_origin) "
                                "values (%s, %s, 'printed_historical') returning id::text",
                           (opportunity_id, quote_number))[0][0]
    else:
        quote = owner_rows(dsn, "select id::text from crm.quote where opportunity_id = %s", (opportunity_id,))[0][0]
    owner_rows(dsn, "insert into crm.quote_revision (quote_id, revision_no, status, origin, origin_source_record_id, "
                    "pdf_sha256, sent_at) values (%s, %s, 'sent', 'historical_import', %s, %s, now())",
               (quote, revision_no, evidence, sha))
    return {"opportunity_id": opportunity_id, "sha": sha, "pdf": pdf}


@needs_worker_db
def test_a_recorded_quote_is_filed_once_in_its_case_folder_and_recorded(dsn) -> None:
    mailbox_id = seed_mailbox(dsn)
    store, drive = _Store(), FakeDrive()
    first = _seed_quote(dsn, mailbox_id, pdf=_pdf(uuid.uuid4().hex), quote_number="01244-26", store=store)
    business_before = business_rows(dsn)

    with open_worker_db(local_test_target(worker_dsn(dsn))) as db:
        filer = DriveFiler(db.connection, drive, store, TARGET)
        assert filer.run(dry_run=True).as_log()["skipped"] == 1
        assert drive.writes == []
        counts = filer.run()
        assert (counts.filed, counts.refused) == (1, 0), counts.as_log()
        assert filer.run().candidates == 0  # recorded: never filed twice

    folders = [i for i in drive.items.values() if i["appProperties"].get(qa.PROP_KIND) == qa.KIND_CASE]
    assert len(folders) == 1 and folders[0]["parents"] == [CASOS]
    assert folders[0]["appProperties"][qa.PROP_CASE_KEY] == first["opportunity_id"]
    files = [i for i in drive.items.values() if i["appProperties"].get(qa.PROP_DOC_SHA) == first["sha"]]
    assert len(files) == 1 and drive.blobs[files[0]["id"]] == first["pdf"]
    assert drive.writes[0].startswith("folder:Caso 01244 — Cliente Ficticio")

    record = owner_rows(dsn, "select review_status, source_uri, payload from evidence.source_record "
                             "where dedupe_key = %s", (f"drive_file:{first['sha']}",))[0]
    assert record[0] == "reviewed" and record[1] == f"gdrive://file/{files[0]['id']}"
    assert record[2]["opportunity_id"] == first["opportunity_id"] and record[2]["drive_folder_id"] == folders[0]["id"]
    assert business_rows(dsn) == business_before  # no crm.* or outbound.* row

    # The API's case card (as origenlab_api) now links the PDF and the case folder.
    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    cards = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(dsn)).pipeline()["items"]
    card = next(c for c in cards if c["opportunity_id"] == first["opportunity_id"])
    revision = card["quotes"][0]["revisions"][0]
    assert revision["drive"]["file_id"] == files[0]["id"]
    assert card["drive_folder"]["folder_id"] == folders[0]["id"]

    # A second revision of the same case lands in the same folder.
    second = _seed_quote(dsn, mailbox_id, pdf=_pdf(uuid.uuid4().hex), quote_number="01244-26", store=store,
                         opportunity_id=first["opportunity_id"], revision_no=2)
    with open_worker_db(local_test_target(worker_dsn(dsn))) as db:
        assert DriveFiler(db.connection, drive, store, TARGET).run().filed == 1
    new = [i for i in drive.items.values() if i["appProperties"].get(qa.PROP_DOC_SHA) == second["sha"]]
    assert new[0]["parents"] == [folders[0]["id"]]


@needs_worker_db
def test_a_case_the_laptop_archive_filed_keeps_its_folder(dsn) -> None:
    """The 2026-09-26 archive keyed folders by the import plan's id, not the CRM's: the ledger's
    case key is recorded, and the next revision of that case goes into the same folder."""
    mailbox_id = seed_mailbox(dsn) if not owner_rows(dsn, "select 1 from comms.mailbox") else \
        owner_rows(dsn, "select id::text from comms.mailbox limit 1")[0][0]
    store, drive = _Store(), FakeDrive()
    old_pdf = _pdf(uuid.uuid4().hex)
    planned = str(uuid.uuid4())
    folder = drive.add("legacyfolder", "Caso 01300 — Cliente Ficticio", CASOS,
                       {qa.PROP_KIND: qa.KIND_CASE, qa.PROP_CASE_KEY: planned, qa.PROP_OPENING_QUOTE: "01300-26"})
    drive.add("legacyfile", "01300-26 r1 — viejo.pdf", folder["id"],
              {qa.PROP_KIND: qa.KIND_REVISION, qa.PROP_CASE_KEY: planned,
               qa.PROP_DOC_SHA: hashlib.sha256(old_pdf).hexdigest()}, mime=qa.PDF_MIME, data=old_pdf)
    case = _seed_quote(dsn, mailbox_id, pdf=_pdf(uuid.uuid4().hex), quote_number="01300-26", store=store)

    with open_worker_db(local_test_target(worker_dsn(dsn))) as db:
        assert import_ledger(db.connection, [{
            "document_sha256": hashlib.sha256(old_pdf).hexdigest(), "drive_file_id": "legacyfile",
            "drive_folder_id": "legacyfolder", "case_key": planned, "archive_status": "archived_verified",
        }]) == {"rows": 1, "documents": 1, "inserted": 1}
        counts = DriveFiler(db.connection, drive, store, TARGET).run()
    assert counts.filed == 1, counts.as_log()
    new = [i for i in drive.items.values() if i["appProperties"].get(qa.PROP_DOC_SHA) == case["sha"]]
    assert new[0]["parents"] == ["legacyfolder"]
    assert new[0]["appProperties"][qa.PROP_CASE_KEY] == planned


@needs_worker_db
def test_a_wrong_account_files_nothing(dsn) -> None:
    mailbox_id = owner_rows(dsn, "select id::text from comms.mailbox limit 1")
    mailbox_id = mailbox_id[0][0] if mailbox_id else seed_mailbox(dsn)
    store = _Store()
    _seed_quote(dsn, mailbox_id, pdf=_pdf(uuid.uuid4().hex), quote_number="01400-26", store=store)
    drive = FakeDrive(principal="otra@example.invalid")
    with open_worker_db(local_test_target(worker_dsn(dsn))) as db:
        counts = DriveFiler(db.connection, drive, store, TARGET).run()
    assert counts.refusals.get("wrong_account", 0) >= 1 and counts.filed == 0
    assert drive.writes == []
