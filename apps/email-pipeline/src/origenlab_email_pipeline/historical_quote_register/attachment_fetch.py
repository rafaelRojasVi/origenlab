"""Recover original attachment bytes for quote candidates whose archive row
has no saved_path (design §2 — attachments.saved_path is NULL repo-wide).

Two sources, matching the archive's two provenance types:
- Gmail-era (2026-03+): targeted read-only IMAP re-fetch, RFC822-only for the
  specific candidate message_ids — never a folder-wide re-ingest.
- mbox-era (2017-2020): re-parse the on-disk mbox tree, no network call.

Both paths verify the recovered payload's SHA256 against attachments.sha256
(already known from the canonical archive, read-only) before returning
anything — this is the integrity check design §3.4 requires, and it is also
what makes this code independent of any assumption about MIME part ordering
or a persisted Gmail UID: we don't need to agree with the original ingest's
part_index, we just need *a* part whose bytes hash to the expected value.
"""

from __future__ import annotations

import hashlib
import imaplib
from email.message import Message

from origenlab_email_pipeline.ingest.gmail_imap import (
    fetch_message_id_headers_for_uids,
    fetch_rfc822,
    imap_select_folder,
    message_from_bytes,
    normalize_message_id,
    search_uids,
)
from origenlab_email_pipeline.parse_mbox import open_mbox


def build_message_id_uid_map(
    mail: imaplib.IMAP4_SSL,
    *,
    folder: str,
    since_days: int | None = None,
) -> dict[str, bytes]:
    typ, _ = imap_select_folder(mail, folder, readonly=True)
    if typ != "OK":
        raise imaplib.IMAP4.error(f"Could not select folder {folder!r} (read-only)")
    uids = search_uids(mail, since_days=since_days)
    raw_map = fetch_message_id_headers_for_uids(mail, uids)
    result: dict[str, bytes] = {}
    for uid, mid in raw_map.items():
        norm = normalize_message_id(mid)
        if norm:
            result[norm] = uid
    return result


def _find_payload_by_sha256(msg: Message, expected_sha256: str) -> bytes | None:
    for part in msg.walk():
        payload = part.get_payload(decode=True)
        if not isinstance(payload, (bytes, bytearray)) or not payload:
            continue
        if hashlib.sha256(payload).hexdigest() == expected_sha256:
            return bytes(payload)
    return None


def recover_gmail_attachment_bytes(
    mail: imaplib.IMAP4_SSL,
    *,
    uid: bytes,
    expected_sha256: str,
) -> bytes | None:
    raw = fetch_rfc822(mail, uid)
    if not raw:
        return None
    msg = message_from_bytes(raw)
    return _find_payload_by_sha256(msg, expected_sha256)


def _find_message_by_id(mbox, message_id: str) -> Message | None:
    target = normalize_message_id(message_id)
    for msg in mbox:
        if normalize_message_id(msg.get("Message-ID")) == target:
            return msg
    return None


def recover_mbox_attachment_bytes(
    *, mbox_source_file: str, message_id: str, expected_sha256: str,
) -> bytes | None:
    mbox = open_mbox(mbox_source_file)
    if mbox is None:
        return None
    msg = _find_message_by_id(mbox, message_id)
    if msg is None:
        return None
    return _find_payload_by_sha256(msg, expected_sha256)
