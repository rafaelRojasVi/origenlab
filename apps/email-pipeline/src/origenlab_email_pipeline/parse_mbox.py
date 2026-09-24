"""Parse mbox files: plain + HTML bodies, safe decoding."""

from __future__ import annotations

import mailbox
import re
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Literal
import hashlib

# Source type for body extraction (Phase 2.1)
BodySourceType = Literal["plain", "html", "mixed", "empty"]

_SKIP_HTML_TAGS = frozenset({"script", "style"})
_BLOCK_BREAK_TAGS = frozenset({"br", "hr", "p", "div", "tr", "li"})
_BLOCK_BREAK_END_TAGS = frozenset({"p", "div", "tr", "li", "hr"})


class _HtmlTextExtractor(HTMLParser):
    """Extract visible text from HTML without regex tag stripping."""

    def __init__(self, *, block_breaks: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._block_breaks = block_breaks
        self._skip_depth = 0

    def _append_word_separator(self) -> None:
        if self._block_breaks or self._skip_depth:
            return
        if self._parts and self._parts[-1] != " ":
            self._parts.append(" ")

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_HTML_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if self._block_breaks and lowered in _BLOCK_BREAK_TAGS:
            self._parts.append("\n")
        else:
            self._append_word_separator()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_HTML_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._block_breaks and lowered in _BLOCK_BREAK_END_TAGS:
            self._parts.append("\n")
        else:
            self._append_word_separator()

    def handle_startendtag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_HTML_TAGS:
            return
        if self._skip_depth:
            return
        if self._block_breaks and lowered in _BLOCK_BREAK_TAGS:
            self._parts.append("\n")
        else:
            self._append_word_separator()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data:
            self._parts.append(data)

    def handle_comment(self, data: str) -> None:
        return

    def get_text(self) -> str:
        return "".join(self._parts)


def _extract_html_text(html: str, *, block_breaks: bool = False) -> str:
    parser = _HtmlTextExtractor(block_breaks=block_breaks)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return unescape(html)
    return parser.get_text()


def html_to_text(html: str) -> str:
    """Cheap HTML → plain (no extra deps). Good enough for search / LLM context.
    Kept for backward compatibility; prefer html_to_text_improved for new code."""
    if not html or not html.strip():
        return ""
    text = unescape(_extract_html_text(html, block_breaks=False))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def decode_payload(raw: bytes | None, charset: str | None) -> str:
    if not raw:
        return ""
    cs = (charset or "utf-8").lower()
    if cs in ("ascii", "us-ascii"):
        return raw.decode("utf-8", errors="replace")
    try:
        return raw.decode(cs, errors="replace")
    except (LookupError, ValueError):
        return raw.decode("utf-8", errors="replace")


def _normalize_whitespace(text: str) -> str:
    """Collapse horizontal whitespace, preserve single newlines, collapse multiple blank lines."""
    if not text or not text.strip():
        return ""
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def html_to_text_improved(html: str) -> str:
    """Improved HTML → plain: remove scripts/styles, preserve line breaks from block elements."""
    if not html or not html.strip():
        return ""
    text = unescape(_extract_html_text(html, block_breaks=True))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _walk_parts(msg: Message) -> tuple[list[str], list[str]]:
    """Collect non-attachment text/plain and text/html parts."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if not payload or not isinstance(payload, bytes):
                continue
            text = decode_payload(payload, part.get_content_charset())
            if ctype == "text/plain" and text.strip():
                plain_parts.append(text)
            elif ctype == "text/html" and text.strip():
                html_parts.append(text)
    else:
        payload = msg.get_payload(decode=True)
        if payload and isinstance(payload, bytes):
            text = decode_payload(payload, msg.get_content_charset())
            ctype = msg.get_content_type()
            if ctype == "text/plain" and text.strip():
                plain_parts.append(text)
            elif ctype == "text/html" and text.strip():
                html_parts.append(text)

    return plain_parts, html_parts


def _decode_filename(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        parts = decode_header(raw)
        out: list[str] = []
        for part, charset in parts:
            if isinstance(part, bytes):
                out.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                out.append(part or "")
        name = "".join(out).strip()
        return name or None
    except Exception:
        return raw


def _walk_with_parent(msg: Message, parent_ctype: str | None = None):
    """Yield (part, parent content type) in the same order as ``msg.walk()``."""
    yield msg, parent_ctype
    if msg.is_multipart():
        ctype = (msg.get_content_type() or "").lower()
        for sub in msg.get_payload():
            yield from _walk_with_parent(sub, ctype)


def walk_attachments(msg: Message) -> list[dict]:
    """
    Collect attachment-like MIME parts with metadata.

    Classification rules (conservative):
    - Treat as attachment if:
      * Content-Disposition contains 'attachment', OR
      * filename is present, OR
      * content-type is a typical document/binary (pdf, office, spreadsheets, archives, etc.).
    - Collect as inline-like if Content-Disposition mentions 'inline', a
      Content-ID is present, or the part is an image (unchanged row set).
    - is_inline, from the disposition *type* first:
      * disposition type 'attachment' -> not inline, even with a Content-ID
        (Gmail stamps a Content-ID on every attachment it sends);
      * disposition type 'inline' -> inline;
      * no disposition: inline if it has a Content-ID inside
        multipart/related (a cid: body image), else inline only when no
        attachment signal fired.
    - Never treat main text/plain or text/html body parts as attachments.
    """
    out: list[dict] = []
    part_index = 0

    for part, parent_ctype in _walk_with_parent(msg):
        ctype = (part.get_content_type() or "").lower()
        disp_raw = part.get("Content-Disposition") or ""
        disp = disp_raw.lower()
        disp_type = part.get_content_disposition()
        cid = part.get("Content-ID")
        filename_raw = part.get_filename()
        filename = _decode_filename(filename_raw)

        # Skip the main text/plain or text/html body parts (handled elsewhere).
        if ctype.startswith("text/") and ("attachment" not in disp):
            continue

        # Decide attachment vs inline.
        is_attachment = False

        # Strong attachment signals.
        if "attachment" in disp:
            is_attachment = True
        elif filename:
            is_attachment = True
        elif ctype in (
            "application/pdf",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        ) or ctype.startswith(
            (
                "application/vnd.openxmlformats-officedocument.",
                "application/vnd.ms-",
                "application/zip",
                "application/x-zip-compressed",
                "application/x-rar-compressed",
            )
        ):
            is_attachment = True

        # Collect the same parts as before: attachment signals, plus anything
        # inline-looking (inline disposition, Content-ID, bare image).
        if not is_attachment and not ("inline" in disp or cid or ctype.startswith("image/")):
            continue

        if disp_type == "attachment":
            is_inline = False
        elif disp_type == "inline":
            is_inline = True
        elif cid and parent_ctype == "multipart/related":
            is_inline = True
        else:
            is_inline = not is_attachment

        payload = part.get_payload(decode=True)
        size_bytes = len(payload) if isinstance(payload, (bytes, bytearray)) else 0
        sha256: str | None = None
        if size_bytes > 0 and isinstance(payload, (bytes, bytearray)):
            try:
                sha256 = hashlib.sha256(payload).hexdigest()
            except Exception:
                sha256 = None

        out.append(
            {
                "part_index": part_index,
                "filename": filename,
                "content_type": ctype or None,
                "content_disposition": disp_raw or None,
                "size_bytes": size_bytes,
                "content_id": cid or None,
                "is_inline": is_inline,
                "sha256": sha256,
                "saved_path": None,
            }
        )
        part_index += 1

    return out


def body_content(msg: Message) -> tuple[str, str]:
    """
    Returns (body, body_html).
    - body: best plain text — real plain parts, else HTML stripped to text.
    - body_html: concatenated raw HTML parts (empty if none).
    """
    plain_parts, html_parts = _walk_parts(msg)
    body_plain = "\n\n".join(p.strip() for p in plain_parts if p.strip()).strip()
    body_html = "\n\n".join(p.strip() for p in html_parts if p.strip()).strip()

    if not body_plain and body_html:
        body_plain = html_to_text(body_html)

    return body_plain, body_html


def extract_body_structured(msg: Message) -> dict[str, str | bool]:
    """
    Extract body with provenance and improved cleaning (Phase 2.1).

    Returns a dict with:
      - body_text_raw: primary extracted text before improved cleaning (backward-compat style).
      - body_text_clean: best readable text (improved HTML cleaning when source is HTML).
      - body_source_type: "plain" | "html" | "mixed" | "empty".
      - body_has_plain: True if any text/plain part was found.
      - body_has_html: True if any text/html part was found.
    """
    plain_parts, html_parts = _walk_parts(msg)
    has_plain = bool(plain_parts)
    has_html = bool(html_parts)

    body_plain = "\n\n".join(p.strip() for p in plain_parts if p.strip()).strip()
    body_html_concat = "\n\n".join(p.strip() for p in html_parts if p.strip()).strip()

    if has_plain and has_html:
        source_type: BodySourceType = "mixed"
        raw = body_plain
        clean = _normalize_whitespace(body_plain)
    elif has_plain:
        source_type = "plain"
        raw = body_plain
        clean = _normalize_whitespace(body_plain)
    elif has_html:
        source_type = "html"
        raw = html_to_text(body_html_concat)  # legacy behaviour for "raw"
        clean = html_to_text_improved(body_html_concat)
    else:
        source_type = "empty"
        raw = ""
        clean = ""

    return {
        "body_text_raw": raw,
        "body_text_clean": clean,
        "body_source_type": source_type,
        "body_has_plain": has_plain,
        "body_has_html": has_html,
    }


def normalize_full_body(structured: dict[str, str | bool]) -> str:
    """
    Derive full_body_clean with explicit precedence:
    - prefer body_text_clean
    - fallback to legacy body_text_raw
    - otherwise empty string
    """
    body_text_clean = (structured.get("body_text_clean") or "").strip()  # type: ignore[arg-type]
    body_text_raw = (structured.get("body_text_raw") or "").strip()  # type: ignore[arg-type]
    if body_text_clean:
        return _normalize_whitespace(body_text_clean)
    if body_text_raw:
        return _normalize_whitespace(body_text_raw)
    return ""


def extract_top_reply(full_body_clean: str) -> str:
    """
    Best-effort newest reply text:
    - detect reply header / quoted blocks and cut below them
    - strip obvious signature block at the end
    - if nothing is confidently detected, fall back to full_body_clean
    """
    if not full_body_clean or not full_body_clean.strip():
        return ""

    text = full_body_clean
    original = text

    # First try to cut at reply headers (handles English/Spanish + common clients).
    text = _cut_at_reply_headers(text)
    # Then try to strip a trailing signature block conservatively.
    text = _strip_signature_block(text)

    # If stripping removed everything or changed nothing in a suspicious way, fall back.
    if not text.strip():
        return original
    return text.strip()


def extract_full_and_top_reply(structured: dict[str, str | bool]) -> tuple[str, str]:
    """
    Convenience helper for ingestion:
    - full_body_clean: normalize_full_body(structured)
    - top_reply_clean: extract_top_reply(full_body_clean)
    """
    full_body = normalize_full_body(structured)
    top_reply = extract_top_reply(full_body)
    return full_body, top_reply


def _cut_at_reply_headers(text: str) -> str:
    """
    Cut text at the first line that looks like a reply header / quoted block.
    Conservative: if we don't see a clear header pattern, return text unchanged.
    """
    lines = text.splitlines()
    if not lines:
        return text

    header_patterns = [
        r"^on .*wrote:$",  # On Mon, ... wrote:
        r"^el .*escribi[oó]:$",  # El ... escribió:
        r"^de:\s",  # De:
        r"^from:\s",  # From:
        r"^sent:\s",  # Sent:
        r"^enviado el:\s",  # Enviado el:
        r"^-{2,}\s*original message\s*-{2,}$",
        r"^-{2,}\s*mensaje original\s*-{2,}$",
    ]
    header_regexes = [re.compile(pat, re.IGNORECASE) for pat in header_patterns]

    cut_index: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Quoted lines starting with '>' in a block
        if stripped.startswith(">") and i > 0:
            cut_index = i
            break
        if any(rx.match(stripped) for rx in header_regexes):
            cut_index = i
            break

    if cut_index is None:
        return text

    # Keep everything before the header start.
    kept = "\n".join(lines[:cut_index]).strip()
    return kept or text


def _strip_signature_block(text: str) -> str:
    """
    Try to remove a trailing signature block using simple heuristics.
    Conservative: if unsure, returns text unchanged.
    """
    lines = text.splitlines()
    if len(lines) < 3:
        return text

    signature_starters = [
        "--",
        "saludos",
        "saludos cordiales",
        "atentamente",
        "best regards",
        "kind regards",
        "regards",
        "enviado desde mi",
    ]
    sig_regexes = [re.compile(rf"^{re.escape(pat)}", re.IGNORECASE) for pat in signature_starters]

    # Scan from bottom up to find a likely signature start.
    sig_start: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if any(rx.match(stripped) for rx in sig_regexes):
            sig_start = i
            break

    if sig_start is None:
        return text

    # Require at least a few lines of main body above the signature.
    if sig_start < 2:
        return text

    kept = "\n".join(lines[:sig_start]).strip()
    return kept or text


def body_text(msg: Message) -> str:
    """Backward-compatible: plain + HTML fallback as single string."""
    body, _ = body_content(msg)
    return body


def recipients_header(msg: Message) -> str:
    to_ = msg.get("To") or ""
    cc = msg.get("Cc") or ""
    bcc = msg.get("Bcc") or ""
    chunks = [x for x in (to_, cc, bcc) if x.strip()]
    return "; ".join(chunks) if chunks else ""


def date_iso_from_msg(msg: Message) -> str | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        return dt.isoformat()
    except (TypeError, ValueError):
        return None


def open_mbox(path: str) -> mailbox.mbox | None:
    try:
        return mailbox.mbox(path)
    except Exception:
        return None
