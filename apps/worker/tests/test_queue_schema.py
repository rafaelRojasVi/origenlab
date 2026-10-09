"""The vendored Procrastinate schema in the migration is exactly the pinned library's.

An upgrade of `procrastinate` without a matching migration would leave the worker's SQL and the
database's functions disagreeing; this fails first, in CI, before anything is deployed."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from procrastinate.schema import SchemaManager

MIGRATION = (Path(__file__).resolve().parents[3] / "supabase" / "migrations"
             / "20261006180000_slice4_procrastinate_triage_queue.sql")
BEGIN = "-- ───────────────────────── vendored: procrastinate 3.10.0 schema ─────────────────────────\n"
END = "-- ───────────────────────── end of vendored schema ─────────────────────────"


def test_the_pinned_version_is_the_vendored_one() -> None:
    assert version("procrastinate") == "3.10.0"


def test_the_migration_carries_the_librarys_schema_from_its_enums_to_the_end() -> None:
    schema = SchemaManager.get_schema()
    expected = schema[schema.index("-- Enums"):].rstrip() + "\n"
    text = MIGRATION.read_text(encoding="utf-8")
    vendored = text[text.index(BEGIN) + len(BEGIN):text.index(END)]
    assert vendored == expected


def test_the_left_out_preamble_only_creates_plpgsql() -> None:
    schema = SchemaManager.get_schema()
    preamble = schema[:schema.index("-- Enums")]
    statements = [line for line in preamble.splitlines() if line.strip().upper().startswith(("CREATE", "ALTER", "DROP"))]
    assert [s.strip() for s in statements] == ["CREATE EXTENSION IF NOT EXISTS plpgsql WITH SCHEMA pg_catalog;"]
