"""The parity check's comparison rule. Nothing here reaches Gmail or a database."""

from __future__ import annotations

from mailfixtures import INTERNAL_MS, MAILBOX, make_raw
from origenlab_worker.capture import build_capture
from scriptload import load_script

parity = load_script("payload_parity")

#: Built once: each EmailMessage gets a fresh MIME boundary, so two builds differ in raw_sha256.
RAW_SENT = make_raw(frm=f"OrigenLab <{MAILBOX}>", to="Ana <ana@cliente.invalid>",
                    subject="Cotización CN01005", pdf_name="Cotizacion CN01005.pdf")


def rebuilt(**over):
    payload = build_capture(RAW_SENT, gmail_message_id="g1", gmail_thread_id="t1", label_ids=("SENT",),
                            label_names={}, internal_date_ms=INTERNAL_MS, mailbox_address=MAILBOX).payload
    return {**payload, **over}


def test_a_staged_payload_equal_on_every_computed_key_has_no_difference() -> None:
    staged = rebuilt(staging_source_record_sha256="a" * 64, source_email_id=1234,
                     sent_revision="eligible_after_operator_confirmation (exact Message-ID -> one Gmail message)")
    assert parity.compare_payload(staged, rebuilt()) == []


def test_the_capture_may_keep_more_documents_than_the_staging_but_never_fewer() -> None:
    capture = rebuilt()
    more = {**capture, "documents": capture["documents"] + [{"sha256": "b" * 64}]}
    assert parity.compare_payload(capture, more) == []
    assert parity.compare_payload(more, capture) == ["documents"]


def test_each_differing_key_is_named_and_a_different_key_set_is_reported() -> None:
    staged = rebuilt(sender="Otro <otro@cliente.invalid>", tier="G2_gmail_no_doc")
    del staged["source_email_id"]
    assert parity.compare_payload(staged, rebuilt()) == ["sender", "tier", "staged_key_set"]
