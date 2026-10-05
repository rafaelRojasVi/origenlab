"""A fake Gmail REST server on 127.0.0.1 for the end-to-end test (spec §7.2).

It speaks exactly the six calls the reader makes — token refresh, profile, history (2 per page),
messages list (2 per page, newest first), message get (metadata / raw) and labels — over real HTTP,
so the e2e exercises `urllib_transport`, paging and base64url decoding, not a stub.
"""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, field
from email.parser import BytesHeaderParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from mailfixtures import MAILBOX
from origenlab_worker.gmail_client import READONLY_SCOPE

PAGE = 2
LABELS = [{"id": "INBOX", "name": "INBOX"}, {"id": "SENT", "name": "SENT"}, {"id": "SPAM", "name": "SPAM"},
          {"id": "DRAFT", "name": "DRAFT"}, {"id": "TRASH", "name": "TRASH"}]


@dataclass
class FakeMailbox:
    email: str = MAILBOX
    history_id: int = 100
    history: list[tuple[int, str, str, tuple[str, ...]]] = field(default_factory=list)
    messages: dict[str, dict] = field(default_factory=dict)
    expired_below: int = 0
    failing_raw: set[str] = field(default_factory=set)
    scope: str = READONLY_SCOPE

    def add(self, message_id: str, raw: bytes, *, labels: tuple[str, ...], internal_ms: int,
            thread_id: str = "t-1") -> None:
        self.history_id += 1
        self.messages[message_id] = {"raw": raw, "labels": list(labels), "internal_ms": internal_ms,
                                     "thread_id": thread_id}
        self.history.append((self.history_id, message_id, "messageAdded", ()))

    def add_label(self, message_id: str, label: str) -> None:
        """A `labelAdded` history record (a draft that was sent, a message moved out of spam)."""
        self.history_id += 1
        self.messages[message_id]["labels"].append(label)
        self.history.append((self.history_id, message_id, "labelAdded", (label,)))

    def list_again(self, message_id: str) -> None:
        """The same message in a second history record (Gmail does this)."""
        self.history_id += 1
        self.history.append((self.history_id, message_id, "messageAdded", ()))


class FakeGmailServer:
    def __init__(self) -> None:
        self.state = FakeMailbox()
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # keep test output clean
                pass

            def _send(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._send(200, {"access_token": "e2e-access", "expires_in": 3600, "scope": state.scope})

            def do_GET(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                query = {k: v for k, v in parse_qs(parts.query).items()}
                path = parts.path.removeprefix("/gmail/v1/users/me/")
                if path == "profile":
                    return self._send(200, {"emailAddress": state.email, "historyId": str(state.history_id)})
                if path == "labels":
                    return self._send(200, {"labels": LABELS})
                if path == "history":
                    start = int(query["startHistoryId"][0])
                    if start < state.expired_below:
                        return self._send(404, {"error": {"code": 404}})
                    wanted = set(query.get("historyTypes", ["messageAdded"]))
                    rows = [(m, kind, added) for h, m, kind, added in state.history if h > start and kind in wanted]

                    def render(entries):
                        out = []
                        for m, kind, added in entries:
                            if kind == "messageAdded":
                                out.append({"messagesAdded": [{"message": {"id": m}}]})
                            else:
                                out.append({"labelsAdded": [{"message": {"id": m}, "labelIds": list(added)}]})
                        return out

                    return self._page(rows, query, "history", render, {"historyId": str(state.history_id)})
                if path == "messages":
                    after = int(query["q"][0].removeprefix("after:"))
                    rows = sorted((m for m, v in state.messages.items() if v["internal_ms"] // 1000 > after),
                                  key=lambda m: state.messages[m]["internal_ms"], reverse=True)
                    return self._page(rows, query, "messages", lambda ids: [{"id": i} for i in ids], {})
                if path.startswith("messages/"):
                    message_id = path.removeprefix("messages/")
                    message = state.messages.get(message_id)
                    if message is None:
                        return self._send(404, {"error": {"code": 404}})
                    body = {"id": message_id, "threadId": message["thread_id"], "labelIds": message["labels"],
                            "internalDate": str(message["internal_ms"]), "sizeEstimate": len(message["raw"])}
                    if query.get("format") == ["raw"]:
                        if message_id in state.failing_raw:
                            return self._send(503, {"error": {"code": 503}})
                        body["raw"] = base64.urlsafe_b64encode(message["raw"]).decode().rstrip("=")
                    else:
                        headers = BytesHeaderParser().parsebytes(message["raw"])
                        body["payload"] = {"headers": [{"name": k, "value": str(v)} for k, v in headers.items()
                                                       if k in ("From", "Subject", "Message-ID", "Date")]}
                    return self._send(200, body)
                return self._send(404, {"error": {"code": 404}})

            def _page(self, rows, query, key, render, extra) -> None:
                start = int((query.get("pageToken") or ["0"])[0])
                body = {key: render(rows[start:start + PAGE]), **extra}
                if start + PAGE < len(rows):
                    body["nextPageToken"] = str(start + PAGE)
                self._send(200, body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}/gmail/v1/users/me"

    @property
    def token_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}/token"

    def __enter__(self) -> FakeGmailServer:
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
