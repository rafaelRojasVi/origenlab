"""The product and the consent a web-quote-form submission carries (spec 2026-11-07, T1.4).

The public form makes contacto@ mail itself (`origenlab_api.v2.gmail_send.build_web_quote_message`);
when the visitor chose a product it adds a body line «Producto: <model>», and when they ticked the
consent box a last line «Consentimiento: <version> <submitted_at>» right after «Enviado: <submitted_at>».
The API's rules never see a body, so the triage reads those two lines and stores them as the
`message_triage` assertion `web_form:v1` — the facts only, never the text. The rules (R7/R12)
then record the consent (`crm.consent`) and the case interest in their own transaction.

Read from the raw message, not `MailText.body`: that one is cut at a «--» line or a quoted-history
marker, which a visitor's free text can contain, and the consent line is the last of the message.

The visitor writes the «Qué necesita» text and could type a «Consentimiento:» line of their own.
A consent counts only as the very last line and only right after «Enviado:» with the same timestamp —
the form always writes both, so text typed earlier in the body cannot stand in for them.
"""

from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any

WEB_FORM_VALUE_NORM = "web_form:v1"
_PRODUCT = "Producto:"
_SENT = "Enviado:"
_CONSENT = "Consentimiento:"


def read_web_form_body(text: str) -> dict[str, str]:
    """`{"product", "consent_version", "accepted_at"}`, each only when it parses."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: dict[str, str] = {}
    for line in lines:
        if line.startswith(_PRODUCT) and (product := line[len(_PRODUCT):].strip()):
            out["product"] = product[:120]
    if len(lines) >= 2 and lines[-1].startswith(_CONSENT) and lines[-2].startswith(_SENT):
        sent = lines[-2][len(_SENT):].strip()
        version, _, stamp = lines[-1][len(_CONSENT):].strip().partition(" ")
        stamp = stamp.strip()
        if version and stamp and stamp == sent:
            out["consent_version"] = version[:80]
            out["accepted_at"] = stamp
    return out


def read_web_form(raw: bytes) -> dict[str, str]:
    """`read_web_form_body` of the message's plain-text body; empty when there is none."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    part = msg.get_body(preferencelist=("plain",)) if isinstance(msg, EmailMessage) else None
    if part is None:
        return {}
    try:
        content: Any = part.get_content()
    except (LookupError, UnicodeError, ValueError):
        return {}
    return read_web_form_body(content) if isinstance(content, str) else {}


__all__ = ["WEB_FORM_VALUE_NORM", "read_web_form", "read_web_form_body"]
