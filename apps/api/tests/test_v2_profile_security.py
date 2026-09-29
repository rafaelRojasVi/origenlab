"""Operator profiles behind a shared Google sign-in — the parts that need no database.

The pepper and its startup refusals, the PIN policy, the Argon2id hash (format, pepper,
floor, decoy), the two session-cookie shapes and their tamper resistance, the identity
adapter's re-checks against a stub repository, the local-only development shortcut, and
the exact route surface. `test_v2_profile_login.py` proves the rest against a disposable
PostgreSQL. Every name, address and PIN here is invented.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.v2.auth_session import (
    CookieRefused,
    CookieSigner,
    PrincipalSession,
    ProfileSelection,
)
from origenlab_api.v2.identity import (
    PROFILE_REQUIRED,
    GoogleSessionIdentity,
    IdentityRefused,
    OperatorIdentity,
    ProfileRequired,
)
from origenlab_api.v2.profile_auth import (
    LOCK_BASE,
    LOCK_MAX,
    PrincipalRecord,
    ProfileBinding,
    SessionState,
    _Throttle,
    lock_duration,
)
from origenlab_api.v2.profile_pin import (
    FLOOR,
    Argon2Parameters,
    PepperRefused,
    PinHasher,
    PinPolicyRefused,
    validate_new_pin,
    validate_pepper,
)
from origenlab_api.v2.profile_routes import is_reserved_dev_address

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
REMOTE = "postgresql://origenlab_api:pw@db.example.test:5432/postgres"
PEPPER = "pepper-for-tests-" + "abcdefghijklmnopqrstuvwxyz0123456789"
SECRET = "session-secret-for-tests-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
PIN = "482913"
PHC_DB_CHECK = re.compile(
    r"^\$argon2id\$v=19\$m=[0-9]{1,8},t=[0-9]{1,3},p=[0-9]{1,3}\$[A-Za-z0-9+/]{16,}\$[A-Za-z0-9+/]{32,}$"
)
PRINCIPAL_ID = "00000000-0000-4000-8000-00000000c0de"
CARLA = "00000000-0000-4000-8000-000000000003"


@pytest.fixture(scope="module")
def hasher() -> PinHasher:
    return PinHasher(PEPPER, FLOOR)


# ------------------------------------------------------------------------------ pepper


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_pepper_refuses_to_start(value) -> None:
    with pytest.raises(PepperRefused, match="required"):
        validate_pepper(value, session_secret=SECRET)


@pytest.mark.parametrize("value", ["short-pepper", "a" * 64, "abababababababababababababababababab"])
def test_a_weak_pepper_refuses_to_start(value) -> None:
    with pytest.raises(PepperRefused, match="too weak"):
        validate_pepper(value, session_secret=SECRET)


def test_the_pepper_must_not_be_the_session_secret() -> None:
    with pytest.raises(PepperRefused, match="differ"):
        validate_pepper(SECRET, session_secret=SECRET)


def test_no_refusal_quotes_the_pepper() -> None:
    weak = "Zq7-weak-but-secret"
    with pytest.raises(PepperRefused) as caught:
        validate_pepper(weak, session_secret=SECRET)
    assert weak not in str(caught.value)
    assert PEPPER not in repr(PinHasher(PEPPER, FLOOR))


# --------------------------------------------------------------------------- PIN policy


@pytest.mark.parametrize("pin", ["482913", "70316402", "905512384761"])
def test_a_good_pin_passes(pin) -> None:
    assert validate_new_pin(pin) == pin


@pytest.mark.parametrize("pin", ["", "12345", "1234567890123", "48291a", " 482913", "４８２９１３",
                                 "000000", "123456", "654321", "890123", "3210987"])
def test_a_weak_or_malformed_pin_is_refused_without_being_quoted(pin) -> None:
    with pytest.raises(PinPolicyRefused) as caught:
        validate_new_pin(pin)
    if pin.strip():
        assert pin not in str(caught.value)


# ------------------------------------------------------------------------------ hashing


def test_a_hash_is_an_argon2id_phc_string_the_database_accepts(hasher) -> None:
    encoded = hasher.hash(PIN)
    assert PHC_DB_CHECK.match(encoded)
    assert PIN not in encoded
    assert hasher.hash(PIN) != encoded, "every hash has its own salt"


def test_the_default_parameters_are_rfc9106_and_above_the_floor() -> None:
    encoded = PinHasher(PEPPER).hash(PIN)
    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_verification_needs_the_right_pin_and_the_right_pepper(hasher) -> None:
    encoded = hasher.hash(PIN)
    assert hasher.verify(PIN, encoded) is True
    assert hasher.verify("482914", encoded) is False
    other = PinHasher("another-pepper-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", FLOOR)
    assert other.verify(PIN, encoded) is False, "a copy of the table alone cannot verify a PIN"


@pytest.mark.parametrize("encoded", [None, "", "482913", "$argon2i$v=19$m=65536,t=3,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g"])
def test_an_unusable_hash_never_verifies(hasher, encoded) -> None:
    assert hasher.verify(PIN, encoded) is False


def test_a_hash_below_the_floor_never_verifies(hasher) -> None:
    weak = hasher.hash(PIN).replace(f"m={FLOOR.memory_kib},", "m=8,")
    assert hasher.verify(PIN, weak) is False
    with pytest.raises(ValueError):
        PinHasher(PEPPER, Argon2Parameters(memory_kib=8, iterations=1, lanes=1))


def test_an_overlong_submission_is_refused_without_hashing_it(hasher) -> None:
    encoded = hasher.hash(PIN)
    assert hasher.verify(PIN + "0" * 100, encoded) is False
    assert hasher.verify(123456, encoded) is False  # type: ignore[arg-type]


def test_a_refusal_costs_one_verification_like_a_wrong_pin(hasher) -> None:
    encoded = hasher.hash(PIN)

    def best(fn) -> float:
        times = []
        for _ in range(5):
            start = time.perf_counter()
            fn()
            times.append(time.perf_counter() - start)
        return min(times)

    wrong = best(lambda: hasher.verify("111111", encoded))
    decoy = best(hasher.dummy_verify)
    assert 0.5 < decoy / wrong < 2.0


# ------------------------------------------------------------------------------ throttle


def test_lock_durations_double_and_cap() -> None:
    assert lock_duration(0) == LOCK_BASE
    assert lock_duration(1) == 2 * LOCK_BASE
    assert lock_duration(3) == 8 * LOCK_BASE
    assert lock_duration(40) == LOCK_MAX


def test_old_failures_stop_counting() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    stale = _Throttle(4, 2, None, now - timedelta(days=2))
    after, locked = stale.after_failure(now, 5)
    assert (after.failed_attempts, after.lockout_count, locked) == (1, 0, False)
    fresh = _Throttle(4, 1, None, now - timedelta(minutes=1))
    after, locked = fresh.after_failure(now, 5)
    assert locked and after.failed_attempts == 0 and after.lockout_count == 2
    assert after.locked_until == now + 2 * LOCK_BASE


# ------------------------------------------------------------------------ session cookies


def _principal_session(**over: Any) -> PrincipalSession:
    now = int(time.time())
    base = dict(principal_id=PRINCIPAL_ID, email="compartida@origenlab.cl", subject="1098",
                principal_version=1, auth_time=now, exp=now + 3600, method="google", sid="sid-" + "a" * 40)
    base.update(over)
    return PrincipalSession(**base)


def test_a_principal_session_round_trips_with_and_without_a_profile() -> None:
    signer = CookieSigner(SECRET)
    bare = _principal_session()
    assert signer.load_any_session(signer.dump_principal_session(bare)) == bare
    chosen = bare.with_profile(ProfileSelection(CARLA, 2, 3, bare.auth_time))
    loaded = signer.load_any_session(signer.dump_principal_session(chosen))
    assert loaded == chosen
    assert loaded.exp == bare.exp and loaded.auth_time == bare.auth_time


def test_a_principal_session_is_never_an_operator_session() -> None:
    signer = CookieSigner(SECRET)
    with pytest.raises(CookieRefused):
        signer.load_session(signer.dump_principal_session(_principal_session()))


def _tamper(value: str, **changes: Any) -> str:
    body, _, tag = value.partition(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload.update(changes)
    forged = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=").decode()
    return f"{forged}.{tag}"


@pytest.mark.parametrize("changes", [{"op": "00000000-0000-4000-8000-0000000000ad"}, {"opv": 99},
                                     {"pid": "00000000-0000-4000-8000-000000000bad"}, {"m": "google"},
                                     {"exp": 4102444800}, {"sub": "another-subject"},
                                     {"iss": "accounts.google.com"}, {"sid": "another-session-id"}])
def test_a_tampered_principal_session_is_refused(changes) -> None:
    signer = CookieSigner(SECRET)
    value = signer.dump_principal_session(
        _principal_session(method="dev").with_profile(ProfileSelection(CARLA, 1, 1, 1)))
    with pytest.raises(CookieRefused):
        signer.load_any_session(_tamper(value, **changes))


def test_an_unknown_session_method_is_refused() -> None:
    signer = CookieSigner(SECRET)
    forged = signer._dump(b"origenlab.dashboard.session.v1", {  # noqa: SLF001 - signed by us on purpose
        "k": "principal", "pid": PRINCIPAL_ID, "email": "c@origenlab.cl", "sub": "x",
        "pv": 1, "at": 1, "exp": int(time.time()) + 60, "m": "header"})
    with pytest.raises(CookieRefused):
        signer.load_any_session(forged)


# ------------------------------------------------------------------ identity re-checks


class _Profiles:
    """Stands in for ProfileAuthRepository.binding().

    The session row is live and names whatever operator the cookie names, unless `session`
    says otherwise; `session=None` is a row that does not exist.
    """

    _AS_SIGNED = object()

    def __init__(self, principal: PrincipalRecord | None, binding: ProfileBinding | None,
                 session: Any = _AS_SIGNED) -> None:
        self.principal, self.binding_row, self.session = principal, binding, session
        self.calls: list[tuple[str, str | None, bytes | None]] = []

    def binding(self, principal_id: str, operator_id: str | None, token_hash: bytes | None = None):
        self.calls.append((principal_id, operator_id, token_hash))
        if self.principal is None or self.principal.principal_id != principal_id:
            return None, None, None
        row = SessionState(True, operator_id) if self.session is self._AS_SIGNED else self.session
        if self.binding_row is None or self.binding_row.operator_id != operator_id:
            return self.principal, None, row
        return self.principal, self.binding_row, row


GOOGLE = "https://accounts.google.com"
PRINCIPAL = PrincipalRecord(PRINCIPAL_ID, "compartida@origenlab.cl", "1098", "active", 1, GOOGLE)
UNPINNED = replace(PRINCIPAL, provider_subject=None, provider_issuer=None)
CARLA_BINDING = ProfileBinding(CARLA, "Carla", "sales", "active", 2, "active", 3)


def _resolve(profiles: _Profiles, session: PrincipalSession, *, production: bool = False) -> OperatorIdentity:
    signer = CookieSigner(SECRET)
    port = GoogleSessionIdentity(signer, "origenlab_session", lookup=None, profiles=profiles,  # type: ignore[arg-type]
                                 production=production)
    return port.resolve({"cookie": f"origenlab_session={signer.dump_principal_session(session)}"})


def test_a_bare_principal_session_is_profile_required() -> None:
    with pytest.raises(ProfileRequired) as caught:
        _resolve(_Profiles(PRINCIPAL, CARLA_BINDING), _principal_session())
    assert str(caught.value) == PROFILE_REQUIRED and caught.value.stale is False


def test_a_selected_profile_resolves_to_its_operator_and_role() -> None:
    session = _principal_session().with_profile(ProfileSelection(CARLA, 2, 3, 1))
    operator = _resolve(_Profiles(PRINCIPAL, CARLA_BINDING), session)
    assert (operator.operator_id, operator.role, operator.auth_method) == (CARLA, "sales", "google_profile")
    assert operator.principal_id == PRINCIPAL_ID


@pytest.mark.parametrize("binding", [
    replace(CARLA_BINDING, role="admin", operator_version=3),   # role changed (trigger bumped)
    replace(CARLA_BINDING, profile_version=4),                  # PIN changed
    replace(CARLA_BINDING, operator_status="disabled", operator_version=3),
    replace(CARLA_BINDING, profile_status="disabled", profile_version=4),
    replace(CARLA_BINDING, role="robot"),
    None,                                                       # unlinked from this principal
])
def test_any_change_to_the_selected_profile_ends_its_session(binding) -> None:
    session = _principal_session().with_profile(ProfileSelection(CARLA, 2, 3, 1))
    with pytest.raises(ProfileRequired) as caught:
        _resolve(_Profiles(PRINCIPAL, binding), session)
    assert caught.value.stale is True


@pytest.mark.parametrize("principal", [
    None,
    replace(PRINCIPAL, status="disabled", version=2),
    replace(PRINCIPAL, version=2),
    replace(PRINCIPAL, email_norm="otra@origenlab.cl"),
    replace(PRINCIPAL, provider_subject="another-subject"),
])
def test_a_changed_principal_ends_every_session_outright(principal) -> None:
    session = _principal_session().with_profile(ProfileSelection(CARLA, 2, 3, 1))
    with pytest.raises(IdentityRefused) as caught:
        _resolve(_Profiles(principal, CARLA_BINDING), session)
    assert not isinstance(caught.value, ProfileRequired)


@pytest.mark.parametrize("row", [
    None,                                  # no such session row: never recorded, or pruned
    SessionState(False, CARLA),            # revoked (logout, switch) or expired by the database
    SessionState(True, None),              # the row was rotated to another selection
    SessionState(True, "00000000-0000-4000-8000-00000000beef"),
])
def test_a_session_whose_row_is_not_live_is_refused_outright(row) -> None:
    session = _principal_session().with_profile(ProfileSelection(CARLA, 2, 3, 1))
    with pytest.raises(IdentityRefused, match="session has ended") as caught:
        _resolve(_Profiles(PRINCIPAL, CARLA_BINDING, row), session)
    assert not isinstance(caught.value, ProfileRequired)


def test_the_session_row_is_looked_up_by_a_keyed_hash_never_by_the_identifier() -> None:
    profiles = _Profiles(PRINCIPAL, CARLA_BINDING)
    session = _principal_session().with_profile(ProfileSelection(CARLA, 2, 3, 1))
    _resolve(profiles, session)
    (_, _, token_hash), = profiles.calls
    assert token_hash == CookieSigner(SECRET).session_token_hash(session.sid)
    assert len(token_hash) == 32 and session.sid.encode() not in token_hash
    assert CookieSigner(SECRET + "x").session_token_hash(session.sid) != token_hash, "keyed"


def test_rotation_keeps_the_expiry_and_changes_the_identifier() -> None:
    bare = _principal_session()
    chosen = bare.rotated(ProfileSelection(CARLA, 2, 3, 1), "sid-" + "b" * 40)
    assert (chosen.exp, chosen.auth_time) == (bare.exp, bare.auth_time) and chosen.sid != bare.sid


def test_a_cookie_without_a_session_identifier_is_refused() -> None:
    signer = CookieSigner(SECRET)
    with pytest.raises(CookieRefused):
        signer.load_any_session(signer.dump_principal_session(_principal_session(sid="")))


@pytest.mark.parametrize("session", [
    _principal_session(subject="1099"),                   # same address, recreated account
    _principal_session(issuer="https://evil.example"),    # same subject, another issuer
])
def test_a_session_of_another_google_account_is_refused_outright(session) -> None:
    with pytest.raises(IdentityRefused) as caught:
        _resolve(_Profiles(PRINCIPAL, CARLA_BINDING), session.with_profile(ProfileSelection(CARLA, 2, 3, 1)))
    assert not isinstance(caught.value, ProfileRequired)


def test_production_refuses_a_principal_with_no_pinned_google_account() -> None:
    session = _principal_session().with_profile(ProfileSelection(CARLA, 2, 3, 1))
    assert _resolve(_Profiles(UNPINNED, CARLA_BINDING), session).operator_id == CARLA, "local only"
    with pytest.raises(IdentityRefused, match="no pinned Google account"):
        _resolve(_Profiles(UNPINNED, CARLA_BINDING), session, production=True)
    assert _resolve(_Profiles(PRINCIPAL, CARLA_BINDING), session, production=True).operator_id == CARLA


@pytest.mark.parametrize("iss", ["https://accounts.google.com", "accounts.google.com"])
def test_both_google_issuer_spellings_verify_as_the_one_canonical_issuer(iss) -> None:
    from origenlab_api.v2.google_oidc import GOOGLE_CANONICAL_ISSUER, validate_claims

    now = int(time.time())
    account = validate_claims(
        {"iss": iss, "aud": "cid", "exp": now + 60, "iat": now, "nonce": "n", "sub": "1098",
         "email_verified": True, "email": "compartida@origenlab.cl", "hd": "origenlab.cl"},
        client_id="cid", nonce="n", workspace_domain="origenlab.cl", now=now)
    assert (account.issuer, account.subject) == (GOOGLE_CANONICAL_ISSUER, "1098")


def test_production_never_honours_a_development_session() -> None:
    session = _principal_session(method="dev").with_profile(ProfileSelection(CARLA, 2, 3, 1))
    assert _resolve(_Profiles(PRINCIPAL, CARLA_BINDING), session).auth_method == "dev_profile"
    with pytest.raises(IdentityRefused):
        _resolve(_Profiles(PRINCIPAL, CARLA_BINDING), session, production=True)


def test_profile_sessions_are_refused_when_profile_login_is_off() -> None:
    signer = CookieSigner(SECRET)
    port = GoogleSessionIdentity(signer, "origenlab_session", lookup=None)  # type: ignore[arg-type]
    cookie = signer.dump_principal_session(_principal_session())
    with pytest.raises(IdentityRefused):
        port.resolve({"cookie": f"origenlab_session={cookie}"})


# ------------------------------------------------------------------- startup refusals


def _env(monkeypatch, *, production: bool = False, google: bool = True, profiles: bool = True,
         pepper: str | None = PEPPER, dev_email: str | None = None, dev: bool = False,
         dsn: str = LOOPBACK, secret: str = SECRET) -> None:
    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", dsn)
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "true" if google else "false")
    monkeypatch.setenv("ORIGENLAB_GOOGLE_CLIENT_ID", "1234567890-test.apps.googleusercontent.com")
    monkeypatch.setenv("ORIGENLAB_GOOGLE_CLIENT_SECRET", secrets.token_urlsafe(24))
    monkeypatch.setenv("ORIGENLAB_AUTH_PUBLIC_BASE_URL",
                       "https://dashboard.origenlab.cl/api" if production else "http://localhost:5173")
    monkeypatch.setenv("ORIGENLAB_AUTH_SESSION_SECRET", secret)
    monkeypatch.setenv("ORIGENLAB_PROFILE_LOGIN_ENABLED", "true" if profiles else "false")
    for name, value in (("ORIGENLAB_PROFILE_PIN_PEPPER", pepper),
                        ("ORIGENLAB_DEV_PROFILE_PRINCIPAL_EMAIL", dev_email)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true" if dev else "false")
    if production:
        monkeypatch.setenv("ORIGENLAB_ENV", "production")
        monkeypatch.setenv("ORIGENLAB_API_BACKEND", "postgres")
        monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u:p@127.0.0.1:5432/unused")
        monkeypatch.setenv("ORIGENLAB_API_CORS_ORIGINS", "https://dashboard.origenlab.cl")
        monkeypatch.setenv("ORIGENLAB_API_AUTH_TOKEN", "proxy-token-for-tests")
    else:
        monkeypatch.delenv("ORIGENLAB_ENV", raising=False)


@pytest.mark.parametrize("pepper", [None, "", "too-short", "a" * 48])
def test_production_refuses_profile_login_without_a_strong_pepper(monkeypatch, pepper) -> None:
    _env(monkeypatch, production=True, pepper=pepper)
    with pytest.raises(PepperRefused):
        create_app()


def test_production_refuses_a_pepper_equal_to_the_session_secret(monkeypatch) -> None:
    _env(monkeypatch, production=True, pepper=SECRET)
    with pytest.raises(PepperRefused, match="differ"):
        create_app()


def test_production_starts_with_a_strong_pepper(monkeypatch) -> None:
    _env(monkeypatch, production=True)
    app = create_app()
    assert app.state.v2_profile_login is not None
    assert app.state.v2_dev_profile_login is False


def test_profile_login_needs_a_way_to_sign_in(monkeypatch) -> None:
    _env(monkeypatch, google=False)
    with pytest.raises(ValueError, match="needs a way to sign in"):
        create_app()


def test_profile_login_needs_the_v2_database(monkeypatch) -> None:
    _env(monkeypatch)
    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL")
    with pytest.raises(ValueError, match="ORIGENLAB_V2_DATABASE_URL"):
        create_app()


def test_production_refuses_the_dev_profile_shortcut(monkeypatch) -> None:
    _env(monkeypatch, production=True, dev_email="prueba@perfiles.test")
    with pytest.raises(ValueError, match="production"):
        create_app()


def test_the_dev_shortcut_needs_the_dev_login_switch(monkeypatch) -> None:
    _env(monkeypatch, google=False, dev_email="prueba@perfiles.test", dev=False)
    with pytest.raises(ValueError, match="ORIGENLAB_DEV_LOGIN_ENABLED"):
        create_app()


@pytest.mark.parametrize("email", ["contacto@origenlab.cl", "someone@gmail.com", "x@test.origenlab.cl"])
def test_the_dev_shortcut_signs_in_only_as_an_invented_address(monkeypatch, email) -> None:
    assert not is_reserved_dev_address(email)
    _env(monkeypatch, google=False, dev=True, dev_email=email)
    with pytest.raises(ValueError, match="reserved"):
        create_app()


def test_the_dev_shortcut_refuses_a_remote_database(monkeypatch) -> None:
    _env(monkeypatch, google=False, dev=True, dev_email="prueba@perfiles.test", dsn=REMOTE)
    with pytest.raises(Exception):
        create_app()


def test_the_dev_shortcut_mounts_only_locally(monkeypatch) -> None:
    _env(monkeypatch, google=False, dev=True, dev_email="prueba@perfiles.test")
    app = create_app()
    assert app.state.v2_dev_profile_login is True
    assert _auth_routes(app)["/auth/dev/principal-session"] == {"post"}


# ------------------------------------------------------------------------- route surface


def _auth_routes(app) -> dict[str, set[str]]:
    return {path: set(ops) for path, ops in app.openapi()["paths"].items() if path.startswith("/auth")}


def test_the_auth_surface_is_exactly_google_session_logout_and_the_three_profile_routes(monkeypatch) -> None:
    _env(monkeypatch)
    assert _auth_routes(create_app()) == {
        "/auth/google/login": {"get"},
        "/auth/google/callback": {"get"},
        "/auth/session": {"get"},
        "/auth/logout": {"post"},
        "/auth/profiles": {"get"},
        "/auth/profile/select": {"post"},
        "/auth/profile/clear": {"post"},
    }


def test_no_route_clears_a_lockout_prunes_sessions_or_administers_profiles(monkeypatch) -> None:
    _env(monkeypatch)
    app = create_app()
    paths = [getattr(route, "path", "") for route in app.routes]
    for word in ("lock", "prune", "roster", "unlock", "throttle", "auth_admin", "provision"):
        assert not [p for p in paths if word in p.lower()], word


def test_profile_routes_are_absent_when_the_switch_is_off(monkeypatch) -> None:
    _env(monkeypatch, profiles=False, pepper=None)
    app = create_app()
    assert set(_auth_routes(app)) == {"/auth/google/login", "/auth/google/callback", "/auth/session",
                                      "/auth/logout"}
    assert app.state.v2_profile_login is None


def _client(monkeypatch) -> TestClient:
    _env(monkeypatch)
    return TestClient(create_app())


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data; boundary=x"])
def test_select_refuses_anything_but_json(monkeypatch, ctype) -> None:
    client = _client(monkeypatch)
    headers = {"content-type": ctype} if ctype else {}
    response = client.post("/auth/profile/select", content=b'{"profile_id":"x","pin":"482913"}', headers=headers)
    assert response.status_code == 415
    assert "482913" not in response.text


def test_select_and_clear_refuse_a_cross_site_request(monkeypatch) -> None:
    client = _client(monkeypatch)
    for path in ("/auth/profile/select", "/auth/profile/clear"):
        response = client.post(path, json={"profile_id": "x", "pin": "482913"},
                               headers={"sec-fetch-site": "cross-site"})
        assert response.status_code == 403
        assert response.json() == {"detail": "cross_site_request"}


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"profile_id": "x"}',
                                  b'{"profile_id": "x", "pin": "482913", "role": "admin"}',
                                  b'{"profile_id": 7, "pin": "482913"}',
                                  b'{"profile_id": "x", "pin": 482913}',
                                  b'{"profile_id": "x", "pin": "' + b"4" * 2000 + b'"}'])
def test_a_malformed_selection_is_refused_without_echoing_it(monkeypatch, body) -> None:
    client = _client(monkeypatch)
    response = client.post("/auth/profile/select", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_request"}
    assert "482913" not in response.text and "4444" not in response.text


def test_selection_without_a_principal_session_is_refused(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post("/auth/profile/select", json={"profile_id": CARLA, "pin": PIN})
    assert response.status_code == 401
    assert client.get("/auth/profiles").status_code == 401
    assert client.post("/auth/profile/clear", json={}).status_code == 401


def test_an_operator_id_header_never_selects_anyone(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = {"X-OriginLab-Operator-Email": "admin@origenlab.cl",
               "X-OriginLab-Operator-Id": CARLA, "X-OriginLab-Profile-Id": CARLA}
    assert client.get("/auth/session", headers=headers).status_code == 401
    assert client.get("/v2/contacts", headers=headers).status_code == 401
