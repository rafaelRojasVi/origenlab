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


def test_rollback_requires_confirm_delete_loaded_rows():
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


# ---- Task 6: verify and rollback -------------------------------------------------------------

import subprocess  # noqa: E402

from psycopg import sql as pgsql  # noqa: E402

REAL = load_cli().hp.TABLES
SEQ = load_cli().hp.SEQUENCE


def _plan_real():
    return {"remap": {"to": "x", "from": ["a"]}, "roster_hash": "r", "send_control_hash": "s", "campaign_block_hash": "c",
            "tables": [{"name": n, "count": 2, "hash": "h", "pk": ["id"], "columns": [["id", "uuid"]],
                        "fks": ["fk_x"] if i == 0 else []} for i, n in enumerate(REAL)]}


class _CatCursor(_FakeCursor):
    """Catalogue-aware fake: honours the schema filter of business_counts and counts real names."""

    def __init__(self, conn):
        super().__init__(conn.log)
        self.conn, self._rows = conn, None

    def execute(self, q, params=None):
        text = q.as_string(None) if isinstance(q, pgsql.Composed) else str(q)
        self.log.append(text)
        if text.startswith("DELETE FROM "):
            name = text.split()[-1]
            if name not in self.conn.sticky:
                self.conn.counts[name] = 0
        elif "from pg_class" in text:
            self._rows = [(n,) for n in self.conn.counts if n.split(".")[0] in params[0]]
        elif text.startswith("select count(*) from "):
            n = text.split("from ", 1)[1].replace('"', "")
            self._rows = [(self.conn.counts[n],)]
        elif text.startswith("select last_value"):
            self._rows = [(23095, True)]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


class _CatConn(_FakeConn):
    def __init__(self, sticky=()):
        super().__init__()
        # every real table loaded; platform.operator-ish and singleton tables outside the 16
        self.counts = {n: 2 for n in REAL}
        self.counts.update({"outbound.send_control": 1, "outbound.campaign_block": 1, "crm.task": 0,
                            "platform.operator": 1, "procurement.notice": 0})
        self.sticky = set(sticky)

    def cursor(self):
        return _CatCursor(self)


def _patch_rollback(cli, monkeypatch, *, tgt=None, roster="r", commit_msg="COMMIT", observed=None):
    src, tgt = _FakeConn(), tgt or _CatConn()
    target = SimpleNamespace(mode="hosted", password="s3cret", user="u", project_ref="r", host="h", hostaddr="1.1.1.1")
    plan = _plan_real()
    monkeypatch.setattr(cli, "connect_both", lambda a: (src, tgt, target))
    monkeypatch.setattr(cli, "load_plan", lambda p: (plan, "a" * 64))
    monkeypatch.setattr(cli.io_, "observed_rows", lambda cur, p: observed or {n: (2, "h") for n in REAL})
    monkeypatch.setattr(cli.io_, "roster_hash", lambda cur: roster)
    monkeypatch.setattr(cli.io_, "_hash_rows", lambda cur, q: "s" if "send_control" in q else "c")
    monkeypatch.setattr(cli.io_, "apply_session_settings", lambda cur, local=False: tgt.log.append("SETTINGS"))
    monkeypatch.setattr(cli.io_, "set_triggers", lambda cur, t, en: tgt.log.append(f"TRIGGERS {t} {en}"))
    monkeypatch.setattr(cli.io_, "set_fks_deferrable", lambda cur, t, fks, d: tgt.log.append(f"FKS {t} {d}"))
    monkeypatch.setattr(_FakeCursor, "statusmessage", commit_msg)
    return src, tgt


def _rb_args(tmp_path):
    a = _apply_args(tmp_path)
    a.confirm_delete_loaded_rows = True
    return a


def test_rollback_succeeds_with_real_count_helpers_and_orders_statements(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_rollback(cli, monkeypatch)
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 0
    log = tgt.log
    assert not any("TRUNCATE" in s.upper() for s in log)
    assert [s for s in log if s.startswith("DELETE")] == [f"DELETE FROM {n}" for n in reversed(REAL)]
    assert log[0] == "BEGIN" and log[-1] == "COMMIT" and log[-2].startswith("select setval")
    last_count = max(i for i, s in enumerate(log) if s.startswith("select count(*)"))
    assert last_count < len(log) - 2
    assert any("SET LOCAL ROLE origenlab_owner" in s for s in log) and "SET CONSTRAINTS ALL DEFERRED" in log
    assert src.closed and tgt.closed and "s3cret" not in capsys.readouterr().out


def test_rollback_dirty_after_delete_exits_12_without_setval_and_rereads(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_rollback(cli, monkeypatch, tgt=_CatConn(sticky=("crm.quote",)))
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 12
    assert not any(s.startswith("select setval") for s in tgt.log) and "COMMIT" not in tgt.log
    assert "ROLLBACK" in tgt.log
    out = capsys.readouterr().out
    assert "tables not empty" in out and "target re-read: state loaded" in out and "is_called=True" in out
    assert "untouched" not in out and src.closed and tgt.closed


def test_rollback_refuses_when_not_loaded(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    obs = {n: (0, "") for n in REAL}
    obs[REAL[0]] = (2, "h")
    src, tgt = _patch_rollback(cli, monkeypatch, observed=obs)
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 11
    out = capsys.readouterr().out
    assert "rollback --help" in out and "pre-load dump" not in out
    assert not any(s.startswith(("DELETE", "select setval")) for s in tgt.log) and tgt.log[-1] == "ROLLBACK"


def test_rollback_refuses_when_rows_exist_outside_the_load(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    tgt = _CatConn()
    tgt.counts["crm.task"] = 2
    src, tgt = _patch_rollback(cli, monkeypatch, tgt=tgt)
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 11
    assert not any(s.startswith("DELETE") for s in tgt.log) and tgt.log[-1] == "ROLLBACK"
    assert "crm.task" in capsys.readouterr().out and src.closed and tgt.closed


def test_rollback_refuses_when_the_roster_changed(tmp_path, monkeypatch):
    cli = load_cli()
    src, tgt = _patch_rollback(cli, monkeypatch, roster="other")
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 11
    assert not any(s.startswith("DELETE") for s in tgt.log) and tgt.log[-1] == "ROLLBACK"


def test_rollback_psycopg_error_rolls_back_without_setval(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_rollback(cli, monkeypatch)

    def boom(cur, t, en):
        raise psycopg.errors.LockNotAvailable("password=s3cret timeout")

    monkeypatch.setattr(cli.io_, "set_triggers", boom)
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 12
    out = capsys.readouterr().out
    assert "s3cret" not in out and "target re-read" in out
    assert "ROLLBACK" in tgt.log and not any(s.startswith("select setval") for s in tgt.log)
    assert src.closed and tgt.closed


def test_rollback_commit_with_unexpected_status_is_unknown_outcome(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_rollback(cli, monkeypatch, commit_msg="ROLLBACK")
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 12
    assert "unknown" in capsys.readouterr().out


def test_rollback_interrupt_rolls_back_and_closes(tmp_path, monkeypatch):
    cli = load_cli()
    src, tgt = _patch_rollback(cli, monkeypatch)

    def boom(cur, t, en):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.io_, "set_triggers", boom)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_rollback(_rb_args(tmp_path))
    assert "ROLLBACK" in tgt.log and src.closed and tgt.closed


def test_rollback_help_has_incident_text_and_no_restore_recipe(capsys):
    cli = load_cli()
    with pytest.raises(SystemExit):
        cli.main(["rollback", "--help"])
    out = capsys.readouterr().out
    assert "--confirm-delete-loaded-rows" in out and "confirm-truncate" not in out
    assert "Incident procedure" in out and "pg_restore --list" in out and "do not restore it" in out
    for forbidden in ("supabase.co", "pooler.supabase.com", "origenlab_migrator", "--data-only"):
        assert forbidden not in out


def test_load_plan_refuses_a_widened_or_reordered_table_set(tmp_path):
    import json
    cli = load_cli()
    plan = _plan_real()
    good = tmp_path / "ok.json"
    good.write_text(json.dumps(plan))
    assert cli.load_plan(good)[0]["tables"][0]["name"] == REAL[0]
    for bad in (plan["tables"] + [{"name": "crm.task"}], list(reversed(plan["tables"])), plan["tables"][:-1]):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps({**plan, "tables": bad}))
        with pytest.raises(cli.PlanRefused):
            cli.load_plan(f)
    assert cli.main(["apply", "--plan", str(f), "--plan-sha256", "a" * 64, *AUTH]) == 11


def _patch_verify(cli, monkeypatch, tmp_path, run):
    src, tgt = _FakeConn(), _FakeConn()
    target = SimpleNamespace(mode="hosted", password="s3cret", user="u", project_ref="r", host="h", hostaddr="1.1.1.1")
    plan = _plan_real()
    monkeypatch.setattr(cli, "connect_both", lambda a: (src, tgt, target))
    monkeypatch.setattr(cli, "load_plan", lambda p: (plan, "a" * 64))
    monkeypatch.setattr(cli.io_, "observed_rows", lambda cur, p: {n: (2, "h") for n in REAL})
    monkeypatch.setattr(cli.io_, "apply_session_settings", lambda cur, local=False: None)
    monkeypatch.setattr(cli.io_, "post_copy_invariants",
                        lambda cur, p, to: [{"check": n, "ok": True, "detail": None} for n in cli.io_.invariant_names(p)])
    monkeypatch.setattr(cli.io_, "pg_dump_target", lambda *a, **k: {"path": str(tmp_path / "post.dump"), "bytes": 1})
    monkeypatch.setattr(cli.subprocess, "run", run)
    return src, tgt


def _runner(calls, mint_out="origenlab_test_deadbeef\n", mint_rc=0, drop_rc=0):
    def run(argv, **kw):
        calls.append(argv[2:])
        if argv[2] == "mint":
            return subprocess.CompletedProcess(argv, mint_rc, mint_out, "boom password=s3cret")
        return subprocess.CompletedProcess(argv, drop_rc, "", "")
    return run


def test_verify_mint_failure_exits_13_and_never_drops(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    calls = []
    src, tgt = _patch_verify(cli, monkeypatch, tmp_path, _runner(calls, mint_out="", mint_rc=1))
    assert cli.cmd_verify(_apply_args(tmp_path)) == 13
    assert calls == [["mint"]] and "s3cret" not in capsys.readouterr().out
    assert src.closed and tgt.closed


def test_verify_unknown_scratch_name_is_refused_and_never_dropped(tmp_path, monkeypatch):
    cli = load_cli()
    calls = []
    _patch_verify(cli, monkeypatch, tmp_path, _runner(calls, mint_out="origenlab_clean\n"))
    assert cli.cmd_verify(_apply_args(tmp_path)) == 13
    assert calls == [["mint"]]


def test_verify_drops_scratch_after_restore_failure_and_warns_when_drop_fails(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    calls = []
    _patch_verify(cli, monkeypatch, tmp_path, _runner(calls, drop_rc=1))

    def fail(dump, name):
        raise RuntimeError("pg_restore failed: password=s3cret")

    monkeypatch.setattr(cli.io_, "restore_into_scratch", fail)
    assert cli.cmd_verify(_apply_args(tmp_path)) == 13
    assert calls == [["mint"], ["drop", "origenlab_test_deadbeef"]]
    out = capsys.readouterr().out
    assert "s3cret" not in out and "origenlab_test_deadbeef could not be dropped" in out


def test_verify_partial_state_exits_13_before_any_dump(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    calls = []
    src, tgt = _patch_verify(cli, monkeypatch, tmp_path, _runner(calls))
    obs = {n: (0, "") for n in REAL}
    obs[REAL[0]] = (2, "h")
    monkeypatch.setattr(cli.io_, "observed_rows", lambda cur, p: obs)
    assert cli.cmd_verify(_apply_args(tmp_path)) == 13
    out = capsys.readouterr().out
    assert "partial" in out and "incident" in out and calls == []
    assert any("READ ONLY" in s for s in tgt.log) and any("SET LOCAL ROLE origenlab_owner" in s for s in tgt.log)
    assert src.closed and tgt.closed


def test_scratch_script_parses_and_lists_all_subcommands():
    script = _SCRIPTS / "hosted_data_load_scratch.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    text = script.read_text()
    assert "mint|clone-cleanroom|drop" in text and script.stat().st_mode & stat.S_IXUSR
    assert text.count("cleanup_on_failure") >= 3
