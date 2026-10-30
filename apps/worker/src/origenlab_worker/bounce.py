"""NDR analysis for V2 hard-bounce suppression.

The grammar and the A-E review batches are the reviewed V1 implementations. V2 stores only the
classification result; the raw delivery report remains in the captured .eml.

**What blocks by itself** (owner 2026-10-29, after approving these kinds by hand on 19 and 21
Oct): one exact recipient whose report says, in so many words, that the address cannot exist —

* ``no_such_user`` — Batch A, the mailbox does not exist (5.1.1);
* ``not_in_directory`` — Microsoft 365 «550 5.4.1 Recipient address rejected: Access denied»
  (directory-based edge blocking: the person is not in that organization);
* ``domain_not_found`` — the domain does not exist (NXDOMAIN);
* ``mailbox_disabled`` — the account is disabled or inactive (5.2.1).

A delay, a temporary (4.x.x) failure, a full mailbox, a mail loop, a spam/policy rejection and a
report naming more than one recipient never block: those addresses may work again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser

from origenlab_email_pipeline.ndr_bounce_extraction import (
    bounce_suppression_code_from_ndr_text,
    extract_failed_recipients_from_ndr,
)
from origenlab_email_pipeline.qa.ndr_review_queue import classify_ndr_candidate
from origenlab_worker.mail_text import html_to_text

#: 2 (2026-10-29): the four hard kinds above block; v1 blocked Batch A only. A bump makes the
#: triage sweep re-read every bounce of the last ``DEFAULT_SINCE_DAYS`` under the new rule.
DELIVERY_FAILURE_VERSION = 2
DELIVERY_FAILURE_VALUE_NORM = f"delivery:v{DELIVERY_FAILURE_VERSION}"


@dataclass(frozen=True)
class BounceAnalysis:
    reason_code: str
    batch: str
    batch_reason: str
    recipient_count: int
    auto_block_addresses: tuple[str, ...]
    block_reason: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "delivery_failure_version": DELIVERY_FAILURE_VERSION,
            "reason_code": self.reason_code,
            "batch": self.batch,
            "batch_reason": self.batch_reason,
            "recipient_count": self.recipient_count,
            "block_reason": self.block_reason,
            # Only eligible hard-bounce addresses are retained in the derived assertion.
            # Ambiguous/temporary recipients stay only in the raw .eml.
            "auto_block_addresses": list(self.auto_block_addresses),
        }


def _part_text(part: Message) -> str:
    ctype = part.get_content_type()
    if ctype in ("message/delivery-status", "message/global-delivery-status"):
        payload = part.get_payload()
        if isinstance(payload, list):
            return "\n".join(p.as_string(policy=policy.default) for p in payload)
        return str(payload or "")

    if ctype not in ("text/plain", "text/html"):
        return ""
    try:
        value = part.get_content()
    except (LookupError, UnicodeError, ValueError, AttributeError):
        raw = part.get_payload(decode=True) or b""
        value = raw.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return ""
    return html_to_text(value) if ctype == "text/html" else value


def diagnostic_text(raw: bytes) -> tuple[str, str]:
    """Return (subject, NDR diagnostic text) without traversing the attached original message."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    subject = " ".join(str(msg.get("Subject", "") or "").split())
    pieces: list[str] = []

    failed = " ".join(str(msg.get("X-Failed-Recipients", "") or "").split())
    if failed:
        pieces.append(f"X-Failed-Recipients: {failed}")

    # RFC 3464 reports normally contain: human-readable text, message/delivery-status,
    # then message/rfc822 (the original email). Deliberately never inspect the third part.
    if msg.get_content_type() == "multipart/report":
        payload = msg.get_payload()
        if isinstance(payload, list):
            for part in payload:
                if part.get_content_type() == "message/rfc822":
                    continue
                text = _part_text(part)
                if text:
                    pieces.append(text)
    else:
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            text = _part_text(body)
            if text:
                pieces.append(text)

    return subject, "\n".join(pieces)


BLOCK_REASONS = ("no_such_user", "not_in_directory", "domain_not_found", "mailbox_disabled")
_HOLD_BATCH_REASONS = frozenset({"delay_dsn_excluded", "multi_recipient_uncertain", "mailbox_full_or_quota"})
_TEMPORARY = re.compile(r"\bstatus:\s*4\.\d+\.\d+|\b4\d\d[ -]4\.\d+\.\d+")
#: A spam / blocklist filter: the address may be fine, the message was refused.
_SPAM = re.compile(r"\bspam\b|blacklist|blocklist|blocked using")
#: Policy rejections (5.7.x) hold unless the same report says the account itself is gone.
_POLICY = re.compile(r"\b5\.7\.\d+\b")
_NO_SUCH_USER = re.compile(
    r"account that you tried to reach does not exist|no such (?:user|recipient)|user unknown|unknown user|"
    r"invalid recipient|address not found|recipient not found|cuenta de destino no existe|"
    r"(?:direcci[oó]n|cuenta) no existe|no existe dicha persona"
)
_DISABLED = re.compile(
    r"account that you tried to reach is (?:inactive|disabled)|mailbox is disabled|"
    r"account (?:has been |is )?disabled|\b5\.2\.1\b|cuenta (?:est[aá] )?(?:deshabilitada|desactivada|inactiva)"
)
_NOT_IN_DIRECTORY = re.compile(r"\b5\.4\.1\b.{0,40}recipient address rejected|recipient address rejected: access denied")
_DOMAIN_NOT_FOUND = re.compile(
    r"nxdomain|domain name not found|no encontramos el dominio|null mx|doesn't receive email|"
    r"lookup of \S+ had no relevant answers"
)


def block_reason_for(batch: str, batch_reason: str, text: str) -> str | None:
    """The hard kind this report proves, or None when the address may work again.

    Reports wrap their diagnostic over several lines, so the text is matched with its whitespace
    collapsed.
    """
    low = " ".join(text.lower().split())
    if batch_reason in _HOLD_BATCH_REASONS or _TEMPORARY.search(low) or _SPAM.search(low):
        return None
    gone = batch == "A" or bool(_NO_SUCH_USER.search(low))
    disabled = bool(_DISABLED.search(low))
    if _POLICY.search(low) and not (gone or disabled):
        return None
    if gone:
        return "no_such_user"
    if disabled:
        return "mailbox_disabled"
    if _NOT_IN_DIRECTORY.search(low):
        return "not_in_directory"
    if _DOMAIN_NOT_FOUND.search(low):
        return "domain_not_found"
    return None


def analyze_bounce(raw: bytes) -> BounceAnalysis:
    subject, blob = diagnostic_text(raw)
    recipients = extract_failed_recipients_from_ndr(blob)
    reason_code = bounce_suppression_code_from_ndr_text(f"{subject}\n{blob}")
    batch, batch_reason = classify_ndr_candidate(
        proposed_code=reason_code,
        subject=subject,
        body_blob=blob,
        multi_recipient_uncertain=len(recipients) > 1,
    )
    reason = block_reason_for(batch, batch_reason, f"{subject}\n{blob}") if len(recipients) == 1 else None
    return BounceAnalysis(
        reason_code=reason_code,
        batch=batch,
        batch_reason=batch_reason,
        recipient_count=len(recipients),
        auto_block_addresses=tuple(recipients) if reason else (),
        block_reason=reason,
    )


__all__ = [
    "BLOCK_REASONS",
    "BounceAnalysis",
    "DELIVERY_FAILURE_VALUE_NORM",
    "DELIVERY_FAILURE_VERSION",
    "analyze_bounce",
    "block_reason_for",
    "diagnostic_text",
]
