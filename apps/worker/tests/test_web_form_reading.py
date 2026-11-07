"""The product and consent a web-form message carries. Invented values only (public repo)."""
from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import MagicMock

from origenlab_api.v2.gmail_send import build_web_quote_message

from origenlab_worker.mail_text import parse_mail
from origenlab_worker.triage import record_web_form_reading
from origenlab_worker.web_form_reading import read_web_form, read_web_form_body

STAMP = "2026-11-08T10:15:00-03:00"


def _form(**over) -> bytes:
    args = dict(reply_to="ana@cliente.invalid", institution="Instituto Ficticio", need="Necesito cotizar",
                extra={}, submitted_at=STAMP, product="UP400St", consent_version="consent-v1-2026-11")
    args.update(over)
    return build_web_quote_message(**args)


def test_the_product_and_the_consent_are_read_from_the_real_form_message() -> None:
    assert read_web_form(_form()) == {"product": "UP400St", "consent_version": "consent-v1-2026-11",
                                      "accepted_at": STAMP}


def test_each_is_optional() -> None:
    assert read_web_form(_form(consent_version=None)) == {"product": "UP400St"}
    assert read_web_form(_form(product=None)) == {"consent_version": "consent-v1-2026-11", "accepted_at": STAMP}
    assert read_web_form(_form(product=None, consent_version=None)) == {}


def test_free_text_with_a_signature_delimiter_does_not_hide_the_consent() -> None:
    assert read_web_form(_form(need="Hola\n--\nsaludos\n> cita"))["consent_version"] == "consent-v1-2026-11"


def test_a_consent_line_typed_by_the_visitor_is_not_a_consent() -> None:
    forged = _form(need="Hola\nConsentimiento: consent-v9 " + STAMP, consent_version=None)
    assert "consent_version" not in read_web_form(forged)
    # ...and a different timestamp after «Enviado:» is not the form's own line either.
    assert read_web_form_body(f"Enviado: {STAMP}\nConsentimiento: v1 2026-01-01T00:00:00") == {}


def test_malformed_lines_store_what_parses() -> None:
    assert read_web_form_body("Producto:\nEnviado: x\nConsentimiento: solo-version") == {}
    assert read_web_form_body("Producto:  MB32 \nEnviado: x") == {"product": "MB32"}
    assert read_web_form(b"not an email at all \xff\xfe") == {}


def test_a_message_without_a_plain_body_reads_nothing() -> None:
    msg = EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("<p>Producto: X</p>", subtype="html")
    assert read_web_form(msg.as_bytes()) == {}


def test_only_the_marked_message_is_read_and_a_write_failure_never_breaks_the_triage() -> None:
    raw = _form()
    db = MagicMock()
    assert record_web_form_reading(db, "sr-1", raw, parse_mail(raw)) is True
    db.record_web_form.assert_called_once_with(
        "sr-1", {"product": "UP400St", "consent_version": "consent-v1-2026-11", "accepted_at": STAMP})
    plain = bytes(raw).replace(b"X-OrigenLab-Origin: web-quote-form/v1\n", b"").replace(
        b"X-OrigenLab-Origin: web-quote-form/v1\r\n", b"")
    other = MagicMock()
    assert record_web_form_reading(other, "sr-2", plain, parse_mail(plain)) is False
    other.record_web_form.assert_not_called()
    broken = MagicMock()
    broken.record_web_form.side_effect = RuntimeError("boom")
    assert record_web_form_reading(broken, "sr-3", raw, parse_mail(raw)) is False
