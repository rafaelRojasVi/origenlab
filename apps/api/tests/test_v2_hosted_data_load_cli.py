from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_TESTS = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_cli():
    sys.path.insert(0, str(_SCRIPTS))
    try:
        return _load("hosted_data_load", _SCRIPTS / "hosted_data_load.py")
    finally:
        sys.path.remove(str(_SCRIPTS))


PLAN_TESTS = _load("_hosted_plan_tests", _TESTS / "test_v2_hosted_data_load_plan.py")
AUTH = ["--authorize-hosted-connection", "--authorize-supavisor-session-route"]


def test_plan_requires_both_authorisations(capsys):
    cli = load_cli()
    with pytest.raises(SystemExit):
        cli.main(["plan", "--out", "/tmp/x"])
    with pytest.raises(SystemExit):
        cli.main(["plan", "--out", "/tmp/x", "--authorize-hosted-connection"])


def test_apply_requires_plan_and_sha(capsys):
    cli = load_cli()
    with pytest.raises(SystemExit):
        cli.main(["apply", *AUTH])


def test_rollback_requires_confirm_truncate():
    cli = load_cli()
    with pytest.raises(SystemExit):
        cli.main(["rollback", "--plan", "/tmp/p.json", "--plan-sha256", "a" * 64, *AUTH])


def test_exit_codes_are_the_documented_ones():
    cli = load_cli()
    assert (cli.EXIT_REFUSED, cli.EXIT_APPLY_FAILED, cli.EXIT_VERIFY_MISMATCH) == (11, 12, 13)


def test_hosted_operator_must_be_a_uuid():
    cli = load_cli()
    with pytest.raises(SystemExit):
        cli.main(["plan", "--out", "/tmp/x", "--hosted-operator", "not-a-uuid", *AUTH])


class _Conn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_plan_env(cli, monkeypatch, *, refuse: bool):
    conns = (_Conn(), _Conn())
    target = SimpleNamespace(mode="hosted", password="pw", user="u", project_ref="ref", host="h", hostaddr="1.2.3.4")
    monkeypatch.setattr(cli, "connect_both", lambda args: (*conns, target))
    monkeypatch.setattr(cli, "_facts", lambda *a, **k: (PLAN_TESTS.good_source(), PLAN_TESTS.good_target(), PLAN_TESTS.good_host()))
    monkeypatch.setattr(cli.io_, "pg_dump_target", lambda *a, **k: None)
    if refuse:
        monkeypatch.setattr(cli.hp, "evaluate", lambda **k: [{"check": "x", "ok": False, "detail": "ref h pw"}])
    return conns


def test_plan_refused_writes_no_file_and_exits_11(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    conns = _patch_plan_env(cli, monkeypatch, refuse=True)
    rc = cli.main(["plan", "--out", str(tmp_path), *AUTH])
    out = capsys.readouterr().out
    assert rc == 11
    assert list(tmp_path.glob("plan-*.json")) == []
    assert "no plan written" in out
    assert "pw" not in out and "1.2.3.4" not in out
    assert all(c.closed for c in conns)


def test_plan_clean_writes_one_0600_file(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _patch_plan_env(cli, monkeypatch, refuse=False)
    rc = cli.main(["plan", "--out", str(tmp_path), *AUTH])
    files = list(tmp_path.glob("plan-*.json"))
    assert rc == 0 and len(files) == 1
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    plan, sha = cli.load_plan(files[0])
    assert sha in capsys.readouterr().out
    assert len(plan["tables"]) == 16


def test_connect_both_closes_source_when_target_fails(monkeypatch):
    cli = load_cli()
    src = _Conn()
    monkeypatch.setattr(cli.io_, "hosted_target", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cli.io_, "source_conninfo", lambda: "s")
    monkeypatch.setattr(cli.io_, "conninfo_for", lambda t: "t")

    def fake_connect(info, **k):
        if info == "s":
            return src
        raise RuntimeError("boom")

    monkeypatch.setattr(cli.psycopg, "connect", fake_connect)
    with pytest.raises(RuntimeError):
        cli.connect_both(SimpleNamespace(target_file="x"))
    assert src.closed
