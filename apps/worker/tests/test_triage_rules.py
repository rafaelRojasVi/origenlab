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
    assert set(CLASSES) == {"outbound", *NOISE_CLASSES, "tender_notice", "purchase_order", "lost",
                            "quote_followup", "quote_request", "business_other"}
    assert "tender_notice" not in NEEDS_MODEL
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


def test_a_purchase_order_from_a_bulk_sender_is_purchase_order() -> None:
    # Business signals (step 5) still beat the bulk check (step 6): a portal at a non-notification
    # domain (not dhl.com / icliente.com) and without a no-reply local part is a purchase order.
    m = mail(sender="portal@compras.portal.invalid", subject="Orden de Compra 4500123 emitida",
             headers={"list-unsubscribe": "<x>"})
    v = classify(TriageInput("inbound", ("INBOX",), m))
    assert v.triage_class == "purchase_order" and v.needs_model


def test_a_noreply_sender_with_oc_subject_is_notification_not_purchase_order() -> None:
    # The early notification check (step 4b) fires before business signals: a noreply sender is
    # noise even when the subject contains «OC» (owner 2026-10-29).
    m = mail(sender="noreply@portal.invalid", subject="Orden de Compra 4500123 emitida",
             headers={"list-unsubscribe": "<x>"})
    v = classify(TriageInput("inbound", ("INBOX",), m))
    assert v.triage_class == "notification"


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


# Cases found in the 2026-10-06 evaluation on real captured mail (STATUS §2.7.69).

@pytest.mark.parametrize("m,expected", [
    (mail(subject="REMOVER", body=""), "unsubscribe"),
    (mail(subject="Re: Cyber OrigenLab", body="BAJA"), "unsubscribe"),
    # The instruction first, then a signature: an unsubscribe reading for a person to confirm
    # (the W10 grammar itself still refuses it as not standalone), never a quote request.
    (mail(subject="RE: Cyber OrigenLab · 5% a 10%", body="REMOVER\n\n \n\nSds.\n\nAna Pérez\nana@cliente.invalid\n+56 9 1111 1111\nCLIENTE SPA"),
     "unsubscribe"),
    (mail(subject="RE: Cyber OrigenLab", body="Remover\n\nGracias"), "unsubscribe"),
    # The instruction outranks an absence phrase in the same reply, in body or subject.
    (mail(subject="RE: Cyber OrigenLab", body="REMOVER\n\nEstoy fuera de la oficina del 28/09 al 23/10."), "unsubscribe"),
    (mail(subject="BAJA", body="Me encuentro fuera de la oficina hasta el 23/10."), "unsubscribe"),
    # An absence notice under a plain «RE:» subject with no machine header: its opening says so.
    (mail(subject="RE: Cyber OrigenLab · 5% a 10%",
          body="Estimados:\n\nJunto con saludarlo, me encuentro fuera de la oficina entre el 28/09 y el 23/10, "
               "sin acceso a correos. En caso de cotizaciones dirigirse a reemplazo@cliente.invalid."), "auto_reply"),
    # A person who mentions a trip after asking is a person.
    (mail(subject="RE: Cyber OrigenLab", body="Necesito cotizar una balanza analítica para el laboratorio, "
          "con despacho a Temuco. Les cuento que la próxima semana estaré fuera de la oficina, así que respondo el lunes."),
     "quote_request"),
    (mail(subject="Re: Cyber OrigenLab", body="Por favor la baja del equipo antiguo y cotizar uno nuevo"),
     "quote_request"),
    (mail(subject="Ausencia Pre y Post natal Re: Cyber OrigenLab"), "auto_reply"),
    (mail(subject="Consulta sobre ausencia de stock"), "business_other"),
    (mail(sender="no-reply@tmes.scanner.invalid", subject="Undelivered Mail Returned to Sender"), "bounce"),
    (mail(sender="system@wherex.com", subject="Wherex – Nueva Licitación de Ejemplo [Lic - 1]"), "tender_notice"),
    # v1 read this as a purchase order because of the OC number; the subject says the order is gone.
    (mail(subject="RE: Anulación OC 4600154766 Lab. Ejemplo"), "lost"),
])
def test_real_mail_cases(m: MailText, expected: str) -> None:
    assert cls(m) == expected


# Cases from the 2026-10-11 audit of «Correos sin caso» (STATUS §2.7.91): v1 called every reply on
# a quote thread a new quote request, because the quote word sat in the «RE:» subject or in the
# quoted history. v2 reads the reply's own words.

QUOTED = "\n\nEl mar, 2 sept 2026 a las 12:05, OrigenLab (<contacto@origenlab.cl>) escribió:\n> Adjunto cotización CN 1171 por el sonicador, precio neto.\n"


@pytest.mark.parametrize("m,expected", [
    # The budget phrase outranks the thread's «Solicitud de cotización» subject and the quoted CN.
    (mail(subject="Re: Solicitud de cotización Hielscher",
          body="Muchas gracias por toda la información. Lamentablemente, los equipos se salen de mi presupuesto actual."
               + QUOTED), "lost"),
    (mail(subject="RE: Anulación OC 4500000123 Lab. Ejemplo",
          body="Le informo que se ha decidido dejar sin efecto la OC 4500000123, la cual fue anulada."), "lost"),
    (mail(subject="RE: Cotización balanzas", body="Las balanzas ya fueron gestionadas por otro proveedor."), "lost"),
    # A client answering on a quote thread with no new ask is not a new request: under a quoted CN
    # it is a follow-up on that quote (the number links; v1 called all of these quote requests).
    (mail(subject="RE: solicitud de cotizacion", body="Buenas tardes, adjunto dirección: Planta Ejemplo, Camino Ficticio km 1."
          + QUOTED), "quote_followup"),
    (mail(subject="Re: Cotización Medidor de Humedad", body="Adjunto comprobante con anticipo del medidor de humedad MB 32."
          + QUOTED, names=("comprobante.pdf",)), "quote_followup"),
    (mail(subject="Re: Cotización Sonicador Ultrasonido",
          body="Muchas gracias por la información. La analizaré para ver cuál se acomoda a nuestro caso." + QUOTED),
     "quote_followup"),
    # … and without a number to link, a person the model (or an operator) reads.
    (mail(subject="RE: solicitud de cotizacion", body="Buenas tardes, adjunto dirección: Planta Ejemplo, Camino Ficticio km 1."
          "\n\nEl jue, 24 sept 2026, OrigenLab escribió:\n> Adjunto documento firmado."), "business_other"),
    # A reply whose own words ask for a price is a request even under a «RE:» subject.
    (mail(subject="RE: Cyber OrigenLab", body="¿Me podría cotizar una centrífuga refrigerada?"
          "\n\nEl mié, 7 oct 2026, OrigenLab escribió:\n> Cyber OrigenLab: 5% a 10% en productos seleccionados."), "quote_request"),
    # A pure forward carries its content below the forwarded header.
    (mail(subject="Fwd: Solicitud de cotización balanzas",
          body="De: Juan Pérez <juan@cliente.invalid>\nEnviado: lunes\nAsunto: balanzas\n\nNecesito cotizar 3 balanzas analíticas."),
     "quote_request"),
    # A supplier's price list is not a request, whatever «precio» says.
    (mail(subject="LISTA DE PRECIOS EDICION 52 //CLIENTE", body="Estimado cliente, envío lista de precios edición 52.",
          names=("Lista52.pdf",)), "business_other"),
    # A fresh subject with the word is still a request; a purchase order is still an order.
    (mail(subject="Solicitud de cotización", body="Buenos días"), "quote_request"),
    (mail(subject="Orden de compra - Origenlab - Estándar", body="Le adjunto orden de compra 9001, correspondiente a cotización N° 00999-26.",
          names=("OC 9001.pdf",)), "purchase_order"),
])
def test_v2_reads_the_replys_own_words(m: MailText, expected: str) -> None:
    assert cls(m) == expected


def test_a_cancelled_order_is_lost_before_it_is_an_order_and_says_so_without_text() -> None:
    v = classify(TriageInput("inbound", (), mail(subject="RE: Anulación OC 4500000123 Lab. Ejemplo")))
    assert v.triage_class == "lost" and v.reasons == ("subject:cancelled",)
    assert v.purchase_order_numbers == ("4500000123",) and v.signals["lost"] and v.signals["purchase_order"]


# ------------------------------------------------------------------ web-quote-form rule (v4)

def test_web_quote_form_from_own_mailbox_becomes_quote_request() -> None:
    m = MailText(
        sender="contacto@origenlab.cl",
        subject="Solicitud de cotización — sitio web — UChile",
        machine_headers={"x-origenlab-origin": "web-quote-form/v1"},
        body="Nombre: Ana García\nEmail: ana@uchile.cl\nProducto: centrífuga",
        reply_to="ana@uchile.cl",
    )
    v = classify(TriageInput("outbound", ("SENT",), m))
    assert v.triage_class == "quote_request"
    assert v.reasons == ("header:x-origenlab-origin",)
    assert v.sender_domain == "uchile.cl"
    assert not v.free_mail


def test_web_quote_header_without_reply_to_stays_outbound() -> None:
    m = MailText(
        sender="contacto@origenlab.cl",
        subject="Solicitud de cotización — sitio web",
        machine_headers={"x-origenlab-origin": "web-quote-form/v1"},
        body="sin Reply-To",
        reply_to=None,
    )
    assert cls(m, direction="outbound") == "outbound"


def test_external_mail_with_forged_web_quote_header_stays_inbound_not_web_quote() -> None:
    # sender NOT in OWN_DOMAINS → the new rule does not fire → classified normally
    m = MailText(
        sender="spoofed@external.invalid",
        subject="Solicitud de cotización",
        machine_headers={"x-origenlab-origin": "web-quote-form/v1"},
        body="cotizar equipo de laboratorio",
        reply_to="ana@uchile.cl",
    )
    v = classify(TriageInput("inbound", ("INBOX",), m))
    assert v.triage_class == "quote_request"
    assert "header:x-origenlab-origin" not in v.reasons


# ------------------------------------------------------------------ notification noise (2026-10-29)

def test_dhl_domain_is_notification() -> None:
    assert cls(mail(sender="tracking@dhl.com", subject="Tu paquete fue entregado")) == "notification"


def test_dhl_subdomain_is_notification() -> None:
    # express.dhl.com ends with .dhl.com
    assert cls(mail(sender="cl.noreply@express.dhl.com", subject="Detalles del envío")) == "notification"


def test_icliente_domain_is_notification() -> None:
    assert cls(mail(sender="contacto@icliente.com", subject="Te contactaremos a la brevedad")) == "notification"


def test_drive_noreply_is_notification_via_loose_pattern() -> None:
    # «drive-shares-dm-noreply@» — «-noreply» after «dm» matches the loose pattern
    assert cls(mail(sender="drive-shares-dm-noreply@google.com", subject="Solicitud de acceso")) == "notification"


def test_no_responder_is_notification_via_loose_pattern() -> None:
    # «no_responder» at address start matches the loose pattern
    assert cls(mail(sender="no_responder@portal.invalid", subject="Tu solicitud fue recibida")) == "notification"


def test_notification_domain_beats_oc_subject() -> None:
    """A courier notice with «OC» in the subject is still notification, not purchase_order."""
    assert cls(mail(sender="tracking@dhl.com", subject="OC 4500000123 entregada hoy")) == "notification"
