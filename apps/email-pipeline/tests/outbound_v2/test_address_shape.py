"""The application address shape and the database address shape are the same pattern."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from origenlab_email_pipeline.outbound_v2 import ADDRESS_SHAPE_PATTERN, normalize_address

MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "supabase"
    / "migrations"
    / "20260908120200_slice0_outbound_address_shape_reject_delimiters.sql"
)

#: The two CHECK constraints that must carry the pattern.
CONSTRAINTS = ("campaign_recipient_address_shape", "send_attempt_address_shape")


def _patterns_from_migration() -> dict[str, str]:
    """constraint name → the regex literal its ``address_norm ~ '...'`` test carries."""
    sql = MIGRATION.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for name in CONSTRAINTS:
        for match in re.finditer(
            rf"constraint {name}\b.*?address_norm ~ '([^']+)'", sql, re.DOTALL
        ):
            found[name] = match.group(1)
    return found


def test_migration_exists() -> None:
    assert MIGRATION.is_file(), MIGRATION


@pytest.mark.parametrize("constraint", CONSTRAINTS)
def test_the_database_constraint_carries_the_application_pattern(constraint) -> None:
    """An address the CHECK would reject must never reach a freeze plan, and vice versa."""
    assert _patterns_from_migration().get(constraint) == ADDRESS_SHAPE_PATTERN


@pytest.mark.parametrize(
    "raw",
    [
        "<sales@steinlite.com>",
        "Ariel<asalvatierra@ceaf.cl>",
        "<k.montenegro@soviquim.cl>",
        '"camilo.alfaro"@indisa.cl',
        "ventas@uni.cl,",
        "ventas@uni.cl;",
    ],
)
def test_a_delimiter_is_not_part_of_an_address(raw) -> None:
    """A header fragment an adapter forgot to extract is unusable, not silently frozen."""
    assert normalize_address(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "compras@universidad.cl",
        "s4system-prod3+camanchaca.doc1426598569@ansmtp.ariba.com",
        "info+canned.response@megadepot.com",
        "no-reply11698+179810@epmas.cl",
        "j.perez@uchile.cl",
        "lab_central@uc.cl",
        "compras@sub.dominio.co.uk",
        "contacto@ñandu.cl",
    ],
)
def test_a_legitimate_address_is_still_accepted(raw) -> None:
    assert normalize_address(raw) == raw
