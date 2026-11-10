"""Tests for apps/api/scripts/release_read_check.py.

Two suites:

1. ``test_every_allowlist_pattern_is_accounted_for`` — no database, no credentials.
   Parses ALLOWED_UPSTREAM_PATHS from apps/dashboard-proxy/src/allowlist.ts and
   asserts that every pattern appears in either CHECKED_PATTERNS or SKIPPED_PATTERNS.
   Adding a route to the proxy allowlist without updating this script fails CI immediately.

2. ``test_run_checks_passes_on_disposable_db`` — uses the make db-up test cluster.
   Creates a disposable database, applies the full migration chain, inserts one open case
   with an active CLP quote revision (amount 1_000_000, stage 'lead'), then calls
   ``run_checks`` and asserts it returns 0.

   The fixture includes a non-zero quote tax_base so that ``FinanceReadRepository.mes()``
   enters the pipeline-ponderado loop and executes ``int(Decimal(amount_clp) * Decimal(str(w)))``.
   Reverting #893's fix (changing ``Decimal(str(w))`` back to the float ``w``) makes
   ``mes()`` raise ``TypeError: unsupported operand type(s) for *: 'decimal.Decimal' and 'float'``,
   causing ``run_checks`` to return 1 and this test to fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the script module (not an installed package; add scripts/ to sys.path).

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from release_read_check import CHECKED_PATTERNS, SKIPPED_PATTERNS, run_checks  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALLOWLIST_TS = _REPO_ROOT / "apps/dashboard-proxy/src/allowlist.ts"

_REGEX_LINE = re.compile(r"^/(.+\$)/")


def _extract_allowlist_patterns(ts_path: Path) -> list[str]:
    r"""Extract regex source strings from ALLOWED_UPSTREAM_PATHS in allowlist.ts.

    Each entry in the TypeScript array looks like  /^\/health$/,
    The source string stored in CHECKED_PATTERNS / SKIPPED_PATTERNS is the
    text between the outer slash delimiters: ^\/health$
    """
    text = ts_path.read_text(encoding="utf-8")
    # Isolate the ALLOWED_UPSTREAM_PATHS array body (ends at the first ];\n).
    marker = "ALLOWED_UPSTREAM_PATHS: readonly RegExp[] = [\n"
    start = text.index(marker) + len(marker)
    end = text.index("];\n", start)
    body = text[start:end]

    patterns: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        m = _REGEX_LINE.match(stripped)
        if m:
            patterns.append(m.group(1))
    return patterns


# ---------------------------------------------------------------------------
# Suite 1 — static coverage check (no DB, no credentials)


def test_every_allowlist_pattern_is_accounted_for() -> None:
    """Every ALLOWED_UPSTREAM_PATHS pattern must be in CHECKED_PATTERNS or SKIPPED_PATTERNS.

    When a route is added to apps/dashboard-proxy/src/allowlist.ts the developer must
    also update apps/api/scripts/release_read_check.py to either exercise the route or
    explicitly skip it with a reason.  This test enforces that gate.
    """
    if not _ALLOWLIST_TS.is_file():
        pytest.skip("allowlist.ts not found — skipping coverage gate")

    patterns = _extract_allowlist_patterns(_ALLOWLIST_TS)
    assert patterns, "parsed zero patterns from ALLOWED_UPSTREAM_PATHS — parser may be broken"

    all_accounted = CHECKED_PATTERNS | set(SKIPPED_PATTERNS)
    missing = [p for p in patterns if p not in all_accounted]
    extra = all_accounted - set(patterns)

    messages: list[str] = []
    if missing:
        messages.append(
            "These ALLOWED_UPSTREAM_PATHS patterns are not in CHECKED_PATTERNS or SKIPPED_PATTERNS:\n"
            + "\n".join(f"  {p!r}" for p in sorted(missing))
        )
    if extra:
        messages.append(
            "These patterns are in CHECKED/SKIPPED_PATTERNS but not in ALLOWED_UPSTREAM_PATHS"
            " (stale entry):\n" + "\n".join(f"  {p!r}" for p in sorted(extra))
        )
    assert not messages, "\n\n".join(messages)


# ---------------------------------------------------------------------------
# Suite 2 — full end-to-end check on a disposable database


try:
    from v2_command_harness import build_disposable_database, needs_db
except ImportError:
    # The harness is only available when the test suite runs with ORIGENLAB_V2_TEST_DSN set.
    needs_db = pytest.mark.skip(reason="v2_command_harness not importable")  # type: ignore[assignment]

    def build_disposable_database():  # type: ignore[misc]
        raise RuntimeError("unreachable")  # pragma: no cover


@pytest.fixture(scope="module")
def _db_with_fixture():
    """A disposable database with one open case + active CLP quote (1 M CLP)."""
    import psycopg

    for dsn in build_disposable_database():
        # Insert the minimum fixture data as the postgres superuser.
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("set role origenlab_owner")
            conn.autocommit = False
            # Operator
            op = conn.execute(
                "insert into platform.operator"
                " (auth_user_id, email_norm, display_name, role, status)"
                " values (gen_random_uuid(), 'check@test.internal',"
                " 'Check Operator', 'sales', 'active') returning id",
            ).fetchone()[0]
            # Open case at 'lead' stage — the guard only allows 'lead' as an opening stage.
            # 'lead' has weight 0.10, which is enough to trigger the pipeline loop.
            case = conn.execute(
                "insert into crm.opportunity (title, stage, owner_operator_id)"
                " values ('Read-check fixture', 'lead', %s) returning id",
                (op,),
            ).fetchone()[0]
            # Quote for the case (printed_historical = historical import, no serial constraint)
            quote = conn.execute(
                "insert into crm.quote (opportunity_id, quote_number, number_origin)"
                " values (%s, 'CN09998-26', 'printed_historical') returning id",
                (case,),
            ).fetchone()[0]
            # Draft CLP revision with tax_base 1_000_000.
            # 'draft' is non-void, so _PIPELINE_BY_STAGE_SQL returns this row for the
            # open case, which guarantees mes() enters the pipeline loop and executes:
            #   int(Decimal(amount_clp) * Decimal(str(w)))
            # Reverting the #893 fix (float w instead of Decimal(str(w))) raises
            # TypeError, making run_checks return 1 and failing this test.
            conn.execute(
                "insert into crm.quote_revision"
                " (quote_id, revision_no, status, quote_currency, price_decimals, tax_base)"
                " values (%s, 1, 'draft', 'CLP', 0, 1000000)",
                (quote,),
            )
            conn.commit()
        yield dsn


@needs_db
def test_run_checks_passes_on_disposable_db(_db_with_fixture: str) -> None:
    """run_checks returns 0 on a fresh schema with one open case + quote.

    The 'lead' stage case with tax_base 1_000_000 ensures that
    FinanceReadRepository.mes() enters its pipeline-ponderado loop and applies
    the Decimal × Decimal multiplication introduced in #893.  Any regression to
    Decimal × float would raise TypeError, return 1, and fail this assertion.
    """
    result = run_checks(_db_with_fixture)
    assert result == 0, (
        f"run_checks returned {result}; check the FAIL lines printed above"
    )


def test_hosted_env_dir_holds_hosted_env() -> None:
    """The release step imports hosted_env from here; a wrong parent count failed the 11-09 release."""
    from release_read_check import HOSTED_ENV_DIR

    assert (HOSTED_ENV_DIR / "hosted_env.py").is_file()


def test_main_runs_the_reads_while_the_ca_certificate_still_exists(monkeypatch, tmp_path):
    # The first hosted run failed all 45 reads: the CA file lived in a TemporaryDirectory that
    # had already been deleted when the reads connected.
    import types

    import release_read_check

    def database_env(cert_directory: Path) -> dict[str, str]:
        cert = cert_directory / "ca.pem"
        cert.write_text("-----BEGIN CERTIFICATE-----\n", encoding="ascii")
        return dict(PGHOST="h", PGPORT="5432", PGDATABASE="d", PGUSER="u", PGPASSWORD="p",
                    PGSSLMODE="verify-full", PGSSLROOTCERT=str(cert), PGCONNECT_TIMEOUT="10",
                    PGGSSENCMODE="disable")

    fake = types.ModuleType("hosted_env")
    fake.Refused = RuntimeError
    fake.database_env = database_env
    monkeypatch.setitem(sys.modules, "hosted_env", fake)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    seen: list[bool] = []

    def run_checks(dsn: str) -> int:
        cert = re.search(r"sslrootcert=(\S+)", dsn).group(1)
        seen.append(Path(cert).is_file())
        return 0

    monkeypatch.setattr(release_read_check, "run_checks", run_checks)
    release_read_check.main()
    assert seen == [True]
