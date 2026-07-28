"""Synthetic tests for commercial identity read model (PR2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_identity.builder import (
    apply_identity_build,
    plan_identity_build,
    require_explicit_sqlite_path,
    run_identity_build,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    ORIGIN_LABDELIVERY_ARCHIVE,
    ORIGIN_ORIGENLAB_GMAIL,
    ORIGIN_RESEARCH,
    REASON_CONSUMER_DOMAIN_REFUSED,
    REASON_DOMAIN_CONFLICTING_ORGS,
    REASON_EMAIL_CONFLICTING_ORGS,
    REASON_INSTITUTIONAL_DOMAIN,
)
from origenlab_email_pipeline.commercial_identity.ids import (
    stable_account_id_for_domain,
    stable_contact_id,
)
from origenlab_email_pipeline.commercial_identity.models import SourceIdentityRow
from origenlab_email_pipeline.commercial_identity.normalize import normalize_identity_email
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_identity.schema import ensure_commercial_identity_tables
from origenlab_email_pipeline.lead_research.commercial_action_buckets import (
    BUCKET_ALREADY_CONTACTED,
    derive_commercial_action_bucket,
)


def _row(**kwargs: object) -> SourceIdentityRow:
    return SourceIdentityRow(
        source_table=str(kwargs.get("source_table") or "contact_master"),
        source_record_id=str(kwargs.get("source_record_id") or kwargs.get("email_raw") or "x"),
        source_plane=str(kwargs.get("source_plane") or "contact_master"),
        origin_plane=str(kwargs.get("origin_plane") or "business_mart"),
        email_raw=kwargs.get("email_raw"),  # type: ignore[arg-type]
        display_name=kwargs.get("display_name"),  # type: ignore[arg-type]
        role=kwargs.get("role"),  # type: ignore[arg-type]
        organization_name=kwargs.get("organization_name"),  # type: ignore[arg-type]
        domain_raw=kwargs.get("domain_raw"),  # type: ignore[arg-type]
        evidence_at=kwargs.get("evidence_at"),  # type: ignore[arg-type]
        extra=dict(kwargs.get("extra") or {}),  # type: ignore[arg-type]
    )


def test_exact_email_joins_same_contact_across_sources() -> None:
    rows = [
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", origin_plane=ORIGIN_ORIGENLAB_GMAIL),
        _row(
            email_raw="a@hospital.cl",
            organization_name="Hospital Sur",
            source_table="commercial_deal",
            source_plane="commercial_deal",
            origin_plane="commercial_deal",
            source_record_id="deal-1",
        ),
    ]
    res = resolve_identity(rows)
    assert len(res.contacts) == 1
    assert res.contacts[0].normalized_email == "a@hospital.cl"


def test_email_case_and_whitespace_normalize() -> None:
    assert normalize_identity_email("  Ada.Lovelace@Hospital.CL ") == "ada.lovelace@hospital.cl"
    rows = [
        _row(email_raw="  Ada.Lovelace@Hospital.CL ", organization_name="Hospital Sur", domain_raw="hospital.cl"),
        _row(email_raw="ada.lovelace@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl"),
    ]
    res = resolve_identity(rows)
    assert len(res.contacts) == 1
    assert res.contacts[0].contact_id == stable_contact_id("ada.lovelace@hospital.cl")


def test_invalid_or_missing_emails_do_not_create_contacts() -> None:
    rows = [
        _row(email_raw=None, organization_name="Hospital Sur", domain_raw="hospital.cl"),
        _row(email_raw="not-an-email", organization_name="Hospital Sur", domain_raw="hospital.cl"),
        _row(email_raw="   ", organization_name="Hospital Sur", domain_raw="hospital.cl"),
    ]
    res = resolve_identity(rows)
    assert res.contacts == []
    assert res.metrics["records_without_usable_email"] >= 3


def test_two_contacts_institutional_domain_one_account() -> None:
    rows = [
        _row(email_raw="one@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl"),
        _row(email_raw="two@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl"),
        _row(
            email_raw=None,
            organization_name="Hospital Sur",
            domain_raw="hospital.cl",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="hospital.cl",
        ),
    ]
    res = resolve_identity(rows)
    assert len(res.accounts) == 1
    assert res.accounts[0].primary_domain == "hospital.cl"
    assert len(res.contacts) == 2
    assert {c.account_id for c in res.contacts} == {res.accounts[0].account_id}
    assert all(c.account_link_method == REASON_INSTITUTIONAL_DOMAIN for c in res.contacts)


def test_consumer_domain_does_not_auto_link() -> None:
    rows = [
        _row(email_raw="person@gmail.com", organization_name="Hospital Sur", domain_raw="gmail.com"),
        _row(
            email_raw="buyer@hospital.cl",
            organization_name="Hospital Sur",
            domain_raw="hospital.cl",
        ),
    ]
    res = resolve_identity(rows)
    gmail = next(c for c in res.contacts if c.normalized_email == "person@gmail.com")
    assert gmail.account_id is None
    assert any(c.reason_code == REASON_CONSUMER_DOMAIN_REFUSED for c in res.conflicts)


def test_similar_org_names_do_not_silently_merge() -> None:
    rows = [
        _row(email_raw="a@alpha.cl", organization_name="Universidad de Chile", domain_raw="alpha.cl"),
        _row(email_raw="b@beta.cl", organization_name="Universidad de Santiago", domain_raw="beta.cl"),
    ]
    res = resolve_identity(rows)
    assert len(res.accounts) == 2
    assert {a.primary_domain for a in res.accounts} == {"alpha.cl", "beta.cl"}


def test_exact_email_conflicting_orgs_produces_conflict() -> None:
    rows = [
        _row(email_raw="shared@hospital.cl", organization_name="Hospital Sur"),
        _row(email_raw="shared@hospital.cl", organization_name="Clinica Norte"),
    ]
    res = resolve_identity(rows)
    assert len(res.contacts) == 1
    assert res.contacts[0].account_id is None
    assert any(c.reason_code == REASON_EMAIL_CONFLICTING_ORGS for c in res.conflicts)


def test_institutional_domain_conflicting_orgs_produces_conflict() -> None:
    rows = [
        _row(
            organization_name="Hospital Sur",
            domain_raw="hospital.cl",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="hospital.cl-a",
        ),
        _row(
            organization_name="Municipalidad X",
            domain_raw="hospital.cl",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="hospital.cl-b",
        ),
    ]
    res = resolve_identity(rows)
    assert len(res.accounts) == 1
    assert res.accounts[0].identity_status == "needs_review"
    assert any(c.reason_code == REASON_DOMAIN_CONFLICTING_ORGS for c in res.conflicts)


def test_same_display_name_different_emails_are_separate_contacts() -> None:
    rows = [
        _row(email_raw="one@hospital.cl", display_name="Ada Lovelace", organization_name="Hospital Sur"),
        _row(email_raw="two@hospital.cl", display_name="Ada Lovelace", organization_name="Hospital Sur"),
    ]
    res = resolve_identity(rows)
    assert len(res.contacts) == 2
    assert {c.normalized_email for c in res.contacts} == {"one@hospital.cl", "two@hospital.cl"}


def test_stable_account_ids_independent_of_input_order() -> None:
    rows_a = [
        _row(email_raw="b@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl"),
    ]
    rows_b = list(reversed(rows_a))
    a = resolve_identity(rows_a)
    b = resolve_identity(rows_b)
    assert [x.account_id for x in a.accounts] == [x.account_id for x in b.accounts]
    assert a.accounts[0].account_id == stable_account_id_for_domain("hospital.cl")


def test_stable_contact_ids_independent_of_input_order() -> None:
    rows_a = [
        _row(email_raw="b@hospital.cl", organization_name="Hospital Sur"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur"),
    ]
    a = resolve_identity(rows_a)
    b = resolve_identity(list(reversed(rows_a)))
    assert sorted(c.contact_id for c in a.contacts) == sorted(c.contact_id for c in b.contacts)


def test_rebuild_unchanged_evidence_idempotent_ids() -> None:
    rows = [
        _row(
            email_raw="a@hospital.cl",
            organization_name="Hospital Sur",
            domain_raw="hospital.cl",
            evidence_at="2024-01-01",
        )
    ]
    first = resolve_identity(rows)
    second = resolve_identity(rows)
    assert [c.contact_id for c in first.contacts] == [c.contact_id for c in second.contacts]
    assert [a.account_id for a in first.accounts] == [a.account_id for a in second.accounts]
    assert [e.evidence_id for e in first.evidence] == [e.evidence_id for e in second.evidence]


def test_first_last_evidence_timestamps() -> None:
    rows = [
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2024-01-10"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2023-05-01"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2024-06-15"),
    ]
    res = resolve_identity(rows)
    c = res.contacts[0]
    assert c.first_evidence_at == "2023-05-01"
    assert c.last_evidence_at == "2024-06-15"


def test_missing_timestamps_remain_missing() -> None:
    rows = [
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at=None),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at=""),
    ]
    res = resolve_identity(rows)
    c = res.contacts[0]
    assert c.first_evidence_at is None
    assert c.last_evidence_at is None
    # Ensure we did not inject "now"
    assert all(e.evidence_at is None for e in res.evidence if e.subject_id == c.contact_id)


def test_origins_remain_distinguishable() -> None:
    rows = [
        _row(email_raw="o@hospital.cl", organization_name="Hospital Sur", origin_plane=ORIGIN_ORIGENLAB_GMAIL),
        _row(
            email_raw="l@old.cl",
            organization_name="Lab Cliente",
            origin_plane=ORIGIN_LABDELIVERY_ARCHIVE,
            domain_raw="old.cl",
        ),
        _row(
            email_raw="r@research.cl",
            organization_name="Research Org",
            origin_plane=ORIGIN_RESEARCH,
            source_table="lead_research_prospect",
            source_plane="lead_research_prospect",
            domain_raw="research.cl",
        ),
    ]
    res = resolve_identity(rows)
    origins = {e.origin_plane for e in res.evidence}
    assert ORIGIN_ORIGENLAB_GMAIL in origins
    assert ORIGIN_LABDELIVERY_ARCHIVE in origins
    assert ORIGIN_RESEARCH in origins
    assert res.metrics["research_only_identities"] >= 1


def test_research_only_not_labeled_customer() -> None:
    rows = [
        _row(
            email_raw="r@research.cl",
            organization_name="Research Org",
            origin_plane=ORIGIN_RESEARCH,
            source_table="lead_research_prospect",
            source_plane="lead_research_prospect",
            domain_raw="research.cl",
        )
    ]
    res = resolve_identity(rows)
    # No customer/stage fields exist on contact or account records.
    c = res.contacts[0]
    assert not hasattr(c, "is_customer")
    assert not hasattr(c, "opportunity_stage")
    assert "customer" not in c.identity_status
    assert res.metrics["opportunity_stage_fields_inferred"] is False
    assert res.metrics["next_action_fields_inferred"] is False


def _seed_fixture(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          contact_name_best TEXT,
          domain TEXT,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER
        );
        INSERT INTO organization_master VALUES (
          'hospital.cl', 'Hospital Sur', 'institution', '2023-01-01', '2024-01-01', 10, 2
        );
        INSERT INTO contact_master VALUES (
          'one@hospital.cl', 'One', 'hospital.cl', 'Hospital Sur', 'institution',
          '2023-02-01', '2024-02-01', 3
        );
        INSERT INTO contact_master VALUES (
          'two@hospital.cl', 'Two', 'hospital.cl', 'Hospital Sur', 'institution',
          '2023-03-01', '2024-03-01', 2
        );
        """
    )
    conn.commit()
    conn.close()


def test_dry_run_performs_no_write(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    _seed_fixture(db)
    summary = run_identity_build(sqlite_path=db, apply=False)
    assert summary["applied"] is False
    conn = sqlite3.connect(str(db))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'commercial_identity_%'"
        )
    }
    conn.close()
    assert tables == set()


def test_apply_writes_atomically(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    _seed_fixture(db)
    summary = run_identity_build(sqlite_path=db, apply=True)
    assert summary["applied"] is True
    conn = sqlite3.connect(str(db))
    n_accounts = conn.execute("SELECT COUNT(*) FROM commercial_identity_account").fetchone()[0]
    n_contacts = conn.execute("SELECT COUNT(*) FROM commercial_identity_contact").fetchone()[0]
    conn.close()
    assert n_accounts == 1
    assert n_contacts == 2


def test_injected_failure_rolls_back(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    _seed_fixture(db)
    plan = plan_identity_build(sqlite_path=db, apply=True)

    def boom(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        apply_identity_build(plan, inject_failure=boom)

    conn = sqlite3.connect(str(db))
    # Schema may exist from ensure, but rebuildable data must be empty after rollback
    # if ensure+clear+write all rolled back. SQLite DDL is transactional in modern SQLite.
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='commercial_identity_account'"
    ).fetchone()[0]
    if n:
        count = conn.execute("SELECT COUNT(*) FROM commercial_identity_account").fetchone()[0]
        assert count == 0
    conn.close()


def test_require_explicit_sqlite_path(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        require_explicit_sqlite_path(None)
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(Exception):
        require_explicit_sqlite_path(missing)


def test_no_opportunity_stage_or_next_action_fields() -> None:
    rows = [_row(email_raw="a@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl")]
    res = resolve_identity(rows)
    assert "opportunity_stage" not in res.metrics or res.metrics["opportunity_stage_fields_inferred"] is False
    for c in res.contacts:
        assert not hasattr(c, "next_action")
        assert not hasattr(c, "commercial_stage")
    for a in res.accounts:
        assert not hasattr(a, "won_lost")
        assert not hasattr(a, "fulfilment_status")


def test_idempotent_apply_rebuild(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    _seed_fixture(db)
    run_identity_build(sqlite_path=db, apply=True)
    conn = sqlite3.connect(str(db))
    ids1 = [r[0] for r in conn.execute("SELECT contact_id FROM commercial_identity_contact ORDER BY contact_id")]
    conn.close()
    run_identity_build(sqlite_path=db, apply=True)
    conn = sqlite3.connect(str(db))
    ids2 = [r[0] for r in conn.execute("SELECT contact_id FROM commercial_identity_contact ORDER BY contact_id")]
    conn.close()
    assert ids1 == ids2


# --- Regression: existing buckets / PR1 / suppression semantics unchanged ---


def test_regression_commercial_action_bucket_unchanged() -> None:
    row = {
        "classification": "manual_outreach_candidate",
        "email": "x@hospital.cl",
        "gmail_sent_count": 1,
        "gmail_received_count": 0,
        "block_reason": None,
    }
    assert derive_commercial_action_bucket(row) == BUCKET_ALREADY_CONTACTED


def test_regression_pr1_normalize_valid_email_still_works() -> None:
    from origenlab_email_pipeline.qa.commercial_truth_audit.emails import normalize_valid_email

    assert normalize_valid_email("  A@B.CL ") == "a@b.cl"


def test_regression_suppression_table_helper_unchanged() -> None:
    from origenlab_email_pipeline.contact_email_suppression import (
        CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL,
        ensure_contact_email_suppression_table,
    )

    conn = sqlite3.connect(":memory:")
    ensure_contact_email_suppression_table(conn)
    assert "contact_email_suppression" in CONTACT_EMAIL_SUPPRESSION_SCHEMA_SQL
    n = conn.execute("SELECT COUNT(*) FROM contact_email_suppression").fetchone()[0]
    assert n == 0
    conn.close()


def test_ensure_schema_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite"
    conn = sqlite3.connect(str(db))
    ensure_commercial_identity_tables(conn)
    ensure_commercial_identity_tables(conn)
    conn.commit()
    conn.close()
