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
from .target import ROUTE_DIRECT, ROUTE_SUPAVISOR_SESSION, Target

PREAMBLE = "begin read only;\nset role origenlab_owner;\n"
EPILOGUE = "reset role;\ncommit;\n"

# ------------------------------------------------------------------------------------------------
# The Supavisor session-mode variant of the same boundary
# ------------------------------------------------------------------------------------------------
#
# The direct route above is unchanged. This variant exists because a pooled route cannot be trusted
# to carry the libpq startup `options` string: Supavisor sits between psql and Postgres, and what
# it forwards of a startup parameter is its business, not this audit's. The audit still *sends*
# PGOPTIONS -- if Supavisor rejects it, the connection fails and the route is refused, which is the
# correct fail-closed answer -- but it does not *rely* on it. Everything the contract needs is
# re-established transaction-locally and then read back from the server:
#
#   1. `begin read only` opens the one transaction, before any audit query.
#   2. `set local statement_timeout / lock_timeout / idle_in_transaction_session_timeout` bind the
#      timeouts to that transaction. `set local` rather than `set`, because the backend behind a
#      pooler outlives this connection and must not inherit anything this audit set.
#   3. `set local role origenlab_owner` for the same reason: the role reverts with the transaction
#      even if the epilogue never runs.
#   4. `g00` reads `transaction_read_only` **back from the server** and, in the same statement,
#      raises if it is not `on` -- so the run aborts under ON_ERROR_STOP before the first check
#      file is parsed. `run()` then asserts the same fact in Python from g00's own output, so the
#      guarantee does not rest on the SQL alone.
#   5. The reviewed check bank runs, each file already statically proven a single read.
#   6. `reset role; rollback;`. The pooler script contains no COMMIT at all, in any branch: on
#      success it rolls back explicitly, and on any error ON_ERROR_STOP aborts the script and psql
#      disconnects with the transaction still open, which the server rolls back.
#
# The `else` branch of the guard is deliberately not a literal cast. `current_setting` is STABLE,
# so PostgreSQL does not constant-fold it during planning; a plain `'...'::int` would be folded and
# would fail the run even when the transaction *is* read-only.

GUARD_CHECK_ID = "g00"

GUARD_SQL = """select jsonb_build_object(
  'check', '""" + GUARD_CHECK_ID + """',
  'data', jsonb_build_object(
    'transaction_read_only',               current_setting('transaction_read_only'),
    'default_transaction_read_only',       current_setting('default_transaction_read_only'),
    'statement_timeout',                   current_setting('statement_timeout'),
    'lock_timeout',                        current_setting('lock_timeout'),
    'idle_in_transaction_session_timeout', current_setting('idle_in_transaction_session_timeout'),
    'guard', case
               when current_setting('transaction_read_only') = 'on'
                 then 'read-only-established'
               else (current_setting('transaction_read_only')
                     || ' is not on: the audit transaction is not read only')::int::text
             end
  )
)::text;
"""

POOLED_EPILOGUE = "reset role;\nrollback;\n"

# Matches the `lock_timeout` already set through PGOPTIONS on both routes (olaudit.target).
LOCK_TIMEOUT_MS = 5_000


def pooled_preamble(statement_timeout_ms: int, lock_timeout_ms: int) -> str:
    """The transaction-local boundary the Supavisor route establishes and then reads back."""
    return (
        "begin read only;\n"
        f"set local statement_timeout = '{statement_timeout_ms}ms';\n"
        f"set local lock_timeout = '{lock_timeout_ms}ms';\n"
        f"set local idle_in_transaction_session_timeout = '{statement_timeout_ms}ms';\n"
        "set local role origenlab_owner;\n"
        + GUARD_SQL
    )


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


def build_script(
    checks: list[Check],
    *,
    route: str = ROUTE_DIRECT,
    statement_timeout_ms: int = 15_000,
    lock_timeout_ms: int = 5_000,
) -> str:
    """The exact text sent to psql: the fixed preamble, the reviewed files, the fixed epilogue.

    Two preambles, one per route, both fixed and both in this module. Neither is assembled from
    anything an operator supplies, and the check files between them are identical either way.
    """
    body = "".join(check.sql.rstrip() + "\n" for check in checks)
    if route == ROUTE_SUPAVISOR_SESSION:
        return pooled_preamble(statement_timeout_ms, lock_timeout_ms) + body + POOLED_EPILOGUE
    return PREAMBLE + body + EPILOGUE


def parse_output(stdout: str, expected: list[str], guard_id: str | None = None) -> RunResult:
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

    if guard_id is not None:
        guard = observations.pop(guard_id, None)
        assert_read_only_guard(guard, guard_id)

    missing = tuple(check_id for check_id in expected if check_id not in observations)
    unexpected = tuple(sorted(set(observations) - set(expected)))
    return RunResult(observations, hosted_contacted=False, simulated=False, missing=missing, unexpected=unexpected)


def assert_read_only_guard(guard: dict | None, guard_id: str) -> None:
    """Require the server's own answer to be `transaction_read_only=on`, and the timeouts set.

    This is the Python half of step 4 above. The SQL already aborts the run when the transaction is
    not read-only; this refuses the run when the guard statement produced nothing, produced a shape
    this code does not recognise, or reported a state the SQL cast happened not to catch. It runs
    before any observation reaches the check evaluators, and the caller discards the run if it
    raises -- so no check result is ever built from a session whose read-only state is unproven.
    """
    if not isinstance(guard, dict):
        raise RunError(
            f"the read-only guard '{guard_id}' produced no usable output, so the transaction's "
            "read-only state was never read back from the server; the run is refused"
        )
    if guard.get("transaction_read_only") != "on":
        raise RunError(
            "the server reported transaction_read_only="
            f"{guard.get('transaction_read_only')!r}; the audit refuses to run its check bank "
            "outside a read-only transaction"
        )
    for setting in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"):
        value = str(guard.get(setting, "0"))
        if value in {"0", "0ms", "None"}:
            raise RunError(
                f"{setting} is not set on the audit transaction; a hosted audit must not be able "
                "to hang on a lock or hold a pooled connection open. The run is refused."
            )


def run(checks: list[Check], target: Target, *, timeout: int = 180) -> RunResult:
    """Execute the bank against `target`. Contacts the database; never called in simulated mode."""
    psql = shutil.which("psql", path=target.child_env()["PATH"])
    if not psql:
        raise RunError("psql is required and was not found on PATH")

    script = build_script(
        checks,
        route=target.route,
        statement_timeout_ms=target.statement_timeout_ms,
        lock_timeout_ms=LOCK_TIMEOUT_MS,
    )
    guard_id = GUARD_CHECK_ID if target.route == ROUTE_SUPAVISOR_SESSION else None
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

        result = parse_output(completed.stdout, [check.check_id for check in checks], guard_id)
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
