"""The cheap triage rules: noise stops here, business signals and people go on to the model."""

from __future__ import annotations

import pytest

from origenlab_worker.mail_text import MailText
from origenlab_worker.triage_rules import CLASSES, NEEDS_MODEL, NOISE_CLASSES, TriageInput, classify


def mail(subject: str = "Hola", body: str = "Buenos días", *, sender: str = "ana@cliente.invalid",
         headers: dict[str, str] | None = None, types: tuple[str, ...] = ("text/plain",),
         names: tuple[str, ...] = ()) -> MailText:
    return MailText(sender=sender, subject=subject, machine_headers=headers or {}, content_types=types,
                    attachment_names=names, body=body)


def cls(m: MailText, *, direction: str = "inbound", labels: tuple[str, ...] = ("INBOX",)) -> str:
    return classify(TriageInput(direction=direction, labels=labels, mail=m)).triage_class


def test_every_class_is_either_noise_a_signal_or_for_the_model() -> None:
    assert set(CLASSES) == {"outbound", *NOISE_CLASSES, "purchase_order", "lost", "quote_followup",
                            "quote_request", "business_other"}
    assert not NEEDS_MODEL & {"outbound", *NOISE_CLASSES}


def test_our_own_mail_is_outbound_and_never_read_by_the_model() -> None:
    v = classify(TriageInput("outbound", ("SENT",), mail(subject="Cotización CN 1234")))
    assert v.triage_class == "outbound" and not v.needs_model
    assert cls(mail(sender="contacto@origenlab.cl")) == "outbound"


@pytest.mark.parametrize("m", [
    mail(types=("multipart/report", "message/delivery-status")),
    mail(sender="MAILER-DAEMON@mx.cliente.invalid"),
    mail(subject="Undeliverable: Cotización"),
    mail(subject="No se pudo entregar el mensaje"),
    mail(headers={"x-failed-recipients": "a@b.invalid"}),
])
def test_delivery_failures_are_bounces(m: MailText) -> None:
    assert cls(m) == "bounce"


@pytest.mark.parametrize("m", [
    mail(headers={"auto-submitted": "auto-replied"}),
    mail(headers={"x-autoreply": "yes"}),
    mail(subject="Respuesta automática: Solicitud de cotización"),
    mail(subject="Out of Office: RFQ"),
])
def test_automatic_replies_are_noise_even_when_they_quote_a_request(m: MailText) -> None:
    assert cls(m) == "auto_reply"


def test_auto_submitted_no_is_a_person() -> None:
    assert cls(mail(headers={"auto-submitted": "no"}, body="Necesito cotizar un pHmetro")) == "quote_request"


def test_a_calendar_invitation_is_noise() -> None:
    assert cls(mail(types=("multipart/mixed", "text/calendar"))) == "calendar"


def test_a_purchase_order_beats_a_no_reply_portal_sender() -> None:
    m = mail(sender="noreply@portal.invalid", subject="Orden de Compra 4500123 emitida",
             headers={"list-unsubscribe": "<x>"})
    v = classify(TriageInput("inbound", ("INBOX",), m))
    assert v.triage_class == "purchase_order" and v.needs_model


def test_an_oc_attachment_is_a_purchase_order_and_its_number_is_kept() -> None:
    v = classify(TriageInput("inbound", (), mail(subject="Adjunto", names=("OC 4500123.pdf",))))
    assert v.triage_class == "purchase_order"
    assert v.purchase_order_numbers == ("4500123",)


def test_lost_phrases_win_over_a_quote_number() -> None:
    assert cls(mail(subject="RE: CN 1234", body="Les informamos que compramos con otro proveedor.")) == "lost"


def test_a_cn_number_is_a_follow_up_on_a_quote() -> None:
    v = classify(TriageInput("inbound", (), mail(subject="RE: Cotización CN-1234-26", body="¿Plazo de entrega?")))
    assert v.triage_class == "quote_followup" and v.quote_numbers == ("1234",)


def test_quote_words_in_subject_or_body_are_a_request() -> None:
    assert cls(mail(subject="Solicitud de cotización")) == "quote_request"
    assert cls(mail(subject="Consulta", body="¿Me podría enviar el precio de un baño termorregulado?")) == "quote_request"


def test_bulk_headers_and_promotion_tabs_are_bulk_when_no_signal() -> None:
    assert cls(mail(headers={"list-unsubscribe": "<x>"})) == "bulk"
    assert cls(mail(), labels=("INBOX", "CATEGORY_PROMOTIONS")) == "bulk"


def test_no_reply_senders_and_updates_are_notifications_but_info_at_is_a_person() -> None:
    assert cls(mail(sender="no-reply@banco.invalid")) == "notification"
    assert cls(mail(), labels=("CATEGORY_UPDATES",)) == "notification"
    assert cls(mail(sender="info@universidad.invalid", body="Queremos conocer su línea de centrífugas")) \
        == "business_other"


def test_a_person_with_no_signal_goes_to_the_model_and_nothing_at_all_is_empty() -> None:
    v = classify(TriageInput("inbound", (), mail(body="Necesito hablar con alguien de servicio técnico")))
    assert v.triage_class == "business_other" and v.needs_model
    assert cls(mail(subject="", body="")) == "empty"


def test_free_mail_and_domain_are_reported_and_reasons_never_carry_text() -> None:
    v = classify(TriageInput("inbound", (), mail(sender="ana@gmail.com", subject="Solicitud de cotización")))
    assert v.free_mail and v.sender_domain == "gmail.com"
    assert v.reasons == ("subject:quote_word",)
