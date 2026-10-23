"""An organization's logo for «Prospectos»: fetched once by the worker, kept small, raster only.

SVG is refused (it can carry script) and so is anything whose bytes are not the image its header
claims. The dashboard never loads a logo from the organization's own site."""

from __future__ import annotations

import re
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

MAX_LOGO_BYTES = 32_768
_MAX_PAGE_BYTES = 262_144
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "image/x-icon": (b"\x00\x00\x01\x00",),
    "image/vnd.microsoft.icon": (b"\x00\x00\x01\x00",),
}
_USER_AGENT = "OrigenLab-Prospectos/1 (+https://origenlab.cl)"


class _IconLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower()
        if tag == "link" and a.get("href") and ("apple-touch-icon" in rel or re.search(r"\bicon\b", rel)):
            self.found.append(a["href"])
        if tag == "meta" and a.get("property", "").lower() == "og:image" and a.get("content"):
            self.found.append(a["content"])


def _get(url: str, limit: int, timeout_s: float) -> tuple[str, bytes] | None:
    if not url.startswith(("http://", "https://")):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - http(s) only, checked above
            if response.status != 200:
                return None
            ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            body = response.read(limit + 1)
    except Exception:  # noqa: BLE001 — any network failure means "no logo", never a failed load
        return None
    return ctype, body


def _as_logo(found: tuple[str, bytes] | None) -> tuple[str, bytes] | None:
    if found is None:
        return None
    ctype, body = found
    if ctype not in _MAGIC or len(body) > MAX_LOGO_BYTES or not body.startswith(_MAGIC[ctype]):
        return None
    return ("image/x-icon" if ctype == "image/vnd.microsoft.icon" else ctype), body


def fetch_logo(website: str, *, timeout_s: float = 8.0) -> tuple[str, bytes] | None:
    base = website if website.endswith("/") else website + "/"
    logo = _as_logo(_get(urljoin(base, "/apple-touch-icon.png"), MAX_LOGO_BYTES, timeout_s))
    if logo:
        return logo
    page = _get(base, _MAX_PAGE_BYTES, timeout_s)
    if page and page[0] == "text/html":
        parser = _IconLinks()
        try:
            parser.feed(page[1][:_MAX_PAGE_BYTES].decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — a broken page just has no icon links
            parser.found = []
        for href in parser.found:
            logo = _as_logo(_get(urljoin(base, href), MAX_LOGO_BYTES, timeout_s))
            if logo:
                return logo
    return _as_logo(_get(urljoin(base, "/favicon.ico"), MAX_LOGO_BYTES, timeout_s))
