"""CRM-Q1B tests for the one-time OAuth bootstrap CLI
(scripts/authorize_drive_user.py).

This script makes a real browser-based OAuth flow and a real Drive API call
when actually run by an operator -- but never in this test suite. Every
test here injects fake run_flow/fetch_identity callables (or monkeypatches
the module's defaults for the couple of CLI-level tests), so nothing here
opens a browser or talks to Google.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "authorize_drive_user.py"
    )
    spec = importlib.util.spec_from_file_location(
        "authorize_drive_user_script_under_test", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module():
    return _load_module()


FAKE_CREDENTIALS_JSON: dict[str, Any] = {
    "token": "fake-access-token-xyz",
    "refresh_token": "fake-refresh-token-abc",
    "client_id": "fake-client-id.apps.googleusercontent.com",
    "client_secret": "fake-client-secret-shh",
    "scopes": ["https://www.googleapis.com/auth/drive"],
}


def _fake_run_flow(expected_scopes: list[str] | None = None):
    calls: list[tuple[Path, list[str]]] = []

    def run_flow(client_secrets_file: Path, scopes: list[str]) -> dict[str, Any]:
        calls.append((client_secrets_file, scopes))
        return dict(FAKE_CREDENTIALS_JSON)

    run_flow.calls = calls  # type: ignore[attr-defined]
    return run_flow


def _fake_fetch_identity(email: str):
    calls: list[dict[str, Any]] = []

    def fetch_identity(credentials_json: dict[str, Any]) -> str:
        calls.append(credentials_json)
        return email

    fetch_identity.calls = calls  # type: ignore[attr-defined]
    return fetch_identity


def test_refuses_missing_client_secrets_file(module, tmp_path: Path) -> None:
    output_file = tmp_path / "out" / "creds.json"

    with pytest.raises(module.DriveAuthorizationError, match="client secrets"):
        module.authorize_drive_user(
            client_secrets_file=tmp_path / "missing-client-secrets.json",
            output_file=output_file,
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    assert not output_file.exists()


def test_refuses_output_path_inside_repo(module, tmp_path: Path) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[3]
    inside_repo_output = repo_root / "apps" / "api" / "leftover-creds.json"

    with pytest.raises(module.DriveAuthorizationError, match="repository"):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=inside_repo_output,
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    assert not inside_repo_output.exists()


def test_refuses_relative_output_path(module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A relative --output-file is CWD-dependent and ambiguous for a
    # one-time credential-writing tool: reject outright rather than
    # silently resolving it against whatever directory the operator
    # happened to be in (which is very likely inside the repo, since the
    # documented usage is `cd apps/api` first).
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    # CWD is safely outside the repo, so if a relative path were resolved
    # against it (instead of rejected outright) the repo-containment check
    # alone would not catch it -- proving this is a distinct guarantee.
    monkeypatch.chdir(tmp_path)

    with pytest.raises(module.DriveAuthorizationError, match="absolute"):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=Path("relative-creds.json"),
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    assert not (tmp_path / "relative-creds.json").exists()


def test_refuses_existing_symlink_output_and_never_writes_through_it(
    module, tmp_path: Path
) -> None:
    # A pre-existing symlink at the exact output path -- pointing anywhere,
    # not necessarily inside the repo -- must never be followed: writing
    # through it would silently overwrite whatever file the symlink target
    # actually names.
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    victim = tmp_path / "victim-file.txt"
    victim.write_text("do not overwrite me", encoding="utf-8")

    symlinked_output = tmp_path / "creds.json"
    symlinked_output.symlink_to(victim)

    with pytest.raises(module.DriveAuthorizationError, match="refusing to write through"):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=symlinked_output,
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    assert victim.read_text(encoding="utf-8") == "do not overwrite me"
    assert symlinked_output.is_symlink()


def test_refuses_symlink_output_even_with_replace_existing(
    module, tmp_path: Path
) -> None:
    # --replace-existing means "replace a previous credentials JSON at this
    # exact path", never "follow whatever this path is linked to".
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    victim = tmp_path / "victim-file.txt"
    victim.write_text("do not overwrite me", encoding="utf-8")

    symlinked_output = tmp_path / "creds.json"
    symlinked_output.symlink_to(victim)

    with pytest.raises(module.DriveAuthorizationError, match="refusing to write through"):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=symlinked_output,
            expected_email="contacto@origenlab.cl",
            replace_existing=True,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    assert victim.read_text(encoding="utf-8") == "do not overwrite me"


def test_refuses_dangling_symlink_output(module, tmp_path: Path) -> None:
    # A symlink whose target does not exist must still be refused -- it
    # must not be silently treated as "no existing file, safe to create".
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    symlinked_output = tmp_path / "creds.json"
    symlinked_output.symlink_to(tmp_path / "does-not-exist.json")

    with pytest.raises(module.DriveAuthorizationError, match="refusing to write through"):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=symlinked_output,
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    assert not (tmp_path / "does-not-exist.json").exists()


def test_refuses_existing_output_without_replace_flag(
    module, tmp_path: Path
) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    output_file = tmp_path / "creds.json"
    output_file.write_text('{"existing": true}', encoding="utf-8")

    with pytest.raises(module.DriveAuthorizationError, match="already exists"):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=output_file,
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    # The pre-existing file must be untouched.
    assert json.loads(output_file.read_text(encoding="utf-8")) == {"existing": True}


def test_create_only_mode_refuses_file_that_appears_during_the_oauth_flow(
    module, tmp_path: Path
) -> None:
    # TOCTOU: the preflight "does it exist" check happens before the
    # (potentially long) interactive OAuth flow and identity check. A file
    # created at the exact output path during that window -- by another
    # concurrent invocation, or anything else -- must never be silently
    # truncated when --replace-existing was not passed.
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    output_file = tmp_path / "creds.json"
    sentinel = "sentinel content that must survive"

    def racing_run_flow(client_secrets_file: Path, scopes: list[str]) -> dict[str, Any]:
        # Simulates a file appearing during the flow, after the initial
        # existence preflight already passed.
        output_file.write_text(sentinel, encoding="utf-8")
        return dict(FAKE_CREDENTIALS_JSON)

    with pytest.raises(module.DriveAuthorizationError):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=output_file,
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=racing_run_flow,
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    # Never truncated -- the racing writer's content survives untouched.
    assert output_file.read_text(encoding="utf-8") == sentinel


def test_replace_existing_mode_atomically_replaces_file_that_appears_during_flow(
    module, tmp_path: Path
) -> None:
    # --replace-existing explicitly authorizes overwriting whatever is at
    # the destination path, including a file that raced in during the flow
    # -- but the write itself must still be atomic (temp file + rename),
    # never an in-place truncate.
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output_file = output_dir / "creds.json"

    def racing_run_flow(client_secrets_file: Path, scopes: list[str]) -> dict[str, Any]:
        output_file.write_text("raced-in stale content", encoding="utf-8")
        return dict(FAKE_CREDENTIALS_JSON)

    email = module.authorize_drive_user(
        client_secrets_file=client_secrets,
        output_file=output_file,
        expected_email="contacto@origenlab.cl",
        replace_existing=True,
        run_flow=racing_run_flow,
        fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
    )

    assert email == "contacto@origenlab.cl"
    assert json.loads(output_file.read_text(encoding="utf-8")) == FAKE_CREDENTIALS_JSON

    # No leftover temp file in the output directory.
    leftovers = [
        p for p in output_file.parent.iterdir() if p.name != output_file.name
    ]
    assert leftovers == []


def test_replace_existing_mode_cleans_up_temp_file_when_rename_fails(
    module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output_file = output_dir / "creds.json"
    output_file.write_text('{"existing": true}', encoding="utf-8")

    def failing_replace(src: object, dst: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr(module.os, "replace", failing_replace)

    with pytest.raises(OSError):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=output_file,
            expected_email="contacto@origenlab.cl",
            replace_existing=True,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
        )

    # The pre-existing destination is untouched, and no temp file was left
    # behind in the output directory.
    assert json.loads(output_file.read_text(encoding="utf-8")) == {"existing": True}
    leftovers = [
        p for p in output_file.parent.iterdir() if p.name != output_file.name
    ]
    assert leftovers == []


def test_create_only_mode_uses_exclusive_create_never_truncating_in_place(
    module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # White-box: create-only mode must use O_EXCL (never O_TRUNC-without-
    # O_EXCL), so a colliding file can never be silently truncated.
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    captured_flags: list[int] = []
    real_open = module.os.open

    def spying_open(path: str, flags: int, mode: int) -> int:
        captured_flags.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(module.os, "open", spying_open)

    module.authorize_drive_user(
        client_secrets_file=client_secrets,
        output_file=tmp_path / "creds.json",
        expected_email="contacto@origenlab.cl",
        replace_existing=False,
        run_flow=_fake_run_flow(),
        fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
    )

    assert len(captured_flags) == 1
    assert captured_flags[0] & os.O_EXCL
    assert captured_flags[0] & os.O_CREAT


def test_allows_overwrite_with_replace_existing_flag(
    module, tmp_path: Path
) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    output_file = tmp_path / "creds.json"
    output_file.write_text('{"existing": true}', encoding="utf-8")

    email = module.authorize_drive_user(
        client_secrets_file=client_secrets,
        output_file=output_file,
        expected_email="contacto@origenlab.cl",
        replace_existing=True,
        run_flow=_fake_run_flow(),
        fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
    )

    assert email == "contacto@origenlab.cl"
    assert json.loads(output_file.read_text(encoding="utf-8")) == FAKE_CREDENTIALS_JSON


def test_writes_credentials_with_mode_0600(module, tmp_path: Path) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    output_file = tmp_path / "nested" / "creds.json"

    module.authorize_drive_user(
        client_secrets_file=client_secrets,
        output_file=output_file,
        expected_email="contacto@origenlab.cl",
        replace_existing=False,
        run_flow=_fake_run_flow(),
        fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
    )

    mode = stat.S_IMODE(output_file.stat().st_mode)
    assert mode == 0o600


def test_rejects_identity_mismatch_and_does_not_write_file(
    module, tmp_path: Path
) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    output_file = tmp_path / "creds.json"

    with pytest.raises(module.DriveAuthorizationError, match="expected-email"):
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=output_file,
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("rafarojasv6@gmail.com"),
        )

    assert not output_file.exists()


def test_identity_mismatch_error_never_names_the_wrong_account(
    module, tmp_path: Path
) -> None:
    # The error is safe to print; it must not leak which other account the
    # credentials actually belonged to.
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    with pytest.raises(module.DriveAuthorizationError) as excinfo:
        module.authorize_drive_user(
            client_secrets_file=client_secrets,
            output_file=tmp_path / "creds.json",
            expected_email="contacto@origenlab.cl",
            replace_existing=False,
            run_flow=_fake_run_flow(),
            fetch_identity=_fake_fetch_identity("someone-unexpected@gmail.com"),
        )

    assert "someone-unexpected@gmail.com" not in str(excinfo.value)


def test_requests_only_the_drive_scope(module, tmp_path: Path) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    run_flow = _fake_run_flow()

    module.authorize_drive_user(
        client_secrets_file=client_secrets,
        output_file=tmp_path / "creds.json",
        expected_email="contacto@origenlab.cl",
        replace_existing=False,
        run_flow=run_flow,
        fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
    )

    assert len(run_flow.calls) == 1
    _client_secrets_arg, scopes = run_flow.calls[0]
    assert scopes == ["https://www.googleapis.com/auth/drive"]


def test_never_prints_tokens_client_secrets_or_credential_contents(
    module, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    module.authorize_drive_user(
        client_secrets_file=client_secrets,
        output_file=tmp_path / "creds.json",
        expected_email="contacto@origenlab.cl",
        replace_existing=False,
        run_flow=_fake_run_flow(),
        fetch_identity=_fake_fetch_identity("contacto@origenlab.cl"),
    )

    output = capsys.readouterr().out + capsys.readouterr().err

    for secret in (
        "fake-access-token-xyz",
        "fake-refresh-token-abc",
        "fake-client-secret-shh",
    ):
        assert secret not in output


def test_main_returns_nonzero_and_prints_safe_error_on_missing_client_secrets(
    module, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = module.main(
        [
            "--client-secrets-file",
            str(tmp_path / "missing.json"),
            "--output-file",
            str(tmp_path / "creds.json"),
            "--expected-email",
            "contacto@origenlab.cl",
        ]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "client secrets" in err.lower()


def test_main_prints_ok_and_email_without_secrets_on_success(
    module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client_secrets = tmp_path / "client-secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")
    output_file = tmp_path / "creds.json"

    monkeypatch.setattr(module, "_default_run_flow", _fake_run_flow())
    monkeypatch.setattr(
        module, "_default_fetch_identity", _fake_fetch_identity("contacto@origenlab.cl")
    )

    exit_code = module.main(
        [
            "--client-secrets-file",
            str(client_secrets),
            "--output-file",
            str(output_file),
            "--expected-email",
            "contacto@origenlab.cl",
        ]
    )

    out = capsys.readouterr().out

    assert exit_code == 0
    assert "contacto@origenlab.cl" in out
    assert str(output_file) in out
    for secret in ("fake-access-token-xyz", "fake-refresh-token-abc"):
        assert secret not in out
    assert output_file.exists()
    assert stat.S_IMODE(output_file.stat().st_mode) == 0o600


def test_default_run_flow_requests_offline_access_and_forces_consent(
    module, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Prove the real (never-exercised-elsewhere) flow wiring requests
    # offline access and forces account-selection/consent, without ever
    # opening a browser: the InstalledAppFlow class itself is faked out.
    calls: dict[str, Any] = {}

    class FakeCredentials:
        def to_json(self) -> str:
            return json.dumps(FAKE_CREDENTIALS_JSON)

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path: str, scopes: list[str]):
            calls["from_client_secrets_file"] = (path, scopes)
            return cls()

        def run_local_server(self, **kwargs: Any) -> FakeCredentials:
            calls["run_local_server_kwargs"] = kwargs
            return FakeCredentials()

    fake_oauthlib_module = type(sys)("google_auth_oauthlib.flow")
    fake_oauthlib_module.InstalledAppFlow = FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_oauthlib_module)

    result = module._default_run_flow(
        Path("/fake/client-secrets.json"),
        ["https://www.googleapis.com/auth/drive"],
    )

    assert result == FAKE_CREDENTIALS_JSON
    assert calls["from_client_secrets_file"] == (
        "/fake/client-secrets.json",
        ["https://www.googleapis.com/auth/drive"],
    )
    kwargs = calls["run_local_server_kwargs"]
    assert kwargs.get("access_type") == "offline"
    assert "consent" in kwargs.get("prompt", "")
