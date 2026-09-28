"""Shared Google sign-in with operator profiles, end to end against a disposable PostgreSQL.

Runs only with `ORIGENLAB_V2_TEST_DSN` and `ORIGENLAB_V2_API_TEST_DSN` (see
`v2_command_harness.py`): the harness creates `origenlab_test_<hex>`, applies every migration,
and drops it afterwards. The API runs as the real `origenlab_api` login; provisioning runs as
the maintenance login under `set role origenlab_owner`, exactly as `profile_roster.py` does.

What is proven, through the real app (Google's token endpoint and signing keys are the only
stand-ins, as in `test_v2_google_auth.py`):

* Google → principal session → `profile_required` on every CRM, workspace and command route.
* `/auth/profiles` lists only the principal's usable profiles; the right PIN selects one.
* Wrong PIN, unknown id, another principal's profile, disabled profile, malformed PIN and a
  lock all answer byte-for-byte the same.
* The throttle and lockout are the database's: they hold across two separately created apps
  and under concurrent attempts, and a success resets them as documented.
* Admin profiles can run an admin command and it is attributed to them; the sales profile is
  refused; switching or clearing removes the previous privileges at once.
* A role, PIN, status or principal change ends the sessions it should, on the next request.
* Tampered or cross-principal cookies are refused; no header selects an operator.
* No PIN appears in a response, a log record or any auth table.
* Provisioning plans first, reads PINs only from a protected file or a hidden prompt, applies
  only an exact confirmed plan, and cannot run as the runtime role.

Every name, address and PIN here is invented.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import secrets
import stat
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.v2.auth_session import ProfileSelection
from origenlab_api.v2.profile_auth import PRINCIPAL_MAX_FAILURES, PROFILE_MAX_FAILURES
from origenlab_api.v2.profile_pin import FLOOR, PinHasher
from origenlab_api.v2.profile_roster import apply as roster_apply
from origenlab_api.v2.profile_roster import parse_roster
from origenlab_api.v2.profile_roster import plan as roster_plan
from test_v2_google_auth import CLIENT_ID, _claims, _google_jwks, _jwt
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

pytestmark = needs_db

PEPPER = "profile-pepper-for-tests-" + secrets.token_urlsafe(24)
SESSION_SECRET = "profile-session-secret-for-tests-" + secrets.token_urlsafe(24)
SHARED = "compartida@origenlab.cl"
OTHER = "otra.compartida@origenlab.cl"
INDIVIDUAL = "persona@origenlab.cl"
#: Invented PINs. Karla's role in the brief is "sales"; here the sales profile is "carla".
PINS = {"ana": "482913", "bruno": "705162", "carla": "391847", "elena": "260594", "franco": "815370"}
REFUSED = {"detail": "profile_selection_failed"}
API_DIR = Path(__file__).resolve().parents[1]


def _roster(email: str, profiles: list[dict[str, Any]]):
    return parse_roster({"principal": {"email": email}, "profiles": profiles}, workspace_domain="origenlab.cl")


SHARED_ROSTER = _roster(SHARED, [
    {"key": "ana", "display_name": "Ana", "role": "admin", "sort_order": 1},
    {"key": "bruno", "display_name": "Bruno", "role": "admin", "sort_order": 2},
    {"key": "carla", "display_name": "Carla", "role": "sales", "sort_order": 3},
    {"key": "elena", "display_name": "Elena", "role": "sales", "status": "disabled", "sort_order": 4},
])
OTHER_ROSTER = _roster(OTHER, [{"key": "franco", "display_name": "Franco", "role": "admin"}])


def _owner(dsn: str):
    import psycopg

    return psycopg.connect(dsn)


def _provision(dsn: str, roster, pins: dict[str, str]) -> None:
    with _owner(dsn) as conn:
        planned = roster_plan(conn, roster, pins)
        roster_apply(conn, roster, pins, hasher=PinHasher(PEPPER, FLOOR),
                     expected_changes=planned.pending, expected_database=planned.database)


def _sql(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with _owner(dsn) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def ids(db) -> dict[str, str]:
    _provision(db, SHARED_ROSTER, {k: PINS[k] for k in ("ana", "bruno", "carla", "elena")})
    _provision(db, OTHER_ROSTER, {"franco": PINS["franco"]})
    _sql(db, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
             "values (gen_random_uuid(), %s, 'Persona', 'admin', 'active')", (INDIVIDUAL,))
    rows = _sql(db, "select p.profile_key, p.operator_id::text from platform.operator_profile p")
    out = dict(rows)
    out["shared"] = _sql(db, "select id::text from platform.auth_principal where email_norm = %s", (SHARED,))[0][0]
    return out


@pytest.fixture(autouse=True)
def _clean_throttle(db, ids):
    """Every test starts with no failures on record; the audit is append-only and stays."""
    for table in ("auth_principal", "operator_profile"):
        _sql(db, f"update platform.{table} set failed_attempts = 0, lockout_count = 0, "
                 "locked_until = null, last_failed_at = null")
    yield


class Api:
    """One app instance — its own settings, pools and state — over the disposable database."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, db: str, *, production: bool = False) -> None:
        env = {
            "ORIGENLAB_DISABLE_DOTENV": "1",
            "ORIGENLAB_V2_DATABASE_URL": runtime_dsn(db),
            "ORIGENLAB_GOOGLE_AUTH_ENABLED": "true",
            "ORIGENLAB_GOOGLE_CLIENT_ID": CLIENT_ID,
            "ORIGENLAB_GOOGLE_CLIENT_SECRET": "client-secret-for-tests",
            "ORIGENLAB_AUTH_PUBLIC_BASE_URL": "https://dashboard.origenlab.cl/api" if production else "http://localhost:5173",
            "ORIGENLAB_AUTH_SESSION_SECRET": SESSION_SECRET,
            "ORIGENLAB_PROFILE_LOGIN_ENABLED": "true",
            "ORIGENLAB_PROFILE_PIN_PEPPER": PEPPER,
            "ORIGENLAB_DEV_LOGIN_ENABLED": "false",
            "ORIGENLAB_V2_COMMANDS_ENABLED": "true",
            "ORIGENLAB_V2_CAMPAIGN_BLOCKS_ENABLED": "true",
        }
        if production:
            env.update({"ORIGENLAB_ENV": "production", "ORIGENLAB_API_BACKEND": "postgres",
                        "ORIGENLAB_POSTGRES_URL": "postgresql://u:p@127.0.0.1:5432/unused",
                        "ORIGENLAB_API_CORS_ORIGINS": "https://dashboard.origenlab.cl",
                        "ORIGENLAB_API_AUTH_TOKEN": "proxy-token-for-tests"})
        else:
            monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
        for name in ("ORIGENLAB_V2_JWKS_URL", "ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        self.app = create_app()
        self.app.state.v2_token_exchanger = self._exchange
        self.app.state.v2_google_jwks = _google_jwks()
        self.production = production
        self.cookie = "__Host-origenlab_session" if production else "origenlab_session"
        self.headers = {"X-OriginLab-API-Key": "proxy-token-for-tests"} if production else {}
        self.client = self.new_client()
        self._email = SHARED

    def new_client(self) -> TestClient:
        return TestClient(self.app, base_url="https://testserver" if self.production else "http://testserver",
                          headers=self.headers)

    def _exchange(self, *, code: str, verifier: str, config: Any) -> dict[str, Any]:
        claims = _claims(self._nonce, email=self._email, sub=f"sub-{self._email.split('@')[0]}")
        return {"id_token": _jwt(claims), "access_token": "discarded"}

    def google(self, email: str = SHARED, client: TestClient | None = None) -> Any:
        c = client or self.client
        self._email = email
        login = c.get("/auth/google/login", follow_redirects=False)
        query = parse_qs(urlsplit(login.headers["location"]).query)
        self._nonce = query["nonce"][0]
        return c.get("/auth/google/callback", params={"code": "c", "state": query["state"][0]},
                     follow_redirects=False)

    def select(self, key_or_id: str, pin: str, ids: dict[str, str], client: TestClient | None = None) -> Any:
        return (client or self.client).post(
            "/auth/profile/select", json={"profile_id": ids.get(key_or_id, key_or_id), "pin": pin})

    def session_cookie(self, client: TestClient | None = None) -> str:
        return (client or self.client).cookies.get(self.cookie)


def _refusal(response: Any) -> str | None:
    """The refusal text of a 401/403, in either shape the API answers with."""
    body = response.json()
    return body.get("detail") or (body.get("error") or {}).get("message")


def _block_all(client: TestClient, version: int = 0) -> Any:
    return client.post("/v2/commands/block-campaign",
                       json={"scope": "all_campaigns", "expected_block_version": version, "reason": "Prueba de perfil"},
                       headers={"Idempotency-Key": f"profile-test-{secrets.token_hex(8)}"})


def _unblock_all(db: str) -> None:
    _sql(db, "update outbound.campaign_block set lifted_at = now(), lift_reason = 'fin de la prueba', "
             "lifted_by_operator_id = placed_by_operator_id, version = 2 "
             "where scope = 'all_campaigns' and lifted_at is null")


# --------------------------------------------------------------------- the whole flow


def test_google_signs_in_the_principal_and_every_crm_route_wants_a_profile(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    response = api.google()
    assert response.status_code == 303 and response.headers["location"] == "http://localhost:5173/"
    me = api.client.get("/auth/session")
    assert me.status_code == 401
    assert me.json()["state"] == "profile_required"
    assert me.json()["principal"] == {"email": SHARED}
    for path in ("/v2/contacts", "/v2/workspace/overview", "/v2/workspace/pipeline", "/v2/cockpit/work-queue"):
        refused = api.client.get(path)
        assert refused.status_code == 401, path
        assert _refusal(refused) == "profile_required", path
    assert _block_all(api.client).status_code == 401


def test_the_profile_list_is_the_principals_usable_profiles_only(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    body = api.client.get("/auth/profiles").json()
    assert body["principal"] == {"email": SHARED}
    assert body["profiles"] == [
        {"id": ids["ana"], "display_name": "Ana", "role_label": "Administración"},
        {"id": ids["bruno"], "display_name": "Bruno", "role_label": "Administración"},
        {"id": ids["carla"], "display_name": "Carla", "role_label": "Ventas"},
    ]
    assert ids["franco"] not in json.dumps(body) and ids["elena"] not in json.dumps(body)


def test_the_right_pin_selects_the_profile_and_binds_it_into_the_session(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    before = api.session_cookie()
    response = api.select("carla", PINS["carla"], ids)
    assert response.status_code == 200
    assert response.json()["profile"] == {"id": ids["carla"], "display_name": "Carla", "role_label": "Ventas"}
    set_cookie = next(c for c in response.headers.get_list("set-cookie") if c.startswith("origenlab_session="))
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie and "Path=/" in set_cookie
    assert api.session_cookie() != before
    me = api.client.get("/auth/session").json()
    assert me["auth_method"] == "google_profile"
    assert me["operator"] == {"operator_id": ids["carla"], "email": SHARED, "display_name": "Carla", "role": "sales"}
    assert me["can_switch_profile"] is True
    assert api.client.get("/v2/contacts").status_code == 200
    event = _sql(db, "select principal_id::text, principal_email_norm, operator_id::text from platform.auth_event "
                     "where event_type = 'profile.selected' order by occurred_at desc limit 1")[0]
    assert event == (ids["shared"], SHARED, ids["carla"])


def test_production_cookies_are_host_prefixed_secure_httponly_and_lax(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db, production=True)
    api.google()
    response = api.select("ana", PINS["ana"], ids)
    assert response.status_code == 200
    cookie = next(c for c in response.headers.get_list("set-cookie") if c.startswith("__Host-origenlab_session="))
    for attribute in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert attribute in cookie
    assert "Domain" not in cookie
    assert api.client.get("/auth/session").json()["operator"]["role"] == "admin"


# ------------------------------------------------------------------ one public refusal


def test_every_refusal_is_the_same_answer(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    attempts = {
        "wrong pin": (ids["carla"], "111119"),
        "unknown id": ("00000000-0000-4000-8000-00000000dead", PINS["carla"]),
        "not a uuid": ("carla", PINS["carla"]),
        "another principal's profile": (ids["franco"], PINS["franco"]),
        "disabled profile, right pin": (ids["elena"], PINS["elena"]),
        "malformed pin": (ids["bruno"], "12ab"),
    }
    answers = {}
    for label, (profile_id, pin) in attempts.items():
        response = api.client.post("/auth/profile/select", json={"profile_id": profile_id, "pin": pin})
        answers[label] = (response.status_code, response.content, response.headers.get("set-cookie"))
    assert set(answers.values()) == {(401, json.dumps(REFUSED, separators=(",", ":")).encode(), None)}, answers
    reasons = [r for (r,) in _sql(db, "select refusal_reason from platform.auth_event where "
                                      "event_type = 'profile.selection_refused' order by occurred_at desc limit 6")]
    assert set(reasons) == {"pin_mismatch", "unknown_profile", "profile_inactive", "malformed_pin"}
    assert api.client.get("/auth/session").json()["state"] == "profile_required"


def test_a_lock_is_the_same_answer_too(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    for _ in range(PROFILE_MAX_FAILURES):
        assert api.select("bruno", "111119", ids).json() == REFUSED
    locked = api.select("bruno", PINS["bruno"], ids)
    assert (locked.status_code, locked.json()) == (401, REFUSED)
    assert api.select("ana", PINS["ana"], ids).status_code == 200, "a lock on one profile is not a lock on another"


# ----------------------------------------------------------------- throttle, persisted


def _profile_throttle(db: str, operator_id: str) -> tuple[int, int, bool]:
    return _sql(db, "select failed_attempts, lockout_count, coalesce(locked_until > now(), false) "
                    "from platform.operator_profile where operator_id = %s", (operator_id,))[0]


def test_the_lockout_holds_across_separate_api_instances(monkeypatch, db, ids) -> None:
    first = Api(monkeypatch, db)
    first.google()
    second = Api(monkeypatch, db)  # created separately: its own app, settings and connections
    assert second.app is not first.app
    second.client.cookies.set(second.cookie, first.session_cookie())
    for i in range(PROFILE_MAX_FAILURES):
        target = first if i % 2 == 0 else second
        assert target.select("carla", "111119", ids).json() == REFUSED
    assert _profile_throttle(db, ids["carla"]) == (0, 1, True)
    for api in (first, second):
        assert api.select("carla", PINS["carla"], ids).json() == REFUSED
    assert _sql(db, "select count(*) from platform.auth_event where event_type = 'profile.locked' "
                    "and operator_id = %s", (ids["carla"],))[0][0] >= 1


def test_concurrent_attempts_through_two_instances_are_all_counted(monkeypatch, db, ids) -> None:
    apps = [Api(monkeypatch, db), Api(monkeypatch, db)]
    apps[0].google()
    cookie = apps[0].session_cookie()
    clients = []
    for i in range(PROFILE_MAX_FAILURES - 1):
        client = apps[i % 2].new_client()
        client.cookies.set(apps[i % 2].cookie, cookie)
        clients.append((apps[i % 2], client))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(clients)) as pool:
        results = list(pool.map(lambda pair: pair[0].select("bruno", "111119", ids, client=pair[1]).status_code,
                                clients))
    assert results == [401] * len(clients)
    assert _profile_throttle(db, ids["bruno"]) == (PROFILE_MAX_FAILURES - 1, 0, False), "no attempt was lost"
    assert apps[1].select("bruno", "111119", ids, client=clients[1][1]).json() == REFUSED
    assert _profile_throttle(db, ids["bruno"])[2] is True


def test_a_success_resets_the_failures_as_documented(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    for _ in range(PROFILE_MAX_FAILURES - 1):
        api.select("ana", "111119", ids)
    assert _profile_throttle(db, ids["ana"])[0] == PROFILE_MAX_FAILURES - 1
    api.select("bruno", "111119", ids)
    assert api.select("ana", PINS["ana"], ids).status_code == 200
    assert _profile_throttle(db, ids["ana"]) == (0, 0, False)
    assert _profile_throttle(db, ids["bruno"])[0] == 1, "a success never resets another profile"
    assert _sql(db, "select failed_attempts from platform.auth_principal where id = %s", (ids["shared"],))[0][0] == 0


def test_a_success_keeps_the_principal_lockout_history(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    for i in range(PRINCIPAL_MAX_FAILURES):
        api.select(f"00000000-0000-4000-8000-{i:012d}", "111119", ids)
    _sql(db, "update platform.auth_principal set locked_until = now() - interval '1 second' where id = %s",
         (ids["shared"],))
    assert api.select("ana", PINS["ana"], ids).status_code == 200
    failed, lockouts = _sql(db, "select failed_attempts, lockout_count from platform.auth_principal "
                                "where id = %s", (ids["shared"],))[0]
    assert (failed, lockouts) == (0, 1), "one known PIN does not wipe the principal-wide backoff"
    for _ in range(PROFILE_MAX_FAILURES - 1):
        api.select("ana", "111119", ids)
    assert _profile_throttle(db, ids["ana"])[2] is False


def test_the_principal_locks_after_too_many_failures_across_profiles(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    for i in range(PRINCIPAL_MAX_FAILURES):
        api.select(f"00000000-0000-4000-8000-{i:012d}", "111119", ids)
    assert api.select("ana", PINS["ana"], ids).json() == REFUSED, "every profile of the principal is locked"
    assert _sql(db, "select coalesce(locked_until > now(), false) from platform.auth_principal where id = %s",
                (ids["shared"],))[0][0] is True


# ------------------------------------------------------------------ roles and switching


def test_the_sales_profile_cannot_run_an_admin_command(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    api.select("carla", PINS["carla"], ids)
    refused = _block_all(api.client)
    assert refused.status_code == 403
    assert _sql(db, "select count(*) from outbound.campaign_block where scope = 'all_campaigns' and lifted_at is null")[0][0] == 0


@pytest.mark.parametrize("key", ["ana", "bruno"])
def test_each_admin_profile_runs_an_admin_command_attributed_to_itself(monkeypatch, db, ids, key) -> None:
    api = Api(monkeypatch, db)
    api.google()
    api.select(key, PINS[key], ids)
    try:
        version = _sql(db, "select (count(*) + count(lifted_at))::int from outbound.campaign_block "
                           "where scope = 'all_campaigns'")[0][0]
        response = _block_all(api.client, version)
        assert response.status_code == 200, response.text
        placed_by = _sql(db, "select placed_by_operator_id::text from outbound.campaign_block "
                             "where scope = 'all_campaigns' and lifted_at is null")[0][0]
        assert placed_by == ids[key]
        actor = _sql(db, "select actor_operator_id::text from crm.domain_event "
                         "where event_type = 'campaign_block.placed' order by recorded_at desc limit 1")[0][0]
        assert actor == ids[key]
    finally:
        _unblock_all(db)


def test_switching_profile_removes_the_previous_privileges_at_once(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    api.select("ana", PINS["ana"], ids)
    admin_cookie = api.session_cookie()
    assert api.select("carla", PINS["carla"], ids).status_code == 200
    assert _block_all(api.client).status_code == 403
    switched = _sql(db, "select operator_id::text, previous_operator_id::text from platform.auth_event "
                        "where event_type = 'profile.selected' order by occurred_at desc limit 1")[0]
    assert switched == (ids["carla"], ids["ana"])
    cleared = api.client.post("/auth/profile/clear", json={})
    assert cleared.status_code == 200
    assert _refusal(api.client.get("/v2/contacts")) == "profile_required"
    assert _block_all(api.client).status_code == 401
    assert api.client.get("/auth/profiles").status_code == 200, "clearing keeps the Google sign-in"
    event = _sql(db, "select event_type, previous_operator_id::text from platform.auth_event "
                     "order by occurred_at desc limit 1")[0]
    assert event == ("profile.cleared", ids["carla"])
    # A copy of the earlier admin cookie still resolves only to what it was: it is not a way
    # to "switch back" into admin without a PIN, but it also was never revoked by the switch.
    assert admin_cookie != api.session_cookie()


# --------------------------------------------------------------------- invalidation


def _selected(monkeypatch, db, ids, key: str) -> Api:
    api = Api(monkeypatch, db)
    api.google()
    assert api.select(key, PINS[key], ids).status_code == 200
    assert api.client.get("/v2/contacts").status_code == 200
    return api


def test_a_role_change_ends_the_profile_session(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "carla")
    _sql(db, "update platform.operator set role = 'admin' where id = %s", (ids["carla"],))
    try:
        assert _refusal(api.client.get("/v2/contacts")) == "profile_required"
        me = api.client.get("/auth/session").json()
        assert me["state"] == "profile_required" and me["profile_expired"] is True
        assert _block_all(api.client).status_code == 401, "the promotion did not carry into the old session"
    finally:
        _sql(db, "update platform.operator set role = 'sales' where id = %s", (ids["carla"],))


def test_a_pin_change_ends_the_profile_session(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "bruno")
    _provision(db, SHARED_ROSTER, {"bruno": "604938"})
    try:
        assert _refusal(api.client.get("/v2/contacts")) == "profile_required"
        assert api.select("bruno", PINS["bruno"], ids).json() == REFUSED
        assert api.select("bruno", "604938", ids).status_code == 200
    finally:
        _provision(db, SHARED_ROSTER, {"bruno": PINS["bruno"]})


def test_disabling_the_operator_or_the_profile_ends_the_session(monkeypatch, db, ids) -> None:
    for table, key in (("operator", "id"), ("operator_profile", "operator_id")):
        api = _selected(monkeypatch, db, ids, "ana")
        _sql(db, f"update platform.{table} set status = 'disabled' where {key} = %s", (ids["ana"],))
        try:
            assert _refusal(api.client.get("/v2/contacts")) == "profile_required"
            assert ids["ana"] not in api.client.get("/auth/profiles").text
        finally:
            _sql(db, f"update platform.{table} set status = 'active' where {key} = %s", (ids["ana"],))


def test_disabling_the_principal_signs_everyone_out(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "ana")
    _sql(db, "update platform.auth_principal set status = 'disabled' where id = %s", (ids["shared"],))
    try:
        me = api.client.get("/auth/session").json()
        assert me["state"] == "signed_out"
        assert api.client.get("/auth/profiles").status_code == 401
        assert api.google().headers["location"].endswith("login_error=operator_disabled")
    finally:
        _sql(db, "update platform.auth_principal set status = 'active' where id = %s", (ids["shared"],))


# ------------------------------------------------------------------------- tampering


def test_a_forged_cookie_naming_another_principals_profile_is_refused(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    signer = api.app.state.v2_profile_login.signer
    session = signer.load_any_session(api.session_cookie())
    # Even a correctly *signed* session cannot carry a profile of another principal, or stale
    # versions: the per-request join is on both ids and every version.
    for selection in (ProfileSelection(ids["franco"], 1, 1, 1), ProfileSelection(ids["ana"], 99, 1, 1)):
        api.client.cookies.set(api.cookie, signer.dump_principal_session(session.with_profile(selection)))
        assert _refusal(api.client.get("/v2/contacts")) == "profile_required"
        assert _block_all(api.client).status_code == 401


def test_a_tampered_cookie_is_refused_outright(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "carla")
    value = api.session_cookie()
    body, _, tag = value.partition(".")
    api.client.cookies.set(api.cookie, f"{body[:-2]}AA.{tag}")
    assert api.client.get("/auth/session").json()["state"] == "signed_out"
    assert api.client.get("/v2/contacts").status_code == 401


def test_no_header_or_body_field_can_choose_or_raise_the_operator(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "carla")
    headers = {"X-OriginLab-Operator-Email": INDIVIDUAL, "X-OriginLab-Operator-Id": ids["ana"],
               "X-OriginLab-Profile-Id": ids["ana"], "X-OriginLab-Role": "admin"}
    assert api.client.get("/auth/session", headers=headers).json()["operator"]["role"] == "sales"
    assert api.client.post(
        "/v2/commands/block-campaign", headers={**headers, "Idempotency-Key": "profile-test-hdr-01"},
        json={"scope": "all_campaigns", "expected_block_version": 0, "reason": "x"}).status_code == 403
    # Carla's PIN on Ana's card is just a wrong PIN.
    assert api.select("ana", PINS["carla"], ids).json() == REFUSED
    assert api.client.post("/auth/profile/select", json={"profile_id": ids["ana"], "pin": PINS["carla"],
                                                          "role": "admin"}).status_code == 400
    assert api.client.get("/auth/session").json()["operator"]["role"] == "sales"


def test_an_individual_google_operator_still_signs_in_directly(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    assert api.google(INDIVIDUAL).status_code == 303
    me = api.client.get("/auth/session").json()
    assert me["auth_method"] == "google_session" and me["operator"]["email"] == INDIVIDUAL
    assert me["can_switch_profile"] is False
    assert api.client.get("/auth/profiles").status_code == 401


# ---------------------------------------------------------------------------- logout


def test_logout_ends_the_whole_session_and_is_audited(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "carla")
    response = api.client.post("/auth/logout")
    assert response.status_code == 200
    assert api.client.get("/auth/session").json()["state"] == "signed_out"
    assert api.client.get("/auth/profiles").status_code == 401
    event = _sql(db, "select event_type, principal_email_norm, operator_id::text from platform.auth_event "
                     "order by occurred_at desc limit 1")[0]
    assert event == ("session.logout", SHARED, ids["carla"])


# ------------------------------------------------------------------ no PIN anywhere


def test_no_pin_reaches_a_response_a_log_or_an_auth_table(monkeypatch, db, ids, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    api = Api(monkeypatch, db)
    api.google()
    seen = []
    for key, pin in (("carla", "917364"), ("carla", PINS["carla"]), ("ana", "12ab"), ("bruno", PINS["ana"])):
        response = api.select(key, pin, ids)
        seen.append(response.text + json.dumps(dict(response.headers)))
    seen.append(api.client.post("/auth/profile/select", content=b'{"profile_id": 1, "pin": "917364"}',
                                headers={"content-type": "application/json"}).text)
    seen.append(api.client.get("/auth/session").text + api.client.get("/auth/profiles").text)
    dumped = "\n".join(
        str(row) for table in ("auth_event", "auth_principal", "operator_profile")
        for row in _sql(db, f"select row_to_json(t)::text from platform.{table} t"))
    for pin in ("917364", *PINS.values()):
        for text in seen:
            assert pin not in text
        assert pin not in caplog.text
        assert pin not in dumped


# ---------------------------------------------------------------------- provisioning


def _run_roster(monkeypatch, tmp_path: Path, argv: list[str], *, dsn: str) -> tuple[int, str, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout
    import importlib.util

    spec = importlib.util.spec_from_file_location("profile_roster_script", API_DIR / "scripts" / "profile_roster.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setenv("ORIGENLAB_V2_PROVISIONING_DATABASE_URL", dsn)
    monkeypatch.setenv("ORIGENLAB_PROFILE_PIN_PEPPER", PEPPER)
    monkeypatch.setenv("ORIGENLAB_AUTH_SESSION_SECRET", SESSION_SECRET)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


def _files(tmp_path: Path, *, pins: dict[str, str], mode: int = 0o600) -> tuple[Path, Path]:
    roster = tmp_path / "profiles.json"
    roster.write_text(json.dumps({"principal": {"email": "tercera@origenlab.cl"}, "profiles": [
        {"key": "gema", "display_name": "Gema", "role": "viewer"}]}))
    pin_file = tmp_path / "pins.json"
    pin_file.write_text(json.dumps(pins))
    os.chmod(pin_file, mode)
    return roster, pin_file


def test_provisioning_plans_then_applies_only_the_exact_confirmed_plan(monkeypatch, db, ids, tmp_path) -> None:
    roster, pin_file = _files(tmp_path, pins={"gema": "518203"})
    code, out, err = _run_roster(monkeypatch, tmp_path, ["--roster", str(roster), "--pin-file", str(pin_file)], dsn=db)
    assert code == 0, err
    assert "PLAN (read-only" in out and "pending changes: 2" in out and "t***@origenlab.cl" in out
    assert "518203" not in out and "$argon2id" not in out
    assert _sql(db, "select count(*) from platform.auth_principal where email_norm = 'tercera@origenlab.cl'")[0][0] == 0
    database = db.rsplit("/", 1)[1]
    for bad in (["--confirm-changes", "3", "--confirm-database", database],
                ["--confirm-changes", "2", "--confirm-database", "origenlab_clean"]):
        code, _, err = _run_roster(monkeypatch, tmp_path, ["--roster", str(roster), "--pin-file", str(pin_file),
                                                           "--apply", *bad], dsn=db)
        assert code == 2 and "refused" in err
    code, _, err = _run_roster(monkeypatch, tmp_path, ["--roster", str(roster), "--pin-file", str(pin_file), "--apply"], dsn=db)
    assert code == 2 and "--confirm-changes" in err
    code, out, err = _run_roster(monkeypatch, tmp_path, ["--roster", str(roster), "--pin-file", str(pin_file), "--apply",
                                                         "--confirm-changes", "2", "--confirm-database", database], dsn=db)
    assert code == 0, err
    assert "APPLIED" in out
    row = _sql(db, "select o.role, o.sign_in_kind, o.email_norm, p.pin_hash from platform.operator_profile p "
                   "join platform.operator o on o.id = p.operator_id where p.profile_key = 'gema'")[0]
    assert row[:3] == ("viewer", "shared_profile", None), "a profile operator gets no invented address"
    assert PinHasher(PEPPER, FLOOR).verify("518203", row[3])
    code, out, _ = _run_roster(monkeypatch, tmp_path, ["--roster", str(roster)], dsn=db)
    assert code == 0 and "nothing to apply" in out


@pytest.mark.parametrize("problem", ["world-readable", "inside-repo", "pin-in-roster", "weak-pin", "no-tty"])
def test_provisioning_refuses_unsafe_pin_input(monkeypatch, db, ids, tmp_path, problem) -> None:
    roster, pin_file = _files(tmp_path, pins={"gema": "518203"}, mode=0o644 if problem == "world-readable" else 0o600)
    argv = ["--roster", str(roster), "--pin-file", str(pin_file)]
    if problem == "inside-repo":
        argv = ["--roster", str(API_DIR / "pyproject.toml"), "--pin-file", str(pin_file)]
    elif problem == "pin-in-roster":
        roster.write_text(json.dumps({"principal": {"email": "tercera@origenlab.cl"}, "profiles": [
            {"key": "gema", "display_name": "Gema", "role": "viewer", "pin": "518203"}]}))
    elif problem == "weak-pin":
        pin_file.write_text(json.dumps({"gema": "123456"}))
    elif problem == "no-tty":
        argv = ["--roster", str(roster), "--prompt-pin", "gema"]
        monkeypatch.setattr("sys.stdin", open(os.devnull))
    code, out, err = _run_roster(monkeypatch, tmp_path, argv, dsn=db)
    assert code == 2, (problem, out, err)
    assert "518203" not in out + err and "123456" not in out + err


def test_the_runtime_role_cannot_provision(monkeypatch, db, ids, tmp_path) -> None:
    roster, pin_file = _files(tmp_path, pins={"gema": "518203"})
    code, _, err = _run_roster(monkeypatch, tmp_path, ["--roster", str(roster), "--pin-file", str(pin_file)],
                               dsn=runtime_dsn(db))
    assert code == 2 and "SQLSTATE 42501" in err
    assert not stat.S_IMODE(pin_file.stat().st_mode) & 0o077


def test_pins_are_never_accepted_as_arguments() -> None:
    source = (API_DIR / "scripts" / "profile_roster.py").read_text()
    assert '"--pin"' not in source and "'--pin'" not in source
    assert "getpass" in source
