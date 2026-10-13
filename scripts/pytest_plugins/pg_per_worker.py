"""pytest plugin, loaded by scripts/run_pytest.sh: one Postgres database per xdist worker.

The V1 Postgres test files all use the one database scripts/test_db.sh migrated. Run in
parallel they would see and race each other's rows. On each xdist worker this clones that
database (`create database ... template`, a file copy) to `<name>_<worker>_test`, points the
variable at the clone for the rest of the run, and drops the clone at exit — the way
pytest-django isolates workers. Serial runs, and variables that are unset, are left alone.

Only a loopback database whose name ends in `_test` is ever cloned or dropped.
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import urlsplit, urlunsplit

VARIABLES = ("ORIGENLAB_TEST_POSTGRES_URL", "ORIGENLAB_POSTGRES_TEST_URL")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _checked(url: str) -> tuple:
    parts = urlsplit(url)
    database = parts.path.lstrip("/")
    if parts.hostname not in LOOPBACK:
        raise ValueError(f"refusing a non-loopback test database host: {parts.hostname!r}")
    if not re.fullmatch(r"[a-z0-9_]+_test", database):
        raise ValueError(f"refusing a database not named *_test: {database!r}")
    return parts, database


def clone_url(url: str, worker: str) -> str:
    if not re.fullmatch(r"gw[0-9]+", worker):
        raise ValueError(f"unexpected xdist worker id: {worker!r}")
    parts, database = _checked(url)
    return urlunsplit(parts._replace(path=f"/{database[: -len('_test')]}_{worker}_test"))


def maintenance_url(url: str) -> str:
    parts, _ = _checked(url)
    return urlunsplit(parts._replace(path="/postgres"))


def _database(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _clone(url: str, worker: str) -> str:
    import psycopg

    target = clone_url(url, worker)
    with psycopg.connect(maintenance_url(url), autocommit=True) as conn:
        conn.execute(f"drop database if exists {_database(target)}")
        # Two workers cloning the same template at the same moment can briefly see each other
        # as "other users" of it; that is transient.
        for attempt in range(40):
            try:
                conn.execute(f"create database {_database(target)} template {_database(url)}")
                break
            except psycopg.errors.ObjectInUse:
                if attempt == 39:
                    raise
                time.sleep(0.25)
    return target


def _drop(url: str, target: str) -> None:
    import psycopg

    with psycopg.connect(maintenance_url(url), autocommit=True) as conn:
        conn.execute(f"drop database if exists {_database(target)} with (force)")


def pytest_configure(config) -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if not worker:
        return
    for name in VARIABLES:
        url = os.environ.get(name, "").strip()
        if not url:
            continue
        target = _clone(url, worker)
        os.environ[name] = target
        config.add_cleanup(lambda url=url, target=target: _drop(url, target))
