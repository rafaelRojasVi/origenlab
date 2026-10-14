"""Reading who really asked, out of a request Tatiana forwarded to contacto@. Every name, address and
number below is invented; the repository is public."""
from __future__ import annotations

import html as html_lib
from email.message import EmailMessage

from origenlab_worker.forwarded_request import is_candidate, read_forward


def _eml(sender: str, subject: str, body: str, *, html: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "contacto@origenlab.cl"
    msg["Subject"] = subject
    if html:
        # As a mail client writes it: text escaped (an address in angle brackets is «&lt;…&gt;»).
        msg.set_content("<html><body>" + html_lib.escape(body).replace("\n", "<br>") + "</body></html>", subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


OUTLOOK_MARKETPLACE = """\

De: Ventas Proveedor <ventas@proveedor-ficticio.com>
Enviado el: lunes, 12 de octubre de 2026 19:01
Para: Tatiana <contacto@labdelivery.cl>
Asunto: FW: Marketplace Quote Request: Tabletas de verificación

Hola Tatiana, ¿puedes ver este lead y contactarlo?

From: Marketplace.com <postoffice@mail.mailer.labx.com>
Sent: Monday, October 12, 2026 4:29 PM
To: Ventas Proveedor <ventas@proveedor-ficticio.com>
Subject: Marketplace Quote Request: Tabletas de verificación

Quote Request: #000001
Tabletas de verificación para durómetro
Sold By:
Proveedor Ficticio
Contact Information:
Name:
Ana Prueba Ficticia
Email:
ana@laboratorio-ficticio.cl
Phone:
900000001
Company:
Laboratorio Ficticio EIRL (Rut:11.111.111-1)
City:
Santiago
Message:
Hello, could I get a quote?
"""

GMAIL_FORWARD = """\
Te reenvío esto.

---------- Forwarded message ---------
De: Pedro Ejemplo <pedro.ejemplo@universidad-ficticia.cl>
Date: lun, 5 oct 2026 a las 10:12
Subject: Solicitud cotización Gradillas
To: <contacto@origenlab.cl>

Estimados, necesitamos cotizar 20 gradillas para tubos de 15 ml.
Saludos, Pedro Ejemplo
Laboratorio de Química, Universidad Ficticia
"""


def test_only_our_senders_with_a_forward_prefix_are_candidates() -> None:
    assert is_candidate("contacto@origenlab.cl", "Fwd: Solicitud cotización")
    assert is_candidate("contacto@labdelivery.cl", "RV: Quotation request")
    assert is_candidate("tatiana.personal@gmail.com", "FW: x", extra_senders={"tatiana.personal@gmail.com"})
    assert not is_candidate("cliente@universidad-ficticia.cl", "Fwd: something")
    assert not is_candidate("contacto@origenlab.cl", "Re: Cotización 01234-26")
    assert not is_candidate("contacto@origenlab.cl", "Solicitud cotización")
    assert not is_candidate(None, "Fwd: x")


def test_a_marketplace_lead_forwarded_twice_names_the_buyer_not_the_relays() -> None:
    raw = _eml("Tatiana <contacto@labdelivery.cl>", "RV: Marketplace Quote Request: Tabletas de verificación",
               OUTLOOK_MARKETPLACE)
    got = read_forward(raw)
    assert got is not None
    assert got.original_email == "ana@laboratorio-ficticio.cl"
    assert got.original_name == "Ana Prueba Ficticia"
    assert got.company == "Laboratorio Ficticio EIRL"
    assert got.rut == "11.111.111-1"
    assert got.phone == "900000001"
    assert got.requested_item == "Tabletas de verificación para durómetro"
    assert got.relay == "marketplace"
    assert got.origin == "labdelivery"


def test_a_gmail_forward_names_the_original_sender_and_subject() -> None:
    raw = _eml("Tatiana Vivanco <contacto@origenlab.cl>", "Fwd: Solicitud cotización Gradillas", GMAIL_FORWARD)
    got = read_forward(raw)
    assert got is not None
    assert (got.original_email, got.original_name) == ("pedro.ejemplo@universidad-ficticia.cl", "Pedro Ejemplo")
    assert got.requested_item == "Solicitud cotización Gradillas"
    assert got.company is None and got.rut is None and got.relay is None
    assert got.origin == "origenlab"


def test_html_bodies_are_read_too() -> None:
    raw = _eml("contacto@origenlab.cl", "Fwd: Solicitud cotización Gradillas", GMAIL_FORWARD, html=True)
    got = read_forward(raw)
    assert got is not None and got.original_email == "pedro.ejemplo@universidad-ficticia.cl"


def test_a_forward_with_only_our_own_addresses_reads_nothing() -> None:
    body = """\
---------- Forwarded message ---------
From: Tatiana <contacto@labdelivery.cl>
Date: lun, 5 oct 2026
Subject: nota interna
To: <contacto@origenlab.cl>

Recordar llamar mañana.
"""
    assert read_forward(_eml("contacto@origenlab.cl", "Fwd: nota interna", body)) is None


def test_no_forwarded_header_reads_nothing() -> None:
    assert read_forward(_eml("contacto@origenlab.cl", "Fwd: algo", "Mira esto, sin encabezado.")) is None


def test_garbage_bytes_read_nothing_instead_of_raising() -> None:
    assert read_forward(b"\x00\xff not an email at all") is None


def test_the_reading_as_json_carries_only_the_fields() -> None:
    raw = _eml("contacto@origenlab.cl", "Fwd: Solicitud cotización Gradillas", GMAIL_FORWARD)
    value = read_forward(raw).as_json()  # type: ignore[union-attr]
    assert set(value) == {"original_name", "original_email", "company", "rut", "phone", "requested_item",
                          "relay", "origin", "subject"}
    assert "gradillas para tubos" not in str(value)  # no body text is kept


TRACKED = "<https://u000.ct.sendgrid.invalid/ls/click?upn=u001.abcDEF-2B\nGHIjkl-3D-3D>"


def test_marketplace_labels_wrapped_in_tracking_links_still_name_the_buyer_not_the_supplier() -> None:
    # The plain-text part as a marketplace mailer writes it: every label behind a multi-line tracking link,
    # and the supplier who forwarded the lead sits above as an ordinary «De:» hop.
    body = (OUTLOOK_MARKETPLACE.split("Contact Information:")[0]
            + f"{TRACKED} Contact Information:\n\n{TRACKED} Name:\n\nAna Prueba Ficticia\n\n{TRACKED} Email:\n\n"
              f"ana@laboratorio-ficticio.cl\n\n{TRACKED} Phone:\n\n900000001\n\n{TRACKED} Company:\n\n"
              f"Laboratorio Ficticio EIRL (Rut:11.111.111-1)\n\n{TRACKED} Message:\n\nHello\n")
    got = read_forward(_eml("contacto@labdelivery.cl", "RV: Marketplace Quote Request: Tabletas", body))
    assert got is not None and got.original_email == "ana@laboratorio-ficticio.cl"
    assert got.company == "Laboratorio Ficticio EIRL" and got.relay == "marketplace"


def test_the_buyer_in_the_html_part_wins_over_a_supplier_hop_in_the_plain_part() -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "contacto@labdelivery.cl", "contacto@origenlab.cl", "RV: lead"
    msg.set_content("De: Ventas Proveedor <ventas@proveedor-ficticio.com>\nEnviado el: lunes\nPara: x\nAsunto: FW: lead\n\nmira")
    msg.add_alternative("<p>Contact Information:</p><p>Name:</p><p>Ana Prueba</p><p>Email:</p>"
                        "<p>ana@laboratorio-ficticio.cl</p><p>Message:</p><p>hola</p>", subtype="html")
    got = read_forward(msg.as_bytes())
    assert got is not None and got.original_email == "ana@laboratorio-ficticio.cl"


def test_a_forward_we_send_to_a_customer_is_not_a_request_for_us() -> None:
    # Tatiana forwarding an answer TO the customer: sender ours, «Fwd:», but addressed outside.
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "contacto@origenlab.cl", "pedro.ejemplo@universidad-ficticia.cl", \
        "Fwd: Solicitud cotización Gradillas"
    msg.set_content(GMAIL_FORWARD)
    assert read_forward(msg.as_bytes()) is None


def test_a_forward_copied_to_someone_outside_is_not_one_either() -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Cc"], msg["Subject"] = ("contacto@labdelivery.cl", "contacto@origenlab.cl",
                                                         "otro@empresa-ficticia.cl", "RV: algo")
    msg.set_content(GMAIL_FORWARD)
    assert read_forward(msg.as_bytes()) is None
