"""
Tests for walk_attachments inline-vs-attachment classification.

Gmail stamps a Content-ID (and X-Attachment-Id) on every attachment it sends,
so a Content-ID alone must not mark a part inline: that flagged every 2026
quote PDF as an inline image and hid it from every ``is_inline = 0`` filter.
"""
from __future__ import annotations

import base64
import email
import hashlib

from origenlab_email_pipeline.parse_mbox import walk_attachments

PDF_BYTES = b"%PDF-1.4\n% CN05118 quote\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-logo"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _parse(raw: str) -> email.message.Message:
    return email.message_from_bytes(raw.encode("ascii"))


# A message as Gmail sends it: multipart/mixed wrapping a multipart/related
# body (HTML + signature logo) and a PDF attachment. Both parts carry a
# Content-ID; only the logo is referenced from the HTML.
GMAIL_QUOTE = f"""\
From: Ventas <ventas@example.cl>
To: cliente@example.org
Subject: Cotizacion CN05118
Message-ID: <CAF-test-1@mail.gmail.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="mixed"

--mixed
Content-Type: multipart/related; boundary="related"

--related
Content-Type: multipart/alternative; boundary="alt"

--alt
Content-Type: text/plain; charset="UTF-8"

Adjunto cotizacion.

--alt
Content-Type: text/html; charset="UTF-8"

<div>Adjunto cotizacion.<img src="cid:ii_logo1"></div>

--alt--

--related
Content-Type: image/png; name="logo.png"
Content-Disposition: inline; filename="logo.png"
Content-Transfer-Encoding: base64
Content-ID: <ii_logo1>
X-Attachment-Id: ii_logo1

{_b64(PNG_BYTES)}
--related--

--mixed
Content-Type: application/pdf; name="CN05118-26.pdf"
Content-Disposition: attachment; filename="CN05118-26.pdf"
Content-Transfer-Encoding: base64
Content-ID: <f_mfq1abcd0>
X-Attachment-Id: f_mfq1abcd0

{_b64(PDF_BYTES)}
--mixed--
"""


def _by_filename(parts: list[dict]) -> dict[str, dict]:
    return {p["filename"]: p for p in parts}


def test_gmail_attachment_with_content_id_is_not_inline() -> None:
    parts = _by_filename(walk_attachments(_parse(GMAIL_QUOTE)))
    pdf = parts["CN05118-26.pdf"]
    assert pdf["content_id"] == "<f_mfq1abcd0>"
    assert pdf["is_inline"] is False
    assert pdf["sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
    assert pdf["size_bytes"] == len(PDF_BYTES)


def test_gmail_inline_signature_image_stays_inline() -> None:
    parts = _by_filename(walk_attachments(_parse(GMAIL_QUOTE)))
    assert parts["logo.png"]["is_inline"] is True


def test_gmail_message_row_set_and_order_unchanged() -> None:
    parts = walk_attachments(_parse(GMAIL_QUOTE))
    assert [(p["part_index"], p["filename"]) for p in parts] == [
        (0, "logo.png"),
        (1, "CN05118-26.pdf"),
    ]


def _single_part(headers: str, payload: bytes, *, container: str = "mixed") -> str:
    return f"""\
From: a@example.cl
To: b@example.org
Subject: t
MIME-Version: 1.0
Content-Type: multipart/{container}; boundary="b"

--b
Content-Type: text/html; charset="UTF-8"

<p>hola</p>

--b
{headers}
Content-Transfer-Encoding: base64

{_b64(payload)}
--b--
"""


def test_related_image_with_content_id_and_no_disposition_is_inline() -> None:
    # Outlook-style embedded image: no Content-Disposition, only a Content-ID
    # inside multipart/related.
    raw = _single_part(
        'Content-Type: image/png; name="image001.png"\nContent-ID: <image001.png@01D>',
        PNG_BYTES,
        container="related",
    )
    [part] = walk_attachments(_parse(raw))
    assert part["is_inline"] is True


def test_pdf_with_content_id_and_no_disposition_in_mixed_is_attachment() -> None:
    raw = _single_part(
        'Content-Type: application/pdf; name="cotizacion.pdf"\nContent-ID: <pdf1>',
        PDF_BYTES,
    )
    [part] = walk_attachments(_parse(raw))
    assert part["is_inline"] is False


def test_explicit_inline_disposition_is_inline() -> None:
    raw = _single_part(
        'Content-Type: image/png; name="x.png"\nContent-Disposition: inline; filename="x.png"',
        PNG_BYTES,
    )
    [part] = walk_attachments(_parse(raw))
    assert part["is_inline"] is True


def test_inline_substring_in_filename_does_not_mark_attachment_inline() -> None:
    raw = _single_part(
        'Content-Type: application/pdf; name="inline_specs.pdf"\n'
        'Content-Disposition: attachment; filename="inline_specs.pdf"',
        PDF_BYTES,
    )
    [part] = walk_attachments(_parse(raw))
    assert part["is_inline"] is False


def test_bare_image_without_disposition_or_cid_is_inline() -> None:
    # Unchanged rule: an unnamed image with no attachment signal is inline.
    raw = _single_part("Content-Type: image/gif", PNG_BYTES)
    [part] = walk_attachments(_parse(raw))
    assert part["is_inline"] is True


def test_content_id_only_part_is_still_collected() -> None:
    # Row-set stability: a part collected before (only because it had a
    # Content-ID) is still collected; only its flag may change.
    raw = _single_part("Content-Type: application/octet-stream\nContent-ID: <blob1>", b"xyz")
    [part] = walk_attachments(_parse(raw))
    assert part["content_id"] == "<blob1>"
    assert part["is_inline"] is True
