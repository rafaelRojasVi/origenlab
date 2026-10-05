"""Invented messages for the worker's tests. The repository is public: every correspondent is at an
`.invalid` domain; the only real address is the business's own published mailbox."""

from __future__ import annotations

from email.message import EmailMessage

MAILBOX = "contacto@origenlab.cl"
PDF = b"%PDF-1.4\n% invented for a test\n"
PNG = b"\x89PNG\r\n\x1a\n invented"
#: 2026-10-12T13:00:00Z, the Gmail internalDate of every fixture unless a test says otherwise.
INTERNAL_MS = 1_791_810_000_000


def make_raw(
    *,
    frm: str | None = "Ana Pérez <ana@cliente.invalid>",
    to: str = MAILBOX,
    cc: str | None = None,
    subject: str = "Solicitud de cotización",
    date: str | None = "Mon, 12 Oct 2026 10:00:00 -0300",
    message_id: str | None = "<m1@cliente.invalid>",
    pdf_name: str | None = None,
    pdf_inline_with_cid: bool = False,
    logo: bool = False,
    list_unsubscribe: bool = False,
) -> bytes:
    msg = EmailMessage()
    if frm is not None:
        msg["From"] = frm
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    if date:
        msg["Date"] = date
    if message_id:
        msg["Message-ID"] = message_id
    if list_unsubscribe:
        msg["List-Unsubscribe"] = f"<mailto:{MAILBOX}?subject=REMOVER>"
    msg.set_content("Hola,\nadjunto el documento.\n")
    if pdf_name:
        msg.add_attachment(
            PDF, maintype="application", subtype="pdf", filename=pdf_name,
            disposition="inline" if pdf_inline_with_cid else "attachment",
            cid="<pdf-1@cliente.invalid>" if pdf_inline_with_cid else None,
        )
    if logo:
        msg.add_attachment(PNG, maintype="image", subtype="png", filename="logo.png",
                           disposition="inline", cid="<logo@cliente.invalid>")
    return msg.as_bytes()


#: 8-bit Latin-1 bytes and a NUL in headers, as some mail servers still send them.
RAW_8BIT = (
    b"From: Jos\xe9 <jose@cliente.invalid>\r\n"
    b"To: contacto@origenlab.cl\r\n"
    b"Subject: Cotizaci\xf3n\x00 urgente\r\n"
    b"Date: Mon, 12 Oct 2026 10:00:00 -0300\r\n"
    b"Message-ID: <m8bit@cliente.invalid>\r\n"
    b"Content-Type: text/plain; charset=iso-8859-1\r\n"
    b"\r\n"
    b"Necesito cotizaci\xf3n.\r\n"
)

#: No From header at all: cannot become evidence.
RAW_NO_FROM = b"To: contacto@origenlab.cl\r\nSubject: sin remitente\r\n\r\ncuerpo\r\n"
