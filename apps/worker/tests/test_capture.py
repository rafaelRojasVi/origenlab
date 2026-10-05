"""Capture builder — one raw message to its rows and evidence payload. Invented messages only."""

from __future__ import annotations

import hashlib
import json

import pytest

from mailfixtures import INTERNAL_MS, MAILBOX, RAW_8BIT, RAW_NO_FROM, make_raw
from origenlab_worker.capture import (
    DOCUMENT_KEYS,
    PAYLOAD_KEYS,
    QUOTE_NUMBER_CONVENTION,
    ParseFailed,
    build_capture,
    clean_text,
    cn_tokens,
    intake_skip_reason,
    normalize_rfc822_id,
    unparsed_message,
)

GMAIL_ID = "1a2b3c4d5e6f0001"
THREAD_ID = "1a2b3c4d5e6f0000"


def capture(raw: bytes, *, labels=("INBOX",), names=None):
    return build_capture(
        raw, gmail_message_id=GMAIL_ID, gmail_thread_id=THREAD_ID, label_ids=labels,
        label_names=names or {}, internal_date_ms=INTERNAL_MS, mailbox_address=MAILBOX,
    )


def test_the_payload_has_exactly_the_21_staged_keys() -> None:
    assert len(PAYLOAD_KEYS) == 21 and len(DOCUMENT_KEYS) == 7


def test_an_inbound_request_with_a_quote_pdf_has_the_staged_shape() -> None:
    raw = make_raw(pdf_name="Solicitud CN 1005.pdf", logo=True)
    c = capture(raw)
    assert set(c.payload) == PAYLOAD_KEYS
    (doc,) = c.payload["documents"]
    assert set(doc) == DOCUMENT_KEYS
    assert (doc["filename"], doc["cn_tokens"], doc["bytes_hash_verified"]) == ("Solicitud CN 1005.pdf", ["CN01005"], True)
    assert doc["source_email_id"] is doc["source_attachment_id"] is doc["stored_path"] is None
    assert c.payload["proposed_quote_numbers"] == ["CN01005"]
    assert c.payload["tier"] == "G1_gmail_doc"
    assert c.payload["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert c.payload["intake_class"] == "primary_evidence"
    assert c.payload["gmail_labels"] == c.payload["gmail_label_ids"] == ["INBOX"]
    assert c.payload["quote_number_convention"] == QUOTE_NUMBER_CONVENTION
    assert c.payload["rfc822_message_id"] == "<m1@cliente.invalid>"
    assert c.payload["sent_at"] == "2026-10-12T10:00:00-03:00"
    assert c.payload["sender"] == "Ana Pérez <ana@cliente.invalid>"
    assert c.payload["subject_raw"] == "Solicitud de cotización"
    for key in ("opportunity_id", "quote_number", "sent_revision", "source_email_id",
                "staging_source_record_sha256", "direction_hint"):
        assert c.payload[key] is None
    assert (c.direction, c.is_bulk_send, c.rfc822_message_id_norm) == ("inbound", False, "m1@cliente.invalid")
    # The logo is an attachment row, never a document.
    assert [a.mime_type for a in c.attachments] == ["application/pdf", "image/png"]
    json.dumps(c.payload)


def test_a_pdf_carrying_a_content_id_is_still_a_document() -> None:
    c = capture(make_raw(pdf_name="Cotizacion CN01022.pdf", pdf_inline_with_cid=True))
    assert [d["cn_tokens"] for d in c.payload["documents"]] == [["CN01022"]]


def test_a_message_without_a_document_is_tier_g2() -> None:
    c = capture(make_raw())
    assert (c.payload["tier"], c.payload["documents"], c.payload["proposed_quote_numbers"]) == ("G2_gmail_no_doc", [], [])


def test_an_outbound_quotation_gets_the_v1_direction_hint() -> None:
    raw = make_raw(frm=f"OrigenLab <{MAILBOX}>", to="Ana <ana@cliente.invalid>", subject="RE: Cotización CN01005")
    c = capture(raw, labels=("SENT",))
    assert (c.direction, c.payload["direction_hint"]) == ("outbound", "customer_quote_candidate")


def test_an_outbound_message_to_ourselves_is_internal_only() -> None:
    c = capture(make_raw(frm=f"OrigenLab <{MAILBOX}>", to=MAILBOX, subject="nota"), labels=("SENT",))
    assert c.payload["direction_hint"] == "internal_only"


def test_a_message_in_both_inbox_and_sent_is_one_outbound_message() -> None:
    c = capture(make_raw(frm=f"OrigenLab <{MAILBOX}>", to=MAILBOX), labels=("INBOX", "SENT"))
    assert c.direction == "outbound" and c.payload["gmail_label_ids"] == ["INBOX", "SENT"]


def test_label_names_are_stored_but_never_decide_intake() -> None:
    labels = ("INBOX", "Label_7")
    assert intake_skip_reason(labels) is None
    c = capture(make_raw(), labels=labels, names={"INBOX": "INBOX", "Label_7": "Borradores 2026"})
    assert c.labels == ("INBOX", "Borradores 2026") and c.payload["gmail_labels"] == ["INBOX", "Label_7"]


@pytest.mark.parametrize("labels,reason", [
    (("DRAFT",), "draft"), (("SPAM", "UNREAD"), "spam"), (("INBOX", "TRASH"), "trash"),
    (("INBOX",), None), (("SENT", "CATEGORY_PERSONAL"), None), (("Label_3",), None),
])
def test_drafts_spam_and_trash_are_skipped_by_label_id(labels, reason) -> None:
    assert intake_skip_reason(labels) == reason


def test_an_outbound_campaign_send_is_a_bulk_send_and_a_newsletter_is_not() -> None:
    sent = capture(make_raw(frm=f"OrigenLab <{MAILBOX}>", to="x@cliente.invalid", list_unsubscribe=True), labels=("SENT",))
    received = capture(make_raw(list_unsubscribe=True), labels=("INBOX",))
    assert (sent.is_bulk_send, received.is_bulk_send) == (True, False)


def test_participants_are_folded_deduplicated_and_never_a_group_placeholder() -> None:
    raw = make_raw(
        frm="Ana Pérez <Ana@Cliente.invalid>",
        to="contacto@origenlab.cl, undisclosed-recipients:;",
        cc="ANA@cliente.invalid, Pedro <pedro@otro.invalid>, ana@cliente.invalid",
    )
    assert [(p.role, p.address_norm, p.display_name) for p in capture(raw).participants] == [
        ("from", "ana@cliente.invalid", "Ana Pérez"),
        ("to", "contacto@origenlab.cl", None),
        ("cc", "ana@cliente.invalid", None),
        ("cc", "pedro@otro.invalid", "Pedro"),
    ]


def test_eight_bit_headers_and_a_nul_become_text_postgres_accepts() -> None:
    """Review Focus 4: V1's parser turns the Latin-1 bytes into U+FFFD (what V1 stored); the NUL
    PostgreSQL and jsonb refuse is removed; nothing raises."""
    c = capture(RAW_8BIT)
    assert c.subject == "Cotizaci�n urgente" and "\x00" not in c.payload["subject_raw"]
    assert c.payload["sender"] == "Jos� <jose@cliente.invalid>"
    for value in (c.subject, c.payload["sender"], c.payload["subject_raw"]):
        value.encode("utf-8")
    json.dumps(c.payload, ensure_ascii=False).encode("utf-8")


def test_a_message_without_from_is_a_parse_failure() -> None:
    with pytest.raises(ParseFailed) as exc:
        capture(RAW_NO_FROM)
    assert exc.value.code == "no_from_header"


def test_without_a_date_header_sent_at_is_gmails_internal_date() -> None:
    assert capture(make_raw(date=None)).payload["sent_at"] == "2026-10-12T13:00:00+00:00"


@pytest.mark.parametrize("name,tokens", [
    ("Cotizacion CN01005.pdf", ["CN01005"]), ("CN 1234.pdf", ["CN01234"]), ("cn_01005-v2.pdf", ["CN01005"]),
    ("Solicitud CN-1005 y CN 2001.pdf", ["CN01005", "CN02001"]),
    ("ACN1234.pdf", []), ("CN12.pdf", []), ("CN1234567.pdf", []), (None, []),
])
def test_cn_tokens_follow_the_staging_rule(name, tokens) -> None:
    assert cn_tokens(name) == tokens


@pytest.mark.parametrize("raw,norm", [
    ("<ABC@Mail.Example.invalid>", "abc@mail.example.invalid"), ("  x@y.invalid ", "x@y.invalid"),
    ("", None), (None, None),
])
def test_rfc822_ids_are_bare_and_folded(raw, norm) -> None:
    assert normalize_rfc822_id(raw) == norm


def test_clean_text_removes_nul_and_lone_surrogates() -> None:
    assert clean_text("a\x00b\udcf3c") == "ab?c" and clean_text(None) is None


def test_an_unparsed_row_comes_from_gmails_metadata() -> None:
    row = unparsed_message(
        gmail_message_id=GMAIL_ID, gmail_thread_id=THREAD_ID, label_ids=("SENT",), label_names={},
        internal_date_ms=INTERNAL_MS, headers={"from": f"OrigenLab <{MAILBOX}>", "subject": "x\x00y",
                                                 "message-id": "<Big@x.invalid>"},
        mailbox_address=MAILBOX, reason="too_large",
    )
    assert (row.direction, row.subject, row.rfc822_message_id_norm, row.reason) == ("outbound", "xy", "big@x.invalid", "too_large")


# ---- fix round 1 ----

ALIAS = "OrigenLab Ventas <ventas@alias.invalid>"


def _raw_with_part(content_type: str, filename: str) -> bytes:
    return (
        b"From: Ana <ana@cliente.invalid>\r\nTo: contacto@origenlab.cl\r\nSubject: x\r\n"
        b"Message-ID: <nul@cliente.invalid>\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/mixed; boundary="b1"\r\n\r\n'
        b"--b1\r\nContent-Type: text/plain\r\n\r\nhola\r\n"
        b"--b1\r\nContent-Type: " + content_type.encode() + b"\r\n"
        b'Content-Disposition: attachment; filename="' + filename.encode() + b'"\r\n\r\n'
        b"datos\r\n--b1--\r\n"
    )


def _no_nul(c) -> None:
    assert "\x00" not in json.dumps(c.payload, ensure_ascii=False)
    for p in c.participants:
        assert "\x00" not in p.address_norm and "\x00" not in (p.display_name or "")
    for a in c.attachments:
        assert "\x00" not in (a.filename or "") and "\x00" not in a.mime_type
    assert all("\x00" not in x for x in c.labels)
    assert "\x00" not in (c.rfc822_message_id_norm or "") and "\x00" not in (c.subject or "")


def test_a_nul_in_a_content_type_never_reaches_the_attachment_row() -> None:
    c = capture(_raw_with_part("application/pd\x00f", "a.pdf"))
    _no_nul(c)
    assert [a.mime_type for a in c.attachments] == ["application/pdf"]


def test_a_nul_in_an_attachment_filename_is_removed() -> None:
    c = capture(_raw_with_part("application/pdf", "CN 1005\x00.pdf"))
    _no_nul(c)
    assert c.attachments[0].filename == "CN 1005.pdf"


def test_a_nul_in_the_from_address_and_name_is_removed() -> None:
    c = capture(make_raw(frm="An\x00a <an\x00a@cliente.invalid>"))
    _no_nul(c)


def test_a_nul_in_a_user_label_name_and_the_message_id_is_removed() -> None:
    c = capture(make_raw(message_id="<a\x00b@cliente.invalid>"), labels=("Label_1",), names={"Label_1": "Bo\x00rrador"})
    _no_nul(c)
    assert c.labels == ("Borrador",) and c.rfc822_message_id_norm == "ab@cliente.invalid"


def test_a_message_in_inbox_and_sent_from_a_send_as_alias_is_outbound_by_the_sent_label() -> None:
    c = capture(make_raw(frm=ALIAS, to=MAILBOX), labels=("INBOX", "SENT"))
    assert c.direction == "outbound"


def test_the_mailbox_as_sender_without_a_sent_label_is_outbound_in_any_case() -> None:
    c = capture(make_raw(frm=f"OrigenLab <Contacto@OrigenLab.cl>", to="x@cliente.invalid"), labels=("INBOX",))
    assert c.direction == "outbound"


def test_an_alias_sender_without_a_sent_label_is_inbound() -> None:
    assert capture(make_raw(frm=ALIAS), labels=("INBOX",)).direction == "inbound"


def test_the_convention_string_is_the_staged_literal() -> None:
    c = capture(make_raw())
    assert c.payload["quote_number_convention"] == "CN<serial, zero-padded to 5> — proposed, requires operator confirmation"
    assert QUOTE_NUMBER_CONVENTION == c.payload["quote_number_convention"]


def test_the_payload_key_names_are_pinned() -> None:
    assert sorted(PAYLOAD_KEYS) == sorted([
        "gmail_message_id", "gmail_thread_id", "rfc822_message_id", "gmail_label_ids", "gmail_labels",
        "raw_sha256", "intake_class", "tier", "direction_hint", "sent_at", "subject_raw", "sender",
        "recipients", "documents", "proposed_quote_numbers", "quote_number_convention", "quote_number",
        "opportunity_id", "sent_revision", "source_email_id", "staging_source_record_sha256",
    ])
    assert sorted(DOCUMENT_KEYS) == sorted([
        "source_email_id", "source_attachment_id", "filename", "sha256", "cn_tokens",
        "bytes_hash_verified", "stored_path",
    ])


def test_ids_recipients_and_attachment_facts_have_their_values() -> None:
    from mailfixtures import PDF

    c = capture(make_raw(to="Pedro <pedro@otro.invalid>, contacto@origenlab.cl", cc="luz@otro.invalid",
                         pdf_name="Cotizacion CN01005.pdf"))
    assert c.payload["gmail_message_id"] == GMAIL_ID and c.payload["gmail_thread_id"] == THREAD_ID
    assert c.payload["recipients"] == "Pedro <pedro@otro.invalid>, contacto@origenlab.cl; luz@otro.invalid"
    digest = hashlib.sha256(PDF).hexdigest()
    assert c.payload["documents"][0]["sha256"] == digest
    (att,) = c.attachments
    assert (att.part_index, att.size_bytes, att.sha256) == (att.part_index, len(PDF), digest)
    assert isinstance(att.part_index, int) and att.part_index >= 0


def test_build_capture_runs_the_staging_validator() -> None:
    from origenlab_worker.v1_reuse import ManifestRefused

    with pytest.raises(ManifestRefused):
        capture(make_raw(), labels=("DRAFT",))


@pytest.mark.parametrize("kwargs", [
    {"message_id": "not an id <<"},
    {"message_id": None},
    {"to": "@@@, ;;; , <>, undisclosed-recipients:;"},
    {"to": "", "cc": ",,,"},
])
def test_hostile_headers_still_build_a_capture(kwargs) -> None:
    assert capture(make_raw(**kwargs)).payload["gmail_message_id"] == GMAIL_ID


def test_a_duplicated_message_id_header_builds_a_capture() -> None:
    raw = make_raw().replace(b"Message-ID: <m1@cliente.invalid>", b"Message-ID: <m1@cliente.invalid>\r\nMessage-ID: <m2@cliente.invalid>")
    assert capture(raw).rfc822_message_id_norm in {"m1@cliente.invalid", "m2@cliente.invalid"}


def test_an_empty_body_builds_a_capture() -> None:
    raw = b"From: Ana <ana@cliente.invalid>\r\nTo: contacto@origenlab.cl\r\nSubject: vacio\r\n\r\n"
    assert capture(raw).payload["tier"] == "G2_gmail_no_doc"
