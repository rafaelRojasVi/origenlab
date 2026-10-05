"""The CRM reads the dashboard proxy lists, behind a production-configured API.

`apps/dashboard-proxy/src/allowlist.ts` names exactly these paths. The Worker forwards only the
dashboard session cookie and adds `X-OriginLab-Operator-Email` from Cloudflare Access; it does
not know who is signed in or which role they hold. Both checks therefore live here, and this
file pins them for exactly the paths the proxy exposes, with the configuration production uses
(Google sign-in on, development header login off, `ORIGENLAB_ENV=production`):

* no session cookie → 401, and the Cloudflare Access header the Worker adds does not change that;
* a signed-in `viewer` → 200 with every email address masked, and every `phone` field;
* an `admin` → the same body unmasked.

No database and no network: the operator lookup and the two read repositories are stubs.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.identity import build_identity_port
from test_v2_google_auth import LOOPBACK, OPERATOR_ID, _env, _operator

#: Must match the proxy's list (`v2WorkspaceReads.test.ts`, `allowlist.test.ts`).
PROXIED_READS = (
    "/v2/workspace/overview",
    "/v2/workspace/pipeline",
    "/v2/workspace/providers",
    "/v2/workspace/marketing",
    "/v2/workspace/drive",
    "/v2/workspace/review",
    "/v2/workspace/person-suggestions",
    "/v2/workspace/mail-sync",
    "/v2/workspace/mail-quote-numbers",
    "/v2/cockpit/work-queue",
)
ADDRESS = "compras@cliente-ficticio.example"
PHONE = "+56 9 1234 5678"
API_KEY = {"X-OriginLab-API-Key": "proxy-token-for-tests"}


class _Operators:
    def __init__(self, role: str) -> None:
        self.role = role

    def by_email(self, email_norm: str):
        return _operator(role=self.role, email=email_norm)


class _Sessions:
    """Stands in for AuthSessionStore: every session `_session` mints has a live row."""

    def __init__(self, operators: _Operators) -> None:
        self.operators = operators
        self.rows: dict[bytes, str] = {}

    def operator_session(self, token_hash: bytes):
        from origenlab_api.v2.auth_session_store import OperatorSessionRow

        email = self.rows.get(token_hash)
        return None if email is None else OperatorSessionRow(live=True, operator=self.operators.by_email(email))


#: The session stub of each app's Google config (a frozen dataclass), by identity.
_STORES: dict[int, _Sessions] = {}


class _Workspace:
    def _body(self) -> dict[str, Any]:
        return {"items": [{"contact": ADDRESS, "phone": PHONE}]}

    overview = pipeline = drive_archive = review = person_suggestions = mail_sync = mail_quote_numbers = _body

    # The providers and marketing routes post-process these keys; empty lists keep the
    # address-bearing `items` the only content to mask.
    def providers(self) -> dict[str, Any]:
        return {**self._body(), "on_cases": [], "candidates": []}

    def marketing(self) -> dict[str, Any]:
        return {**self._body(), "campaigns": []}


class _Cockpit:
    def work_queue(self, limit: int, offset: int) -> dict[str, Any]:
        item = {"kind": "pending_evidence", "reason": f"reply from {ADDRESS}", "next_action": "review",
                "subject_ids": {}, "label": f"Cotización para {ADDRESS}"}
        return {"items": [item], "total": 1, "limit": limit, "offset": offset}


def _client(monkeypatch: pytest.MonkeyPatch, role: str) -> tuple[TestClient, Any]:
    from origenlab_api.main import create_app

    _env(monkeypatch, base="https://dashboard.origenlab.cl/api", production=True)
    app = create_app()
    config = app.state.v2_google_auth
    operators = _Operators(role)
    _STORES[id(config)] = _Sessions(operators)
    app.state.v2_identity = build_identity_port(
        jwks_url=None, database_url=LOOPBACK, lookup=operators, google=config,
        sessions=_STORES[id(config)], dev_login_enabled=False, production=True,
    )
    app.state.crm_workspace = _Workspace()
    app.state.cockpit_repository = _Cockpit()
    return TestClient(app, base_url="https://testserver"), config


def _session(config: Any, email: str = "someone@origenlab.cl") -> dict[str, str]:
    sid = secrets.token_urlsafe(32)
    _STORES[id(config)].rows[config.signer.session_token_hash(sid)] = email
    value = config.signer.dump_session(email_norm=email, operator_id=OPERATOR_ID,
                                       google_sub="s", operator_version=1, sid=sid, ttl=600,
                                       now=time.time())
    return {config.cookie_names.session: value}


@pytest.mark.parametrize("path", PROXIED_READS)
def test_without_a_session_every_proxied_read_is_401(monkeypatch, path: str) -> None:
    client, _ = _client(monkeypatch, "admin")
    assert client.get(path, headers=API_KEY).status_code == 401
    # What the Worker adds from Cloudflare Access is not a sign-in.
    spoofed = {**API_KEY, OPERATOR_EMAIL_HEADER: "someone@origenlab.cl"}
    response = client.get(path, headers=spoofed)
    assert response.status_code == 401
    assert ADDRESS not in response.text


@pytest.mark.parametrize("path", PROXIED_READS)
def test_a_forged_session_cookie_is_401(monkeypatch, path: str) -> None:
    client, config = _client(monkeypatch, "admin")
    client.cookies.set(config.cookie_names.session, "forged.value")
    assert client.get(path, headers=API_KEY).status_code == 401


@pytest.mark.parametrize("path", PROXIED_READS)
def test_a_viewer_reads_every_proxied_path_with_addresses_masked(monkeypatch, path: str) -> None:
    client, config = _client(monkeypatch, "viewer")
    client.cookies.update(_session(config))
    response = client.get(path, headers=API_KEY)
    assert response.status_code == 200
    assert ADDRESS not in response.text
    # Phones are masked by field name or contact-point kind, not by value
    # (`contact_redaction.py`); the work queue carries no phone field.
    assert "1234 5678" not in response.text
    assert response.headers.get("X-OrigenLab-Redaction")


@pytest.mark.parametrize("path", PROXIED_READS)
def test_an_admin_reads_the_same_path_unmasked(monkeypatch, path: str) -> None:
    client, config = _client(monkeypatch, "admin")
    client.cookies.update(_session(config))
    response = client.get(path, headers=API_KEY)
    assert response.status_code == 200
    assert ADDRESS in response.text
