#!/usr/bin/env python3
"""Local web server for viewing/downloading lead CSVs (advanced / parked helper).

Purpose:
- Provide a simple "web UI" for local or LAN CSV download demos.
- Do NOT expose SQLite; serve only generated CSVs.
- Use HTTP Basic Auth (username/password) to require a login.

By default binds to **127.0.0.1** (localhost only). Pass ``--lan`` intentionally to
expose on the LAN. Set ``LEADS_WEB_PASS`` or pass ``--pass`` — no default password.

Serves files under ``--reports-dir`` that match ``leads_*.csv``.
Not daily outbound, not send approval, not ``apps/api``.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import socketserver
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_LEGACY_PASSWORD = "leads123"

CSV_RE = re.compile(r"^leads_.*\.csv$", re.IGNORECASE)
_HEADER_VALUE_UNSAFE_RE = re.compile(r"[\r\n]")


def sanitize_header_value(value: str) -> str:
    """Reject CR/LF in header values to prevent response splitting."""
    if _HEADER_VALUE_UNSAFE_RE.search(value):
        raise ValueError("header value must not contain CR or LF")
    return value


def safe_download_basename(filename: str) -> str:
    """Return a single-path-segment filename safe for Content-Disposition."""
    base = Path(filename).name
    slug = re.sub(r"[^\w.\-]", "_", base).strip("._")
    if not slug or slug in {".", ".."}:
        return "download.csv"
    if not CSV_RE.match(slug):
        return "download.csv"
    return slug


def content_disposition_attachment(filename: str) -> str:
    safe_name = safe_download_basename(filename)
    return f'attachment; filename="{sanitize_header_value(safe_name)}"'


def _auth_ok(req_handler: SimpleHTTPRequestHandler, expected_user: str, expected_pass: str) -> bool:
    auth = req_handler.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    b64 = auth.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(b64).decode("utf-8")
    except Exception:
        return False
    if ":" not in decoded:
        return False
    user, pw = decoded.split(":", 1)
    return user == expected_user and pw == expected_pass


class LeadsRequestHandler(SimpleHTTPRequestHandler):
    # These are filled by `run_server`.
    reports_dir: Path = Path("reports/out")
    expected_user: str = ""
    expected_pass: str = ""

    def do_GET(self) -> None:  # noqa: N802 (http naming)
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        # Auth for all requests (including "/").
        if not _auth_ok(self, self.expected_user, self.expected_pass):
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="OrigenLab Leads"')
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return

        if path == "/" or path == "":
            return self._serve_index()

        if path.startswith("/download/"):
            filename = path.split("/download/", 1)[1]
            filename = os.path.basename(filename)  # prevents path traversal
            return self._serve_csv(filename)

        # Fallback: 404
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()
        self.wfile.write(b"Not found")

    def _serve_index(self) -> None:
        # List latest few CSVs for quick access.
        csv_files = []
        if self.reports_dir.exists():
            for p in self.reports_dir.glob("leads_*.csv"):
                if p.is_file() and CSV_RE.match(p.name):
                    csv_files.append(p)
        csv_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        csv_files = csv_files[:30]

        links = "\n".join(
            f'<li><a href="/download/{p.name}">{p.name}</a></li>'
            for p in csv_files
        )
        html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>OrigenLab - Leads CSVs</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 28px; }}
    code {{ background: #f2f2f2; padding: 2px 6px; border-radius: 6px; }}
    ul {{ line-height: 1.6; }}
  </style>
</head>
<body>
  <h2>OrigenLab - Leads CSVs</h2>
  <p>Download-only list (no SQLite exposed). Most recent files first.</p>
  <ul>{links}</ul>
</body>
</html>
""".strip()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_csv(self, filename: str) -> None:
        if not filename or not CSV_RE.match(filename):
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        target = (self.reports_dir / filename).resolve()
        # Ensure it stays within reports_dir even if weird paths were passed.
        if self.reports_dir.resolve() not in target.parents and target != self.reports_dir / filename:
            self.send_response(HTTPStatus.FORBIDDEN)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return

        if not target.exists() or not target.is_file():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        ctype, _ = mimetypes.guess_type(str(target))
        if not ctype:
            ctype = "text/csv; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", sanitize_header_value(ctype))
        self.send_header("Content-Disposition", content_disposition_attachment(filename))
        self.end_headers()
        with target.open("rb") as f:
            self.wfile.write(f.read())


def run_server(host: str, port: int, user: str, pw: str, reports_dir: Path) -> None:
    handler = LeadsRequestHandler
    handler.reports_dir = reports_dir
    handler.expected_user = user
    handler.expected_pass = pw

    with socketserver.TCPServer((host, port), handler) as httpd:
        httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local authenticated server for leads CSVs (localhost by default).")
    ap.add_argument(
        "--host",
        default=None,
        help="Explicit bind host (cannot combine with --lan). Default without --lan: 127.0.0.1.",
    )
    ap.add_argument(
        "--lan",
        action="store_true",
        help="Bind 0.0.0.0 so other devices on the LAN can connect (intentional exposure).",
    )
    ap.add_argument("--port", type=int, default=8000, help="Port (default: 8000).")
    ap.add_argument("--user", default=os.getenv("LEADS_WEB_USER", "leads"), help="Basic auth username.")
    ap.add_argument(
        "--pass",
        dest="password",
        default=None,
        help="Basic auth password (or set LEADS_WEB_PASS). Required.",
    )
    ap.add_argument(
        "--allow-default-password",
        action="store_true",
        help="Unsafe: allow the legacy default password leads123 for local demo/dev only.",
    )
    ap.add_argument(
        "--reports-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "reports" / "out",
        help="Directory with generated CSVs (default: reports/out).",
    )
    args = ap.parse_args(argv)

    if args.lan and args.host is not None:
        ap.error("--lan and --host cannot be used together")

    host = "0.0.0.0" if args.lan else (args.host if args.host is not None else "127.0.0.1")

    password = args.password if args.password is not None else os.getenv("LEADS_WEB_PASS")
    if not password:
        ap.error("Set LEADS_WEB_PASS or pass --pass.")
    if password == DEFAULT_LEGACY_PASSWORD and not args.allow_default_password:
        ap.error(
            f'Default password "{DEFAULT_LEGACY_PASSWORD}" is not allowed; '
            "set a strong password or pass --allow-default-password."
        )

    reports_dir: Path = args.reports_dir
    if not reports_dir.exists():
        ap.error(f"reports_dir does not exist: {reports_dir}")

    print(f"Reports dir: {reports_dir}")
    print(f"Binding: http://{host}:{args.port}/")
    if args.lan:
        print("LAN mode: server reachable from other devices on your network.")
    else:
        print("Localhost only (127.0.0.1). Pass --lan to expose on the LAN.")
    print(f"Auth username: {args.user!r}")

    run_server(host, args.port, args.user, password, reports_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
