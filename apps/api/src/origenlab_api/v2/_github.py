"""Minimal GitHub REST helpers for «Publicar web» (Contents API + workflow dispatch).

Standard library only, with an injectable transport so tests never touch the network. The token is
a `SecretStr` that is read exactly once, to build the Authorization header; it is never logged and
never part of an exception: a `GithubError` carries a stable code, the HTTP status and nothing from
the response body (GitHub echoes request details in error messages).
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import SecretStr

API = "https://api.github.com"
WEB_URL = "https://github.com"
WORKFLOW_FILE = "web-deploy.yml"
TIMEOUT_SECONDS = 20.0

Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")


class GithubError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class GithubPublishConfig:
    token: SecretStr = field(repr=False)
    repo: str
    ref: str
    committer_name: str
    committer_email: str
    workflow: str = WORKFLOW_FILE

    def __post_init__(self) -> None:
        if not _REPO.fullmatch(self.repo) or ".." in self.repo:
            raise ValueError("github_repo must look like owner/name")
        if not _REF.fullmatch(self.ref) or ".." in self.ref:
            raise ValueError("github_ref is not a valid branch name")


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _call(
    config: GithubPublishConfig, method: str, url: str, body: dict | None, transport: Transport, code: str,
) -> tuple[int, bytes]:
    headers = {
        "Authorization": f"Bearer {config.token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "origenlab-api",
    }
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        return transport(method, url, headers, payload, TIMEOUT_SECONDS)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # `exc` can name the URL but never the headers; still, only its type is kept.
        raise GithubError(code, f"GitHub could not be reached ({type(exc).__name__})") from None


def _contents_url(config: GithubPublishConfig, path: str) -> str:
    return f"{API}/repos/{config.repo}/contents/{urllib.parse.quote(path)}"


def github_file_sha(config: GithubPublishConfig, path: str, *, transport: Transport = urllib_transport) -> str | None:
    """The blob sha of `path` on the configured branch, or None when the file does not exist yet."""
    url = f"{_contents_url(config, path)}?ref={urllib.parse.quote(config.ref)}"
    status, raw = _call(config, "GET", url, None, transport, "commit_failed")
    if status == 404:
        return None
    if status != 200:
        raise GithubError("commit_failed", f"GitHub answered HTTP {status} reading the file", status)
    try:
        sha = json.loads(raw)["sha"]
    except (ValueError, KeyError, TypeError):
        raise GithubError("commit_failed", "GitHub returned an unreadable file record", status) from None
    if not isinstance(sha, str) or not sha:
        raise GithubError("commit_failed", "GitHub returned an unreadable file record", status)
    return sha


def github_put_file(
    config: GithubPublishConfig, path: str, content: bytes, message: str, sha: str | None,
    *, transport: Transport = urllib_transport,
) -> str:
    """Create or update `path` on the configured branch; returns the new commit sha."""
    body: dict = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": config.ref,
        "committer": {"name": config.committer_name, "email": config.committer_email},
    }
    if sha:
        body["sha"] = sha
    status, raw = _call(config, "PUT", _contents_url(config, path), body, transport, "commit_failed")
    if status not in (200, 201):
        raise GithubError("commit_failed", f"GitHub answered HTTP {status} writing the file", status)
    try:
        commit = json.loads(raw)["commit"]["sha"]
    except (ValueError, KeyError, TypeError):
        raise GithubError("commit_failed", "GitHub returned an unreadable commit record", status) from None
    if not isinstance(commit, str) or not commit:
        raise GithubError("commit_failed", "GitHub returned an unreadable commit record", status)
    return commit


def github_dispatch_workflow(config: GithubPublishConfig, *, transport: Transport = urllib_transport) -> str:
    """Start the deploy workflow with `apply=true`; returns the workflow's page (no run id yet).

    `apply: "true"` still waits for the `production` environment approval in GitHub; that human
    gate is deliberately outside this API.
    """
    url = f"{API}/repos/{config.repo}/actions/workflows/{config.workflow}/dispatches"
    status, _raw = _call(
        config, "POST", url, {"ref": config.ref, "inputs": {"apply": "true"}}, transport, "dispatch_failed",
    )
    if status != 204:
        raise GithubError("dispatch_failed", f"GitHub answered HTTP {status} starting the deploy", status)
    return f"{WEB_URL}/{config.repo}/actions/workflows/{config.workflow}"
