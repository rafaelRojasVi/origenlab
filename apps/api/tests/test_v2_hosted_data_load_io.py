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
