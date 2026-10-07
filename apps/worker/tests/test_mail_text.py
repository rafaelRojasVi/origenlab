"""The text the triage reads out of an `.eml`: the reply only, plain or HTML, and the machine headers."""

from __future__ import annotations

from email.message import EmailMessage

from origenlab_worker.mail_text import MAX_BODY_CHARS, parse_mail, reply_text


def _raw(body: str, *, html: str | None = None, headers: dict[str, str] | None = None,
         subject: str = "Consulta", attachment: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Ana Pérez <Ana@Cliente.invalid>"
    msg["To"] = "contacto@origenlab.cl"
    msg["Subject"] = subject
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    if attachment:
        msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename=attachment)
    return msg.as_bytes()


def test_the_sender_is_lower_case_and_the_subject_one_line() -> None:
    raw = _raw("Hola", subject="Solicitud   de cotización").replace(b"Subject: Solicitud", b"Subject: Solicitud\r\n")
    mail = parse_mail(raw)
    assert mail.sender == "ana@cliente.invalid"
    assert mail.subject == "Solicitud de cotización"


def test_the_quoted_history_of_a_spanish_gmail_reply_is_dropped() -> None:
    body = ("Necesito precio de 2 agitadores.\n\nEl lun, 5 oct 2026 a las 10:02, Contacto "
            "<contacto@origenlab.cl> escribió:\n> Estimada Ana\n> adjunto cotización")
    assert parse_mail(_raw(body)).body == "Necesito precio de 2 agitadores."


def test_an_outlook_header_block_ends_the_reply_but_a_sentence_starting_with_de_does_not() -> None:
    assert reply_text("Confirmo.\n\nDe: Contacto\nEnviado: lunes\nPara: Ana\n\nCotización") == "Confirmo."
    assert reply_text("De: nuestra parte, todo bien.\nGracias") == "De: nuestra parte, todo bien.\nGracias"


def test_the_signature_and_mobile_footer_are_dropped() -> None:
    assert reply_text("Por favor cotizar.\n-- \nAna Pérez\nJefa de laboratorio") == "Por favor cotizar."
    assert reply_text("Ok gracias\nEnviado desde mi iPhone") == "Ok gracias"


def test_html_only_mail_is_read_as_text() -> None:
    msg = EmailMessage()
    msg["From"] = "x@cliente.invalid"
    msg["Subject"] = "Hola"
    msg.set_content("<p>Necesito <b>cotizar</b></p><style>p{}</style><p>un pHmetro</p>", subtype="html")
    assert parse_mail(msg.as_bytes()).body == "Necesito cotizar\n\nun pHmetro" or \
        parse_mail(msg.as_bytes()).body == "Necesito cotizar\nun pHmetro"


def test_plain_is_preferred_over_html() -> None:
    mail = parse_mail(_raw("texto plano", html="<p>texto html</p>"))
    assert mail.body == "texto plano"


def test_machine_headers_attachments_and_types_are_reported() -> None:
    mail = parse_mail(_raw("x", headers={"Auto-Submitted": "auto-replied", "List-Id": "<l.invalid>"},
                           attachment="OC 4500123.pdf"))
    assert mail.machine_headers == {"auto-submitted": "auto-replied", "list-id": "<l.invalid>"}
    assert mail.attachment_names == ("OC 4500123.pdf",)
    assert "application/pdf" in mail.content_types


def test_a_long_body_is_cut_and_says_so() -> None:
    mail = parse_mail(_raw("a" * (MAX_BODY_CHARS + 50)))
    assert len(mail.body) == MAX_BODY_CHARS and mail.body_truncated
