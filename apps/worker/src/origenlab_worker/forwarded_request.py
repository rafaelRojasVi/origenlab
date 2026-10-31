"""Who really asked: the customer inside a request Tatiana forwarded to contacto@ (spec 2026-10-14).

Tatiana forwards requests that reached her elsewhere — her Labdelivery Outlook («RV:»), Gmail
(«Fwd:»), a supplier passing on a marketplace lead — to contacto@. The rules see such an email as
sent by OrigenLab itself, so it never became a Solicitud. This module reads the forwarded text and
names the original requester.

Pure and defensive: bytes in, a :class:`ForwardedRequest` or ``None`` out; it never raises (a
reading that cannot be made is simply not made), never calls a model, and keeps no body text —
only the fields below. The triage step calls it only for :func:`is_candidate` messages.

How it reads, in order:
1. The full text of the message (HTML turned to text) — not the reply-cut body the triage uses,
   because a forward's whole point is the history below the cut.
2. A marketplace contact block («Contact Information: Name … Email … Company …», as LabX sends it):
   when present it names the buyer, whatever relays sit on top.
3. Otherwise the forwarded header blocks («---------- Forwarded message» / Outlook «De: … Enviado: …»,
   Spanish and English): the innermost hop whose address is neither ours nor a known relay.
No customer email found → ``None``: refuse, don't guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr

from origenlab_worker.mail_text import html_to_text

#: Value norm of the reading (an `evidence.assertion` of kind `message_triage`).
FORWARDED_VALUE_NORM = "forwarded:v1"

#: Our own addresses and domains: never the customer.
OWN_SENDERS: frozenset[str] = frozenset({"contacto@origenlab.cl", "contacto@labdelivery.cl"})
OWN_DOMAINS: frozenset[str] = frozenset({"origenlab.cl", "labdelivery.cl"})
#: Mail relays of marketplaces and mailers: a hop through them is not the customer either.
RELAY_DOMAINS: frozenset[str] = frozenset({"labx.com", "mail.mailer.labx.com", "mailer.labx.com"})

_FORWARD_PREFIX = re.compile(r"^\s*(?:(?:re|aw)\s*:\s*)*(?:rv|fw|fwd|tr|reenv(?:iado)?)\s*:", re.IGNORECASE)
_ANY_PREFIX = re.compile(r"^\s*(?:(?:re|rv|fw|fwd|tr|aw|wg|sv|reenv(?:iado)?)\s*:\s*)+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_FROM_LINE = re.compile(r"^\s*(?:de|from)\s*:\s*(.+)$", re.IGNORECASE)
_HOP_FIELD = re.compile(r"^\s*(enviado(?: el)?|sent|date|fecha|para|to|asunto|subject|cc)\s*:\s*(.*)$", re.IGNORECASE)
_RUT = re.compile(r"\(?\s*rut\s*[:.]?\s*([\d.]+-[\dkK])\s*\)?", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(name|nombre|email|e-mail|correo|phone|tel[eé]fono|company|empresa|"
                    r"street|city|ciudad|region/state|country|pa[ií]s|postal/zip|message|mensaje)\s*:\s*(.*)$",
                    re.IGNORECASE)
_FIELD_KEY = {"name": "name", "nombre": "name", "email": "email", "e-mail": "email", "correo": "email",
              "phone": "phone", "teléfono": "phone", "telefono": "phone", "company": "company", "empresa": "company"}


@dataclass(frozen=True)
class ForwardedRequest:
    original_name: str | None
    original_email: str
    company: str | None
    rut: str | None
    phone: str | None
    requested_item: str | None
    relay: str | None          # "marketplace" when a marketplace contact block named the buyer
    origin: str                # "labdelivery" | "origenlab" | "personal": whose mailbox forwarded it
    subject: str               # the forward's own subject, prefixes removed

    def as_json(self) -> dict[str, str | None]:
        return asdict(self)


def is_forward_subject(subject: str | None) -> bool:
    """Whether a subject starts with a forward prefix («RV:», «Fwd:», «TR:» …), after any «Re:»."""
    return bool(_FORWARD_PREFIX.match(subject or ""))


def is_candidate(sender: str | None, subject: str | None, *, extra_senders: Iterable[str] = ()) -> bool:
    """A message we sent (contacto@, the Labdelivery mailbox, or a configured personal address of
    Tatiana) whose subject starts with a forward prefix. Everything else skips this module at once."""
    address = (parseaddr(sender or "")[1] or sender or "").strip().lower()
    if not address or not _FORWARD_PREFIX.match(subject or ""):
        return False
    return address in OWN_SENDERS or address in {s.strip().lower() for s in extra_senders}


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def _ours(address: str) -> bool:
    return address.lower() in OWN_SENDERS or _domain(address) in OWN_DOMAINS


def _ours_or_relay(address: str) -> bool:
    d = _domain(address)
    return (address.lower() in OWN_SENDERS or d in OWN_DOMAINS
            or any(d == r or d.endswith("." + r) for r in RELAY_DOMAINS))


#: A link a mailer puts in the plain-text part: «<https://…>», possibly folded over several lines.
_LINK = re.compile(r"<https?://[^>\s][^>]*>", re.DOTALL)
_WS = re.compile("[ \t\u00a0]+")


def _texts(msg: EmailMessage) -> list[str]:
    """The plain-text part and the HTML part as text, each with mailer links removed. Both are read:
    a marketplace's plain part hides its labels behind tracking links that its HTML part lacks."""
    out = []
    for kind in ("plain", "html"):
        part = msg.get_body(preferencelist=(kind,))
        if part is None or part.get_content_type() != f"text/{kind}":
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, ValueError):
            content = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if not isinstance(content, str):
            continue
        if kind == "html":
            content = html_to_text(content)
        content = _LINK.sub(" ", content.replace("\r", ""))
        lines = [_WS.sub(" ", line).strip() for line in content.split("\n")]
        out.append("\n".join(line for line in lines if not line.startswith(">") or len(line) > 1))
    return out


def _hops(lines: list[str]) -> list[dict[str, str | None]]:
    """Every forwarded header block, outermost first: its sender name, address and subject."""
    hops = []
    for i, line in enumerate(lines):
        m = _FROM_LINE.match(line)
        if not m:
            continue
        nearby = [ln for ln in lines[i + 1:i + 6] if ln]
        fields = {}
        for ln in nearby:
            f = _HOP_FIELD.match(ln)
            if f:
                fields[f.group(1).lower()] = f.group(2).strip()
        if not fields:
            continue  # «De: nuestra parte…» is a sentence, not a header
        name, address = parseaddr(m.group(1))
        if not address:
            found = _EMAIL.search(m.group(1))
            address = found.group(0) if found else ""
        if not address or "@" not in address:
            continue
        subject = fields.get("asunto") or fields.get("subject")
        hops.append({"name": (name or "").strip(" \"'") or None, "email": address.lower(), "subject": subject})
    return hops


def _marketplace_block(lines: list[str]) -> tuple[dict[str, str], str | None] | None:
    """The «Contact Information» label/value block, values on the same line or the next one; and
    the product line (the line before «Sold By:»), if the block exists."""
    start = next((i for i, ln in enumerate(lines) if re.match(r"^\s*(contact information|datos de contacto)\s*:?\s*$",
                                                              ln, re.IGNORECASE)), None)
    if start is None:
        return None
    out: dict[str, str] = {}
    i = start + 1
    while i < len(lines) and i < start + 40:
        m = _LABEL.match(lines[i])
        if m:
            key = _FIELD_KEY.get(m.group(1).lower())
            value = m.group(2).strip()
            if not value:
                j = i + 1
                while j < len(lines) and not lines[j]:
                    j += 1
                value = lines[j].strip() if j < len(lines) and not _LABEL.match(lines[j]) else ""
            if key and value and key not in out:
                out[key] = value
            if m.group(1).lower() in ("message", "mensaje"):
                break
        i += 1
    product = None
    sold = next((k for k in range(start) if re.match(r"^\s*sold by\s*:?\s*$", lines[k], re.IGNORECASE)), None)
    if sold is not None:
        product = next((lines[k] for k in range(sold - 1, max(-1, sold - 4), -1) if lines[k]), None)
    return out, product


def _origin(sender: str) -> str:
    d = _domain(sender) if "@" in sender else ""
    if d == "labdelivery.cl":
        return "labdelivery"
    if d == "origenlab.cl":
        return "origenlab"
    return "personal"


def read_forward(raw: bytes) -> ForwardedRequest | None:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        if not isinstance(msg, EmailMessage):
            return None
        sender = (parseaddr(str(msg.get("From", "")))[1] or "").lower()
        # Only a forward addressed to us is a request for us: Tatiana also forwards answers TO
        # customers («Fwd:» from contacto@ to the client), and those are conversations, not requests.
        recipients = [a.lower() for _, a in getaddresses([str(msg.get(h, "")) for h in ("To", "Cc")]) if a]
        if not recipients or any(not _ours(a) for a in recipients):
            return None
        subject = _ANY_PREFIX.sub("", " ".join(str(msg.get("Subject", "") or "").split())).strip()
        texts = [t.split("\n") for t in _texts(msg)]
    except Exception:  # noqa: BLE001 - an unreadable message is no reading, never an error
        return None
    # A marketplace buyer, found in either part, outranks any forwarded hop (a hop above it is a relay);
    # when both parts name the buyer, the reading with more fields wins (a plain part may split the RUT off).
    buyers = [b for b in (_from_marketplace(lines, sender, subject) for lines in texts) if b is not None]
    if buyers:
        return max(buyers, key=lambda b: sum(v is not None for v in b.as_json().values()))
    for lines in texts:
        for hop in reversed(_hops(lines)):  # innermost first
            email = hop["email"] or ""
            if _EMAIL.fullmatch(email) and not _ours_or_relay(email):
                item = _ANY_PREFIX.sub("", hop["subject"] or "").strip() or subject or None
                return ForwardedRequest(
                    original_name=hop["name"], original_email=email, company=None, rut=None, phone=None,
                    requested_item=item, relay=None, origin=_origin(sender), subject=subject)
    return None


def _from_marketplace(lines: list[str], sender: str, subject: str) -> ForwardedRequest | None:
    block = _marketplace_block(lines)
    if block is None:
        return None
    fields, product = block
    email = (fields.get("email") or "").lower()
    if not _EMAIL.fullmatch(email) or _ours_or_relay(email):
        return None
    company = fields.get("company")
    rut = None
    if company:
        r = _RUT.search(company)
        if r:
            rut = r.group(1)
            company = _RUT.sub("", company).strip(" ,;") or None
    return ForwardedRequest(
        original_name=fields.get("name"), original_email=email, company=company, rut=rut,
        phone=fields.get("phone"), requested_item=product or subject or None, relay="marketplace",
        origin=_origin(sender), subject=subject)


__all__ = ["FORWARDED_VALUE_NORM", "ForwardedRequest", "is_candidate", "is_forward_subject", "read_forward"]
