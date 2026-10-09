"""Conservative NDR analysis for V2 hard-bounce suppression.

The grammar and the A-E safety policy are the reviewed V1 implementations. V2 stores only the
classification result; the raw delivery report remains in the captured .eml. Only one exact
recipient in Batch A ("no such user" with explicit final evidence) is eligible for automatic
blocking. Everything else is evidence only.
"""

from __future__ import annotations

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

DELIVERY_FAILURE_VERSION = 1
DELIVERY_FAILURE_VALUE_NORM = f"delivery:v{DELIVERY_FAILURE_VERSION}"


@dataclass(frozen=True)
class BounceAnalysis:
    reason_code: str
    batch: str
    batch_reason: str
    recipient_count: int
    auto_block_addresses: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "delivery_failure_version": DELIVERY_FAILURE_VERSION,
            "reason_code": self.reason_code,
            "batch": self.batch,
            "batch_reason": self.batch_reason,
            "recipient_count": self.recipient_count,
            # Only eligible hard-bounce addresses are retained in the derived assertion.
            # Ambiguous/temporary recipients stay only in the raw .eml.
            "auto_block_addresses": list(self.auto_block_addresses),
        }


def _part_text(part: Message) -> str:
    ctype = part.get_content_type()
    if ctype == "message/delivery-status":
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
    eligible = tuple(recipients) if batch == "A" and len(recipients) == 1 else ()
    return BounceAnalysis(
        reason_code=reason_code,
        batch=batch,
        batch_reason=batch_reason,
        recipient_count=len(recipients),
        auto_block_addresses=eligible,
    )


__all__ = [
    "BounceAnalysis",
    "DELIVERY_FAILURE_VALUE_NORM",
    "DELIVERY_FAILURE_VERSION",
    "analyze_bounce",
    "diagnostic_text",
]
