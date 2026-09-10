"""Running the check bank: one psql process, one read-only transaction, no arbitrary SQL.

The session boundary, in the order it is established:

1. `default_transaction_read_only=on`, `statement_timeout`, `idle_in_transaction_session_timeout`
   and `lock_timeout` are set through the libpq connection options, so they are already in force
   when the first statement is parsed. They are not statements the transaction could have been
   opened without.
2. `begin read only` opens the single explicit transaction every check runs inside. A read-only
   transaction refuses INSERT, UPDATE, DELETE, DDL and sequence advancement at the transaction
   level, whatever privileges the session holds.
3. `set role origenlab_owner` is the one privilege change, and it is in the preamble below rather
   than in any check file. The configured audit identity holds no privilege of its own -- it is
   NOINHERIT with no grants -- so it assumes the owner exactly as a migration does. Inside a
   read-only transaction that grants reach, not write capability.
4. The reviewed check files run, each statically proven to be a single read (`olaudit.sqlbank`).
5. `reset role`, then `commit`.

**There is no mutation probe.** The audit never issues an INSERT, UPDATE, DELETE, DDL statement,
sequence change or any other intentional mutation against a hosted project, not even one it expects
to be refused and not even inside a read-only transaction. The read-only boundary is proven by
reading it back from the server (`s01`), by the static rejection of non-allowlisted statements, and
by the fixed reviewed query files -- never by attempting a write to see what happens. The negative
test that does attempt a write lives in `supabase/scripts/audit_failure_tests.sh` and runs only
against the disposable local CI database, inside a rollback-only harness.

`ON_ERROR_STOP=1` means the first failure aborts the run with no output for the checks after it.
That is the intended behaviour: a partial result is reported as incomplete, never as a pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import redact
from .sqlbank import Check
from .target import Target

PREAMBLE = "begin read only;\nset role origenlab_owner;\n"
EPILOGUE = "reset role;\ncommit;\n"


class RunError(RuntimeError):
    """The bank could not be executed. The message is already redacted."""


@dataclass(frozen=True)
class RunResult:
    """Raw observations, keyed by check id, plus how they were obtained."""

    observations: dict[str, dict]
    hosted_contacted: bool
    simulated: bool
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing and not self.unexpected


def build_script(checks: list[Check]) -> str:
    """The exact text sent to psql: the fixed preamble, the reviewed files, the fixed epilogue."""
    body = "".join(check.sql.rstrip() + "\n" for check in checks)
    return PREAMBLE + body + EPILOGUE


def parse_output(stdout: str, expected: list[str]) -> RunResult:
    """Map psql's one-JSON-object-per-line output onto check ids.

    Each check file names itself, so the mapping does not depend on output order and a check that
    produced nothing is detectable rather than silently attributed to its neighbour.
    """
    observations: dict[str, dict] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RunError(f"a check produced output that is not JSON: {exc.msg}") from exc
        if not isinstance(payload, dict) or "check" not in payload or "data" not in payload:
            raise RunError("a check produced JSON without a 'check' and 'data' pair")
        check_id = payload["check"]
        if check_id in observations:
            raise RunError(f"check '{check_id}' reported twice in one run")
        observations[check_id] = payload["data"]

    missing = tuple(check_id for check_id in expected if check_id not in observations)
    unexpected = tuple(sorted(set(observations) - set(expected)))
    return RunResult(observations, hosted_contacted=False, simulated=False, missing=missing, unexpected=unexpected)


def run(checks: list[Check], target: Target, *, timeout: int = 180) -> RunResult:
    """Execute the bank against `target`. Contacts the database; never called in simulated mode."""
    psql = shutil.which("psql", path=target.child_env()["PATH"])
    if not psql:
        raise RunError("psql is required and was not found on PATH")

    script = build_script(checks)
    workdir = tempfile.mkdtemp(prefix="ol-slice0-audit-")
    try:
        os.chmod(workdir, 0o700)
        script_path = Path(workdir) / "bank.sql"
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o600)

        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, built environment
                [
                    psql,
                    "-X",
                    "-q",
                    "-A",
                    "-t",
                    "--no-psqlrc",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-f",
                    str(script_path),
                ],
                env=target.child_env(),
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RunError(f"psql did not finish within {timeout}s; the run is incomplete") from exc

        if completed.returncode != 0:
            detail = redact.tail(completed.stderr or "", lines=10, secrets=target.secrets)
            raise RunError(
                f"psql exited {completed.returncode}; the run is incomplete.\n{detail}"
            )

        result = parse_output(completed.stdout, [check.check_id for check in checks])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    return RunResult(
        observations=result.observations,
        hosted_contacted=target.mode == "hosted",
        simulated=False,
        missing=result.missing,
        unexpected=result.unexpected,
    )


def run_simulated(checks: list[Check], fixture_path: Path) -> RunResult:
    """Replay a committed fixture. No process is started and no socket is opened.

    A simulated run exercises the whole engine -- the bank loads, the observations are evaluated,
    the verdict algebra runs, the reports are written and sanitised -- while contacting nothing.
    Its result is never eligible to satisfy a migration gate; see `olaudit.verdict`.
    """
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "observations" not in payload:
        raise RunError(f"{fixture_path.name} is not a simulated-run fixture")

    observations = payload["observations"]
    expected = [check.check_id for check in checks]
    missing = tuple(check_id for check_id in expected if check_id not in observations)
    unexpected = tuple(sorted(set(observations) - set(expected)))
    return RunResult(
        observations=observations,
        hosted_contacted=False,
        simulated=True,
        missing=missing,
        unexpected=unexpected,
    )
