"""Organization logos for «Prospectos» (`origenlab_worker.prospects.logo`): fetched once, small,
raster only — never SVG, never a page. A local HTTP server stands in for the organization's site."""

from __future__ import annotations

import http.server
import threading
import time

import pytest

from origenlab_worker.prospects.logo import MAX_LOGO_BYTES, fetch_logo

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


class _Site(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str, bytes]] = {}
    delay = 0.0

    def do_GET(self):  # noqa: N802 - http.server API
        time.sleep(self.delay)
        status, ctype, body = self.routes.get(self.path, (404, "text/plain", b"no"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Site.routes, _Site.delay = {}, 0.0
    yield f"http://127.0.0.1:{server.server_address[1]}", _Site
    server.shutdown()


def test_fetch_logo_prefers_apple_touch_icon(site):
    url, handler = site
    handler.routes = {"/apple-touch-icon.png": (200, "image/png", PNG)}
    assert fetch_logo(url) == ("image/png", PNG)


def test_fetch_logo_reads_link_rel_icon(site):
    url, handler = site
    handler.routes = {"/": (200, "text/html", b'<html><head><link rel="icon" href="/i/logo.png"></head></html>'),
                      "/i/logo.png": (200, "image/png", PNG)}
    assert fetch_logo(url) == ("image/png", PNG)


def test_fetch_logo_falls_back_to_favicon(site):
    url, handler = site
    handler.routes = {"/favicon.ico": (200, "image/x-icon", b"\0\0\1\0" + b"\0" * 20)}
    assert fetch_logo(url)[0] == "image/x-icon"


def test_fetch_logo_refuses_svg(site):
    url, handler = site
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    handler.routes = {"/apple-touch-icon.png": (200, "image/svg+xml", svg),
                      "/favicon.ico": (200, "image/svg+xml", svg)}
    assert fetch_logo(url) is None


def test_fetch_logo_refuses_over_32k(site):
    url, handler = site
    handler.routes = {"/apple-touch-icon.png": (200, "image/png", PNG + b"\0" * MAX_LOGO_BYTES)}
    assert MAX_LOGO_BYTES == 32_768
    assert fetch_logo(url) is None


def test_fetch_logo_refuses_html_served_as_image(site):
    url, handler = site
    handler.routes = {"/favicon.ico": (200, "image/png", b"<html>not an image</html>")}
    assert fetch_logo(url) is None


def test_fetch_logo_returns_none_on_timeout(site):
    url, handler = site
    handler.routes = {"/apple-touch-icon.png": (200, "image/png", PNG)}
    handler.delay = 1.0
    assert fetch_logo(url, timeout_s=0.2) is None


def test_fetch_logo_returns_none_for_unreachable_site():
    assert fetch_logo("http://127.0.0.1:9") is None
