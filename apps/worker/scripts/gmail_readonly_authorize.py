"""One-time, local, owner: consent as contacto@origenlab.cl for the Gmail capture — `gmail.readonly` only.

    cd apps/worker
    uv run --group bootstrap python scripts/gmail_readonly_authorize.py \
        --client-secrets ~/Downloads/client_secret_<id>.json \
        --out ~/.config/origenlab-v2/gmail_readonly_token.json

A browser opens; sign in as contacto@origenlab.cl and accept «Ver tus mensajes de correo». The file
written (mode 600, never printed) holds the three values the Render cron service needs. It refuses
another account, a consent broader or narrower than `gmail.readonly`, and a missing refresh token.
Revoke: the contacto@ Google account → Seguridad → Conexiones con terceros → remove the app.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from origenlab_worker.gmail_client import API_BASE, READONLY_SCOPE
from origenlab_worker.gmail_sync import MAILBOX_ADDRESS

REPO_ROOT = Path(__file__).resolve().parents[3]


def token_payload(client_id: str, client_secret: str, refresh_token: str | None,
                  profile_email: str | None, granted_scopes: Iterable[str] | None) -> dict[str, str]:
    email = (profile_email or "").strip().lower()
    if email != MAILBOX_ADDRESS:
        raise SystemExit(f"signed in as {email or 'an unknown account'}, not {MAILBOX_ADDRESS}: nothing written")
    if set(granted_scopes or ()) != {READONLY_SCOPE}:
        raise SystemExit("the consent is not exactly gmail.readonly: nothing written")
    if not refresh_token:
        raise SystemExit("Google returned no refresh token: remove the app's access and run again")
    return {
        "ORIGENLAB_WORKER_GMAIL_CLIENT_ID": client_id,
        "ORIGENLAB_WORKER_GMAIL_CLIENT_SECRET": client_secret,
        "ORIGENLAB_WORKER_GMAIL_REFRESH_TOKEN": refresh_token,
        "address": email,
    }


def _profile_email(access_token: str) -> str:
    request = urllib.request.Request(f"{API_BASE}/profile", headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return str(json.loads(resp.read()).get("emailAddress") or "")


def checked_out_path(path: Path) -> Path:
    """`--out` resolved, and refused if it is the repository or inside it (a token file there could
    be committed). Resolving first means `..` and symlinks cannot slip past the check."""
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit("--out must be outside the repository: nothing written")
    return resolved


def write_private(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.chmod(path, 0o600)


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    parser = argparse.ArgumentParser()
    parser.add_argument("--client-secrets", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = checked_out_path(args.out)  # before the browser opens: a refused path costs no consent
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secrets), scopes=[READONLY_SCOPE])
    creds = flow.run_local_server(port=0, login_hint=MAILBOX_ADDRESS, prompt="consent", access_type="offline")
    payload = token_payload(creds.client_id, creds.client_secret, creds.refresh_token,
                            _profile_email(creds.token), creds.granted_scopes or creds.scopes)
    write_private(out, payload)
    print(f"written {out} (mode 600) for {payload['address']} — scope gmail.readonly")


if __name__ == "__main__":
    main()
