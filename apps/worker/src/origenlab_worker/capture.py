"""One raw Gmail message → the rows Phase 4a writes and its evidence payload.

Pure: no network, no database. V1 functions come through :mod:`origenlab_worker.v1_reuse`
(owner decision D4), so a captured payload is computed the way the 92 staged `gmail_message`
records were.

**The payload has exactly the 21 keys of every staged record** (`PAYLOAD_KEYS`, measured from the
two 2026-09-26 import manifests). Where each comes from:

* `subject_raw`, `sender`, `recipients`, `sent_at`, `documents` — V1's parser
  (`message_from_bytes`, policy.default) with `recipients_header`, `date_iso_from_msg` and
  `walk_attachments`, exactly as V1's Gmail ingest stores them;
* `direction_hint` — V1 `classify_send_direction`, on outbound mail only. It reads recipients, so
  on received mail it would call every message `internal_only`; inbound → null;
* `documents[].cn_tokens`, `proposed_quote_numbers`, `quote_number_convention`, `tier` — the
  2026-09-24 quote-evidence staging script, which lives outside the repository; its CN rule and
  its strings are reproduced verbatim here (`CN_TOKEN`, `QUOTE_NUMBER_CONVENTION`, the G1/G2 tiers);
* `intake_class`, `gmail_labels` — what `quote_crm_import_plan._manifest_record` set on every
  staged record (`apps/api/src/origenlab_api/v2/quote_crm_import_plan.py:265-268`):
  `primary_evidence`, and the label ids themselves;
* `opportunity_id`, `quote_number`, `sent_revision`, `source_email_id`,
  `staging_source_record_sha256`, and each document's `source_email_id`, `source_attachment_id`,
  `stored_path` — known only to a person or to the V1 replay: null.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from typing import Any

from origenlab_worker.v1_reuse import (
    classify_intake_folder,
    classify_send_direction,
    date_iso_from_msg,
    message_from_bytes,
    recipients_header,
    require_stageable_gmail_payload,
    walk_attachments,
)

PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "gmail_message_id", "gmail_thread_id", "rfc822_message_id", "gmail_label_ids",
        "gmail_labels", "raw_sha256", "intake_class", "tier", "direction_hint", "sent_at",
        "subject_raw", "sender", "recipients", "documents", "proposed_quote_numbers",
        "quote_number_convention", "quote_number", "opportunity_id", "sent_revision",
        "source_email_id", "staging_source_record_sha256",
    }
)
DOCUMENT_KEYS: frozenset[str] = frozenset(
    {
        "source_email_id", "source_attachment_id", "filename", "sha256", "cn_tokens",
        "bytes_hash_verified", "stored_path",
    }
)
QUOTE_NUMBER_CONVENTION = "CN<serial, zero-padded to 5> — proposed, requires operator confirmation"
CN_TOKEN = re.compile(r"(?i)(?<![A-Za-z0-9])CN[\s_-]?(\d{3,6})(?!\d)")
TIER_WITH_DOCUMENT = "G1_gmail_doc"
TIER_WITHOUT_DOCUMENT = "G2_gmail_no_doc"
INTAKE_CLASS = "primary_evidence"
#: V1 intake classes a capture never stores, by the run counter each lands in.
SKIPPED_INTAKE: Mapping[str, str] = {
    "metadata_only": "draft",
    "excluded_spam": "spam",
    "excluded_trash": "trash",
}
PARTICIPANT_HEADERS: tuple[tuple[str, str], ...] = (
    ("From", "from"), ("Sender", "sender"), ("Reply-To", "reply_to"),
    ("To", "to"), ("Cc", "cc"), ("Bcc", "bcc"),
)


class ParseFailed(Exception):
    """The message cannot become evidence. `code` is stored in `comms.message.parse_error`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Participant:
    role: str
    address_norm: str
    display_name: str | None


@dataclass(frozen=True)
class AttachmentMeta:
    part_index: int
    filename: str | None
    mime_type: str
    size_bytes: int
    sha256: str | None


@dataclass(frozen=True)
class Capture:
    provider_message_id: str
    provider_thread_id: str | None
    rfc822_message_id_norm: str | None
    direction: str
    internal_date: datetime
    subject: str | None
    labels: tuple[str, ...]
    participants: tuple[Participant, ...]
    attachments: tuple[AttachmentMeta, ...]
    payload: dict[str, Any]
    is_bulk_send: bool


@dataclass(frozen=True)
class UnparsedMessage:
    provider_message_id: str
    provider_thread_id: str | None
    rfc822_message_id_norm: str | None
    direction: str
    internal_date: datetime
    subject: str | None
    labels: tuple[str, ...]
    reason: str


def intake_skip_reason(label_ids: Sequence[str]) -> str | None:
    """`draft`, `spam` or `trash` when any label is one V1 intake excludes; else None.

    Label *ids*, never names: system ids (`DRAFT`, `SPAM`, `TRASH`, `INBOX`, `SENT`) are what the
    staging rule saw, and a user label named «Borradores 2026» must not exclude a message.
    """
    for label in label_ids:
        reason = SKIPPED_INTAKE.get(classify_intake_folder(label))
        if reason is not None:
            return reason
    return None


def cn_tokens(filename: str | None) -> list[str]:
    return sorted({f"CN{int(digits):05d}" for digits in CN_TOKEN.findall(filename or "")})


def clean_text(value: object) -> str | None:
    """Text PostgreSQL and jsonb accept: no NUL, no lone surrogate.

    V1's parser already turns undecodable 8-bit header bytes into U+FFFD — kept, as V1 stored it.
    """
    if value is None:
        return None
    text = str(value).replace("\x00", "")
    return text.encode("utf-8", "replace").decode("utf-8")


def normalize_rfc822_id(raw: str | None) -> str | None:
    """Bare and folded, as `apps/api/src/origenlab_api/v2/unsubscribe_replies.py:199` folds it.

    Without that function's shape check: an inbound id may be malformed and is stored as received
    (docs/DATA.md §6 — never an identity key).
    """
    value = (clean_text(raw) or "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    value = value.lower()[:998]
    return value or None


def is_document(part: Mapping[str, Any]) -> bool:
    """A named, non-empty part that is not an embedded picture.

    Deliberately not `is_inline`: on `main`, V1's `walk_attachments` marks every part that carries
    a Content-ID as inline, which hid 2026 quote PDFs (2026-09-24 coverage audit; the fix is on an
    unmerged branch). A picture counts only when it is sent as an attachment.
    """
    if not part.get("filename") or not part.get("sha256"):
        return False
    is_image = str(part.get("content_type") or "").startswith("image/")
    return not is_image or "attachment" in str(part.get("content_disposition") or "").lower()


def internal_datetime(internal_date_ms: int) -> datetime:
    return datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)


def _direction(label_ids: Sequence[str], from_address: str | None, mailbox_address: str) -> str:
    """Outbound when Gmail labels it SENT, or when it is From the mailbox itself.

    The From rule covers a copy of our own mail that arrives without a SENT label (a Bcc or a note
    to ourselves); the SENT label covers Send-As aliases, whose From is not the mailbox.
    """
    mine = from_address is not None and from_address.lower() == mailbox_address.lower()
    return "outbound" if "SENT" in label_ids or mine else "inbound"


def _label_names(label_ids: Sequence[str], names: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(clean_text(names.get(label, label)) or label for label in label_ids)


def _participants(msg: Any) -> tuple[Participant, ...]:
    found: dict[tuple[str, str], Participant] = {}
    for header, role in PARTICIPANT_HEADERS:
        for value in msg.get_all(header) or []:
            pairs = [(a.display_name, a.addr_spec) for a in getattr(value, "addresses", ())]
            if not pairs:
                pairs = getaddresses([str(value)])
            for name, address in pairs:
                norm = (clean_text(address) or "").strip().lower()
                if "@" not in norm or any(ch.isspace() for ch in norm):
                    continue
                found.setdefault((role, norm), Participant(role, norm, clean_text(name) or None))
    return tuple(found.values())


def build_capture(
    raw: bytes,
    *,
    gmail_message_id: str,
    gmail_thread_id: str | None,
    label_ids: Sequence[str],
    label_names: Mapping[str, str],
    internal_date_ms: int,
    mailbox_address: str,
) -> Capture:
    """Raises :class:`ParseFailed` when the message cannot become evidence (the run records it as
    `parse_failed` and keeps the `.eml`); raises V1's `ManifestRefused` if the payload would break
    the staging rule — a bug, never data, so the run stops."""
    try:
        msg = message_from_bytes(raw)
        if msg.get("From") is None:
            raise ParseFailed("no_from_header")
        participants = _participants(msg)
        subject = clean_text(msg.get("Subject"))
        sender = clean_text(msg.get("From"))
        recipients = clean_text(recipients_header(msg)) or ""
        rfc822 = (clean_text(msg.get("Message-ID")) or "").strip() or None
        parts = walk_attachments(msg)
        sent_at = date_iso_from_msg(msg)
        has_list_unsubscribe = msg.get("List-Unsubscribe") is not None
    except ParseFailed:
        raise
    except Exception as exc:  # the stdlib parser's rare refusals become a recorded parse failure
        raise ParseFailed(type(exc).__name__) from None

    internal = internal_datetime(internal_date_ms)
    from_address = next((p.address_norm for p in participants if p.role == "from"), None)
    direction = _direction(label_ids, from_address, mailbox_address)
    documents = [
        {
            "source_email_id": None,
            "source_attachment_id": None,
            "filename": clean_text(part["filename"]),
            "sha256": part["sha256"],
            "cn_tokens": cn_tokens(clean_text(part["filename"])),
            "bytes_hash_verified": True,
            "stored_path": None,
        }
        for part in parts
        if is_document(part)
    ]
    payload: dict[str, Any] = {
        "gmail_message_id": gmail_message_id,
        "gmail_thread_id": gmail_thread_id,
        "rfc822_message_id": rfc822,
        "gmail_label_ids": list(label_ids),
        "gmail_labels": list(label_ids),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "intake_class": INTAKE_CLASS,
        "tier": TIER_WITH_DOCUMENT if documents else TIER_WITHOUT_DOCUMENT,
        "direction_hint": (
            classify_send_direction(recipients=recipients, subject=subject)[0]
            if direction == "outbound"
            else None
        ),
        "sent_at": sent_at or internal.isoformat(),
        "subject_raw": subject,
        "sender": sender,
        "recipients": recipients,
        "documents": documents,
        "proposed_quote_numbers": sorted({t for d in documents for t in d["cn_tokens"]}),
        "quote_number_convention": QUOTE_NUMBER_CONVENTION,
        "quote_number": None,
        "opportunity_id": None,
        "sent_revision": None,
        "source_email_id": None,
        "staging_source_record_sha256": None,
    }
    require_stageable_gmail_payload(payload, f"gmail_message:{gmail_message_id}")
    return Capture(
        provider_message_id=gmail_message_id,
        provider_thread_id=gmail_thread_id,
        rfc822_message_id_norm=normalize_rfc822_id(rfc822),
        direction=direction,
        internal_date=internal,
        subject=subject,
        labels=_label_names(label_ids, label_names),
        participants=participants,
        attachments=tuple(
            AttachmentMeta(
                part_index=int(part["part_index"]),
                filename=clean_text(part["filename"]),
                mime_type=clean_text(part["content_type"]) or "application/octet-stream",
                size_bytes=int(part["size_bytes"] or 0),
                sha256=part["sha256"],
            )
            for part in parts
        ),
        payload=payload,
        is_bulk_send=direction == "outbound" and has_list_unsubscribe,
    )


def unparsed_message(
    *,
    gmail_message_id: str,
    gmail_thread_id: str | None,
    label_ids: Sequence[str],
    label_names: Mapping[str, str],
    internal_date_ms: int,
    headers: Mapping[str, str],
    mailbox_address: str,
    reason: str,
) -> UnparsedMessage:
    """The `comms.message` row for a message kept without evidence, from Gmail's metadata alone."""
    from_address = parseaddr(headers.get("from", ""))[1].strip().lower() or None
    return UnparsedMessage(
        provider_message_id=gmail_message_id,
        provider_thread_id=gmail_thread_id,
        rfc822_message_id_norm=normalize_rfc822_id(headers.get("message-id")),
        direction=_direction(label_ids, from_address, mailbox_address),
        internal_date=internal_datetime(internal_date_ms),
        subject=clean_text(headers.get("subject")),
        labels=_label_names(label_ids, label_names),
        reason=reason,
    )
