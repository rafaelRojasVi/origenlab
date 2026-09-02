"""Real-Postgres coverage proving 0043's `GRANT UPDATE` and its downgrade's
`REVOKE UPDATE` + view SELECT re-grants actually take effect -- never
merely source-inspected (see test_customer_quote_workflow_migration.py for
the source-level assertions on the same migration).

Requires a disposable Postgres reachable via ``ALEMBIC_DATABASE_URL`` (or
``ORIGENLAB_POSTGRES_URL``), migrated at least as far as 0042, with the
``origenlab_api_ro``/``origenlab_api_rw`` roles already created --
matching the guarded ``DO $$ ... IF EXISTS (pg_roles) ...`` style every
migration in this chain uses: if a role doesn't exist, its GRANT/REVOKE is
a silent no-op and there is nothing to prove here, so the whole module
skips.

Drives the migration chain itself with ``alembic.command`` (down to 0042,
up to head, back down to 0042), checking effective privileges with
``has_table_privilege`` at each checkpoint. Always leaves the database at
head when done -- every other Postgres-backed test in this repo expects
the shared disposable database already migrated to head.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

REPO = Path(__file__).resolve().parents[1]

_RO_ROLE = "origenlab_api_ro"
_RW_ROLE = "origenlab_api_rw"

_QUOTE_TABLE = "commercial.customer_quote"
_REVISION_TABLE = "commercial.customer_quote_revision"
_QUOTE_VIEW = "api.v_commercial_customer_quote"
_REVISION_VIEW = "api.v_commercial_customer_quote_revision"

_PRE_0043 = "20260901_0042"
_HEAD = "head"


def _raw_database_url() -> str | None:
    url = (
        os.environ.get("ALEMBIC_DATABASE_URL")
        or os.environ.get("ORIGENLAB_POSTGRES_URL")
        or ""
    ).strip()
    return url or None


def _psycopg_url(url: str) -> str:
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def _roles_ready(url: str) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(_psycopg_url(url), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                    ([_RO_ROLE, _RW_ROLE],),
                )
                found = {row[0] for row in cur.fetchall()}
        return {_RO_ROLE, _RW_ROLE} <= found
    except Exception:
        return False


_URL = _raw_database_url()
_READY = _URL is not None and _roles_ready(_URL)

pytestmark = pytest.mark.skipif(
    not _READY,
    reason=(
        "Set ALEMBIC_DATABASE_URL (or ORIGENLAB_POSTGRES_URL) to a disposable "
        "Postgres migrated at least to 20260901_0042, with origenlab_api_ro/"
        "origenlab_api_rw roles already created, to run the 0043 permission "
        "round-trip test."
    ),
)


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "alembic"))
    return cfg


def _has_privilege(role: str, relation: str, privilege: str) -> bool:
    import psycopg

    assert _URL is not None
    with psycopg.connect(_psycopg_url(_URL), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT has_table_privilege(%s, %s, %s)",
                (role, relation, privilege),
            )
            row = cur.fetchone()
            assert row is not None
            return bool(row[0])


@pytest.fixture(autouse=True)
def _leave_database_at_head() -> Iterator[None]:
    """Every other Postgres-backed test in this repo expects the shared
    disposable database already migrated to head -- restore that even if
    an assertion below fails midway through the round trip."""

    yield
    from alembic import command

    command.upgrade(_alembic_config(), _HEAD)


def test_0043_update_grant_and_downgrade_revoke_are_effective() -> None:
    from alembic import command

    cfg = _alembic_config()

    # Known baseline: pre-0043. 0043's downgrade only fails closed on
    # adopted/non-draft/CRM-Q2-introduced rows -- harmless against any
    # disposable Postgres actually usable for this test.
    command.downgrade(cfg, _PRE_0043)

    assert not _has_privilege(_RW_ROLE, _QUOTE_TABLE, "UPDATE")
    assert not _has_privilege(_RW_ROLE, _REVISION_TABLE, "UPDATE")

    # --- upgrade through 0043 into 0044 (0044 is a purely additive CHECK
    # widening -- it must not touch these grants) -----------------------
    command.upgrade(cfg, _HEAD)

    assert _has_privilege(_RW_ROLE, _QUOTE_TABLE, "UPDATE")
    assert _has_privilege(_RW_ROLE, _REVISION_TABLE, "UPDATE")

    assert not _has_privilege(_RO_ROLE, _QUOTE_TABLE, "UPDATE")
    assert not _has_privilege(_RO_ROLE, _REVISION_TABLE, "UPDATE")

    assert _has_privilege(_RO_ROLE, _QUOTE_VIEW, "SELECT")
    assert _has_privilege(_RW_ROLE, _QUOTE_VIEW, "SELECT")
    assert _has_privilege(_RO_ROLE, _REVISION_VIEW, "SELECT")
    assert _has_privilege(_RW_ROLE, _REVISION_VIEW, "SELECT")

    # --- downgrade 0044 -> 0043 -> 0042: UPDATE must be gone, SELECT on
    # the recreated views must survive ------------------------------------
    command.downgrade(cfg, _PRE_0043)

    assert not _has_privilege(_RW_ROLE, _QUOTE_TABLE, "UPDATE")
    assert not _has_privilege(_RW_ROLE, _REVISION_TABLE, "UPDATE")

    assert _has_privilege(_RO_ROLE, _QUOTE_VIEW, "SELECT")
    assert _has_privilege(_RW_ROLE, _QUOTE_VIEW, "SELECT")
    assert _has_privilege(_RO_ROLE, _REVISION_VIEW, "SELECT")
    assert _has_privilege(_RW_ROLE, _REVISION_VIEW, "SELECT")
