"""Web suggestions for institutions: the file, its loader, and the organization read.

The real file names customers and lives outside this public repository (a Render secret file);
every institution, RUT and domain below is invented.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.settings import Settings
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.org_web_suggestions import load_org_suggestions, rut_key, suggestion_for, valid_rut

REPO_ROOT = Path(__file__).resolve().parents[3]
ORG = "0a000000-0000-4000-8000-00000000000a"
OTHER_ORG = "0b000000-0000-4000-8000-00000000000b"


def _entry(**over: Any) -> dict[str, Any]:
    return {
        "display_name": "Universidad Ficticia del Sur",
        "legal_name": "Corporación Universidad Ficticia del Sur",
        "rut": "12.345.678-5",
        "rut_source": "official",
        "website": "https://www.ficticia.example",
        "email_domain": "ficticia.example",
        "type": "universidad",
        "city": "Ciudad Inventada",
        "region": "Región Inventada",
        "confidence": "high",
        "sources": [{"url": "https://www.ficticia.example/transparencia", "shows": "RUT y razón social"}],
        "notes": "Casa central.",
        **over,
    }


def _write(path: Path, organizations: dict[str, Any], version: int = 1) -> None:
    path.write_text(json.dumps({"version": version, "organizations": organizations}), encoding="utf-8")


def _file(tmp_path: Path, organizations: dict[str, Any]) -> str:
    path = tmp_path / "org-suggestions.json"
    _write(path, organizations)
    return str(path)


# ─────────────────────────────────────────────────────────────────────── the RUT ──


@pytest.mark.parametrize(("raw", "key"), [("12.345.678-5", "12345678-5"), (" 012345678 5", "12345678-5"), ("6-k", "6-K")])
def test_one_rut_has_one_spelling(raw: str, key: str) -> None:
    # Two spellings of one RUT must collide on `crm.external_identifier (scheme, value_norm)`,
    # so the existing 409 `identifier_taken` catches a RUT another institution already holds.
    assert rut_key(raw) == key


@pytest.mark.parametrize(
    ("rut", "ok"),
    [("12345678-5", True), ("12345678-9", False), ("6-K", True), ("6-1", False), ("0-0", False), ("1234567890-1", False)],
)
def test_a_rut_needs_its_check_digit(rut: str, ok: bool) -> None:
    assert valid_rut(rut) is ok


# ─────────────────────────────────────────────────────────────────────── the file ──


def test_a_valid_file_loads_normalised(tmp_path: Path) -> None:
    s = load_org_suggestions(_file(tmp_path, {ORG: _entry(email_domain="Ficticia.Example")}))[ORG]
    assert (s["rut"], s["rut_source"]) == ("12345678-5", "official")
    assert (s["email_domain"], s["type"], s["confidence"]) == ("ficticia.example", "universidad", "high")
    assert s["sources"] == [{"url": "https://www.ficticia.example/transparencia", "shows": "RUT y razón social"}]


def test_no_setting_is_no_suggestions_and_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_org_suggestions(None) == {}
    assert caplog.records == []


@pytest.mark.parametrize(
    "break_it",
    [
        lambda p: p.write_text("{not json", encoding="utf-8"),
        lambda p: p.unlink(),
        lambda p: _write(p, {}, version=2),
        lambda p: _write(p, {"no-es-uuid": _entry()}),
        lambda p: _write(p, {ORG: _entry(rut="12.345.678-9")}),
        lambda p: _write(p, {ORG: _entry(rut_source=None)}),
        lambda p: _write(p, {ORG: _entry(rut=None)}),
        lambda p: _write(p, {ORG: _entry(type="Universidad Pública")}),
        lambda p: _write(p, {ORG: _entry(email_domain="no es dominio")}),
        lambda p: _write(p, {ORG: _entry(sources=[{"url": "javascript:alert(1)"}])}),
        lambda p: _write(p, {ORG: _entry(confidence="segura")}),
        lambda p: _write(p, {ORG: _entry(extra="x")}),
    ],
)
def test_a_bad_file_is_no_suggestions_and_a_warning_never_an_error(tmp_path: Path, caplog, break_it) -> None:
    path = _file(tmp_path, {ORG: _entry()})
    break_it(Path(path))
    with caplog.at_level(logging.WARNING):
        assert load_org_suggestions(path) == {}
    assert any("org_web_suggestions" in r.getMessage() for r in caplog.records)


def test_an_institution_not_in_the_file_has_none(tmp_path: Path) -> None:
    suggestions = load_org_suggestions(_file(tmp_path, {ORG: _entry()}))
    assert suggestion_for(suggestions, OTHER_ORG) is None
    assert suggestion_for(suggestions, ORG.upper()) is not None


def test_the_setting_has_no_default_path() -> None:
    assert Settings(_env_file=None).v2_org_suggestions_file is None


def test_no_suggestions_file_is_tracked_in_the_repository() -> None:
    """The file names customers and the repository is public: nothing tracked may look like one."""
    listed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", "*.json"], capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")
    offenders = []
    for rel in filter(None, listed):
        try:
            data = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        orgs = data.get("organizations") if isinstance(data, dict) else None
        if isinstance(orgs, dict) and any(isinstance(v, dict) and {"confidence", "rut_source"} <= set(v) for v in orgs.values()):
            offenders.append(rel)
        if isinstance(data, list) and any(isinstance(v, dict) and {"crm_name", "confidence", "sources"} <= set(v) for v in data):
            offenders.append(rel)
    assert offenders == []


# ─────────────────────────────────────────────────────────────── the organization read ──


class _Identity:
    def __init__(self, role: str) -> None:
        self.role = role

    def resolve(self, headers: Any) -> OperatorIdentity:  # noqa: ARG002
        return OperatorIdentity(
            operator_id="00000000-0000-4000-8000-000000000001", email_norm=f"{self.role}@example.test",
            display_name=self.role, role=self.role, status="active",
        )


class _Repo:
    def organization_authoring(self, org_id: str) -> dict[str, Any]:
        return {
            "organization": {"id": org_id, "name": "Ficticiasur", "confirmation": "machine_proposed", "version": 1},
            "identifiers": [], "domains": [], "web_suggestions": None, "authoring": None,
        }


def _client(path: str | None) -> TestClient:
    app = FastAPI()
    app.state.v2_identity = _Identity("sales")
    app.state.crm_workspace = _Repo()
    app.state.crm_authoring_enabled = True
    app.state.org_suggestions_file = path
    app.include_router(workspace_router)
    return TestClient(app)


def test_the_organization_read_carries_its_web_suggestions(tmp_path: Path) -> None:
    body = _client(_file(tmp_path, {ORG: _entry()})).get(f"/v2/workspace/organizations/{ORG}/authoring").json()
    assert body["web_suggestions"]["display_name"] == "Universidad Ficticia del Sur"
    assert body["web_suggestions"]["rut"] == "12345678-5"


@pytest.mark.parametrize("path", [None, "/nonexistent/org-suggestions.json"])
def test_without_a_usable_file_the_read_answers_null(path: str | None) -> None:
    response = _client(path).get(f"/v2/workspace/organizations/{ORG}/authoring")
    assert response.status_code == 200 and response.json()["web_suggestions"] is None


def test_the_file_is_read_per_request(tmp_path: Path) -> None:
    path = _file(tmp_path, {})
    client = _client(path)
    assert client.get(f"/v2/workspace/organizations/{ORG}/authoring").json()["web_suggestions"] is None
    _write(Path(path), {ORG: _entry()})
    assert client.get(f"/v2/workspace/organizations/{ORG}/authoring").json()["web_suggestions"] is not None


@pytest.mark.parametrize("domain", ["gmail.com", "Hotmail.com", "outlook.com", "yahoo.com", "live.com", "icloud.com"])
def test_a_free_mail_domain_is_no_institutions_domain_and_does_not_fail_the_file(tmp_path: Path, domain: str) -> None:
    s = load_org_suggestions(_file(tmp_path, {ORG: _entry(email_domain=domain)}))
    assert s[ORG]["email_domain"] is None and s[ORG]["display_name"]
