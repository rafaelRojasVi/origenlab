"""The database target rules (pure) and the connection probe (against a disposable database)."""

from __future__ import annotations

import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from origenlab_worker.database import (
    WORKER_ROLE,
    TargetRefused,
    WorkerTarget,
    verify_worker_connection,
    local_test_target,
    open_worker_db,
    remote_worker_target,
    write_ca_file,
)
from v2_command_harness import build_disposable_database, needs_worker_db, runtime_dsn, worker_dsn

HOST = "pooler.example.invalid"
REF = "".join(["abcdefghij", "klmnopqrst"])  # a fake 20-letter project ref, not a literal the hygiene check flags
LOGIN = f"origenlab_worker.{REF}"
URL = f"postgresql://{LOGIN}:pw@{HOST}:5432/postgres"
FAKE_CA = "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
CA = write_ca_file(FAKE_CA)  # target validation needs an existing absolute file; removed at exit


def test_the_session_route_as_the_worker_is_accepted_with_verify_full() -> None:
    target = remote_worker_target(URL, expected_host=HOST, ca_file=CA)
    assert (target.database, target.remote) == ("postgres", True)
    assert target.connect_options["sslmode"] == "verify-full" and target.connect_options["sslrootcert"] == CA
    assert remote_worker_target(URL.replace(LOGIN, "origenlab_worker"),
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
    (URL.replace(":5432", ":5433"), "database_port_invalid"),
    (URL.replace(":5432", ""), "database_port_invalid"),  # libpq would fall back to PGPORT
    (f"postgresql://x@origenlab_worker:pw@{HOST}:5432/postgres", "database_url_multiple_at"),
    (URL.replace("origenlab_worker.", "origenlab_api."), "login_not_worker"),
    (URL.replace(LOGIN, "postgres"), "login_not_worker"),
    (URL.replace("/postgres", "/"), "database_name_invalid"),
    (URL.replace("/postgres", "/template1"), "database_name_invalid"),
])
def test_every_unsafe_target_is_refused_by_code(url, code) -> None:
    with pytest.raises(TargetRefused) as exc:
        remote_worker_target(url, expected_host=HOST, ca_file=CA)
    assert exc.value.code == code


def test_the_password_never_reaches_a_repr() -> None:
    target = remote_worker_target(URL.replace(":pw@", ":hunter2@"), expected_host=HOST, ca_file=CA)
    assert "hunter2" not in repr(target) and "hunter2" not in str(target)
    assert "hunter2" not in repr(local_test_target("postgresql://origenlab_worker:hunter2@127.0.0.1:5432/origenlab_test_0a1b2c3d"))


@pytest.mark.parametrize("ca", ["", "system", "relative/ca.pem", "/nonexistent/ca.pem"])
def test_the_ca_file_must_be_an_existing_absolute_path(ca) -> None:
    with pytest.raises(TargetRefused) as exc:
        remote_worker_target(URL, expected_host=HOST, ca_file=ca)
    assert exc.value.code == "ca_file_invalid"


def test_the_expected_host_must_be_a_dns_name() -> None:
    with pytest.raises(TargetRefused) as exc:
        remote_worker_target(URL, expected_host="10.0.0.5", ca_file=CA)
    assert exc.value.code == "expected_host_invalid"


def test_the_ca_is_written_privately_and_a_private_key_is_refused() -> None:
    path = write_ca_file(FAKE_CA)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert "BEGIN CERTIFICATE" in Path(path).read_text()
    for bad, code in (("", "ca_pem_missing"), ("not a pem", "ca_pem_missing"),
                      (FAKE_CA.replace("ZmFr", "ZmF\u00e9"), "ca_pem_not_ascii"),
                      (FAKE_CA + "-----BEGIN PRIVATE KEY-----\n", "ca_pem_has_private_key")):
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


def test_a_test_target_refuses_a_query_string() -> None:
    with pytest.raises(TargetRefused) as exc:
        local_test_target("postgresql://origenlab_worker:pw@127.0.0.1:5432/origenlab_test_0a1b2c3d?sslmode=disable")
    assert exc.value.code == "database_url_has_options"


# ------------------------------------------------------------------ the probe against a fake session


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return self._rows


class _Conn:
    def __init__(self, *, tls=True, sslmode="verify-full", role_row=None):
        self.pgconn = type("P", (), {"ssl_in_use": tls})()
        self.info = type("I", (), {"get_parameters": lambda _s: {"sslmode": sslmode}})()
        self._row = role_row

    def transaction(self):
        return _Cur(None)

    def cursor(self):
        return _Cur(self._row)


REMOTE = WorkerTarget(dsn="x", database="postgres", remote=True)


@pytest.mark.parametrize("conn,code", [
    (_Conn(tls=False), "connection_not_tls"),
    (_Conn(sslmode="require"), "connection_not_verify_full"),
    (_Conn(role_row=(WORKER_ROLE, "postgres", False, True, False, False, False, [])), "worker_role_elevated"),
    (_Conn(role_row=(WORKER_ROLE, "postgres", False, False, False, False, False, ["some_role"])),
     "worker_role_member_of_privileged_role"),
])
def test_the_probe_refuses_by_code_on_a_fake_session(conn, code) -> None:
    with pytest.raises(TargetRefused) as exc:
        verify_worker_connection(conn, REMOTE)
    assert exc.value.code == code


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


# ------------------------------------------------------------------ drift the probe must catch


@contextmanager
def _owner(disposable_database):
    """The maintenance login acting as the schema owner, on the disposable database only."""
    import psycopg

    from v2_command_harness import _TEST_DSN, swap_database

    with psycopg.connect(swap_database(_TEST_DSN, disposable_database.rpartition("/")[2]), autocommit=True) as conn:
        conn.execute("set role origenlab_owner")
        yield conn


def _refused_as(disposable_database) -> str:
    with pytest.raises(TargetRefused) as exc:
        with open_worker_db(local_test_target(worker_dsn(disposable_database))):
            pass
    return exc.value.code


@pytest.fixture(scope="module")
def scratch_database():
    yield from build_disposable_database()


@needs_worker_db
def test_a_set_only_membership_in_any_role_is_refused(scratch_database) -> None:
    import psycopg

    from v2_command_harness import _TEST_DSN, swap_database

    role = f"ol_t_{uuid.uuid4().hex[:8]}"
    dbname = scratch_database.rpartition("/")[2]
    with psycopg.connect(swap_database(_TEST_DSN, dbname), autocommit=True) as conn:
        conn.execute(f"create role {role} nologin")
        try:
            conn.execute("set role origenlab_owner")
            conn.execute(f"grant insert on crm.organization to {role}")
            conn.execute("reset role")
            conn.execute(f"grant {role} to origenlab_worker with inherit false, set true")
            assert _refused_as(scratch_database) == "worker_role_member_of_privileged_role"
        finally:
            conn.execute(f"revoke {role} from origenlab_worker")
            conn.execute("set role origenlab_owner")
            conn.execute(f"revoke insert on crm.organization from {role}")
            conn.execute("reset role")
            conn.execute(f"drop role {role}")


@needs_worker_db
@pytest.mark.parametrize("setup,teardown", [
    ("grant trigger on crm.organization to origenlab_worker", "revoke trigger on crm.organization from origenlab_worker"),
    ("create view crm.ol_t_view as select 1 as a; grant insert on crm.ol_t_view to origenlab_worker",
     "drop view crm.ol_t_view"),
    ("create materialized view crm.ol_t_mv as select 1 as a; grant update on crm.ol_t_mv to origenlab_worker",
     "drop materialized view crm.ol_t_mv"),
])
def test_trigger_view_and_matview_write_privileges_are_refused(scratch_database, setup, teardown) -> None:
    with _owner(scratch_database) as conn:
        conn.execute(setup)
        try:
            assert _refused_as(scratch_database) == "worker_can_write_crm_or_outbound"
        finally:
            conn.execute(teardown)


@needs_worker_db
def test_execute_on_a_security_definer_function_is_refused(scratch_database) -> None:
    with _owner(scratch_database) as conn:
        conn.execute("create function crm.ol_t_definer() returns int language sql security definer as 'select 1'")
        conn.execute("grant execute on function crm.ol_t_definer() to origenlab_worker")
        try:
            assert _refused_as(scratch_database) == "worker_can_execute_security_definer"
        finally:
            conn.execute("drop function crm.ol_t_definer()")


@needs_worker_db
def test_a_definer_function_in_a_schema_without_usage_is_not_refused(scratch_database) -> None:
    with _owner(scratch_database) as conn:
        conn.execute("reset role")  # the owner cannot create schemas; the maintenance login can
        conn.execute("create schema ol_t_hidden")
        try:
            conn.execute("revoke all on schema ol_t_hidden from public")
            conn.execute("create function ol_t_hidden.f() returns int language sql security definer as 'select 1'")
            conn.execute("grant execute on function ol_t_hidden.f() to origenlab_worker")
            with open_worker_db(local_test_target(worker_dsn(scratch_database))):
                pass
        finally:
            conn.execute("drop schema ol_t_hidden cascade")


@needs_worker_db
def test_an_extension_owned_definer_function_is_skipped(scratch_database) -> None:
    from origenlab_worker.database import _DEFINER_PROBE

    with _owner(scratch_database) as conn:
        owned = conn.execute(
            "select n.nspname || '.' || p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "join pg_depend d on d.classid = 'pg_proc'::regclass and d.objid = p.oid and d.deptype = 'e' "
            "where p.prosecdef and has_function_privilege('origenlab_worker', p.oid, 'EXECUTE') "
            "and n.nspname not in ('pg_catalog', 'information_schema')"
        ).fetchall()
    if not owned:
        pytest.skip("this cluster has no extension-owned SECURITY DEFINER function the worker can execute")
    with open_worker_db(local_test_target(worker_dsn(scratch_database))) as db:
        found = {r[0] for r in db.connection.execute(_DEFINER_PROBE).fetchall()}
    assert not found & {r[0] for r in owned}


@needs_worker_db
def test_a_missing_grant_and_a_missing_policy_are_refused_and_restored(scratch_database) -> None:
    with _owner(scratch_database) as conn:
        conn.execute("revoke select on comms.mailbox from origenlab_worker")
        try:
            assert _refused_as(scratch_database) == "worker_grant_missing"
        finally:
            conn.execute("grant select on comms.mailbox to origenlab_worker")
        conn.execute("alter policy origenlab_worker_select on comms.mailbox to origenlab_api")
        try:
            assert _refused_as(scratch_database) == "worker_policy_missing"
        finally:
            conn.execute("alter policy origenlab_worker_select on comms.mailbox to origenlab_worker")
    with open_worker_db(local_test_target(worker_dsn(scratch_database))):
        pass


@needs_worker_db
def test_a_table_created_after_connect_is_caught_on_the_next_probe(scratch_database) -> None:
    target = local_test_target(worker_dsn(scratch_database))
    with open_worker_db(target) as db, _owner(scratch_database) as conn:
        conn.execute("create table crm.ol_t_late (id int)")
        conn.execute("grant insert on crm.ol_t_late to origenlab_worker")
        try:
            with pytest.raises(TargetRefused) as exc:
                verify_worker_connection(db.connection, target)
            assert exc.value.code == "worker_can_write_crm_or_outbound"
        finally:
            conn.execute("drop table crm.ol_t_late")
