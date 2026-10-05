"""The database target rules (pure) and the connection probe (against a disposable database)."""

from __future__ import annotations

import os
import stat

import pytest

from origenlab_worker.database import (
    TargetRefused,
    local_test_target,
    open_worker_db,
    remote_worker_target,
    write_ca_file,
)
from v2_command_harness import build_disposable_database, needs_worker_db, runtime_dsn, worker_dsn

HOST = "pooler.example.invalid"
URL = f"postgresql://origenlab_worker.abcdefghijklmnopqrst:pw@{HOST}:5432/postgres"
CA = "/run/secrets/ca.pem"  # a path; target validation never opens it
FAKE_CA = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"


def test_the_session_route_as_the_worker_is_accepted_with_verify_full() -> None:
    target = remote_worker_target(URL, expected_host=HOST, ca_file=CA)
    assert (target.database, target.remote) == ("postgres", True)
    assert target.connect_options["sslmode"] == "verify-full" and target.connect_options["sslrootcert"] == CA
    assert remote_worker_target(URL.replace("origenlab_worker.abcdefghijklmnopqrst", "origenlab_worker"),
                                expected_host=HOST, ca_file=CA).remote


@pytest.mark.parametrize("url,code", [
    ("", "database_url_missing"),
    ("mysql://origenlab_worker:pw@pooler.example.invalid/postgres", "database_url_scheme"),
    (URL + "?sslmode=disable", "database_url_has_options"),
    (URL + "#x", "database_url_has_options"),
    (f"postgresql://origenlab_worker:pw@{HOST},other.example.invalid/postgres", "database_url_multiple_hosts"),
    ("postgresql://origenlab_worker:pw@10.0.0.5:5432/postgres", "database_host_is_ip"),
    ("postgresql://origenlab_worker:pw@other.example.invalid:5432/postgres", "database_host_not_expected"),
    (URL.replace(":5432", ":6543"), "transaction_pooler_refused"),
    (URL.replace("origenlab_worker.", "origenlab_api."), "login_not_worker"),
    (URL.replace("origenlab_worker.abcdefghijklmnopqrst", "postgres"), "login_not_worker"),
    (URL.replace("/postgres", "/"), "database_name_invalid"),
    (URL.replace("/postgres", "/template1"), "database_name_invalid"),
])
def test_every_unsafe_target_is_refused_by_code(url, code) -> None:
    with pytest.raises(TargetRefused) as exc:
        remote_worker_target(url, expected_host=HOST, ca_file=CA)
    assert exc.value.code == code


def test_the_expected_host_must_be_a_dns_name() -> None:
    with pytest.raises(TargetRefused) as exc:
        remote_worker_target(URL, expected_host="10.0.0.5", ca_file=CA)
    assert exc.value.code == "expected_host_invalid"


def test_the_ca_is_written_privately_and_a_private_key_is_refused() -> None:
    path = write_ca_file(FAKE_CA)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and "BEGIN CERTIFICATE" in open(path).read()
    for bad, code in (("", "ca_pem_missing"), (FAKE_CA + "-----BEGIN PRIVATE KEY-----\n", "ca_pem_has_private_key")):
        with pytest.raises(TargetRefused) as exc:
            write_ca_file(bad)
        assert exc.value.code == code


@pytest.mark.parametrize("dsn,code", [
    ("postgresql://origenlab_worker:pw@db.example.invalid:5432/origenlab_test_0a1b2c3d", "not_loopback"),
    ("postgresql://origenlab_worker:pw@127.0.0.1:5432/origenlab_clean", "not_a_disposable_test_database"),
    ("postgresql://origenlab_worker:pw@127.0.0.1:5432/postgres", "not_a_disposable_test_database"),
])
def test_a_test_target_is_only_ever_a_disposable_loopback_database(dsn, code) -> None:
    with pytest.raises(TargetRefused) as exc:
        local_test_target(dsn)
    assert exc.value.code == code
    assert local_test_target("postgresql://origenlab_worker:pw@127.0.0.1:5432/origenlab_test_0a1b2c3d").remote is False


# ------------------------------------------------------------------ against a disposable database


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@needs_worker_db
def test_the_worker_login_proves_it_is_the_worker(disposable_database) -> None:
    with open_worker_db(local_test_target(worker_dsn(disposable_database))) as db, db.connection.cursor() as cur:
        cur.execute("select current_user, current_setting('application_name')")
        assert cur.fetchone() == ("origenlab_worker", "origenlab-worker-gmail-sync")


@needs_worker_db
def test_the_api_login_is_refused_as_not_the_worker(disposable_database) -> None:
    with pytest.raises(TargetRefused) as exc:
        with open_worker_db(local_test_target(runtime_dsn(disposable_database))):
            pass
    assert exc.value.code == "not_worker_role"


@needs_worker_db
def test_the_only_write_the_worker_holds_in_crm_or_outbound_is_the_reply_proposal(disposable_database) -> None:
    import psycopg

    from origenlab_worker.database import ALLOWED_OUTSIDE_WRITES, _WRITE_PROBE

    assert ALLOWED_OUTSIDE_WRITES == {("outbound.campaign_reply", "INSERT")}
    with open_worker_db(local_test_target(worker_dsn(disposable_database))) as db:
        assert set(db.connection.execute(_WRITE_PROBE).fetchall()) == ALLOWED_OUTSIDE_WRITES
        for sql in ("insert into crm.organization (kind, name) values ('unknown', 'x')",
                    "update outbound.send_control set marketing_enabled = true"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.connection.transaction():
                db.connection.execute(sql)


@needs_worker_db
@pytest.mark.parametrize("grant,table", [
    ("update on outbound.campaign_reply", "outbound.campaign_reply"),
    ("delete on outbound.campaign_reply", "outbound.campaign_reply"),
    ("insert on crm.organization", "crm.organization"),
    ("update (name) on crm.organization", "crm.organization"),
    ("insert (id) on crm.person", "crm.person"),
    ("truncate on outbound.contact_control", "outbound.contact_control"),
])
def test_any_other_write_privilege_is_refused(disposable_database, grant, table) -> None:
    """Grant it inside a throwaway database as the maintenance login; the probe must refuse."""
    import psycopg

    from v2_command_harness import _TEST_DSN, swap_database

    admin = swap_database(_TEST_DSN, disposable_database.rpartition("/")[2])
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        conn.execute(f"grant {grant} to origenlab_worker")
        try:
            with pytest.raises(TargetRefused) as exc:
                with open_worker_db(local_test_target(worker_dsn(disposable_database))):
                    pass
            assert exc.value.code == "worker_can_write_crm_or_outbound"
        finally:
            conn.execute(f"revoke {grant} from origenlab_worker")
