"""Read contacto@origenlab.cl through the Gmail REST API — `gmail.readonly` only.

Mirrors the campaign test-send client (`apps/api/src/origenlab_api/v2/gmail_send.py` on
`feat/campaign-test-send-v1` @ `e488c2a1`: `Transport` :37, `urllib_transport` :87, the
refresh-with-margin `_access_token` :105 and the `_call` that names a network failure :128):
plain HTTPS through `urllib`, no Google SDK at runtime.

The refresh token belongs to a Workspace *Internal* OAuth client that contacto@ consented to once
(`scripts/gmail_readonly_authorize.py`). Every refresh re-checks that Google granted exactly
`gmail.readonly`: a token that could send or delete — V1's desktop token carries
`https://mail.google.com/` — is refused before a single Gmail request is made.
"""

from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT_SECONDS = 30.0
REFRESH_MARGIN_SECONDS = 60.0
MAX_ATTEMPTS = 4
PAGE_SIZE = 500
METADATA_HEADERS = ("From", "Subject", "Message-ID", "Date")
RETRYABLE = frozenset({429, 500, 502, 503, 504})

Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]


class GmailError(RuntimeError):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class GmailAuthError(GmailError):
    """`invalid_grant` (revoked or expired consent), `token_refresh_failed`,
    `scope_not_readonly` or `unauthorized` (a 401 that survives a fresh token)."""


class GmailNotFound(GmailError):
    pass


class HistoryExpired(GmailError):
    """`startHistoryId` is older than Gmail keeps (about a week): resync instead."""


class GmailUnavailable(GmailError):
    """The network, a 5xx or a 429 after retries, or an answer that is not JSON."""


@dataclass(frozen=True)
class GmailCredentials:
    client_id: str
    client_secret: str
    refresh_token: str


@dataclass(frozen=True)
class Profile:
    email_address: str
    history_id: str


@dataclass(frozen=True)
class HistoryWindow:
    message_ids: tuple[str, ...]
    history_id: str


@dataclass(frozen=True)
class MessageMeta:
    message_id: str
    thread_id: str | None
    label_ids: tuple[str, ...]
    size_estimate: int
    internal_date_ms: int
    headers: dict[str, str]


@dataclass(frozen=True)
class RawMessage:
    message_id: str
    thread_id: str | None
    label_ids: tuple[str, ...]
    internal_date_ms: int
    raw: bytes


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=body if method != "GET" else None, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class GmailReader:
    def __init__(
        self,
        credentials: GmailCredentials,
        *,
        transport: Transport = urllib_transport,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        api_base: str = API_BASE,
        token_url: str = TOKEN_URL,
    ) -> None:
        self._credentials = credentials
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._api_base = api_base.rstrip("/")
        self._token_url = token_url
        self._access: str | None = None
        self._expires_at = 0.0
        self.granted_scopes: tuple[str, ...] = ()

    # ------------------------------------------------------------------ transport and token

    def _call(self, method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        try:
            return self._transport(method, url, headers, body, TIMEOUT_SECONDS)
        except (OSError, http.client.HTTPException):
            raise GmailUnavailable("network") from None

    def _refresh(self) -> str:
        body = urllib.parse.urlencode(
            {
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "refresh_token": self._credentials.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()
        status, raw = self._call(
            "POST", self._token_url, {"Content-Type": "application/x-www-form-urlencoded"}, body
        )
        data = _json(raw)
        data = data if isinstance(data, dict) else {}
        if status != 200:
            if data.get("error") == "invalid_grant":
                raise GmailAuthError("invalid_grant")
            raise GmailAuthError("token_refresh_failed")
        scopes = tuple(sorted(str(data.get("scope") or "").split()))
        if scopes != (READONLY_SCOPE,):
            raise GmailAuthError("scope_not_readonly")
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise GmailAuthError("token_refresh_failed")
        self._access = access
        self._expires_at = self._clock() + float(data.get("expires_in") or 0)
        self.granted_scopes = scopes
        return access

    def _token(self) -> str:
        if self._access and self._clock() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._access
        return self._refresh()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self._api_base}/{path}" + (f"?{query}" if query else "")
        refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            status, raw = self._call("GET", url, {"Authorization": f"Bearer {self._token()}"}, b"")
            if status == 200:
                data = _json(raw)
                if not isinstance(data, dict):
                    raise GmailUnavailable("invalid_json")
                return data
            if status == 401 and not refreshed:
                self._access, refreshed = None, True
                continue
            if status == 401:
                raise GmailAuthError("unauthorized")
            if status == 404:
                raise GmailNotFound("not_found")
            if status in RETRYABLE and attempt < MAX_ATTEMPTS - 1:
                self._sleep(2.0**attempt)
                continue
            raise GmailUnavailable(f"http_{status}")
        raise GmailUnavailable("retries_exhausted")

    # ------------------------------------------------------------------ the reads

    def profile(self) -> Profile:
        data = self._get("profile")
        return Profile(
            email_address=str(data.get("emailAddress") or "").strip().lower(),
            history_id=str(data.get("historyId") or ""),
        )

    def history(self, start_history_id: str) -> HistoryWindow:
        """Every message added since `start_history_id`, oldest first, each id once."""
        ids: dict[str, None] = {}
        latest, token = start_history_id, None
        while True:
            params: dict[str, Any] = {
                "startHistoryId": start_history_id,
                "historyTypes": "messageAdded",
                "maxResults": PAGE_SIZE,
            }
            if token:
                params["pageToken"] = token
            try:
                data = self._get("history", params)
            except GmailNotFound:
                raise HistoryExpired("history_expired") from None
            for record in data.get("history") or []:
                for added in record.get("messagesAdded") or []:
                    message_id = (added.get("message") or {}).get("id")
                    if message_id:
                        ids.setdefault(str(message_id), None)
            latest = str(data.get("historyId") or latest)
            token = data.get("nextPageToken")
            if not token:
                return HistoryWindow(tuple(ids), latest)

    def messages_after(self, epoch_seconds: int) -> list[str]:
        """The resync list: every message after `epoch_seconds`, oldest first, spam and trash excluded."""
        ids: list[str] = []
        token = None
        while True:
            params: dict[str, Any] = {"q": f"after:{int(epoch_seconds)}", "maxResults": PAGE_SIZE}
            if token:
                params["pageToken"] = token
            data = self._get("messages", params)
            ids.extend(str(m["id"]) for m in data.get("messages") or [] if m.get("id"))
            token = data.get("nextPageToken")
            if not token:
                return list(dict.fromkeys(reversed(ids)))

    def metadata(self, message_id: str) -> MessageMeta:
        data = self._get(
            f"messages/{urllib.parse.quote(message_id, safe='')}",
            {"format": "metadata", "metadataHeaders": list(METADATA_HEADERS)},
        )
        headers: dict[str, str] = {}
        for header in (data.get("payload") or {}).get("headers") or []:
            name = str(header.get("name") or "").lower()
            if name and name not in headers:
                headers[name] = str(header.get("value") or "")
        return MessageMeta(
            message_id=str(data.get("id") or message_id),
            thread_id=data.get("threadId"),
            label_ids=tuple(data.get("labelIds") or ()),
            size_estimate=int(data.get("sizeEstimate") or 0),
            internal_date_ms=int(data.get("internalDate") or 0),
            headers=headers,
        )

    def raw(self, message_id: str) -> RawMessage:
        data = self._get(f"messages/{urllib.parse.quote(message_id, safe='')}", {"format": "raw"})
        return RawMessage(
            message_id=str(data.get("id") or message_id),
            thread_id=data.get("threadId"),
            label_ids=tuple(data.get("labelIds") or ()),
            internal_date_ms=int(data.get("internalDate") or 0),
            raw=_b64url(str(data.get("raw") or "")),
        )

    def labels(self) -> dict[str, str]:
        data = self._get("labels")
        return {
            str(label["id"]): str(label.get("name") or label["id"])
            for label in data.get("labels") or []
            if label.get("id")
        }
