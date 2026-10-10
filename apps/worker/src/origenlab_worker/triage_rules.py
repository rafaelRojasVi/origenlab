"""Mail triage, the cheap part: which captured messages are machine noise, which carry a business
signal, and which deserve the model. Pure — a :class:`TriageInput` in, a :class:`RuleVerdict` out.

Order of the checks, first match wins:

1. **outbound** — our own mail (a reply, a quote we sent). The email → cases rules in the API read
   it; the model never does.
2. **bounce** — a delivery report (`multipart/report`, mailer-daemon, «Undeliverable» …).
3. **auto_reply** — RFC 3834 `Auto-Submitted`, `X-Autoreply`, out-of-office subjects.
4. **calendar** — an invitation (`text/calendar`).
   **unsubscribe** — a «BAJA» / «REMOVER» reply: the API's W10 grammar (`unsubscribe_replies`)
   on the body, or the bare word as the whole subject. The API records the unsubscribe; the
   model never reads it.
   **tender_notice** — a tender alert (Wherex, Mercado Público): a lead for the procurement
   pipeline, structured already, so not for the model.
5. Business signals, in this order, **before** the bulk and notification checks, because a
   purchase order or a quotation from a portal often arrives from a no-reply sender:
   **purchase_order**, **lost**, **quote_followup** (a CN quote number), **quote_request**.
6. **bulk** — mailing-list and bulk headers, Gmail's Promotions / Social / Forums tabs.
7. **notification** — no-reply and notification senders, Gmail's Updates tab.
8. **business_other** — a person wrote and nothing above matched: the model reads it.
9. **empty** — nothing to read at all.

The phrase lists are the API's (`origenlab_api.v2.mail_rules`), by name, so «orden de compra»
means the same thing to the triage and to the rules that link an email to a case.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from origenlab_api.v2.mail_rules import (
    FREE_MAIL_DOMAINS,
    LOST_PHRASES,
    OWN_DOMAINS,
    PURCHASE_ORDER_FILENAME,
    PURCHASE_ORDER_NUMBER,
    PURCHASE_ORDER_PHRASES,
    QUOTE_REQUEST_WORDS,
    fold,
)
from origenlab_api.v2.unsubscribe_replies import ACCEPTED_FORMS, classify_reply, reply_text
from origenlab_worker.mail_text import MailText

#: Bumped whenever a rule changes what it decides. The proposal is stored per version
#: (`value_norm = triage:v<N>`), so a new version re-triages every message beside the old reading.
TRIAGE_VERSION = 1

NOISE_CLASSES: tuple[str, ...] = ("bounce", "auto_reply", "calendar", "unsubscribe", "bulk", "notification",
                                   "empty")
#: Business, but settled without the model: tender alerts go to the procurement pipeline.
ROUTED_CLASSES: tuple[str, ...] = ("tender_notice",)
SIGNAL_CLASSES: tuple[str, ...] = ("purchase_order", "lost", "quote_followup", "quote_request")
CLASSES: tuple[str, ...] = ("outbound", *NOISE_CLASSES, *ROUTED_CLASSES, *SIGNAL_CLASSES, "business_other")

#: The classes the model reads after the rules: a person wrote, and either a business signal
#: was found or nothing explains the message.
NEEDS_MODEL: frozenset[str] = frozenset({*SIGNAL_CLASSES, "business_other"})

_BOUNCE_SENDERS = re.compile(r"^(?:mailer-daemon|postmaster|mail-daemon|bounce[s]?)(?:[+.\-]|@)")
_BOUNCE_SUBJECTS: tuple[str, ...] = (
    "undeliverable", "delivery status notification", "mail delivery failed", "returned mail",
    "delivery has failed", "no se puede entregar", "no se pudo entregar", "entrega fallida",
    "mensaje no entregado", "failure notice", "undelivered mail", "returned to sender",
    "mail delivery subsystem",
)
_AUTO_REPLY_SUBJECTS: tuple[str, ...] = (
    "respuesta automatica", "automatic reply", "auto reply", "autoreply", "auto-reply",
    "out of office", "out of the office", "fuera de la oficina", "fuera de oficina",
    "acuse de recibo", "read receipt", "leido:",
)
#: An absence notice whose subject is only «RE: <our campaign>» says so in its first lines. Read
#: on the reply's own text (quoted history cut), and only its opening: a person who mentions a
#: trip after asking for a price is still a person (no quote word may precede the phrase).
_AUTO_REPLY_BODY_OPENINGS: tuple[str, ...] = (
    "fuera de la oficina", "fuera de oficina", "out of office", "out of the office",
    "me encuentro de vacaciones", "estare ausente", "estoy ausente", "me encuentro ausente",
)
_AUTO_REPLY_BODY_CHARS = 300
#: Absence notices start with the word; anywhere else «ausencia» can be a real subject.
_AUTO_REPLY_PREFIXES: tuple[str, ...] = ("ausencia", "ausente", "estare fuera", "estoy fuera")
_UNSUBSCRIBE_SUBJECTS: frozenset[str] = frozenset({"baja", "remover", "unsubscribe", "remove"})
_TENDER_DOMAINS: frozenset[str] = frozenset({"wherex.com", "mercadopublico.cl", "chilecompra.cl"})
_TENDER_SUBJECTS: tuple[str, ...] = ("nueva licitacion", "licitacion publicada", "nueva oportunidad de negocio")
_NOTIFICATION_SENDERS = re.compile(
    # Deliberately narrow: info@, ventas@ or soporte@ at an institution is often a person asking.
    r"^(?:no[\-_.]?reply|do[\-_.]?not[\-_.]?reply|notifica(?:tion|cion|ciones)s?|alerts?|news(?:letter)?|"
    r"marketing|mailer)(?:[+.\-]|@)"
)
_BULK_LABELS: frozenset[str] = frozenset({"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"})
_UPDATE_LABELS: frozenset[str] = frozenset({"CATEGORY_UPDATES"})
#: A CN quote number as OrigenLab prints it: «CN 1234», «CN-1234-26», «cn_998».
_QUOTE_NUMBER = re.compile(r"(?<![a-z0-9])cn[\s_\-]?(\d{3,6})(?![0-9])")


@dataclass(frozen=True)
class TriageInput:
    direction: str  # 'inbound' | 'outbound' (comms.message.direction)
    labels: tuple[str, ...]
    mail: MailText


@dataclass(frozen=True)
class RuleVerdict:
    triage_class: str
    needs_model: bool
    #: Short machine reasons, never message text: `header:auto-submitted`, `subject:quote_word` …
    reasons: tuple[str, ...]
    quote_numbers: tuple[str, ...] = ()
    purchase_order_numbers: tuple[str, ...] = ()
    sender_domain: str | None = None
    free_mail: bool = False
    signals: dict[str, bool] = field(default_factory=dict)


def _domain(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    return address.rsplit("@", 1)[1].lower()


def _local(address: str | None) -> str:
    return (address or "").lower()


def _contains_any(text: str, phrases: Sequence[str]) -> str | None:
    return next((p for p in phrases if p in text), None)


def _first_line_is_baja(body: str | None) -> bool:
    """The reply's first non-empty line is a bare BAJA/REMOVER: the instruction, followed by a
    signature («REMOVER\n\nSds.\nAna Pérez»). The W10 suppression grammar still refuses it (not
    standalone) — this only files the email as an unsubscribe reading for a person to confirm,
    instead of a quote request."""
    if not body:
        return False
    own = reply_text(body)
    first = next((line.strip() for line in own.split("\n") if line.strip()), "")
    return first.upper() in ACCEPTED_FORMS and first != own.strip()


def _machine_header_reason(headers: dict[str, str]) -> str | None:
    auto = headers.get("auto-submitted", "").lower()
    if auto and auto != "no":
        return "header:auto-submitted"
    for name in ("x-autoreply", "x-autorespond"):
        if name in headers:
            return f"header:{name}"
    if headers.get("precedence", "").lower() == "auto_reply":
        return "header:precedence-auto_reply"
    return None


def _bulk_header_reason(headers: dict[str, str]) -> str | None:
    if headers.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return "header:precedence-bulk"
    for name in ("list-unsubscribe", "list-id", "feedback-id", "x-campaign"):
        if name in headers:
            return f"header:{name}"
    return None


def classify(item: TriageInput) -> RuleVerdict:
    mail = item.mail
    subject = fold(mail.subject)
    body = fold(mail.body)
    names = [fold(n) for n in mail.attachment_names]
    sender = _local(mail.sender)
    domain = _domain(mail.sender)
    labels = set(item.labels)
    base = {"sender_domain": domain, "free_mail": domain in FREE_MAIL_DOMAINS if domain else False}

    def verdict(cls: str, *reasons: str, **extra: object) -> RuleVerdict:
        return RuleVerdict(triage_class=cls, needs_model=cls in NEEDS_MODEL, reasons=tuple(reasons),
                           **base, **extra)  # type: ignore[arg-type]

    if item.direction == "outbound" or (domain is not None and domain in OWN_DOMAINS):
        return verdict("outbound", "direction:outbound")

    if "multipart/report" in mail.content_types or "message/delivery-status" in mail.content_types:
        return verdict("bounce", "mime:delivery-report")
    if _BOUNCE_SENDERS.match(sender):
        return verdict("bounce", "sender:mailer-daemon")
    if "x-failed-recipients" in mail.machine_headers:
        return verdict("bounce", "header:x-failed-recipients")
    if (phrase := _contains_any(subject, _BOUNCE_SUBJECTS)) is not None:
        return verdict("bounce", f"subject:{phrase}")

    if (reason := _machine_header_reason(mail.machine_headers)) is not None:
        return verdict("auto_reply", reason)
    if (phrase := _contains_any(subject, _AUTO_REPLY_SUBJECTS)) is not None:
        return verdict("auto_reply", f"subject:{phrase}")
    if subject.startswith(_AUTO_REPLY_PREFIXES):
        return verdict("auto_reply", "subject:absence_prefix")
    opening = fold(reply_text(mail.body or ""))[:_AUTO_REPLY_BODY_CHARS]
    if (phrase := _contains_any(opening, _AUTO_REPLY_BODY_OPENINGS)) is not None \
            and _contains_any(opening[:opening.index(phrase)], QUOTE_REQUEST_WORDS) is None:
        return verdict("auto_reply", f"body:{phrase}")

    if "text/calendar" in mail.content_types or "application/ics" in mail.content_types:
        return verdict("calendar", "mime:text/calendar")

    if subject.strip(" .!") in _UNSUBSCRIBE_SUBJECTS:
        return verdict("unsubscribe", "subject:baja_word")
    if mail.body and classify_reply(mail.body).accepted:
        return verdict("unsubscribe", "body:baja_grammar")
    if _first_line_is_baja(mail.body):
        return verdict("unsubscribe", "body:baja_first_line")

    if (domain in _TENDER_DOMAINS or (domain or "").endswith(tuple("." + d for d in _TENDER_DOMAINS))) \
            and _contains_any(subject, _TENDER_SUBJECTS + ("licitacion",)):
        return verdict("tender_notice", "sender:tender_platform")
    if _contains_any(subject, _TENDER_SUBJECTS) is not None:
        return verdict("tender_notice", "subject:tender_alert")

    text = f"{subject}\n{body}"
    quote_numbers = tuple(dict.fromkeys(m.group(1) for src in (subject, body, *names)
                                        for m in _QUOTE_NUMBER.finditer(src)))
    po_numbers = tuple(dict.fromkeys(m.group(1) for src in (subject, body, *names)
                                     for m in PURCHASE_ORDER_NUMBER.finditer(src)))
    po_file = next((n for n in names if PURCHASE_ORDER_FILENAME.match(n)), None)
    po_phrase = _contains_any(text, PURCHASE_ORDER_PHRASES)
    lost = _contains_any(text, LOST_PHRASES)
    quote_word_subject = _contains_any(subject, QUOTE_REQUEST_WORDS)
    quote_word_body = _contains_any(body, QUOTE_REQUEST_WORDS)
    signals = {
        "purchase_order": bool(po_phrase or po_file or PURCHASE_ORDER_NUMBER.search(subject)),
        "lost": bool(lost),
        "quote_number": bool(quote_numbers),
        "quote_word": bool(quote_word_subject or quote_word_body),
        "attachments": bool(mail.attachment_names),
    }
    extra = {"quote_numbers": quote_numbers, "purchase_order_numbers": po_numbers, "signals": signals}

    po_in_subject = bool(PURCHASE_ORDER_NUMBER.search(subject))
    if po_phrase or po_file or po_in_subject:
        reason = "attachment:oc" if po_file else "text:purchase_order_phrase" if po_phrase else "subject:oc_number"
        return verdict("purchase_order", reason, **extra)
    if lost:
        return verdict("lost", "text:lost_phrase", **extra)
    if quote_numbers:
        return verdict("quote_followup", "text:cn_quote_number", **extra)
    if quote_word_subject or quote_word_body:
        where = "subject" if quote_word_subject else "body"
        return verdict("quote_request", f"{where}:quote_word", **extra)

    if (reason := _bulk_header_reason(mail.machine_headers)) is not None:
        return verdict("bulk", reason, **extra)
    if labels & _BULK_LABELS:
        return verdict("bulk", "label:" + sorted(labels & _BULK_LABELS)[0].lower(), **extra)
    if _NOTIFICATION_SENDERS.match(sender):
        return verdict("notification", "sender:no-reply", **extra)
    if labels & _UPDATE_LABELS:
        return verdict("notification", "label:category_updates", **extra)

    if not mail.body and not mail.subject and not mail.attachment_names:
        return verdict("empty", "content:none", **extra)
    return verdict("business_other", "person:unclassified", **extra)


__all__ = ["CLASSES", "NEEDS_MODEL", "NOISE_CLASSES", "ROUTED_CLASSES", "SIGNAL_CLASSES", "TRIAGE_VERSION", "RuleVerdict",
           "TriageInput", "classify"]
