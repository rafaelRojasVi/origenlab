"""POST /v2/web/publish-model-sheet: schema validation, role gate, GitHub write + deploy dispatch.

No database, no network: the GitHub helpers are monkeypatched on the route module and the helpers
themselves are tested against a fake transport.
"""

from __future__ import annotations

import base64
import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2 import _github, web_publish
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}
TOKEN = "ghp_FAKE_TOKEN_FOR_TESTS_ONLY"
PATH = "/v2/web/publish-model-sheet"


def _valid_sheet() -> dict:
    """A complete sheet in the shape of `apps/web/src/content/modelos/<slug>.json`; all values fictitious."""
    return {
        "slug": "modelo-ficticio-100",
        "family": "sonicacion",
        "brand": "hielscher",
        "name": "Modelo Ficticio 100",
        "headline": "Un equipo inventado para las pruebas",
        "lead": "Descripción de prueba, sin cifras reales.",
        "applications": ["Dispersión de muestras de prueba"],
        "specs": [{"label": "Potencia", "value": "100 W", "source": "https://fabricante.example.invalid/ficha.pdf"}],
        "accessories": ["Sonotrodo de prueba"],
        "related": ["otro-modelo-ficticio"],
        "faqs": [{"q": "¿Es real?", "a": "No, es un dato de prueba."}],
        "official_url": "https://fabricante.example.invalid/modelo",
        "official_pdf_url": "https://fabricante.example.invalid/modelo.pdf",
        "verified_on": "2026-11-07",
    }


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


@pytest.fixture(autouse=True)
def _fresh_guard():
    web_publish.reset_rate_guard()
    yield
    web_publish.reset_rate_guard()


@pytest.fixture
def calls(monkeypatch):
    log: dict[str, list] = {"put": [], "dispatch": []}

    def put(config, path, content, message, sha):
        log["put"].append({"path": path, "content": content, "message": message, "sha": sha})
        return "c" * 40

    def dispatch(config):
        log["dispatch"].append(config.workflow)
        return "https://github.com/example-owner/example-repo/actions/workflows/web-deploy.yml"

    monkeypatch.setattr(web_publish, "github_put_file", put)
    monkeypatch.setattr(web_publish, "github_dispatch_workflow", dispatch)
    monkeypatch.setattr(web_publish, "github_file_sha", lambda config, path: None)
    return log


def _client(role: str = "sales") -> TestClient:
    app = FastAPI()
    app.include_router(web_publish.web_publish_router)
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    app.state.web_publish_config = _github.GithubPublishConfig(
        token=SecretStr(TOKEN), repo="example-owner/example-repo", ref="main",
        committer_name="OrigenLab panel", committer_email="panel@origenlab.cl",
    )
    return TestClient(app)


def _post(client: TestClient, sheet: dict, model_id: str = "modelo-ficticio-100"):
    return client.post(PATH, json={"model_id": model_id, "sheet": sheet}, headers=HEADERS)


# --------------------------------------------------------------------------- happy path

def test_valid_sheet_is_committed_and_deploy_dispatched(calls) -> None:
    resp = _post(_client(), _valid_sheet())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "dispatched"
    assert body["commit_sha"] == "c" * 40
    assert body["run_url"].endswith("/actions/workflows/web-deploy.yml")
    [put] = calls["put"]
    assert put["path"] == "apps/web/src/content/modelos/modelo-ficticio-100.json"
    assert put["message"] == "web: ficha de modelo-ficticio-100 publicada desde el panel"
    assert json.loads(put["content"]) == _valid_sheet()
    assert put["content"].endswith(b"\n")
    assert len(calls["dispatch"]) == 1
    assert TOKEN not in resp.text


def test_existing_file_sha_is_passed_for_an_update(calls, monkeypatch) -> None:
    monkeypatch.setattr(web_publish, "github_file_sha", lambda config, path: "abc123")
    assert _post(_client(), _valid_sheet()).status_code == 200
    assert calls["put"][0]["sha"] == "abc123"


# ------------------------------------------------------------------ validation (nothing is written)

def _assert_refused(resp, calls, status=422) -> None:
    assert resp.status_code == status, resp.text
    assert calls["put"] == [] and calls["dispatch"] == []


def test_spec_without_source_is_422(calls) -> None:
    sheet = _valid_sheet()
    del sheet["specs"][0]["source"]
    resp = _post(_client(), sheet)
    _assert_refused(resp, calls)
    assert any("source" in e["message"] for e in resp.json()["detail"]["errors"])


def test_top_level_price_is_422(calls) -> None:
    sheet = _valid_sheet()
    sheet["price"] = "1000"
    _assert_refused(_post(_client(), sheet), calls)


@pytest.mark.parametrize("field", ["stock", "warranty", "lead_time"])
def test_other_forbidden_fields_are_422(calls, field) -> None:
    sheet = _valid_sheet()
    sheet[field] = "x"
    _assert_refused(_post(_client(), sheet), calls)


def test_uppercase_model_id_is_422(calls) -> None:
    sheet = _valid_sheet()
    sheet["slug"] = "Modelo-Ficticio-100"
    _assert_refused(_post(_client(), sheet, model_id="Modelo-Ficticio-100"), calls)


def test_path_traversal_model_id_is_422(calls) -> None:
    sheet = _valid_sheet()
    sheet["slug"] = "../x"
    _assert_refused(_post(_client(), sheet, model_id="../x"), calls)


def test_slug_must_equal_model_id(calls) -> None:
    resp = _post(_client(), _valid_sheet(), model_id="otro-modelo")
    _assert_refused(resp, calls)
    assert resp.json()["detail"]["code"] == "slug_mismatch"


def test_missing_required_field_is_422(calls) -> None:
    sheet = _valid_sheet()
    del sheet["faqs"]
    _assert_refused(_post(_client(), sheet), calls)


def test_extra_body_field_is_422(calls) -> None:
    resp = _client().post(PATH, json={"model_id": "a", "sheet": {}, "x": 1}, headers=HEADERS)
    _assert_refused(resp, calls)


# --------------------------------------------------------------------------------------- role gate

def test_viewer_is_403(calls) -> None:
    _assert_refused(_post(_client("viewer"), _valid_sheet()), calls, status=403)


def test_unidentified_caller_is_401(calls) -> None:
    resp = _client().post(PATH, json={"model_id": "a", "sheet": _valid_sheet()})
    _assert_refused(resp, calls, status=401)


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_may_publish(calls, role) -> None:
    assert _post(_client(role), _valid_sheet()).status_code == 200


# --------------------------------------------------------------------------------------- rate guard

def test_second_publish_of_same_model_within_60s_is_429(calls) -> None:
    client = _client()
    assert _post(client, _valid_sheet()).status_code == 200
    resp = _post(client, _valid_sheet())
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    assert len(calls["put"]) == 1


def test_other_model_is_not_limited(calls) -> None:
    client = _client()
    assert _post(client, _valid_sheet()).status_code == 200
    other = copy.deepcopy(_valid_sheet())
    other["slug"] = "otro-modelo"
    assert _post(client, other, model_id="otro-modelo").status_code == 200


def test_guard_expires_after_60s(calls, monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(web_publish, "_clock", lambda: now[0])
    client = _client()
    assert _post(client, _valid_sheet()).status_code == 200
    now[0] += 61
    assert _post(client, _valid_sheet()).status_code == 200


# --------------------------------------------------------------------------------- GitHub failures

def test_commit_failure_is_502_and_releases_the_guard(calls, monkeypatch) -> None:
    def boom(config, path, content, message, sha):
        raise _github.GithubError("commit_failed", "GitHub answered HTTP 403", status=403)

    monkeypatch.setattr(web_publish, "github_put_file", boom)
    client = _client()
    resp = _post(client, _valid_sheet())
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "commit_failed"
    assert calls["dispatch"] == []
    assert TOKEN not in resp.text
    monkeypatch.setattr(web_publish, "github_put_file", lambda *a: "d" * 40)
    assert _post(client, _valid_sheet()).status_code == 200


def test_dispatch_failure_reports_the_commit(calls, monkeypatch) -> None:
    def boom(config):
        raise _github.GithubError("dispatch_failed", "GitHub answered HTTP 422", status=422)

    monkeypatch.setattr(web_publish, "github_dispatch_workflow", boom)
    resp = _post(_client(), _valid_sheet())
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["code"] == "dispatch_failed"
    assert detail["commit_sha"] == "c" * 40


# ------------------------------------------------------------------------------- _github helpers

class _FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.responses.pop(0)


def _config():
    return _github.GithubPublishConfig(
        token=SecretStr(TOKEN), repo="example-owner/example-repo", ref="main",
        committer_name="OrigenLab panel", committer_email="panel@origenlab.cl",
    )


def test_file_sha_none_on_404_and_sha_on_200() -> None:
    t = _FakeTransport((404, b"{}"), (200, json.dumps({"sha": "abc"}).encode()))
    assert _github.github_file_sha(_config(), "a/b.json", transport=t) is None
    assert _github.github_file_sha(_config(), "a/b.json", transport=t) == "abc"
    assert t.requests[0]["url"] == (
        "https://api.github.com/repos/example-owner/example-repo/contents/a/b.json?ref=main")
    assert t.requests[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_put_file_body_and_returned_commit_sha() -> None:
    t = _FakeTransport((201, json.dumps({"commit": {"sha": "e" * 40}}).encode()))
    sha = _github.github_put_file(_config(), "a/b.json", b'{"x": 1}\n', "msg", "old", transport=t)
    assert sha == "e" * 40
    req = t.requests[0]
    assert req["method"] == "PUT"
    sent = json.loads(req["body"])
    assert base64.b64decode(sent["content"]) == b'{"x": 1}\n'
    assert sent["message"] == "msg" and sent["branch"] == "main" and sent["sha"] == "old"
    assert sent["committer"] == {"name": "OrigenLab panel", "email": "panel@origenlab.cl"}


def test_put_file_without_sha_omits_it() -> None:
    t = _FakeTransport((201, json.dumps({"commit": {"sha": "e" * 40}}).encode()))
    _github.github_put_file(_config(), "a/b.json", b"{}", "m", None, transport=t)
    assert "sha" not in json.loads(t.requests[0]["body"])


def test_dispatch_sends_apply_true_and_returns_workflow_url() -> None:
    t = _FakeTransport((204, b""))
    url = _github.github_dispatch_workflow(_config(), transport=t)
    req = t.requests[0]
    assert req["url"] == (
        "https://api.github.com/repos/example-owner/example-repo/actions/workflows/web-deploy.yml/dispatches")
    assert json.loads(req["body"]) == {"ref": "main", "inputs": {"apply": "true"}}
    assert url == "https://github.com/example-owner/example-repo/actions/workflows/web-deploy.yml"


def test_github_errors_never_carry_the_token_or_response_body() -> None:
    t = _FakeTransport((401, ('{"message":"Bad credentials ' + TOKEN + '"}').encode()))
    with pytest.raises(_github.GithubError) as exc:
        _github.github_put_file(_config(), "a/b.json", b"{}", "m", None, transport=t)
    assert TOKEN not in str(exc.value) and TOKEN not in repr(exc.value)
    assert exc.value.status == 401


def test_config_repr_hides_the_token() -> None:
    assert TOKEN not in repr(_config())


def test_invalid_repo_is_refused() -> None:
    with pytest.raises(ValueError):
        _github.GithubPublishConfig(
            token=SecretStr("x"), repo="not a repo/../x", ref="main",
            committer_name="n", committer_email="e@example.invalid",
        )


# ------------------------------------------------------------------------------------------ mounting

def _app(monkeypatch, **env):
    from origenlab_api.main import create_app
    from origenlab_api.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return create_app()


def test_route_off_by_default(monkeypatch) -> None:
    assert PATH not in _app(monkeypatch).openapi()["paths"]


def test_route_off_with_flag_but_no_token(monkeypatch) -> None:
    app = _app(monkeypatch, ORIGENLAB_V2_WEB_PUBLISH_ENABLED="true")
    assert PATH not in app.openapi()["paths"]
    assert app.state.web_publish_enabled is False


def test_route_on_with_flag_and_token(monkeypatch) -> None:
    app = _app(monkeypatch, ORIGENLAB_V2_WEB_PUBLISH_ENABLED="true", ORIGENLAB_GITHUB_PUBLISH_TOKEN=TOKEN)
    assert PATH in app.openapi()["paths"]
    assert app.state.web_publish_enabled is True
    assert TOKEN not in repr(app.state.web_publish_config)


# ---------------------------------------------------------------------------------- schema loading

def test_schema_is_the_sites_file_and_is_loaded_once() -> None:
    path = web_publish.SCHEMA_PATH
    assert path.as_posix().endswith("apps/web/src/content/modelos.schema.json")
    assert web_publish.MODEL_SCHEMA == json.loads(path.read_text(encoding="utf-8"))


def test_missing_schema_fails_loudly(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="modelos.schema.json"):
        web_publish.find_schema(start=tmp_path)
