from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg
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


def test_plan_requires_both_authorisations():
    cli = load_cli()
    with pytest.raises(SystemExit):
        cli.main(["plan", "--out", "/tmp/x"])
    with pytest.raises(SystemExit):
        cli.main(["plan", "--out", "/tmp/x", "--authorize-hosted-connection"])


def test_apply_requires_plan_and_sha():
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
    target = SimpleNamespace(mode="hosted", password="s3cretpw", user="loginuser", project_ref="projref123", host="db.example.test", hostaddr="10.9.8.7")
    monkeypatch.setattr(cli, "connect_both", lambda args: (*conns, target))
    monkeypatch.setattr(cli, "_facts", lambda *a, **k: (PLAN_TESTS.good_source(), PLAN_TESTS.good_target(), PLAN_TESTS.good_host()))
    monkeypatch.setattr(cli.io_, "pg_dump_target", lambda *a, **k: None)
    if refuse:
        monkeypatch.setattr(cli.hp, "evaluate", lambda **k: [{"check": "x", "ok": False, "detail": "projref123 db.example.test s3cretpw"}])
    return conns


def test_plan_refused_writes_no_file_and_exits_11(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    conns = _patch_plan_env(cli, monkeypatch, refuse=True)
    rc = cli.main(["plan", "--out", str(tmp_path), *AUTH])
    out = capsys.readouterr().out
    assert rc == 11
    assert list(tmp_path.glob("plan-*.json")) == []
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []
    assert "no plan written" in out
    assert "s3cretpw" not in out and "10.9.8.7" not in out
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


@pytest.mark.parametrize("exc_type", [RuntimeError, OSError])
def test_probe_failure_cleans_up_redacts_and_refuses(tmp_path, monkeypatch, capsys, exc_type):
    cli = load_cli()
    conns = _patch_plan_env(cli, monkeypatch, refuse=False)
    # the probe outcome reaches the facts the way the real readers pass it
    monkeypatch.setattr(
        cli, "_facts",
        lambda args, src, tgt, target, op, probe: (
            PLAN_TESTS.good_source(), PLAN_TESTS.good_target(pg_dump_probe_ok=probe), PLAN_TESTS.good_host()))

    def failing_dump(target, out, *, schema_only=False):
        Path(out).write_text("partial")
        Path(str(out) + ".sha256").write_text("x")
        raise exc_type("pg_dump failed: s3cretpw loginuser projref123 10.9.8.7")

    monkeypatch.setattr(cli.io_, "pg_dump_target", failing_dump)
    rc = cli.main(["plan", "--out", str(tmp_path), *AUTH])
    cap = capsys.readouterr()
    assert rc == 11
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []
    for secret in ("s3cretpw", "loginuser", "projref123", "10.9.8.7"):
        assert secret not in cap.out + cap.err
    assert "***" in cap.out
    assert all(c.closed for c in conns)


class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    statusmessage = "COMMIT"

    def execute(self, sql, params=None):
        self.log.append(str(sql))


class _FakeConn:
    def __init__(self):
        self.log: list[str] = []
        self.closed = False
        self.info = SimpleNamespace(transaction_status=psycopg.pq.TransactionStatus.IDLE)

    def cursor(self):
        return _FakeCursor(self.log)

    def close(self):
        self.closed = True


def _apply_args(tmp_path):
    return SimpleNamespace(plan=tmp_path / "p.json", plan_sha256="a" * 64, out=str(tmp_path / "out"),
                           target_file="x", authorize_hosted_connection=True, authorize_supavisor_session_route=True)


def _patch_apply(cli, monkeypatch, refused: bool, dump):
    src, tgt = _FakeConn(), _FakeConn()
    target = SimpleNamespace(mode="hosted", password="s3cret", user="u", project_ref="r", host="h", hostaddr="1.1.1.1")
    plan = {"remap": {"to": "x", "from": ["a"]}, "tables": []}
    monkeypatch.setattr(cli, "connect_both", lambda a: (src, tgt, target))
    monkeypatch.setattr(cli, "load_plan", lambda p: (plan, "sha"))
    monkeypatch.setattr(cli, "_facts", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(cli.hp, "evaluate", lambda **k: [{"check": "c", "ok": not refused, "detail": None}])
    monkeypatch.setattr(cli.io_, "pg_dump_target", dump)
    return src, tgt


def test_apply_refusal_exits_11_and_never_dumps_or_writes(tmp_path, monkeypatch):
    cli = load_cli()
    calls = []
    src, tgt = _patch_apply(cli, monkeypatch, True, lambda *a, **k: calls.append(1))
    assert cli.cmd_apply(_apply_args(tmp_path)) == 11
    assert calls == [] and tgt.log == [] and src.log == []
    assert src.closed and tgt.closed


def test_apply_pre_dump_failure_exits_11_before_any_begin(tmp_path, monkeypatch, capsys):
    cli = load_cli()

    def boom(*a, **k):
        raise RuntimeError("pg_dump failed: password=s3cret for u")

    src, tgt = _patch_apply(cli, monkeypatch, False, boom)
    assert cli.cmd_apply(_apply_args(tmp_path)) == 11
    out = capsys.readouterr().out
    assert "s3cret" not in out
    assert not any("begin" in s.lower() for s in tgt.log + src.log)
    assert src.closed and tgt.closed


def test_apply_interrupt_inside_write_tx_rolls_back_and_closes(tmp_path, monkeypatch):
    cli = load_cli()
    src, tgt = _patch_apply(cli, monkeypatch, False, lambda *a, **k: {"path": "p", "bytes": 1})
    calls = []

    def settings(cur, local=False):
        calls.append(1)
        if len(calls) == 2:  # the target's, inside the write transaction
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.io_, "apply_session_settings", settings)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_apply(_apply_args(tmp_path))
    assert any(s.upper() == "BEGIN" for s in tgt.log)
    assert any(s.upper() == "ROLLBACK" for s in tgt.log)
    assert src.closed and tgt.closed
