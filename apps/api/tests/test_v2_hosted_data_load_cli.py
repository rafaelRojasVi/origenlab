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
    assert (cli.EXIT_REFUSED, cli.EXIT_APPLY_FAILED, cli.EXIT_VERIFY_MISMATCH, cli.EXIT_VERIFY_INCOMPLETE) == \
        (11, 12, 13, 14)
    for line in ("11 refused", "12 apply/rollback failed", "13 verify mismatch", "14 verify incomplete"):
        assert line in cli.__doc__


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
    monkeypatch.setattr(cli.io_, "hosted_target", lambda *a, **k: SimpleNamespace(mode="hosted"))
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
        for prefix, exc in self.conn.raise_on.items():
            if text.startswith(prefix):
                raise exc
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
        self.raise_on: dict[str, BaseException] = {}

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


def test_verify_mint_failure_is_incomplete_14_and_never_drops(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    calls = []
    src, tgt = _patch_verify(cli, monkeypatch, tmp_path, _runner(calls, mint_out="", mint_rc=1))
    assert cli.cmd_verify(_apply_args(tmp_path)) == 14
    assert calls == [["mint"]] and "s3cret" not in capsys.readouterr().out
    assert src.closed and tgt.closed


def test_verify_unknown_scratch_name_is_refused_and_never_dropped(tmp_path, monkeypatch):
    cli = load_cli()
    calls = []
    _patch_verify(cli, monkeypatch, tmp_path, _runner(calls, mint_out="origenlab_clean\n"))
    assert cli.cmd_verify(_apply_args(tmp_path)) == 14
    assert calls == [["mint"]]


def test_verify_drops_scratch_after_restore_failure_and_warns_when_drop_fails(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    calls = []
    _patch_verify(cli, monkeypatch, tmp_path, _runner(calls, drop_rc=1))

    def fail(dump, name):
        raise RuntimeError("pg_restore failed: password=s3cret")

    monkeypatch.setattr(cli.io_, "restore_into_scratch", fail)
    assert cli.cmd_verify(_apply_args(tmp_path)) == 14
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


def test_reread_state_pins_settings_after_the_role_switch(monkeypatch, capsys):
    cli = load_cli()
    tgt = _CatConn()
    monkeypatch.setattr(cli.io_, "apply_session_settings", lambda cur, local=False: tgt.log.append(f"SETTINGS local={local}"))
    monkeypatch.setattr(cli.io_, "observed_rows", lambda cur, p: {n: (0, "") for n in REAL})
    cli._reread_state(tgt, _plan_real(), ())
    role = tgt.log.index("SET LOCAL ROLE origenlab_owner")
    assert tgt.log[role + 1] == "SETTINGS local=True"
    assert "state empty" in capsys.readouterr().out


def test_rollback_interrupt_message_says_run_verify(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _patch_rollback(cli, monkeypatch)
    monkeypatch.setattr(cli.io_, "set_triggers", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_rollback(_rb_args(tmp_path))
    assert "run `verify` before anything else" in capsys.readouterr().out


def test_rollback_epilog_points_pg_restore_into_the_container(capsys):
    cli = load_cli()
    with pytest.raises(SystemExit):
        cli.main(["rollback", "--help"])
    out = capsys.readouterr().out
    assert "docker cp <dump> origenlab_dev_db:/tmp/x.dump" in out and "docker exec origenlab_dev_db pg_restore --list" in out


def _fp_facts(extra_line):
    fp = PLAN_TESTS.fp()
    src = PLAN_TESTS.good_source(schema_fingerprint=fp)
    src["schema_fingerprint_lines"] = {"columns": [f"crm.note|{i}" for i in range(12)]}
    tgt = PLAN_TESTS.good_target(schema_fingerprint=PLAN_TESTS.fp(columns="other"))
    tgt["schema_fingerprint_lines"] = {"columns": [*src["schema_fingerprint_lines"]["columns"], extra_line]}
    return src, tgt


def test_fingerprint_refusal_prints_catalogue_diff_redacted(capsys):
    cli = load_cli()
    src, tgt = _fp_facts("crm.note|e2e_extra projref123")
    checks = [{"check": "schema_fingerprint_identical", "ok": False, "detail": ["columns"]}]
    cli.print_fingerprint_diff(checks, src, tgt, ("projref123",))
    out = capsys.readouterr().out
    assert "[columns]" in out and "+ crm.note|e2e_extra ***" in out and "projref123" not in out


def test_fingerprint_diff_is_capped_at_ten_lines(capsys):
    cli = load_cli()
    src, tgt = _fp_facts("x")
    src["schema_fingerprint_lines"] = {"columns": [f"s{i}" for i in range(30)]}
    tgt["schema_fingerprint_lines"] = {"columns": []}
    cli.print_fingerprint_diff([{"check": "schema_fingerprint_identical", "ok": False, "detail": None}], src, tgt)
    assert capsys.readouterr().out.count("    - s") == 10


def test_no_diff_printed_when_fingerprint_check_passes_or_facts_absent(capsys):
    cli = load_cli()
    src, tgt = _fp_facts("x")
    cli.print_fingerprint_diff([{"check": "schema_fingerprint_identical", "ok": True, "detail": None}], src, tgt)
    cli.print_fingerprint_diff([{"check": "schema_fingerprint_identical", "ok": False, "detail": None}], None, None)
    assert capsys.readouterr().out == ""


def test_plan_refusal_by_fingerprint_prints_diff(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _patch_plan_env(cli, monkeypatch, refuse=False)
    src, tgt = _fp_facts("crm.note|e2e_extra")
    monkeypatch.setattr(cli, "_facts", lambda *a, **k: (src, tgt, PLAN_TESTS.good_host()))
    assert cli.main(["plan", "--out", str(tmp_path), *AUTH]) == 11
    out = capsys.readouterr().out
    assert "REFUSED schema_fingerprint_identical" in out and "+ crm.note|e2e_extra" in out


# ---- final review: locks first, bounded waits, apply re-check (I3, I6, M4) ------------------------

import re  # noqa: E402

_ROW_HASH_TABLE = re.compile(r"from ([a-z_]+\.[a-z_]+)\) t$")


class _SrcCursor(_FakeCursor):
    """Source fake: answers the snapshot re-hash of each planned table with ``conn.rows[table]``."""

    def __init__(self, conn):
        super().__init__(conn.log)
        self.conn, self._row = conn, None

    def execute(self, q, params=None):
        self.log.append(str(q))
        m = _ROW_HASH_TABLE.search(str(q))
        self._row = self.conn.rows.get(m.group(1), (2, "h")) if m else None

    def fetchone(self):
        return self._row


class _SrcConn(_FakeConn):
    def __init__(self, rows=None):
        super().__init__()
        self.rows = rows or {}

    def cursor(self):
        return _SrcCursor(self)


def _patch_apply_real(cli, monkeypatch, tmp_path, *, src=None, tgt=None, copied=None, invariants=None):
    """apply over the 16 real table names: preflight passes, the target is empty, every helper logs."""
    src, tgt = src or _SrcConn(), tgt or _CatConn()
    for n in REAL:
        tgt.counts[n] = 0
    target = SimpleNamespace(mode="hosted", password="s3cret", user="u", project_ref="r", host="h", hostaddr="1.1.1.1")
    plan = {**_plan_real(), "payload_rows_with_local_uuid": 0,
            "sequence": {"name": SEQ, "target_last_value": 23095}}
    monkeypatch.setattr(cli, "connect_both", lambda a: (src, tgt, target))
    monkeypatch.setattr(cli, "load_plan", lambda p: (plan, "a" * 64))
    monkeypatch.setattr(cli, "_facts", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(cli.hp, "evaluate", lambda **k: [{"check": "c", "ok": True, "detail": None}])
    monkeypatch.setattr(cli.io_, "pg_dump_target", lambda *a, **k: {"path": "pre.dump", "bytes": 1})
    monkeypatch.setattr(cli.io_, "apply_session_settings", lambda cur, local=False: tgt.log.append("SETTINGS"))
    monkeypatch.setattr(cli.io_, "set_triggers", lambda cur, t, en: tgt.log.append(f"TRIGGERS {t} {en}"))
    monkeypatch.setattr(cli.io_, "set_fks_deferrable", lambda cur, t, fks, d: tgt.log.append(f"FKS {t} {d}"))

    def copy(sc, tc, name, cols, select_sql):
        tgt.log.append(f"COPY {name}")
        return (copied or {}).get(name, 2)

    monkeypatch.setattr(cli.io_, "copy_table", copy)
    monkeypatch.setattr(cli.io_, "post_copy_invariants", lambda cur, p, to: invariants if invariants is not None else
                        [{"check": n, "ok": True, "detail": None} for n in cli.io_.invariant_names(p)])
    monkeypatch.setattr(_FakeCursor, "statusmessage", "COMMIT")
    (tmp_path / "out").mkdir(exist_ok=True)
    return src, tgt


def _lock_index(log):
    return next(i for i, s in enumerate(log) if s.startswith("LOCK TABLE"))


def test_rollback_locks_the_16_tables_before_the_first_check(tmp_path, monkeypatch):
    """I3: a command committing between the checks and the DELETE would be silently deleted."""
    cli = load_cli()
    src, tgt = _patch_rollback(cli, monkeypatch)

    def observed(cur, p):
        tgt.log.append("OBSERVED")
        return {n: (2, "h") for n in REAL}

    monkeypatch.setattr(cli.io_, "observed_rows", observed)
    assert cli.cmd_rollback(_rb_args(tmp_path)) == 0
    log = tgt.log
    lock = _lock_index(log)
    assert log[lock] == "LOCK TABLE " + ", ".join(".".join(f'"{p}"' for p in n.split(".")) for n in REAL) + \
        " IN SHARE ROW EXCLUSIVE MODE"
    assert lock < log.index("OBSERVED")
    assert log.index("SET LOCAL ROLE origenlab_owner") < lock
    assert any("pg_advisory_xact_lock" in s for s in log[:lock])
    assert not any(s.startswith(("select count(*)", "DELETE")) for s in log[:lock])


@pytest.mark.parametrize("cmd", ["apply", "rollback"])
def test_write_transactions_bound_every_wait(tmp_path, monkeypatch, cmd):
    """I6: lock_timeout, a 5 min statement_timeout and a 60 s idle_in_transaction timeout, all SET LOCAL."""
    cli = load_cli()
    if cmd == "apply":
        src, tgt = _patch_apply_real(cli, monkeypatch, tmp_path)
        assert cli.cmd_apply(_apply_args(tmp_path)) == 0
    else:
        src, tgt = _patch_rollback(cli, monkeypatch)
        assert cli.cmd_rollback(_rb_args(tmp_path)) == 0
    log = tgt.log
    begin = log.index("BEGIN")
    head = log[begin:_lock_index(log)]
    assert "SET LOCAL lock_timeout = '30s'" in head
    assert "SET LOCAL statement_timeout = '5min'" in head
    assert "SET LOCAL idle_in_transaction_session_timeout = '60s'" in head
    assert not any("15min" in s for s in log)


def test_apply_refuses_when_the_target_changed_since_preflight(tmp_path, monkeypatch, capsys):
    """M4: under the locks, a row outside the 16 (or in them) -> ROLLBACK, exit 11, nothing written."""
    cli = load_cli()
    tgt = _CatConn()
    tgt.counts["crm.task"] = 1
    src, tgt = _patch_apply_real(cli, monkeypatch, tmp_path, tgt=tgt)
    assert cli.cmd_apply(_apply_args(tmp_path)) == 11
    out = capsys.readouterr().out
    assert "target changed since preflight" in out and "crm.task" in out
    log = tgt.log
    assert _lock_index(log) < max(i for i, s in enumerate(log) if s.startswith("select count(*)"))
    assert log[-1] == "ROLLBACK" and not any(s.startswith(("COPY", "FKS", "TRIGGERS", "COMMIT")) for s in log)
    assert src.closed and tgt.closed


def test_apply_refuses_when_a_planned_table_is_no_longer_empty(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_apply_real(cli, monkeypatch, tmp_path)
    tgt.counts["crm.quote"] = 3
    assert cli.cmd_apply(_apply_args(tmp_path)) == 11
    assert "crm.quote" in capsys.readouterr().out and not any(s.startswith("COPY") for s in tgt.log)


# ---- final review: unexpected errors are redacted and mapped (I5); verify outcomes (M2) ----------

_HOSTED_ERR = ('connection to server at "aws-0-sa-east-1.pooler.supabase.com" (1.2.3.4), port 5432 failed: '
               'FATAL: password authentication failed for user "origenlab_migrator.abcdefghijklmnopqrst"')


@pytest.mark.parametrize(("cmd", "code"), [("plan", 11), ("apply", 11), ("rollback", 11), ("verify", 14)])
def test_connect_failure_is_redacted_and_mapped(tmp_path, monkeypatch, capsys, cmd, code):
    """I5: no traceback, no hosted host / address / login, and the documented exit code."""
    cli = load_cli()

    def boom(args):
        raise psycopg.OperationalError(_HOSTED_ERR)

    monkeypatch.setattr(cli, "connect_both", boom)
    monkeypatch.setattr(cli, "connect_target", boom, raising=False)
    monkeypatch.setattr(cli, "load_plan", lambda p: (_plan_real(), "a" * 64))
    extra = {"plan": [], "apply": ["--plan", "p", "--plan-sha256", "a" * 64],
             "verify": ["--plan", "p", "--plan-sha256", "a" * 64],
             "rollback": ["--plan", "p", "--plan-sha256", "a" * 64, "--confirm-delete-loaded-rows"]}[cmd]
    assert cli.main([cmd, "--out", str(tmp_path), *extra, *AUTH]) == code
    cap = capsys.readouterr()
    text = cap.out + cap.err
    for leak in ("pooler.supabase.com", "1.2.3.4", "origenlab_migrator.abcdefghijklmnopqrst", "abcdefghijklmnopqrst",
                 "Traceback"):
        assert leak not in text
    assert "OperationalError" in text


def test_unexpected_error_inside_the_write_transaction_exits_12(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_apply_real(cli, monkeypatch, tmp_path)

    def broken(*a, **k):
        raise ValueError("bug at 10.9.8.7")

    monkeypatch.setattr(cli.io_, "copy_table", broken)
    rc = cli.main(["apply", "--plan", "p", "--plan-sha256", "a" * 64, "--out", str(tmp_path / "out"), *AUTH])
    out = capsys.readouterr().out
    assert rc == 12 and "ROLLBACK" in tgt.log and "COMMIT" not in tgt.log
    assert "apply interrupted; transaction rolled back" in out and "run `verify`" in out and "10.9.8.7" not in out


def test_unexpected_error_before_the_write_transaction_exits_11(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_apply_real(cli, monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_facts", lambda *a, **k: (_ for _ in ()).throw(KeyError("tables")))
    rc = cli.main(["apply", "--plan", "p", "--plan-sha256", "a" * 64, "--out", str(tmp_path / "out"), *AUTH])
    assert rc == 11 and "nothing was written" in capsys.readouterr().out
    assert "BEGIN" not in tgt.log


def test_ctrl_c_still_interrupts_main(tmp_path, monkeypatch):
    cli = load_cli()
    monkeypatch.setattr(cli, "connect_both", lambda a: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        cli.main(["plan", "--out", str(tmp_path), *AUTH])


_CPE = subprocess.CalledProcessError(1, ["docker", "cp", "/x/ca.crt", "origenlab_dev_db:/tmp/hosted-ca.crt"])


def test_pre_dump_docker_cp_failure_refuses_11(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    src, tgt = _patch_apply_real(cli, monkeypatch, tmp_path)
    monkeypatch.setattr(cli.io_, "pg_dump_target", lambda *a, **k: (_ for _ in ()).throw(_CPE))
    assert cli.cmd_apply(_apply_args(tmp_path)) == 11
    assert "pre-load dump failed; nothing written" in capsys.readouterr().out and "BEGIN" not in tgt.log


def test_probe_docker_cp_failure_is_a_refusal_not_a_crash(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _patch_plan_env(cli, monkeypatch, refuse=False)
    monkeypatch.setattr(cli, "_facts", lambda args, src, tgt, target, op, probe: (
        PLAN_TESTS.good_source(), PLAN_TESTS.good_target(pg_dump_probe_ok=probe), PLAN_TESTS.good_host()))
    monkeypatch.setattr(cli.io_, "pg_dump_target", lambda *a, **k: (_ for _ in ()).throw(_CPE))
    assert cli.main(["plan", "--out", str(tmp_path), *AUTH]) == 11
    assert "REFUSED pg_dump_can_reach_target" in capsys.readouterr().out


def test_post_dump_failure_is_verify_incomplete_14(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    calls = []
    _patch_verify(cli, monkeypatch, tmp_path, _runner(calls))
    monkeypatch.setattr(cli.io_, "pg_dump_target", lambda *a, **k: (_ for _ in ()).throw(_CPE))
    assert cli.cmd_verify(_apply_args(tmp_path)) == 14
    assert "VERIFY INCOMPLETE" in capsys.readouterr().out and calls == []


def test_verify_empty_prints_counts_and_says_apply_may_be_rerun(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    calls = []
    _patch_verify(cli, monkeypatch, tmp_path, _runner(calls))
    monkeypatch.setattr(cli.io_, "observed_rows", lambda cur, p: {n: (0, "") for n in REAL})
    assert cli.cmd_verify(_apply_args(tmp_path)) == 13
    out = capsys.readouterr().out
    assert "target state: empty" in out and "the load did not commit; apply may be re-run after a fresh plan" in out
    assert "observed" in out and "planned" in out and f"{REAL[0]:40} {0:>9} {2:>9}" in out and calls == []


def test_verify_partial_prints_observed_vs_planned_per_table(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _patch_verify(cli, monkeypatch, tmp_path, _runner([]))
    obs = {n: (2, "h") for n in REAL}
    obs["crm.quote"] = (1, "x")
    monkeypatch.setattr(cli.io_, "observed_rows", lambda cur, p: obs)
    assert cli.cmd_verify(_apply_args(tmp_path)) == 13
    out = capsys.readouterr().out
    assert f"{'crm.quote':40} {1:>9} {2:>9}   <- differs" in out and "incident" in out


def test_verify_target_read_failure_is_incomplete_14(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    _patch_verify(cli, monkeypatch, tmp_path, _runner([]))
    monkeypatch.setattr(cli.io_, "observed_rows",
                        lambda cur, p: (_ for _ in ()).throw(psycopg.OperationalError("server closed the connection")))
    assert cli.cmd_verify(_apply_args(tmp_path)) == 14


def test_failed_rollback_statement_is_reported_as_attempted(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    tgt = _CatConn()
    tgt.raise_on["ROLLBACK"] = psycopg.OperationalError("connection lost")
    src, tgt = _patch_apply_real(cli, monkeypatch, tmp_path, tgt=tgt, copied={"crm.quote": 1})
    assert cli.cmd_apply(_apply_args(tmp_path)) == 12
    out = capsys.readouterr().out
    assert "rollback attempted (the ROLLBACK itself failed)" in out and "transaction rolled back" not in out


def test_ctrl_c_during_commit_says_outcome_unknown(tmp_path, monkeypatch, capsys):
    cli = load_cli()
    tgt = _CatConn()
    tgt.raise_on["COMMIT"] = KeyboardInterrupt()
    _patch_apply_real(cli, monkeypatch, tmp_path, tgt=tgt)
    with pytest.raises(KeyboardInterrupt):
        cli.cmd_apply(_apply_args(tmp_path))
    assert "commit outcome unknown — run `verify`" in capsys.readouterr().out
