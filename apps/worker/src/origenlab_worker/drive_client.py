"""Google Drive v3 for the quote filing — the archiver's `DrivePort`, over plain HTTPS.

The same shape as `gmail_client.py` (and `apps/api/scripts/quote_drive_case_archive.py`'s port):
`urllib`, no Google SDK, a refresh-with-margin access token, a named error for every failure and
never a token, an address, a file name or an id in one.

**Scope.** The refresh token is contacto@'s consent to `https://www.googleapis.com/auth/drive` —
the scope the case archive already uses (`apps/api/scripts/authorize_drive_user.py`), because the
`Cotizaciones/Casos` folders were created by that archive, and `drive.file` could not see them.
Every refresh re-checks the grant: a token that carries any scope outside Drive and the sign-in
identity scopes (Gmail above all) is refused before a single Drive request.

**Writes.** Only the two the archiver needs: create a folder, upload a PDF. Nothing here renames,
moves, trashes or deletes; `quote_case_archive.archive_case` additionally refuses any write under
a protected (legacy) folder and verifies every upload twice.

Error kinds (`DriveError.kind`): `invalid_grant`, `token_refresh_failed`, `scope_not_drive`,
`unauthorized`, `network`, `http_<status>`, `invalid_json`, `too_many_pages`.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
#: What a Drive consent may also carry: the sign-in identity, nothing that reads or sends mail.
IDENTITY_SCOPES = frozenset({"openid", "email", "https://www.googleapis.com/auth/userinfo.email"})
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
PDF_MIME = "application/pdf"
FIELDS = "id,name,mimeType,parents,size,sha256Checksum,trashed,appProperties,webViewLink"
TIMEOUT_SECONDS = 120.0
REFRESH_MARGIN_SECONDS = 60.0
MAX_ATTEMPTS = 4
MAX_PAGES = 50
RETRYABLE = frozenset({429, 500, 502, 503, 504})

Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]


class DriveError(RuntimeError):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class DriveAuthError(DriveError):
    """`invalid_grant`, `token_refresh_failed`, `scope_not_drive` or `unauthorized`."""


@dataclass(frozen=True)
class DriveCredentials:
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)


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


def _quote_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveRest:
    """The seven operations `quote_case_archive.DrivePort` names."""

    def __init__(
        self,
        credentials: DriveCredentials,
        *,
        transport: Transport = urllib_transport,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        api: str = API,
        upload: str = UPLOAD,
        token_url: str = TOKEN_URL,
    ) -> None:
        self._credentials = credentials
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._api = api.rstrip("/")
        self._upload = upload
        self._token_url = token_url
        self._access: str | None = None
        self._expires_at = 0.0
        self._principal: str | None = None

    # ------------------------------------------------------------------ transport and token

    def _call(self, method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        try:
            return self._transport(method, url, headers, body, TIMEOUT_SECONDS)
        except (OSError, http.client.HTTPException):
            raise DriveError("network") from None

    def _refresh(self) -> str:
        body = urllib.parse.urlencode({
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
            "refresh_token": self._credentials.refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        status, raw = self._call("POST", self._token_url,
                                 {"Content-Type": "application/x-www-form-urlencoded"}, body)
        data = _json(raw)
        data = data if isinstance(data, dict) else {}
        if status != 200:
            if status in RETRYABLE:
                raise DriveError(f"http_{status}")
            if data.get("error") == "invalid_grant":
                raise DriveAuthError("invalid_grant")
            raise DriveAuthError("token_refresh_failed")
        scopes = set(str(data.get("scope") or "").split())
        if DRIVE_SCOPE not in scopes or scopes - {DRIVE_SCOPE} - IDENTITY_SCOPES:
            raise DriveAuthError("scope_not_drive")
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise DriveAuthError("token_refresh_failed")
        try:
            expires_in = float(data.get("expires_in") or 0)
        except (TypeError, ValueError):
            raise DriveError("invalid_json") from None
        self._access, self._expires_at = access, self._clock() + expires_in
        return access

    def _token(self) -> str:
        if self._access and self._clock() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._access
        return self._refresh()

    def _request(self, method: str, url: str, *, params: Mapping[str, Any] | None = None,
                 body: bytes = b"", content_type: str | None = None,
                 allow_404: bool = False) -> bytes | None:
        query = urllib.parse.urlencode(dict(params or {}), doseq=True)
        full = url + (f"?{query}" if query else "")
        refreshed, attempt = False, 0
        while True:
            headers = {"Authorization": f"Bearer {self._token()}"}
            if content_type:
                headers["Content-Type"] = content_type
            status, raw = self._call(method, full, headers, body)
            if 200 <= status < 300:
                return raw
            if status == 401 and not refreshed:
                self._access, refreshed = None, True
                continue
            if status == 401:
                raise DriveAuthError("unauthorized")
            if status == 404 and allow_404:
                return None
            if status in RETRYABLE and attempt < MAX_ATTEMPTS - 1:
                self._sleep(2.0**attempt)
                attempt += 1
                continue
            raise DriveError(f"http_{status}")

    def _json_request(self, method: str, url: str, **kw: Any) -> dict[str, Any] | None:
        raw = self._request(method, url, **kw)
        if raw is None:
            return None
        data = _json(raw)
        if not isinstance(data, dict):
            raise DriveError("invalid_json")
        return data

    # ------------------------------------------------------------------ DrivePort

    def principal(self) -> str:
        if self._principal is None:
            data = self._json_request("GET", f"{self._api}/about", params={"fields": "user(emailAddress)"}) or {}
            self._principal = str((data.get("user") or {}).get("emailAddress") or "").strip().lower()
        return self._principal

    def get(self, file_id: str) -> dict[str, Any] | None:
        return self._json_request("GET", f"{self._api}/files/{urllib.parse.quote(file_id, safe='')}",
                                  params={"fields": FIELDS, "supportsAllDrives": "true"}, allow_404=True)

    def _list(self, q: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"q": q, "fields": f"nextPageToken,files({FIELDS})", "pageSize": 1000,
                                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            data = self._json_request("GET", f"{self._api}/files", params=params) or {}
            files = data.get("files") or []
            if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
                raise DriveError("invalid_json")
            out += files
            token = data.get("nextPageToken")
            if not token:
                return out
        raise DriveError("too_many_pages")

    def find_by_property(self, key: str, value: str) -> list[dict[str, Any]]:
        return self._list(f"appProperties has {{ key='{_quote_q(key)}' and value='{_quote_q(value)}' }} "
                          "and trashed = false")

    def children(self, folder_id: str) -> list[dict[str, Any]]:
        return self._list(f"'{_quote_q(folder_id)}' in parents and trashed = false")

    def create_folder(self, *, name: str, parent_id: str, app_properties: Mapping[str, str]) -> dict[str, Any]:
        meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id], "appProperties": dict(app_properties)}
        data = self._json_request("POST", f"{self._api}/files", params={"fields": FIELDS, "supportsAllDrives": "true"},
                                  body=json.dumps(meta, ensure_ascii=False).encode(),
                                  content_type="application/json; charset=UTF-8")
        return data or {}

    def upload_pdf(self, *, name: str, parent_id: str, app_properties: Mapping[str, str],
                   data: bytes) -> dict[str, Any]:
        meta = {"name": name, "parents": [parent_id], "mimeType": PDF_MIME, "appProperties": dict(app_properties)}
        boundary = "origenlab-" + hashlib.sha256(data).hexdigest()[:16]
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                + json.dumps(meta, ensure_ascii=False).encode() + b"\r\n"
                + f"--{boundary}\r\nContent-Type: application/pdf\r\n\r\n".encode() + data
                + f"\r\n--{boundary}--\r\n".encode())
        out = self._json_request("POST", self._upload,
                                 params={"uploadType": "multipart", "fields": "id", "supportsAllDrives": "true"},
                                 body=body, content_type=f"multipart/related; boundary={boundary}")
        return out or {}

    def download(self, file_id: str) -> bytes:
        raw = self._request("GET", f"{self._api}/files/{urllib.parse.quote(file_id, safe='')}",
                            params={"alt": "media", "supportsAllDrives": "true"})
        return raw or b""
