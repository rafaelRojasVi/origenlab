#!/usr/bin/env python3
"""One-time operator bootstrap: authorize a Google account for CRM-Q1 Drive
workspace provisioning (``authorized_user_my_drive`` mode) and write the
resulting authorized-user credentials JSON for
``ORIGENLAB_DRIVE_CREDENTIALS_FILE``.

Requests ONLY the Drive scope this backend actually uses
(``origenlab_api.drive.factory.DRIVE_OAUTH_SCOPE`` --
https://www.googleapis.com/auth/drive). The narrower ``drive.file`` scope is
insufficient: the operator shares an existing quotations root folder and
master template with this identity, and ``drive.file`` cannot see items
merely shared to the account. Never requests Gmail, Sheets, profile, or any
other scope.

This is a LOCAL, OPERATOR-RUN, one-time bootstrap. It is never imported by
the running API and never runs in CI or scripts/validate.sh. When actually
executed it opens a real browser-based OAuth consent flow and makes a real
Drive API call to confirm the resulting identity -- this module's own test
suite (tests/test_authorize_drive_user_script.py) never exercises either;
everything is injected as a fake.

Usage:
    uv run python scripts/authorize_drive_user.py \\
      --client-secrets-file /secure/path/oauth-client.json \\
      --output-file /secure/path/origenlab-drive-credentials.json \\
      --expected-email contacto@origenlab.cl

--output-file must resolve outside this repository; the script refuses
otherwise. Never prints access tokens, refresh tokens, client secrets, or
any credential JSON content -- only the authenticated email and the output
path.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from origenlab_api.drive.factory import DRIVE_OAUTH_SCOPE

_REPO_ROOT = Path(__file__).resolve().parents[3]

# POSIX-only; a no-op flag on a platform that lacks it (this backend only
# ever runs this bootstrap on Linux/macOS operator machines).
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class DriveAuthorizationError(RuntimeError):
    """Bootstrap failed. Message is always safe to print (no secrets)."""


def _reject_path_inside_repo(resolved: Path) -> None:
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError:
        return
    raise DriveAuthorizationError(
        "refusing to write credentials inside the repository "
        f"(--output-file resolves under {_REPO_ROOT}); choose a path "
        "outside the repo, e.g. a secrets directory the production "
        "deployment mounts."
    )


def _default_run_flow(
    client_secrets_file: Path, scopes: list[str]
) -> dict[str, Any]:
    """Real OAuth desktop flow. Never called by this script's own tests."""

    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_file), scopes=scopes
    )
    # access_type=offline requests a refresh_token; prompt="select_account
    # consent" forces both an account picker and a fresh consent screen so
    # a refresh_token is issued even if this browser already has a session
    # with the account (Google otherwise silently skips re-issuing one).
    credentials = flow.run_local_server(
        access_type="offline",
        prompt="select_account consent",
    )
    return json.loads(credentials.to_json())


def _default_fetch_identity(credentials_json: dict[str, Any]) -> str:
    """Real identity check via Drive about.get. Never called by tests."""

    from google.auth.transport.requests import AuthorizedSession, Request  # type: ignore[import-not-found]
    from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]

    credentials = Credentials.from_authorized_user_info(credentials_json)
    if not credentials.valid:
        credentials.refresh(Request())

    session = AuthorizedSession(credentials)
    response = session.get(
        "https://www.googleapis.com/drive/v3/about",
        params={"fields": "user(emailAddress)"},
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    email = str((body.get("user") or {}).get("emailAddress") or "").strip()

    if not email:
        raise DriveAuthorizationError(
            "could not determine the authenticated Google identity"
        )

    return email


def authorize_drive_user(
    *,
    client_secrets_file: Path,
    output_file: Path,
    expected_email: str,
    replace_existing: bool,
    run_flow: Callable[[Path, list[str]], dict[str, Any]] = _default_run_flow,
    fetch_identity: Callable[[dict[str, Any]], str] = _default_fetch_identity,
) -> str:
    """Runs the bootstrap; returns the authenticated email on success.

    Raises DriveAuthorizationError (a safe, printable message -- never a
    secret) on any failure: missing client secrets file, a relative or
    repository-relative output path, an existing symlink at the output
    path (regardless of --replace-existing -- it is never followed, so it
    can never be used to write through to an arbitrary file), an existing
    output file without --replace-existing, or an authenticated identity
    that does not match --expected-email. Nothing is written to disk
    unless every check passes.
    """

    if not client_secrets_file.is_file():
        raise DriveAuthorizationError(
            f"client secrets file not found: {client_secrets_file}"
        )

    # A relative path is CWD-dependent and ambiguous for a one-time
    # credential-writing tool; expanduser() first so "~/..." still counts
    # as absolute.
    literal_output = output_file.expanduser()

    if not literal_output.is_absolute():
        raise DriveAuthorizationError(
            "--output-file must be an absolute path (relative paths "
            "depend on the current working directory, which is easy to "
            "get wrong for a one-time credential write)"
        )

    # is_symlink() lstat's only the final path component -- it does not
    # care whether ancestor directories are symlinks (that is normal and
    # fine; only the credential *file* itself must never be a symlink).
    # Checked before resolving so a symlink pointing anywhere -- inside or
    # outside the repo, dangling or not -- is caught here, before the
    # target is silently substituted in and treated as the real output.
    if literal_output.is_symlink():
        raise DriveAuthorizationError(
            f"refusing to write through an existing symlink at {literal_output} "
            "-- remove it first if you intend to replace it"
        )

    resolved_output = literal_output.resolve()
    _reject_path_inside_repo(resolved_output)

    if resolved_output.exists() and not replace_existing:
        raise DriveAuthorizationError(
            f"credentials file already exists: {resolved_output} "
            "(pass --replace-existing to overwrite)"
        )

    credentials_json = run_flow(client_secrets_file, [DRIVE_OAUTH_SCOPE])

    authenticated_email = fetch_identity(credentials_json)

    if authenticated_email.strip().lower() != expected_email.strip().lower():
        raise DriveAuthorizationError(
            "authenticated Google identity does not match --expected-email "
            f"({expected_email!r}); sign in as that account and retry"
        )

    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    # Create with restrictive permissions from the start (no window where
    # the file is world/group readable), then chmod again in case umask
    # widened the mode passed to os.open. O_NOFOLLOW is defense-in-depth
    # against a symlink appearing at this exact path in the (tiny) window
    # since the is_symlink() check above -- the primary guarantee.
    try:
        fd = os.open(
            str(resolved_output),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_NOFOLLOW,
            stat.S_IRUSR | stat.S_IWUSR,
        )
    except OSError as exc:
        if _O_NOFOLLOW and exc.errno == errno.ELOOP:
            raise DriveAuthorizationError(
                f"refusing to write through a symlink at {resolved_output}"
            ) from exc
        raise

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(credentials_json, handle)
    os.chmod(resolved_output, stat.S_IRUSR | stat.S_IWUSR)

    return authenticated_email


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-time operator bootstrap: authorize a Google account for "
            "CRM-Q1 Drive workspace provisioning (authorized_user_my_drive)."
        )
    )
    parser.add_argument(
        "--client-secrets-file",
        required=True,
        type=Path,
        help=(
            "Path to the OAuth Desktop client JSON downloaded from Google "
            "Cloud Console."
        ),
    )
    parser.add_argument(
        "--output-file",
        required=True,
        type=Path,
        help=(
            "Where to write the authorized-user credentials JSON. Must "
            "resolve outside this repository."
        ),
    )
    parser.add_argument(
        "--expected-email",
        required=True,
        help="The Google account this credential must belong to.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Overwrite an existing credentials file at --output-file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        email = authorize_drive_user(
            client_secrets_file=args.client_secrets_file,
            output_file=args.output_file,
            expected_email=args.expected_email,
            replace_existing=args.replace_existing,
            # Looked up by module-global name at call time (not bound as a
            # default parameter value) so tests can monkeypatch these two
            # names on the module without a browser or Google call.
            run_flow=_default_run_flow,
            fetch_identity=_default_fetch_identity,
        )
    except DriveAuthorizationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"ok: authorized {email}")
    print(f"file: {args.output_file}")
    print("mode: 0600")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
