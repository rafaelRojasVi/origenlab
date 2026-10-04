from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
try:
    import hosted_data_load_io as io_
finally:
    sys.path.remove(str(_SCRIPTS))


def _hosted():
    return SimpleNamespace(mode="hosted", host="aws-0-sa-east-1.pooler.supabase.com", hostaddr="3.3.3.3",
                           port=5432, user="origenlab_migrator.abc", database="postgres",
                           sslmode="verify-full", sslrootcert="/x/ca.crt", password="s3cret")


def test_source_conninfo_is_loopback_cleanroom():
    ci = io_.source_conninfo()
    assert "host=127.0.0.1" in ci and "port=54332" in ci and "dbname=origenlab_clean" in ci


def test_scratch_conninfo_targets_named_database():
    ci = io_.scratch_conninfo("scratch_db")
    assert "host=127.0.0.1" in ci and "dbname=scratch_db" in ci and "user=supabase_admin" in ci


def test_conninfo_for_pins_address_and_verify_full():
    ci = io_.conninfo_for(_hosted())
    assert "hostaddr=3.3.3.3" in ci and "sslmode=verify-full" in ci and "sslrootcert=/x/ca.crt" in ci
    assert "password=s3cret" in ci


def test_conninfo_for_local_mode_is_loopback_scratch():
    t = SimpleNamespace(mode="local", database="scratch_db")
    ci = io_.conninfo_for(t)
    assert "host=127.0.0.1" in ci and "dbname=scratch_db" in ci and "sslmode=disable" in ci


def test_redact_hides_password_host_and_dsn():
    s = "postgresql://u:s3cret@aws-0-sa-east-1.pooler.supabase.com:5432/db password=s3cret"
    r = io_.redact(s)
    assert "s3cret" not in r and "pooler.supabase.com" not in r


def test_sidecar_sha_matches_file(tmp_path: Path):
    p = tmp_path / "x.dump"
    p.write_bytes(b"abc")
    assert io_.write_sha256_sidecar(p).endswith("x.dump.sha256")
    digest = (tmp_path / "x.dump.sha256").read_text().split()[0]
    assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def _fake_proc(tmp_path: Path, entries: dict[str, str]) -> Path:
    for pid, cmd in entries.items():
        d = tmp_path / pid
        d.mkdir()
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())
    (tmp_path / "self").mkdir()
    return tmp_path


def test_scan_processes_reports_only_real_clients(tmp_path: Path):
    proc = _fake_proc(tmp_path, {
        "101": "postgres: main: supabase_admin origenlab_clean 172.18.0.1(53950) idle",
        "102": "psql dbname=origenlab_clean",
        "103": "python hosted_data_load.py origenlab_clean",
        str(os.getpid()): "python origenlab_clean something",
        "104": "bash unrelated",
    })
    found = io_._scan_processes(proc)
    assert len(found) == 1 and found[0].startswith("psql")


def test_pg_dump_command_keeps_secret_out_of_argv():
    argv, env = io_._pg_dump_command(_hosted(), schema_only=True)
    assert not any("s3cret" in a for a in argv)
    assert env["PGPASSWORD"] == "s3cret" and env["PGHOSTADDR"] == "3.3.3.3"
    assert "PGPASSWORD" in argv and "--schema-only" in argv


def test_pg_dump_command_local_has_no_extra_env():
    argv, env = io_._pg_dump_command(SimpleNamespace(mode="local", database="scratch_db"), schema_only=False)
    assert env == {} and "scratch_db" in argv


def _full_hosted():
    t = _hosted()
    t.project_ref = "abcdefghijklmnopqrst"
    t.user = "origenlab_migrator.abcdefghijklmnopqrst"
    return t


def test_redact_extra_masks_login_ref_and_ip():
    t = _full_hosted()
    s = f'password authentication failed for user "{t.user}" ref {t.project_ref} at (3.3.3.3)'
    r = io_.redact(s, io_.target_secrets(t))
    assert t.user not in r and t.project_ref not in r and "3.3.3.3" not in r


def test_redact_keeps_ordinary_text_and_local_has_no_secrets():
    assert io_.redact("crm.quote 412 rows", io_.target_secrets(_full_hosted())) == "crm.quote 412 rows"
    assert io_.target_secrets(SimpleNamespace(mode="local")) == ()


def test_pg_dump_failure_leaks_nothing_and_removes_partial(tmp_path: Path, monkeypatch):
    t = _full_hosted()
    t.sslrootcert = "/x/ca.crt"
    err = f'connection to server at "{t.host}" (3.3.3.3), port 5432 failed: password authentication failed for user "{t.user}"'

    def fake_run(argv, **kw):
        if "stdout" in kw:
            kw["stdout"].write(b"partial")
        return SimpleNamespace(returncode=1, stderr=err.encode())

    monkeypatch.setattr(io_.subprocess, "run", fake_run)
    out = tmp_path / "d" / "t.dump"
    try:
        io_.pg_dump_target(t, out)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        msg = str(e)
    for secret in (t.user, t.project_ref, "3.3.3.3", t.host, "s3cret"):
        assert secret not in msg
    assert not out.exists()


def test_invariant_names_cover_every_table_and_the_globals():
    plan = {"tables": [{"name": t, "count": 1, "hash": "h", "pk": ["id"], "columns": [["id", "uuid"]]} for t in
                       ("comms.mailbox", "crm.domain_event")],
            "payload_rows_with_local_uuid": 44, "sequence": {"name": "crm.domain_event_stream_position_seq", "target_last_value": 23095},
            "roster_hash": "r", "send_control_hash": "s", "campaign_block_hash": "c", "remap": {"to": "x", "from": ["a", "b"]}}
    names = io_.invariant_names(plan)
    assert "rows_match:comms.mailbox" in names and "rows_match:crm.domain_event" in names
    assert {"payload_hosted_uuid_rows", "payload_local_uuid_rows_zero", "no_foreign_operator_refs",
            "roster_unchanged", "send_control_unchanged", "campaign_block_unchanged", "send_flags_false",
            "all_user_triggers_enabled", "no_deferrable_fks", "sequence_set"} <= set(names)


def test_post_copy_invariants_emit_exactly_the_declared_names():
    class Cur:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return (1, "h")

    plan = {"tables": [{"name": t, "count": 1, "hash": "h", "pk": ["id"], "columns": [["id", "uuid"]]} for t in
                       ("comms.mailbox", "crm.domain_event")],
            "payload_rows_with_local_uuid": 44, "sequence": {"name": "s", "target_last_value": 1},
            "roster_hash": "r", "send_control_hash": "s", "campaign_block_hash": "c", "remap": {"to": "x", "from": ["a", "b"]}}
    got = [c["check"] for c in io_.post_copy_invariants(Cur(), plan, "x")]
    assert got == io_.invariant_names(plan)


def _plan2():
    return {"tables": [{"name": "a", "count": 2, "hash": "ha"}, {"name": "b", "count": 3, "hash": "hb"}]}


def test_verify_classifies_loaded_empty_and_partial():
    assert io_.classify_target_state(_plan2(), {"a": (2, "ha"), "b": (3, "hb")}) == "loaded"
    assert io_.classify_target_state(_plan2(), {"a": (0, ""), "b": (0, "")}) == "empty"
    assert io_.classify_target_state(_plan2(), {"a": (2, "ha"), "b": (0, "")}) == "partial"


def test_verify_classifies_partial_state_as_incident():
    # a hash drift with full counts is still partial: somebody wrote after the load
    assert io_.classify_target_state(_plan2(), {"a": (2, "ha"), "b": (3, "xx")}) == "partial"


def test_classify_ignores_observed_tables_outside_the_plan():
    assert io_.classify_target_state(_plan2(), {"a": (2, "ha"), "b": (3, "hb"), "zzz": (9, "q")}) == "loaded"
    assert io_.classify_target_state(_plan2(), {"a": (0, ""), "b": (0, ""), "zzz": (9, "q")}) == "empty"


def test_rows_outside_load_flags_new_rows_and_singleton_drift():
    plan = _plan2()
    ok = {"a": 2, "b": 3, "outbound.send_control": 1, "outbound.campaign_block": 1, "crm.task": 0}
    assert io_.rows_outside_load(plan, ok) == {}
    bad = {**ok, "crm.task": 4, "outbound.campaign_block": 0}
    assert io_.rows_outside_load(plan, bad) == {"crm.task": 4, "outbound.campaign_block": 0}


# ---- Task 7: test-only switches ----------------------------------------------------------------

import pytest  # noqa: E402

_GOOD = "origenlab_test_0123abcd"


@pytest.mark.parametrize("bad", ["origenlab_clean", "postgres", "origenlab_test_XYZ", "origenlab_test_0123abcdx", ""])
def test_source_db_override_ignores_non_disposable_names(monkeypatch, bad):
    monkeypatch.setenv("OL_HOSTED_LOAD_SOURCE_DB", bad)
    assert "dbname=origenlab_clean" in io_.source_conninfo()


def test_source_db_override_accepts_disposable_name(monkeypatch):
    monkeypatch.setenv("OL_HOSTED_LOAD_SOURCE_DB", _GOOD)
    assert f"dbname={_GOOD}" in io_.source_conninfo()


def _tf(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "t.env"
    f.write_text(text)
    f.chmod(0o600)
    return f


def test_local_scratch_target_needs_matching_name_and_only_key(tmp_path):
    ok = _tf(tmp_path, f"# c\nOL_HOSTED_LOCAL_SCRATCH={_GOOD}\n")
    t = io_.hosted_target(Path("/"), {"OL_HOSTED_LOCAL_SCRATCH": _GOOD}, ok)
    assert (t.mode, t.database, t.host, t.port, t.sslmode) == ("local", _GOOD, "127.0.0.1", 54332, "disable")
    for env_name, text in [("origenlab_clean", "OL_HOSTED_LOCAL_SCRATCH=origenlab_clean\n"),
                           ("origenlab_test_XYZ", "OL_HOSTED_LOCAL_SCRATCH=origenlab_test_XYZ\n"),
                           (_GOOD, f"OL_HOSTED_LOCAL_SCRATCH={_GOOD}\nOL_HOSTED_HOST=x\n"),
                           (_GOOD, "OL_HOSTED_LOCAL_SCRATCH=origenlab_test_ffffffff\n")]:
        assert io_._local_scratch_target({"OL_HOSTED_LOCAL_SCRATCH": env_name}, _tf(tmp_path, text)) is None
    assert io_._local_scratch_target({}, ok) is None


def test_host_facts_for_local_target_keep_real_file_checks(tmp_path, monkeypatch):
    f = _tf(tmp_path, f"OL_HOSTED_LOCAL_SCRATCH={_GOOD}\n")
    f.chmod(0o644)
    t = io_.hosted_target(Path("/"), {"OL_HOSTED_LOCAL_SCRATCH": _GOOD}, f)
    monkeypatch.setattr(io_.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    facts = io_.read_host_facts(Path("/"), f, t, proc=_fake_proc(tmp_path, {}))
    assert facts["target_file_mode"] == 0o644  # the mode check stays real
    assert (facts["route"], facts["port"], facts["sslmode"], facts["ca_exists"]) == ("supavisor-session", 5432, "verify-full", True)


def test_target_ledger_is_read_before_the_role_switch(monkeypatch):
    """origenlab_owner cannot read supabase_migrations (found by the e2e): the ledger is read as the login role."""
    log: list[str] = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, params=None): log.append(str(q))
        def fetchone(self): raise StopIteration

    class Conn:
        def cursor(self): return Cur()

    monkeypatch.setattr(io_, "_ledger_or_none", lambda cur: log.append("LEDGER") or ["1"])
    monkeypatch.setattr(io_, "apply_session_settings", lambda cur, local=False: None)
    with pytest.raises(StopIteration):
        io_.read_target_facts(Conn(), SimpleNamespace(project_ref="r", host="h"), True)
    assert log.index("LEDGER") < log.index("set local role origenlab_owner")


class _FpCur:
    """Fake cursor: answers the schema list, then one canned row set per fingerprint category."""
    def __init__(self, schemas, rows):
        self.schemas, self.rows, self.log, self._res = schemas, rows, [], []
        self._cats = iter(io_._FINGERPRINT_QUERIES)

    def execute(self, q, params=None):
        self.log.append(str(q))
        if "from pg_namespace where nspowner" in q:
            self._res = [(s,) for s in self.schemas]
        elif q.startswith("set local"):
            self._res = []
        else:
            self._res = self.rows.get(next(self._cats), [])

    def fetchall(self):
        return self._res


def test_schema_fingerprint_shape_and_hashes():
    import hashlib
    cur = _FpCur(["crm", "comms"], {"tables": [("crm.b", "r", "f", "f"), ("crm.a", "r", "t", "f"), (None, "x", "y", "z")]})
    fp = io_.schema_fingerprint(cur)
    assert cur.log[0] == "set local search_path = pg_catalog"
    assert fp["schemas"] == ["crm", "comms"]
    assert set(fp["categories"]) == set(io_._FINGERPRINT_QUERIES) == set(fp["lines"])
    assert set(fp["categories"]) == {"tables", "columns", "constraints", "indexes", "triggers", "functions",
                                     "policies", "views", "sequences", "types"}
    assert fp["lines"]["tables"] == ["crm.a|r|t|f", "crm.b|r|f|f", "|x|y|z"]  # sorted, NULL as empty
    assert fp["categories"]["tables"] == hashlib.sha256("\n".join(fp["lines"]["tables"]).encode()).hexdigest()
    assert fp["categories"]["views"] == hashlib.sha256(b"").hexdigest()


def test_schema_fingerprint_excludes_acls_and_owners():
    text = " ".join(io_._FINGERPRINT_QUERIES.values()).lower()
    for banned in ("relacl", "proacl", "relowner", "proowner", "nspacl", "last_value"):
        assert banned not in text


def test_fingerprint_diff_lines_caps_and_signs():
    a = {"schemas": ["crm"], "lines": {"columns": [f"c{i}" for i in range(30)], "tables": ["t"]}}
    b = {"schemas": ["crm"], "lines": {"columns": ["c0", "extra"], "tables": ["t"]}}
    d = io_.fingerprint_diff_lines(a, b)
    assert set(d) == {"columns"} and len(d["columns"]) == 10
    assert d["columns"][0] == "- c1" and "+ extra" not in d["columns"]
    d = io_.fingerprint_diff_lines({"schemas": [], "lines": {"columns": ["x"]}}, {"schemas": [], "lines": {"columns": ["x", "crm.note|e2e_extra"]}})
    assert d == {"columns": ["+ crm.note|e2e_extra"]}


def test_ledger_is_none_when_unreadable_and_never_selected():
    log = []

    class Cur:
        def __init__(self, exists, readable): self.exists, self.readable, self.val = exists, readable, None
        def execute(self, q, params=None):
            log.append(q)
            self.val = self.exists if "to_regclass" in q else self.readable
        def fetchone(self): return (self.val,)

    assert io_._ledger_or_none(Cur(True, False)) is None
    assert io_._ledger_or_none(Cur(False, True)) is None
    assert not any("order by version" in q for q in log)
    assert any("has_schema_privilege" in q and "has_table_privilege" in q for q in log)
