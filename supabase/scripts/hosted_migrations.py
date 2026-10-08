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
EXPECTED_DATABASE = "postgres"
PATTERN = re.compile(r"^([0-9]{14})_([a-z0-9_]+)\.sql$")

def chain(root: Path) -> list[tuple[str, str, Path]]:
    output = []
    for f in sorted((root / "supabase/migrations").glob("*.sql")):
        m = PATTERN.fullmatch(f.name)
        if not m:
            raise Refused("Unrecognized migration filename")
        if output and output[-1][0] >= m.group(1):
            raise Refused("Duplicate or unordered migration version")
        output.append((m.group(1), m.group(2), f))
    if not output:
        raise Refused("Migration chain empty")
    return output

def sql_statements(sql: str) -> list[str]:
    """Split top-level SQL, masking literals/comments, never execute psql commands.

    This is a conservative transaction-boundary guard for REVIEWED SQL, not a
    sandbox for malicious owner-role code. Dollar-quoted function bodies stay
    opaque; PostgreSQL itself forbids their transaction termination inside ours.
    """
    masked = []
    i = 0
    while i < len(sql):
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end
            masked.append(" ")
        elif sql.startswith("/*", i):
            depth = 1
            i += 2
            while i < len(sql) and depth:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise Refused("Unterminated SQL comment")
            masked.append(" ")
        elif sql[i] in "'\"":
            quote = sql[i]
            i += 1
            while i < len(sql):
                if sql[i] == "\\":
                    # Backslash interpretation depends on string settings; refuse
                    # ambiguous top-level literals rather than hide a COMMIT.
                    raise Refused("Backslash in top-level SQL literal requires manual review")
                elif sql[i] == quote:
                    i += 1
                    if i < len(sql) and sql[i] == quote:
                        i += 1
                    else:
                        break
                else:
                    i += 1
            else:
                raise Refused("Unterminated SQL literal")
            masked.append(" <literal> ")
        elif sql[i] == "$" and (tag := re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", sql[i:])):
            delimiter = tag.group()
            end = sql.find(delimiter, i + len(delimiter))
            if end < 0:
                raise Refused("Unterminated dollar-quoted SQL")
            i = end + len(delimiter)
            masked.append(" <body> ")
        elif sql[i] == "\\":
            raise Refused("Pending migration contains psql meta-commands")
        else:
            masked.append(sql[i])
            i += 1
    return [" ".join(x.lower().split()) for x in "".join(masked).split(";") if x.strip()]


def require_reviewed_owner_transition(file: Path) -> None:
    statements = sql_statements(file.read_text(encoding="utf-8"))
    if not statements or statements[0] != "set role origenlab_owner" or statements[-1] != "reset role":
        raise Refused(f"Pending migration requires first SET ROLE owner and last RESET ROLE: {file.name}")
    for statement in statements:
        if re.match(r"^(?:begin|start|commit|end|rollback|abort|prepare|discard)\b", statement):
            raise Refused(f"Pending migration contains transaction control: {file.name}")
        if re.match(r"^set\s+(?:session\s+authorization|(?:local\s+|session\s+)?transaction)\b", statement):
            raise Refused(f"Pending migration changes transaction/session contract: {file.name}")


def pending_files(rows: list[tuple[str,str,Path]], versions: list[str]) -> list[tuple[str,str,Path]]:
    if versions != [r[0] for r in rows[:len(versions)]]:
        raise Refused("Hosted ledger is not a prefix of reviewed main; manual reconciliation required")
    return rows[len(versions):]

def psql(env: dict[str,str], *args: str) -> str:
    p = subprocess.run(["psql", "-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", *args],
                       env=env, capture_output=True, text=True, timeout=240, check=False)
    if p.returncode:
        # This repository's Actions logs may be public. PostgreSQL DETAIL text can
        # contain personal data from a failing row; never copy stderr to CI.
        raise Refused(f"psql operation failed (exit {p.returncode}); no production output logged")
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
        if len(identity) != 7 or identity[:3] != ["origenlab_migrator",EXPECTED_DATABASE,"true"] or not identity[3].startswith("17") or identity[4:] != ["true","true","true"]:
            raise Refused("Wrong identity, server version or narrowly scoped ledger grants")
        recorded = psql(env, "-Atc", "select version from supabase_migrations.schema_migrations order by version")
        pending = pending_files(rows, recorded.splitlines() if recorded else [])
        # Historic ledger includes one admin-only privilege-revocation migration.
        # Validate owner transitions only for files actually being applied now.
        for _version, _name, file in pending:
            require_reviewed_owner_transition(file)
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
            # BEGIN / COMMIT managed by psql --single-transaction across -c, -f, -c.
            # Lock and recheck latest migration under lock: no competing apply may race.
            guard = ("set lock_timeout = '10s'; set statement_timeout = '180s'; "
                     "select pg_advisory_xact_lock(72830224092701); "
                     "do $$ begin "
                     f"if (select coalesce(array_agg(version order by version), ARRAY[]::text[]) from supabase_migrations.schema_migrations) <> ARRAY[{','.join(repr(row[0]) for row in rows[:i])}]::text[] "
                     "then raise exception 'schema ledger changed while waiting for migration lock'; end if; "
                     "end $$;")
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
