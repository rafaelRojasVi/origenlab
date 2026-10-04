"""Send one email as contacto@origenlab.cl through the Gmail API — the test-send path only.

The credential is a refresh token for the `gmail.send` scope alone (it cannot read mail),
authorized once by the owner as contacto@ via the authorization script, and kept as a
Render secret file. The V1 desktop token is never read. Plain HTTPS through `urllib`:
no new runtime dependency.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

SENDER_ADDRESS = "contacto@origenlab.cl"
SENDER_NAME = "OrigenLab"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
TEST_SUBJECT_PREFIX = "[PRUEBA] "
TIMEOUT_SECONDS = 10.0
REFRESH_MARGIN_SECONDS = 60.0

#: One plain address: no display name, list separator, quote, angle bracket, space or control.
_ADDRESS = re.compile(r"^[^@\s,;<>\"'\x00-\x1f\x7f]+@[^@\s,;<>\"'\x00-\x1f\x7f]+\.[^@\s,;<>\"'\x00-\x1f\x7f]+$")

Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]


class GmailSendError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class GmailSendToken:
    client_id: str
    client_secret: str
    refresh_token: str
    address: str


def load_send_token(path: str) -> GmailSendToken:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the Gmail send token: {exc}") from exc
    fields = {k: data.get(k) for k in ("client_id", "client_secret", "refresh_token", "address")}
    if not all(isinstance(v, str) and v.strip() for v in fields.values()):
        raise ValueError("the Gmail send token needs client_id, client_secret, refresh_token and address")
    if fields["address"].strip().lower() != SENDER_ADDRESS:
        raise ValueError(f"the Gmail send token is for {fields['address']!r}, not {SENDER_ADDRESS}")
    return GmailSendToken(**{k: v.strip() for k, v in fields.items()})


def valid_test_address(raw: str) -> str:
    value = (raw or "").strip()
    if len(value) > 254 or not _ADDRESS.match(value):
        raise ValueError("escribe una sola dirección de correo, sin nombre ni separadores")
    return value


def build_test_message(*, to: str, subject: str, html: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = formataddr((SENDER_NAME, SENDER_ADDRESS))
    msg["To"] = to
    msg["Subject"] = TEST_SUBJECT_PREFIX + subject
    msg["List-Unsubscribe"] = f"<mailto:{SENDER_ADDRESS}?subject=REMOVER>"
    msg["X-OrigenLab-Test-Send"] = "1"
    msg.set_content(html, subtype="html", charset="utf-8")
    return msg.as_bytes()


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class GmailSender:
    def __init__(self, token: GmailSendToken, transport: Transport = urllib_transport,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._token = token
        self._transport = transport
        self._clock = clock
        self._access: str | None = None
        self._expires_at = 0.0

    def _access_token(self) -> str:
        if self._access and self._clock() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._access
        body = urllib.parse.urlencode({
            "client_id": self._token.client_id, "client_secret": self._token.client_secret,
            "refresh_token": self._token.refresh_token, "grant_type": "refresh_token",
        }).encode()
        status, raw = self._call("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status != 200:
            raise GmailSendError("token_refresh_failed", f"token refresh answered {status}")
        data = json.loads(raw)
        self._access = data["access_token"]
        self._expires_at = self._clock() + float(data.get("expires_in", 0))
        return self._access

    def _call(self, method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        try:
            return self._transport(method, url, headers, body, TIMEOUT_SECONDS)
        except OSError as exc:
            raise GmailSendError("network", str(exc)) from exc

    def send(self, raw: bytes) -> str:
        token = self._access_token()
        payload = json.dumps({"raw": base64.urlsafe_b64encode(raw).decode()}).encode()
        status, body = self._call("POST", SEND_URL, {"Authorization": f"Bearer {token}",
                                                     "Content-Type": "application/json"}, payload)
        if status != 200:
            raise GmailSendError("gmail_rejected", f"Gmail answered {status}")
        return json.loads(body)["id"]
