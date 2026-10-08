#!/usr/bin/env python3
"""Encrypted pre-release/daily PostgreSQL 17 app-schema backup. No plaintext Actions artifact."""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
import tempfile
import select
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from hosted_env import Refused, database_env

SCHEMAS = ("crm", "comms", "outbound", "evidence", "catalog", "procurement", "platform",
           "procrastinate")

def execute(args: list[str], env: dict[str, str] | None = None) -> str:
    p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600, check=False)
    if p.returncode:
        # No raw stdout/stderr from production commands in public CI logs.
        raise Refused(f"Backup command {Path(args[0]).name} failed (exit {p.returncode}); details withheld")
    return p.stdout

@contextmanager
def exported_snapshot(env: dict[str, str]):
    """Keep the exporting transaction alive through dump AND ledger extraction.

    A shared migration advisory lock also excludes cooperating DDL runners.
    The dump and ledger import the same MVCC snapshot; two independent reads
    would describe different recovery points during concurrent migrations.
    """
    process = subprocess.Popen(
        ["psql", "-X", "--no-psqlrc", "-qAt", "-v", "ON_ERROR_STOP=1"],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0)
    try:
        process.stdin.write((
            "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
            "SET LOCAL lock_timeout = '10s';\n"
            "SET LOCAL statement_timeout = '900s';\n"
            "SELECT pg_advisory_xact_lock_shared(72830224092701);\n"
            "SELECT pg_export_snapshot();\n").encode("ascii"))
        process.stdin.flush()
        deadline = time.monotonic() + 30
        snapshot = ""
        received = b""
        while time.monotonic() < deadline:
            if not select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            received += os.read(process.stdout.fileno(), 4096)
            candidates = [line for line in received.decode("ascii").splitlines()
                          if re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", line)]
            if candidates:
                snapshot = candidates[0]
                break
            if process.poll() is not None:
                break
        if not snapshot:
            raise Refused("Could not export a consistent production backup snapshot")
        yield snapshot
        if process.poll() is not None:
            raise Refused("Backup snapshot session ended before verification")
    finally:
        # Closing the exporter releases the snapshot and advisory lock even on failure.
        if process.poll() is None:
            try:
                process.communicate(b"ROLLBACK;\n\\q\n", timeout=10)
            except (subprocess.TimeoutExpired, BrokenPipeError):
                process.kill()
                process.communicate()


def run() -> None:
    os.umask(0o077)  # All plaintext and manifest outputs are owner-only.
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
        with exported_snapshot(env) as snapshot:
            execute([pg_dump, "--role=origenlab_owner", "--format=custom", "--no-password",
                     "--snapshot", snapshot,
                     *(flag for schema in SCHEMAS for flag in ("-n", schema)),
                     "--file", str(dump)], env)
            ledger = execute(["psql", "-X", "--no-psqlrc", "-qAt", "-v", "ON_ERROR_STOP=1", "-c",
                              "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; "
                              f"SET TRANSACTION SNAPSHOT '{snapshot}'; "
                              "select version || ' ' || coalesce(name,'') from supabase_migrations.schema_migrations order by version; "
                              "COMMIT;"], env)
        if dump.stat().st_size < 1000:
            raise Refused("Custom-format backup appears empty")
        with dump.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise Refused("Custom-format archive header is invalid")
        contents = execute([pg_restore, "--list", str(dump)])
        missing = [schema for schema in SCHEMAS
                   if not any(re.search(r"\bSCHEMA - " + re.escape(schema) + r"\s", line)
                              for line in contents.splitlines())]
        if missing:
            raise Refused("Backup is missing required application/queue schemas: " + ", ".join(missing))
        if sum(" TABLE DATA " in line for line in contents.splitlines()) < 25:
            raise Refused("Too few TABLE DATA entries; backup might be partial")
        (private / "restore-toc.txt").write_text(contents, encoding="utf-8")
        (private / "migration-ledger.txt").write_text(ledger, encoding="utf-8")
        import hashlib
        with dump.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        sums = [f"{digest}  {dump.name}"]
        for name in ("restore-toc.txt", "migration-ledger.txt"):
            with (private / name).open("rb") as stream:
                sums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {name}")
        (private / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
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
        print(f"ENCRYPTED BACKUP CREATED (restore not yet proven): {output.name}, {output.stat().st_size} bytes")
        print(f"App and queue schemas: {len(SCHEMAS)}; table data entries: {sum(' TABLE DATA ' in x for x in contents.splitlines())}")
        print("Plaintext backups are never uploaded")

if __name__ == "__main__":
    try:
        run()
    except (Refused, subprocess.TimeoutExpired, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
