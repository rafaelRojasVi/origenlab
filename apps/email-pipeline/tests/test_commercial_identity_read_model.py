"""Synthetic tests for commercial identity read model (PR2) + hardening."""

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
    IDENTITY_STATUS_INTERNAL_ACTOR,
    INTERNAL_DOMAINS,
    ORIGIN_LABDELIVERY_ARCHIVE,
    ORIGIN_ORIGENLAB_GMAIL,
    ORIGIN_RESEARCH,
    REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES,
    REASON_COMPETING_ACCOUNT_LINKS,
    REASON_CONSUMER_DOMAIN_REFUSED,
    REASON_CONSUMER_ORG_LINK_WITHHELD,
    REASON_DOMAIN_CONFLICTING_ORGS,
    REASON_EMAIL_COMPETING_DOMAINS,
    REASON_EMAIL_CONFLICTING_ORGS,
    REASON_INSTITUTIONAL_DOMAIN,
    REASON_INTERNAL_DOMAIN_EXCLUDED,
    TRANSACTION_CONTRACT,
)
from origenlab_email_pipeline.commercial_identity.ids import (
    stable_account_id_for_domain,
    stable_account_id_for_name,
    stable_contact_id,
)
from origenlab_email_pipeline.commercial_identity.models import SourceIdentityRow
from origenlab_email_pipeline.commercial_identity.normalize import (
    is_institutional_domain,
    is_internal_domain,
    normalize_identity_email,
)
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_identity.schema import ensure_commercial_identity_tables
from origenlab_email_pipeline.commercial_identity.sources import load_source_identity_rows
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
        _row(
            email_raw="a@hospital.cl",
            organization_name="Hospital Sur",
            origin_plane=ORIGIN_ORIGENLAB_GMAIL,
            source_record_id="cm:a:1",
        ),
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
        _row(
            email_raw="  Ada.Lovelace@Hospital.CL ",
            organization_name="Hospital Sur",
            domain_raw="hospital.cl",
            source_record_id="a1",
        ),
        _row(
            email_raw="ada.lovelace@hospital.cl",
            organization_name="Hospital Sur",
            domain_raw="hospital.cl",
            source_record_id="a2",
        ),
    ]
    res = resolve_identity(rows)
    assert len(res.contacts) == 1
    assert res.contacts[0].contact_id == stable_contact_id("ada.lovelace@hospital.cl")


def test_invalid_or_missing_emails_do_not_create_contacts() -> None:
    rows = [
        _row(email_raw=None, organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="m1"),
        _row(email_raw="not-an-email", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="m2"),
        _row(email_raw="   ", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="m3"),
    ]
    res = resolve_identity(rows)
    assert res.contacts == []
    assert res.metrics["records_without_usable_email"] == 3


def test_two_contacts_institutional_domain_one_account() -> None:
    rows = [
        _row(email_raw="one@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="1"),
        _row(email_raw="two@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="2"),
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
        _row(email_raw="person@gmail.com", organization_name="Hospital Sur", domain_raw="gmail.com", source_record_id="g1"),
        _row(email_raw="buyer@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="h1"),
    ]
    res = resolve_identity(rows)
    gmail = next(c for c in res.contacts if c.normalized_email == "person@gmail.com")
    assert gmail.account_id is None
    assert any(c.reason_code == REASON_CONSUMER_DOMAIN_REFUSED for c in res.conflicts)
    assert res.metrics["consumer_domain_auto_link_refusals"] == 1


def test_similar_org_names_do_not_silently_merge() -> None:
    rows = [
        _row(email_raw="a@alpha.cl", organization_name="Universidad de Chile", domain_raw="alpha.cl", source_record_id="a"),
        _row(email_raw="b@beta.cl", organization_name="Universidad de Santiago", domain_raw="beta.cl", source_record_id="b"),
    ]
    res = resolve_identity(rows)
    assert len([a for a in res.accounts if a.primary_domain]) == 2
    assert {a.primary_domain for a in res.accounts if a.primary_domain} == {"alpha.cl", "beta.cl"}


def test_exact_email_conflicting_orgs_produces_conflict() -> None:
    rows = [
        _row(email_raw="shared@hospital.cl", organization_name="Hospital Sur", source_record_id="s1"),
        _row(email_raw="shared@hospital.cl", organization_name="Clinica Norte", source_record_id="s2"),
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
        _row(email_raw="one@hospital.cl", display_name="Ada Lovelace", organization_name="Hospital Sur", source_record_id="1"),
        _row(email_raw="two@hospital.cl", display_name="Ada Lovelace", organization_name="Hospital Sur", source_record_id="2"),
    ]
    res = resolve_identity(rows)
    assert len(res.contacts) == 2
    assert {c.normalized_email for c in res.contacts} == {"one@hospital.cl", "two@hospital.cl"}


def test_stable_account_ids_independent_of_input_order() -> None:
    rows_a = [
        _row(email_raw="b@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="b"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="a"),
    ]
    a = resolve_identity(rows_a)
    b = resolve_identity(list(reversed(rows_a)))
    assert [x.account_id for x in a.accounts if x.primary_domain] == [
        x.account_id for x in b.accounts if x.primary_domain
    ]
    assert stable_account_id_for_domain("hospital.cl") in {x.account_id for x in a.accounts}


def test_stable_contact_ids_independent_of_input_order() -> None:
    rows_a = [
        _row(email_raw="b@hospital.cl", organization_name="Hospital Sur", source_record_id="b"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", source_record_id="a"),
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
            source_record_id="once",
        )
    ]
    first = resolve_identity(rows)
    second = resolve_identity(rows)
    assert [c.contact_id for c in first.contacts] == [c.contact_id for c in second.contacts]
    assert [a.account_id for a in first.accounts] == [a.account_id for a in second.accounts]
    assert [e.evidence_id for e in first.evidence] == [e.evidence_id for e in second.evidence]


def test_first_last_evidence_timestamps() -> None:
    rows = [
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2024-01-10", source_record_id="t1"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2023-05-01", source_record_id="t2"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2024-06-15", source_record_id="t3"),
    ]
    res = resolve_identity(rows)
    c = res.contacts[0]
    assert c.first_evidence_at == "2023-05-01"
    assert c.last_evidence_at == "2024-06-15"


def test_missing_timestamps_remain_missing() -> None:
    rows = [
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at=None, source_record_id="n1"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="", source_record_id="n2"),
    ]
    res = resolve_identity(rows)
    c = res.contacts[0]
    assert c.first_evidence_at is None
    assert c.last_evidence_at is None
    assert all(e.evidence_at is None for e in res.evidence if e.subject_id == c.contact_id)


def test_origins_remain_distinguishable() -> None:
    rows = [
        _row(
            email_raw="o@hospital.cl",
            organization_name="Hospital Sur",
            origin_plane=ORIGIN_ORIGENLAB_GMAIL,
            source_record_id="o1",
        ),
        _row(
            email_raw="l@old.cl",
            organization_name="Lab Cliente",
            origin_plane=ORIGIN_LABDELIVERY_ARCHIVE,
            domain_raw="old.cl",
            source_record_id="l1",
        ),
        _row(
            email_raw="r@research.cl",
            organization_name="Research Org",
            origin_plane=ORIGIN_RESEARCH,
            source_table="lead_research_prospect",
            source_plane="lead_research_prospect",
            domain_raw="research.cl",
            source_record_id="r1",
        ),
    ]
    res = resolve_identity(rows)
    origins = {e.origin_plane for e in res.evidence}
    assert ORIGIN_ORIGENLAB_GMAIL in origins
    assert ORIGIN_LABDELIVERY_ARCHIVE in origins
    assert ORIGIN_RESEARCH in origins
    assert res.metrics["origenlab_origin_source_assertion_rows"] == 1
    assert res.metrics["labdelivery_origin_source_assertion_rows"] == 1
    assert res.metrics["research_origin_source_assertion_rows"] == 1
    assert res.metrics["canonical_contacts_research_only"] == 1


def test_research_only_not_labeled_customer() -> None:
    rows = [
        _row(
            email_raw="r@research.cl",
            organization_name="Research Org",
            origin_plane=ORIGIN_RESEARCH,
            source_table="lead_research_prospect",
            source_plane="lead_research_prospect",
            domain_raw="research.cl",
            source_record_id="r1",
        )
    ]
    res = resolve_identity(rows)
    c = res.contacts[0]
    assert not hasattr(c, "is_customer")
    assert not hasattr(c, "opportunity_stage")
    assert "customer" not in c.identity_status
    assert res.metrics["opportunity_stage_fields_inferred"] is False
    assert res.metrics["next_action_fields_inferred"] is False


# --- Hardening: internal domains ---


def test_internal_domains_constant_includes_origenlab_and_labdelivery() -> None:
    assert "origenlab.cl" in INTERNAL_DOMAINS
    assert "labdelivery.cl" in INTERNAL_DOMAINS
    assert is_internal_domain("origenlab.cl")
    assert is_internal_domain("labdelivery.cl")
    assert not is_institutional_domain("origenlab.cl")
    assert not is_institutional_domain("labdelivery.cl")


def test_origenlab_internal_contact_not_commercial_account() -> None:
    rows = [
        _row(
            email_raw="ops@origenlab.cl",
            organization_name="OrigenLab",
            domain_raw="origenlab.cl",
            source_record_id="ol1",
        )
    ]
    res = resolve_identity(rows)
    assert len(res.contacts) == 1
    c = res.contacts[0]
    assert c.identity_status == IDENTITY_STATUS_INTERNAL_ACTOR
    assert c.account_id is None
    assert not any(a.primary_domain == "origenlab.cl" for a in res.accounts)
    assert any(x.reason_code == REASON_INTERNAL_DOMAIN_EXCLUDED for x in res.conflicts)


def test_labdelivery_internal_contact_not_commercial_account() -> None:
    rows = [
        _row(
            email_raw="archivo@labdelivery.cl",
            organization_name="Labdelivery",
            domain_raw="labdelivery.cl",
            source_record_id="ld1",
        )
    ]
    res = resolve_identity(rows)
    c = res.contacts[0]
    assert c.identity_status == IDENTITY_STATUS_INTERNAL_ACTOR
    assert c.account_id is None
    assert not any(a.primary_domain == "labdelivery.cl" for a in res.accounts)


# --- Hardening: role_title ---


def test_role_title_from_lead_research_prospect_reaches_contact(tmp_path: Path) -> None:
    db = tmp_path / "role.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE lead_research_prospect (
          id INTEGER PRIMARY KEY,
          email TEXT,
          contact_name TEXT,
          role_title TEXT,
          organization_name TEXT,
          domain TEXT,
          updated_at TEXT
        );
        INSERT INTO lead_research_prospect VALUES (
          1, 'buyer@hospital.cl', 'Ada', 'Jefa de Adquisiciones', 'Hospital Sur', 'hospital.cl', '2024-01-01'
        );
        """
    )
    conn.commit()
    rows = load_source_identity_rows(conn)
    conn.close()
    assert any(r.role == "Jefa de Adquisiciones" for r in rows)
    res = resolve_identity(rows)
    contact = next(c for c in res.contacts if c.normalized_email == "buyer@hospital.cl")
    assert contact.role == "Jefa de Adquisiciones"


def test_legacy_role_column_still_accepted(tmp_path: Path) -> None:
    db = tmp_path / "legacy_role.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE lead_research_prospect (
          id INTEGER PRIMARY KEY,
          email TEXT,
          role TEXT,
          organization_name TEXT,
          domain TEXT
        );
        INSERT INTO lead_research_prospect VALUES (1, 'x@hospital.cl', 'Buyer', 'Hospital Sur', 'hospital.cl');
        """
    )
    conn.commit()
    rows = load_source_identity_rows(conn)
    conn.close()
    assert rows[0].role == "Buyer"


# --- Hardening: competing domains ---


def test_email_domain_vs_explicit_domain_disagreement() -> None:
    rows = [
        _row(
            email_raw="person@alpha.cl",
            organization_name="Alpha Org",
            domain_raw="beta.cl",
            source_record_id="d1",
        ),
        _row(
            organization_name="Alpha Org",
            domain_raw="alpha.cl",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="alpha.cl",
        ),
        _row(
            organization_name="Beta Org",
            domain_raw="beta.cl",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="beta.cl",
        ),
    ]
    res = resolve_identity(rows)
    c = res.contacts[0]
    assert c.account_id is None
    assert any(
        x.reason_code in {REASON_EMAIL_COMPETING_DOMAINS, REASON_COMPETING_ACCOUNT_LINKS}
        for x in res.conflicts
    )
    # Evidence pointers must include real source records.
    competing = next(
        x
        for x in res.conflicts
        if x.reason_code in {REASON_EMAIL_COMPETING_DOMAINS, REASON_COMPETING_ACCOUNT_LINKS}
    )
    assert "source_table" in competing.evidence_pointers_json
    assert "source_record_id" in competing.evidence_pointers_json


def test_multiple_explicit_domains_on_same_email() -> None:
    rows = [
        _row(email_raw="shared@alpha.cl", domain_raw="alpha.cl", organization_name="A", source_record_id="e1"),
        _row(email_raw="shared@alpha.cl", domain_raw="beta.cl", organization_name="B", source_record_id="e2"),
        _row(
            domain_raw="alpha.cl",
            organization_name="A",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="oa",
        ),
        _row(
            domain_raw="beta.cl",
            organization_name="B",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="ob",
        ),
    ]
    res = resolve_identity(rows)
    assert res.contacts[0].account_id is None
    assert any(
        x.reason_code in {REASON_EMAIL_COMPETING_DOMAINS, REASON_COMPETING_ACCOUNT_LINKS, REASON_EMAIL_CONFLICTING_ORGS}
        for x in res.conflicts
    )


# --- Hardening: same org name across multiple institutional domains ---


def test_same_org_name_multiple_domains_no_arbitrary_link() -> None:
    rows = [
        _row(
            domain_raw="north.cl",
            organization_name="Hospital Sur",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="north",
        ),
        _row(
            domain_raw="south.cl",
            organization_name="Hospital Sur",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="south",
        ),
        _row(email_raw="buyer@gmail.com", organization_name="Hospital Sur", source_record_id="g"),
    ]
    res = resolve_identity(rows)
    domain_accounts = [a for a in res.accounts if a.primary_domain in {"north.cl", "south.cl"}]
    assert len(domain_accounts) == 2
    # Consumer contact must not be linked to an arbitrary domain account.
    gmail = next(c for c in res.contacts if c.normalized_email == "buyer@gmail.com")
    assert gmail.account_id is None


def test_name_link_ambiguous_when_multiple_domain_accounts_share_name() -> None:
    rows = [
        _row(
            domain_raw="north.cl",
            organization_name="Hospital Sur",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="north",
        ),
        _row(
            domain_raw="south.cl",
            organization_name="Hospital Sur",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="south",
        ),
        # Contact on a third domain with only org-name evidence (no institutional email domain match).
        _row(email_raw="other@elsewhere.cl", organization_name="Hospital Sur", source_record_id="e1"),
    ]
    a = resolve_identity(rows)
    b = resolve_identity(list(reversed(rows)))
    ca = next(c for c in a.contacts if c.normalized_email == "other@elsewhere.cl")
    cb = next(c for c in b.contacts if c.normalized_email == "other@elsewhere.cl")
    assert ca.account_id is None
    assert cb.account_id is None
    assert any(x.reason_code == REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES for x in a.conflicts)
    assert any(x.reason_code == REASON_AMBIGUOUS_NAME_ACCOUNT_CANDIDATES for x in b.conflicts)


# --- Hardening: consumer email org evidence ---


def test_consumer_email_org_creates_account_but_withholds_link() -> None:
    rows = [
        _row(
            email_raw="person@gmail.com",
            organization_name="Hospital Sur SPA",
            evidence_at="2024-01-01",
            source_record_id="g1",
        )
    ]
    res = resolve_identity(rows)
    org_nn_accounts = [a for a in res.accounts if a.primary_domain is None]
    assert len(org_nn_accounts) == 1
    assert org_nn_accounts[0].account_id == stable_account_id_for_name(org_nn_accounts[0].normalized_name)
    contact = res.contacts[0]
    assert contact.account_id is None
    assert contact.identity_status == "unlinked"
    assert any(x.reason_code == REASON_CONSUMER_DOMAIN_REFUSED for x in res.conflicts)
    assert any(x.reason_code == REASON_CONSUMER_ORG_LINK_WITHHELD for x in res.conflicts)
    assert not hasattr(contact, "is_customer")
    assert res.metrics["opportunity_stage_fields_inferred"] is False
    assert res.metrics["consumer_domain_auto_link_refusals"] == 1


# --- Hardening: metrics exact values ---


def test_consumer_refusal_metric_is_distinct_contacts_not_double_counted() -> None:
    rows = [
        _row(email_raw="a@gmail.com", organization_name="Org A", source_record_id="1", evidence_at="2024-01-01"),
        _row(email_raw="a@gmail.com", organization_name="Org A", source_record_id="2", evidence_at="2024-02-01"),
        _row(email_raw="b@outlook.com", organization_name="Org B", source_record_id="3"),
    ]
    res = resolve_identity(rows)
    assert res.metrics["canonical_contact_count"] == 2
    assert res.metrics["consumer_domain_auto_link_refusals"] == 2


def test_origin_metrics_split_assertion_rows_vs_canonical_contacts() -> None:
    rows = [
        _row(email_raw="o@h.cl", organization_name="H", origin_plane=ORIGIN_ORIGENLAB_GMAIL, source_record_id="o1"),
        _row(email_raw="o@h.cl", organization_name="H", origin_plane=ORIGIN_ORIGENLAB_GMAIL, source_record_id="o2"),
        _row(email_raw="l@old.cl", organization_name="L", origin_plane=ORIGIN_LABDELIVERY_ARCHIVE, domain_raw="old.cl", source_record_id="l1"),
    ]
    res = resolve_identity(rows)
    assert res.metrics["origenlab_origin_source_assertion_rows"] == 2
    assert res.metrics["canonical_contacts_with_origenlab_origin"] == 1
    assert res.metrics["labdelivery_origin_source_assertion_rows"] == 1
    assert res.metrics["canonical_contacts_with_labdelivery_origin"] == 1


# --- Hardening: alias provenance ---


def test_alias_evidence_count_and_timestamps_are_alias_specific() -> None:
    rows = [
        _row(
            domain_raw="hospital.cl",
            organization_name="Hospital Sur",
            evidence_at="2023-01-01",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="a1",
        ),
        _row(
            domain_raw="hospital.cl",
            organization_name="Hospital Sur",
            evidence_at="2023-06-01",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="a2",
        ),
        _row(
            domain_raw="hospital.cl",
            organization_name="Hosp. Sur",
            evidence_at="2024-12-01",
            source_table="organization_master",
            source_plane="organization_master",
            source_record_id="b1",
        ),
    ]
    res = resolve_identity(rows)
    # Conflicting aliases on same domain → needs_review account with both aliases.
    acc = res.accounts[0]
    assert len(acc.aliases) == 2
    counts = sorted(int(a["evidence_count"]) for a in acc.aliases.values())
    assert counts == [1, 2]
    # Alias with two observations must not inherit the other alias's only timestamp as first/last blindly.
    by_count = {int(a["evidence_count"]): a for a in acc.aliases.values()}
    two = by_count[2]
    one = by_count[1]
    assert two["first_evidence_at"] == "2023-01-01"
    assert two["last_evidence_at"] == "2023-06-01"
    assert one["first_evidence_at"] == "2024-12-01"
    assert one["last_evidence_at"] == "2024-12-01"


# --- Hardening: transaction / FK ---


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
    assert summary["transaction_contract"] == TRANSACTION_CONTRACT
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


def test_rebuild_failure_preserves_previous_dataset(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    _seed_fixture(db)
    run_identity_build(sqlite_path=db, apply=True)
    conn = sqlite3.connect(str(db))
    before_accounts = list(conn.execute("SELECT * FROM commercial_identity_account ORDER BY account_id"))
    before_contacts = list(conn.execute("SELECT * FROM commercial_identity_contact ORDER BY contact_id"))
    before_evidence = list(conn.execute("SELECT * FROM commercial_identity_evidence ORDER BY evidence_id"))
    conn.close()

    plan = plan_identity_build(sqlite_path=db, apply=True)

    def boom(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("injected failure after write")

    with pytest.raises(RuntimeError, match="injected failure"):
        apply_identity_build(plan, inject_failure=boom)

    conn = sqlite3.connect(str(db))
    after_accounts = list(conn.execute("SELECT * FROM commercial_identity_account ORDER BY account_id"))
    after_contacts = list(conn.execute("SELECT * FROM commercial_identity_contact ORDER BY contact_id"))
    after_evidence = list(conn.execute("SELECT * FROM commercial_identity_evidence ORDER BY evidence_id"))
    conn.close()
    assert after_accounts == before_accounts
    assert after_contacts == before_contacts
    assert after_evidence == before_evidence


def test_foreign_keys_enforced_on_apply(tmp_path: Path) -> None:
    db = tmp_path / "fk.sqlite"
    _seed_fixture(db)
    run_identity_build(sqlite_path=db, apply=True)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO commercial_identity_contact (
              contact_id, normalized_email, display_name, role, account_id,
              account_link_method, first_evidence_at, last_evidence_at,
              identity_confidence, identity_status, email_domain
            ) VALUES ('c_bad', 'bad@x.cl', NULL, NULL, 'a_missing',
                      'institutional_domain', NULL, NULL, 'low', 'unlinked', 'x.cl')
            """
        )
    conn.close()


def test_first_run_failure_may_leave_empty_schema_contract_b(tmp_path: Path) -> None:
    db = tmp_path / "first.sqlite"
    _seed_fixture(db)
    plan = plan_identity_build(sqlite_path=db, apply=True)

    def boom(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("first-run failure")

    with pytest.raises(RuntimeError, match="first-run failure"):
        apply_identity_build(plan, inject_failure=boom)

    conn = sqlite3.connect(str(db))
    # Contract B: additive schema may remain.
    n_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'commercial_identity_%'"
    ).fetchone()[0]
    assert n_tables >= 1
    # But data replacement rolled back → empty data tables.
    n_accounts = conn.execute("SELECT COUNT(*) FROM commercial_identity_account").fetchone()[0]
    n_contacts = conn.execute("SELECT COUNT(*) FROM commercial_identity_contact").fetchone()[0]
    conn.close()
    assert n_accounts == 0
    assert n_contacts == 0


def test_require_explicit_sqlite_path(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        require_explicit_sqlite_path(None)
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(Exception):
        require_explicit_sqlite_path(missing)


def test_no_opportunity_stage_or_next_action_fields() -> None:
    rows = [_row(email_raw="a@hospital.cl", organization_name="Hospital Sur", domain_raw="hospital.cl", source_record_id="1")]
    res = resolve_identity(rows)
    assert res.metrics["opportunity_stage_fields_inferred"] is False
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


def test_distinct_source_record_ids_preserve_distinct_evidence() -> None:
    rows = [
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2024-01-01", source_record_id="email:a|first_seen"),
        _row(email_raw="a@hospital.cl", organization_name="Hospital Sur", evidence_at="2024-06-01", source_record_id="email:a|last_seen"),
    ]
    res = resolve_identity(rows)
    contact_evidence = [e for e in res.evidence if e.subject_kind == "contact"]
    assert len(contact_evidence) == 2
    assert len({e.evidence_id for e in contact_evidence}) == 2


def test_malformed_domain_not_institutional() -> None:
    assert not is_institutional_domain("notadomain")
    assert not is_institutional_domain("")
    assert not is_institutional_domain(None)


# --- Regression ---


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
