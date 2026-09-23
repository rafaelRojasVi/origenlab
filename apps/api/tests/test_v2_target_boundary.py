"""`ORIGENLAB_V2_DATABASE_URL` is validated, not trusted.

The variable decides which database the /v2 read and command boundaries open. While the hosted
phase is frozen (docs/OPERATIONS.md §1.1) the only legitimate values are loopback DSNs — the
persistent development database, or the clean-room database. A hosted value here would let the
API read, and with commands enabled write, a project no migration slice has adopted.

These tests are the proof that the refusal is real and that it closes the *class*, not one
spelling of it: a host name, a libpq keyword smuggled through a query string, a hosted marker
on an otherwise loopback DSN.
"""

from __future__ import annotations

import pytest

from origenlab_api.settings import (
    Settings,
    V2TargetRefused,
    assert_v2_target_is_local,
)

LOOPBACK_V4 = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_clean"


@pytest.mark.parametrize(
    "url",
    [
        LOOPBACK_V4,
        "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev",
        "postgres://origenlab_api:pw@127.0.0.1:54322/postgres",
        "postgresql://origenlab_api:pw@[::1]:54332/origenlab_clean",
        "postgresql://origenlab_api:pw@127.0.0.2:54332/origenlab_clean",
    ],
)
def test_a_loopback_literal_is_accepted(url: str) -> None:
    assert assert_v2_target_is_local(url) == url


def test_an_empty_value_is_refused() -> None:
    with pytest.raises(V2TargetRefused, match="is empty"):
        assert_v2_target_is_local("   ")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db.abcdefghijkl.supabase.co:5432/postgres",
        "postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
        "postgresql://u:p@dpg-xyz.oregon-postgres.render.com/db",
        "postgresql://u:p@ep-cool.neon.tech/db",
    ],
)
def test_a_hosted_provider_is_refused(url: str) -> None:
    with pytest.raises(V2TargetRefused, match="hosted provider"):
        assert_v2_target_is_local(url)


def test_a_hosted_marker_is_refused_even_on_a_loopback_authority() -> None:
    """A hosted DSN hand-edited to look local is still a hosted DSN.

    The marker check runs before the host check on purpose: the operator who produced this
    string was reaching for a hosted project, and that intent is the thing to refuse.
    """
    with pytest.raises(V2TargetRefused, match="hosted provider"):
        assert_v2_target_is_local(
            "postgresql://u:p@127.0.0.1:5432/postgres.abcdefghijkl.supabase.co"
        )


def test_a_host_name_is_refused_including_localhost() -> None:
    """`localhost` is a name, and a name is resolved by whatever /etc/hosts says today.

    What this function validates (text) and what libpq connects to (an address) would be two
    different things, so only an IP literal is accepted.
    """
    for url in (
        "postgresql://u:p@localhost:54332/origenlab_clean",
        "postgresql://u:p@db.internal:5432/x",
    ):
        with pytest.raises(V2TargetRefused, match="is a name, not a literal IP address"):
            assert_v2_target_is_local(url)


def test_a_non_loopback_literal_is_refused() -> None:
    with pytest.raises(V2TargetRefused, match="not a loopback address"):
        assert_v2_target_is_local("postgresql://u:p@203.0.113.10:5432/x")


def test_a_query_string_is_refused() -> None:
    """libpq reads a URI's query string as connection keywords.

    `postgresql://127.0.0.1/db?host=203.0.113.10` has a loopback authority and a remote
    effective target. Refusing every query string closes that whole class rather than
    enumerating `host`, `hostaddr` and `service` one at a time.
    """
    with pytest.raises(V2TargetRefused, match="no query string"):
        assert_v2_target_is_local(
            "postgresql://u:p@127.0.0.1:5432/db?host=203.0.113.10"
        )


def test_a_fragment_is_refused() -> None:
    with pytest.raises(V2TargetRefused, match="no fragment"):
        assert_v2_target_is_local("postgresql://u:p@127.0.0.1:5432/db#frag")


def test_a_non_postgres_scheme_is_refused() -> None:
    with pytest.raises(V2TargetRefused, match="postgres:// or postgresql://"):
        assert_v2_target_is_local("mysql://u:p@127.0.0.1:3306/db")


def test_a_hostless_dsn_is_refused() -> None:
    with pytest.raises(V2TargetRefused, match="names no host"):
        assert_v2_target_is_local("postgresql:///origenlab_clean")


def test_a_unix_socket_path_is_refused() -> None:
    with pytest.raises(V2TargetRefused):
        assert_v2_target_is_local("postgresql:///db?host=/var/run/postgresql")


def test_require_v2_database_url_applies_the_boundary() -> None:
    """The refusal happens where the setting is read, so a misconfigured process never serves."""
    ok = Settings(v2_database_url=LOOPBACK_V4)
    assert ok.require_v2_database_url() == LOOPBACK_V4

    hosted = Settings(v2_database_url="postgresql://u:p@db.x.supabase.co:5432/postgres")
    with pytest.raises(V2TargetRefused):
        hosted.require_v2_database_url()


def test_an_unset_url_still_reports_the_original_error() -> None:
    """Absent and invalid are different problems and keep different messages."""
    with pytest.raises(ValueError, match="is required to mount"):
        Settings(v2_database_url=None).require_v2_database_url()
