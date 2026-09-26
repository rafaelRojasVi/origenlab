"""The production guard of the quotation CRM import: every refusal, and the apply's order of events.

Fictitious data throughout. Nothing here opens `origenlab_clean`: the guards are judged from
synthetic facts, and the one database-backed test reads a disposable database.
"""

from __future__ import annotations

import csv
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from origenlab_api.v2 import quote_crm_import_plan as qp

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
try:
    import quote_crm_import_production as prod  # the same module object the script imports
finally:
    sys.path.remove(str(_SCRIPTS))
OP_ID = "714dd8d0-0000-4000-8000-000000000001"
H = {name: f"{i:x}" * 64 for i, name in enumerate(prod.REQUIRED_INPUTS, start=1)}
PLAN, LEDGER = "a" * 64, "b" * 64
TARGET = "postgresql://origenlab_api:secret@127.0.0.1:54332/origenlab_clean"
ADMIN = "postgresql://postgres:secret@127.0.0.1:54332/origenlab_clean"
KEYS = ["qimport:v1:opp:open", "qimport:v1:opp:org"]


def load_script():
    sys.path.insert(0, str(_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("quote_crm_import_script_prod", _SCRIPTS / "quote_crm_import.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(_SCRIPTS))


def good_db(**over) -> dict:
    db = {"current_database": "origenlab_clean", "system_identifier": "7400000000000000001",
          "migrations": ["20260922200000", prod.REQUIRED_MIGRATION], "number_origin_column": True,
          "operator": {"id": OP_ID, "role": "admin", "status": "active", "display_name": "Rafael"},
          "other_sessions": [], "advisory_lock_free": True, "qimport_receipts": {}, "domain_event_max_position": 10}
    db.update(over)
    return db


def good_container(**over) -> dict:
    c = {"published": ["127.0.0.1:54332"], "publishes_port": True, "system_identifier": "7400000000000000001",
         "pg_dump_version": "pg_dump (PostgreSQL) 17.4"}
    c.update(over)
    return c


def judge(**over) -> list[str]:
    kw = dict(target_dsn=TARGET, admin_dsn=ADMIN, plan_sha=PLAN, expected_plan=PLAN, ledger_sha=LEDGER,
              expected_ledger=LEDGER, input_hashes=dict(H), expected_inputs=dict(H), db=good_db(),
              container=good_container(), processes={"processes": []}, crontab={"lines": []}, plan_keys=KEYS,
              confirmation_operator_ids={OP_ID}, evidence_unclean=0, scope_violations=0)
    kw.update(over)
    return prod.refused(prod.evaluate(**kw))


# ------------------------------------------------------------------ arguments


def test_expected_inputs_must_name_exactly_the_required_set() -> None:
    pairs = [f"{k}={v}" for k, v in H.items()]
    assert prod.parse_expected_inputs(pairs) == H
    with pytest.raises(prod.ProductionRefused, match="missing"):
        prod.parse_expected_inputs(pairs[1:])
    with pytest.raises(prod.ProductionRefused, match="unknown"):
        prod.parse_expected_inputs([*pairs, f"other.csv={'c' * 64}"])
    with pytest.raises(prod.ProductionRefused, match="twice"):
        prod.parse_expected_inputs([*pairs, pairs[0]])
    for bad in ("policy", f"policy={'A' * 64}", "policy=abc", f"policy:{'c' * 64}"):
        with pytest.raises(prod.ProductionRefused):
            prod.parse_expected_inputs([bad])


def test_the_policy_fingerprint_is_stable_and_moves_with_the_excluded_documents() -> None:
    one = prod.policy_fingerprint({"d" * 64: ["G1"], "e" * 64: ["W1", "A2"]})
    assert one == prod.policy_fingerprint({"e" * 64: ["A2", "W1"], "d" * 64: ["G1"]})
    assert one != prod.policy_fingerprint({"d" * 64: ["G1"]})


# ------------------------------------------------------------------ every guard


def test_a_clean_production_state_passes_every_guard() -> None:
    assert judge() == []


def test_completed_receipts_of_this_plan_do_not_block_a_resume() -> None:
    receipts = {k: {"status": "completed", "operator_id": OP_ID} for k in KEYS}
    assert judge(db=good_db(qimport_receipts=receipts)) == []


@pytest.mark.parametrize(("over", "refusal"), [
    ({"target_dsn": TARGET.replace("origenlab_clean", "origenlab_dev")}, "target_dsn_names_production_db"),
    ({"target_dsn": TARGET.replace("origenlab_clean", "origenlab_clean2")}, "target_dsn_names_production_db"),
    ({"admin_dsn": ADMIN.replace("origenlab_clean", "origenlab_test_0000000a")}, "admin_dsn_names_production_db"),
    ({"admin_dsn": ADMIN.replace("54332", "54322")}, "dsns_share_host_and_port"),
    ({"db": good_db(current_database="origenlab_dev")}, "connected_database_is_production_db"),
    ({"container": good_container(publishes_port=False)}, "container_publishes_the_port"),
    ({"container": good_container(system_identifier="1")}, "container_is_the_server_behind_the_dsn"),
    ({"container": good_container(system_identifier=None)}, "container_is_the_server_behind_the_dsn"),
    ({"container": good_container(pg_dump_version=None)}, "pg_dump_available"),
    ({"db": good_db(migrations=["20260922200000"])}, "migration_recorded"),
    ({"db": good_db(number_origin_column=False)}, "migration_schema_present"),
    ({"db": good_db(other_sessions=[{"pid": 9, "user": "origenlab_api"}])}, "no_other_database_sessions"),
    ({"processes": {"processes": [{"pid": 9, "cmd": "uvicorn", "where": ["env:X"]}]}}, "no_local_process_names_the_db"),
    ({"crontab": {"lines": ["* * * * * quote_crm_import.py"]}}, "no_crontab_entry_names_the_db"),
    ({"db": good_db(advisory_lock_free=False)}, "import_advisory_lock_free"),
    ({"plan_sha": "c" * 64}, "plan_sha256_matches"),
    ({"ledger_sha": "c" * 64}, "ledger_sha256_matches"),
    ({"evidence_unclean": 1}, "evidence_reconciliation_clean"),
    ({"scope_violations": 2}, "evidence_scope_clean"),
    ({"confirmation_operator_ids": {"someone-else"}}, "operator_is_the_confirming_operator"),
    ({"confirmation_operator_ids": {OP_ID, "someone-else"}}, "operator_is_the_confirming_operator"),
])
def test_each_guard_refuses_on_its_own(over, refusal) -> None:
    assert judge(**over) == [refusal]


@pytest.mark.parametrize("host", ["localhost", "10.0.0.5", "db.example.test"])
def test_the_host_must_be_a_loopback_literal(host) -> None:
    assert "host_is_loopback_literal" in judge(target_dsn=TARGET.replace("127.0.0.1", host),
                                               admin_dsn=ADMIN.replace("127.0.0.1", host))


@pytest.mark.parametrize("name", prod.REQUIRED_INPUTS)
def test_each_input_hash_is_checked(name) -> None:
    assert judge(input_hashes={**H, name: "f" * 64}) == [f"input_sha256_matches:{name}"]


@pytest.mark.parametrize(("operator", "refusals"), [
    (None, ["operator_registered", "operator_active", "operator_admin", "operator_is_the_confirming_operator"]),
    ({"id": OP_ID, "role": "admin", "status": "disabled"}, ["operator_active"]),
    ({"id": OP_ID, "role": "operator", "status": "active"}, ["operator_admin"]),
])
def test_the_operator_must_be_registered_active_and_admin(operator, refusals) -> None:
    assert judge(db=good_db(operator=operator)) == refusals


@pytest.mark.parametrize(("receipts", "refusal"), [
    ({"qimport:v1:another-plan:open": {"status": "completed", "operator_id": OP_ID}}, "no_foreign_import_receipts"),
    ({KEYS[0]: {"status": "in_progress", "operator_id": OP_ID}}, "no_unfinished_import_receipts"),
    ({KEYS[0]: {"status": "failed", "operator_id": OP_ID}}, "no_unfinished_import_receipts"),
    ({KEYS[0]: {"status": "completed", "operator_id": "other"}}, "import_receipts_belong_to_the_operator"),
])
def test_receipts_the_plan_cannot_account_for_refuse(receipts, refusal) -> None:
    assert judge(db=good_db(qimport_receipts=receipts)) == [refusal]


# ------------------------------------------------------------------ the host facts


def _proc(root: Path, pid: int, ppid: int, cmd: list[str], env: dict[str, str] | None) -> None:
    d = root / str(pid)
    d.mkdir()
    (d / "stat").write_text(f"{pid} (x y) S {ppid} 0 0")
    (d / "cmdline").write_bytes(b"\0".join(c.encode() for c in cmd) + b"\0")
    if env is not None:
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")


def test_the_process_scan_finds_writers_by_name_never_by_value(tmp_path) -> None:
    _proc(tmp_path, 1, 0, ["init"], {})
    _proc(tmp_path, 10, 1, ["bash"], {"DSN": "postgresql://u:p@127.0.0.1:54332/origenlab_clean"})  # our parent
    _proc(tmp_path, 11, 10, ["python", "quote_crm_import.py", "--target-dsn", "x/origenlab_clean"], {})  # us
    _proc(tmp_path, 20, 1, ["uvicorn", "origenlab_api.main:app"],
          {"ORIGENLAB_V2_DATABASE_URL": "postgresql://origenlab_api:hunter2@127.0.0.1:54332/origenlab_clean"})
    _proc(tmp_path, 21, 1, ["psql", "postgresql://postgres:hunter2@127.0.0.1:54332/origenlab_clean"], {})
    _proc(tmp_path, 22, 1, ["uvicorn"], {"ORIGENLAB_V2_DATABASE_URL": "postgresql://u:p@h/origenlab_cleanroom"})
    _proc(tmp_path, 23, 1, ["postgres: origenlab_api origenlab_clean 172.17.0.1 idle"], {})
    _proc(tmp_path, 24, 1, ["python", "stage.py", "origenlab_clean"], None)  # environ unreadable
    found = prod.scan_processes(tmp_path, self_pid=11)
    by_pid = {p["pid"]: p for p in found["processes"]}
    assert set(by_pid) == {20, 21, 24}
    assert by_pid[20]["where"] == ["env:ORIGENLAB_V2_DATABASE_URL"]
    assert by_pid[21]["where"] == ["cmdline"]
    assert "hunter2" not in repr(found)
    assert found["environ_unreadable"] == 1


def _completed(rc: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def test_the_crontab_is_read_for_the_db_and_the_importer() -> None:
    tab = "# a comment naming origenlab_clean\n* * * * * refresh.sh\n0 * * * * psql -d origenlab_clean -f x.sql\n" \
          "5 * * * * uv run python scripts/quote_crm_import.py --apply\n"
    lines = prod.read_crontab(lambda *a, **k: _completed(0, tab))["lines"]
    assert len(lines) == 2 and all("refresh" not in ln for ln in lines)
    assert prod.read_crontab(lambda *a, **k: _completed(1, "", "no crontab for u"))["lines"] == []


def test_container_facts_come_from_the_container() -> None:
    answers = {"port": _completed(0, "127.0.0.1:54332\n"), "psql": _completed(0, "7400\n"),
               "pg_dump": _completed(0, "pg_dump (PostgreSQL) 17.4\n")}

    def run(argv, **kw):
        return answers["port" if argv[1] == "port" else argv[3] if argv[3] in answers else "psql"]

    facts = prod.container_facts(54332, run)
    assert facts == {"published": ["127.0.0.1:54332"], "publishes_port": True, "system_identifier": "7400",
                     "pg_dump_version": "pg_dump (PostgreSQL) 17.4"}
    assert prod.container_facts(54322, run)["publishes_port"] is False
    down = prod.container_facts(54332, lambda *a, **k: _completed(1))
    assert down["system_identifier"] is None and down["pg_dump_version"] is None and not down["publishes_port"]


# ------------------------------------------------------------------ the backup


def _dump_runner(dump: bytes = b"PGDMP-fake", dump_rc: int = 0, list_rc: int = 0,
                 toc: bytes = b";\n; header\n1; 0 0 TABLE DATA crm quote postgres\n2; 0 0 TABLE crm quote postgres\n"):
    def run(argv, **kw):
        if "pg_dump" in argv:
            kw["stdout"].write(dump)
            return SimpleNamespace(returncode=dump_rc, stderr=b"boom" if dump_rc else b"")
        assert kw["input"] == dump
        return SimpleNamespace(returncode=list_rc, stdout=toc if not list_rc else b"")
    return run


def test_the_backup_is_verified_private_and_outside_the_repository(tmp_path) -> None:
    repo, out = tmp_path / "repo", tmp_path / "backups"
    repo.mkdir()
    out.mkdir()
    b = prod.take_backup(out, repo, "20260926T000000Z", _dump_runner())
    assert b["toc_entries"] == 2 and b["table_data_entries"] == 1 and len(b["sha256"]) == 64
    assert (os.stat(b["path"]).st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError):  # never overwrites
        prod.take_backup(out, repo, "20260926T000000Z", _dump_runner())


@pytest.mark.parametrize(("runner", "match"), [
    (_dump_runner(dump_rc=1), "pg_dump failed"),
    (_dump_runner(dump=b"not a dump"), "not a custom-format dump"),
    (_dump_runner(list_rc=1), "pg_restore --list"),
    (_dump_runner(toc=b"; only comments\n"), "pg_restore --list"),
])
def test_a_backup_that_cannot_be_proven_refuses(tmp_path, runner, match) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(prod.ProductionRefused, match=match):
        prod.take_backup(tmp_path, repo, "s", runner)


def test_the_backup_refuses_the_repository_and_a_missing_directory(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    for where in (repo, repo / "sub"):
        with pytest.raises(prod.ProductionRefused, match="inside the repository"):
            prod.take_backup(where, repo, "s", _dump_runner())
    with pytest.raises(prod.ProductionRefused, match="does not exist"):
        prod.take_backup(tmp_path / "nope", repo, "s", _dump_runner())


def test_fingerprint_diff_reports_only_changed_tables() -> None:
    before = {"tables": {"crm.quote": {"rows": 0, "md5": "-"}, "outbound.x": {"rows": 3, "md5": "m"}}}
    after = {"tables": {"crm.quote": {"rows": 2, "md5": "q"}, "outbound.x": {"rows": 3, "md5": "m"}}}
    assert prod.fingerprint_diff(before, after) == {"crm.quote": {"rows_before": 0, "rows_after": 2, "delta": 2}}


# ------------------------------------------------------------------ the CLI


def _inputs(tmp_path) -> list[str]:
    files = {}
    for name in ("ledger", "email-ledger", "identity", "staging", "blocking", "confirmations"):
        p = tmp_path / f"{name}.in"
        p.write_text("")
        files[name] = p
    return [a for n, p in files.items() for a in (f"--{n}", str(p))]


def _prod_args(mod, tmp_path, *extra: str) -> list[str]:
    ledger_sha = mod.sha256_file(tmp_path / "ledger.in") if (tmp_path / "ledger.in").exists() else "0" * 64
    return ["--allow-cleanroom-production", "--expect-ledger-hash", ledger_sha, "--out", str(tmp_path / "out"),
            "--target-dsn", TARGET, "--admin-dsn", ADMIN, "--operator-email", "op@example.invalid",
            "--expect-plan-sha256", PLAN, *[a for k, v in H.items() for a in ("--expect-input-sha256", f"{k}={v}")],
            *extra]


@pytest.mark.parametrize("drop", ["--target-dsn", "--admin-dsn", "--operator-email", "--expect-plan-sha256"])
def test_production_needs_every_argument(tmp_path, drop, capsys) -> None:
    mod = load_script()
    args = _prod_args(mod, tmp_path, *_inputs(tmp_path))
    i = args.index(drop)
    del args[i:i + 2]
    assert mod.main(args) == 2
    assert drop in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_production_needs_confirmations_every_input_hash_and_a_backup_dir_to_apply(tmp_path, capsys) -> None:
    mod = load_script()
    inputs = _inputs(tmp_path)
    no_conf = inputs[:inputs.index("--confirmations")]
    assert mod.main(_prod_args(mod, tmp_path, *no_conf)) == 2
    args = _prod_args(mod, tmp_path, *inputs)
    i = args.index("--expect-input-sha256")
    assert mod.main(args[:i] + args[i + 2:]) == 2  # one input hash missing
    assert mod.main(_prod_args(mod, tmp_path, *inputs, "--apply")) == 2
    assert "--backup-dir" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_production_arguments_are_refused_without_the_production_flag(tmp_path) -> None:
    mod = load_script()
    assert mod.main(["--expect-ledger-hash", "0" * 64, "--out", str(tmp_path / "o"),
                     "--expect-input-sha256", f"policy={'a' * 64}"]) == 2
    assert mod.main(["--expect-ledger-hash", "0" * 64, "--out", str(tmp_path / "o"), "--backup-dir", str(tmp_path)]) == 2


def test_apply_without_the_production_flag_still_refuses_origenlab_clean(tmp_path) -> None:
    mod = load_script()
    with pytest.raises(SystemExit) as err:
        mod.main(["--apply", "--expect-ledger-hash", "0" * 64, "--expect-plan-sha256", PLAN, "--out",
                  str(tmp_path / "o"), "--target-dsn", TARGET, "--admin-dsn", ADMIN, "--operator-email", "x@y.z"])
    assert err.value.code == 5
    assert not (tmp_path / "o").exists()


def _stub_main(mod, monkeypatch, preflight: dict, calls: list[str]) -> None:
    monkeypatch.setattr(mod, "load_inputs", lambda args: {
        "ledger_rows": [], "identity_docs": {}, "staged_by_email": {}, "printed_in_text": {}, "blocked": {},
        "blocked_opps": {}, "confirmations": {}})
    monkeypatch.setattr(mod, "reconcile_evidence", lambda *a: [])
    monkeypatch.setattr(mod, "stage_evidence", lambda *a, **k: {"mode": "dry-run"})
    monkeypatch.setattr(mod, "read_state", lambda *a: (qp.TargetState(), {}))
    monkeypatch.setattr(mod, "production_preflight", lambda *a: preflight)
    monkeypatch.setattr(mod, "apply", lambda *a, **k: calls.append("apply") or 0)
    monkeypatch.setattr(mod, "apply_production", lambda *a, **k: calls.append("apply_production") or 0)


@pytest.mark.parametrize("apply_flag", [[], ["--apply"]])
def test_a_refused_preflight_stops_before_any_write(tmp_path, monkeypatch, apply_flag) -> None:
    mod = load_script()
    calls: list[str] = []
    _stub_main(mod, monkeypatch, {"passed": False, "refused": ["migration_recorded"], "fingerprint_sha256": "f"}, calls)
    args = _prod_args(mod, tmp_path, *_inputs(tmp_path), *apply_flag,
                      *(["--backup-dir", str(tmp_path)] if apply_flag else []))
    # The stub plan's hash is not PLAN: the preflight refuses before the plan check is reached.
    assert mod.main(args) == prod.EXIT_PREFLIGHT_REFUSED
    assert calls == []


def test_a_passing_production_apply_goes_through_the_production_path_only(tmp_path, monkeypatch) -> None:
    mod = load_script()
    calls: list[str] = []
    _stub_main(mod, monkeypatch, {"passed": True, "refused": [], "fingerprint_sha256": "f"}, calls)
    inputs = _inputs(tmp_path)
    probe = tmp_path / "probe"
    # First a production dry run tells us the stub plan's hash.
    assert mod.main(_prod_args(mod, tmp_path, *inputs)) == 0
    plan = (tmp_path / "out" / "intent.sha256").read_text().strip()
    args = _prod_args(mod, tmp_path, *inputs, "--apply", "--backup-dir", str(tmp_path))
    args[args.index("--out") + 1] = str(probe)
    args[args.index("--expect-plan-sha256") + 1] = plan
    assert mod.main(args) == 0
    assert calls == ["apply_production"]


# ------------------------------------------------------------------ the production apply


class FakeLock:
    def __init__(self, others=(), fail=False):
        self.others, self.fail = list(others), fail

    def __call__(self, dsn):
        return self

    def __enter__(self):
        if self.fail:
            raise prod.ProductionRefused("another quotation import holds the import advisory lock")
        return self

    def __exit__(self, *a):
        return None

    def other_sessions(self):
        return self.others


def _fp(sha: str, **tables) -> dict:
    return {"sha256": sha, "tables": {k: {"rows": v, "md5": f"{k}{v}"} for k, v in tables.items()}}


PREFLIGHT = {"passed": True, "refused": [], "fingerprint_sha256": "same", "domain_event_max_position": 10,
             "operator": {"id": OP_ID, "role": "admin", "status": "active"}}


def _setup_apply(mod, monkeypatch, tmp_path, *, lock=None, procs=(), fps=None, backup_error=None, events=None,
                 apply_code=0, new_writes=3):
    calls: list = []

    def backup(d, repo, stamp):
        calls.append("backup")
        if backup_error:
            raise prod.ProductionRefused(backup_error)
        return {"path": "p", "sha256": "s"}

    def fake_apply(args, intent, manifest, prefix, run, *, production_operator_id=None):
        calls.append(("apply", production_operator_id))
        with (args.out / "apply_log.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["command_receipt_id", "replayed"])
            w.writeheader()
            w.writerows([{"command_receipt_id": f"r{i}", "replayed": False} for i in range(new_writes)])
        run["apply"] = {"new_writes": new_writes, "events_written": 5, "refused_opportunities": 0}
        return apply_code

    fps = iter(fps or [_fp("same", **{"crm.domain_event": 10, "evidence.source_record": 1, "evidence.assertion": 1}),
                       _fp("after", **{"crm.domain_event": 15, "evidence.source_record": 2, "evidence.assertion": 3})])
    monkeypatch.setattr(prod, "take_backup", backup)
    monkeypatch.setattr(prod, "ImportLock", lock or FakeLock())
    monkeypatch.setattr(prod, "scan_processes", lambda *a, **k: {"processes": list(procs)})
    monkeypatch.setattr(prod, "fingerprint", lambda dsn: next(fps))
    monkeypatch.setattr(prod, "events_after", lambda *a: events or {"owned_by_run": 5, "not_owned_by_run": 0})
    monkeypatch.setattr(mod, "apply", fake_apply)
    out = tmp_path / "out"
    out.mkdir()
    args = SimpleNamespace(backup_dir=tmp_path, admin_dsn=ADMIN, out=out)
    run = {"state_before_totals": {"commands_would_write": 3, "source_records_would_write": 1,
                                   "assertions_would_write": 2}}
    return calls, args, run


def test_the_production_apply_backs_up_then_rechecks_then_applies_as_the_registered_operator(tmp_path, monkeypatch) -> None:
    mod = load_script()
    calls, args, run = _setup_apply(mod, monkeypatch, tmp_path)
    assert mod.apply_production(args, {}, Path("m"), b"", run, PREFLIGHT) == 0
    assert calls == ["backup", ("apply", OP_ID)]
    assert run["post_apply_problems"] == []
    assert (args.out / "production_report.json").is_file() and (args.out / "fingerprint_after.json").is_file()


@pytest.mark.parametrize("case", ["backup", "lock", "session", "process", "moved"])
def test_the_production_apply_refuses_before_the_first_write(tmp_path, monkeypatch, case) -> None:
    mod = load_script()
    kw = {"backup": {"backup_error": "pg_dump failed"}, "lock": {"lock": FakeLock(fail=True)},
          "session": {"lock": FakeLock(others=[{"pid": 7}])}, "process": {"procs": [{"pid": 8}]},
          "moved": {"fps": [_fp("changed")]}}[case]
    calls, args, run = _setup_apply(mod, monkeypatch, tmp_path, **kw)
    code = mod.apply_production(args, {}, Path("m"), b"", run, PREFLIGHT)
    assert code == (prod.EXIT_BACKUP_FAILED if case == "backup" else prod.EXIT_PREFLIGHT_REFUSED)
    assert not any(isinstance(c, tuple) for c in calls)  # apply never ran
    assert run["aborted"]


@pytest.mark.parametrize(("kw", "needle"), [
    ({"new_writes": 2}, "new command writes"),
    ({"events": {"owned_by_run": 5, "not_owned_by_run": 1}}, "no receipt of this run owns"),
    ({"events": {"owned_by_run": 4, "not_owned_by_run": 0}}, "events owned by the run"),
    ({"fps": [_fp("same", **{"crm.domain_event": 10, "evidence.source_record": 1, "evidence.assertion": 1,
                             "outbound.contact_control": 4}),
              _fp("after", **{"crm.domain_event": 15, "evidence.source_record": 2, "evidence.assertion": 3,
                              "outbound.contact_control": 5})]}, "tables outside the import changed"),
    ({"fps": [_fp("same", **{"crm.domain_event": 10, "evidence.source_record": 1, "evidence.assertion": 1}),
              _fp("after", **{"crm.domain_event": 15, "evidence.source_record": 1, "evidence.assertion": 3})]},
     "evidence.source_record grew by 0"),
])
def test_a_production_apply_the_database_does_not_account_for_fails(tmp_path, monkeypatch, kw, needle) -> None:
    mod = load_script()
    _, args, run = _setup_apply(mod, monkeypatch, tmp_path, **kw)
    assert mod.apply_production(args, {}, Path("m"), b"", run, PREFLIGHT) == prod.EXIT_POST_APPLY_MISMATCH
    assert any(needle in p for p in run["post_apply_problems"])


def test_a_refused_command_keeps_its_own_exit_code(tmp_path, monkeypatch) -> None:
    mod = load_script()
    _, args, run = _setup_apply(mod, monkeypatch, tmp_path, apply_code=7)
    assert mod.apply_production(args, {}, Path("m"), b"", run, PREFLIGHT) == 7


def test_production_never_registers_an_operator(tmp_path, monkeypatch) -> None:
    mod = load_script()
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("{}\n")
    registered: list = []
    monkeypatch.setattr(mod, "register_operator", lambda *a: registered.append(a))
    monkeypatch.setattr(mod, "domain_event_total", lambda dsn: 0)
    monkeypatch.setattr(mod, "Executor", lambda *a, **k: SimpleNamespace(run=lambda o, log: None))
    args = SimpleNamespace(ledger=ledger, expect_ledger_hash=mod.sha256_file(ledger), admin_dsn="x", target_dsn="x",
                           operator_email="op@example.invalid", out=tmp_path)
    intent = {"evidence_scope": qp.EVIDENCE_SCOPE_READY, "evidence_manifest": {"records": []}, "opportunities": []}
    run: dict = {}
    assert mod.apply(args, intent, tmp_path / "m", b"", run, production_operator_id=OP_ID) == 0
    assert registered == [] and run["operator"] == {"registered_now": False}


# ------------------------------------------------------------------ database-backed (opt-in)

from v2_command_harness import build_disposable_database, needs_db  # noqa: E402


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


@needs_db
def test_the_facts_are_read_from_a_real_database_and_judged(disposable_database) -> None:
    """Against a disposable db: no migration ledger → refused; the lock is exclusive; fingerprint is stable."""
    name = disposable_database.rsplit("/", 1)[1]
    facts = prod.read_database_facts(disposable_database, "nobody@example.invalid")
    assert facts["current_database"] == name
    assert facts["number_origin_column"] is True  # the migration's DDL is in the chain
    assert facts["migrations"] == []  # but a disposable db records no ledger rows
    assert facts["operator"] is None and facts["advisory_lock_free"] is True
    refusals = prod.refused(prod.evaluate(
        target_dsn=disposable_database, admin_dsn=disposable_database, plan_sha=PLAN, expected_plan=PLAN,
        ledger_sha=LEDGER, expected_ledger=LEDGER, input_hashes=H, expected_inputs=H, db=facts,
        container=good_container(system_identifier=facts["system_identifier"]), processes={"processes": []},
        crontab={"lines": []}, plan_keys=[], confirmation_operator_ids={OP_ID}, evidence_unclean=0,
        scope_violations=0, expected_db=name))
    assert "migration_recorded" in refusals and "operator_registered" in refusals
    assert "import_advisory_lock_free" not in refusals
    with prod.ImportLock(disposable_database) as lock:
        assert prod.read_database_facts(disposable_database, "x@y.z")["advisory_lock_free"] is False
        with pytest.raises(prod.ProductionRefused, match="advisory lock"):
            prod.ImportLock(disposable_database).__enter__()
        assert all(s["pid"] != lock.pid for s in lock.other_sessions())
    assert prod.read_database_facts(disposable_database, "x@y.z")["advisory_lock_free"] is True
    assert prod.fingerprint(disposable_database)["sha256"] == prod.fingerprint(disposable_database)["sha256"]

