from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

#: A made-up pooler login (`<role>.<20-letter project ref>`), built at run time so the shape
#: the public-repo hygiene check looks for never sits in a tracked file.
FAKE_POOLER_USER = "origenlab_migrator." + "abcdefghijklmnopqrst"

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


_PG_NAMES = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD", "PGSSLMODE", "PGSSLROOTCERT")


def test_pg_dump_command_keeps_every_hosted_value_out_of_argv():
    """M5: host, address, port, login, database, password and TLS settings travel by NAME only."""
    t = _hosted()
    t.database = "postgres_e2e_db"
    argv, env = io_._pg_dump_command(t, schema_only=True)
    for value in (t.host, t.hostaddr, str(t.port), t.user, t.database, t.password, "/tmp/hosted-ca.crt"):
        assert not any(value in a for a in argv), value
    assert set(env) == set(_PG_NAMES)
    assert (env["PGHOST"], env["PGHOSTADDR"], env["PGPORT"], env["PGUSER"], env["PGDATABASE"], env["PGPASSWORD"]) == \
        (t.host, "3.3.3.3", "5432", t.user, "postgres_e2e_db", "s3cret")
    assert env["PGSSLMODE"] == "verify-full" and env["PGSSLROOTCERT"] == "/tmp/hosted-ca.crt"
    exec_part = argv[:argv.index(io_.CONTAINER)]
    assert exec_part == ["docker", "exec", *(a for n in env for a in ("-e", n))]
    assert "--schema-only" in argv and "--role=origenlab_owner" in argv


def test_pg_dump_command_local_dumps_as_the_hosted_login():
    """TEST-ONLY local target: the container's pg_dump connects as origenlab_migrator, like hosted."""
    argv, env = io_._pg_dump_command(SimpleNamespace(mode="local", database="origenlab_test_0123abcd"), schema_only=False)
    assert env == {"PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "origenlab_migrator",
                   "PGDATABASE": "origenlab_test_0123abcd"}
    assert "origenlab_test_0123abcd" not in argv and "supabase_admin" not in argv and "--schema-only" not in argv


def test_connect_target_assumes_the_hosted_login_only_for_a_local_target(monkeypatch):
    executed: list[str] = []

    class Conn:
        def execute(self, q):
            executed.append(q)

    monkeypatch.setattr(io_.psycopg, "connect", lambda info, **k: Conn())
    io_.connect_target(SimpleNamespace(mode="local", database="origenlab_test_0123abcd"))
    assert executed == ["SET SESSION AUTHORIZATION origenlab_migrator"]
    executed.clear()
    io_.connect_target(_hosted())
    assert executed == []


def _full_hosted():
    t = _hosted()
    t.project_ref = "abcdefghijklmnopqrst"
    t.user = FAKE_POOLER_USER
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
            self.q = str(sql)

        def fetchone(self):
            return ("h",) if self.q.startswith("select coalesce(md5") else (1, "h")

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


def test_target_ledger_and_sessions_are_read_before_the_role_switch(monkeypatch):
    """origenlab_owner cannot read supabase_migrations (found by the e2e), and after the switch
    pg_stat_activity hides the login's sessions (I2): both are read as the login role."""
    log: list[str] = []

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, params=None): log.append(str(q))
        def fetchone(self): raise StopIteration

    class Conn:
        def cursor(self): return Cur()

    monkeypatch.setattr(io_, "_ledger_or_none", lambda cur: log.append("LEDGER") or ["1"])
    monkeypatch.setattr(io_, "_other_sessions", lambda cur, same_role_only: log.append(f"SESSIONS {same_role_only}") or [])
    monkeypatch.setattr(io_, "apply_session_settings", lambda cur, local=False: None)
    with pytest.raises(StopIteration):
        io_.read_target_facts(Conn(), SimpleNamespace(project_ref="r", host="h"), True)
    role = log.index("set local role origenlab_owner")
    assert log.index("LEDGER") < role and log.index("SESSIONS True") < role


def _sessions_sql(same_role_only: bool) -> str:
    class Cur:
        def execute(self, q, params=None): self.q = q
        def fetchall(self): return []
    cur = Cur()
    io_._other_sessions(cur, same_role_only)
    return " ".join(cur.q.split())


def test_target_session_guard_ignores_idle_pool_backends_and_fails_closed_on_hidden_rows():
    q = _sessions_sql(True)
    assert "usename = session_user" in q and "state is distinct from 'idle'" in q
    assert "coalesce(backend_type, 'client backend') = 'client backend'" in q


def test_source_session_guard_refuses_every_other_client_session():
    q = _sessions_sql(False)
    assert "session_user" not in q and "idle" not in q and "backend_type = 'client backend'" in q


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


class _LedgerCur:
    """Fake cursor: the OID probe answers ``probe`` (a row, or None when the table is absent)."""
    def __init__(self, probe):
        self.probe, self.log, self._res = probe, [], None

    def execute(self, q, params=None):
        self.log.append(q)
        self._res = [("20260930120000",)] if "order by version" in q else self.probe

    def fetchone(self):
        return self._res

    def fetchall(self):
        return self._res


def test_ledger_is_none_when_absent_or_unreadable_and_never_selected():
    for probe in (None, (False,)):
        cur = _LedgerCur(probe)
        assert io_._ledger_or_none(cur) is None
        assert not any("order by version" in q for q in cur.log)
    cur = _LedgerCur((True,))
    assert io_._ledger_or_none(cur) == ["20260930120000"]


def test_ledger_probe_never_resolves_a_name_through_the_schema_acl():
    """C1: to_regclass / ::regclass / has_table_privilege('<name>') RAISE "permission denied for schema
    supabase_migrations" for the hosted login; the probe must go through OIDs only."""
    cur = _LedgerCur(None)
    io_._ledger_or_none(cur)
    probe = cur.log[0]
    assert "to_regclass" not in probe and "::regclass" not in probe
    assert "has_schema_privilege(n.oid" in probe and "has_table_privilege(c.oid" in probe
    assert "'supabase_migrations.schema_migrations'" not in probe


# ---- final review: I1 roster identity columns ----------------------------------------------------

_THROTTLE = ("failed_attempts", "lockout_count", "locked_until", "last_failed_at", "pin_attempt_id", "pin_attempt_xact",
             "pin_attempt_operator_id", "pin_attempt_nonce", "pin_set_at", "created_at", "updated_at")


def test_roster_hash_reads_identity_columns_only():
    """I1: a failed PIN writes the throttle columns; the roster hash must not see them."""
    class Cur:
        def __init__(self): self.log = []
        def execute(self, q, params=None): self.log.append(q)
        def fetchone(self): return ("d",)

    cur = Cur()
    assert io_.roster_hash(cur) == "ddd"
    assert len(cur.log) == 3
    for q, (table, cols) in zip(cur.log, io_.hp.ROSTER_IDENTITY_COLUMNS.items()):
        assert f"select {io_.hp.qcols(cols)} from {table}" in q
        assert "*" not in q.split(" from ")[0].replace("count(*)", "")
        for col in _THROTTLE:
            assert f'"{col}"' not in q


# ---- final review: I4 quoted conninfo, I6 keepalives ---------------------------------------------

from psycopg.conninfo import conninfo_to_dict  # noqa: E402


def test_conninfo_round_trips_a_password_with_space_quote_and_backslash():
    t = _hosted()
    t.password = "p a's\\s w\\'rd"
    d = conninfo_to_dict(io_.conninfo_for(t))
    assert d["password"] == t.password
    assert (d["host"], d["hostaddr"], d["port"], d["user"], d["sslmode"], d["sslrootcert"]) == \
        (t.host, "3.3.3.3", "5432", t.user, "verify-full", "/x/ca.crt")


def test_hosted_conninfo_carries_keepalives_and_connect_timeout():
    d = conninfo_to_dict(io_.conninfo_for(_hosted()))
    assert {k: d[k] for k in ("keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count",
                              "connect_timeout")} == {"keepalives": "1", "keepalives_idle": "15",
                                                      "keepalives_interval": "5", "keepalives_count": "3",
                                                      "connect_timeout": "20"}


def test_local_and_scratch_conninfo_are_parseable():
    d = conninfo_to_dict(io_.conninfo_for(SimpleNamespace(mode="local", database="origenlab_test_0123abcd")))
    assert (d["host"], d["port"], d["dbname"], d["sslmode"]) == ("127.0.0.1", "54332", "origenlab_test_0123abcd", "disable")
    assert conninfo_to_dict(io_.scratch_conninfo("origenlab_test_0123abcd"))["user"] == "supabase_admin"


def test_redact_masks_a_quoted_password_whole():
    assert io_.redact("password='p a\\'ss' host=x") == "password=*** host=x"


def test_redact_masks_hosted_shapes_before_the_target_is_known():
    """I5: an error raised before the target resolves is redacted by shape alone."""
    s = ('connection to server at "aws-0-sa-east-1.pooler.supabase.com" (1.2.3.4), port 5432 failed: '
         f'FATAL: password authentication failed for user "{FAKE_POOLER_USER}"')
    r = io_.redact(s)
    for leak in ("pooler.supabase.com", "1.2.3.4", "abcdefghijklmnopqrst"):
        assert leak not in r
    assert "<hosted-host>" in r and "<address>" in r and "<hosted-login>" in r and "port 5432" in r
    assert io_.redact("crm.organization_relationship 41 rows") == "crm.organization_relationship 41 rows"


# ---- final review: M1 ------------------------------------------------------------------------------


class _SeqCur:
    def __init__(self, nxt):
        self.nxt, self.log = nxt, []

    def execute(self, q, params=None):
        self.log.append(q.as_string(None) if hasattr(q, "as_string") else q)

    def fetchone(self):
        return (self.nxt,)


def test_restart_sequence_is_transactional_ddl_not_setval():
    cur = _SeqCur(0)
    io_.restart_sequence(cur, 23096)
    assert cur.log == ['ALTER SEQUENCE "crm"."domain_event_stream_position_seq" RESTART WITH 23096']


def test_sequence_set_compares_the_next_value():
    plan = {"tables": [], "payload_rows_with_local_uuid": 0, "sequence": {"name": "s", "target_last_value": 23095},
            "roster_hash": "", "send_control_hash": "", "campaign_block_hash": "", "remap": {"to": "x", "from": ["a"]}}

    class Cur(_SeqCur):
        def fetchone(self):
            q = self.log[-1]
            if q.startswith("select case when is_called"):
                return (self.nxt,)
            return ("",) if "md5" in q else (0,)

    def seq_check(nxt):
        cur = Cur(nxt)
        checks = io_.post_copy_invariants(cur, plan, "x")
        assert any("case when is_called then last_value + 1 else last_value end" in q for q in cur.log)
        return next(c for c in checks if c["check"] == "sequence_set")["ok"]

    assert seq_check(23096) is True
    assert seq_check(23095) is False


# ---- final review: M7 one helper each ------------------------------------------------------------


def test_best_effort_rollback_reports_whether_the_server_confirmed():
    import psycopg

    class Cur:
        def __init__(self, fail): self.fail, self.log = fail, []
        def execute(self, q):
            self.log.append(q)
            if self.fail:
                raise psycopg.OperationalError("connection lost")

    assert io_.best_effort_rollback(Cur(False)) is True
    assert io_.best_effort_rollback(Cur(True)) is False
    assert not hasattr(io_, "_rollback") and not hasattr(io_, "_qcols")


def test_post_copy_invariants_reuse_the_observed_rows_they_are_given():
    class Cur:
        def __init__(self): self.log = []
        def execute(self, q, params=None): self.log.append(str(q))
        def fetchone(self): return ("h",) if self.log[-1].startswith("select coalesce(md5") else (0,)

    plan = {"tables": [{"name": "comms.mailbox", "count": 2, "hash": "h", "pk": ["id"], "columns": [["id", "uuid"]]}],
            "payload_rows_with_local_uuid": 0, "sequence": {"name": "s", "target_last_value": 0},
            "roster_hash": "", "send_control_hash": "", "campaign_block_hash": "", "remap": {"to": "x", "from": ["a"]}}
    cur = Cur()
    checks = io_.post_copy_invariants(cur, plan, "x", {"comms.mailbox": (2, "h")})
    assert checks[0] == {"check": "rows_match:comms.mailbox", "ok": True, "detail": {"count": 2, "expected": 2}}
    assert not any(q.startswith("select count(*), coalesce(md5") for q in cur.log)


def test_one_disposable_name_pattern():
    assert io_.DISPOSABLE_DB.fullmatch("origenlab_test_0123abcd") and not io_.DISPOSABLE_DB.fullmatch("origenlab_clean")
    assert "SCRATCH_NAME" not in (_SCRIPTS / "hosted_data_load.py").read_text()
