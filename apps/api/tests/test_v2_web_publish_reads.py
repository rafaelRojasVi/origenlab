"""GET /v2/web/model-sheet/{id} and GET /v2/web/publish-status/{id}: the editor's two reads.

No database, no network: the GitHub helpers are monkeypatched on the route module; the helpers
themselves run against a fake transport.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2 import _github, web_publish
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from test_v2_web_publish import _valid_sheet

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}
TOKEN = "ghp_FAKE_TOKEN_FOR_TESTS_ONLY"
SHEET_URL = "/v2/web/model-sheet/modelo-ficticio-100"
STATUS_URL = "/v2/web/publish-status/modelo-ficticio-100"
HTML_URL = "https://github.com/example-owner/example-repo/blob/main/apps/web/src/content/modelos/modelo-ficticio-100.json"


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


def _operator(role: str) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="00000000-0000-0000-0000-000000000001",
        email_norm="op@example.test", display_name="Op", role=role, status="active",
    )


def _config() -> _github.GithubPublishConfig:
    return _github.GithubPublishConfig(
        token=SecretStr(TOKEN), repo="example-owner/example-repo", ref="main",
        committer_name="OrigenLab panel", committer_email="panel@origenlab.cl",
    )


def _client(role: str = "sales") -> TestClient:
    app = FastAPI()
    app.include_router(web_publish.web_publish_router)
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    app.state.web_publish_config = _config()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_state():
    web_publish.reset_status_cache()
    yield
    web_publish.reset_status_cache()


# ------------------------------------------------------------------------------ model-sheet read

def _file(sheet: dict) -> dict:
    return {"content": (json.dumps(sheet, ensure_ascii=False, indent=2) + "\n").encode(), "sha": "b" * 40,
            "html_url": HTML_URL}


def test_sheet_read_happy_path(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(web_publish, "github_read_file", lambda config, path: seen.append(path) or _file(_valid_sheet()))
    resp = _client().get(SHEET_URL, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "model_id": "modelo-ficticio-100", "sheet": _valid_sheet(), "sha": "b" * 40,
        "html_url": HTML_URL, "valid": True, "errors": [],
    }
    assert seen == ["apps/web/src/content/modelos/modelo-ficticio-100.json"]
    assert TOKEN not in resp.text


def test_invalid_sheet_is_returned_with_its_errors(monkeypatch) -> None:
    sheet = _valid_sheet()
    sheet["price"] = 1
    del sheet["specs"][0]["source"]
    monkeypatch.setattr(web_publish, "github_read_file", lambda config, path: _file(sheet))
    body = _client().get(SHEET_URL, headers=HEADERS).json()
    assert body["sheet"] == sheet
    assert body["valid"] is False
    assert body["errors"] and all({"path", "message"} <= set(e) for e in body["errors"])


def test_missing_sheet_is_404(monkeypatch) -> None:
    monkeypatch.setattr(web_publish, "github_read_file", lambda config, path: None)
    resp = _client().get(SHEET_URL, headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "sheet_not_found"


def test_github_failure_reading_is_502(monkeypatch) -> None:
    def boom(config, path):
        raise _github.GithubError("read_failed", "GitHub answered HTTP 500 reading the file", 500)

    monkeypatch.setattr(web_publish, "github_read_file", boom)
    resp = _client().get(SHEET_URL, headers=HEADERS)
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "read_failed"
    assert TOKEN not in resp.text


@pytest.mark.parametrize("content", [b"not json", b"[1, 2]"])
def test_unreadable_sheet_content_is_502(monkeypatch, content) -> None:
    monkeypatch.setattr(web_publish, "github_read_file",
                        lambda config, path: {"content": content, "sha": "b" * 40, "html_url": HTML_URL})
    resp = _client().get(SHEET_URL, headers=HEADERS)
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "read_failed"


def test_deeply_nested_sheet_file_is_502_not_500(monkeypatch) -> None:
    # Balanced, so it is valid JSON that only the parser's recursion limit rejects.
    nested = b"[" * 50_000 + b"]" * 50_000
    monkeypatch.setattr(web_publish, "github_read_file",
                        lambda config, path: {"content": nested, "sha": "b" * 40, "html_url": HTML_URL})
    resp = _client().get(SHEET_URL, headers=HEADERS)
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "read_failed"


@pytest.mark.parametrize("bad", ["Modelo-X", "a..b", "a_b", "x" * 61])
def test_bad_model_id_is_422(monkeypatch, bad) -> None:
    monkeypatch.setattr(web_publish, "github_read_file", lambda config, path: pytest.fail("must not call GitHub"))
    monkeypatch.setattr(web_publish, "github_latest_deploy_run", lambda config: pytest.fail("must not call GitHub"))
    assert _client().get(f"/v2/web/model-sheet/{bad}", headers=HEADERS).status_code == 422
    assert _client().get(f"/v2/web/publish-status/{bad}", headers=HEADERS).status_code == 422


# --------------------------------------------------------------------------------------- role gate

@pytest.mark.parametrize("url", [SHEET_URL, STATUS_URL])
def test_viewer_is_403_and_anonymous_401(monkeypatch, url) -> None:
    monkeypatch.setattr(web_publish, "github_read_file", lambda config, path: pytest.fail("no call"))
    monkeypatch.setattr(web_publish, "github_latest_deploy_run", lambda config: pytest.fail("no call"))
    assert _client("viewer").get(url, headers=HEADERS).status_code == 403
    assert _client().get(url).status_code == 401


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_may_read(monkeypatch, role) -> None:
    monkeypatch.setattr(web_publish, "github_read_file", lambda config, path: _file(_valid_sheet()))
    monkeypatch.setattr(web_publish, "github_latest_deploy_run", lambda config: None)
    assert _client(role).get(SHEET_URL, headers=HEADERS).status_code == 200
    web_publish.reset_status_cache()
    assert _client(role).get(STATUS_URL, headers=HEADERS).status_code == 200


# ----------------------------------------------------------------------------------- publish status

def _run(status: str, conclusion: str | None = None, **extra) -> dict:
    return {"status": status, "conclusion": conclusion,
            "html_url": "https://github.com/example-owner/example-repo/actions/runs/42",
            "updated_at": "2026-11-07T12:00:00Z", "head_sha": "d" * 40, **extra}


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("queued", None, "queued"),
        ("requested", None, "queued"),
        ("pending", None, "queued"),
        ("in_progress", None, "running"),
        ("waiting", None, "waiting"),
        ("completed", "success", "success"),
        ("completed", "failure", "failure"),
        ("completed", "cancelled", "failure"),
        ("completed", "timed_out", "failure"),
        ("completed", None, "unknown"),
        ("something_new", None, "unknown"),
    ],
)
def test_status_mapping(monkeypatch, status, conclusion, expected) -> None:
    monkeypatch.setattr(web_publish, "github_latest_deploy_run", lambda config: _run(status, conclusion))
    resp = _client().get(STATUS_URL, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": expected, "run_url": "https://github.com/example-owner/example-repo/actions/runs/42",
        "updated_at": "2026-11-07T12:00:00Z", "head_sha": "d" * 40,
    }


def test_no_runs_is_unknown(monkeypatch) -> None:
    monkeypatch.setattr(web_publish, "github_latest_deploy_run", lambda config: None)
    assert _client().get(STATUS_URL, headers=HEADERS).json() == {
        "status": "unknown", "run_url": None, "updated_at": None, "head_sha": None}


def test_status_is_cached_for_10_seconds(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(web_publish, "_clock", lambda: now[0])
    calls = []
    monkeypatch.setattr(web_publish, "github_latest_deploy_run", lambda config: calls.append(1) or _run("queued"))
    client = _client()
    first = client.get(STATUS_URL, headers=HEADERS).json()
    now[0] += 9.0
    assert client.get(STATUS_URL, headers=HEADERS).json() == first
    assert len(calls) == 1
    now[0] += 1.5
    client.get(STATUS_URL, headers=HEADERS)
    assert len(calls) == 2


def test_status_failure_is_502_and_cached_for_5_seconds(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(web_publish, "_clock", lambda: now[0])
    calls = []

    def boom(config):
        calls.append(1)
        raise _github.GithubError("status_failed", "GitHub answered HTTP 500 reading the runs", 500)

    monkeypatch.setattr(web_publish, "github_latest_deploy_run", boom)
    client = _client()
    resp = client.get(STATUS_URL, headers=HEADERS)
    assert resp.status_code == 502 and resp.json()["detail"]["code"] == "status_failed"
    now[0] += 4.0
    again = client.get(STATUS_URL, headers=HEADERS)
    assert again.status_code == 502 and again.json() == resp.json()
    assert len(calls) == 1
    now[0] += 1.5
    client.get(STATUS_URL, headers=HEADERS)
    assert len(calls) == 2


def test_status_recovers_after_a_cached_failure(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(web_publish, "_clock", lambda: now[0])
    outcomes = [_github.GithubError("status_failed", "x", 500), _run("queued")]

    def fetch(config):
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(web_publish, "github_latest_deploy_run", fetch)
    client = _client()
    assert client.get(STATUS_URL, headers=HEADERS).status_code == 502
    now[0] += 6.0
    assert client.get(STATUS_URL, headers=HEADERS).json()["status"] == "queued"


# ------------------------------------------------------------------------------- _github helpers

class _FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.responses.pop(0)


def test_read_file_decodes_content_and_sends_ref() -> None:
    record = {"sha": "abc", "html_url": HTML_URL, "encoding": "base64",
              "content": base64.encodebytes(b'{"x": 1}\n').decode()}
    t = _FakeTransport((200, json.dumps(record).encode()))
    got = _github.github_read_file(_config(), "a/b.json", transport=t)
    assert got == {"content": b'{"x": 1}\n', "sha": "abc", "html_url": HTML_URL}
    assert t.requests[0]["method"] == "GET"
    assert t.requests[0]["url"] == (
        "https://api.github.com/repos/example-owner/example-repo/contents/a/b.json?ref=main")


def test_read_file_404_is_none_and_other_statuses_raise() -> None:
    assert _github.github_read_file(_config(), "a.json", transport=_FakeTransport((404, b"{}"))) is None
    with pytest.raises(_github.GithubError) as exc:
        _github.github_read_file(_config(), "a.json", transport=_FakeTransport((403, TOKEN.encode())))
    assert exc.value.code == "read_failed" and exc.value.status == 403
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize("record", [
    [{"name": "a"}],                                   # a directory listing
    {"sha": "abc", "encoding": "none", "content": ""}, # file over 1 MB
    {"sha": "abc", "encoding": "base64", "content": "!!!"},
    {"encoding": "base64", "content": "e30="},         # no sha
])
def test_read_file_unreadable_record_raises(record) -> None:
    t = _FakeTransport((200, json.dumps(record).encode()))
    with pytest.raises(_github.GithubError) as exc:
        _github.github_read_file(_config(), "a.json", transport=t)
    assert exc.value.code == "read_failed"


def test_oversized_body_is_refused() -> None:
    t = _FakeTransport((200, b"x" * (_github.MAX_BODY_BYTES + 1)))
    with pytest.raises(_github.GithubError) as exc:
        _github.github_read_file(_config(), "a.json", transport=t)
    assert exc.value.code == "read_failed"


def test_latest_deploy_run_asks_for_dispatch_runs_and_picks_the_newest() -> None:
    runs = {"workflow_runs": [
        _run("completed", "success", created_at="2026-11-07T10:00:00Z"),
        _run("queued", None, created_at="2026-11-07T11:00:00Z", head_sha="e" * 40),
    ]}
    t = _FakeTransport((200, json.dumps(runs).encode()))
    run = _github.github_latest_deploy_run(_config(), transport=t)
    assert run["head_sha"] == "e" * 40
    assert t.requests[0]["url"] == (
        "https://api.github.com/repos/example-owner/example-repo/actions/workflows/web-deploy.yml/runs"
        "?per_page=5&event=workflow_dispatch")


def test_latest_deploy_run_empty_and_error() -> None:
    assert _github.github_latest_deploy_run(
        _config(), transport=_FakeTransport((200, b'{"workflow_runs": []}'))) is None
    with pytest.raises(_github.GithubError) as exc:
        _github.github_latest_deploy_run(_config(), transport=_FakeTransport((500, b"{}")))
    assert exc.value.code == "status_failed" and exc.value.status == 500
    with pytest.raises(_github.GithubError):
        _github.github_latest_deploy_run(_config(), transport=_FakeTransport((200, b"nope")))
