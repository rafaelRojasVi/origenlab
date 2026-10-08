#!/usr/bin/env python3
"""Apply reviewed hosted SQL with DDL and Supabase ledger in one transaction as migrator."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from hosted_env import Refused, database_env

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"^([0-9]{14})_([a-z0-9_]+)\.sql$")

def chain(root: Path) -> list[tuple[str, str, Path]]:
    output = []
    for f in sorted((root / "supabase/migrations").glob("*.sql")):
        m = PATTERN.fullmatch(f.name)
        if not m:
            raise Refused("Unrecognized migration filename")
        if output and output[-1][0] >= m.group(1):
            raise Refused("Duplicate or unordered migration version")
        sql = f.read_text(encoding="utf-8").lower()
        if not re.search(r"\bset\s+role\s+origenlab_owner\s*;", sql) or not re.search(r"\breset\s+role\s*;", sql):
            raise Refused(f"Missing reviewed owner role transition in {f.name}")
        output.append((m.group(1), m.group(2), f))
    if not output:
        raise Refused("Migration chain empty")
    return output

def pending_files(rows: list[tuple[str,str,Path]], versions: list[str]) -> list[tuple[str,str,Path]]:
    if versions != [r[0] for r in rows[:len(versions)]]:
        raise Refused("Hosted ledger is not a prefix of reviewed main; manual reconciliation required")
    return rows[len(versions):]

def psql(env: dict[str,str], *args: str) -> str:
    p = subprocess.run(["psql", "-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", *args],
                       env=env, capture_output=True, text=True, timeout=240, check=False)
    if p.returncode:
        raise Refused(f"psql failed (exit {p.returncode}): {p.stderr[-1100:]}")
    return p.stdout.strip()

def run(mode: str) -> None:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("Hosted migrations require trusted main")
    rows = chain(ROOT)
    with tempfile.TemporaryDirectory(prefix="ol-hosted-", dir=os.environ.get("RUNNER_TEMP")) as directory:
        env = database_env(Path(directory))
        identity = psql(env, "-Atc", """select session_user, current_database(),
          pg_has_role(session_user,'origenlab_owner','SET')::text,
          current_setting('server_version_num'),
          has_schema_privilege(session_user,'supabase_migrations','USAGE')::text,
          has_table_privilege(session_user,'supabase_migrations.schema_migrations','SELECT')::text,
          has_table_privilege(session_user,'supabase_migrations.schema_migrations','INSERT')::text""").split("|")
        if len(identity) != 7 or identity[:3] != ["origenlab_migrator","postgres","true"] or not identity[3].startswith("17") or identity[4:] != ["true","true","true"]:
            raise Refused("Wrong identity, server version or narrowly scoped ledger grants")
        recorded = psql(env, "-Atc", "select version from supabase_migrations.schema_migrations order by version")
        pending = pending_files(rows, recorded.splitlines() if recorded else [])
        print(f"Hosted {len(rows)-len(pending)} / reviewed {len(rows)} migrations")
        for v,n,_ in pending:
            print(f"PENDING {v}_{n}")
        if mode == "plan":
            print("PLAN ONLY — zero writes")
            return
        if os.environ.get("OL_PROD_BACKUP_VERIFIED") != "true":
            raise Refused("Successful encrypted off-machine backup artifact required")
        for v,n,f in pending:
            i = rows.index((v,n,f))
            previous = rows[i-1][0] if i else ""
            # BEGIN / COMMIT managed by psql --single-transaction across -c, -f, -c.
            # Lock and recheck latest migration under lock: no competing apply may race.
            guard = ("select pg_advisory_xact_lock(72830224092701); "
                     "do $$ begin "
                     f"if (select coalesce(max(version),'') from supabase_migrations.schema_migrations) <> '{previous}' "
                     "then raise exception 'schema ledger changed while waiting for migration lock'; end if; "
                     "end $$; "
                     "set lock_timeout = '10s'; set statement_timeout = '180s';")
            ledger = ("do $$ begin if session_user <> 'origenlab_migrator' or current_user <> 'origenlab_migrator' "
                      "then raise exception 'migration failed to reset the role'; end if; end $$; "
                      f"insert into supabase_migrations.schema_migrations(version,name) values ('{v}','{n}');")
            print(f"Applying {v}_{n} atomically")
            psql(env, "--single-transaction", "-c", guard, "-f", str(f), "-c", ledger)
            print(f"COMMITTED {v}")
        final = psql(env, "-Atc", "select version from supabase_migrations.schema_migrations order by version")
        if final.splitlines() != [row[0] for row in rows]:
            raise Refused("Final ledger mismatch")
        print("SUCCESS: hosted ledger matches reviewed main")

if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in {"plan","apply"}:
            raise Refused("Use: hosted_migrations.py plan|apply")
        run(sys.argv[1])
    except (Refused, subprocess.TimeoutExpired) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
