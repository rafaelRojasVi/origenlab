"""Shared Google sign-in with operator profiles, end to end against a disposable PostgreSQL.

Runs only with `ORIGENLAB_V2_TEST_DSN` and `ORIGENLAB_V2_API_TEST_DSN` (see
`v2_command_harness.py`): the harness creates `origenlab_test_<hex>`, applies every migration,
and drops it afterwards. The API runs as the real `origenlab_api` login; provisioning runs as
the maintenance login under `set role origenlab_owner`, exactly as `profile_roster.py` does.

What is proven, through the real app (Google's token endpoint and signing keys are the only
stand-ins, as in `test_v2_google_auth.py`):

* Google → principal session → `profile_required` on every CRM, workspace and command route.
* The principal is the Google account (issuer + subject), not its address: a recreated account
  under the same address is refused, and production refuses a principal with no pinned account.
* `/auth/profiles` lists only the principal's usable profiles; the right PIN selects one.
* Wrong PIN, unknown id, another principal's profile, disabled profile, malformed PIN and a
  lock all answer byte-for-byte the same.
* The throttle and lockout are the database's: they hold across two separately created apps
  and under concurrent attempts, and a success resets them as documented.
* Admin profiles can run an admin command and it is attributed to them; the sales profile is
  refused; switching or clearing removes the previous privileges at once.
* A role, PIN, status or principal change ends the sessions it should, on the next request.
* Sessions are revocable rows: a cookie copied to another API instance fails right after logout,
  "Cambiar perfil" and a switch revoke the old selection, rotation never extends the expiry, the
  database's expiry is enforced, only a keyed hash is stored, and the migrator prunes.
* A lockout is cleared early only by the migrator tool — plan, exact count and database, one
  `lockout.cleared` audit event per target — never by the runtime role or a route.
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
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import get_settings
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


def _sub(email: str) -> str:
    """The invented Google subject the stand-in token endpoint issues for an address."""
    return f"sub-{email.split('@')[0]}"


def _roster(email: str, profiles: list[dict[str, Any]], *, pinned: bool = True):
    principal: dict[str, Any] = {"email": email}
    if pinned:
        principal["provider_subject"] = _sub(email)
    return parse_roster({"principal": principal, "profiles": profiles}, workspace_domain="origenlab.cl")


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
        get_settings.cache_clear()  # one test may build a local and a production instance
        self.app = create_app()
        self.app.state.v2_token_exchanger = self._exchange
        self.app.state.v2_google_jwks = _google_jwks()
        self.production = production
        self.cookie = "__Host-origenlab_session" if production else "origenlab_session"
        self.headers = {"X-OriginLab-API-Key": "proxy-token-for-tests"} if production else {}
        self.client = self.new_client()
        self._email = SHARED
        #: When set, the stand-in token names this subject instead of the address's own.
        self.subject: str | None = None

    def new_client(self) -> TestClient:
        return TestClient(self.app, base_url="https://testserver" if self.production else "http://testserver",
                          headers=self.headers)

    def _exchange(self, *, code: str, verifier: str, config: Any) -> dict[str, Any]:
        claims = _claims(self._nonce, email=self._email, sub=self.subject or _sub(self._email))
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


# ------------------------------------------------- the Google account, not the address


def _login_error(response: Any) -> str | None:
    return parse_qs(urlsplit(response.headers["location"]).query).get("login_error", [None])[0]


@pytest.mark.parametrize("production", [False, True])
def test_a_recreated_account_under_the_same_address_is_refused(monkeypatch, db, ids, production) -> None:
    api = Api(monkeypatch, db, production=production)
    api.subject = "sub-recreated-account"
    response = api.google()
    assert _login_error(response) == "operator_not_permitted"
    assert not any(c.split("=")[0].endswith("origenlab_session") and "Max-Age=0" not in c
                   for c in response.headers.get_list("set-cookie")), "no session is issued"
    assert api.client.get("/auth/profiles").status_code == 401


def test_an_account_pinned_to_one_principal_never_signs_in_under_another_address(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.subject = _sub(SHARED)  # the shared account's subject, presenting the individual's address
    assert _login_error(api.google(INDIVIDUAL)) == "operator_not_permitted"
    assert api.client.get("/auth/session").json()["state"] == "signed_out"


def test_production_refuses_a_principal_with_no_pinned_account(monkeypatch, db, ids, caplog) -> None:
    unpinned = "sin.cuenta@origenlab.cl"
    _provision(db, _roster(unpinned, [{"key": "hugo", "display_name": "Hugo", "role": "viewer"}], pinned=False),
               {"hugo": "730418"})
    local = Api(monkeypatch, db)
    assert _login_error(local.google(unpinned)) is None, "local development may leave it unpinned"
    local_cookie = local.session_cookie()
    caplog.set_level(logging.WARNING)
    production = Api(monkeypatch, db, production=True)
    assert _login_error(production.google(unpinned)) == "operator_not_permitted"
    assert "no pinned Google account" in caplog.text
    # A session minted while unpinned is refused in production on its next request, too.
    production.client.cookies.set(production.cookie, local_cookie)
    assert production.client.get("/auth/profiles").status_code == 401
    # Pinning it through the roster tool is what lets production in.
    _provision(db, _roster(unpinned, [{"key": "hugo", "display_name": "Hugo", "role": "viewer"}]), {})
    assert _login_error(Api(monkeypatch, db, production=True).google(unpinned)) is None


def test_a_re_pin_ends_every_session_of_the_principal(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "carla")
    moved = _roster(SHARED, [{"key": "carla", "display_name": "Carla", "role": "sales", "sort_order": 3}])
    moved = type(moved)(type(moved.principal)(SHARED, "active", "sub-replacement-account"), moved.profiles)
    _provision(db, moved, {})
    try:
        assert api.client.get("/auth/session").json()["state"] == "signed_out"
        assert _login_error(Api(monkeypatch, db).google()) == "operator_not_permitted", "the old account is out"
    finally:
        _provision(db, SHARED_ROSTER, {})
    assert _login_error(Api(monkeypatch, db).google()) is None


def test_omitting_the_subject_in_the_roster_never_unpins_it(monkeypatch, db, ids) -> None:
    _provision(db, _roster(SHARED, [{"key": "ana", "display_name": "Ana", "role": "admin", "sort_order": 1}],
                           pinned=False), {})
    assert _sql(db, "select provider_issuer, provider_subject from platform.auth_principal where id = %s",
                (ids["shared"],))[0] == ("https://accounts.google.com", _sub(SHARED))


def test_the_runtime_role_cannot_pin_or_unpin_an_account(monkeypatch, db, ids) -> None:
    import psycopg

    for sql in ("update platform.auth_principal set provider_subject = 'sub-attacker'",
                "update platform.auth_principal set provider_subject = null, provider_issuer = null"):
        with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql)


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
    hasher = api.app.state.v2_profile_login.hasher
    calls: list[tuple[str, str | None]] = []
    real_verify = hasher.verify
    monkeypatch.setattr(hasher, "verify", lambda pin, encoded: calls.append((pin, encoded)) or real_verify(pin, encoded))
    locked = api.select("bruno", PINS["bruno"], ids)
    assert (locked.status_code, locked.json()) == (401, REFUSED)
    stored = _sql(db, "select pin_hash from platform.operator_profile where operator_id = %s", (ids["bruno"],))[0][0]
    assert calls == [("", stored)], "while locked the submitted PIN is never checked; the cost is still spent"
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


def test_no_profile_route_answer_carries_a_timing_header(monkeypatch, db, ids) -> None:
    """Success, refusal, lockout and an unknown profile look alike in their headers too."""
    timing = ("server-timing", "x-process-time-ms")
    api = Api(monkeypatch, db)
    seen: dict[str, Any] = {"callback": api.google()}
    seen["profiles"] = api.client.get("/auth/profiles")
    seen["session: profile required"] = api.client.get("/auth/session")
    seen["unknown profile"] = api.select("00000000-0000-4000-8000-00000000abcd", "111119", ids)
    seen["wrong pin"] = api.select("carla", "111119", ids)
    seen["malformed pin"] = api.select("carla", "12", ids)
    for _ in range(PROFILE_MAX_FAILURES):
        api.select("bruno", "111119", ids)
    seen["lockout"] = api.select("bruno", PINS["bruno"], ids)
    assert seen["lockout"].json() == REFUSED
    seen["success"] = api.select("ana", PINS["ana"], ids)
    assert seen["success"].status_code == 200
    seen["session: signed in"] = api.client.get("/auth/session")
    seen["clear"] = api.client.post("/auth/profile/clear", json={})
    seen["logout"] = api.client.post("/auth/logout")
    seen["logout again"] = api.client.post("/auth/logout")
    for name, response in seen.items():
        assert not any(h in response.headers for h in timing), name
    assert all(h in api.client.get("/v2/contacts").headers for h in timing), "other routes stay timed"


# ------------------------------------------------- the throttle function (20260928194000)


def _throttle_call(db: str, operation: Any, principal_id: str | None, operator_id: str | None,
                   *, conn: Any = None) -> tuple[bool, bool, bool, bool]:
    """One `platform.record_pin_attempt` call as the real `origenlab_api` login, committed."""
    import psycopg

    sql = ("select principal_locked, profile_locked, principal_lock_started, profile_lock_started "
           "from platform.record_pin_attempt(%s, %s::uuid, %s::uuid)")
    if conn is not None:
        return conn.execute(sql, (operation, principal_id, operator_id)).fetchone()
    with psycopg.connect(runtime_dsn(db)) as own:
        return own.execute(sql, (operation, principal_id, operator_id)).fetchone()


@pytest.mark.parametrize("table, column, value", [
    (t, c, v) for t in ("auth_principal", "operator_profile")
    for c, v in (("failed_attempts", "0"), ("lockout_count", "0"), ("locked_until", "null"),
                 ("last_failed_at", "null"))
])
def test_the_runtime_role_cannot_write_a_throttle_column_directly(db, ids, table, column, value) -> None:
    import psycopg

    with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"update platform.{table} set {column} = {value}")


@pytest.mark.parametrize("table", ["auth_principal", "operator_profile"])
def test_the_runtime_role_cannot_lock_a_throttle_row_itself(db, ids, table) -> None:
    import psycopg

    with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(f"select 1 from platform.{table} for update")


@pytest.mark.parametrize("operation", ["reset", "clear", "set_counters", "", None, "RECORD_FAILURE"])
def test_the_throttle_function_takes_only_a_closed_operation(db, ids, operation) -> None:
    import psycopg

    with pytest.raises((psycopg.errors.InvalidParameterValue, psycopg.errors.NullValueNotAllowed)):
        _throttle_call(db, operation, ids["shared"], ids["ana"])


def test_the_throttle_function_accepts_no_counter_or_timestamp() -> None:
    """Its signature is (operation, principal, profile) and three OUT flags — nothing else."""
    sql = (Path(__file__).resolve().parents[3] / "supabase" / "migrations"
           / "20260928194000_slice1_pin_throttle_definer.sql").read_text()
    head = sql[sql.index("create function platform.record_pin_attempt("):sql.index("language plpgsql")]
    inputs = [line.strip().rstrip(",") for line in head.splitlines()[1:]
              if line.strip().startswith("p_")]
    assert inputs == ["p_operation text", "p_principal_id uuid", "p_operator_id uuid"]
    assert "execute " not in sql.lower().split("as $$", 1)[1].split("$$;", 1)[0], "no dynamic SQL"


def test_the_throttle_function_refuses_every_other_login(db, ids) -> None:
    import psycopg

    fn = "platform.record_pin_attempt(text,uuid,uuid)"
    with _owner(db) as conn, conn.cursor() as cur:
        cur.execute(
            "select array_agg(r order by r) from unnest(array['origenlab_worker', 'origenlab_migrator', "
            "'origenlab_owner', 'anon', 'authenticated', 'service_role']) r "
            "where has_function_privilege(r, %s, 'EXECUTE') and r <> 'origenlab_owner'", (fn,))
        assert cur.fetchone()[0] is None, "EXECUTE is origenlab_api's alone"
        cur.execute("select coalesce(array_agg(a.grantee::regrole::text order by 1) "
                    "filter (where a.grantee <> p.proowner), '{}') "
                    "from pg_proc p, aclexplode(p.proacl) a where p.oid = %s::regprocedure", (fn,))
        assert cur.fetchone()[0] == ["origenlab_api"], "no PUBLIC grant, no other grantee"
        # The maintenance login under `set role origenlab_api` holds EXECUTE through the role, but
        # session_user is not origenlab_api: the body refuses (ARCHITECTURE.md §6.2 item 7).
        cur.execute("grant origenlab_api to session_user with set true, inherit false")
        cur.execute("set role origenlab_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="refused for login"):
            cur.execute(f"select * from platform.record_pin_attempt('begin_attempt', %s::uuid, null)",
                        (ids["shared"],))
        conn.rollback()


def test_the_throttle_function_computes_the_documented_policy(db, ids) -> None:
    from datetime import timedelta

    from origenlab_api.v2.profile_auth import LOCK_BASE, LOCK_MAX, lock_duration

    carla = ids["carla"]
    # A failure older than FAILURE_WINDOW no longer counts; a lockout older than LOCKOUT_MEMORY
    # no longer doubles.
    _sql(db, "update platform.operator_profile set failed_attempts = 4, lockout_count = 2, "
             "last_failed_at = now() - interval '2 days' where operator_id = %s", (carla,))
    assert _throttle_call(db, "record_failure", ids["shared"], carla) == (False, False, False, False)
    assert _profile_throttle(db, carla) == (1, 0, False)
    # Within the window the fifth failure locks, for LOCK_BASE × 2^(earlier lockouts).
    _sql(db, "update platform.operator_profile set failed_attempts = 4, lockout_count = 1, "
             "last_failed_at = now() - interval '1 minute' where operator_id = %s", (carla,))
    assert _throttle_call(db, "record_failure", ids["shared"], carla) == (False, False, False, True)
    assert _profile_throttle(db, carla) == (0, 2, True)
    lasted = _sql(db, "select locked_until - last_failed_at from platform.operator_profile "
                      "where operator_id = %s", (carla,))[0][0]
    assert lasted == lock_duration(1) == 2 * LOCK_BASE
    # A failure while locked counts nothing and does not extend the lock.
    before = _sql(db, "select locked_until, failed_attempts, lockout_count from platform.operator_profile "
                      "where operator_id = %s", (carla,))[0]
    assert _throttle_call(db, "record_failure", ids["shared"], carla) == (False, True, False, False)
    assert _sql(db, "select locked_until, failed_attempts, lockout_count from platform.operator_profile "
                    "where operator_id = %s", (carla,))[0] == before
    # The cap.
    _sql(db, "update platform.operator_profile set failed_attempts = 4, lockout_count = 40, "
             "locked_until = null, last_failed_at = now() - interval '1 minute' where operator_id = %s", (carla,))
    _throttle_call(db, "record_failure", ids["shared"], carla)
    assert _sql(db, "select locked_until - last_failed_at from platform.operator_profile "
                    "where operator_id = %s", (carla,))[0][0] == LOCK_MAX == timedelta(hours=24)


def test_a_success_is_refused_while_locked_and_needs_a_profile_of_the_principal(db, ids) -> None:
    import psycopg

    _sql(db, "update platform.operator_profile set locked_until = now() + interval '5 minutes' "
             "where operator_id = %s", (ids["ana"],))
    with pytest.raises(psycopg.errors.LockNotAvailable):
        _throttle_call(db, "record_success", ids["shared"], ids["ana"])
    assert _profile_throttle(db, ids["ana"])[2] is True, "claiming success lifts no lock"
    _sql(db, "update platform.auth_principal set locked_until = now() + interval '5 minutes' "
             "where id = %s", (ids["shared"],))
    with pytest.raises(psycopg.errors.LockNotAvailable):
        _throttle_call(db, "record_success", ids["shared"], ids["bruno"])
    with pytest.raises(psycopg.errors.NoDataFound):
        _throttle_call(db, "record_success", ids["shared"], ids["franco"])  # another principal's


def test_another_principals_profile_is_counted_as_unknown(db, ids) -> None:
    before = _profile_throttle(db, ids["franco"])
    assert _throttle_call(db, "record_failure", ids["shared"], ids["franco"]) == (False, False, False, False)
    assert _profile_throttle(db, ids["franco"]) == before
    assert _sql(db, "select failed_attempts from platform.auth_principal where id = %s",
                (ids["shared"],))[0][0] == 1


def test_begin_attempt_holds_the_rows_until_the_attempt_ends(db, ids) -> None:
    """Serialization across workers: a second attempt waits for the first to commit."""
    import psycopg

    first = psycopg.connect(runtime_dsn(db))
    second = psycopg.connect(runtime_dsn(db))
    try:
        _throttle_call(db, "begin_attempt", ids["shared"], ids["carla"], conn=first)  # open transaction
        second.execute("set lock_timeout = '300ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            _throttle_call(db, "begin_attempt", ids["shared"], ids["bruno"], conn=second)
        second.rollback()
        _throttle_call(db, "record_failure", ids["shared"], ids["carla"], conn=first)
        first.commit()
        second.execute("set lock_timeout = '300ms'")
        assert _throttle_call(db, "begin_attempt", ids["shared"], ids["bruno"], conn=second)[:2] == (False, False)
        second.rollback()
    finally:
        first.close()
        second.close()
    assert _profile_throttle(db, ids["carla"]) == (1, 0, False)


def test_concurrent_failures_through_the_function_are_all_counted(db, ids) -> None:
    attempts = PRINCIPAL_MAX_FAILURES - 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=attempts) as pool:
        list(pool.map(lambda _: _throttle_call(db, "record_failure", ids["shared"], None), range(attempts)))
    assert _sql(db, "select failed_attempts, coalesce(locked_until > now(), false) from platform.auth_principal "
                    "where id = %s", (ids["shared"],))[0] == (attempts, False)
    assert _throttle_call(db, "record_failure", ids["shared"], None)[2] is True, "the tenth starts the lock"


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
    # The admin selection was revoked by the switch: a copy of that cookie is signed out.
    other = api.new_client()
    other.cookies.set(api.cookie, admin_cookie)
    assert other.get("/auth/session").json()["state"] == "signed_out"
    assert _block_all(other).status_code == 401


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
        assert api.client.get("/v2/contacts").status_code == 401
        assert api.client.get("/auth/session").json()["authenticated"] is False
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


def _individual_row(db: str, api: Api, cookie: str | None = None) -> tuple[Any, ...]:
    signer = api.app.state.v2_google_auth.signer
    sid = signer.load_any_session(cookie or api.session_cookie()).sid
    rows = _sql(db, "select sign_in_kind, principal_id, operator_id, account_operator_id::text, revoked_reason, "
                    "extract(epoch from expires_at)::bigint from platform.auth_session where token_hash = %s",
                (signer.session_token_hash(sid),))
    assert len(rows) == 1
    return rows[0]


def _cookie_exp(api: Api) -> int:
    import base64

    body = api.session_cookie().partition(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["exp"]


def _individual_id(db: str) -> str:
    return _sql(db, "select id::text from platform.operator where email_norm = %s", (INDIVIDUAL,))[0][0]


def test_an_individual_session_is_a_revocable_row_too(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google(INDIVIDUAL)
    kind, principal, profile, account, revoked, expires = _individual_row(db, api)
    assert (kind, principal, profile, account, revoked) == ("google_account", None, None, _individual_id(db), None)
    session = api.app.state.v2_google_auth.signer.load_session(api.session_cookie())
    assert session.operator_id == _individual_id(db)
    assert abs(expires - _cookie_exp(api)) <= 1, "the row expires with the cookie"
    dumped = "\n".join(r[0] for r in _sql(db, "select row_to_json(t)::text from platform.auth_session t"))
    assert session.sid not in dumped, "only the keyed hash of the identifier is stored"


@pytest.mark.parametrize("production", [False, True])
def test_a_copied_individual_cookie_fails_on_another_instance_right_after_logout(monkeypatch, db, ids,
                                                                                 production) -> None:
    first = Api(monkeypatch, db, production=production)
    first.google(INDIVIDUAL)
    cookie = first.session_cookie()
    second = Api(monkeypatch, db, production=production)
    copy = _copy_to(second, cookie)
    assert copy.get("/v2/contacts").status_code == 200, "the copy works while the session is live"
    assert first.client.post("/auth/logout").status_code == 200
    assert _individual_row(db, first, cookie)[4] == "logout"
    assert copy.get("/auth/session").json()["state"] == "signed_out"
    assert copy.get("/v2/contacts").status_code == 401
    assert _block_all(copy).status_code == 401
    event = _sql(db, "select event_type, principal_id, operator_id::text from platform.auth_event "
                     "order by occurred_at desc limit 1")[0]
    assert event == ("session.logout", None, _individual_id(db))


def test_disabling_an_individual_operator_or_changing_its_role_ends_its_session(monkeypatch, db, ids) -> None:
    oid = _individual_id(db)
    try:
        for change in ("role = 'sales'", "status = 'disabled'"):
            api = Api(monkeypatch, db)
            api.google(INDIVIDUAL)
            assert api.client.get("/v2/contacts").status_code == 200
            _sql(db, f"update platform.operator set {change} where id = %s", (oid,))
            assert api.client.get("/auth/session").json()["state"] == "signed_out", change
            assert api.client.get("/v2/contacts").status_code == 401, change
            _sql(db, "update platform.operator set role = 'admin', status = 'active' where id = %s", (oid,))
    finally:
        _sql(db, "update platform.operator set role = 'admin', status = 'active' where id = %s", (oid,))


def test_an_individual_session_without_its_row_or_past_its_database_expiry_is_refused(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google(INDIVIDUAL)
    signer = api.app.state.v2_google_auth.signer
    session = signer.load_session(api.session_cookie())
    _sql(db, "update platform.auth_session set expires_at = issued_at + interval '1 millisecond' "
             "where token_hash = %s", (signer.session_token_hash(session.sid),))
    assert api.client.get("/auth/session").json()["state"] == "signed_out"
    forged = signer.dump_session(email_norm=INDIVIDUAL, operator_id=session.operator_id, google_sub="s",
                                 operator_version=session.operator_version, sid="y" * 43, ttl=600,
                                 now=time.time())
    api.client.cookies.set(api.cookie, forged)
    assert api.client.get("/auth/session").json()["state"] == "signed_out", "correctly signed, never recorded"


def test_an_individual_session_names_only_a_google_account_operator(db, ids) -> None:
    import psycopg

    with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("insert into platform.auth_session (token_hash, sign_in_kind, account_operator_id, expires_at) "
                     "values (%s, 'google_account', %s::uuid, now() + interval '1 hour')",
                     (b"\x01" * 32, ids["ana"]))


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


# ------------------------------------------------------------------ revocable sessions


def _session_row(db: str, api: Api, cookie: str | None = None) -> tuple[Any, ...]:
    signer = api.app.state.v2_profile_login.signer
    sid = signer.load_any_session(cookie or api.session_cookie()).sid
    rows = _sql(db, "select operator_id::text, revoked_reason, expires_at, extract(epoch from expires_at)::bigint "
                    "from platform.auth_session where token_hash = %s", (signer.session_token_hash(sid),))
    assert len(rows) == 1
    return rows[0]


def _copy_to(api: Api, cookie: str) -> TestClient:
    client = api.new_client()
    client.cookies.set(api.cookie, cookie)
    return client


def test_a_copied_cookie_fails_on_another_instance_right_after_logout(monkeypatch, db, ids) -> None:
    first = _selected(monkeypatch, db, ids, "carla")
    second = Api(monkeypatch, db)  # its own app, settings and connections
    copy = _copy_to(second, first.session_cookie())
    assert copy.get("/v2/contacts").status_code == 200, "the copy works while the session is live"
    assert first.client.post("/auth/logout").status_code == 200
    assert _session_row(db, first, copy.cookies.get(second.cookie))[1] == "logout"
    assert copy.get("/auth/session").json()["state"] == "signed_out"
    assert copy.get("/v2/contacts").status_code == 401
    assert copy.get("/auth/profiles").status_code == 401
    assert second.select("carla", PINS["carla"], ids, client=copy).status_code == 401
    assert copy.post("/auth/profile/clear", json={}).status_code == 401


def test_cambiar_perfil_revokes_the_selected_profile_everywhere(monkeypatch, db, ids) -> None:
    first = _selected(monkeypatch, db, ids, "ana")
    admin_cookie = first.session_cookie()
    second = Api(monkeypatch, db)
    copy = _copy_to(second, admin_cookie)
    assert first.client.post("/auth/profile/clear", json={}).status_code == 200
    assert _session_row(db, first, admin_cookie)[1] == "profile_cleared"
    assert copy.get("/auth/session").json()["state"] == "signed_out"
    assert _block_all(copy).status_code == 401, "the admin selection does not survive in a copy"
    fresh = _copy_to(second, first.session_cookie())
    assert fresh.get("/auth/session").json()["state"] == "profile_required", "the successor lives on"


def test_rotation_never_extends_the_sign_in(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    signer = api.app.state.v2_profile_login.signer
    cookies = [api.session_cookie()]
    assert api.select("ana", PINS["ana"], ids).status_code == 200
    cookies.append(api.session_cookie())
    assert api.select("carla", PINS["carla"], ids).status_code == 200, "a direct switch"
    cookies.append(api.session_cookie())
    assert api.client.post("/auth/profile/clear", json={}).status_code == 200
    cookies.append(api.session_cookie())
    sessions = [signer.load_any_session(c) for c in cookies]
    rows = [_session_row(db, api, c) for c in cookies]
    assert len({s.sid for s in sessions}) == 4, "every step has a new identifier"
    assert {s.exp for s in sessions} == {sessions[0].exp}, "the cookie's expiry never moves"
    assert {r[3] for r in rows} == {sessions[0].exp}, "nor does the row's"
    assert [r[:2] for r in rows] == [(None, "profile_selected"), (ids["ana"], "profile_selected"),
                                     (ids["carla"], "profile_cleared"), (None, None)]


def test_the_database_expiry_holds_whatever_the_cookie_says(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "carla")
    signer = api.app.state.v2_profile_login.signer
    sid = signer.load_any_session(api.session_cookie()).sid
    # The owner may shorten a session, never extend it (trigger); the cookie still says 8 h.
    _sql(db, "update platform.auth_session set expires_at = issued_at + interval '1 millisecond' "
             "where token_hash = %s", (signer.session_token_hash(sid),))
    assert api.client.get("/auth/session").json()["state"] == "signed_out"
    assert api.client.get("/v2/contacts").status_code == 401


def test_only_a_keyed_hash_of_the_session_identifier_is_stored(monkeypatch, db, ids) -> None:
    import hashlib
    import hmac

    api = _selected(monkeypatch, db, ids, "bruno")
    sid = api.app.state.v2_profile_login.signer.load_any_session(api.session_cookie()).sid
    dumped = "\n".join(r[0] for r in _sql(db, "select row_to_json(t)::text from platform.auth_session t"))
    assert sid not in dumped and api.session_cookie() not in dumped
    stored = {bytes(h) for (h,) in _sql(db, "select token_hash from platform.auth_session")}
    assert hashlib.sha256(sid.encode()).digest() not in stored, "not an unkeyed hash either"
    keyed = hmac.new(SESSION_SECRET.encode(), b"origenlab.dashboard.session-id.v1." + sid.encode(),
                     hashlib.sha256).digest()
    assert keyed in stored


def test_a_session_without_its_row_is_refused(monkeypatch, db, ids) -> None:
    api = _selected(monkeypatch, db, ids, "carla")
    signer = api.app.state.v2_profile_login.signer
    session = signer.load_any_session(api.session_cookie())
    # Correctly signed, but naming an identifier that was never recorded.
    api.client.cookies.set(api.cookie, signer.dump_principal_session(session.rotated(session.profile, "x" * 43)))
    assert api.client.get("/auth/session").json()["state"] == "signed_out"


def _sets_or_clears_a_cookie(response: Any) -> bool:
    return bool(response.headers.get_list("set-cookie"))


@pytest.mark.parametrize("kind", ["shared", "individual"])
def test_a_failed_revocation_is_reported_not_claimed_and_a_retry_succeeds(monkeypatch, db, ids, kind) -> None:
    if kind == "shared":
        api = _selected(monkeypatch, db, ids, "carla")
        target, method, row = api.app.state.v2_profile_login.profiles, "revoke_session", _session_row
        revoked_at = 1
    else:
        api = Api(monkeypatch, db)
        api.google(INDIVIDUAL)
        target, method, row = api.app.state.v2_auth_sessions, "revoke_operator_session", _individual_row
        revoked_at = 4
    cookie = api.session_cookie()
    copy = _copy_to(Api(monkeypatch, db), cookie)
    real = getattr(target, method)

    def broken(**_: Any) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(target, method, broken)
    response = api.client.post("/auth/logout")
    assert (response.status_code, response.json()) == (503, {"detail": "logout_not_recorded"})
    assert not _sets_or_clears_a_cookie(response), "the cookie is left in place: the session is still live"
    assert row(db, api, cookie)[revoked_at] is None, "nothing pretends the row was revoked"
    assert api.session_cookie() == cookie
    assert api.client.get("/auth/session").json()["state"] == "signed_in", "this browser stays signed in"
    assert copy.get("/auth/session").json()["state"] == "signed_in", "and so, honestly, does a copy"

    monkeypatch.setattr(target, method, real)
    retry = api.client.post("/auth/logout")
    assert (retry.status_code, retry.json()) == (200, {"authenticated": False})
    assert any("max-age=0" in c.lower() for c in retry.headers.get_list("set-cookie"))
    assert row(db, api, cookie)[revoked_at] == "logout"
    assert copy.get("/auth/session").json()["state"] == "signed_out", "the retry ends the copy too"


def test_the_runtime_role_can_neither_extend_nor_reopen_nor_delete_a_session(monkeypatch, db, ids) -> None:
    import psycopg

    _selected(monkeypatch, db, ids, "ana")
    for sql, error in (("update platform.auth_session set expires_at = expires_at + interval '1 day'",
                        psycopg.errors.InsufficientPrivilege),
                       ("update platform.auth_session set operator_id = null", psycopg.errors.InsufficientPrivilege),
                       ("delete from platform.auth_session", psycopg.errors.InsufficientPrivilege)):
        with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(error):
            conn.execute(sql)
    with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("update platform.auth_session set revoked_at = null, revoked_reason = null "
                     "where revoked_at is not null")


def _run_admin(monkeypatch, argv: list[str], *, dsn: str) -> tuple[int, str, str]:
    import importlib.util
    import io
    from contextlib import redirect_stderr, redirect_stdout

    spec = importlib.util.spec_from_file_location("profile_auth_admin_script",
                                                  API_DIR / "scripts" / "profile_auth_admin.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    monkeypatch.setenv("ORIGENLAB_V2_PROVISIONING_DATABASE_URL", dsn)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = module.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_the_migrator_prunes_ended_sessions_only_as_planned(monkeypatch, db, ids) -> None:
    live = _selected(monkeypatch, db, ids, "ana")
    _sql(db, "insert into platform.auth_session (token_hash, principal_id, issued_at, expires_at) "
             "values (%s, %s, now() - interval '3 days', now() - interval '2 days')",
         (secrets.token_bytes(32), ids["shared"]))
    code, out, err = _run_admin(monkeypatch, ["prune-sessions"], dsn=db)
    assert code == 0, err
    assert "PLAN (read-only" in out
    pending = int(next(line for line in out.splitlines() if line.startswith("pending changes:")).split(":")[1])
    assert pending >= 1
    cutoff = out.split("--confirm-cutoff ")[1].split()[0]
    database = db.rsplit("/", 1)[1]
    for bad in (["--confirm-changes", str(pending + 1), "--confirm-database", database, "--confirm-cutoff", cutoff],
                ["--confirm-changes", str(pending), "--confirm-database", "origenlab_clean", "--confirm-cutoff", cutoff],
                ["--confirm-changes", str(pending), "--confirm-database", database]):
        code, _, err = _run_admin(monkeypatch, ["prune-sessions", "--apply", *bad], dsn=db)
        assert code == 2 and "refused" in err
    code, out, err = _run_admin(monkeypatch, ["prune-sessions", "--apply", "--confirm-changes", str(pending),
                                              "--confirm-database", database, "--confirm-cutoff", cutoff], dsn=db)
    assert code == 0 and "APPLIED" in out, err
    assert _sql(db, "select count(*) from platform.auth_session where expires_at < now() - interval '1 day'")[0][0] == 0
    assert live.client.get("/v2/contacts").status_code == 200, "a live session is never pruned"
    code, _, err = _run_admin(monkeypatch, ["prune-sessions"], dsn=runtime_dsn(db))
    assert code == 2 and "SQLSTATE 42501" in err, "the runtime role cannot run it"


# --------------------------------------------------------------- clearing a lockout


def _clear(monkeypatch, db: str, *extra: str) -> tuple[int, str, str]:
    return _run_admin(monkeypatch, ["clear-lockout", "--principal-email", SHARED, *extra], dsn=db)


def _pending(out: str) -> int:
    return int(next(line for line in out.splitlines() if line.startswith("pending changes:")).split(":")[1])


def test_the_migrator_clears_a_profile_lockout_only_as_planned(monkeypatch, db, ids) -> None:
    using = _selected(monkeypatch, db, ids, "ana")
    api = Api(monkeypatch, db)
    api.google()
    for _ in range(PROFILE_MAX_FAILURES):
        api.select("carla", "111119", ids)
    assert api.select("carla", PINS["carla"], ids).json() == REFUSED, "locked"
    versions = _sql(db, "select version from platform.operator_profile where operator_id = %s", (ids["carla"],))

    code, out, err = _clear(monkeypatch, db, "--profile", "carla")
    assert code == 0, err
    assert "PLAN (read-only" in out and "LOCKED until" in out and "c***@origenlab.cl" in out
    assert _pending(out) == 1
    assert _profile_throttle(db, ids["carla"])[2] is True, "the plan wrote nothing"
    database = db.rsplit("/", 1)[1]
    for bad in (["--confirm-changes", "2", "--confirm-database", database],
                ["--confirm-changes", "1", "--confirm-database", "origenlab_clean"],
                ["--confirm-changes", "1"]):
        code, _, err = _clear(monkeypatch, db, "--profile", "carla", "--apply", *bad)
        assert code == 2 and "refused" in err
    assert _profile_throttle(db, ids["carla"])[2] is True, "a refused apply wrote nothing"

    code, out, err = _clear(monkeypatch, db, "--profile", "carla", "--apply",
                            "--confirm-changes", "1", "--confirm-database", database)
    assert code == 0 and "APPLIED" in out, err
    assert _profile_throttle(db, ids["carla"]) == (0, 0, False)
    event = _sql(db, "select event_type, principal_id::text, principal_email_norm, operator_id::text "
                     "from platform.auth_event where event_type = 'lockout.cleared' order by occurred_at desc limit 1")
    assert event == [("lockout.cleared", ids["shared"], SHARED, ids["carla"])]
    assert api.select("carla", PINS["carla"], ids).status_code == 200
    assert _sql(db, "select version from platform.operator_profile where operator_id = %s",
                (ids["carla"],)) == versions, "clearing signs nobody out"
    assert using.client.get("/v2/contacts").status_code == 200
    code, out, _ = _clear(monkeypatch, db, "--profile", "carla")
    assert code == 0 and "nothing to apply" in out


def test_the_migrator_clears_the_principal_wide_lockout(monkeypatch, db, ids) -> None:
    api = Api(monkeypatch, db)
    api.google()
    for i in range(PRINCIPAL_MAX_FAILURES):
        api.select(f"00000000-0000-4000-8000-{i:012d}", "111119", ids)
    assert api.select("ana", PINS["ana"], ids).json() == REFUSED
    code, out, err = _clear(monkeypatch, db, "--principal")
    assert code == 0 and _pending(out) == 1, err
    code, out, err = _clear(monkeypatch, db, "--principal", "--apply", "--confirm-changes", "1",
                            "--confirm-database", db.rsplit("/", 1)[1])
    assert code == 0, err
    assert api.select("ana", PINS["ana"], ids).status_code == 200
    assert _sql(db, "select operator_id from platform.auth_event where event_type = 'lockout.cleared' "
                    "order by occurred_at desc limit 1") == [(None,)], "the principal-wide throttle"


@pytest.mark.parametrize("argv, reason", [
    ([], "name what to clear"),
    (["--profile", "nadie"], "no profile 'nadie'"),
    (["--profile", "Carla Admin"], "not profile keys"),
])
def test_clearing_refuses_an_unclear_target(monkeypatch, db, ids, argv, reason) -> None:
    code, _, err = _clear(monkeypatch, db, *argv)
    assert code == 2 and reason in err


def test_the_runtime_role_can_neither_clear_a_lockout_nor_record_one(monkeypatch, db, ids) -> None:
    import psycopg

    code, _, err = _clear(monkeypatch, runtime_dsn(db), "--profile", "carla")
    assert code == 2 and "SQLSTATE 42501" in err
    with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("insert into platform.auth_event (event_type, principal_id, principal_email_norm) "
                      "values ('lockout.cleared', %s, %s)", (ids["shared"], SHARED))
    with psycopg.connect(runtime_dsn(db)) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("update platform.operator_profile set lockout_count = 0, status = 'active'")


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
