"""Synthetic tests for commercial opportunity stage read model (PR3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.commercial_identity.builder import (
    run_identity_build,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
    RUN_CONTEXT_PRODUCTION_DRY_RUN,
    RUN_CONTEXT_SYNTHETIC_FIXTURE,
)
from origenlab_email_pipeline.commercial_identity.fingerprint import identity_resolution_fingerprint
from origenlab_email_pipeline.commercial_identity.ids import (
    stable_account_id_for_domain,
    stable_contact_id,
)
from origenlab_email_pipeline.commercial_identity.models import SourceIdentityRow
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_opportunity.builder import (
    apply_opportunity_build,
    plan_opportunity_build,
    run_opportunity_build,
)
from origenlab_email_pipeline.commercial_opportunity.identity_gate import IdentitySnapshotError
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
        AccountRecord,
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
            contact_email="buyer@hospital.cl",
            organization_name="Hospital Sur",
            signal_type="quote_signal",
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
            contact_email="buyer@hospital.cl",
            signal_type="quote_signal",
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
          quote_email_count INTEGER DEFAULT 0,
          invoice_email_count INTEGER DEFAULT 0,
          purchase_email_count INTEGER DEFAULT 0,
          gmail_sent_count INTEGER DEFAULT 0,
          gmail_received_count INTEGER DEFAULT 0
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
        INSERT INTO organization_master VALUES (
          'hospital.cl', 'Hospital Sur', 'institution', '2023-01-01', '2024-01-01', 10, 1
        );
        INSERT INTO contact_master (
          email, contact_name_best, domain, organization_name_guess, organization_type_guess,
          first_seen_at, last_seen_at, total_emails
        ) VALUES (
          'buyer@hospital.cl', 'Buyer', 'hospital.cl', 'Hospital Sur', 'institution',
          '2023-02-01', '2024-02-01', 3
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
