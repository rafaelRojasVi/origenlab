"""One-time, local: authorize «Enviar prueba» to send as contacto@origenlab.cl (gmail.send only).

    uv run --extra drive-bootstrap python scripts/gmail_send_authorize.py \
        --client-secrets ~/Downloads/client_secret_<id>.json \
        --out ~/.config/origenlab-v2/gmail_send_token.json

Sign in as contacto@origenlab.cl in the browser that opens. The file it writes (mode 600) is
uploaded to Render as the secret file `gmail-send-token.json`. It refuses any other account.
Revoke: delete ORIGENLAB_V2_GMAIL_SEND_TOKEN_FILE on Render, or remove the app's access in the
contacto@ Google account (Seguridad -> Apps de terceros).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from origenlab_api.v2.gmail_send import SEND_SCOPE, SENDER_ADDRESS  # noqa: E402

SCOPES = [SEND_SCOPE, "openid", "https://www.googleapis.com/auth/userinfo.email"]


def token_payload(
    client_id: str, client_secret: str, refresh_token: str | None, id_token_email: str | None
) -> dict:
    email = (id_token_email or "").strip().lower()
    if email != SENDER_ADDRESS:
        raise SystemExit(
            f"signed in as {email or 'an unknown account'}, not {SENDER_ADDRESS}: nothing written"
        )
    if not refresh_token:
        raise SystemExit("Google returned no refresh token: remove the app's access and run again")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "address": email,
    }


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    p = argparse.ArgumentParser()
    p.add_argument("--client-secrets", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(a.client_secrets, scopes=SCOPES)
    creds = flow.run_local_server(
        port=0, login_hint=SENDER_ADDRESS, prompt="consent", access_type="offline"
    )
    raw = getattr(creds, "id_token", None)
    email = None
    if raw:
        from google.auth.transport import requests as gat
        from google.oauth2 import id_token

        email = id_token.verify_oauth2_token(raw, gat.Request(), creds.client_id).get("email")
    payload = token_payload(creds.client_id, creds.client_secret, creds.refresh_token, email)
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    print(f"written {out} for {payload['address']} (scope gmail.send)")


if __name__ == "__main__":
    main()
