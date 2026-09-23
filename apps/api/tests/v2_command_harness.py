"""The disposable database every V2 command test runs in, and the guard that keeps it so.

**Why this is a module and not a copy in each test file.** The safety argument for running
commands against a real PostgreSQL is not "we remember to clean up". It is that the database
did not exist a moment ago and will not exist a moment later, so the real staged Gmail
records of `docs/STATUS.md` §2.7.7 and the clean room's historical load are unreachable by
construction. An argument like that is worth exactly one implementation: two copies are two
chances for one of them to be subtly weaker, and the weaker one would be the one nobody
looked at.

Both command families — evidence review and the commercial case — build on what is here.

`ORIGENLAB_V2_TEST_DSN` names the cluster and a role that may `create database` and
`set role origenlab_owner`. `ORIGENLAB_V2_API_TEST_DSN` is the `origenlab_api` login, the
unprivileged runtime role a deployment actually uses; the commands run as that role, so the
per-verb grants and the RLS policies under test are the production ones. It cannot be
reached by `set role` — the maintenance role holds `origenlab_api` with `set_option = false`
— which is exactly the separation that makes running as it worth proving. Without both, the
database-backed tests skip rather than quietly running as a role that can do anything.
"""

from __future__ import annotations

import os
import pathlib
import uuid

import pytest

from protected_databases import assert_connection_is_disposable, assert_not_protected

_TEST_DSN = assert_not_protected(
    os.environ.get("ORIGENLAB_V2_TEST_DSN", "").strip(),
    variable="ORIGENLAB_V2_TEST_DSN",
)
_API_DSN = assert_not_protected(
    os.environ.get("ORIGENLAB_V2_API_TEST_DSN", "").strip(),
    variable="ORIGENLAB_V2_API_TEST_DSN",
)

needs_db = pytest.mark.skipif(
    not (_TEST_DSN and os.environ.get("ORIGENLAB_V2_API_TEST_DSN", "").strip()),
    reason="ORIGENLAB_V2_TEST_DSN and ORIGENLAB_V2_API_TEST_DSN are both required",
)

RUNTIME_ROLE = "origenlab_api"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

#: A hosted Supabase project provides `extensions` and its trusted extensions before any
#: migration runs. A database we create ourselves does not, so the first migration's
#: `create extension btree_gist with schema extensions` needs this first — the same platform
#: emulation `supabase/scripts/dev_db.sh` performs, and deliberately not part of the chain.
BOOTSTRAP = """
create schema if not exists extensions authorization postgres;
create extension if not exists btree_gist with schema extensions;
"""


def swap_database(dsn: str, database: str) -> str:
    base, _, _ = dsn.rpartition("/")
    return f"{base}/{database}"


def runtime_dsn(disposable_database: str) -> str:
    """The `origenlab_api` login, pointed at the disposable database."""
    return swap_database(_API_DSN, disposable_database.rpartition("/")[2])


def build_disposable_database():
    """Create `origenlab_test_<hex>`, migrate it, yield its DSN, drop it.

    A generator rather than a fixture, so each test module can declare its own module-scoped
    fixture over it: building the schema costs more than any test in it, and the modules must
    not share one database or one module's fixtures would be visible to the other's counts.
    """
    import psycopg

    name = f"origenlab_test_{uuid.uuid4().hex[:8]}"
    if not MIGRATIONS.is_dir():  # pragma: no cover - the repository always has these
        pytest.skip("supabase/migrations is missing")

    with psycopg.connect(_TEST_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"create database {name}")

    dsn = swap_database(_TEST_DSN, name)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            # Ask the server, before any statement runs, which database this actually is.
            # The migration chain is applied below WITHOUT writing ledger rows, which is
            # correct for a throwaway database and corrupting for a real one — on 2026-09-22
            # `origenlab_dev` ended up carrying a migration's DDL with no ledger row to
            # match. A name computed by string surgery is not evidence; this is.
            reached = assert_connection_is_disposable(conn)
            assert reached == name, (
                f"expected to reach the disposable database {name!r}, reached {reached!r}"
            )
            with conn.cursor() as cur:
                cur.execute(BOOTSTRAP)
                for path in sorted(MIGRATIONS.glob("*.sql")):
                    cur.execute(path.read_text(encoding="utf-8"))
        yield dsn
    finally:
        with psycopg.connect(_TEST_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
                (name,),
            )
            cur.execute(f"drop database if exists {name}")
