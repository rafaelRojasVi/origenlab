from __future__ import annotations

import email.message
import hashlib
import mailbox

from origenlab_email_pipeline.historical_quote_register.attachment_fetch import (
    build_message_id_uid_map,
    recover_gmail_attachment_bytes,
    recover_mbox_attachment_bytes,
)


class FakeImap:
    """Minimal stand-in for imaplib.IMAP4_SSL — only the .select/.uid surface
    gmail_imap.py's helpers actually call."""

    def __init__(self, *, uid_to_message_id: dict[bytes, str], uid_to_rfc822: dict[bytes, bytes]):
        self._uid_to_message_id = uid_to_message_id
        self._uid_to_rfc822 = uid_to_rfc822

    def select(self, mailbox, readonly=False):
        assert readonly is True, "must always select read-only"
        return "OK", [b"1"]

    def _quote(self, arg):
        # Mirrors imaplib.IMAP4._quote exactly: imap_select_folder() calls
        # mail._quote(folder) for any mailbox name that isn't "simple"
        # (e.g. "[Gmail]/Enviados" contains brackets and a slash), so a
        # faithful fake must implement it too.
        arg = arg.replace("\\", "\\\\")
        arg = arg.replace('"', '\\"')
        return '"' + arg + '"'

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(self._uid_to_message_id.keys())]
        if command == "FETCH":
            uid_arg = args[0]
            requested = uid_arg.split(b",") if isinstance(uid_arg, bytes) else [uid_arg]
            data = []
            for uid in requested:
                if uid in self._uid_to_message_id:
                    meta = f"{uid.decode()} (UID {uid.decode()} BODY[HEADER.FIELDS (MESSAGE-ID)] {{2}}".encode()
                    mid_header = f"Message-ID: {self._uid_to_message_id[uid]}\r\n".encode()
                    data.append((meta, mid_header))
                elif uid in self._uid_to_rfc822:
                    data.append((b"", self._uid_to_rfc822[uid]))
            return "OK", data
        raise AssertionError(f"unexpected uid command {command}")


def _rfc822_with_attachment(payload: bytes, *, message_id: str = "<sent-1@origenlab.cl>") -> bytes:
    msg = email.message.EmailMessage()
    msg["Subject"] = "Cotización"
    msg["Message-ID"] = message_id
    msg.set_content("cuerpo")
    msg.add_attachment(payload, maintype="application", subtype="pdf", filename="cot.pdf")
    return bytes(msg)


def _build_real_mbox_file(path, *messages: bytes) -> None:
    """Write a real on-disk mbox file (no mocking of open_mbox/mailbox) so the
    mbox-era recovery path is exercised against genuine mbox parsing, matching
    what scripts/ingest/02_mbox_to_sqlite.py's open_mbox() will actually read."""
    box = mailbox.mbox(str(path))
    box.lock()
    try:
        for raw in messages:
            box.add(email.message_from_bytes(raw))
        box.flush()
    finally:
        box.unlock()
        box.close()


def test_build_message_id_uid_map_normalizes_and_maps():
    fake = FakeImap(
        uid_to_message_id={b"101": "<sent-1@origenlab.cl>"},
        uid_to_rfc822={},
    )
    mapping = build_message_id_uid_map(fake, folder="[Gmail]/Enviados")
    assert mapping == {"<sent-1@origenlab.cl>": b"101"}


def test_recover_gmail_attachment_bytes_verifies_sha256():
    payload = b"%PDF-1.4 fake quote bytes"
    expected_sha = hashlib.sha256(payload).hexdigest()
    raw = _rfc822_with_attachment(payload)
    fake = FakeImap(uid_to_message_id={}, uid_to_rfc822={b"101": raw})

    recovered = recover_gmail_attachment_bytes(fake, uid=b"101", expected_sha256=expected_sha)
    assert recovered == payload


def test_recover_gmail_attachment_bytes_rejects_hash_mismatch():
    payload = b"%PDF-1.4 fake quote bytes"
    raw = _rfc822_with_attachment(payload)
    fake = FakeImap(uid_to_message_id={}, uid_to_rfc822={b"101": raw})

    recovered = recover_gmail_attachment_bytes(fake, uid=b"101", expected_sha256="0" * 64)
    assert recovered is None


def test_recover_mbox_attachment_bytes_verifies_sha256(tmp_path):
    payload = b"%PDF-1.4 legacy mbox quote bytes"
    expected_sha = hashlib.sha256(payload).hexdigest()
    mbox_path = tmp_path / "Elementos enviados"
    _build_real_mbox_file(
        mbox_path,
        _rfc822_with_attachment(payload, message_id="<legacy-1@labdelivery.cl>"),
    )

    recovered = recover_mbox_attachment_bytes(
        mbox_source_file=str(mbox_path),
        message_id="<legacy-1@labdelivery.cl>",
        expected_sha256=expected_sha,
    )
    assert recovered == payload


def test_recover_mbox_attachment_bytes_rejects_hash_mismatch(tmp_path):
    payload = b"%PDF-1.4 legacy mbox quote bytes"
    mbox_path = tmp_path / "Elementos enviados"
    _build_real_mbox_file(
        mbox_path,
        _rfc822_with_attachment(payload, message_id="<legacy-1@labdelivery.cl>"),
    )

    recovered = recover_mbox_attachment_bytes(
        mbox_source_file=str(mbox_path),
        message_id="<legacy-1@labdelivery.cl>",
        expected_sha256="0" * 64,
    )
    assert recovered is None


def test_recover_mbox_attachment_bytes_message_id_not_found_returns_none(tmp_path):
    payload = b"%PDF-1.4 legacy mbox quote bytes"
    expected_sha = hashlib.sha256(payload).hexdigest()
    mbox_path = tmp_path / "Elementos enviados"
    _build_real_mbox_file(
        mbox_path,
        _rfc822_with_attachment(payload, message_id="<legacy-1@labdelivery.cl>"),
    )

    recovered = recover_mbox_attachment_bytes(
        mbox_source_file=str(mbox_path),
        message_id="<does-not-exist@labdelivery.cl>",
        expected_sha256=expected_sha,
    )
    assert recovered is None


def test_recover_mbox_attachment_bytes_matches_by_normalized_message_id(tmp_path):
    """message_id lookup must be case/whitespace-insensitive like normalize_message_id,
    since canonical archive rows and mbox headers may differ in casing."""
    payload = b"%PDF-1.4 legacy mbox quote bytes"
    expected_sha = hashlib.sha256(payload).hexdigest()
    mbox_path = tmp_path / "Elementos enviados"
    _build_real_mbox_file(
        mbox_path,
        _rfc822_with_attachment(payload, message_id="<Legacy-1@LabDelivery.cl>"),
    )

    recovered = recover_mbox_attachment_bytes(
        mbox_source_file=str(mbox_path),
        message_id=" <legacy-1@labdelivery.cl> ",
        expected_sha256=expected_sha,
    )
    assert recovered == payload
