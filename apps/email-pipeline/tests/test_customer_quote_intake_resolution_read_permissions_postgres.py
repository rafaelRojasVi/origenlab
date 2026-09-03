"""Real-Postgres coverage proving 0046's least-privilege grant actually
takes effect -- never merely source-inspected (see
test_customer_quote_intake_resolution_read_migration.py for the
source-level assertions on the same migration).

Requires a disposable Postgres reachable via ``ALEMBIC_DATABASE_URL`` (or
``ORIGENLAB_POSTGRES_URL``), migrated at least as far as 0045, with the
``origenlab_api_ro``/``origenlab_api_rw`` roles already created --
matching the guarded ``DO $$ ... IF EXISTS (pg_roles) ...`` style every
migration in this chain uses: if a role doesn't exist, its GRANT/REVOKE is
a silent no-op and there is nothing to prove here, so the whole module
skips.

Drives the migration chain itself with ``alembic.command`` (down to 0045,
up to head, back down to 0045), checking effective privileges with
``has_table_privilege``/``has_schema_privilege`` at each checkpoint. Always
leaves the database at head when done -- every other Postgres-backed test
in this repo expects the shared disposable database already migrated to
head.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

REPO = Path(__file__).resolve().parents[1]

_RO_ROLE = "origenlab_api_ro"
_RW_ROLE = "origenlab_api_rw"

_VIEW = "api.v_lead_intel_prospect_evidence"
_SOURCE_TABLE = "lead_intel.prospect"
_SOURCE_SCHEMA = "lead_intel"

_PRE_0046 = "20260902_0045"
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
        "Postgres migrated at least to 20260902_0045, with origenlab_api_ro/"
        "origenlab_api_rw roles already created, to run the 0046 permission "
        "round-trip test."
    ),
)


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "alembic"))
    return cfg


def _has_table_privilege(role: str, relation: str, privilege: str) -> bool:
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


def _has_schema_privilege(role: str, schema: str, privilege: str) -> bool:
    import psycopg

    assert _URL is not None
    with psycopg.connect(_psycopg_url(_URL), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT has_schema_privilege(%s, %s, %s)",
                (role, schema, privilege),
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


def test_0046_grants_narrow_view_select_without_source_schema_or_table_access() -> None:
    from alembic import command

    cfg = _alembic_config()

    # Known baseline: pre-0046 -- the view doesn't exist yet, no grant on
    # it, and (per every prior migration) no USAGE on lead_intel at all.
    command.downgrade(cfg, _PRE_0046)

    assert not _has_schema_privilege(_RO_ROLE, _SOURCE_SCHEMA, "USAGE")
    assert not _has_schema_privilege(_RW_ROLE, _SOURCE_SCHEMA, "USAGE")

    # --- upgrade to head: both roles get SELECT on the narrow view only ---
    command.upgrade(cfg, _HEAD)

    assert _has_table_privilege(_RO_ROLE, _VIEW, "SELECT")
    assert _has_table_privilege(_RW_ROLE, _VIEW, "SELECT")

    # Least privilege: no USAGE on the source schema, no SELECT on the
    # source table -- a plain view runs with its owner's privileges, not
    # the querying role's, so the API roles never needed either.
    assert not _has_schema_privilege(_RO_ROLE, _SOURCE_SCHEMA, "USAGE")
    assert not _has_schema_privilege(_RW_ROLE, _SOURCE_SCHEMA, "USAGE")
    assert not _has_table_privilege(_RO_ROLE, _SOURCE_TABLE, "SELECT")
    assert not _has_table_privilege(_RW_ROLE, _SOURCE_TABLE, "SELECT")

    # --- downgrade back to 0045: the view SELECT grant must be gone, and
    # the schema must still never have been touched ------------------------
    command.downgrade(cfg, _PRE_0046)

    assert not _has_schema_privilege(_RO_ROLE, _SOURCE_SCHEMA, "USAGE")
    assert not _has_schema_privilege(_RW_ROLE, _SOURCE_SCHEMA, "USAGE")


def test_0046_ro_role_can_actually_select_the_view_as_itself() -> None:
    """Proves the grant is not just present but *sufficient*: connecting
    as origenlab_api_ro (via SET ROLE) and running a real SELECT against
    the view succeeds with zero additional privileges."""

    import psycopg

    assert _URL is not None
    with psycopg.connect(_psycopg_url(_URL), connect_timeout=5, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Session-scoped (not LOCAL): autocommit means each statement is
            # its own implicit transaction, so SET LOCAL would reset before
            # the SELECT below ever ran as the target role.
            cur.execute(f"SET ROLE {_RO_ROLE}")
            try:
                cur.execute(f"SELECT * FROM {_VIEW} LIMIT 0")
            finally:
                cur.execute("RESET ROLE")
