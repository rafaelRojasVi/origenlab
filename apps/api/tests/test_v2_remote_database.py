"""The opt-in remote V2 target: one named host, verify-full TLS, the runtime role only.

Validation tests open nothing. The TLS proofs at the bottom run only against a disposable,
throwaway PostgreSQL that serves a certificate from a throwaway CA — never a real database —
and skip unless `ORIGENLAB_V2_TLS_TEST_*` names one. Every address here is fictitious.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from origenlab_api.settings import Settings, V2TargetRefused
from origenlab_api.v2.identity import IdentityMisconfigured, LocalDevIdentity
from origenlab_api.v2.remote_database import (
    RemoteTargetRefused,
    V2DatabaseTarget,
    validate_remote_target,
    verify_runtime_connection,
)

HOST = "db.example-v2.test"
DSN = f"postgresql://origenlab_api:pw@{HOST}:5432/origenlab"
CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


@pytest.fixture
def ca(tmp_path: Path) -> str:
    path = tmp_path / "ca.pem"
    path.write_text(CA_PEM, encoding="ascii")
    return str(path)


def test_a_complete_remote_target_binds_verify_full(ca) -> None:
    target = validate_remote_target(DSN, expected_host=HOST.upper() + ".", ca_file=ca)
    assert target.remote and target.dsn == DSN and target.database == "origenlab"
    assert target.connect_options["sslmode"] == "verify-full"
    assert target.connect_options["sslrootcert"] == ca

    seen = {}

    def fake_connect(dsn, **kwargs):
        seen.update(kwargs, dsn=dsn)

    target.connect_factory(fake_connect)(DSN)
    assert seen["sslmode"] == "verify-full" and seen["sslrootcert"] == ca and seen["dsn"] == DSN


@pytest.mark.parametrize(
    "dsn, reason",
    [
        (f"postgresql://origenlab_api:pw@other.example-v2.test:5432/origenlab", "does not name"),
        (f"postgresql://origenlab_api:pw@{HOST}.evil.test:5432/origenlab", "does not name"),
        (f"{DSN}?sslmode=disable", "no query string"),
        (f"{DSN}?host=elsewhere.test", "no query string"),
        (f"{DSN}#x", "no fragment"),
        (f"postgresql://origenlab_api:pw@{HOST},other.test/origenlab", "exactly one host"),
        ("postgresql://origenlab_api:pw@203.0.113.7:5432/origenlab", "host name, not an IP"),
        ("postgresql://origenlab_api:pw@127.0.0.1:5432/origenlab", "host name, not an IP"),
        (f"postgresql://postgres:pw@{HOST}:5432/origenlab", "runtime role"),
        (f"postgresql://origenlab_migrator:pw@{HOST}:5432/origenlab", "runtime role"),
        (f"postgresql://origenlab_worker:pw@{HOST}:5432/origenlab", "runtime role"),
        (f"postgresql://origenlab_api_admin:pw@{HOST}:5432/origenlab", "runtime role"),
        (f"postgresql://{HOST}:5432/origenlab", "runtime role"),
        (f"postgresql://origenlab_api:pw@{HOST}:5432/", "exactly one database"),
        (f"postgresql://origenlab_api:pw@{HOST}:5432/template1", "template1"),
        (f"mysql://origenlab_api:pw@{HOST}/origenlab", "postgres://"),
        ("", "empty"),
    ],
)
def test_an_unsafe_remote_dsn_is_refused(dsn, reason, ca) -> None:
    with pytest.raises(RemoteTargetRefused, match=reason):
        validate_remote_target(dsn, expected_host=HOST, ca_file=ca)


def test_the_pooler_spelling_of_the_runtime_role_is_accepted(ca) -> None:
    dsn = f"postgresql://origenlab_api.abcdefghijklmnopqrst:pw@{HOST}:6543/postgres"
    assert validate_remote_target(dsn, expected_host=HOST, ca_file=ca).database == "postgres"


def test_an_expected_host_and_a_ca_file_are_required(ca, tmp_path: Path) -> None:
    with pytest.raises(RemoteTargetRefused, match="EXPECTED_HOST is required"):
        validate_remote_target(DSN, expected_host=None, ca_file=ca)
    with pytest.raises(RemoteTargetRefused, match="DNS host name"):
        validate_remote_target(DSN, expected_host="203.0.113.7", ca_file=ca)
    with pytest.raises(RemoteTargetRefused, match="SSLROOTCERT is required"):
        validate_remote_target(DSN, expected_host=HOST, ca_file=None)
    with pytest.raises(RemoteTargetRefused, match="absolute path"):
        validate_remote_target(DSN, expected_host=HOST, ca_file="ca.pem")
    with pytest.raises(RemoteTargetRefused, match="not a readable file"):
        validate_remote_target(DSN, expected_host=HOST, ca_file=str(tmp_path / "missing.pem"))
    empty = tmp_path / "empty.pem"
    empty.write_text("nothing here", encoding="ascii")
    with pytest.raises(RemoteTargetRefused, match="no PEM certificate"):
        validate_remote_target(DSN, expected_host=HOST, ca_file=str(empty))
    keyed = tmp_path / "keyed.pem"
    keyed.write_text(CA_PEM + "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n", encoding="ascii")
    with pytest.raises(RemoteTargetRefused, match="private key"):
        validate_remote_target(DSN, expected_host=HOST, ca_file=str(keyed))


# ------------------------------------------------------------------ settings wiring


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_settings_stay_loopback_only_unless_remote_is_opted_into(ca) -> None:
    with pytest.raises(V2TargetRefused, match="not a literal IP|is a name"):
        _settings(v2_database_url=DSN).v2_database_target()
    with pytest.raises(V2TargetRefused, match="REMOTE is not"):
        _settings(
            v2_database_url="postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_clean",
            v2_database_expected_host=HOST,
        ).v2_database_target()
    local = _settings(
        v2_database_url="postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_clean"
    ).v2_database_target()
    assert local.remote is False and local.connect_options == {}
    remote = _settings(
        v2_database_url=DSN,
        v2_database_remote=True,
        v2_database_expected_host=HOST,
        v2_database_sslrootcert=ca,
    ).v2_database_target()
    assert remote.remote and remote.connect_options["sslmode"] == "verify-full"


def test_the_remote_opt_in_does_not_admit_a_hosted_dsn_without_its_checks() -> None:
    with pytest.raises(RemoteTargetRefused, match="EXPECTED_HOST"):
        _settings(v2_database_url=DSN, v2_database_remote=True).v2_database_target()


def test_the_development_header_login_still_refuses_a_remote_database() -> None:
    with pytest.raises(IdentityMisconfigured, match="loopback"):
        LocalDevIdentity(DSN, lookup=object())


def test_the_import_tooling_still_refuses_a_remote_database() -> None:
    target = pytest.importorskip("origenlab_email_pipeline.migration.v2_import.target")
    with pytest.raises(target.TargetRefused):
        target.assert_local_target(DSN)


# ------------------------------------------------------------------ the startup probe (fake session)


class _Cursor:
    def __init__(self, row):
        self.row, self.sql = row, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row, *, ssl=True, sslmode="verify-full"):
        self.cursor_ = _Cursor(row)
        self.pgconn = type("P", (), {"ssl_in_use": ssl})()
        self.info = type("I", (), {"get_parameters": lambda _self: {"sslmode": sslmode}})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cursor_

    def rollback(self):
        pass


CLEAN = ("origenlab_api", "origenlab", False, False, False, False, False, [])
TARGET = V2DatabaseTarget(dsn=DSN, remote=True, database="origenlab",
                          connect_options={"sslmode": "verify-full", "sslrootcert": "/ca.pem"})


def _probe(conn: _Conn) -> None:
    verify_runtime_connection(TARGET, lambda dsn, **kw: conn)


def test_the_probe_accepts_the_restricted_runtime_role() -> None:
    conn = _Conn(CLEAN)
    _probe(conn)
    assert conn.cursor_.sql[0] == "set transaction read only"


@pytest.mark.parametrize(
    "row, reason",
    [
        (("postgres", *CLEAN[1:]), "not the runtime role"),
        (("origenlab_api", "other_db", *CLEAN[2:]), "different database"),
        ((*CLEAN[:2], True, *CLEAN[3:]), "SUPERUSER"),
        ((*CLEAN[:3], True, *CLEAN[4:]), "BYPASSRLS"),
        ((*CLEAN[:4], True, *CLEAN[5:]), "CREATEROLE"),
        ((*CLEAN[:7], ["origenlab_owner"]), "member of origenlab_owner"),
        (None, "not visible"),
    ],
)
def test_the_probe_refuses_anything_more_than_the_runtime_role(row, reason) -> None:
    with pytest.raises(RemoteTargetRefused, match=reason):
        _probe(_Conn(row))


def test_the_probe_refuses_a_connection_without_verified_tls() -> None:
    with pytest.raises(RemoteTargetRefused, match="not using TLS"):
        _probe(_Conn(CLEAN, ssl=False))
    with pytest.raises(RemoteTargetRefused, match="verify-full"):
        _probe(_Conn(CLEAN, sslmode="require"))


# ------------------------------------------------------------------ real TLS (disposable server)

_TLS = {k: os.environ.get(f"ORIGENLAB_V2_TLS_TEST_{k}", "").strip()
        for k in ("DSN", "HOSTADDR", "CA", "WRONG_CA", "SUPERUSER_DSN")}
needs_tls = pytest.mark.skipif(
    not all(_TLS.values()),
    reason="ORIGENLAB_V2_TLS_TEST_{DSN,HOSTADDR,CA,WRONG_CA,SUPERUSER_DSN} name a disposable TLS server",
)


def _tls_connect(hostaddr: str):
    """psycopg.connect, reaching the throwaway server by address while TLS checks the name."""
    import psycopg

    def connect(dsn, **kwargs):
        return psycopg.connect(dsn, hostaddr=hostaddr, **kwargs)

    return connect


def _tls_target(dsn: str, ca: str) -> V2DatabaseTarget:
    import urllib.parse as up

    return validate_remote_target(dsn, expected_host=up.urlsplit(dsn).hostname, ca_file=ca)


@needs_tls
def test_verified_tls_to_the_runtime_role_passes_the_probe() -> None:
    verify_runtime_connection(_tls_target(_TLS["DSN"], _TLS["CA"]), _tls_connect(_TLS["HOSTADDR"]))


@needs_tls
def test_a_certificate_from_another_ca_is_refused_before_any_query() -> None:
    import psycopg

    with pytest.raises(psycopg.OperationalError, match="certificate verify failed"):
        verify_runtime_connection(
            _tls_target(_TLS["DSN"], _TLS["WRONG_CA"]), _tls_connect(_TLS["HOSTADDR"])
        )


@needs_tls
def test_a_certificate_for_another_host_name_is_refused() -> None:
    import psycopg
    import urllib.parse as up

    parts = up.urlsplit(_TLS["DSN"])
    other = "wrong-name.example-v2.test"
    dsn = parts._replace(netloc=parts.netloc.replace(parts.hostname, other)).geturl()
    with pytest.raises(psycopg.OperationalError, match="does not match host name|server certificate"):
        verify_runtime_connection(_tls_target(dsn, _TLS["CA"]), _tls_connect(_TLS["HOSTADDR"]))


@needs_tls
def test_a_superuser_session_over_verified_tls_is_refused_by_the_probe() -> None:
    # The DSN validator already refuses a superuser login by name; this proves the probe
    # refuses one on its own, from inside the session.
    import urllib.parse as up

    dsn = _TLS["SUPERUSER_DSN"]
    target = V2DatabaseTarget(
        dsn=dsn, remote=True, database=up.urlsplit(dsn).path.lstrip("/"),
        connect_options={"sslmode": "verify-full", "sslrootcert": _TLS["CA"]},
    )
    with pytest.raises(RemoteTargetRefused, match="not the runtime role"):
        verify_runtime_connection(target, _tls_connect(_TLS["HOSTADDR"]))


# ------------------------------------------------------------------ startup wiring


def test_startup_probes_a_remote_target_and_binds_tls_for_every_repository(ca, monkeypatch) -> None:
    import psycopg
    from fastapi import FastAPI

    import origenlab_api.v2.remote_database as remote
    from origenlab_api import main

    settings = _settings(
        v2_database_url=DSN,
        v2_database_remote=True,
        v2_database_expected_host=HOST,
        v2_database_sslrootcert=ca,
        env="production",
        google_auth_enabled=True,
        google_client_id="1234567890-test.apps.googleusercontent.com",
        google_client_secret="test-client-secret",
        auth_public_base_url="https://dashboard.origenlab.cl/api",
        auth_session_secret="s" * 48,
    )
    probed = []
    monkeypatch.setattr(remote, "verify_runtime_connection", lambda t, c: probed.append((t, c)))
    app = FastAPI()
    main._mount_v2_read_boundary(app, settings)
    assert len(probed) == 1 and probed[0][1] is psycopg.connect
    connect = app.state.v2_repository._connect
    assert connect.keywords["sslmode"] == "verify-full" and connect.keywords["sslrootcert"] == ca

    def refuse(t, c):
        raise RemoteTargetRefused("the runtime role holds SUPERUSER")

    monkeypatch.setattr(remote, "verify_runtime_connection", refuse)
    with pytest.raises(RemoteTargetRefused):
        main._mount_v2_read_boundary(FastAPI(), settings)
