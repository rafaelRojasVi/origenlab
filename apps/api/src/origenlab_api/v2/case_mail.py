"""One case email, read from its stored `.eml`, for the dashboard's reader (owner 2026-10-25:
«when I press Responder a new Gmail window opens — show it there»).

Gmail cannot be reused from the dashboard: its `Cross-Origin-Opener-Policy` drops the tab's name,
so every link opens a new tab. The dashboard shows the email itself instead, and Gmail is one
click away for the answer.

**Plain text only.** The body is the message's `text/plain` part, else its HTML reduced to text
(`audience_freeze.text_from_html`): no markup, remote image or tracking pixel ever reaches the
browser. The quoted earlier conversation is split off («El … escribió:», Outlook's «De: … Enviado:»,
`>` lines) so the reader leads with what the person wrote. People come by display name, else by
domain — the full address stays in Gmail, as in «Correos del caso».

Nothing is written; the message must be on one of the case's threads (`opportunity_message_source`).
"""

from __future__ import annotations

import email
import re
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

from origenlab_api.v2.audience_freeze import text_from_html

#: The largest `.eml` read (Gmail's limit is 25 MB; base64 inflates it).
MAX_EML_BYTES = 40 * 1024 * 1024
#: The reader shows a message, not a document: longer bodies are cut, with Gmail for the rest.
MAX_TEXT_CHARS = 20_000

_GMAIL_ID = re.compile(r"^[0-9A-Za-z_-]{6,64}$")
# Where the quoted earlier conversation starts: Gmail/Apple («El … escribió:», «On … wrote:»), the
# Outlook header block («De: … » followed by «Enviado:» or «Sent:»), or a separator line.
_QUOTE_HEAD = re.compile(
    r"^\s*(?:(?:El|On)\s.{4,200}(?:escribió|wrote)\s*:\s*$"
    r"|-{2,}\s*(?:Mensaje original|Original Message|Forwarded message|Mensaje reenviado)\s*-{2,}"
    r"|_{10,}\s*$)",
    re.IGNORECASE,
)
_OUTLOOK_FROM = re.compile(r"^\s*\*?(?:De|From)\s*:\*?\s+\S", re.IGNORECASE)
_OUTLOOK_SENT = re.compile(r"^\s*\*?(?:Enviado|Sent|Fecha|Date)\s*:\*?\s", re.IGNORECASE)


def valid_message_id(value: str) -> bool:
    return bool(_GMAIL_ID.match(value))


def person(name: str, address: str) -> str | None:
    """A display name, else the address's domain — never the full address."""
    name = (name or "").strip().strip('"').strip()
    if name and "@" not in name:
        return name
    domain = address.rsplit("@", 1)[-1].strip().lower() if "@" in (address or "") else ""
    return domain or None


def people(header: str | None) -> list[str]:
    out: list[str] = []
    for name, address in getaddresses([header or ""]):
        shown = person(name, address)
        if shown and shown not in out:
            out.append(shown)
    return out


def split_quoted(text: str) -> tuple[str, str | None]:
    """The part a person wrote, and the quoted earlier conversation below it (or None)."""
    lines = text.splitlines()
    cut = None
    for i, line in enumerate(lines):
        if _QUOTE_HEAD.match(line):
            cut = i
            break
        if _OUTLOOK_FROM.match(line) and any(_OUTLOOK_SENT.match(n) for n in lines[i + 1:i + 4]):
            cut = i
            break
        if line.startswith(">") and all(not n.strip() or n.startswith(">") for n in lines[i:]):
            cut = i
            break
    if cut is None:
        return text.strip(), None
    main, quoted = "\n".join(lines[:cut]).strip(), "\n".join(lines[cut:]).strip()
    if not main:  # nothing above the quote: show it all as the message
        return quoted, None
    return main, quoted or None


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    if part.get_content_subtype() == "html":
        content = text_from_html(content)
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def read_message(raw_eml: bytes) -> dict[str, Any]:
    """What the reader shows of one email: who, when, the subject, the text and its attachments."""
    msg = email.message_from_bytes(raw_eml, policy=policy.default)
    assert isinstance(msg, EmailMessage)
    try:
        sent_at = parsedate_to_datetime(str(msg["date"])).isoformat() if msg["date"] else None
    except (TypeError, ValueError):
        sent_at = None
    text = _body_text(msg)
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS].rstrip()
    main, quoted = split_quoted(text)
    attachments = []
    for part in msg.iter_attachments():
        name = part.get_filename()
        if not name:
            continue
        data = part.get_payload(decode=True)
        attachments.append({"filename": name, "size": len(data) if isinstance(data, bytes) else None})
    senders = people(str(msg["from"] or ""))
    return {
        "subject": str(msg["subject"] or "") or None,
        "from": senders[0] if senders else None,
        "to": people(str(msg["to"] or "")),
        "cc": people(str(msg["cc"] or "")),
        "sent_at": sent_at,
        "text": main,
        "quoted": quoted,
        "truncated": truncated,
        "attachments": attachments,
    }
