"""One-time, local: authorize «Enviar prueba» to send as contacto@origenlab.cl (gmail.compose + gmail.settings.basic by default).

    uv run --extra drive-bootstrap python scripts/gmail_send_authorize.py [--scopes web-form|send] \
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
from origenlab_api.v2.gmail_send import COMPOSE_SCOPE, SEND_SCOPE, SENDER_ADDRESS  # noqa: E402

IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
SETTINGS_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic"
#: `web-form` (default): the production token — compose also sends, settings.basic for the
#: signature. `send`: the original gmail.send-only token for «Enviar prueba».
SCOPE_SETS = {
    "web-form": [COMPOSE_SCOPE, SETTINGS_SCOPE, *IDENTITY_SCOPES],
    "send": [SEND_SCOPE, *IDENTITY_SCOPES],
}
SCOPES = SCOPE_SETS["web-form"]


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


def write_token_file(out: Path, payload: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.chmod(out, 0o600)  # an existing file keeps its old mode through os.open


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    p = argparse.ArgumentParser()
    p.add_argument("--client-secrets", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--scopes", choices=sorted(SCOPE_SETS), default="web-form")
    a = p.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(a.client_secrets, scopes=SCOPE_SETS[a.scopes])
    creds = flow.run_local_server(
        port=0, login_hint=SENDER_ADDRESS, prompt="consent", access_type="offline"
    )
    raw = getattr(creds, "id_token", None)
    email = None
    if raw:
        from google.auth.transport import requests as gat
        from google.oauth2 import id_token

        try:
            email = id_token.verify_oauth2_token(raw, gat.Request(), creds.client_id).get("email")
        except Exception as exc:  # fail closed; never echo the token
            raise SystemExit(
                f"could not verify the signed-in account: {type(exc).__name__}: nothing written"
            ) from None
    payload = token_payload(creds.client_id, creds.client_secret, creds.refresh_token, email)
    out = Path(a.out).expanduser()
    write_token_file(out, payload)
    print(f"written {out} for {payload['address']} (scopes: {a.scopes})")


if __name__ == "__main__":
    main()
