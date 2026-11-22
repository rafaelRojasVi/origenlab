"""Google Workspace / Gmail IMAP OAuth2 helpers (optional dependency group `gmail` or `workspace`)."""

from __future__ import annotations

import imaplib
from pathlib import Path

# IMAP (not Gmail REST) requires full mail scope — see Google’s “Using OAuth 2.0 to access Google APIs” for Gmail.
GMAIL_IMAP_SCOPE = "https://mail.google.com/"


def xoauth2_initial_response(user_email: str, access_token: str) -> bytes:
    """Raw UTF-8 SASL XOAUTH2 payload for Gmail IMAP.

    ``imaplib.IMAP4.authenticate`` base64-encodes the callback return value on
    the wire; do not pre-encode or Gmail returns ``Invalid SASL argument``.
    """
    auth_string = f"user={user_email}\x01auth=Bearer {access_token}\x01\x01"
    return auth_string.encode("utf-8")


def xoauth2_authenticate(imap_conn: imaplib.IMAP4_SSL, user_email: str, access_token: str) -> None:
    blob = xoauth2_initial_response(user_email, access_token)

    def _auth(_challenge: bytes | None) -> bytes:
        return blob

    imap_conn.authenticate("XOAUTH2", _auth)


def load_credentials_for_gmail_imap(
    *,
    client_secrets_json: Path,
    token_json: Path,
    open_browser: bool = True,
) -> "object":  # google.oauth2.credentials.Credentials
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [GMAIL_IMAP_SCOPE]
    creds: Credentials | None = None
    if token_json.is_file():
        creds = Credentials.from_authorized_user_file(str(token_json), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_json.parent.mkdir(parents=True, exist_ok=True)
        token_json.write_text(creds.to_json(), encoding="utf-8")
        return creds

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_json), scopes)
    creds = flow.run_local_server(port=0, open_browser=open_browser)
    token_json.parent.mkdir(parents=True, exist_ok=True)
    token_json.write_text(creds.to_json(), encoding="utf-8")
    return creds


def refresh_gmail_credentials(creds: "object", *, token_json: Path) -> "object":
    """Refresh once, now, so a batch starts on a full-lifetime access token.

    ``load_credentials_for_gmail_imap`` returns a token that is merely still
    valid -- possibly minutes from expiry, which a long send loop outlives.
    Credentials without a refresh token are returned unchanged.
    """
    if not getattr(creds, "refresh_token", None):
        return creds
    from google.auth.transport.requests import Request

    creds.refresh(Request())
    token_json.parent.mkdir(parents=True, exist_ok=True)
    token_json.write_text(creds.to_json(), encoding="utf-8")
    return creds
