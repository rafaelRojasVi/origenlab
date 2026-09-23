"""Databases a test run may never open, and the two places that check.

On 2026-09-22 a database-backed run of `test_v2_command_boundary.py` left seven fixture
source records, seven fixture operators, nine command receipts and the rows their commands
produced inside `origenlab_dev` — a persistent database holding twenty real staged Gmail
records. It also applied a migration's DDL without recording its ledger row, so that database
no longer described its own schema.

The suites already intend to run in a disposable `origenlab_test_<hex>` database they create
and drop. Intent was not enough, so this module makes it a check, in both of the places it can
fail:

* :func:`assert_not_protected` — the DSN as configured. A `ORIGENLAB_V2_TEST_DSN` or
  `ORIGENLAB_V2_API_TEST_DSN` that names a protected database is refused at import time, before
  a single test runs. This catches the common case: sourcing `api.env` (which points at
  `origenlab_dev`) and running pytest.

* :func:`assert_connection_is_disposable` — the database actually reached. Asked of the server,
  so it holds even if the DSN was rewritten, the name-swapping helper mangled a URL, or a
  fixture silently fell back to its maintenance connection. This is the check that would have
  caught what happened, whatever the mechanism was.

Both are deliberately *refusals*, never skips. A protected target is a misconfiguration to fix,
not a reason to quietly run fewer tests.
"""

from __future__ import annotations

#: Databases that hold real rows and are never disposable.
#:
#: `origenlab_dev` is the persistent development database (quarantined since 2026-09-22).
#: `origenlab_clean` is the clean-room database: rebuildable, but rebuilding it takes a full
#: historical load, and a test that dirties it makes every verification after it a lie.
PROTECTED_DATABASES: frozenset[str] = frozenset({"origenlab_dev", "origenlab_clean"})


class ProtectedDatabaseRefused(RuntimeError):
    """A test tried to use a database that holds real rows."""


def database_name_of(dsn: str) -> str:
    """The database a DSN names, or "" if it names none.

    Deliberately crude: it reads the path component and stops at the first `?`. Anything it
    cannot parse yields "", which is *not* treated as safe — callers pair it with the
    server-side check, which does not depend on parsing at all.
    """
    tail = (dsn or "").rpartition("/")[2]
    return tail.partition("?")[0].strip()


def assert_not_protected(dsn: str, *, variable: str) -> str:
    """Return ``dsn`` unchanged, or refuse if it names a protected database."""
    name = database_name_of(dsn)
    if name in PROTECTED_DATABASES:
        raise ProtectedDatabaseRefused(
            f"{variable} names {name!r}, which holds real rows and is never a test target. "
            "Point it at a maintenance connection in a disposable cluster instead — the suite "
            "creates and drops its own origenlab_test_<hex> database. "
            "See supabase/scripts/cleanroom_db.sh and docs/OPERATIONS.md §4.1 (“The clean-room database”)."
        )
    return dsn


def assert_connection_is_disposable(conn) -> str:
    """Ask the server which database this connection actually reached, and refuse a protected one.

    Returns the database name. Takes any DB-API connection, so it works for both `psycopg`
    connections the suites open and any wrapper around them.
    """
    with conn.cursor() as cur:
        cur.execute("select current_database()")
        name = cur.fetchone()[0]
    if name in PROTECTED_DATABASES:
        raise ProtectedDatabaseRefused(
            f"this connection reached {name!r}, which holds real rows. Refusing before any "
            "statement runs. A test must only ever reach a database it created itself."
        )
    return name
