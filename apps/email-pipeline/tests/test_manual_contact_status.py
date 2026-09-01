"""Tests for the manual contact lifecycle sidecar (active/inactive/hold)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.manual_contact_status import (
    HARD_BLOCK_STATUSES,
    ensure_manual_contact_status_table,
    fetch_manual_contact_status,
    load_hard_block_norms,
    load_manual_status_map,
    normalize_manual_contact_email,
    upsert_manual_contact_status,
    validate_manual_contact_status_payload,
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(tmp_path / "t.sqlite"))
    ensure_manual_contact_status_table(c)
    yield c
    c.close()


def test_hard_block_statuses_are_inactive_and_hold() -> None:
    assert HARD_BLOCK_STATUSES == frozenset({"inactive", "hold"})


def test_normalize_manual_contact_email() -> None:
    assert normalize_manual_contact_email("  CarolinaLobo@PharmaIsa.CL ") == "carolinalobo@pharmaisa.cl"


def test_validate_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="no válido"):
        validate_manual_contact_status_payload(email="a@b.cl", status="banana")


def test_upsert_and_fetch_roundtrip(conn: sqlite3.Connection) -> None:
    payload = validate_manual_contact_status_payload(
        email="carolinalobo@pharmaisa.cl",
        status="inactive",
        organization_domain="pharmaisa.cl",
        organization_name="Pharma Isa",
        role_label="Control de Calidad",
        reason="No longer works at Pharma Isa",
        evidence="Operator evidence, effective 06 April",
        effective_at="2026-04-06",
        updated_by="operator",
    )
    upsert_manual_contact_status(conn, payload=payload)
    row = fetch_manual_contact_status(conn, "carolinalobo@pharmaisa.cl")
    assert row is not None
    assert row["status"] == "inactive"
    assert row["organization_name"] == "Pharma Isa"
    assert row["effective_at"] == "2026-04-06"


def test_upsert_is_idempotent_update(conn: sqlite3.Connection) -> None:
    p1 = validate_manual_contact_status_payload(email="a@b.cl", status="hold", reason="r1")
    upsert_manual_contact_status(conn, payload=p1)
    p2 = validate_manual_contact_status_payload(email="a@b.cl", status="active", reason="r2")
    upsert_manual_contact_status(conn, payload=p2)
    row = fetch_manual_contact_status(conn, "a@b.cl")
    assert row["status"] == "active"
    assert row["reason"] == "r2"
    count = conn.execute("SELECT COUNT(*) FROM manual_contact_status").fetchone()[0]
    assert count == 1


def test_load_hard_block_norms_only_inactive_and_hold(conn: sqlite3.Connection) -> None:
    for email, status in (
        ("carolinalobo@pharmaisa.cl", "inactive"),
        ("someone-onhold@pharmaisa.cl", "hold"),
        ("cristianrios@pharmaisa.cl", "active"),
    ):
        upsert_manual_contact_status(
            conn, payload=validate_manual_contact_status_payload(email=email, status=status)
        )
    blocked = load_hard_block_norms(conn)
    assert blocked == frozenset({"carolinalobo@pharmaisa.cl", "someone-onhold@pharmaisa.cl"})


def test_load_manual_status_map_includes_active(conn: sqlite3.Connection) -> None:
    upsert_manual_contact_status(
        conn, payload=validate_manual_contact_status_payload(email="cristianrios@pharmaisa.cl", status="active")
    )
    m = load_manual_status_map(conn)
    assert m["cristianrios@pharmaisa.cl"] == "active"


def test_seed_pharma_isa_facts(conn: sqlite3.Connection) -> None:
    """Regression fixture matching the operator evidence supplied for this task."""
    upsert_manual_contact_status(
        conn,
        payload=validate_manual_contact_status_payload(
            email="carolinalobo@pharmaisa.cl",
            status="inactive",
            organization_domain="pharmaisa.cl",
            organization_name="Pharma Isa",
            reason="No longer works at Pharma Isa",
            evidence="Operator evidence: no longer employed as of 06 April",
            effective_at="2026-04-06",
            updated_by="operator",
        ),
    )
    for email in (
        "cristianrios@pharmaisa.cl",
        "jeanettetorres@pharmaisa.cl",
        "edgarjofre@pharmaisa.cl",
        "maribelcastillo@pharmaisa.cl",
    ):
        upsert_manual_contact_status(
            conn,
            payload=validate_manual_contact_status_payload(
                email=email,
                status="active",
                organization_domain="pharmaisa.cl",
                organization_name="Pharma Isa",
                role_label="Control de Calidad",
                reason="Confirmed replacement / control-quality contact",
                updated_by="operator",
            ),
        )
    assert load_hard_block_norms(conn) == frozenset({"carolinalobo@pharmaisa.cl"})
    maribel = fetch_manual_contact_status(conn, "maribelcastillo@pharmaisa.cl")
    assert maribel["status"] == "active"
    # No CC semantics stored anywhere on this row — role_label is descriptive only.
    assert "cc" not in maribel


def test_no_row_created_for_fernanda_unverified_contact(conn: sqlite3.Connection) -> None:
    """Per task instructions: leave the unresolved Fernanda/Ligia mailto mismatch unseeded."""
    assert fetch_manual_contact_status(conn, "fernandafarias@pharmaisa.cl") is None
    assert fetch_manual_contact_status(conn, "ligiaromo@pharmaisa.cl") is None
