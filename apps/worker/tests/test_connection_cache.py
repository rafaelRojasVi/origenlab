"""Tests for VerifiedConnectionCache: connection reuse, broken-connection recovery and
verification refusal.

These tests use fake connections and a fake verify function so no database is needed.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest

from origenlab_worker.database import (
    TargetRefused,
    VerifiedConnectionCache,
    WorkerTarget,
    local_test_target,
    verify_worker_connection,
)

# ── helpers ────────────────────────────────────────────────────────────────────

_TARGET = local_test_target("postgresql://origenlab_worker:pw@127.0.0.1:5432/origenlab_test_aabbccdd")


def _fake_connect(conn: MagicMock):
    """Return a ``connect`` callable that always returns ``conn``."""
    def _connect(*a, **k):
        return conn
    return _connect


def _live_conn(*, broken: bool = False, closed: bool = False) -> MagicMock:
    """A fake psycopg3 connection that passes all attribute checks."""
    m = MagicMock()
    m.closed = closed
    m.broken = broken
    return m


# ── (a) N consecutive sweeps open exactly one verified connection ───────────────

def test_n_consecutive_get_calls_open_one_verified_connection() -> None:
    """Calling get() five times must connect once and verify once."""
    conn = _live_conn()
    verifications: list[int] = []

    def _verify(c, target, *, privileges, policies):
        verifications.append(1)

    cache = VerifiedConnectionCache(
        _TARGET,
        application_name="test",
        privileges=(),
        policies=frozenset(),
        connect=_fake_connect(conn),
    )
    with patch("origenlab_worker.database.verify_worker_connection", side_effect=_verify):
        results = [cache.get() for _ in range(5)]

    assert all(r is conn for r in results), "all calls must return the same connection"
    assert len(verifications) == 1, "verification must run exactly once"
    assert cache.verification_count == 1


def test_connection_context_manager_yields_the_cached_connection() -> None:
    """connection() must yield the cached connection without opening a new one."""
    conn = _live_conn()
    cache = VerifiedConnectionCache(
        _TARGET,
        application_name="test",
        privileges=(),
        policies=frozenset(),
        connect=_fake_connect(conn),
    )
    with patch("origenlab_worker.database.verify_worker_connection"):
        with cache.connection() as c1:
            pass
        with cache.connection() as c2:
            pass

    assert c1 is conn and c2 is conn
    assert cache.verification_count == 1


# ── (b) a broken connection is replaced and re-verified ─────────────────────────

def test_a_closed_connection_is_replaced_on_next_get() -> None:
    """get() returns a new verified connection after the previous one is closed."""
    conn1 = _live_conn()
    conn2 = _live_conn()
    call_count = [0]

    def _connect(*a, **k):
        call_count[0] += 1
        return conn1 if call_count[0] == 1 else conn2

    cache = VerifiedConnectionCache(
        _TARGET,
        application_name="test",
        privileges=(),
        policies=frozenset(),
        connect=_connect,
    )

    with patch("origenlab_worker.database.verify_worker_connection"):
        first = cache.get()
        assert first is conn1
        assert cache.verification_count == 1

        # Simulate the connection being closed by the server.
        conn1.closed = True

        second = cache.get()
        assert second is conn2
        assert cache.verification_count == 2


def test_a_broken_connection_is_invalidated_by_the_context_manager() -> None:
    """connection() invalidates the cache when OperationalError is raised."""
    import psycopg

    conn1 = _live_conn()
    conn2 = _live_conn()
    call_count = [0]

    def _connect(*a, **k):
        call_count[0] += 1
        return conn1 if call_count[0] == 1 else conn2

    cache = VerifiedConnectionCache(
        _TARGET,
        application_name="test",
        privileges=(),
        policies=frozenset(),
        connect=_connect,
    )

    with patch("origenlab_worker.database.verify_worker_connection"):
        # First use: connection breaks mid-use.
        with pytest.raises(psycopg.OperationalError):
            with cache.connection() as conn:
                assert conn is conn1
                raise psycopg.OperationalError("wire reset")

        # The cache was invalidated; the next use reconnects and re-verifies.
        with cache.connection() as conn:
            assert conn is conn2

    assert cache.verification_count == 2


def test_invalidate_clears_the_cache_so_next_get_reconnects() -> None:
    """Explicit invalidate() causes the next get() to reconnect and re-verify."""
    conn1 = _live_conn()
    conn2 = _live_conn()
    call_count = [0]

    def _connect(*a, **k):
        call_count[0] += 1
        return conn1 if call_count[0] == 1 else conn2

    cache = VerifiedConnectionCache(
        _TARGET,
        application_name="test",
        privileges=(),
        policies=frozenset(),
        connect=_connect,
    )

    with patch("origenlab_worker.database.verify_worker_connection"):
        assert cache.get() is conn1
        cache.invalidate()
        assert cache.get() is conn2

    assert cache.verification_count == 2


# ── (c) a failed verification still refuses ──────────────────────────────────────

def test_a_failed_verification_raises_and_leaves_no_connection_cached() -> None:
    """If verify_worker_connection raises, get() propagates the error and caches nothing."""
    conn = _live_conn()

    def _bad_verify(c, target, *, privileges, policies):
        raise TargetRefused("not_worker_role")

    cache = VerifiedConnectionCache(
        _TARGET,
        application_name="test",
        privileges=(),
        policies=frozenset(),
        connect=_fake_connect(conn),
    )

    with patch("origenlab_worker.database.verify_worker_connection", side_effect=_bad_verify):
        with pytest.raises(TargetRefused) as exc:
            cache.get()
        assert exc.value.code == "not_worker_role"

    # Nothing is cached — the next call would try again.
    assert cache._conn is None
    assert cache.verification_count == 0


def test_a_failed_verification_is_refused_on_every_call() -> None:
    """Every call returns the same refusal until the underlying reason is fixed."""
    conn = _live_conn()

    def _bad_verify(c, target, *, privileges, policies):
        raise TargetRefused("worker_grant_missing")

    cache = VerifiedConnectionCache(
        _TARGET,
        application_name="test",
        privileges=(),
        policies=frozenset(),
        connect=_fake_connect(conn),
    )

    with patch("origenlab_worker.database.verify_worker_connection", side_effect=_bad_verify):
        for _ in range(3):
            with pytest.raises(TargetRefused):
                cache.get()

    assert cache.verification_count == 0


# ── thread safety: concurrent calls don't double-verify ──────────────────────────

def test_concurrent_get_calls_on_separate_instances_each_verify_once() -> None:
    """Two threads using separate cache instances each verify exactly once."""
    conn_a = _live_conn()
    conn_b = _live_conn()
    counts = [0, 0]

    def make_verify(idx):
        def _verify(c, target, *, privileges, policies):
            counts[idx] += 1
        return _verify

    results: list = []
    errors: list = []

    def worker(conn, idx):
        try:
            cache = VerifiedConnectionCache(
                _TARGET,
                application_name="test",
                privileges=(),
                policies=frozenset(),
                connect=_fake_connect(conn),
            )
            with patch("origenlab_worker.database.verify_worker_connection",
                       side_effect=make_verify(idx)):
                for _ in range(10):
                    results.append(cache.get())
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker, args=(conn_a, 0))
    t2 = threading.Thread(target=worker, args=(conn_b, 1))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors
    assert counts == [1, 1], "each thread's cache verifies exactly once"
