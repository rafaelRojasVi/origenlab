"""Full cycle plan -> apply -> verify -> rollback against two disposable databases on the clean-room
container: source = a restored copy of origenlab_clean, target = a fresh chain + a test roster.
Nothing here touches origenlab_clean or the hosted project.

Run with ORIGENLAB_HOSTED_LOAD_E2E=1 (needs the origenlab_dev_db container, a few minutes)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("ORIGENLAB_HOSTED_LOAD_E2E") != "1",
                                reason="needs the dev container; set ORIGENLAB_HOSTED_LOAD_E2E=1")
API = Path(__file__).resolve().parents[1]
SCRATCH = API / "scripts" / "hosted_data_load_scratch.sh"
DB_NAME = re.compile(r"origenlab_test_[0-9a-f]{8}")
EXIT_REFUSED, EXIT_FAILED = 11, 12


def _scratch(cmd: str, *args: str) -> str:
    return subprocess.run(["bash", str(SCRATCH), cmd, *args], capture_output=True, text=True, check=True).stdout.strip()


def _drop(name: str) -> None:
    if DB_NAME.fullmatch(name):
        subprocess.run(["bash", str(SCRATCH), "drop", name], check=False)


def _psql(db: str, sql: str) -> str:
    return subprocess.run(["docker", "exec", "origenlab_dev_db", "psql", "-U", "supabase_admin", "-d", db, "-At", "-c", sql],
                          capture_output=True, text=True, check=True).stdout.strip()


def _roster(tgt: str, tmp_path: Path) -> str:
    """Build the hosted roster shape with the real roster tool: dry run for the change count, then apply."""
    roster, pins = tmp_path / "profiles.json", tmp_path / "pins.json"
    roster.write_text(json.dumps({"principal": {"email": "contacto@example.test", "provider_subject": "e2e-sub"},
                                  "profiles": [{"key": "rafael", "display_name": "Rafael", "role": "admin", "sort_order": 1},
                                               {"key": "tati", "display_name": "Tati", "role": "admin", "sort_order": 2},
                                               {"key": "karla", "display_name": "Karla", "role": "sales", "sort_order": 3}]}))
    pins.write_text(json.dumps({"rafael": "482913", "tati": "573014", "karla": "604127"}))
    roster.chmod(0o600)
    pins.chmod(0o600)
    env = {**os.environ, "OL_E2E_DSN": f"postgresql://supabase_admin:postgres@127.0.0.1:54332/{tgt}",
           "ORIGENLAB_PROFILE_PIN_PEPPER": "e2e-pepper-0123456789abcdefghijklmnopqrstuv"}
    base = [sys.executable, str(API / "scripts" / "profile_roster.py"), "--roster", str(roster), "--domain", "example.test",
            "--dsn-env", "OL_E2E_DSN", "--pin-file", str(pins)]
    dry = subprocess.run(base, capture_output=True, text=True, env=env, cwd=API)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    pending = re.search(r"pending changes: (\d+)", dry.stdout).group(1)
    done = subprocess.run([*base, "--apply", "--confirm-changes", pending, "--confirm-database", tgt],
                          capture_output=True, text=True, env=env, cwd=API)
    assert done.returncode == 0, done.stdout + done.stderr
    return _psql(tgt, "select id from platform.operator where display_name='Rafael'")


@pytest.fixture()
def databases(tmp_path: Path):
    created: list[str] = []
    try:
        # source: byte-exact clone of origenlab_clean (ledger 41, data, sequences, the 2 local operators)
        src = _scratch("clone-cleanroom")
        created.append(src)
        # target: a fresh chain (seeds send_control and the september hold row itself, as hosted did)
        tgt = _scratch("mint")
        created.append(tgt)
        assert _psql(tgt, "select count(*) from outbound.campaign_block where lifted_at is null") == "1"
        hosted_operator = _roster(tgt, tmp_path)
        target_file = tmp_path / "hosted_target.env"
        target_file.write_text(f"OL_HOSTED_LOCAL_SCRATCH={tgt}\n")
        target_file.chmod(0o600)
        env = {**os.environ, "OL_HOSTED_LOAD_SOURCE_DB": src, "OL_HOSTED_LOCAL_SCRATCH": tgt}
        yield src, tgt, target_file, env, hosted_operator
    finally:
        for name in created:
            _drop(name)


def _cli(env, *args, out: Path):
    # every command writes its dumps and reports under the test's tmp dir, never ~/data
    r = subprocess.run([sys.executable, str(API / "scripts" / "hosted_data_load.py"), *args, "--out", str(out),
                        "--authorize-hosted-connection", "--authorize-supavisor-session-route"],
                       capture_output=True, text=True, env=env, cwd=API)
    print(f"$ hosted_data_load.py {args[0]}  -> exit {r.returncode}\n{r.stdout}{r.stderr}")
    return r


def test_full_cycle_and_injected_failures(databases, tmp_path: Path):
    src, tgt, target_file, env, hosted = databases
    out = tmp_path / "out"
    tf = ["--target-file", str(target_file)]

    def counts():
        return [_psql(tgt, f"select count(*) from {t['name']}") for t in plan["tables"]]

    r = _cli(env, "plan", *tf, "--hosted-operator", hosted, out=out)
    assert r.returncode == 0, r.stdout + r.stderr
    plan_path = next(out.glob("plan-*.json"))
    sha = r.stdout.split("sha256: ")[1].split()[0]
    plan = json.loads(plan_path.read_text())
    assert len(plan["tables"]) == 16 and sum(t["count"] for t in plan["tables"]) > 50_000
    ap = ["--plan", str(plan_path), "--plan-sha256", sha, *tf]

    # injected failure 1: wrong sha -> refused, nothing written
    r = _cli(env, "apply", "--plan", str(plan_path), "--plan-sha256", "0" * 64, *tf, out=out)
    assert r.returncode == EXIT_REFUSED and set(counts()) == {"0"}

    # injected failure 2: a foreign trigger on the target -> refused before any write
    _psql(tgt, "create function public.e2e_noop() returns trigger language plpgsql as $$ begin return new; end $$; "
               "create trigger e2e_extra before insert on crm.quote execute function public.e2e_noop();")
    r = _cli(env, "apply", *ap, out=out)
    assert r.returncode == EXIT_REFUSED and "trigger_inventory_matches_plan:crm.quote" in r.stdout
    assert set(counts()) == {"0"}
    _psql(tgt, "drop trigger e2e_extra on crm.quote; drop function public.e2e_noop();")

    # injected failure 2b: schema drift in a table OUTSIDE the 16 (no per-table contract sees it) -> only the fingerprint does
    _psql(tgt, "alter table crm.note add column e2e_extra int")
    r = _cli(env, "apply", *ap, out=out)
    assert r.returncode == EXIT_REFUSED and "schema_fingerprint_identical" in r.stdout
    assert "crm.note" in r.stdout and "e2e_extra" in r.stdout and "+ crm.note" in r.stdout
    assert set(counts()) == {"0"}
    _psql(tgt, "alter table crm.note drop column e2e_extra")

    # injected failure 3 (R4): a write-time error mid-load -> exit 12, one transaction, target still empty.
    # The schema fingerprint now sees every constraint/trigger/column, so the injection must be invisible to the
    # catalogue scope: an ACL (excluded from the fingerprint) -> the COPY into the 9th table is denied.
    _psql(tgt, "revoke insert on crm.quote_revision from origenlab_owner")
    r = _cli(env, "apply", *ap, out=out)
    assert r.returncode == EXIT_FAILED and "all 16 tables empty" in r.stdout
    assert "quote_revision" in r.stdout and "permission denied" in r.stdout
    assert set(counts()) == {"0"}
    _psql(tgt, "grant insert on crm.quote_revision to origenlab_owner")

    # injected failure 3b: an event trigger (database-level, outside every fingerprinted schema) raising e2e_reject
    # on the first DDL of the write transaction -> exit 12, the error names e2e_reject, target still empty
    _psql(tgt, "create function public.e2e_reject() returns event_trigger language plpgsql as "
               "$$ begin raise exception 'e2e_reject: injected failure'; end $$; "
               "create event trigger e2e_reject on ddl_command_start execute function public.e2e_reject()")
    r = _cli(env, "apply", *ap, out=out)
    assert r.returncode == EXIT_FAILED and "all 16 tables empty" in r.stdout
    assert "e2e_reject" in r.stdout
    assert set(counts()) == {"0"}
    _psql(tgt, "drop event trigger e2e_reject; drop function public.e2e_reject()")

    # the real apply
    r = _cli(env, "apply", *ap, out=out)
    assert r.returncode == 0 and "COMMITTED" in r.stdout, r.stdout + r.stderr
    loaded = counts()
    assert loaded == [str(t["count"]) for t in plan["tables"]]
    assert _psql(tgt, f"select count(*) from crm.domain_event where actor_operator_id is not null and actor_operator_id <> '{hosted}'") == "0"
    assert _psql(tgt, "select count(*) from pg_trigger where not tgisinternal and tgenabled <> 'O'") == "0"
    assert _psql(tgt, "select count(*) from pg_constraint where contype='f' and condeferrable") == "0"
    assert _psql(tgt, "select last_value from crm.domain_event_stream_position_seq") == str(plan["sequence"]["target_last_value"])

    # injected failure 4: a second apply -> target not empty -> refused
    r = _cli(env, "apply", *ap, out=out)
    assert r.returncode == EXIT_REFUSED and "target_tables_empty" in r.stdout
    assert counts() == loaded

    # verify (re-hash, post-load dump, restore drill)
    r = _cli(env, "verify", *ap, out=out)
    assert r.returncode == 0 and "target state: loaded" in r.stdout and "Checklist para el dashboard" in r.stdout

    # rollback refusal (R8): a row in an OUTSIDE table that references a loaded row
    seq = _psql(tgt, "select last_value, is_called from crm.domain_event_stream_position_seq")
    _psql(tgt, f"insert into crm.task (opportunity_id, owner_operator_id, title, due_at) "
               f"select id, '{hosted}', 'e2e outside row', now() from crm.opportunity limit 1")
    assert _psql(tgt, "select count(*) from crm.task") == "1"
    r = _cli(env, "rollback", *ap, "--confirm-delete-loaded-rows", out=out)
    assert r.returncode == EXIT_REFUSED and "crm.task" in r.stdout, r.stdout
    assert counts() == loaded
    assert _psql(tgt, "select last_value, is_called from crm.domain_event_stream_position_seq") == seq
    _psql(tgt, "delete from crm.task where title = 'e2e outside row'")

    # the real rollback
    r = _cli(env, "rollback", *ap, "--confirm-delete-loaded-rows", out=out)
    assert r.returncode == 0 and "ROLLED BACK" in r.stdout, r.stdout + r.stderr
    assert set(counts()) == {"0"}
    assert _psql(tgt, "select count(*) from platform.operator") == "3"
    # the singletons survived (rollback uses DELETE, never TRUNCATE ... CASCADE) and the sequence is reset
    assert _psql(tgt, "select count(*) from outbound.campaign_block where lifted_at is null") == "1"
    assert _psql(tgt, "select count(*) from outbound.campaign_block") == "1"
    assert _psql(tgt, "select count(*), bool_or(marketing_enabled), bool_or(transactional_enabled) from outbound.send_control") == "1|f|f"
    assert _psql(tgt, "select last_value, is_called from crm.domain_event_stream_position_seq") == "1|f"

    # a second rollback refuses: the target is now empty, not loaded
    r = _cli(env, "rollback", *ap, "--confirm-delete-loaded-rows", out=out)
    assert r.returncode == EXIT_REFUSED and "does not match the plan exactly" in r.stdout
