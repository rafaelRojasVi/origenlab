"""The application vocabulary and the database vocabulary are the same closed list."""

from __future__ import annotations

import re
from pathlib import Path

from origenlab_email_pipeline.outbound_v2.reasons import (
    EXCLUSION_VOCABULARY,
    OVERRIDABLE_REASONS,
)

MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "supabase"
    / "migrations"
    / "20260908120000_slice0_outbound_campaign_content_criteria_exclusions.sql"
)


def _vocabulary_from_migration() -> frozenset[str]:
    sql = MIGRATION.read_text(encoding="utf-8")
    marker = "campaign_recipient_exclusion_reasons_vocabulary"
    body = sql[sql.index(marker) : sql.index("]::text[]", sql.index(marker))]
    return frozenset(re.findall(r"'([a-z_]+)'", body[body.index("array["):]))


def test_migration_exists() -> None:
    assert MIGRATION.is_file(), MIGRATION


def test_application_vocabulary_equals_the_database_vocabulary() -> None:
    """A reason the CHECK would reject must never reach a freeze plan."""
    assert _vocabulary_from_migration() == EXCLUSION_VOCABULARY


def test_overridable_reasons_are_part_of_the_vocabulary() -> None:
    assert OVERRIDABLE_REASONS < EXCLUSION_VOCABULARY
