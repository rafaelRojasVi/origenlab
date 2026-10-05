"""Read contacto@origenlab.cl through the Gmail REST API — `gmail.readonly` only.

Mirrors the campaign test-send client (`apps/api/src/origenlab_api/v2/gmail_send.py`, merged on
main in #633: `Transport`, `urllib_transport`, the refresh-with-margin `_access_token` and the
`_call` that names a network failure) without importing it: plain HTTPS through `urllib`, no
Google SDK at runtime.

Error kinds. `GmailAuthError`: `invalid_grant`, `token_refresh_failed`, `scope_not_readonly`,
`unauthorized`. `GmailNotFound`: `not_found`. `HistoryExpired`: `history_expired`.
`GmailUnavailable`: `network`, `http_<status>` (also for a token endpoint 429/5xx),
`invalid_json` (any answer whose shape or values are not what Gmail documents),
`retries_exhausted`, `too_many_pages`. No message ever carries a token, address, subject or id.

The refresh token belongs to a Workspace *Internal* OAuth client that contacto@ consented to once
(`scripts/gmail_readonly_authorize.py`). Every refresh re-checks that Google granted exactly
`gmail.readonly`: a token that could send or delete — V1's desktop token carries
`https://mail.google.com/` — is refused before a single Gmail request is made.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT_SECONDS = 30.0
REFRESH_MARGIN_SECONDS = 60.0
MAX_ATTEMPTS = 4
PAGE_SIZE = 500
MAX_PAGES = 1000
METADATA_HEADERS = ("From", "Subject", "Message-ID", "Date")
#: A message becomes capturable by arriving *or* by gaining one of these labels: a draft that is
#: sent gets `SENT`, a message moved out of spam gets `INBOX`.
CAPTURE_LABELS = frozenset({"SENT", "INBOX"})
HISTORY_TYPES = ("messageAdded", "labelAdded")
RETRYABLE = frozenset({429, 500, 502, 503, 504})
_MALFORMED = (ValueError, TypeError, AttributeError, KeyError, binascii.Error)

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


@contextmanager
def _well_formed() -> Iterator[None]:
    """Whatever a 200 body holds, a wrong shape or value is `invalid_json` and never echoed."""
    try:
        yield
    except _MALFORMED:
        raise GmailUnavailable("invalid_json") from None


def _items(value: Any) -> list[dict[str, Any]]:
    """A JSON list of objects, or nothing; anything else is malformed."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise TypeError("expected a list of objects")
    return value


def _labels(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError("labelIds")
    return tuple(str(v) for v in value)


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
        parsed = _json(raw)
        data: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        if status != 200:
            if status in RETRYABLE:
                raise GmailUnavailable(f"http_{status}")
            if data.get("error") == "invalid_grant":
                raise GmailAuthError("invalid_grant")
            raise GmailAuthError("token_refresh_failed")
        if not isinstance(parsed, dict):
            raise GmailUnavailable("invalid_json")
        with _well_formed():
            scopes = tuple(sorted(str(data.get("scope") or "").split()))
            expires_in = float(data.get("expires_in") or 0)
        if scopes != (READONLY_SCOPE,):
            raise GmailAuthError("scope_not_readonly")
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise GmailAuthError("token_refresh_failed")
        self._access = access
        self._expires_at = self._clock() + expires_in
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
        attempt = 0
        while True:
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
                attempt += 1
                continue
            raise GmailUnavailable(f"http_{status}")

    # ------------------------------------------------------------------ the reads

    def profile(self) -> Profile:
        data = self._get("profile")
        return Profile(
            email_address=str(data.get("emailAddress") or "").strip().lower(),
            history_id=str(data.get("historyId") or ""),
        )

    def history(self, start_history_id: str) -> HistoryWindow:
        """Every message added since `start_history_id`, oldest first, each id once. A message that
        gained `SENT` or `INBOX` (a draft sent later, a message moved out of spam) counts as added.
        Other label changes and every label removal are not tracked."""
        ids: dict[str, None] = {}
        latest, token = start_history_id, None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {
                "startHistoryId": start_history_id,
                "historyTypes": list(HISTORY_TYPES),
                "maxResults": PAGE_SIZE,
            }
            if token:
                params["pageToken"] = token
            try:
                data = self._get("history", params)
            except GmailNotFound:
                raise HistoryExpired("history_expired") from None
            with _well_formed():
                for record in _items(data.get("history")):
                    for added in _items(record.get("messagesAdded")):
                        message_id = (added.get("message") or {}).get("id")
                        if message_id:
                            ids.setdefault(str(message_id), None)
                    for changed in _items(record.get("labelsAdded")):
                        message_id = (changed.get("message") or {}).get("id")
                        if message_id and CAPTURE_LABELS & set(_labels(changed.get("labelIds"))):
                            ids.setdefault(str(message_id), None)
                latest = str(data.get("historyId") or latest)
                token = data.get("nextPageToken")
            if not token:
                return HistoryWindow(tuple(ids), latest)
        raise GmailUnavailable("too_many_pages")

    def messages_after(self, epoch_seconds: int) -> list[str]:
        """The resync list: every message after `epoch_seconds`, oldest first, spam and trash excluded."""
        ids: list[str] = []
        token = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"q": f"after:{int(epoch_seconds)}", "maxResults": PAGE_SIZE}
            if token:
                params["pageToken"] = token
            data = self._get("messages", params)
            with _well_formed():
                ids.extend(str(m["id"]) for m in _items(data.get("messages")) if m.get("id"))
                token = data.get("nextPageToken")
            if not token:
                return list(dict.fromkeys(reversed(ids)))
        raise GmailUnavailable("too_many_pages")

    def metadata(self, message_id: str) -> MessageMeta:
        data = self._get(
            f"messages/{urllib.parse.quote(message_id, safe='')}",
            {"format": "metadata", "metadataHeaders": list(METADATA_HEADERS)},
        )
        with _well_formed():
            headers: dict[str, str] = {}
            for header in _items((data.get("payload") or {}).get("headers")):
                name = str(header.get("name") or "").lower()
                if name and name not in headers:
                    headers[name] = str(header.get("value") or "")
            return MessageMeta(
                message_id=str(data.get("id") or message_id),
                thread_id=data.get("threadId"),
                label_ids=_labels(data.get("labelIds")),
                size_estimate=int(data.get("sizeEstimate") or 0),
                internal_date_ms=int(data.get("internalDate") or 0),
                headers=headers,
            )

    def raw(self, message_id: str) -> RawMessage:
        data = self._get(f"messages/{urllib.parse.quote(message_id, safe='')}", {"format": "raw"})
        with _well_formed():
            return RawMessage(
                message_id=str(data.get("id") or message_id),
                thread_id=data.get("threadId"),
                label_ids=_labels(data.get("labelIds")),
                internal_date_ms=int(data.get("internalDate") or 0),
                raw=_b64url(str(data.get("raw") or "")),
            )

    def labels(self) -> dict[str, str]:
        data = self._get("labels")
        with _well_formed():
            return {
                str(label["id"]): str(label.get("name") or label["id"])
                for label in _items(data.get("labels"))
                if label.get("id")
            }
