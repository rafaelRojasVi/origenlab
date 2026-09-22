"""A test run may never open a database that holds real rows.

On 2026-09-22 a database-backed run left seven fixture source records, seven fixture operators,
nine command receipts and the rows their commands produced inside `origenlab_dev` — beside
twenty real staged Gmail records — and applied a migration's DDL without recording its ledger
row. The suites already *intended* to run in a database they created themselves; intent is not
a check, so now there are two.
"""

from __future__ import annotations

import pytest

from protected_databases import (
    PROTECTED_DATABASES,
    ProtectedDatabaseRefused,
    assert_connection_is_disposable,
    assert_not_protected,
    database_name_of,
)


def test_both_real_databases_are_protected() -> None:
    assert PROTECTED_DATABASES == {"origenlab_dev", "origenlab_clean"}


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("postgresql://u:p@127.0.0.1:54332/origenlab_dev", "origenlab_dev"),
        ("postgresql://u:p@127.0.0.1:54332/origenlab_test_a1b2c3d4", "origenlab_test_a1b2c3d4"),
        ("postgresql://u:p@127.0.0.1:54332/postgres?sslmode=disable", "postgres"),
        ("", ""),
    ],
)
def test_database_name_of(dsn: str, expected: str) -> None:
    assert database_name_of(dsn) == expected


@pytest.mark.parametrize("name", sorted(PROTECTED_DATABASES))
def test_a_protected_dsn_is_refused(name: str) -> None:
    with pytest.raises(ProtectedDatabaseRefused, match=name):
        assert_not_protected(
            f"postgresql://u:p@127.0.0.1:54332/{name}", variable="ORIGENLAB_V2_TEST_DSN"
        )


def test_the_refusal_names_the_variable_so_the_fix_is_obvious() -> None:
    with pytest.raises(ProtectedDatabaseRefused, match="ORIGENLAB_V2_API_TEST_DSN"):
        assert_not_protected(
            "postgresql://u:p@127.0.0.1:54332/origenlab_dev",
            variable="ORIGENLAB_V2_API_TEST_DSN",
        )


def test_a_disposable_dsn_passes_through_unchanged() -> None:
    dsn = "postgresql://u:p@127.0.0.1:54332/origenlab_test_a1b2c3d4"
    assert assert_not_protected(dsn, variable="ORIGENLAB_V2_TEST_DSN") == dsn


def test_an_unset_dsn_is_not_treated_as_protected() -> None:
    """Absent means "skip these tests", which the suites already handle. It is not a refusal."""
    assert assert_not_protected("", variable="ORIGENLAB_V2_TEST_DSN") == ""


class _FakeCursor:
    def __init__(self, name: str) -> None:
        self._name = name

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def execute(self, sql: str) -> None:
        assert sql == "select current_database()"

    def fetchone(self):
        return (self._name,)


class _FakeConnection:
    def __init__(self, name: str) -> None:
        self._name = name

    def cursor(self):
        return _FakeCursor(self._name)


@pytest.mark.parametrize("name", sorted(PROTECTED_DATABASES))
def test_a_connection_that_reached_a_protected_database_is_refused(name: str) -> None:
    """The check that does not depend on parsing a DSN.

    This is the one that holds when a name-swapping helper mangles a URL or a fixture falls
    back to its maintenance connection — the server is asked what it actually is.
    """
    with pytest.raises(ProtectedDatabaseRefused, match=name):
        assert_connection_is_disposable(_FakeConnection(name))


def test_a_connection_to_a_disposable_database_returns_its_name() -> None:
    assert (
        assert_connection_is_disposable(_FakeConnection("origenlab_test_a1b2c3d4"))
        == "origenlab_test_a1b2c3d4"
    )
