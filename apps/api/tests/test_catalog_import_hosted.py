"""Catalog importers in hosted mode (`--hosted-target`). Every name and number is invented.

The refusals need no database. The full cycle runs against a disposable database posing as hosted,
the way PR #623's e2e does: the TEST-ONLY local scratch target logs in as the superuser and each side
immediately assumes the hosted identity — `origenlab_api` for the catalog rows, `origenlab_migrator`
(SET-only membership in origenlab_owner, as on hosted) for the manifest and the rollback.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo

from origenlab_api.v2.catalog import importing
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn
from test_catalog_import_apply import _loser_plan, _maker, _owner, _report  # noqa: F401 - shared helpers

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS / "catalog"))
sys.path.insert(0, str(_SCRIPTS))
import _common  # noqa: E402
import hosted_data_load_io as io_  # noqa: E402
import import_price_lists as ipl  # noqa: E402

AUTH = ["--hosted-target", "--authorize-hosted-connection", "--authorize-supavisor-session-route"]


@pytest.fixture(autouse=True)
def _clean_globals():
    yield
    _common._SECRETS.clear()


def _argv(cmd: str, plan: Path, sha: str, out: Path, *extra: str) -> list[str]:
    return [cmd, "--plan", str(plan), "--plan-sha256", sha, "--out", str(out), *extra]


def _fake_plan(tmp_path: Path) -> tuple[Path, str]:
    return _loser_plan(tmp_path, "p", [("Osmometer", "ACME-1", "1.000,00")], _maker("Hosted"))


# ------------------------------------------------------------------ refusals (no database)

@pytest.mark.parametrize("missing", AUTH[1:])
def test_hosted_needs_both_authorisations(tmp_path, missing, capsys) -> None:
    plan, sha = _fake_plan(tmp_path)
    flags = [f for f in AUTH if f != missing]
    assert ipl.main(_argv("verify", plan, sha, tmp_path / "o", *flags)) == _common.EXIT_REFUSED
    assert "needs --authorize-hosted-connection and" in capsys.readouterr().err


def test_authorisations_without_hosted_target_are_refused(tmp_path, capsys) -> None:
    plan, sha = _fake_plan(tmp_path)
    argv = _argv("verify", plan, sha, tmp_path / "o", "--target-dsn", "postgresql://u@127.0.0.1:1/x",
                 "--authorize-hosted-connection")
    assert ipl.main(argv) == _common.EXIT_REFUSED
    assert "only go with --hosted-target" in capsys.readouterr().err


def test_hosted_refuses_a_dsn_alongside(tmp_path, capsys) -> None:
    plan, sha = _fake_plan(tmp_path)
    argv = _argv("verify", plan, sha, tmp_path / "o", *AUTH, "--target-dsn", "postgresql://u@127.0.0.1:1/x")
    assert ipl.main(argv) == _common.EXIT_REFUSED
    assert "takes no --target-dsn" in capsys.readouterr().err


def test_missing_dsn_without_hosted_is_refused(tmp_path, capsys) -> None:
    plan, sha = _fake_plan(tmp_path)
    assert ipl.main(_argv("verify", plan, sha, tmp_path / "o")) == _common.EXIT_REFUSED
    assert "--target-dsn is required" in capsys.readouterr().err


def test_hosted_needs_the_runtime_password(tmp_path, monkeypatch, capsys) -> None:
    target = io_.Target(mode="hosted", host="aws-0-sa-east-1.pooler.supabase.com", hostaddr="203.0.113.7",
                        port=5432, user="origenlab_migrator.abcdefghijklmnopqrst", database="postgres",
                        sslmode="verify-full", sslrootcert="/nonexistent/ca.crt", password="migrator-secret",
                        project_ref="abcdefghijklmnopqrst", route="supavisor-session")
    monkeypatch.setattr(io_, "hosted_target", lambda *a, **k: target)
    monkeypatch.delenv(_common.RUNTIME_PASSWORD_ENV, raising=False)
    plan, sha = _fake_plan(tmp_path)
    assert ipl.main(_argv("verify", plan, sha, tmp_path / "o", *AUTH)) == _common.EXIT_REFUSED
    assert f"{_common.RUNTIME_PASSWORD_ENV} is not set" in capsys.readouterr().err


def test_hosted_runtime_side_swaps_only_the_login(monkeypatch) -> None:
    target = io_.Target(mode="hosted", host="aws-0-sa-east-1.pooler.supabase.com", hostaddr="203.0.113.7",
                        port=5432, user="origenlab_migrator.abcdefghijklmnopqrst", database="postgres",
                        sslmode="verify-full", sslrootcert="/ca.crt", password="migrator-secret",
                        project_ref="abcdefghijklmnopqrst", route="supavisor-session")
    monkeypatch.setattr(io_, "hosted_target", lambda *a, **k: target)
    ns = _common.argparse.Namespace(authorize_hosted_connection=True, authorize_supavisor_session_route=True,
                                    target_dsn=None, admin_dsn=None, allow_cleanroom_production=False,
                                    target_file="x")
    _common.resolve_hosted(ns, environ={_common.RUNTIME_PASSWORD_ENV: "api-secret"})
    from psycopg.conninfo import conninfo_to_dict

    t, a = conninfo_to_dict(ns.target_dsn), conninfo_to_dict(ns.admin_dsn)
    assert t["user"] == "origenlab_api.abcdefghijklmnopqrst" and t["password"] == "api-secret"
    assert a["user"] == target.user and a["password"] == "migrator-secret"
    for k in ("host", "hostaddr", "port", "dbname", "sslmode", "sslrootcert"):
        assert t[k] == a[k]
    assert t["sslmode"] == "verify-full"
    line = _common.redact(f"failed for {ns.target_dsn} at 203.0.113.7 with api-secret")
    for secret in ("api-secret", "migrator-secret", "abcdefghijklmnopqrst", "203.0.113.7", "pooler.supabase.com"):
        assert secret not in line


def test_hosted_refuses_a_database_other_than_postgres(monkeypatch) -> None:
    target = io_.Target(mode="hosted", host="h", hostaddr=None, port=5432, user="u", database="other",
                        sslmode="verify-full", sslrootcert="/ca.crt", password="p", project_ref="r")
    monkeypatch.setattr(io_, "hosted_target", lambda *a, **k: target)
    ns = _common.argparse.Namespace(authorize_hosted_connection=True, authorize_supavisor_session_route=True,
                                    target_dsn=None, admin_dsn=None, target_file="x")
    with pytest.raises(_common.Refused, match="'postgres' database"):
        _common.resolve_hosted(ns, environ={_common.RUNTIME_PASSWORD_ENV: "x"})


# ------------------------------------------------------------------ full cycle, posing as hosted

@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@pytest.fixture()
def posing_as_hosted(disposable_database, monkeypatch):
    """A resolved hosted target whose two logins land on the disposable database: the migrator side
    on the maintenance login, the `origenlab_api.<ref>` side on the real `origenlab_api` login."""
    target = io_.Target(mode="hosted", host="aws-0-sa-east-1.pooler.supabase.com", hostaddr="203.0.113.7",
                        port=5432, user="origenlab_migrator.abcdefghijklmnopqrst", database="postgres",
                        sslmode="verify-full", sslrootcert="/ca.crt", password="migrator-secret",
                        project_ref="abcdefghijklmnopqrst", route="supavisor-session")
    monkeypatch.setattr(io_, "hosted_target", lambda *a, **k: target)
    monkeypatch.setenv(_common.RUNTIME_PASSWORD_ENV, "api-secret")
    by_user = {"origenlab_api.abcdefghijklmnopqrst": runtime_dsn(disposable_database),
               target.user: disposable_database}
    monkeypatch.setattr(io_, "conninfo_for", lambda t: make_conninfo(by_user[t.user]))


@needs_db
def test_hosted_full_cycle(disposable_database, posing_as_hosted, tmp_path) -> None:
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    _owner(disposable_database, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                                "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active')", (email,))
    plan, sha = _loser_plan(tmp_path, "a", [("Osmometer", "ACME-1", "1.000,00"), ("Printer", "ACME-2", "300,50")],
                            _maker("Hosted"))
    assert ipl.main(_argv("apply", plan, sha, tmp_path / "apply", *AUTH, "--operator-email", email)) == 0
    report = _report(tmp_path / "apply", "apply-report.json")
    assert report["mode"] == "hosted" and report["inserted"] == 5
    assert ipl.main(_argv("verify", plan, sha, tmp_path / "verify", *AUTH)) == 0
    assert _report(tmp_path / "verify", "verify-report.json")["counts"] == {"present": 5}
    assert ipl.main(_argv("rollback", plan, sha, tmp_path / "rb", *AUTH, "--confirm-delete-loaded-rows")) == 0
    assert _owner(disposable_database, "select count(*) from evidence.source_record where dedupe_key = %s",
                  (importing.manifest_dedupe_key(sha),)) == [(0,)]


@needs_db
def test_hosted_refuses_the_migrator_as_runtime(disposable_database, posing_as_hosted, monkeypatch, tmp_path,
                                                capsys) -> None:
    monkeypatch.setattr(io_, "conninfo_for", lambda t: make_conninfo(disposable_database))
    plan, sha = _fake_plan(tmp_path)
    assert ipl.main(_argv("apply", plan, sha, tmp_path / "o", *AUTH, "--operator-email", "x@example.test")) \
        in (_common.EXIT_REFUSED,)
    assert "abcdefghijklmnopqrst" not in capsys.readouterr().err
