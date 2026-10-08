#!/usr/bin/env python3
"""Encrypted pre-release/daily PostgreSQL 17 app-schema backup. No plaintext Actions artifact."""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from hosted_env import Refused, database_env

SCHEMAS = ("crm", "comms", "outbound", "evidence", "catalog", "procurement", "platform")

def execute(args: list[str], env: dict[str, str] | None = None) -> str:
    p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600, check=False)
    if p.returncode:
        raise Refused(f"Backup command {Path(args[0]).name} failed (exit {p.returncode}): {p.stderr[-1200:]}")
    return p.stdout

def run() -> None:
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Refused("Hosted backup requires main")
    recipient = os.environ.get("OL_PROD_BACKUP_AGE_RECIPIENT", "")
    if not re.fullmatch(r"age1[023456789acdefghjklmnpqrstuvwxyz]{58}", recipient):
        raise Refused("Missing or malformed offline age public recipient")
    if not shutil.which("age"):
        raise Refused("age encryption tool missing")
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    if not pg_dump or not pg_restore or not execute([pg_dump, "--version"]).startswith("pg_dump (PostgreSQL) 17."):
        raise Refused("PostgreSQL 17 pg_dump client required")
    output = Path(os.environ.get("OL_PROD_BACKUP_OUT", "")).resolve()
    if not output.parent.is_dir() or not output.name.endswith(".age"):
        raise Refused("Output must be an encrypted .age archive under the runner artifact directory")
    with tempfile.TemporaryDirectory(prefix="ol-prod-backup-", dir=os.environ.get("RUNNER_TEMP")) as d:
        private = Path(d)
        env = database_env(private)
        identity = execute(["psql", "-X", "--no-psqlrc", "-Atc",
                            "select session_user || '|' || current_setting('server_version_num')"], env).strip()
        if not identity.startswith("origenlab_migrator|17"):
            raise Refused("Incorrect production backup login")
        dump = private / "origenlab-production-app-schemas.dump"
        execute([pg_dump, "--role=origenlab_owner", "--format=custom", "--no-password",
                 *(flag for schema in SCHEMAS for flag in ("-n", schema)),
                 "--file", str(dump)], env)
        if dump.stat().st_size < 1000 or dump.read_bytes()[:5] != b"PGDMP":
            raise Refused("Custom-format backup appears empty or malformed")
        contents = execute([pg_restore, "--list", str(dump)])
        if sum(" TABLE DATA " in line for line in contents.splitlines()) < 25:
            raise Refused("Too few TABLE DATA entries; backup might be partial")
        (private / "restore-toc.txt").write_text(contents, encoding="utf-8")
        ledger = execute(["psql", "-X", "--no-psqlrc", "-Atc",
                          "select version || ' ' || coalesce(name,'') from supabase_migrations.schema_migrations order by version"],env)
        (private / "migration-ledger.txt").write_text(ledger, encoding="utf-8")
        import hashlib
        digest = hashlib.sha256(dump.read_bytes()).hexdigest()
        (private / "SHA256SUMS.txt").write_text(f"{digest}  {dump.name}\n", encoding="utf-8")
        (private / "README.txt").write_text(
            "OrigenLab production application schemas only; NOT a complete Supabase or Storage backup.\n"
            f"Created UTC {datetime.now(timezone.utc).isoformat()}\n"
            "Verify contents with: age -d -i <offline-key> FILE.age | tar -tf -\n"
            "Restore only into an isolated recovery database after reviewing roles and ownership.\n",
            encoding="utf-8")
        archive = private / "bundle.tar"
        execute(["tar", "-C", str(private), "-cf", str(archive),
                 dump.name, "restore-toc.txt", "migration-ledger.txt", "SHA256SUMS.txt", "README.txt"])
        # Only this ciphertext is published by GitHub Actions; the plaintext is destroyed with tmpdir.
        execute(["age", "-r", recipient, "-o", str(output), str(archive)])
        if output.stat().st_size < 1000:
            raise Refused("Encrypted archive is unexpectedly small")
        print(f"ENCRYPTED BACKUP VERIFIED: {output.name}, {output.stat().st_size} bytes")
        print(f"App schemas: {len(SCHEMAS)}; table data entries: {sum(' TABLE DATA ' in x for x in contents.splitlines())}")
        print("Plaintext backups are never uploaded")

if __name__ == "__main__":
    try:
        run()
    except (Refused, subprocess.TimeoutExpired, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
