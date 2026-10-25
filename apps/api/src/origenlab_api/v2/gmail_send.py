"""Send one email as contacto@origenlab.cl through the Gmail API — the test-send path only.

The credential is a refresh token for the `gmail.send` OR `gmail.compose` scope (it cannot read
mail), authorized once by the owner as contacto@ with the local authorization script under
apps/api/scripts, and kept as a Render secret file named by `ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE`.
The V1 desktop token is never read. Plain HTTPS through `urllib`: no new runtime dependency.
"""

from __future__ import annotations

import base64
import http.client
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
#: Kept for backward compatibility — either SEND_SCOPE or COMPOSE_SCOPE is accepted.
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
#: gmail.compose permits users.messages.send and is accepted as an alternative to gmail.send.
COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
#: gmail.modify: needed to apply labels to a drafted message.
MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
DRAFT_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
LABELS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/labels"
MODIFY_URL_TEMPLATE = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/modify"
SEND_AS_URL_TEMPLATE = "https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs/{address}"
TEST_SUBJECT_PREFIX = "[PRUEBA] "
TIMEOUT_SECONDS = 10.0
REFRESH_MARGIN_SECONDS = 60.0
#: How long (seconds) to cache the contacto@ send-as signature in-process.
SIGNATURE_CACHE_TTL_SECONDS = 600.0

#: One plain ASCII mailbox and nothing a mail parser could read differently from what is recorded
#: (no display name, comment, encoded word, group, quote or separator): a local part of
#: `[A-Za-z0-9._%+-]` with no leading, trailing or doubled dot; one or more DNS labels; an
#: alphabetic top-level domain of 2-63 letters. The dashboard's PLAIN_ADDRESS is the same rule.
_LOCAL_PART = r"[A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+)*"
_DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_ADDRESS = re.compile(rf"{_LOCAL_PART}@(?:{_DOMAIN_LABEL}\.)+[A-Za-z]{{2,63}}")
MAX_ADDRESS_LENGTH = 254
#: A machine-readable Google error code (`invalid_grant`, `PERMISSION_DENIED`, `rateLimitExceeded`).
_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]


class GmailSendError(RuntimeError):
    """`kind` is the error class; `detail` is the short reason the failed receipt keeps — Google's
    HTTP status and error code (`HTTP 403 PERMISSION_DENIED/dailyLimitExceeded`), or the network
    exception's class. Never Google's message text, the recipient or a token."""

    def __init__(self, kind: str, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail


def _google_error_code(raw: bytes) -> str | None:
    """`error` (OAuth token endpoint) or `error.status` / `error.errors[0].reason` (Gmail API)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    parts: list[object] = []
    if isinstance(error, str):
        parts.append(error)
    elif isinstance(error, dict):
        parts.append(error.get("status"))
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            parts.append(errors[0].get("reason"))
    clean = [p for p in parts if isinstance(p, str) and _ERROR_CODE.fullmatch(p)]
    return "/".join(clean) or None


def _http_detail(status: int, raw: bytes) -> str:
    code = _google_error_code(raw)
    return f"HTTP {status} {code}" if code else f"HTTP {status}"


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
    if not isinstance(data, dict):
        raise ValueError("the Gmail send token must be a JSON object")
    fields = {k: data.get(k) for k in ("client_id", "client_secret", "refresh_token", "address")}
    if not all(isinstance(v, str) and v.strip() for v in fields.values()):
        raise ValueError("the Gmail send token needs client_id, client_secret, refresh_token and address")
    if fields["address"].strip().lower() != SENDER_ADDRESS:
        raise ValueError(f"the Gmail send token is for {fields['address']!r}, not {SENDER_ADDRESS}")
    scope = data.get("scope")
    if isinstance(scope, str):
        if SEND_SCOPE not in scope and COMPOSE_SCOPE not in scope:
            raise ValueError("the Gmail send token must include gmail.send or gmail.compose scope")
    return GmailSendToken(**{k: v.strip() for k, v in fields.items()})


def valid_test_address(raw: str) -> str:
    value = (raw or "").strip()
    if len(value) > MAX_ADDRESS_LENGTH or not _ADDRESS.fullmatch(value):
        raise ValueError("escribe una sola dirección de correo, sin nombre ni separadores")
    return value


_HEADER_INJECTION = re.compile(r"[\r\n]")
#: Control characters that must not appear in body text (CR and LF are allowed — they are
#: legitimate line breaks in a multi-line body, unlike in a header value).
_BODY_CONTROL = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]")


def _safe_header(value: str, max_len: int) -> str:
    """Raise ValueError if value contains CR/LF; truncate to max_len."""
    if _HEADER_INJECTION.search(value):
        raise ValueError("header injection attempt")
    return value[:max_len]


def _safe_body(value: str, max_len: int) -> str:
    """Strip non-printable control chars (preserving CR/LF) and truncate to max_len."""
    return _BODY_CONTROL.sub("", value)[:max_len]


def build_web_quote_message(
    *,
    reply_to: str,
    institution: str,
    need: str,
    extra: dict[str, str],
    submitted_at: str,
) -> bytes:
    """One self-addressed form notification: From/To=contacto@, Reply-To=visitor.

    Raises ValueError if reply_to is not a valid plain mailbox address or if
    any field contains CR/LF (header injection attempt).
    """
    if not _ADDRESS.fullmatch(reply_to) or len(reply_to) > MAX_ADDRESS_LENGTH:
        raise ValueError("reply_to is not a valid plain mailbox address")
    institution_safe = _safe_header(institution, 120)
    msg = EmailMessage()
    msg["From"] = formataddr((SENDER_NAME, SENDER_ADDRESS))
    msg["To"] = SENDER_ADDRESS
    msg["Reply-To"] = reply_to
    msg["Subject"] = f"Solicitud de cotización — sitio web — {institution_safe}"
    msg["X-OrigenLab-Origin"] = "web-quote-form/v1"
    lines = [
        f"Institución o empresa: {_safe_header(institution, 200)}",
        f"Correo: {reply_to}",
        f"Qué necesita: {_safe_body(need, 4000)}",
    ]
    for label, value in extra.items():
        if value:
            lines.append(f"{label}: {_safe_body(value, 200)}")
    lines.append(f"Enviado: {_safe_header(submitted_at, 50)}")
    msg.set_content("\n".join(lines), charset="utf-8")
    return msg.as_bytes()


def build_test_message(*, to: str, subject: str, html: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = formataddr((SENDER_NAME, SENDER_ADDRESS))
    msg["To"] = to
    msg["Subject"] = TEST_SUBJECT_PREFIX + subject
    msg["List-Unsubscribe"] = f"<mailto:{SENDER_ADDRESS}?subject=REMOVER>"
    msg["X-OrigenLab-Test-Send"] = "1"
    # The recorded recipient must be the one Gmail delivers to: the parsed header holds exactly
    # one mailbox, and it is `to` itself.
    recipients = msg["To"].addresses
    if len(recipients) != 1 or recipients[0].addr_spec != to:
        raise ValueError("the To header does not parse back to exactly the one address given")
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
        self._signature_html: str | None = None
        self._signature_cached_at = 0.0

    def _access_token(self) -> str:
        if self._access and self._clock() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._access
        body = urllib.parse.urlencode({
            "client_id": self._token.client_id, "client_secret": self._token.client_secret,
            "refresh_token": self._token.refresh_token, "grant_type": "refresh_token",
        }).encode()
        status, raw = self._call("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status != 200:
            detail = _http_detail(status, raw)
            raise GmailSendError("token_refresh_failed", f"token refresh answered {detail}", detail)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            self._access = data["access_token"]
            expires_in = data.get("expires_in", 0)
            if expires_in is None:
                expires_in = 0
            self._expires_at = self._clock() + float(expires_in)
        except (ValueError, KeyError, TypeError) as exc:  # never echo the body: it holds the token
            raise GmailSendError("token_refresh_failed", f"token endpoint returned invalid data ({type(exc).__name__})",
                                 "HTTP 200 invalid_response") from exc
        return self._access

    def _call(self, method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        try:
            return self._transport(method, url, headers, body, TIMEOUT_SECONDS)
        except (OSError, http.client.HTTPException) as exc:
            raise GmailSendError("network", str(exc), type(exc).__name__) from exc

    def send(self, raw: bytes) -> str:
        token = self._access_token()
        payload = json.dumps({"raw": base64.urlsafe_b64encode(raw).decode()}).encode()
        status, body = self._call("POST", SEND_URL, {"Authorization": f"Bearer {token}",
                                                     "Content-Type": "application/json"}, payload)
        if status != 200:
            detail = _http_detail(status, body)
            raise GmailSendError("gmail_rejected", f"Gmail answered {detail}", detail)
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data["id"]
        except (ValueError, KeyError) as exc:
            raise GmailSendError("gmail_rejected", f"Gmail returned invalid data ({type(exc).__name__})",
                                 "HTTP 200 invalid_response") from exc

    def create_draft(self, raw: bytes) -> tuple[str, str]:
        """Create a Gmail draft; return (draft_id, message_id).

        Never sends the message. Requires gmail.compose scope (granted by the default
        production token). Raises GmailSendError on network or API failure.
        """
        token = self._access_token()
        payload = json.dumps({"message": {"raw": base64.urlsafe_b64encode(raw).decode()}}).encode()
        status, body = self._call("POST", DRAFT_URL, {"Authorization": f"Bearer {token}",
                                                      "Content-Type": "application/json"}, payload)
        if status != 200:
            detail = _http_detail(status, body)
            raise GmailSendError("draft_creation_failed", f"Gmail answered {detail}", detail)
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            draft_id = data["id"]
            message_id = data["message"]["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GmailSendError("draft_creation_failed",
                                 f"Gmail returned invalid data ({type(exc).__name__})",
                                 "HTTP 200 invalid_response") from exc
        return draft_id, message_id

    def _ensure_label(self, token: str, name: str) -> str:
        """Return the Gmail label id for `name`, creating it if it does not exist."""
        status, body = self._call("GET", LABELS_URL, {"Authorization": f"Bearer {token}"}, b"")
        if status != 200:
            raise GmailSendError("label_list_failed", f"Gmail answered {_http_detail(status, body)}")
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            for lbl in data.get("labels") or []:
                if isinstance(lbl, dict) and lbl.get("name") == name:
                    return lbl["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GmailSendError("label_list_failed",
                                 f"Gmail returned invalid data ({type(exc).__name__})",
                                 "HTTP 200 invalid_response") from exc
        # Label does not exist — create it
        create_payload = json.dumps({"name": name}).encode()
        status2, body2 = self._call("POST", LABELS_URL, {"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json"}, create_payload)
        if status2 not in (200, 201):
            raise GmailSendError("label_create_failed",
                                 f"Gmail answered {_http_detail(status2, body2)}")
        try:
            lbl_data = json.loads(body2)
            if not isinstance(lbl_data, dict):
                raise ValueError("not an object")
            return lbl_data["id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GmailSendError("label_create_failed",
                                 f"Gmail returned invalid data ({type(exc).__name__})",
                                 "HTTP 200 invalid_response") from exc

    def apply_labels(self, message_id: str, label_names: list[str]) -> None:
        """Apply named Gmail labels to `message_id`. Requires gmail.modify scope.

        Raises GmailSendError if the scope is missing (HTTP 403) or any other API error.
        Never raises on an empty label list.
        """
        if not label_names:
            return
        token = self._access_token()
        label_ids = [self._ensure_label(token, name) for name in label_names]
        url = MODIFY_URL_TEMPLATE.format(message_id=message_id)
        payload = json.dumps({"addLabelIds": label_ids}).encode()
        status, body = self._call("POST", url, {"Authorization": f"Bearer {token}",
                                                "Content-Type": "application/json"}, payload)
        if status != 200:
            detail = _http_detail(status, body)
            raise GmailSendError("label_apply_failed", f"Gmail answered {detail}", detail)

    def fetch_send_as_signature(self, address: str = SENDER_ADDRESS) -> str | None:
        """Fetch the HTML send-as signature for `address` from the Gmail settings API.

        Results are cached for SIGNATURE_CACHE_TTL_SECONDS. Returns the HTML string or
        None if the signature is empty, the scope is missing, or any error occurs.
        Requires gmail.settings.basic scope (in the default production token).
        """
        now = self._clock()
        if self._signature_html is not None and now < self._signature_cached_at + SIGNATURE_CACHE_TTL_SECONDS:
            return self._signature_html if self._signature_html else None
        url = SEND_AS_URL_TEMPLATE.format(address=urllib.parse.quote(address, safe=""))
        try:
            token = self._access_token()
            status, body = self._call("GET", url, {"Authorization": f"Bearer {token}"}, b"")
        except GmailSendError:
            return None
        if status != 200:
            return None
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                return None
            sig = data.get("signature") or ""
            self._signature_html = str(sig) if sig else ""
            self._signature_cached_at = now
            return self._signature_html if self._signature_html else None
        except (ValueError, TypeError):
            return None
