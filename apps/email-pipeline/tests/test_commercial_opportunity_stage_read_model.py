"""Synthetic tests for commercial opportunity stage read model (PR3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_identity.builder import (
    CommercialIdentityPathError,
    run_identity_build,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
    RUN_CONTEXT_PRODUCTION_APPLY,
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
    RUN_CONTEXT_SYNTHETIC_FIXTURE,
)
from origenlab_email_pipeline.commercial_identity.fingerprint import (
    FINGERPRINT_ALGORITHM_VERSION,
    identity_resolution_fingerprint,
)
from origenlab_email_pipeline.commercial_identity.ids import (
    stable_account_id_for_domain,
    stable_contact_id,
)
from origenlab_email_pipeline.commercial_identity.models import (
    AccountRecord,
    ContactRecord,
    IdentityResolution,
    SourceIdentityRow,
)
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_opportunity.builder import (
    apply_opportunity_build,
    plan_opportunity_build,
    run_opportunity_build,
)
from origenlab_email_pipeline.commercial_opportunity.identity_gate import (
    IdentitySnapshotError,
    StaleBuildPlanError,
)
from origenlab_email_pipeline.commercial_opportunity.ids import opportunity_id_for_deal
from origenlab_email_pipeline.commercial_opportunity.resolve import resolve_opportunities
from origenlab_email_pipeline.commercial_opportunity.models import (
    SourceContactMasterRow,
    SourceDealDocumentRow,
    SourceDealEventRow,
    SourceDealPaymentRow,
    SourceDealRow,
    SourceSignalRow,
)
from origenlab_email_pipeline.commercial_opportunity.sources import (
    SourceSchemaError,
    load_contact_master_history,
    load_opportunity_signals,
)
from origenlab_email_pipeline.lead_research.commercial_action_buckets import (
    BUCKET_ALREADY_CONTACTED,
    derive_commercial_action_bucket,
)


def _identity_row(**kwargs: object) -> SourceIdentityRow:
    return SourceIdentityRow(
        source_table=str(kwargs.get("source_table") or "contact_master"),
        source_record_id=str(kwargs.get("source_record_id") or kwargs.get("email_raw") or "x"),
        source_plane=str(kwargs.get("source_plane") or "contact_master"),
        origin_plane=str(kwargs.get("origin_plane") or "business_mart"),
        email_raw=kwargs.get("email_raw"),  # type: ignore[arg-type]
        display_name=kwargs.get("display_name"),  # type: ignore[arg-type]
        organization_name=kwargs.get("organization_name"),  # type: ignore[arg-type]
        domain_raw=kwargs.get("domain_raw"),  # type: ignore[arg-type]
        evidence_at=kwargs.get("evidence_at"),  # type: ignore[arg-type]
    )


def _deal(**kwargs: object) -> SourceDealRow:
    return SourceDealRow(
        deal_id=int(kwargs.get("deal_id") or 1),
        deal_key=str(kwargs.get("deal_key") or "deal-a"),
        deal_status=str(kwargs.get("deal_status") or "quoted"),
        client_org_name=str(kwargs.get("client_org_name") or "Hospital Sur"),
        client_domain=kwargs.get("client_domain"),  # type: ignore[arg-type]
        client_contact_email=kwargs.get("client_contact_email"),  # type: ignore[arg-type]
        supplier_org_name=kwargs.get("supplier_org_name"),  # type: ignore[arg-type]
        supplier_domain=kwargs.get("supplier_domain"),  # type: ignore[arg-type]
        confidence=str(kwargs.get("confidence") or "extracted_high"),
        created_at=kwargs.get("created_at"),  # type: ignore[arg-type]
        updated_at=kwargs.get("updated_at"),  # type: ignore[arg-type]
    )


def _event(**kwargs: object) -> SourceDealEventRow:
    return SourceDealEventRow(
        event_id=int(kwargs.get("event_id") or 1),
        deal_id=int(kwargs.get("deal_id") or 1),
        deal_key=str(kwargs.get("deal_key") or "deal-a"),
        event_type=str(kwargs.get("event_type") or "client_quote_sent"),
        event_at=kwargs.get("event_at"),  # type: ignore[arg-type]
        confidence=str(kwargs.get("confidence") or "extracted_high"),
        operator_confirmed=bool(kwargs.get("operator_confirmed") or False),
        source_email_id=kwargs.get("source_email_id"),  # type: ignore[arg-type]
        source_attachment_id=kwargs.get("source_attachment_id"),  # type: ignore[arg-type]
    )


def _base_identity() -> object:
    rows = [
        _identity_row(
            email_raw="buyer@hospital.cl",
            organization_name="Hospital Sur",
            domain_raw="hospital.cl",
            source_record_id="cm:1",
        ),
    ]
    return resolve_identity(rows)


# --- 1–2: explicit deal + stable ID ---


def test_explicit_deal_creates_one_deterministic_opportunity() -> None:
    identity = _base_identity()
    deals = [_deal(deal_key="ceaf-oc-26172", updated_at="2026-01-10T12:00:00+00:00")]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    assert res.metrics["explicit_deal_opportunity_count"] == 1
    assert len(res.opportunities) == 1
    assert res.opportunities[0].opportunity_id == opportunity_id_for_deal("ceaf-oc-26172")
    assert res.opportunities[0].record_kind == "explicit_opportunity"


def test_opportunity_id_stable_across_input_ordering() -> None:
    identity = _base_identity()
    deals_a = [
        _deal(deal_id=2, deal_key="b-deal", updated_at="2026-01-02T00:00:00+00:00"),
        _deal(deal_id=1, deal_key="a-deal", updated_at="2026-01-01T00:00:00+00:00"),
    ]
    deals_b = list(reversed(deals_a))
    ra = resolve_opportunities(identity=identity, deals=deals_a, events=[], documents=[], payments=[])
    rb = resolve_opportunities(identity=identity, deals=deals_b, events=[], documents=[], payments=[])
    assert [o.opportunity_id for o in ra.opportunities] == [o.opportunity_id for o in rb.opportunities]
    assert ra.opportunities[0].opportunity_id == opportunity_id_for_deal("a-deal")


# --- 3–7: identity linkage ---


def test_exact_contact_email_links_through_pr2_identity() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            client_contact_email="buyer@hospital.cl",
            client_domain="hospital.cl",
            updated_at="2026-01-10T12:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.primary_contact_id == stable_contact_id("buyer@hospital.cl")
    assert opp.account_id == stable_account_id_for_domain("hospital.cl")
    assert opp.identity_link_status == "linked"


def test_institutional_domain_fallback_when_unambiguous() -> None:
    identity = resolve_identity(
        [
            _identity_row(
                email_raw="other@hospital.cl",
                organization_name="Hospital Sur",
                domain_raw="hospital.cl",
            )
        ]
    )
    deals = [
        _deal(
            client_contact_email=None,
            client_domain="hospital.cl",
            updated_at="2026-01-10T12:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    assert res.opportunities[0].account_id == stable_account_id_for_domain("hospital.cl")
    assert res.opportunities[0].primary_contact_id is None
    assert res.opportunities[0].identity_link_status == "linked"


def test_consumer_email_does_not_create_institutional_account_link() -> None:
    identity = resolve_identity(
        [
            _identity_row(
                email_raw="person@gmail.com",
                organization_name="Some Lab",
                domain_raw="gmail.com",
            )
        ]
    )
    deals = [
        _deal(
            client_contact_email="person@gmail.com",
            client_domain="gmail.com",
            updated_at="2026-01-10T12:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    assert res.opportunities[0].account_id is None
    assert res.opportunities[0].identity_link_status in {"withheld", "unresolved"}


def test_internal_actor_does_not_become_client_account() -> None:
    identity = resolve_identity(
        [
            _identity_row(
                email_raw="ops@origenlab.cl",
                organization_name="OrigenLab",
                domain_raw="origenlab.cl",
            )
        ]
    )
    deals = [
        _deal(
            client_contact_email="ops@origenlab.cl",
            client_domain="origenlab.cl",
            updated_at="2026-01-10T12:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    assert res.opportunities[0].account_id is None
    assert res.opportunities[0].identity_link_status == "withheld"


def test_ambiguous_identity_retains_opportunity_withholds_link() -> None:
    from origenlab_email_pipeline.commercial_identity.models import (
        IdentityResolution,
    )

    # Two accounts share the same institutional domain → ambiguous domain fallback.
    a1 = AccountRecord(
        account_id="a_one",
        canonical_name="Org One",
        normalized_name="org one",
        primary_domain="shared.edu",
        first_evidence_at=None,
        last_evidence_at=None,
        identity_confidence="extracted_high",
        identity_status="resolved",
        domains={"shared.edu": {"link_method": "institutional_domain"}},
    )
    a2 = AccountRecord(
        account_id="a_two",
        canonical_name="Org Two",
        normalized_name="org two",
        primary_domain="shared.edu",
        first_evidence_at=None,
        last_evidence_at=None,
        identity_confidence="extracted_high",
        identity_status="resolved",
        domains={"shared.edu": {"link_method": "institutional_domain"}},
    )
    identity = IdentityResolution(
        accounts=[a1, a2],
        contacts=[],
        evidence=[],
        conflicts=[],
        metrics={},
    )
    deals = [
        _deal(
            deal_key="amb-deal",
            client_contact_email=None,
            client_domain="shared.edu",
            updated_at="2026-01-10T12:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    assert len(res.opportunities) == 1
    assert res.opportunities[0].account_id is None
    assert res.opportunities[0].identity_link_status == "ambiguous"
    assert any(c.reason_code == "opportunity_identity_ambiguous" for c in res.conflicts)


# --- 8–15: stage mapping & precedence ---


def test_deal_status_with_timestamp_maps_to_expected_stage() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            deal_status="quoted",
            client_contact_email="buyer@hospital.cl",
            updated_at="2026-03-01T10:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "quote_sent"
    assert opp.stage_is_current is True
    assert opp.stage_evidence_at == "2026-03-01T10:00:00+00:00"


def test_deal_status_without_timestamp_cannot_be_current() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at=None, created_at=None)]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.stage_is_current is False
    assert opp.canonical_stage == "unknown"
    assert opp.stage_evidence_at is None


def test_cancelled_terminal_wins_over_older_active_stages() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_quote_sent", event_at="2026-01-01T00:00:00+00:00", event_id=1),
        _event(event_type="deal_cancelled", event_at="2026-02-01T00:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "lost"
    assert opp.stage_is_terminal is True
    assert opp.stage_is_current is False


def test_delivered_cannot_regress_to_quote_stage() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="delivered", updated_at="2026-04-01T00:00:00+00:00")]
    events = [
        _event(event_type="delivered", event_at="2026-04-01T00:00:00+00:00", event_id=1),
        _event(event_type="client_quote_sent", event_at="2026-01-01T00:00:00+00:00", event_id=2),
    ]
    # Process quote after delivered in input order — result must not regress
    events_rev = list(reversed(events))
    res = resolve_opportunities(
        identity=identity, deals=deals, events=events_rev, documents=[], payments=[]
    )
    assert res.opportunities[0].canonical_stage == "post_sale"
    assert res.opportunities[0].stage_is_terminal is True


def test_same_time_incompatible_terminals_produce_conflict() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    ts = "2026-05-01T12:00:00+00:00"
    events = [
        _event(event_type="deal_cancelled", event_at=ts, event_id=1),
        _event(event_type="delivered", event_at=ts, event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    reasons = {c.reason_code for c in res.conflicts}
    assert "conflicting_terminal_events" in reasons or "same_timestamp_stage_conflict" in reasons


def test_operator_confirmed_outranks_extracted() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(
            event_type="client_quote_sent",
            event_at="2026-03-01T00:00:00+00:00",
            confidence="extracted_high",
            event_id=1,
        ),
        _event(
            event_type="client_po_received",
            event_at="2026-02-01T00:00:00+00:00",
            confidence="extracted_low",
            operator_confirmed=True,
            event_id=2,
        ),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    # Operator-confirmed purchase_pending should outrank later extracted quote
    assert res.opportunities[0].canonical_stage == "purchase_pending"


def test_latest_compatible_event_refines_stage() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="draft", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_quote_sent", event_at="2026-01-05T00:00:00+00:00", event_id=1),
        _event(event_type="client_po_received", event_at="2026-02-05T00:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert res.opportunities[0].canonical_stage == "purchase_pending"


def test_supplier_side_refines_existing_deal_not_standalone_client_opp() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            deal_status="client_paid",
            client_contact_email="buyer@hospital.cl",
            updated_at="2026-02-01T00:00:00+00:00",
        )
    ]
    events = [
        _event(event_type="client_payment_received", event_at="2026-02-01T00:00:00+00:00", event_id=1),
        _event(event_type="supplier_po_sent", event_at="2026-02-10T00:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert res.metrics["explicit_deal_opportunity_count"] == 1
    assert res.opportunities[0].canonical_stage == "fulfillment"
    # No extra opportunity manufactured from supplier-only
    assert all(o.deal_key == "deal-a" for o in res.opportunities if o.record_kind == "explicit_opportunity")


# --- 16–20: history / timestamps ---


def test_typed_dated_non_deal_evidence_is_candidate_not_current() -> None:
    identity = _base_identity()
    signals = [
        SourceSignalRow(
            signal_id="sig-1",
            signal_type="quote_signal",
            entity_kind="contact",
            entity_key="buyer@hospital.cl",
            created_at="2026-06-01T00:00:00+00:00",  # mart stamp
            email_id=99,
            email_date="2026-01-15T08:00:00+00:00",
        )
    ]
    res = resolve_opportunities(
        identity=identity,
        deals=[],
        events=[],
        documents=[],
        payments=[],
        signals=signals,
    )
    assert res.metrics["evidence_candidate_count"] == 1
    cand = [o for o in res.opportunities if o.record_kind == "evidence_candidate"][0]
    assert cand.stage_is_current is False
    assert cand.stage_evidence_at == "2026-01-15T08:00:00+00:00"


def test_lifetime_counts_create_history_only() -> None:
    identity = _base_identity()
    cm = [
        SourceContactMasterRow(
            contact_id="1",
            email="buyer@hospital.cl",
            organization_name="Hospital Sur",
            quote_email_count=5,
            invoice_email_count=2,
            purchase_email_count=1,
        )
    ]
    res = resolve_opportunities(
        identity=identity,
        deals=[],
        events=[],
        documents=[],
        payments=[],
        contact_master=cm,
    )
    assert res.metrics["commercial_history_count"] == 1
    hist = res.opportunities[0]
    assert hist.canonical_stage == "commercial_history"
    assert hist.stage_is_current is False
    assert hist.record_kind == "commercial_history"


def test_opportunity_signals_created_at_not_event_time() -> None:
    identity = _base_identity()
    signals = [
        SourceSignalRow(
            signal_id="sig-2",
            signal_type="quote_signal",
            entity_kind="contact",
            entity_key="buyer@hospital.cl",
            created_at="2026-06-01T00:00:00+00:00",
            email_id=None,
            email_date=None,
        )
    ]
    res = resolve_opportunities(
        identity=identity, deals=[], events=[], documents=[], payments=[], signals=signals
    )
    assert res.metrics["undated_signal_history_count"] == 1
    assert res.metrics["evidence_candidate_count"] == 0
    assert any(c.reason_code == "undated_signal_history_only" for c in res.conflicts)


def test_missing_event_timestamps_remain_missing() -> None:
    identity = _base_identity()
    deals = [_deal(updated_at="2026-01-01T00:00:00+00:00")]
    events = [_event(event_at=None, event_id=1)]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert res.metrics["missing_event_timestamp_count"] == 1
    assert any(e.event_at is None for e in res.events)
    assert any(c.reason_code == "source_event_missing_timestamp" for c in res.conflicts)


def test_build_time_never_stage_evidence() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at=None, created_at=None)]
    res = resolve_opportunities(
        identity=identity,
        deals=deals,
        events=[],
        documents=[],
        payments=[],
        build_time_iso="2099-12-31T23:59:59+00:00",
    )
    opp = res.opportunities[0]
    assert opp.stage_evidence_at is None
    assert opp.stage_evidence_at != "2099-12-31T23:59:59+00:00"
    assert res.metrics.get("build_time_iso_metadata_only") == "2099-12-31T23:59:59+00:00"


def test_no_next_action_tender_product_interest_inferred() -> None:
    identity = _base_identity()
    res = resolve_opportunities(
        identity=identity,
        deals=[_deal(updated_at="2026-01-01T00:00:00+00:00")],
        events=[],
        documents=[],
        payments=[],
    )
    assert res.metrics["opportunity_stage_fields_inferred"] is True
    assert res.metrics["next_action_fields_inferred"] is False
    assert res.metrics["tender_fields_inferred"] is False
    assert res.metrics["product_interest_fields_inferred"] is False


# --- SQLite dry-run / apply ---


def _seed_minimal_db(path: Path) -> None:
    """Fixture matching production contact_master / opportunity_signals DDL exactly."""
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
          total_emails INTEGER,
          inbound_emails INTEGER,
          outbound_emails INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          confidence_score REAL
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT,
          last_seen_at TEXT,
          total_emails INTEGER,
          total_contacts INTEGER,
          quote_email_count INTEGER,
          invoice_email_count INTEGER,
          purchase_email_count INTEGER,
          business_doc_email_count INTEGER,
          quote_doc_count INTEGER,
          invoice_doc_count INTEGER,
          top_equipment_tags TEXT,
          key_contacts TEXT
        );
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          date_iso TEXT,
          sender TEXT,
          source_file TEXT
        );
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL,
          entity_kind TEXT NOT NULL,
          entity_key TEXT NOT NULL,
          email_id INTEGER,
          attachment_id INTEGER,
          score REAL,
          details_json TEXT,
          created_at TEXT
        );
        CREATE TABLE commercial_deal (
          id INTEGER PRIMARY KEY,
          deal_key TEXT NOT NULL UNIQUE,
          deal_status TEXT NOT NULL,
          client_org_name TEXT NOT NULL,
          client_domain TEXT,
          client_contact_email TEXT,
          supplier_org_name TEXT,
          supplier_domain TEXT,
          confidence TEXT NOT NULL DEFAULT 'extracted_high',
          created_at TEXT,
          updated_at TEXT
        );
        CREATE TABLE commercial_deal_event (
          id INTEGER PRIMARY KEY,
          deal_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          event_at TEXT,
          confidence TEXT NOT NULL,
          summary TEXT NOT NULL DEFAULT '',
          source_email_id INTEGER,
          source_attachment_id INTEGER,
          created_at TEXT
        );
        CREATE TABLE commercial_deal_document (
          id INTEGER PRIMARY KEY,
          deal_id INTEGER NOT NULL,
          document_type TEXT NOT NULL,
          issued_at TEXT,
          confidence TEXT NOT NULL,
          source_email_id INTEGER,
          source_attachment_id INTEGER,
          created_at TEXT
        );
        CREATE TABLE commercial_deal_payment (
          id INTEGER PRIMARY KEY,
          deal_id INTEGER NOT NULL,
          direction TEXT NOT NULL,
          paid_at TEXT,
          confidence TEXT NOT NULL,
          created_at TEXT
        );
        INSERT INTO organization_master (
          domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, total_contacts,
          quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count,
          top_equipment_tags, key_contacts
        ) VALUES (
          'hospital.cl', 'Hospital Sur', 'institution',
          '2023-01-01', '2024-01-01', 10, 1,
          0, 0, 0, 0, 0, 0, NULL, NULL
        );
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails, inbound_emails, outbound_emails,
          quote_email_count, invoice_email_count, purchase_email_count,
          business_doc_email_count, quote_doc_count, invoice_doc_count,
          top_equipment_tags, confidence_score
        ) VALUES (
          'buyer@hospital.cl', 'Buyer', 'hospital.cl', 'Hospital Sur', 'institution',
          '2023-02-01', '2024-02-01', 3, 1, 2,
          0, 0, 0, 0, 0, 0, NULL, 0.5
        );
        INSERT INTO commercial_deal (
          id, deal_key, deal_status, client_org_name, client_domain,
          client_contact_email, confidence, created_at, updated_at
        ) VALUES (
          1, 'fixture-deal', 'quoted', 'Hospital Sur', 'hospital.cl',
          'buyer@hospital.cl', 'extracted_high',
          '2026-01-01T00:00:00+00:00', '2026-01-10T00:00:00+00:00'
        );
        """
    )
    conn.commit()
    conn.close()


def test_dry_run_creates_no_tables_and_no_writes(tmp_path: Path) -> None:
    db = tmp_path / "dry.sqlite"
    _seed_minimal_db(db)
    before = db.read_bytes()
    summary = run_opportunity_build(
        sqlite_path=db, apply=False, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    after = db.read_bytes()
    assert before == after
    assert summary["applied"] is False
    assert summary["mode"] == "dry-run"
    conn = sqlite3.connect(str(db))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'commercial_opportunity%'"
        )
    }
    conn.close()
    assert tables == set()
    assert summary["metrics"]["label"] == RUN_CONTEXT_SYNTHETIC_FIXTURE


def test_apply_requires_matching_identity_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "apply_ok.sqlite"
    _seed_minimal_db(db)
    run_identity_build(
        sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    summary = run_opportunity_build(
        sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    assert summary["applied"] is True
    assert summary["identity_fingerprint_match_status"] == "matched"
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0]
    conn.close()
    assert n >= 1


def test_missing_identity_snapshot_blocks_apply(tmp_path: Path) -> None:
    db = tmp_path / "no_id.sqlite"
    _seed_minimal_db(db)
    with pytest.raises(IdentitySnapshotError):
        run_opportunity_build(
            sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
        )


def test_stale_identity_fingerprint_blocks_apply(tmp_path: Path) -> None:
    db = tmp_path / "stale.sqlite"
    _seed_minimal_db(db)
    run_identity_build(
        sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    # Corrupt fingerprint in meta
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO commercial_identity_build_meta(meta_key, meta_value)
        VALUES ('identity_fingerprint', 'deadbeef')
        ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
        """
    )
    conn.commit()
    conn.close()
    with pytest.raises(IdentitySnapshotError):
        run_opportunity_build(
            sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
        )


def test_atomic_rebuild_failure_preserves_previous(tmp_path: Path) -> None:
    db = tmp_path / "atomic.sqlite"
    _seed_minimal_db(db)
    run_identity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    plan1 = plan_opportunity_build(
        sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    apply_opportunity_build(plan1)
    conn = sqlite3.connect(str(db))
    before_ids = {
        r[0] for r in conn.execute("SELECT opportunity_id FROM commercial_opportunity").fetchall()
    }
    conn.close()
    assert before_ids

    plan2 = plan_opportunity_build(
        sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )

    def _boom(_conn: sqlite3.Connection) -> None:
        raise RuntimeError("inject")

    with pytest.raises(RuntimeError, match="inject"):
        apply_opportunity_build(plan2, inject_failure=_boom)

    conn = sqlite3.connect(str(db))
    after_ids = {
        r[0] for r in conn.execute("SELECT opportunity_id FROM commercial_opportunity").fetchall()
    }
    conn.close()
    assert after_ids == before_ids


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    db = tmp_path / "fk.sqlite"
    _seed_minimal_db(db)
    run_identity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    run_opportunity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO commercial_opportunity_event (
              event_id, opportunity_id, canonical_event_type, source_event_type,
              event_at, source_table, source_record_id, confidence, operator_confirmed
            ) VALUES ('bad', 'missing-opp', 'quote_sent', 'x', NULL, 't', '1', 'needs_review', 0)
            """
        )
    conn.close()


def test_repeated_rebuild_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "idem.sqlite"
    _seed_minimal_db(db)
    run_identity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    s1 = run_opportunity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    s2 = run_opportunity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    assert s1["planned_writes"] == s2["planned_writes"]
    assert s1["identity_fingerprint"] == s2["identity_fingerprint"]
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0]
    conn.close()
    assert n == s2["planned_writes"]["commercial_opportunity"]


def test_existing_commercial_deal_tables_unchanged(tmp_path: Path) -> None:
    db = tmp_path / "deal_safe.sqlite"
    _seed_minimal_db(db)
    conn = sqlite3.connect(str(db))
    before = conn.execute(
        "SELECT deal_key, deal_status, updated_at FROM commercial_deal"
    ).fetchall()
    conn.close()
    run_identity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    run_opportunity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    conn = sqlite3.connect(str(db))
    after = conn.execute(
        "SELECT deal_key, deal_status, updated_at FROM commercial_deal"
    ).fetchall()
    conn.close()
    assert before == after


def test_action_bucket_behavior_unchanged() -> None:
    assert (
        derive_commercial_action_bucket(
            {
                "gmail_sent_count": 2,
                "gmail_received_count": 0,
                "classification": "prospect",
            }
        )
        == BUCKET_ALREADY_CONTACTED
    )


def test_run_context_labels_exact() -> None:
    identity = _base_identity()
    fp1 = identity_resolution_fingerprint(identity)
    fp2 = identity_resolution_fingerprint(identity)
    assert fp1 == fp2


def test_identity_run_context_label_from_cli_contract(tmp_path: Path) -> None:
    db = tmp_path / "ctx.sqlite"
    _seed_minimal_db(db)
    summary = run_identity_build(
        sqlite_path=db, apply=False, run_context=RUN_CONTEXT_PRODUCTION_DRY_RUN
    )
    assert summary["metrics"]["label"] == RUN_CONTEXT_PRODUCTION_DRY_RUN
    assert summary["run_context"] == RUN_CONTEXT_PRODUCTION_DRY_RUN
    assert "identity_fingerprint" in summary
    default = run_identity_build(sqlite_path=db, apply=False)
    assert default["metrics"]["label"] == RUN_CONTEXT_LOCAL_FIXTURE


def test_fingerprint_order_independent() -> None:
    rows = [
        _identity_row(email_raw="a@hospital.cl", organization_name="H", domain_raw="hospital.cl", source_record_id="1"),
        _identity_row(email_raw="b@hospital.cl", organization_name="H", domain_raw="hospital.cl", source_record_id="2"),
    ]
    r1 = resolve_identity(rows)
    r2 = resolve_identity(list(reversed(rows)))
    assert identity_resolution_fingerprint(r1) == identity_resolution_fingerprint(r2)


def test_document_payment_dated_evidence(tmp_path: Path) -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="draft", updated_at="2026-01-01T00:00:00+00:00")]
    docs = [
        SourceDealDocumentRow(
            document_id=1,
            deal_id=1,
            deal_key="deal-a",
            document_type="client_quote",
            issued_at="2026-01-20T00:00:00+00:00",
            confidence="extracted_high",
            source_email_id=None,
            source_attachment_id=None,
        )
    ]
    pays = [
        SourceDealPaymentRow(
            payment_id=1,
            deal_id=1,
            deal_key="deal-a",
            direction="inbound",
            paid_at="2026-03-01T00:00:00+00:00",
            confidence="operator_confirmed",
        )
    ]
    res = resolve_opportunities(
        identity=identity, deals=deals, events=[], documents=docs, payments=pays
    )
    assert res.opportunities[0].canonical_stage == "won"


# --- PR3 hardening ---


def test_chrono_older_operator_quote_later_shipment_fulfillment() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(
            event_type="client_quote_sent",
            event_at="2026-01-05T00:00:00+00:00",
            operator_confirmed=True,
            event_id=1,
        ),
        _event(
            event_type="client_po_received",
            event_at="2026-02-01T00:00:00+00:00",
            event_id=2,
        ),
        _event(event_type="shipment_released", event_at="2026-03-01T00:00:00+00:00", event_id=3),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert res.opportunities[0].canonical_stage == "fulfillment"


def test_chrono_later_quote_does_not_regress_fulfillment() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(
            event_type="client_po_received",
            event_at="2026-01-15T00:00:00+00:00",
            event_id=1,
        ),
        _event(event_type="shipment_released", event_at="2026-02-01T00:00:00+00:00", event_id=2),
        _event(event_type="client_quote_sent", event_at="2026-03-01T00:00:00+00:00", event_id=3),
    ]
    res_a = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    res_b = resolve_opportunities(
        identity=identity, deals=deals, events=list(reversed(events)), documents=[], payments=[]
    )
    assert res_a.opportunities[0].canonical_stage == "fulfillment"
    assert res_b.opportunities[0].canonical_stage == "fulfillment"
    assert any(c.reason_code == "stage_regression_prevented" for c in res_a.conflicts)


def test_delivered_then_cancelled_conflict() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="delivered", event_at="2026-02-01T00:00:00+00:00", event_id=1),
        _event(event_type="deal_cancelled", event_at="2026-03-01T00:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert any(c.reason_code == "conflicting_terminal_events" for c in res.conflicts)
    assert res.opportunities[0].review_status == "needs_review"
    # Deterministic display: latest hard terminal timestamp
    assert res.opportunities[0].canonical_stage == "lost"


def test_undated_terminal_statuses_unproven() -> None:
    identity = _base_identity()
    for status, expected_reason in (
        ("closed", "closed_without_supporting_evidence"),
        ("client_paid", "undated_terminal_unproven"),
        ("delivered", "undated_terminal_unproven"),
        ("cancelled", "undated_terminal_unproven"),
    ):
        res = resolve_opportunities(
            identity=identity,
            deals=[_deal(deal_status=status, updated_at=None, created_at=None)],
            events=[],
            documents=[],
            payments=[],
        )
        opp = res.opportunities[0]
        assert opp.canonical_stage == "unknown", status
        assert opp.stage_is_terminal is False
        assert opp.stage_is_current is False
        assert opp.stage_confidence == "unavailable"
        assert any(c.reason_code == expected_reason for c in res.conflicts), status


def test_dated_terminal_statuses_proven() -> None:
    identity = _base_identity()
    cases = (
        ("client_paid", "won", True),
        ("delivered", "post_sale", True),
        ("cancelled", "lost", True),
    )
    for status, stage, terminal in cases:
        res = resolve_opportunities(
            identity=identity,
            deals=[_deal(deal_status=status, updated_at="2026-04-01T00:00:00+00:00")],
            events=[],
            documents=[],
            payments=[],
        )
        opp = res.opportunities[0]
        assert opp.canonical_stage == stage, status
        assert opp.stage_is_terminal is terminal
        assert opp.stage_is_current is False


def test_closed_without_support_needs_review() -> None:
    identity = _base_identity()
    res = resolve_opportunities(
        identity=identity,
        deals=[
            _deal(
                deal_status="closed",
                updated_at="2026-04-01T00:00:00+00:00",
                client_contact_email="buyer@hospital.cl",
                client_domain="hospital.cl",
            )
        ],
        events=[],
        documents=[],
        payments=[],
    )
    opp = res.opportunities[0]
    assert opp.canonical_stage == "unknown"
    assert opp.review_status == "needs_review"
    assert any(c.reason_code == "closed_without_supporting_evidence" for c in res.conflicts)
    # Evidence points at closed deal status provenance
    ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert ev.source_table == "commercial_deal"
    assert ev.evidence_type == "deal_status"


def test_closed_with_payment_evidence_is_won() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            deal_status="closed",
            updated_at="2026-04-01T00:00:00+00:00",
            client_contact_email="buyer@hospital.cl",
            client_domain="hospital.cl",
        )
    ]
    events = [
        _event(event_type="client_payment_received", event_at="2026-03-15T00:00:00+00:00", event_id=1)
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "won"
    assert opp.review_status == "ok"
    assert opp.stage_evidence_id is not None
    ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert ev.source_table == "commercial_deal_event"
    assert ev.evidence_type == "client_payment_received"
    assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)
    assert not any(c.reason_code == "closed_without_supporting_evidence" for c in res.conflicts)


def test_closed_with_delivered_evidence_is_post_sale() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            deal_status="closed",
            updated_at="2026-04-01T00:00:00+00:00",
            client_contact_email="buyer@hospital.cl",
            client_domain="hospital.cl",
        )
    ]
    events = [_event(event_type="delivered", event_at="2026-03-20T00:00:00+00:00", event_id=1)]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "post_sale"
    assert opp.review_status == "ok"
    ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert ev.evidence_type == "delivered"
    assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)


def test_closed_with_cancellation_evidence_is_lost() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            deal_status="closed",
            updated_at="2026-04-01T00:00:00+00:00",
            client_contact_email="buyer@hospital.cl",
            client_domain="hospital.cl",
        )
    ]
    events = [
        _event(event_type="deal_cancelled", event_at="2026-03-10T00:00:00+00:00", event_id=1)
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "lost"
    assert opp.review_status == "ok"
    ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert ev.evidence_type == "deal_cancelled"
    assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)


def test_closed_with_inbound_payment_row_is_won() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            deal_status="closed",
            updated_at="2026-04-01T00:00:00+00:00",
            client_contact_email="buyer@hospital.cl",
            client_domain="hospital.cl",
        )
    ]
    payments = [
        SourceDealPaymentRow(
            payment_id=1,
            deal_id=1,
            deal_key="deal-a",
            direction="inbound",
            paid_at="2026-03-12T00:00:00+00:00",
            confidence="extracted_high",
        )
    ]
    res = resolve_opportunities(
        identity=identity, deals=deals, events=[], documents=[], payments=payments
    )
    opp = res.opportunities[0]
    assert opp.canonical_stage == "won"
    assert opp.review_status == "ok"
    ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert ev.source_table == "commercial_deal_payment"
    assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)


def test_supplier_proforma_alone_does_not_fulfill() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    docs = [
        SourceDealDocumentRow(
            document_id=1,
            deal_id=1,
            deal_key="deal-a",
            document_type="supplier_proforma",
            issued_at="2026-02-01T00:00:00+00:00",
            confidence="extracted_high",
            source_email_id=None,
            source_attachment_id=None,
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=docs, payments=[])
    assert res.opportunities[0].canonical_stage == "quote_sent"
    assert res.opportunities[0].stage_is_current is True
    proforma = [e for e in res.evidence if e.evidence_type == "supplier_proforma"]
    assert len(proforma) == 1
    assert proforma[0].reason_code == "deal_document_non_stage"


def test_supplier_proforma_after_client_po_never_wins_fulfillment() -> None:
    """Proforma remains provenance even after client commitment; cannot select fulfillment."""
    identity = _base_identity()
    deals = [_deal(deal_status="client_po_received", updated_at="2026-02-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T00:00:00+00:00", event_id=1)
    ]
    docs = [
        SourceDealDocumentRow(
            document_id=1,
            deal_id=1,
            deal_key="deal-a",
            document_type="supplier_proforma",
            issued_at="2026-02-10T00:00:00+00:00",
            confidence="extracted_high",
            source_email_id=None,
            source_attachment_id=None,
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=docs, payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "purchase_pending"
    assert opp.stage_evidence_id is not None
    winner_ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert winner_ev.evidence_type != "supplier_proforma"
    proforma = [e for e in res.evidence if e.evidence_type == "supplier_proforma"]
    assert len(proforma) == 1


def test_payment_voucher_alone_does_not_establish_won() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    docs = [
        SourceDealDocumentRow(
            document_id=1,
            deal_id=1,
            deal_key="deal-a",
            document_type="payment_voucher",
            issued_at="2026-02-01T00:00:00+00:00",
            confidence="extracted_high",
            source_email_id=None,
            source_attachment_id=None,
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=docs, payments=[])
    assert res.opportunities[0].canonical_stage == "quote_sent"


def test_outbound_payment_refines_fulfillment_not_won() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="client_paid", updated_at="2026-02-01T00:00:00+00:00")]
    pays = [
        SourceDealPaymentRow(
            payment_id=1,
            deal_id=1,
            deal_key="deal-a",
            direction="outbound",
            paid_at="2026-02-15T00:00:00+00:00",
            confidence="operator_confirmed",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=pays)
    assert res.opportunities[0].canonical_stage == "fulfillment"


def test_stage_evidence_provenance_invariant() -> None:
    identity = _base_identity()
    deals = [_deal(updated_at="2026-01-10T00:00:00+00:00")]
    events = [_event(event_at="2026-01-15T00:00:00+00:00", event_id=1)]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.stage_evidence_id is not None
    matches = [e for e in res.evidence if e.evidence_id == opp.stage_evidence_id]
    assert len(matches) == 1
    assert matches[0].opportunity_id == opp.opportunity_id


def test_fingerprint_detects_contact_account_move() -> None:
    base = resolve_identity(
        [
            _identity_row(
                email_raw="a@hospital.cl",
                organization_name="Hospital Sur",
                domain_raw="hospital.cl",
            )
        ]
    )
    fp1 = identity_resolution_fingerprint(base)
    contact = base.contacts[0]
    moved = IdentityResolution(
        accounts=list(base.accounts),
        contacts=[
            ContactRecord(
                contact_id=contact.contact_id,
                normalized_email=contact.normalized_email,
                display_name=contact.display_name,
                role=contact.role,
                account_id="a_other",
                account_link_method="forced_test",
                first_evidence_at=contact.first_evidence_at,
                last_evidence_at=contact.last_evidence_at,
                identity_confidence=contact.identity_confidence,
                identity_status=contact.identity_status,
                email_domain=contact.email_domain,
            )
        ],
        evidence=list(base.evidence),
        conflicts=list(base.conflicts),
        metrics={},
    )
    assert identity_resolution_fingerprint(moved) != fp1


def test_fingerprint_detects_status_change() -> None:
    base = resolve_identity(
        [
            _identity_row(
                email_raw="a@hospital.cl",
                organization_name="Hospital Sur",
                domain_raw="hospital.cl",
            )
        ]
    )
    fp1 = identity_resolution_fingerprint(base)
    c0 = base.contacts[0]
    changed = IdentityResolution(
        accounts=list(base.accounts),
        contacts=[
            ContactRecord(
                contact_id=c0.contact_id,
                normalized_email=c0.normalized_email,
                display_name=c0.display_name,
                role=c0.role,
                account_id=c0.account_id,
                account_link_method=c0.account_link_method,
                first_evidence_at=c0.first_evidence_at,
                last_evidence_at=c0.last_evidence_at,
                identity_confidence=c0.identity_confidence,
                identity_status="needs_review",
                email_domain=c0.email_domain,
            )
        ],
        evidence=list(base.evidence),
        conflicts=list(base.conflicts),
        metrics={},
    )
    assert identity_resolution_fingerprint(changed) != fp1
    assert FINGERPRINT_ALGORITHM_VERSION == "identity_fp_v2"


def test_generic_email_history_not_opportunity() -> None:
    identity = _base_identity()
    cm = [
        SourceContactMasterRow(
            contact_id="1",
            email="buyer@hospital.cl",
            organization_name="Hospital Sur",
            total_emails=50,
            inbound_emails=20,
            outbound_emails=30,
        )
    ]
    res = resolve_opportunities(
        identity=identity, deals=[], events=[], documents=[], payments=[], contact_master=cm
    )
    assert res.metrics["commercial_history_count"] == 0
    assert res.metrics["canonical_opportunity_count"] == 0


def test_signal_not_skipped_for_same_contact_different_email_id() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            client_contact_email="buyer@hospital.cl",
            updated_at="2026-01-10T00:00:00+00:00",
        )
    ]
    events = [
        _event(
            event_at="2026-01-10T00:00:00+00:00",
            source_email_id=10,
            event_id=1,
        )
    ]
    signals = [
        SourceSignalRow(
            signal_id="sig-indep",
            signal_type="quote_signal",
            entity_kind="contact",
            entity_key="buyer@hospital.cl",
            email_id=99,
            email_date="2026-02-01T00:00:00+00:00",
            created_at="2026-06-01T00:00:00+00:00",
        )
    ]
    res = resolve_opportunities(
        identity=identity, deals=deals, events=events, documents=[], payments=[], signals=signals
    )
    assert res.metrics["explicit_deal_opportunity_count"] == 1
    assert res.metrics["evidence_candidate_count"] == 1


def test_consumer_email_with_institutional_deal_domain_partial_link() -> None:
    identity = resolve_identity(
        [
            _identity_row(
                email_raw="person@gmail.com",
                organization_name="Some Lab",
                domain_raw="gmail.com",
            ),
            _identity_row(
                email_raw="staff@hospital.cl",
                organization_name="Hospital Sur",
                domain_raw="hospital.cl",
                source_record_id="inst",
            ),
        ]
    )
    deals = [
        _deal(
            client_contact_email="person@gmail.com",
            client_domain="hospital.cl",
            updated_at="2026-01-10T00:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.account_id == stable_account_id_for_domain("hospital.cl")
    assert opp.primary_contact_id == stable_contact_id("person@gmail.com")
    assert opp.identity_link_status == "account_via_deal_domain"


def test_conflict_subject_keys_exclude_raw_email() -> None:
    identity = _base_identity()
    deals = [
        _deal(
            deal_key="no-id-deal",
            client_contact_email="unknown@mystery.example",
            client_domain=None,
            updated_at="2026-01-10T00:00:00+00:00",
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=[], documents=[], payments=[])
    blob = " ".join(c.subject_keys_json for c in res.conflicts)
    assert "unknown@mystery.example" not in blob


def test_dry_run_match_status_truthful(tmp_path: Path) -> None:
    db = tmp_path / "match.sqlite"
    _seed_minimal_db(db)
    dry_missing = run_opportunity_build(
        sqlite_path=db, apply=False, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    assert dry_missing["identity_fingerprint_match_status"] == "missing"
    run_identity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    dry_ok = run_opportunity_build(
        sqlite_path=db, apply=False, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    assert dry_ok["identity_fingerprint_match_status"] == "matched"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO commercial_identity_build_meta(meta_key, meta_value)
        VALUES ('identity_fingerprint_algorithm_version', 'identity_fp_v0')
        ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
        """
    )
    conn.commit()
    conn.close()
    dry_ver = run_opportunity_build(
        sqlite_path=db, apply=False, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )
    assert dry_ver["identity_fingerprint_match_status"] == "version_mismatch"


def test_run_context_mode_combinations(tmp_path: Path) -> None:
    db = tmp_path / "mode.sqlite"
    _seed_minimal_db(db)
    with pytest.raises(CommercialIdentityPathError):
        run_opportunity_build(
            sqlite_path=db, apply=False, run_context=RUN_CONTEXT_PRODUCTION_APPLY
        )
    with pytest.raises(CommercialIdentityPathError):
        run_identity_build(
            sqlite_path=db, apply=True, run_context=RUN_CONTEXT_PRODUCTION_DRY_RUN
        )


def test_stale_source_plan_blocks_apply(tmp_path: Path) -> None:
    db = tmp_path / "stale_src.sqlite"
    _seed_minimal_db(db)
    run_identity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    plan = plan_opportunity_build(
        sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )

    def _mutate(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE commercial_deal SET deal_status='cancelled', updated_at=? WHERE deal_key=?",
            ("2026-09-01T00:00:00+00:00", "fixture-deal"),
        )

    with pytest.raises(StaleBuildPlanError):
        apply_opportunity_build(plan, inject_source_change=_mutate)
    conn = sqlite3.connect(str(db))
    # Previous opportunity dataset may be absent on first apply; ensure no partial write
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='commercial_opportunity'"
    ).fetchone()[0]
    if n:
        # If schema exists from ensure, data must be empty or previous
        count = conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0]
        assert count == 0
    conn.close()


def test_production_loaders_exact_row_counts(tmp_path: Path) -> None:
    db = tmp_path / "prod_schema.sqlite"
    _seed_minimal_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO emails (id, date_iso, sender, source_file)
        VALUES (7, '2026-01-20T10:00:00+00:00', 'buyer@hospital.cl', 'gmail')
        """
    )
    conn.execute(
        """
        INSERT INTO opportunity_signals (
          signal_type, entity_kind, entity_key, email_id, attachment_id,
          score, details_json, created_at
        ) VALUES (
          'quote_signal', 'contact', 'buyer@hospital.cl', 7, NULL,
          0.9, '{"k":"v"}', '2026-06-01T00:00:00+00:00'
        )
        """
    )
    conn.execute(
        """
        UPDATE contact_master SET quote_email_count=2, invoice_email_count=1
        WHERE email='buyer@hospital.cl'
        """
    )
    conn.commit()
    signals = load_opportunity_signals(conn)
    contacts = load_contact_master_history(conn)
    assert len(signals) == 1
    assert signals[0].email_date == "2026-01-20T10:00:00+00:00"
    assert signals[0].contact_email == "buyer@hospital.cl"
    assert len(contacts) == 1
    assert contacts[0].has_typed_commercial_evidence is True
    conn.close()


def test_bad_opportunity_signals_schema_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "bad_sig.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY,
          contact_email TEXT,
          created_at TEXT
        )
        """
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    with pytest.raises(SourceSchemaError):
        load_opportunity_signals(conn)
    conn.close()


def test_incompatible_fingerprint_version_blocks_apply(tmp_path: Path) -> None:
    db = tmp_path / "fpver.sqlite"
    _seed_minimal_db(db)
    run_identity_build(sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO commercial_identity_build_meta(meta_key, meta_value)
        VALUES ('identity_fingerprint_algorithm_version', 'identity_fp_v0')
        ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
        """
    )
    conn.commit()
    conn.close()
    with pytest.raises(IdentitySnapshotError):
        run_opportunity_build(
            sqlite_path=db, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
        )


def test_utc_instant_order_overrides_lexical_iso_for_hard_terminals() -> None:
    """Lexical ISO order disagrees with UTC: later lexical string is earlier instant."""
    identity = _base_identity()
    # Lexically: 2026-01-02T01:00:00+02:00 > 2026-01-01T23:30:00-04:00
    # UTC:       2026-01-01T23:00:00Z      < 2026-01-02T03:30:00Z
    earlier_lex_later_utc = "2026-01-01T23:30:00-04:00"  # 03:30Z next calendar day UTC
    later_lex_earlier_utc = "2026-01-02T01:00:00+02:00"  # 23:00Z same calendar day UTC
    assert later_lex_earlier_utc > earlier_lex_later_utc  # lexical trap

    deals = [
        _deal(
            deal_status="quoted",
            updated_at="2026-01-01T00:00:00+00:00",
            client_contact_email="buyer@hospital.cl",
            client_domain="hospital.cl",
        )
    ]
    events_a = [
        _event(event_type="delivered", event_at=later_lex_earlier_utc, event_id=1),
        _event(event_type="deal_cancelled", event_at=earlier_lex_later_utc, event_id=2),
    ]
    events_b = list(reversed(events_a))
    res_a = resolve_opportunities(
        identity=identity, deals=deals, events=events_a, documents=[], payments=[]
    )
    res_b = resolve_opportunities(
        identity=identity, deals=deals, events=events_b, documents=[], payments=[]
    )
    assert res_a.opportunities[0].canonical_stage == "lost"
    assert res_b.opportunities[0].canonical_stage == "lost"
    assert res_a.opportunities[0].stage_evidence_at == earlier_lex_later_utc
    assert res_b.opportunities[0].stage_evidence_at == earlier_lex_later_utc
    assert any(c.reason_code == "conflicting_terminal_events" for c in res_a.conflicts)
    assert any(c.reason_code == "conflicting_terminal_events" for c in res_b.conflicts)
    assert res_a.opportunities[0].review_status == "needs_review"


def test_malformed_timestamp_emits_stable_conflict_not_current_evidence() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_payment_received", event_at="not-a-timestamp", event_id=1),
        _event(event_type="client_quote_sent", event_at="2026-02-01T00:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(
        identity=identity, deals=deals, events=events, documents=[], payments=[]
    )
    assert any(c.reason_code == "malformed_event_timestamp" for c in res.conflicts)
    opp = res.opportunities[0]
    assert opp.canonical_stage == "quote_sent"
    assert opp.stage_is_current is True
    assert opp.review_status == "needs_review"
    # Malformed payment must not become current won evidence
    assert opp.stage_evidence_at != "not-a-timestamp"


def test_date_only_timestamp_orders_as_utc_midnight_without_inventing_time() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_quote_sent", event_at="2026-02-01", event_id=1),
        _event(event_type="client_po_received", event_at="2026-02-02T12:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(
        identity=identity, deals=deals, events=events, documents=[], payments=[]
    )
    opp = res.opportunities[0]
    assert opp.canonical_stage == "purchase_pending"
    # Original date-only string preserved on the earlier event record
    quote_ev = next(e for e in res.events if e.source_event_type == "client_quote_sent")
    assert quote_ev.event_at == "2026-02-01"


def test_review_status_forced_by_attached_review_conflicts() -> None:
    identity = _base_identity()
    # Unsupported stage + missing event timestamp → needs_review via conflicts
    deals = [_deal(deal_status="not_a_real_status", updated_at="2026-01-01T00:00:00+00:00")]
    events = [_event(event_type="client_quote_sent", event_at=None, event_id=1)]
    res = resolve_opportunities(
        identity=identity, deals=deals, events=events, documents=[], payments=[]
    )
    opp = res.opportunities[0]
    assert opp.review_status == "needs_review"
    assert any(c.reason_code == "unsupported_source_stage" for c in res.conflicts)
    assert any(c.reason_code == "source_event_missing_timestamp" for c in res.conflicts)

    # Invariant: no opportunity may be ok while carrying a review-requiring conflict
    from origenlab_email_pipeline.commercial_opportunity.constants import (
        REVIEW_REQUIRING_CONFLICT_REASONS,
    )

    for o in res.opportunities:
        attached = [c for c in res.conflicts if c.opportunity_id == o.opportunity_id]
        if any(c.reason_code in REVIEW_REQUIRING_CONFLICT_REASONS for c in attached):
            assert o.review_status == "needs_review"


def test_deal_status_needs_review_forces_review_even_without_extra_conflict() -> None:
    identity = _base_identity()
    res = resolve_opportunities(
        identity=identity,
        deals=[_deal(deal_status="needs_review", updated_at="2026-01-01T00:00:00+00:00")],
        events=[],
        documents=[],
        payments=[],
    )
    assert res.opportunities[0].canonical_stage == "unknown"
    assert res.opportunities[0].review_status == "needs_review"


def test_cli_operator_contract_errors_no_traceback(tmp_path: Path) -> None:
    import importlib.util
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/commercial/build_commercial_opportunity_read_model.py"

    # Invalid production run-context/mode
    db = tmp_path / "cli_mode.sqlite"
    _seed_minimal_db(db)
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--sqlite-path",
            str(db),
            "--run-context",
            "production_apply",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "error:" in proc.stderr
    assert "Traceback" not in proc.stderr

    # Missing identity snapshot on apply
    proc2 = subprocess.run(
        [
            sys.executable,
            str(script),
            "--sqlite-path",
            str(db),
            "--apply",
            "--run-context",
            "synthetic_fixture",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc2.returncode == 3
    assert "error:" in proc2.stderr
    assert "Traceback" not in proc2.stderr

    # Incompatible source schema (opportunity_signals)
    bad = tmp_path / "cli_schema.sqlite"
    conn = sqlite3.connect(str(bad))
    conn.executescript(
        """
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT,
          organization_type_guess TEXT,
          first_seen_at TEXT, last_seen_at TEXT,
          total_emails INTEGER, total_contacts INTEGER,
          quote_email_count INTEGER, invoice_email_count INTEGER,
          purchase_email_count INTEGER, business_doc_email_count INTEGER,
          quote_doc_count INTEGER, invoice_doc_count INTEGER,
          top_equipment_tags TEXT, key_contacts TEXT
        );
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          display_name TEXT, organization_name_guess TEXT, domain TEXT,
          first_seen_at TEXT, last_seen_at TEXT,
          total_emails INTEGER, inbound_emails INTEGER, outbound_emails INTEGER,
          as_sender_count INTEGER, as_recipient_count INTEGER,
          quote_email_count INTEGER, invoice_email_count INTEGER,
          purchase_email_count INTEGER, business_doc_email_count INTEGER,
          quote_doc_count INTEGER, invoice_doc_count INTEGER
        );
        CREATE TABLE emails (id INTEGER PRIMARY KEY, date_iso TEXT, sender TEXT, source_file TEXT);
        CREATE TABLE commercial_deal (
          id INTEGER PRIMARY KEY, deal_key TEXT NOT NULL UNIQUE, deal_status TEXT NOT NULL,
          client_org_name TEXT NOT NULL, client_domain TEXT, client_contact_email TEXT,
          supplier_org_name TEXT, supplier_domain TEXT,
          confidence TEXT NOT NULL DEFAULT 'extracted_high', created_at TEXT, updated_at TEXT
        );
        CREATE TABLE commercial_deal_event (
          id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, event_type TEXT NOT NULL,
          event_at TEXT, confidence TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
          source_email_id INTEGER, source_attachment_id INTEGER, created_at TEXT
        );
        CREATE TABLE commercial_deal_document (
          id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, document_type TEXT NOT NULL,
          issued_at TEXT, confidence TEXT NOT NULL, source_email_id INTEGER,
          source_attachment_id INTEGER, created_at TEXT
        );
        CREATE TABLE commercial_deal_payment (
          id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, direction TEXT NOT NULL,
          paid_at TEXT, confidence TEXT NOT NULL, created_at TEXT
        );
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY, contact_email TEXT, created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    proc3 = subprocess.run(
        [
            sys.executable,
            str(script),
            "--sqlite-path",
            str(bad),
            "--run-context",
            "synthetic_fixture",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc3.returncode == 4
    assert "error:" in proc3.stderr
    assert "Traceback" not in proc3.stderr

    # Stale build plan: plan then mutate sources before apply via library (exit path tested
    # through CLI would require two-step; exercise StaleBuildPlanError mapping via import).
    good = tmp_path / "cli_stale.sqlite"
    _seed_minimal_db(good)
    run_identity_build(sqlite_path=good, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE)
    # Import CLI main and simulate by catching through subprocess after identity apply
    # with injected stale path: call apply_opportunity_build raises; CLI maps exit 5 when
    # run_opportunity_build apply races — use library raise to confirm code path exists, then
    # force via monkeypatch-free: mutate between plan+apply is library-only. Drive CLI by
    # replacing fingerprint mid-flight is hard; instead invoke main with a db that fails
    # stale check inside apply after identity is present — use inject via Python one-liner.
    from origenlab_email_pipeline.commercial_opportunity.builder import (
        apply_opportunity_build,
        plan_opportunity_build,
    )

    plan = plan_opportunity_build(
        sqlite_path=good, apply=True, run_context=RUN_CONTEXT_SYNTHETIC_FIXTURE
    )

    def _mutate(conn: sqlite3.Connection) -> None:
        conn.execute(
            "UPDATE commercial_deal SET deal_status='cancelled' WHERE deal_key=?",
            ("fixture-deal",),
        )

    with pytest.raises(StaleBuildPlanError):
        apply_opportunity_build(plan, inject_source_change=_mutate)

    # CLI exit 5: import main and call with patched run that raises StaleBuildPlanError
    spec = importlib.util.spec_from_file_location("opp_cli", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def _raise_stale(*_a, **_k):
        raise StaleBuildPlanError("stale plan test")

    original = mod.run_opportunity_build
    mod.run_opportunity_build = _raise_stale  # type: ignore[assignment]
    try:
        code = mod.main(
            [
                "--sqlite-path",
                str(good),
                "--apply",
                "--run-context",
                "synthetic_fixture",
            ]
        )
    finally:
        mod.run_opportunity_build = original  # type: ignore[assignment]
    assert code == 5


def _linked_deal(**kwargs: object) -> SourceDealRow:
    kwargs.setdefault("client_contact_email", "buyer@hospital.cl")
    kwargs.setdefault("client_domain", "hospital.cl")
    return _deal(**kwargs)


def test_quoted_plus_deal_closed_without_support_retains_quote_sent() -> None:
    identity = _base_identity()
    deals = [_linked_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events_a = [
        _event(event_type="deal_closed", event_at="2026-03-01T00:00:00+00:00", event_id=1),
    ]
    events_b = list(events_a)  # single-event order invariant
    for events in (events_a, events_b):
        res = resolve_opportunities(
            identity=identity, deals=deals, events=events, documents=[], payments=[]
        )
        opp = res.opportunities[0]
        assert opp.canonical_stage == "quote_sent"
        assert opp.review_status == "needs_review"
        assert any(c.reason_code == "closed_without_supporting_evidence" for c in res.conflicts)
        assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)
        claim = next(
            c for c in res.conflicts if c.reason_code == "closed_without_supporting_evidence"
        )
        keys = json.loads(claim.subject_keys_json)
        assert keys.get("closure_claim_from_deal_closed_event") is True
        assert keys.get("closure_claim_from_deal_status") is False


def test_quoted_plus_deal_closed_plus_payment_is_won() -> None:
    identity = _base_identity()
    deals = [_linked_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events_fwd = [
        _event(event_type="deal_closed", event_at="2026-03-01T00:00:00+00:00", event_id=1),
        _event(
            event_type="client_payment_received",
            event_at="2026-02-15T00:00:00+00:00",
            event_id=2,
        ),
    ]
    events_rev = list(reversed(events_fwd))
    for events in (events_fwd, events_rev):
        res = resolve_opportunities(
            identity=identity, deals=deals, events=events, documents=[], payments=[]
        )
        opp = res.opportunities[0]
        assert opp.canonical_stage == "won"
        assert opp.review_status == "ok"
        ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
        assert ev.evidence_type == "client_payment_received"
        assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)
        assert not any(
            c.reason_code == "closed_without_supporting_evidence" for c in res.conflicts
        )


def test_header_closed_plus_deal_closed_plus_delivered_is_post_sale() -> None:
    identity = _base_identity()
    deals = [_linked_deal(deal_status="closed", updated_at="2026-04-01T00:00:00+00:00")]
    events_fwd = [
        _event(event_type="deal_closed", event_at="2026-03-01T00:00:00+00:00", event_id=1),
        _event(event_type="delivered", event_at="2026-03-20T00:00:00+00:00", event_id=2),
    ]
    events_rev = list(reversed(events_fwd))
    for events in (events_fwd, events_rev):
        res = resolve_opportunities(
            identity=identity, deals=deals, events=events, documents=[], payments=[]
        )
        opp = res.opportunities[0]
        assert opp.canonical_stage == "post_sale"
        assert opp.review_status == "ok"
        ev = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
        assert ev.evidence_type == "delivered"
        assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)


def test_undated_terminal_claims_from_any_source_unproven() -> None:
    identity = _base_identity()
    cases = (
        ("client_payment_received", "commercial_deal_event"),
        ("delivered", "commercial_deal_event"),
        ("deal_cancelled", "commercial_deal_event"),
    )
    for event_type, _table in cases:
        res = resolve_opportunities(
            identity=identity,
            deals=[_linked_deal(deal_status="quoted", updated_at=None, created_at=None)],
            events=[_event(event_type=event_type, event_at=None, event_id=1)],
            documents=[],
            payments=[],
        )
        opp = res.opportunities[0]
        assert opp.canonical_stage == "unknown", event_type
        assert opp.stage_is_current is False
        assert opp.stage_is_terminal is False
        assert opp.stage_confidence == "unavailable"
        assert opp.review_status == "needs_review"
        assert any(c.reason_code == "undated_terminal_unproven" for c in res.conflicts)
        assert opp.stage_evidence_id is not None

    # Undated inbound payment row
    res_pay = resolve_opportunities(
        identity=identity,
        deals=[_linked_deal(deal_status="quoted", updated_at=None, created_at=None)],
        events=[],
        documents=[],
        payments=[
            SourceDealPaymentRow(
                payment_id=1,
                deal_id=1,
                deal_key="deal-a",
                direction="inbound",
                paid_at=None,
                confidence="extracted_high",
            )
        ],
    )
    opp = res_pay.opportunities[0]
    assert opp.canonical_stage == "unknown"
    assert opp.stage_is_terminal is False
    assert opp.stage_confidence == "unavailable"
    assert any(c.reason_code == "undated_terminal_unproven" for c in res_pay.conflicts)
    assert opp.review_status == "needs_review"


def test_dated_terminal_claims_from_events_and_payments_proven() -> None:
    identity = _base_identity()
    cases = (
        ("client_payment_received", "won"),
        ("delivered", "post_sale"),
        ("deal_cancelled", "lost"),
    )
    for event_type, stage in cases:
        res = resolve_opportunities(
            identity=identity,
            deals=[
                _linked_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")
            ],
            events=[
                _event(
                    event_type=event_type,
                    event_at="2026-02-01T00:00:00+00:00",
                    event_id=1,
                )
            ],
            documents=[],
            payments=[],
        )
        opp = res.opportunities[0]
        assert opp.canonical_stage == stage, event_type
        assert opp.stage_is_terminal is True
        assert opp.review_status == "ok"
        assert not any(c.reason_code == "undated_terminal_unproven" for c in res.conflicts)

    res_pay = resolve_opportunities(
        identity=identity,
        deals=[_linked_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")],
        events=[],
        documents=[],
        payments=[
            SourceDealPaymentRow(
                payment_id=1,
                deal_id=1,
                deal_key="deal-a",
                direction="inbound",
                paid_at="2026-02-01T00:00:00+00:00",
                confidence="extracted_high",
            )
        ],
    )
    opp = res_pay.opportunities[0]
    assert opp.canonical_stage == "won"
    assert opp.stage_is_terminal is True
    assert opp.review_status == "ok"


def test_cli_deal_ledger_schema_errors_exit_4(tmp_path: Path) -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/commercial/build_commercial_opportunity_read_model.py"

    base_tables = """
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          organization_name_guess TEXT, organization_type_guess TEXT,
          first_seen_at TEXT, last_seen_at TEXT,
          total_emails INTEGER, total_contacts INTEGER,
          quote_email_count INTEGER, invoice_email_count INTEGER,
          purchase_email_count INTEGER, business_doc_email_count INTEGER,
          quote_doc_count INTEGER, invoice_doc_count INTEGER,
          top_equipment_tags TEXT, key_contacts TEXT
        );
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          display_name TEXT, organization_name_guess TEXT, domain TEXT,
          first_seen_at TEXT, last_seen_at TEXT,
          total_emails INTEGER, inbound_emails INTEGER, outbound_emails INTEGER,
          quote_email_count INTEGER, invoice_email_count INTEGER,
          purchase_email_count INTEGER, business_doc_email_count INTEGER,
          quote_doc_count INTEGER, invoice_doc_count INTEGER
        );
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY, date_iso TEXT, sender TEXT, source_file TEXT
        );
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_type TEXT NOT NULL, entity_kind TEXT NOT NULL, entity_key TEXT NOT NULL,
          email_id INTEGER, attachment_id INTEGER, score REAL, details_json TEXT, created_at TEXT
        );
    """
    good_deal = """
        CREATE TABLE commercial_deal (
          id INTEGER PRIMARY KEY, deal_key TEXT NOT NULL UNIQUE, deal_status TEXT NOT NULL,
          client_org_name TEXT NOT NULL, client_domain TEXT, client_contact_email TEXT,
          supplier_org_name TEXT, supplier_domain TEXT,
          confidence TEXT NOT NULL DEFAULT 'extracted_high', created_at TEXT, updated_at TEXT
        );
    """
    good_event = """
        CREATE TABLE commercial_deal_event (
          id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, event_type TEXT NOT NULL,
          event_at TEXT, confidence TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
          source_email_id INTEGER, source_attachment_id INTEGER, created_at TEXT
        );
    """
    good_doc = """
        CREATE TABLE commercial_deal_document (
          id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, document_type TEXT NOT NULL,
          issued_at TEXT, confidence TEXT NOT NULL, source_email_id INTEGER,
          source_attachment_id INTEGER, created_at TEXT
        );
    """
    good_pay = """
        CREATE TABLE commercial_deal_payment (
          id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, direction TEXT NOT NULL,
          paid_at TEXT, confidence TEXT NOT NULL, created_at TEXT
        );
    """
    malformed = {
        "commercial_deal": (
            """
            CREATE TABLE commercial_deal (
              id INTEGER PRIMARY KEY, deal_key TEXT, status TEXT
            );
            """
            + good_event
            + good_doc
            + good_pay,
            "commercial_deal",
        ),
        "commercial_deal_event": (
            good_deal
            + """
            CREATE TABLE commercial_deal_event (
              id INTEGER PRIMARY KEY, deal_id INTEGER, kind TEXT
            );
            """
            + good_doc
            + good_pay,
            "commercial_deal_event",
        ),
        "commercial_deal_document": (
            good_deal
            + good_event
            + """
            CREATE TABLE commercial_deal_document (
              id INTEGER PRIMARY KEY, deal_id INTEGER, doc_kind TEXT
            );
            """
            + good_pay,
            "commercial_deal_document",
        ),
        "commercial_deal_payment": (
            good_deal
            + good_event
            + good_doc
            + """
            CREATE TABLE commercial_deal_payment (
              id INTEGER PRIMARY KEY, deal_id INTEGER, amount TEXT
            );
            """,
            "commercial_deal_payment",
        ),
    }
    for name, (ddl, expect_table) in malformed.items():
        db = tmp_path / f"cli_schema_{name}.sqlite"
        conn = sqlite3.connect(str(db))
        conn.executescript(base_tables + ddl)
        conn.commit()
        conn.close()
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--sqlite-path",
                str(db),
                "--run-context",
                "synthetic_fixture",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 4, (name, proc.stderr)
        assert "error:" in proc.stderr
        assert f"source_schema_incompatible:{expect_table}:" in proc.stderr
        assert "Traceback" not in proc.stderr
        assert "OperationalError" not in proc.stderr


# --- Causal fulfillment gating / source-side / same-stage (PR3 follow-up) ---


def test_every_deal_status_has_explicit_source_side() -> None:
    from origenlab_email_pipeline.commercial_opportunity.constants import (
        DEAL_STATUS_SOURCE_SIDE,
        DEAL_STATUS_STAGE_MAP,
        SOURCE_SIDE_CLIENT,
        SOURCE_SIDE_SUPPLIER,
    )
    from origenlab_email_pipeline.commercial_opportunity.stage_map import (
        deal_status_is_client_side,
        deal_status_is_supplier_side,
        deal_status_source_side,
    )

    assert set(DEAL_STATUS_SOURCE_SIDE) == set(DEAL_STATUS_STAGE_MAP)
    client_statuses = {
        "draft",
        "quoted",
        "client_po_received",
        "client_invoiced",
        "client_paid",
        "cancelled",
        "delivered",
        "needs_review",
        "closed",
    }
    supplier_statuses = {
        "supplier_po_sent",
        "supplier_invoiced",
        "supplier_paid",
        "logistics_pending",
        "in_transit",
    }
    for status in client_statuses:
        assert deal_status_source_side(status) == SOURCE_SIDE_CLIENT
        assert deal_status_is_client_side(status) is True
        assert deal_status_is_supplier_side(status) is False
    for status in supplier_statuses:
        assert deal_status_source_side(status) == SOURCE_SIDE_SUPPLIER
        assert deal_status_is_client_side(status) is False
        assert deal_status_is_supplier_side(status) is True


def test_supplier_deal_statuses_do_not_count_as_client_commitment() -> None:
    identity = _base_identity()
    for status in (
        "supplier_po_sent",
        "supplier_invoiced",
        "supplier_paid",
        "logistics_pending",
        "in_transit",
    ):
        deals = [_deal(deal_status=status, updated_at="2026-02-01T12:00:00+00:00")]
        docs = [
            SourceDealDocumentRow(
                document_id=1,
                deal_id=1,
                deal_key="deal-a",
                document_type="supplier_proforma",
                issued_at="2026-02-02T12:00:00+00:00",
                confidence="extracted_high",
                source_email_id=None,
                source_attachment_id=None,
            )
        ]
        res = resolve_opportunities(
            identity=identity, deals=deals, events=[], documents=docs, payments=[]
        )
        # Supplier header alone is withheld without client commitment → unknown/undated path
        # or non-fulfillment from proforma. Proforma must never win.
        opp = res.opportunities[0]
        if opp.stage_evidence_id:
            winner = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
            assert winner.evidence_type != "supplier_proforma", status
        assert status
        withheld = [
            e
            for e in res.evidence
            if e.reason_code == "supplier_stage_withheld_before_client_commitment"
        ]
        assert withheld, status
        assert opp.canonical_stage != "fulfillment" or opp.stage_is_current is False


def test_client_statuses_count_as_client_commitment() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T00:00:00+00:00", event_id=1),
        _event(event_type="supplier_po_sent", event_at="2026-02-02T00:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert res.opportunities[0].canonical_stage == "fulfillment"
    assert res.metrics["supplier_stage_candidates_inspected"] == 1
    assert res.metrics["supplier_stage_candidates_withheld_before_commitment"] == 0
    assert res.metrics["supplier_stage_candidates_eligible"] == 1


def test_supplier_execution_before_commitment_is_withheld() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="supplier_po_sent", event_at="2026-02-01T12:00:00+02:00", event_id=1),
        _event(event_type="client_po_received", event_at="2026-02-01T12:00:00-04:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "purchase_pending"
    assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)
    assert res.metrics["supplier_stage_candidates_inspected"] == 1
    assert res.metrics["supplier_stage_candidates_withheld_before_commitment"] == 1
    assert res.metrics["supplier_stage_candidates_eligible"] == 0
    assert any(
        e.reason_code == "supplier_stage_withheld_before_client_commitment" for e in res.evidence
    )


def test_supplier_execution_after_commitment_advances_fulfillment() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T12:00:00-04:00", event_id=1),
        _event(event_type="supplier_po_sent", event_at="2026-02-01T18:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert res.opportunities[0].canonical_stage == "fulfillment"
    assert res.metrics["supplier_stage_candidates_inspected"] == 1
    assert res.metrics["supplier_stage_candidates_withheld_before_commitment"] == 0
    assert res.metrics["supplier_stage_candidates_eligible"] == 1


def test_early_supplier_plus_later_client_po_payment_no_regression() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="supplier_po_sent", event_at="2026-02-01T12:00:00+02:00", event_id=1),
        _event(event_type="client_po_received", event_at="2026-02-01T12:00:00-04:00", event_id=2),
        _event(
            event_type="client_payment_received",
            event_at="2026-02-05T15:00:00-04:00",
            event_id=3,
        ),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage in {"purchase_pending", "won"}
    assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)


def test_fulfillment_plus_later_client_payment_no_regression() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T00:00:00+00:00", event_id=1),
        _event(event_type="supplier_po_sent", event_at="2026-02-02T00:00:00+00:00", event_id=2),
        _event(
            event_type="client_payment_received",
            event_at="2026-02-10T00:00:00+00:00",
            event_id=3,
        ),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "fulfillment"
    assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)
    assert res.metrics["compatible_late_client_prerequisites"] == 1


def test_fulfillment_plus_later_quote_still_regressions() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T00:00:00+00:00", event_id=1),
        _event(event_type="shipment_released", event_at="2026-02-02T00:00:00+00:00", event_id=2),
        _event(event_type="client_quote_sent", event_at="2026-03-01T00:00:00+00:00", event_id=3),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    assert res.opportunities[0].canonical_stage == "fulfillment"
    assert any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)


def test_later_same_stage_logistics_replaces_earlier_supplier_evidence() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T00:00:00+00:00", event_id=1),
        _event(
            event_type="supplier_po_sent",
            event_at="2026-02-02T00:00:00+00:00",
            event_id=2,
            confidence="operator_confirmed",
            operator_confirmed=True,
        ),
        _event(
            event_type="logistics_pending",
            event_at="2026-02-20T00:00:00+00:00",
            event_id=3,
            confidence="extracted_low",
        ),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "fulfillment"
    assert opp.stage_is_current is True
    assert opp.stage_is_terminal is False
    winner = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert winner.evidence_type == "logistics_pending"
    assert opp.stage_evidence_at == "2026-02-20T00:00:00+00:00"
    assert res.metrics["same_stage_winner_advanced_to_later_evidence"] == 1


def test_same_stage_uses_utc_instants_not_lexical_strings() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    # Lexically earlier string is actually later in UTC (+02 vs -04 on same wall clock).
    events = [
        _event(event_type="client_po_received", event_at="2026-05-14T10:00:00+00:00", event_id=1),
        _event(
            event_type="supplier_po_sent",
            event_at="2026-05-14T12:00:00+02:00",  # 10:00Z
            event_id=2,
            confidence="operator_confirmed",
            operator_confirmed=True,
        ),
        _event(
            event_type="logistics_pending",
            event_at="2026-05-14T12:00:00-04:00",  # 16:00Z — later UTC
            event_id=3,
            confidence="extracted_low",
        ),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "fulfillment"
    winner = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
    assert winner.evidence_type == "logistics_pending"


def test_currentness_true_for_dated_logistics_pending() -> None:
    identity = _base_identity()
    deals = [
        _deal(deal_status="logistics_pending", updated_at="2026-03-01T00:00:00+00:00")
    ]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T00:00:00+00:00", event_id=1),
        _event(event_type="logistics_pending", event_at="2026-03-01T00:00:00+00:00", event_id=2),
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=[], payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage == "fulfillment"
    assert opp.stage_is_current is True
    assert opp.stage_is_terminal is False


def test_proforma_only_never_current_fulfillment() -> None:
    identity = _base_identity()
    deals = [_deal(deal_status="quoted", updated_at="2026-01-01T00:00:00+00:00")]
    events = [
        _event(event_type="client_po_received", event_at="2026-02-01T00:00:00+00:00", event_id=1)
    ]
    docs = [
        SourceDealDocumentRow(
            document_id=1,
            deal_id=1,
            deal_key="deal-a",
            document_type="supplier_proforma",
            issued_at="2026-02-05T00:00:00+00:00",
            confidence="extracted_high",
            source_email_id=None,
            source_attachment_id=None,
        )
    ]
    res = resolve_opportunities(identity=identity, deals=deals, events=events, documents=docs, payments=[])
    opp = res.opportunities[0]
    assert opp.canonical_stage != "fulfillment"
    assert opp.stage_is_current is True  # purchase_pending from client PO is current


def test_production_chronology_regression_fixture_redacted() -> None:
    """Synthetic shape of the production dry-run defect (no real identifiers)."""
    identity = _base_identity()
    # Low-entropy synthetic key (historical tip used synthetic-oc-fixture-1;
    # gitleaks fingerprints for that commit remain in .gitleaksignore).
    deal_key = "case-one"
    deals = [
        _deal(
            deal_key=deal_key,
            deal_status="quoted",
            updated_at="2026-05-01T00:00:00+00:00",
            client_contact_email="buyer@hospital.cl",
            client_domain="hospital.cl",
        )
    ]
    events = [
        # Early supplier evidence (UTC before client PO despite wall-clock appearance)
        _event(
            deal_key=deal_key,
            event_type="supplier_po_sent",
            event_at="2026-05-14T12:00:00+02:00",
            event_id=10,
            confidence="extracted_high",
        ),
        # Client PO later in UTC
        _event(
            deal_key=deal_key,
            event_type="client_po_received",
            event_at="2026-05-14T12:00:00-04:00",
            event_id=20,
        ),
        # Client payment later
        _event(
            deal_key=deal_key,
            event_type="client_payment_received",
            event_at="2026-05-20T09:30:00-04:00",
            event_id=30,
        ),
        # Later logistics
        _event(
            deal_key=deal_key,
            event_type="logistics_pending",
            event_at="2026-06-01T15:00:00-04:00",
            event_id=40,
            confidence="extracted_low",
        ),
        # Unrelated undated note (non-stage)
        _event(deal_key=deal_key, event_type="note", event_at=None, event_id=50),
    ]
    docs = [
        SourceDealDocumentRow(
            document_id=1,
            deal_id=1,
            deal_key=deal_key,
            document_type="supplier_proforma",
            issued_at="2026-05-14T12:00:00+02:00",
            confidence="extracted_high",
            source_email_id=None,
            source_attachment_id=None,
        ),
        SourceDealDocumentRow(
            document_id=2,
            deal_id=1,
            deal_key=deal_key,
            document_type="other",
            issued_at=None,
            confidence="extracted_low",
            source_email_id=None,
            source_attachment_id=None,
        ),
    ]
    payments = [
        SourceDealPaymentRow(
            payment_id=1,
            deal_id=1,
            deal_key=deal_key,
            direction="inbound",
            paid_at="2026-05-20T10:00:00-04:00",
            confidence="extracted_high",
        )
    ]

    def _run(evs, docs_in):
        return resolve_opportunities(
            identity=identity,
            deals=deals,
            events=evs,
            documents=docs_in,
            payments=payments,
        )

    res_a = _run(events, docs)
    res_b = _run(list(reversed(events)), list(reversed(docs)))
    for res in (res_a, res_b):
        opp = res.opportunities[0]
        assert opp.canonical_stage == "fulfillment"
        assert opp.stage_is_current is True
        assert opp.stage_is_terminal is False
        winner = next(e for e in res.evidence if e.evidence_id == opp.stage_evidence_id)
        assert winner.evidence_type == "logistics_pending"
        assert not any(c.reason_code == "stage_regression_prevented" for c in res.conflicts)
        assert any(e.evidence_type == "supplier_proforma" for e in res.evidence)
        assert winner.evidence_type != "supplier_proforma"
        assert res.metrics["supplier_stage_candidates_inspected"] == 2
        assert res.metrics["supplier_stage_candidates_withheld_before_commitment"] == 1
        assert res.metrics["supplier_stage_candidates_eligible"] == 1
        assert res.metrics["compatible_late_client_prerequisites"] == 0
        assert res.metrics["same_stage_winner_advanced_to_later_evidence"] == 1
    assert res_a.opportunities[0].stage_evidence_id == res_b.opportunities[0].stage_evidence_id
