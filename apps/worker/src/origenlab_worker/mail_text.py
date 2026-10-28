"""What the triage reads out of a stored `.eml`: the headers that mark machine mail, and the text
the sender actually wrote — the quoted history, the signature and the HTML markup removed.

Pure: bytes in, a :class:`MailText` out. No network, no database. The body is never stored by
the capture (comms.message has no body column); it is read here from Storage, used for one
triage, and dropped. Only what the triage proposes is kept (`evidence.assertion`).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr

#: The longest reply text kept for the rules and the model. A request for a quotation fits in a
#: few hundred characters; anything past this is history the quote markers missed.
MAX_BODY_CHARS = 6000

#: The longest signature block kept (name, title, institution, phone fit well inside).
MAX_SIGNATURE_CHARS = 800

#: How many trailing non-empty body lines are kept as `tail_text` (signatures without a delimiter).
TAIL_LINES = 12

#: Headers that machine mail sets (RFC 3834, mailing lists, bulk senders). Lower-case names.
MACHINE_HEADERS: tuple[str, ...] = (
    "auto-submitted", "x-autoreply", "x-autorespond", "x-auto-response-suppress", "precedence",
    "list-id", "list-unsubscribe", "x-failed-recipients", "feedback-id", "x-campaign",
    "x-origenlab-origin",
)

# A line that starts the quoted history of a reply: «El lun, 6 oct 2026 a las 10:02, X escribió:»,
# «On Mon, … wrote:», Outlook's «De: … Enviado: …» block and its English twin, forwarded headers.
_QUOTE_STARTS = re.compile(
    r"^\s*(?:"
    r"el\s.{0,120}\sescribi[oó]\s*:"
    r"|on\s.{0,120}\swrote\s*:"
    r"|-{2,}\s*(?:original message|mensaje original|forwarded message|mensaje reenviado)\s*-{2,}"
    r"|(?:de|from)\s*:\s.+$"
    r")",
    re.IGNORECASE,
)
_OUTLOOK_NEXT = re.compile(r"^\s*(?:enviado|sent|para|to|fecha|date)\s*:", re.IGNORECASE)
_SIGNATURE = re.compile(r"^(?:--\s*|_{5,}\s*|enviado desde mi \S+.*|sent from my \S+.*)$", re.IGNORECASE)
_TAGS = re.compile(r"<(script|style)\b.*?</\1\s*>|<[^>]+>", re.IGNORECASE | re.DOTALL)
_BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|/div|/tr|/li|/h\d)\b[^>]*>", re.IGNORECASE)
_SPACES = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class MailText:
    sender: str | None
    subject: str
    #: Lower-case header name → value, only for :data:`MACHINE_HEADERS` that are present.
    machine_headers: dict[str, str] = field(default_factory=dict)
    #: The MIME types of every part, in walk order (`multipart/report`, `text/calendar` …).
    content_types: tuple[str, ...] = ()
    attachment_names: tuple[str, ...] = ()
    #: What the sender wrote, without quoted history or signature, at most MAX_BODY_CHARS.
    body: str = ""
    body_truncated: bool = False
    #: The Reply-To address (lower-cased bare address), when present; None otherwise.
    reply_to: str | None = None
    #: The lines after the first signature delimiter inside the reply part, at most
    #: MAX_SIGNATURE_CHARS. Never taken from quoted history.
    signature_text: str = ""
    #: The last TAIL_LINES non-empty lines of `body`; signatures without a delimiter live here.
    tail_text: str = ""


def html_to_text(markup: str) -> str:
    text = _BLOCK_TAGS.sub("\n", markup)
    text = _TAGS.sub("", text)
    return html.unescape(text)


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def _is_quote_start(lines: list[str], i: int) -> bool:
    """True when `lines[i]` opens the quoted history (the «De:» rule included)."""
    line = lines[i]
    if not _QUOTE_STARTS.match(line):
        return False
    # «De: X» alone is a sentence («De: nuestra parte…») unless Outlook's next header follows.
    if re.match(r"^\s*(?:de|from)\s*:", line, re.IGNORECASE):
        following = next((nxt for nxt in lines[i + 1:i + 4] if nxt.strip()), "")
        return bool(_OUTLOOK_NEXT.match(following))
    return True


def split_reply(text: str) -> tuple[str, str]:
    """`(reply, signature)`: the reply cut at the first signature delimiter or quoted-history
    marker (`>` lines dropped), and the lines after that delimiter up to the quoted history."""
    kept: list[str] = []
    signature: list[str] = []
    in_signature = False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.lstrip().startswith(">"):
            continue
        if not in_signature and _SIGNATURE.match(line.strip()):
            in_signature = True
            continue
        if _is_quote_start(lines, i):
            break
        (signature if in_signature else kept).append(line)
    return _normalise("\n".join(kept)), _normalise("\n".join(signature))


def reply_text(text: str) -> str:
    """`text` cut at the first quoted-history marker or signature delimiter; `>` lines dropped."""
    return split_reply(text)[0]


def _body_part(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, ValueError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if not isinstance(content, str):
        return ""
    if part.get_content_type() == "text/html":
        content = html_to_text(content)
    return content


def parse_mail(raw: bytes) -> MailText:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    sender = parseaddr(str(msg.get("From", "")))[1].strip().lower() or None
    subject = " ".join(str(msg.get("Subject", "") or "").split())
    machine = {name: " ".join(str(msg.get(name, "")).split()) for name in MACHINE_HEADERS if msg.get(name) is not None}
    types: list[str] = []
    names: list[str] = []
    for part in msg.walk():
        types.append(part.get_content_type())
        filename = part.get_filename()
        if filename:
            names.append(" ".join(filename.split()))
    reply_to_raw = str(msg.get("Reply-To", ""))
    reply_to_addr = parseaddr(reply_to_raw)[1].strip().lower() or None
    body, signature = split_reply(_normalise(_body_part(msg)))
    truncated = len(body) > MAX_BODY_CHARS
    tail = "\n".join([ln for ln in body[:MAX_BODY_CHARS].split("\n") if ln.strip()][-TAIL_LINES:])
    return MailText(
        sender=sender, subject=subject, machine_headers=machine, content_types=tuple(types),
        attachment_names=tuple(names), body=body[:MAX_BODY_CHARS], body_truncated=truncated,
        reply_to=reply_to_addr, signature_text=signature[:MAX_SIGNATURE_CHARS], tail_text=tail,
    )


__all__ = ["MAX_BODY_CHARS", "MAX_SIGNATURE_CHARS", "TAIL_LINES", "split_reply", "MACHINE_HEADERS", "MailText", "html_to_text", "parse_mail", "reply_text"]
