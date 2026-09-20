"""The Wave 1A / Wave 1B → V2 import path.

These tests prove the properties that make a historical safety load safe to run at all:
the mapping is deterministic and idempotent, an address never becomes a person or a
prospect, a supplier contact stays distinguishable from a customer, the reconciliation
fails closed on a count mismatch, the database boundary refuses anything but a disposable
loopback target, and no address reaches an aggregate report, a console line or a test.

Every fixture is synthetic. Addresses use the reserved ``example.invalid`` domain and the
counts each assertion expects are derived from the fixture literals rather than written
twice, so a fixture change cannot silently invalidate an assertion.

The database-backed tests are skipped unless ``ORIGENLAB_V2_TEST_DSN`` names a disposable
local PostgreSQL carrying the Slice 0 schema. They are never pointed at anything else —
:mod:`..target` refuses a non-loopback DSN regardless.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path
from typing import Any

import pytest

from origenlab_email_pipeline.migration.v2_import.artifacts import (
    ArtifactIdentity,
    ExpectedHashes,
    ImportInputs,
    InputRefused,
    load_inputs,
)
from origenlab_email_pipeline.migration.v2_import.plan import (
    CLOSED_STRUCTURAL_GAPS,
    IMPLEMENTED_CONTACT_CONTROL_SOURCES,
    STRUCTURAL_GAPS,
    MappingRefused,
    build_plan,
    build_supplier_index,
    canonical_address,
    canonical_domain,
    dedupe_key,
)
from origenlab_email_pipeline.migration.v2_import.report import (
    ReportRefused,
    assert_report_is_pii_safe,
    build_aggregate_report,
    render_console,
    write_reject_artifact,
)
from origenlab_email_pipeline.migration.v2_import.target import (
    TargetRefused,
    assert_local_target,
)

# --------------------------------------------------------------------------- #
# The synthetic universe
# --------------------------------------------------------------------------- #

#: Wave 1A contacted union.
UNION = [f"union-{n}@example.invalid" for n in range(1, 6)]
#: RFC 2047 addendum — absent from the union by construction (docs/DATA.md §7.4).
ADDENDUM = ["decoded-1@example.invalid"]
#: A supplier contact that a campaign also reached. This is the Ohaus-class case.
SUPPLIER_CONTACT = "ventas@proveedor.invalid"
#: Wave 1B delta: one address already in Wave 1A (the overlap), one genuinely new, plus
#: the supplier contact, which campaign evidence reached.
WAVE1B_NEW = ["nuevo-1@example.invalid"]
WAVE1B_OVERLAP = [UNION[0]]

WAVE1A_SAFETY = [*UNION, *ADDENDUM]
WAVE1B_COMBINED = [*WAVE1B_OVERLAP, *WAVE1B_NEW, SUPPLIER_CONTACT]
CROSS_WAVE_UNION = sorted({*WAVE1A_SAFETY, *WAVE1B_COMBINED})
CROSS_WAVE_INTERSECTION = sorted(set(WAVE1A_SAFETY) & set(WAVE1B_COMBINED))

SUPPRESSED_1A = ["bounced-1@example.invalid", "bounced-2@example.invalid"]
SUPPRESSED_1B = ["bounced-3@example.invalid"]
DOMAINS_1A = ["blocked.invalid"]

CAMPAIGN_1A = "hielscher-2026"
CAMPAIGN_1B = "septiembre-final"
#: The single historical sender both campaigns were sent from.
SENDER = "contacto@origenlab.invalid"


def _identity(name: str) -> ArtifactIdentity:
    return ArtifactIdentity(name=name, sha256=hashlib.sha256(name.encode()).hexdigest())


def make_inputs(
    *,
    union: list[str] | None = None,
    addendum: list[str] | None = None,
    combined: list[str] | None = None,
    suppressed_1a: list[str] | None = None,
    suppressed_1b: list[str] | None = None,
    domains: list[str] | None = None,
    manual_1a: list[dict[str, Any]] | None = None,
    manual_1b: list[dict[str, Any]] | None = None,
    recipients_1a: list[dict[str, Any]] | None = None,
    recipients_1b: list[dict[str, Any]] | None = None,
    attempts_1a: list[dict[str, Any]] | None = None,
    attempts_1b: list[dict[str, Any]] | None = None,
    supplier_master: list[dict[str, Any]] | None = None,
    supplier_channels: list[dict[str, Any]] | None = None,
    reconciliation: dict[str, Any] | None = None,
) -> ImportInputs:
    """Build a synthetic :class:`ImportInputs` whose counts reconcile by default."""
    union = UNION if union is None else union
    addendum = ADDENDUM if addendum is None else addendum
    combined = WAVE1B_COMBINED if combined is None else combined
    suppressed_1a = SUPPRESSED_1A if suppressed_1a is None else suppressed_1a
    suppressed_1b = SUPPRESSED_1B if suppressed_1b is None else suppressed_1b
    domains = DOMAINS_1A if domains is None else domains

    # The measured report counts *normalized* addresses, so the synthetic report does too.
    # An unnormalizable fixture address is a reject, and a reject is never in a union.
    def norm(values: list[str]) -> set[str]:
        return {a for v in values if (a := canonical_address(v, origin="fixture"))}

    safety = norm(union) | norm(addendum)
    cross = safety | norm(combined)
    overlap = safety & norm(combined)
    address_union = norm(suppressed_1a) | norm(suppressed_1b)

    if reconciliation is None:
        reconciliation = {
            "tool": "synthetic",
            "tool_version": "0",
            "normalizer": "candidate_export_gate.normalize_export_email",
            "counts": {
                "cross_wave_safety_union": len(cross),
                "cross_wave_intersection": len(overlap),
            },
            "address_suppression": {"deduplicated_union": len(address_union)},
            "domain_suppression": {"deduplicated_union": len(domains)},
        }

    return ImportInputs(
        wave1a={
            "recipient_ledger": [
                {"email_norm": a, "in_contacted_union": True} for a in union
            ],
            "contact_email_suppression": [
                {"email": a, "suppression_reason_code": "bounce_no_such_user"}
                for a in suppressed_1a
            ],
            "contact_domain_suppression": [{"domain_norm": d} for d in domains],
            "manual_contact_status": manual_1a or [],
            "outbound_campaign": [
                {
                    "campaign_id": CAMPAIGN_1A,
                    "name": "Wave 1A campaign",
                    "status": "active",
                    "sender_email": "contacto@origenlab.invalid",
                    "sender_name": "OrigenLab",
                    "target_attempt_count": 100,
                }
            ],
            "outbound_campaign_recipient": recipients_1a
            if recipients_1a is not None
            else [
                {
                    "campaign_id": CAMPAIGN_1A,
                    "email_norm": a,
                    "state": "sent",
                    "institution_name": "Laboratorio Uno",
                }
                for a in union
            ],
            "outbound_send_attempt": attempts_1a
            if attempts_1a is not None
            else [
                {
                    "campaign_id": CAMPAIGN_1A,
                    "email_norm": a,
                    "id": f"a-{i}",
                    "result": "accepted",
                    "attempted_at": "2026-01-01T00:00:00Z",
                }
                for i, a in enumerate(union)
            ],
            "outreach_contact_state": [],
            "supplier_master": supplier_master
            if supplier_master is not None
            else [{"domain_norm": "proveedor.invalid", "trade_name": "Proveedor SA"}],
            "supplier_contact_channel": supplier_channels
            if supplier_channels is not None
            else [{"value_normalized": SUPPLIER_CONTACT, "channel_type": "email"}],
        },
        wave1b={
            "combined_prior_contact": [
                {"address": a, "source_categories": ["campaign_accepted"]} for a in combined
            ],
            "campaign_prior_contact": [{"address": a} for a in combined],
            "sent_history_prior_contact": [],
            "outreach_prior_contact": [],
            "contact_email_suppression": [
                {"email": a, "suppression_reason_code": "bounce_other"} for a in suppressed_1b
            ],
            "contact_domain_suppression": [],
            "manual_contact_status": manual_1b or [],
            "outbound_campaign": [
                {
                    "campaign_id": CAMPAIGN_1B,
                    "name": "Septiembre",
                    "status": "active",
                    "sender_email": "contacto@origenlab.invalid",
                    "sender_name": "OrigenLab",
                    "target_attempt_count": 50,
                }
            ],
            "outbound_campaign_recipient": recipients_1b
            if recipients_1b is not None
            else [
                {"campaign_id": CAMPAIGN_1B, "email_norm": a, "state": "sent"} for a in combined
            ]
            + [{"campaign_id": CAMPAIGN_1B, "email_norm": "cand-1@example.invalid", "state": "candidate"}],
            "outbound_send_attempt": attempts_1b
            if attempts_1b is not None
            else [
                {
                    "campaign_id": CAMPAIGN_1B,
                    "email_norm": a,
                    "id": f"b-{i}",
                    "result": "accepted",
                    "attempted_at": "2026-09-18T00:00:00Z",
                }
                for i, a in enumerate(combined)
            ],
            "remaining_candidates": [
                {"campaign_id": CAMPAIGN_1B, "email_norm": "cand-1@example.invalid", "state": "candidate"}
            ],
        },
        addendum=[{"address": a} for a in addendum],
        reconciliation=reconciliation,
        identities=(
            _identity("wave1a_bundle"),
            _identity("wave1a_addendum"),
            _identity("wave1b_bundle"),
            _identity("reconciliation_v2"),
        ),
    )


def controls(plan: Any, kind: str, scope: str = "address") -> list[Any]:
    return [
        r
        for r in plan.table("outbound.contact_control").rows
        if r.columns["kind"] == kind and r.columns["scope"] == scope
    ]


# --------------------------------------------------------------------------- #
# Canonical normalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Person@Example.Invalid", "person@example.invalid"),
        ("  spaced@example.invalid  ", "spaced@example.invalid"),
        ("Name <tagged+x@example.invalid>", "tagged+x@example.invalid"),
    ],
)
def test_addresses_normalize_to_one_canonical_form(raw: str, expected: str) -> None:
    assert canonical_address(raw, origin="t") == expected


@pytest.mark.parametrize("raw", ["", "   ", "no-at-sign", "missing@domain", None, 17])
def test_an_unnormalizable_address_is_none_never_a_repaired_guess(raw: Any) -> None:
    assert canonical_address(raw, origin="t") is None


def test_normalization_is_idempotent() -> None:
    once = canonical_address("MiXeD@Example.Invalid", origin="t")
    assert once is not None
    assert canonical_address(once, origin="t") == once


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Example.Invalid", "example.invalid"), ("@example.invalid", "example.invalid")],
)
def test_domains_normalize(raw: str, expected: str) -> None:
    assert canonical_domain(raw) == expected


@pytest.mark.parametrize("raw", ["", "nodot", "bad domain.invalid", None])
def test_an_unnormalizable_domain_is_none(raw: Any) -> None:
    assert canonical_domain(raw) is None


def test_dedupe_keys_are_stable_across_runs() -> None:
    assert dedupe_key("migration_manifest", "a", "b") == dedupe_key("migration_manifest", "a", "b")
    assert dedupe_key("migration_manifest", "a", "b") != dedupe_key("migration_manifest", "a", "c")


# --------------------------------------------------------------------------- #
# Deterministic, idempotent mapping
# --------------------------------------------------------------------------- #


def test_the_same_inputs_produce_an_identical_plan() -> None:
    first = build_plan(make_inputs())
    second = build_plan(make_inputs())
    assert first.counts == second.counts
    assert [(r.table, r.key, r.columns) for r in first.table("outbound.contact_control").rows] == [
        (r.table, r.key, r.columns) for r in second.table("outbound.contact_control").rows
    ]


def test_every_planned_row_carries_the_natural_key_the_database_enforces() -> None:
    plan = build_plan(make_inputs())
    keys = [r.key for r in plan.table("outbound.contact_control").rows]
    assert len(keys) == len(set(keys)), "a duplicate key would insert twice on a re-run"


def test_the_prior_contact_union_is_deduplicated_across_waves() -> None:
    plan = build_plan(make_inputs())
    prior = controls(plan, "prior_contact")
    assert len(prior) == len(CROSS_WAVE_UNION)
    assert len({r.columns["value_norm"] for r in prior}) == len(CROSS_WAVE_UNION)


def test_the_cross_wave_overlap_is_measured_not_summed() -> None:
    plan = build_plan(make_inputs())
    assert plan.counts["cross_wave_overlap"] == len(CROSS_WAVE_INTERSECTION)
    naive = len(WAVE1A_SAFETY) + len(WAVE1B_COMBINED)
    assert plan.counts["input_prior_contact_union"] < naive


# --------------------------------------------------------------------------- #
# Identity: an address is not a person, a recipient is not a prospect
# --------------------------------------------------------------------------- #


def test_no_crm_table_is_ever_planned() -> None:
    plan = build_plan(make_inputs())
    assert [name for name in plan.tables if name.startswith("crm.")] == []
    assert plan.counts["crm_rows"] == 0


def test_an_address_creates_no_person() -> None:
    plan = build_plan(make_inputs())
    assert "crm.person" not in plan.tables
    assert plan.counts["prospect_or_customer_classified_contacts"] == 0


def test_a_recipient_creates_no_prospect_relationship() -> None:
    plan = build_plan(make_inputs())
    planned_roles = [
        r for rows in plan.tables.values() for r in rows.rows if "role" in r.columns
    ]
    assert planned_roles == []


def test_an_address_with_no_known_owner_stays_unresolved_evidence() -> None:
    plan = build_plan(make_inputs())
    contacted = [
        r for r in plan.table("evidence.assertion").rows if r.columns["kind"] == "contacted_address"
    ]
    assert len(contacted) == len(CROSS_WAVE_UNION)
    assert {r.columns["resolution"] for r in contacted} == {"unresolved"}


def test_campaign_recipients_link_no_identity() -> None:
    plan = build_plan(make_inputs())
    for row in plan.table("outbound.campaign_recipient").rows:
        assert row.columns["contact_point_id"] is None
        assert row.columns["person_id"] is None
        assert row.columns["organization_id"] is None


def test_with_an_empty_crm_every_contact_point_is_new_and_none_is_matched() -> None:
    plan = build_plan(make_inputs())
    assert plan.counts["existing_matched_contact_points"] == 0
    assert plan.counts["new_unresolved_contact_points"] == len(CROSS_WAVE_UNION)
    assert plan.counts["existing_matched_organizations"] == 0


def test_an_observed_institution_name_becomes_unresolved_evidence_not_an_organization() -> None:
    plan = build_plan(make_inputs())
    orgs = [
        r for r in plan.table("evidence.assertion").rows if r.columns["kind"] == "organization_name"
    ]
    assert orgs, "the fixture records an institution_name"
    assert {r.columns["resolution"] for r in orgs} == {"unresolved"}
    assert "crm.organization" not in plan.tables


# --------------------------------------------------------------------------- #
# Supplier / provider safety — the Ohaus failure mode
# --------------------------------------------------------------------------- #


def test_a_supplier_contact_reached_by_a_campaign_is_classified_as_supplier() -> None:
    plan = build_plan(make_inputs())
    row = next(
        r for r in controls(plan, "prior_contact") if r.columns["value_norm"] == SUPPLIER_CONTACT
    )
    assert row.columns["_classification"] == "supplier"
    assert plan.counts["supplier_provider_contacts"] >= 1


def test_supplier_classification_is_by_domain_not_by_a_hard_coded_address() -> None:
    index = build_supplier_index(make_inputs())
    # Any address under the recorded supplier domain classifies, not just the one seen.
    assert index.classify("alguien.nuevo@proveedor.invalid") == "supplier"
    assert index.classify("sub@mail.proveedor.invalid") == "supplier"
    assert index.classify("cliente@example.invalid") is None


def test_a_supplier_contact_is_never_classified_as_prospect_or_customer() -> None:
    plan = build_plan(make_inputs())
    assert plan.counts["prospect_or_customer_classified_contacts"] == 0
    supplier_rows = [
        r for r in controls(plan, "prior_contact") if r.columns["_classification"] == "supplier"
    ]
    assert supplier_rows
    for row in supplier_rows:
        # A supplier's prior-contact row is an outreach fact, never a lifecycle claim.
        assert row.columns["kind"] == "prior_contact"
        assert row.columns["purpose"] == "marketing"


def test_provider_only_contacts_are_refused_marketing_eligibility_by_the_canonical_gate() -> None:
    """The import records the role; `outbound_v2.eligibility` owns the refusal.

    This proves the two agree, and that the refusal is generic — driven by the supplier
    domain set the import indexes, not by any address literal.
    """
    from datetime import UTC, datetime

    from origenlab_email_pipeline.outbound_v2.eligibility import (
        CampaignPolicy,
        ContactControlIndex,
        RecipientCandidate,
        evaluate_recipient_eligibility,
    )
    from origenlab_email_pipeline.outbound_v2.reasons import REASON_POLICY_SUPPLIER

    index = build_supplier_index(make_inputs())
    verdict = evaluate_recipient_eligibility(
        candidate=RecipientCandidate(address_norm=SUPPLIER_CONTACT),
        controls=ContactControlIndex(),
        policy=CampaignPolicy(
            max_sends=1, recontact_interval_days=30, supplier_domains=index.domains
        ),
        now=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert not verdict.eligible
    assert REASON_POLICY_SUPPLIER in verdict.reasons


def test_supplier_candidates_load_as_pending_evidence_never_as_organizations() -> None:
    plan = build_plan(make_inputs())
    candidates = [
        r for r in plan.table("evidence.assertion").rows if r.columns["kind"] == "supplier_candidate"
    ]
    assert candidates
    assert {r.columns["resolution"] for r in candidates} == {"unresolved"}


# --------------------------------------------------------------------------- #
# Outreach facts versus consent, and suppression handling
# --------------------------------------------------------------------------- #


def test_every_prior_contact_row_is_marketing_scoped_so_it_never_stops_a_transactional_send() -> None:
    plan = build_plan(make_inputs())
    assert {r.columns["purpose"] for r in controls(plan, "prior_contact")} == {"marketing"}


def test_an_accepted_delivery_never_becomes_consent() -> None:
    plan = build_plan(make_inputs())
    for row in controls(plan, "prior_contact"):
        assert "consent" not in row.columns
        assert row.columns["kind"] == "prior_contact"


def test_suppressions_are_a_separate_control_class_from_prior_contact() -> None:
    plan = build_plan(make_inputs())
    prior = {r.columns["value_norm"] for r in controls(plan, "prior_contact")}
    blocks = {r.columns["value_norm"] for r in controls(plan, "block")}
    assert prior & blocks == set(), "a suppression never enters the prior-contact union"


def test_a_classifiable_bounce_code_maps_to_purpose_all_without_review() -> None:
    plan = build_plan(make_inputs())
    row = next(r for r in controls(plan, "block") if r.columns["value_norm"] == SUPPRESSED_1A[0])
    assert row.columns["purpose"] == "all"
    assert row.columns["needs_review"] is False


def test_an_unclassifiable_reason_fails_closed_to_all_and_is_flagged() -> None:
    inputs = make_inputs()
    inputs.wave1a["contact_email_suppression"] = [
        {"email": SUPPRESSED_1A[0], "suppression_reason_code": "something_new"},
        {"email": SUPPRESSED_1A[1], "suppression_reason_code": None},
    ]
    plan = build_plan(inputs)
    flagged = [r for r in controls(plan, "block") if r.columns["needs_review"]]
    assert len(flagged) == 2
    assert {r.columns["purpose"] for r in flagged} == {"all"}, "never silently 'marketing'"


def test_a_manual_hard_block_is_folded_in_and_counted_separately() -> None:
    inputs = make_inputs(manual_1a=[{"email_norm": "held@example.invalid", "status": "hold"}])
    plan = build_plan(inputs)
    assert plan.counts["manual_hard_blocks_folded_in"] == 1
    assert plan.counts["address_blocks_total"] == plan.counts["input_suppression_union"] + 1


def test_a_manual_status_is_counted_apart_from_a_suppression_in_every_wave() -> None:
    """A suppression row and a manual hard-block status are different V1 control classes.

    Calling the manual status a suppression would make the Wave 1B input read as 333
    suppressions when the suppression table holds 332 (docs/DATA.md §7.5.4, §7.6.5). Each
    wave's two classes are therefore counted apart, and the per-wave input total is their
    union, not their sum.
    """
    plan = build_plan(make_inputs())
    counts = plan.counts
    for wave in ("wave1a", "wave1b"):
        suppressions = counts[f"{wave}_suppression_rows"]
        manual = counts[f"{wave}_manual_hard_block_statuses"]
        inputs_total = counts[f"{wave}_address_block_inputs"]
        assert suppressions + manual >= inputs_total, "the union never exceeds the sum"
        assert inputs_total >= suppressions, "a suppression is always an address-block input"

    # The suppression union reconciles to the suppression *tables* alone; the manual
    # statuses are additional address-block inputs on top of it.
    assert counts["input_suppression_union"] == len({*SUPPRESSED_1A, *SUPPRESSED_1B})
    assert counts["address_blocks_total"] == (
        counts["input_suppression_union"] + counts["manual_hard_blocks_folded_in"]
    )


def test_an_active_manual_status_is_not_consent_and_creates_no_row() -> None:
    inputs = make_inputs(manual_1a=[{"email_norm": "active@example.invalid", "status": "active"}])
    plan = build_plan(inputs)
    assert "active@example.invalid" not in {r.columns["value_norm"] for r in controls(plan, "block")}


def test_domain_blocks_stay_marketing_scoped() -> None:
    plan = build_plan(make_inputs())
    domain_rows = controls(plan, "block", scope="domain")
    assert len(domain_rows) == len(DOMAINS_1A)
    assert {r.columns["purpose"] for r in domain_rows} == {"marketing"}


# --------------------------------------------------------------------------- #
# Campaign history: membership, delivery, candidate-versus-sent
# --------------------------------------------------------------------------- #


def test_campaign_membership_and_delivery_history_are_planned() -> None:
    plan = build_plan(make_inputs())
    assert plan.counts["campaigns"] == 2
    assert plan.counts["campaign_memberships"] == plan.table("outbound.campaign_recipient").count
    assert plan.counts["accepted_deliveries"] == len(UNION) + len(WAVE1B_COMBINED)


def test_a_candidate_is_snapshotted_and_never_counted_as_sent() -> None:
    plan = build_plan(make_inputs())
    candidates = [
        r for r in plan.table("outbound.campaign_recipient").rows if r.columns["_candidate_only"]
    ]
    assert len(candidates) == 1
    assert {r.columns["state"] for r in candidates} == {"snapshotted"}
    # A candidate has no attempt, so it can never appear as an accepted delivery.
    candidate_addresses = {r.columns["address_norm"] for r in candidates}
    attempt_addresses = {r.columns["address_norm"] for r in plan.table("outbound.send_attempt").rows}
    assert candidate_addresses & attempt_addresses == set()


def test_a_candidate_never_becomes_a_prior_contact() -> None:
    plan = build_plan(make_inputs())
    prior = {r.columns["value_norm"] for r in controls(plan, "prior_contact")}
    assert "cand-1@example.invalid" not in prior


def test_a_failed_attempt_is_rejected_not_accepted() -> None:
    inputs = make_inputs(
        attempts_1a=[
            {"campaign_id": CAMPAIGN_1A, "email_norm": UNION[0], "id": "x", "result": "failed"}
        ]
    )
    plan = build_plan(inputs)
    row = next(r for r in plan.table("outbound.send_attempt").rows if r.columns["v1_attempt_id"] == "x")
    assert row.columns["submission_state"] == "rejected"
    assert row.columns["delivery_state"] == "n/a"
    assert row.columns["error_class"] == "permanent"


def test_a_migrated_attempt_carries_no_minted_rfc822_id() -> None:
    plan = build_plan(make_inputs())
    assert {r.columns["rfc822_message_id"] for r in plan.table("outbound.send_attempt").rows} == {None}


def test_every_v1_campaign_loads_as_archived() -> None:
    plan = build_plan(make_inputs())
    assert {r.columns["status"] for r in plan.table("outbound.campaign").rows} == {"archived"}


def test_an_unmapped_recipient_state_is_rejected_not_guessed() -> None:
    inputs = make_inputs(
        recipients_1a=[{"campaign_id": CAMPAIGN_1A, "email_norm": UNION[0], "state": "wat"}]
    )
    plan = build_plan(inputs)
    assert any("unmapped V1 recipient state" in r.reason for r in plan.rejects)


def test_an_accepted_attempt_with_no_timestamp_is_rejected_not_dated_by_guesswork() -> None:
    """`send_attempt_accepted_shape` requires accepted_at, and V1 cannot always supply it.

    Inventing an instant would put a fabricated date in the send ledger, so the row is
    rejected and stays visible in the private reject artifact instead.
    """
    inputs = make_inputs(
        attempts_1a=[
            {"campaign_id": CAMPAIGN_1A, "email_norm": UNION[0], "id": "n", "result": "accepted"}
        ]
    )
    plan = build_plan(inputs)
    wave1a = [
        r
        for r in plan.table("outbound.send_attempt").rows
        if r.columns["v1_campaign_id"] == CAMPAIGN_1A
    ]
    assert wave1a == [], "the undateable Wave 1A attempt is not planned"
    assert any("cannot be dated" in r.reason for r in plan.rejects)


def test_a_failed_attempt_needs_no_timestamp() -> None:
    """Only the accepted shape requires an instant; a rejection may be undated."""
    inputs = make_inputs(
        attempts_1a=[
            {"campaign_id": CAMPAIGN_1A, "email_norm": UNION[0], "id": "f", "result": "failed"}
        ]
    )
    plan = build_plan(inputs)
    row = next(r for r in plan.table("outbound.send_attempt").rows if r.columns["v1_attempt_id"] == "f")
    assert row.columns["submission_state"] == "rejected"
    assert row.columns["accepted_at"] is None


# --------------------------------------------------------------------------- #
# Conflicting evidence stays visible
# --------------------------------------------------------------------------- #


def test_an_address_in_both_waves_yields_one_row_that_records_both() -> None:
    plan = build_plan(make_inputs())
    overlapping = CROSS_WAVE_INTERSECTION[0]
    rows = [r for r in controls(plan, "prior_contact") if r.columns["value_norm"] == overlapping]
    assert len(rows) == 1, "deduplicated, not doubled"
    assertion = next(
        r
        for r in plan.table("evidence.assertion").rows
        if r.columns["kind"] == "contacted_address" and r.columns["value_norm"] == overlapping
    )
    assert json.loads(assertion.columns["value"])["waves"] == ["wave1a", "wave1b"]


def test_an_address_that_is_both_contacted_and_suppressed_keeps_both_facts() -> None:
    inputs = make_inputs(suppressed_1a=[UNION[0], *SUPPRESSED_1A])
    plan = build_plan(inputs)
    kinds = {
        r.columns["kind"]
        for r in plan.table("outbound.contact_control").rows
        if r.columns["value_norm"] == UNION[0]
    }
    assert kinds == {"prior_contact", "block"}, "neither fact silently wins"


def test_conflicts_are_counted_and_reported() -> None:
    plan = build_plan(make_inputs())
    assert plan.counts["conflicts"] == len(CROSS_WAVE_INTERSECTION)


# --------------------------------------------------------------------------- #
# Aggregate reconciliation fails closed
# --------------------------------------------------------------------------- #


def test_the_plan_reconciles_against_the_measured_report() -> None:
    plan = build_plan(make_inputs())
    assert plan.counts["input_prior_contact_union"] == len(CROSS_WAVE_UNION)
    assert plan.counts["input_suppression_union"] == len({*SUPPRESSED_1A, *SUPPRESSED_1B})
    assert plan.counts["input_domain_suppression_union"] == len(DOMAINS_1A)


def test_a_union_that_disagrees_with_the_measured_report_is_refused() -> None:
    inputs = make_inputs()
    inputs.reconciliation["counts"]["cross_wave_safety_union"] = 999
    with pytest.raises(MappingRefused, match="fails closed"):
        build_plan(inputs)


def test_a_report_without_the_measured_counts_is_refused() -> None:
    inputs = make_inputs()
    inputs.reconciliation["counts"].pop("cross_wave_safety_union")
    with pytest.raises(MappingRefused, match="carries no"):
        build_plan(inputs)


def test_a_suppression_union_mismatch_is_refused() -> None:
    inputs = make_inputs()
    inputs.reconciliation["address_suppression"]["deduplicated_union"] = 42
    with pytest.raises(MappingRefused):
        build_plan(inputs)


def test_every_output_class_reconciles_to_a_source_total() -> None:
    plan = build_plan(make_inputs())
    # A prior-contact row and its assertion describe the same address once each: they are
    # two relational representations of one fact, never two contacts.
    contacted = [
        r for r in plan.table("evidence.assertion").rows if r.columns["kind"] == "contacted_address"
    ]
    assert len(contacted) == plan.counts["input_prior_contact_union"]
    assert plan.counts["unclassified_contacts"] + plan.counts["supplier_provider_contacts"] == (
        plan.counts["input_prior_contact_union"]
    )


# --------------------------------------------------------------------------- #
# Structural gaps — both closed by migration 20260920190000
# --------------------------------------------------------------------------- #


def test_no_structural_gap_remains_open() -> None:
    assert STRUCTURAL_GAPS == ()


def test_wave1b_rows_now_carry_their_own_source_labels() -> None:
    """DATA.md §7.5.1: provenance stays separable after the load."""
    plan = build_plan(make_inputs())
    wave1b = [
        r
        for r in plan.table("outbound.contact_control").rows
        if r.columns["source"] in {"wave1b_prior_contact", "wave1b_block"}
    ]
    assert wave1b, "the fixture has Wave 1B-only addresses"
    assert all(r.columns["_blocked_by"] is None for r in wave1b)
    assert {"wave1b_prior_contact", "wave1b_block"} <= IMPLEMENTED_CONTACT_CONTROL_SOURCES


def test_a_wave1a_row_never_borrows_a_wave1b_label_or_the_reverse() -> None:
    plan = build_plan(make_inputs())
    for row in controls(plan, "prior_contact"):
        source = row.columns["source"]
        in_wave1a = row.columns["value_norm"] in {
            a for v in [*UNION, *ADDENDUM] if (a := canonical_address(v, origin="t"))
        }
        assert source.startswith("wave1a_") == in_wave1a


def test_every_planned_source_label_is_one_the_database_accepts() -> None:
    plan = build_plan(make_inputs())
    sources = {r.columns["source"] for r in plan.table("outbound.contact_control").rows}
    assert sources <= IMPLEMENTED_CONTACT_CONTROL_SOURCES


def test_campaign_tables_are_no_longer_blocked() -> None:
    plan = build_plan(make_inputs())
    for name in (
        "comms.mailbox",
        "outbound.campaign",
        "outbound.campaign_recipient",
        "outbound.send_attempt",
    ):
        assert plan.table(name).blocked_by is None


def test_an_archived_campaign_still_invents_no_recontact_interval() -> None:
    """The carve-out makes the column optional; it does not license a made-up value."""
    plan = build_plan(make_inputs())
    campaigns = plan.table("outbound.campaign").rows
    assert campaigns
    assert {r.columns["recontact_interval_days"] for r in campaigns} == {None}
    assert {r.columns["status"] for r in campaigns} == {"archived"}


def test_the_closed_gaps_keep_their_reasoning() -> None:
    """A gap that was closed keeps its argument, so the decision is not lost with it."""
    assert CLOSED_STRUCTURAL_GAPS
    for gap in CLOSED_STRUCTURAL_GAPS:
        assert gap.requirement and gap.blocked_rows and gap.why_not_representable
        assert len(gap.why_not_representable) > 80, "a gap must be argued, not asserted"


def test_the_importer_vocabulary_matches_the_migration() -> None:
    """A label the database would reject must never reach a plan.

    The migration file is the source of truth; this reads it rather than restating it.
    """
    migration = (
        Path(__file__).resolve().parents[3]
        / "supabase/migrations"
        / "20260920190000_slice0_wave1b_source_labels_and_archived_recontact_interval.sql"
    )
    import re as _re

    text = migration.read_text(encoding="utf-8")
    block = text[text.index("add constraint contact_control_source_check") :]
    block = block[: block.index("));")]
    in_migration = set(_re.findall(r"'([a-z0-9_]+)'", block))
    assert in_migration == set(IMPLEMENTED_CONTACT_CONTROL_SOURCES)


# --------------------------------------------------------------------------- #
# The database boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "dsn",
    [
        # Hosted providers, and a plainly remote address.
        "postgresql://u:p@db.abcdefghij.supabase.co:5432/postgres",
        "postgresql://u:p@10.0.0.5:5432/postgres",
        "postgresql://u:p@origenlab-api.render.com:5432/postgres",
        "postgresql://u:p@example.com:5432/postgres",
        # libpq reads URI query parameters as connection keywords, so each of these has a
        # loopback authority and a remote effective target.
        "postgresql://u:p@127.0.0.1/db?host=203.0.113.10",
        "postgresql://u:p@127.0.0.1/db?hostaddr=203.0.113.10",
        "postgresql://u:p@127.0.0.1/db?service=remote",
        "postgresql://u:p@127.0.0.1/db?host=db.abcdefghij.supabase.co",
        "postgresql://u:p@127.0.0.1/db?target_session_attrs=any",
        "postgresql://u:p@127.0.0.1/db?",
        # A fragment: a connection URI has none, so its presence means the string is not
        # what it appears to be.
        "postgresql://u:p@127.0.0.1/db#host=203.0.113.10",
        "postgresql://u:p@127.0.0.1/db#",
        # Multi-host: libpq tries each in turn, so only the first could be validated.
        "postgresql://u:p@127.0.0.1,203.0.113.10/db",
        # Unix sockets, in both spellings. Either connects to an unknown cluster.
        "postgresql:///postgres",
        "postgresql://%2Fvar%2Frun%2Fpostgresql/db",
        "postgresql://u:p@%2Ftmp/db",
        # A name, including the loopback name: what this module validates (text) and what
        # libpq resolves (an address, later) are two different things.
        "postgresql://postgres@localhost:5432/v2test",
        "postgresql://postgres@localhost.localdomain:5432/v2test",
        # A key/value conninfo string is not a URI at all.
        "host=203.0.113.10 dbname=postgres",
        "mysql://u:p@127.0.0.1:3306/db",
        "postgresql://u:p@127.0.0.1:5432/",
        "",
    ],
)
def test_a_non_local_or_malformed_target_is_refused(dsn: str) -> None:
    with pytest.raises(TargetRefused):
        assert_local_target(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        "postgresql://postgres@127.0.0.1/v2test",
        "postgresql://postgres@127.1.2.3:5432/v2test",
        "postgres://postgres@[::1]:5432/v2test",
    ],
)
def test_a_literal_loopback_target_is_accepted(dsn: str) -> None:
    target = assert_local_target(dsn)
    assert target.database


def test_the_dsn_handed_to_the_driver_is_rebuilt_from_what_was_validated() -> None:
    """The original string is never passed through, so it can carry no unchecked keyword."""
    target = assert_local_target("POSTGRESQL://postgres:pw@127.0.0.1:54322/postgres")
    assert target.dsn == "postgresql://postgres:pw@127.0.0.1:54322/postgres"
    assert "?" not in target.dsn and "#" not in target.dsn


def test_an_ipv6_loopback_target_keeps_its_bracketed_literal() -> None:
    target = assert_local_target("postgres://postgres@[::1]:5432/v2test")
    assert target.dsn == "postgres://postgres@[::1]:5432/v2test"
    assert target.host == "::1"


def test_the_refusal_names_the_routing_mechanism_it_refused() -> None:
    """A refusal has to be actionable: it says *why*, not only *no*."""
    with pytest.raises(TargetRefused, match="query string"):
        assert_local_target("postgresql://u:p@127.0.0.1/db?host=203.0.113.10")
    with pytest.raises(TargetRefused, match="fragment"):
        assert_local_target("postgresql://u:p@127.0.0.1/db#x")
    with pytest.raises(TargetRefused, match="more than one host"):
        assert_local_target("postgresql://u:p@127.0.0.1,203.0.113.10/db")
    with pytest.raises(TargetRefused, match="name, not an IP address"):
        assert_local_target("postgresql://u:p@localhost/db")


def test_the_libpq_routing_environment_is_removed_for_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PGHOSTADDR` redirects a connection whose URI names a loopback host.

    Validating the DSN is therefore only half the boundary; the other half is that no `PG*`
    variable survives into the connection.
    """
    from origenlab_email_pipeline.migration.v2_import.target import (
        LIBPQ_ROUTING_ENVIRONMENT,
        neutralized_libpq_environment,
    )

    assert {"PGHOSTADDR", "PGHOST", "PGSERVICE"} <= set(LIBPQ_ROUTING_ENVIRONMENT)
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.10")
    monkeypatch.setenv("PGSERVICE", "remote")
    with neutralized_libpq_environment() as removed:
        assert "PGHOSTADDR" not in os.environ
        assert "PGSERVICE" not in os.environ
        assert "PGHOSTADDR" in removed and "203.0.113.10" not in " ".join(removed)
    assert os.environ["PGHOSTADDR"] == "203.0.113.10"


def test_the_apply_path_neutralizes_the_environment_before_it_connects() -> None:
    """The guard is wired in, not merely available."""
    from origenlab_email_pipeline.migration.v2_import import apply as apply_module

    source = Path(apply_module.__file__).read_text(encoding="utf-8")
    assert "neutralized_libpq_environment()" in source


def test_a_redacted_target_carries_no_credential() -> None:
    target = assert_local_target("postgresql://user:secret@127.0.0.1:54322/postgres")
    assert "secret" not in target.redacted()
    assert "user" not in target.redacted()


def test_there_is_no_flag_that_widens_the_target_boundary() -> None:
    from origenlab_email_pipeline.migration.v2_import import target as target_module

    source = Path(target_module.__file__).read_text(encoding="utf-8")
    for token in ("--authorize", "allow_hosted", "allow_remote", "skip_check", "force="):
        assert token not in source, f"{token} would be an override of the local-only boundary"


# --------------------------------------------------------------------------- #
# Reporting: PII-safe aggregates, private rejects
# --------------------------------------------------------------------------- #


def test_the_aggregate_report_carries_no_address() -> None:
    plan = build_plan(make_inputs())
    report = build_aggregate_report(plan, mode="dry-run", target=None)
    blob = json.dumps(report)
    for address in [*CROSS_WAVE_UNION, *SUPPRESSED_1A, SUPPLIER_CONTACT]:
        assert address not in blob


def test_a_report_carrying_an_address_is_refused() -> None:
    with pytest.raises(ReportRefused, match="address-shaped"):
        assert_report_is_pii_safe({"counts": {}, "leak": "someone@example.invalid"})


def test_a_report_carrying_an_address_in_a_nested_list_is_refused() -> None:
    with pytest.raises(ReportRefused):
        assert_report_is_pii_safe({"rows": [{"deep": ["x", "a@b.invalid"]}]})


def test_the_console_rendering_carries_no_address() -> None:
    plan = build_plan(make_inputs())
    text = render_console(build_aggregate_report(plan, mode="dry-run", target=None))
    for address in [*CROSS_WAVE_UNION, SUPPLIER_CONTACT]:
        assert address not in text


def test_the_report_records_the_gaps_and_the_invariants() -> None:
    plan = build_plan(make_inputs())
    report = build_aggregate_report(plan, mode="dry-run", target=None)
    assert report["structural_gaps"] == [], "both gaps are closed"
    assert report["invariants"]["crm_rows_planned"] == 0
    assert report["invariants"]["no_person_created_from_an_address"] is True


def test_rejects_are_summarised_by_reason_never_by_address() -> None:
    inputs = make_inputs(union=[*UNION, "not-an-address"])
    plan = build_plan(inputs)
    inputs.reconciliation["counts"]["cross_wave_safety_union"] = plan.counts[
        "input_prior_contact_union"
    ]
    report = build_aggregate_report(plan, mode="dry-run", target=None)
    assert report["rejects"]["total"] >= 1
    assert "not-an-address" not in json.dumps(report["rejects"])


def test_the_reject_artifact_is_written_private_and_outside_the_report(tmp_path: Path) -> None:
    inputs = make_inputs(union=[*UNION, "not-an-address"])
    plan = build_plan(inputs)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    written = write_reject_artifact(plan, root, stamp="20260920T000000Z")
    assert written is not None
    assert stat.S_IMODE(written.lstat().st_mode) == 0o600
    assert "not-an-address" in written.read_text(encoding="utf-8")


def test_no_reject_artifact_is_written_when_there_is_nothing_to_report(tmp_path: Path) -> None:
    plan = build_plan(make_inputs())
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    assert write_reject_artifact(plan, root) is None
    assert list(root.iterdir()) == []


def test_a_world_writable_reject_destination_is_refused(tmp_path: Path) -> None:
    inputs = make_inputs(union=[*UNION, "not-an-address"])
    plan = build_plan(inputs)
    root = tmp_path / "loose"
    root.mkdir()
    root.chmod(0o777)  # mkdir(mode=...) is masked by umask; chmod is not
    with pytest.raises(ReportRefused):
        write_reject_artifact(plan, root)


# --------------------------------------------------------------------------- #
# No network, no hosted service, no Gmail
# --------------------------------------------------------------------------- #


def test_the_import_package_imports_no_network_or_hosted_client() -> None:
    package = Path(
        __import__(
            "origenlab_email_pipeline.migration.v2_import", fromlist=["__file__"]
        ).__file__
    ).parent
    forbidden = (
        "googleapiclient",
        "google.oauth2",
        "google_auth",
        "requests",
        "httpx",
        "urllib.request",
        "socket",
        "supabase",
        "boto3",
    )
    for module in sorted(package.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        for token in forbidden:
            assert f"import {token}" not in source, f"{module.name} imports {token}"


def test_the_importer_opens_no_gmail_and_changes_no_send_state() -> None:
    package = Path(
        __import__(
            "origenlab_email_pipeline.migration.v2_import", fromlist=["__file__"]
        ).__file__
    ).parent
    for module in sorted(package.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "send_control" not in source, f"{module.name} touches the send flags"
        assert "gmail" not in source.lower().replace("'gmail'", "").replace('"gmail"', ""), (
            f"{module.name} references Gmail beyond the mailbox provider literal"
        )


def test_the_apply_path_writes_no_crm_table() -> None:
    from origenlab_email_pipeline.migration.v2_import.apply import WRITABLE_TABLES

    assert not any(name.startswith("crm.") for name in WRITABLE_TABLES)


def test_the_apply_path_contains_no_destructive_statement() -> None:
    """Every SQL string in the apply path must be a read or an insert.

    The module docstring is excluded, because it *describes* the statements the path
    refuses to execute; the check is about executable SQL, not prose.
    """
    import ast

    from origenlab_email_pipeline.migration.v2_import import apply as apply_module

    tree = ast.parse(Path(apply_module.__file__).read_text(encoding="utf-8"))
    sql = [
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(verb in node.value.lower() for verb in ("select ", "insert ", "alter ", "update "))
    ]
    assert sql, "the apply path should contain SQL to check"
    for statement in sql:
        if statement == ast.get_docstring(tree, clean=False).lower():  # type: ignore[union-attr]
            continue
        for forbidden in ("truncate", "drop table", "delete from", "update "):
            assert forbidden not in statement, f"the apply path must never {forbidden.strip()}"


# --------------------------------------------------------------------------- #
# Artifact verification
# --------------------------------------------------------------------------- #


def _write_bundle(root: Path, name: str, files: dict[str, Any], *, gzipped: bool) -> Path:
    """Write a synthetic bundle with a valid SHA256SUMS and an archive sidecar."""
    bundle = root / name
    bundle.mkdir(mode=0o700, parents=True)
    written: list[str] = []
    for rel, rows in files.items():
        target = bundle / rel
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()
        if rel.endswith(".gz"):
            payload = gzip.compress(payload, mtime=0)
        target.write_bytes(payload)
        target.chmod(0o600)
        written.append(rel)
    return bundle


def _finalize(bundle: Path, manifest: dict[str, Any]) -> None:
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    (bundle / "manifest.json").chmod(0o600)
    lines = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(bundle).as_posix()}")
    sums = bundle / "SHA256SUMS"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sums.chmod(0o600)


def _archive(bundle: Path) -> tuple[Path, str]:
    archive = bundle.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname=bundle.name)
    archive.chmod(0o600)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar = Path(str(archive) + ".sha256")
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    sidecar.chmod(0o600)
    return archive, digest


@pytest.fixture
def synthetic_root(tmp_path: Path) -> dict[str, Any]:
    """A complete, internally consistent synthetic artifact set."""
    root = tmp_path / "migration"
    root.mkdir(mode=0o700)

    a = _write_bundle(
        root,
        "wave1a",
        {
            "derived/recipient_ledger.jsonl.gz": [
                {"email_norm": x, "in_contacted_union": True} for x in UNION
            ],
            "exact/contact_email_suppression.jsonl.gz": [
                {"email": x, "suppression_reason_code": "bounce_other"} for x in SUPPRESSED_1A
            ],
            "exact/contact_domain_suppression.jsonl.gz": [{"domain_norm": d} for d in DOMAINS_1A],
            "exact/manual_contact_status.jsonl.gz": [],
            "exact/outbound_campaign.jsonl.gz": [],
            "exact/outbound_campaign_recipient.jsonl.gz": [],
            "exact/outbound_send_attempt.jsonl.gz": [],
            "exact/outreach_contact_state.jsonl.gz": [],
            "exact/supplier_master.jsonl.gz": [],
            "exact/supplier_contact_channel.jsonl.gz": [],
        },
        gzipped=True,
    )
    _finalize(a, {"bundle": "wave1a"})
    a_archive, a_digest = _archive(a)

    b = _write_bundle(
        root,
        "wave1b",
        {
            "delta/combined_prior_contact.jsonl": [
                {"address": x, "source_categories": ["campaign_accepted"]} for x in WAVE1B_NEW
            ],
            "delta/campaign_prior_contact.jsonl": [],
            "delta/sent_history_prior_contact.jsonl": [],
            "delta/outreach_prior_contact.jsonl": [],
            "delta/contact_email_suppression.jsonl": [],
            "delta/contact_domain_suppression.jsonl": [],
            "delta/manual_contact_status.jsonl": [],
            "exact/outbound_campaign.jsonl": [],
            "exact/outbound_campaign_recipient.jsonl": [],
            "exact/outbound_send_attempt.jsonl": [],
            "derived/remaining_candidates.jsonl": [],
        },
        gzipped=False,
    )
    _finalize(b, {"bundle": "wave1b", "content_digest_sha256": "0" * 64})
    b_archive, b_digest = _archive(b)

    addendum = root / "addendum.jsonl"
    addendum.write_text(
        "".join(json.dumps({"address": x}) + "\n" for x in ADDENDUM), encoding="utf-8"
    )
    addendum.chmod(0o600)
    add_digest = hashlib.sha256(addendum.read_bytes()).hexdigest()
    Path(str(addendum) + ".sha256").write_text(
        f"{add_digest}  {addendum.name}\n", encoding="utf-8"
    )

    recon = root / "recon.json"
    recon.write_text(
        json.dumps(
            {
                "counts": {
                    "cross_wave_safety_union": len({*UNION, *ADDENDUM, *WAVE1B_NEW}),
                    "cross_wave_intersection": 0,
                },
                "address_suppression": {"deduplicated_union": len(SUPPRESSED_1A)},
                "domain_suppression": {"deduplicated_union": len(DOMAINS_1A)},
            }
        ),
        encoding="utf-8",
    )
    recon.chmod(0o600)
    recon_digest = hashlib.sha256(recon.read_bytes()).hexdigest()
    Path(str(recon) + ".sha256").write_text(f"{recon_digest}  {recon.name}\n", encoding="utf-8")

    manifest_sha = hashlib.sha256((a / "manifest.json").read_bytes()).hexdigest()
    b_manifest_sha = hashlib.sha256((b / "manifest.json").read_bytes()).hexdigest()

    return {
        "root": root,
        "kwargs": {
            "wave1a_bundle": a,
            "wave1a_archive": a_archive,
            "wave1a_addendum": addendum,
            "wave1b_bundle": b,
            "wave1b_archive": b_archive,
            "reconciliation": recon,
        },
        "expected": ExpectedHashes(
            wave1a_archive=a_digest,
            wave1a_manifest=manifest_sha,
            wave1b_archive=b_digest,
            wave1b_manifest=b_manifest_sha,
            wave1b_content_digest="0" * 64,
            reconciliation=recon_digest,
        ),
    }


def test_a_consistent_artifact_set_loads(synthetic_root: dict[str, Any]) -> None:
    inputs = load_inputs(**synthetic_root["kwargs"], expected=synthetic_root["expected"])
    assert len(inputs.wave1a["recipient_ledger"]) == len(UNION)
    assert len(inputs.identities) == 4


def test_a_tampered_bundle_file_is_refused(synthetic_root: dict[str, Any]) -> None:
    ledger = synthetic_root["kwargs"]["wave1a_bundle"] / "derived/recipient_ledger.jsonl.gz"
    ledger.write_bytes(gzip.compress(b'{"email_norm": "x@y.invalid"}\n', mtime=0))
    with pytest.raises(InputRefused, match="SHA256SUMS mismatch"):
        load_inputs(**synthetic_root["kwargs"], expected=synthetic_root["expected"])


def test_an_unlisted_extra_file_is_refused(synthetic_root: dict[str, Any]) -> None:
    (synthetic_root["kwargs"]["wave1a_bundle"] / "derived/extra.jsonl").write_text("{}\n")
    with pytest.raises(InputRefused, match="SHA256SUMS does not list"):
        load_inputs(**synthetic_root["kwargs"], expected=synthetic_root["expected"])


def test_a_recorded_hash_mismatch_is_refused(synthetic_root: dict[str, Any]) -> None:
    wrong = ExpectedHashes(
        **{**synthetic_root["expected"].__dict__, "wave1a_archive": "0" * 64}
    )
    with pytest.raises(InputRefused, match="docs/DATA.md"):
        load_inputs(**synthetic_root["kwargs"], expected=wrong)


def test_a_symlinked_artifact_is_refused(synthetic_root: dict[str, Any], tmp_path: Path) -> None:
    link = tmp_path / "linked.json"
    link.symlink_to(synthetic_root["kwargs"]["reconciliation"])
    kwargs = {**synthetic_root["kwargs"], "reconciliation": link}
    with pytest.raises(InputRefused, match="symbolic link"):
        load_inputs(**kwargs, expected=synthetic_root["expected"])


def test_a_group_writable_artifact_is_refused(synthetic_root: dict[str, Any]) -> None:
    target = synthetic_root["kwargs"]["wave1a_bundle"] / "manifest.json"
    target.chmod(0o660)
    with pytest.raises(InputRefused, match="group- or world-writable"):
        load_inputs(**synthetic_root["kwargs"], expected=synthetic_root["expected"])


def test_a_missing_required_file_is_refused(synthetic_root: dict[str, Any]) -> None:
    (synthetic_root["kwargs"]["wave1b_bundle"] / "delta/combined_prior_contact.jsonl").unlink()
    with pytest.raises(InputRefused):
        load_inputs(**synthetic_root["kwargs"], expected=synthetic_root["expected"])


def test_a_malformed_jsonl_line_is_refused_not_skipped(synthetic_root: dict[str, Any]) -> None:
    target = synthetic_root["kwargs"]["wave1b_bundle"] / "delta/combined_prior_contact.jsonl"
    target.write_text('{"address": "a@b.invalid"}\nnot json\n', encoding="utf-8")
    _finalize(synthetic_root["kwargs"]["wave1b_bundle"], {"bundle": "wave1b", "content_digest_sha256": "0" * 64})
    archive, digest = _archive(synthetic_root["kwargs"]["wave1b_bundle"])
    expected = ExpectedHashes(
        **{
            **synthetic_root["expected"].__dict__,
            "wave1b_archive": digest,
            "wave1b_manifest": hashlib.sha256(
                (synthetic_root["kwargs"]["wave1b_bundle"] / "manifest.json").read_bytes()
            ).hexdigest(),
        }
    )
    with pytest.raises(InputRefused, match="not valid JSON"):
        load_inputs(**synthetic_root["kwargs"], expected=expected)


# --------------------------------------------------------------------------- #
# Database-backed behaviour
# --------------------------------------------------------------------------- #

_DSN = os.environ.get("ORIGENLAB_V2_TEST_DSN", "")
requires_db = pytest.mark.skipif(
    not _DSN, reason="set ORIGENLAB_V2_TEST_DSN to a disposable local Slice 0 database"
)


@pytest.fixture
def clean_db() -> Any:
    """A connection to the disposable database with the import's tables emptied."""
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(_DSN, autocommit=True)
    with conn.cursor() as cur:
        # The seven tables are owned by origenlab_owner and grant nothing to the CLI's
        # postgres login; a fixture clears them the same way the importer writes them.
        cur.execute("set role origenlab_owner")
        # Child-first, so no foreign key into evidence.source_record survives the clear.
        cur.execute("delete from outbound.send_attempt")
        cur.execute("delete from outbound.campaign_recipient")
        cur.execute("delete from outbound.campaign")
        cur.execute("delete from comms.mailbox")
        cur.execute("delete from evidence.assertion")
        # prior_contact is permanent by trigger — that guard is exactly what production
        # needs and what a disposable fixture must step around to start from empty.
        cur.execute(
            "alter table outbound.contact_control "
            "disable trigger contact_control_prior_contact_permanent"
        )
        cur.execute("delete from outbound.contact_control")
        cur.execute(
            "alter table outbound.contact_control "
            "enable trigger contact_control_prior_contact_permanent"
        )
        cur.execute("delete from evidence.source_record")
    yield conn
    conn.close()


@requires_db
def test_applying_twice_inserts_nothing_the_second_time(clean_db: Any) -> None:
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    plan = build_plan(make_inputs())
    target = assert_local_target(_DSN)

    first = apply_plan(plan, target)
    assert first.total_inserted > 0

    second = apply_plan(plan, target)
    assert second.total_inserted == 0, "a re-run must create zero duplicates"
    assert second.skipped_existing["outbound.contact_control"] == first.inserted[
        "outbound.contact_control"
    ]


@requires_db
def test_an_apply_never_changes_crm(clean_db: Any) -> None:
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    result = apply_plan(build_plan(make_inputs()), assert_local_target(_DSN))
    assert result.crm_rows_after == 0


@requires_db
def test_only_applicable_rows_reach_the_database(clean_db: Any) -> None:
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    apply_plan(build_plan(make_inputs()), assert_local_target(_DSN))
    with clean_db.cursor() as cur:
        cur.execute("select distinct source from outbound.contact_control order by 1")
        sources = {r[0] for r in cur.fetchall()}
    assert sources <= IMPLEMENTED_CONTACT_CONTROL_SOURCES
    assert "wave1b_prior_contact" in sources, "Wave 1B provenance now survives the load"


@requires_db
def test_a_schema_without_the_v2_tables_is_refused(clean_db: Any) -> None:
    from origenlab_email_pipeline.migration.v2_import.apply import (
        ApplyRefused,
        assert_schema_matches,
    )

    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_DSN) as conn, conn.cursor() as cur:
        cur.execute("create schema if not exists probe")
        cur.execute("set search_path = probe")
        conn.rollback()
    # A connection to a database with no V2 schema at all: use a temporary search_path
    # trick is not enough, so assert the positive case instead and prove the negative by
    # renaming the constraint inside a rolled-back transaction.
    with psycopg.connect(_DSN) as conn:
        assert_schema_matches(conn)
        with conn.cursor() as cur:
            cur.execute("set local role origenlab_owner")
            cur.execute(
                "alter table outbound.contact_control rename constraint "
                "contact_control_source_check to contact_control_source_check_tmp"
            )
            with pytest.raises(ApplyRefused, match="schema mismatch"):
                assert_schema_matches(conn)
        conn.rollback()


@requires_db
def test_a_failed_batch_leaves_nothing_behind(clean_db: Any) -> None:
    """A mid-apply failure must roll the whole batch back."""
    from origenlab_email_pipeline.migration.v2_import import apply as apply_module

    plan = build_plan(make_inputs())
    target = assert_local_target(_DSN)

    original = apply_module._insert_contact_controls

    def explode(cur: Any, rows: Any) -> int:
        original(cur, rows[:1])
        raise RuntimeError("injected failure part-way through the batch")

    apply_module._insert_contact_controls = explode  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            apply_module.apply_plan(plan, target)
    finally:
        apply_module._insert_contact_controls = original  # type: ignore[assignment]

    with clean_db.cursor() as cur:
        cur.execute("select count(*) from outbound.contact_control")
        assert cur.fetchone()[0] == 0, "the transaction must have rolled back"
        cur.execute("select count(*) from evidence.source_record")
        assert cur.fetchone()[0] == 0


@requires_db
def test_a_second_apply_creates_no_duplicate_campaign_audience_or_ledger(clean_db: Any) -> None:
    """Regression: `outbound.campaign` has no natural-key unique constraint.

    `ON CONFLICT DO NOTHING` is a no-op there, so a naive apply inserted a *second* campaign
    on every run and, with it, a second copy of the whole audience and send ledger. The
    apply path looks the campaign up on `(mailbox_id, name)` before writing instead.
    """
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    plan = build_plan(make_inputs())
    target = assert_local_target(_DSN)

    first = apply_plan(plan, target)
    assert first.inserted["outbound.campaign"] > 0

    second = apply_plan(plan, target)
    assert second.inserted["outbound.campaign"] == 0
    assert second.inserted["outbound.campaign_recipient"] == 0
    assert second.inserted["outbound.send_attempt"] == 0
    assert second.total_inserted == 0

    with clean_db.cursor() as cur:
        for table, expected in (
            ("outbound.campaign", first.inserted["outbound.campaign"]),
            ("outbound.campaign_recipient", first.inserted["outbound.campaign_recipient"]),
            ("outbound.send_attempt", first.inserted["outbound.send_attempt"]),
        ):
            cur.execute(f"select count(*) from {table}")  # noqa: S608 - literal table name
            assert cur.fetchone()[0] == expected, f"{table} was duplicated by the second apply"


@requires_db
def test_a_repeat_attempt_for_one_recipient_is_preserved_not_collapsed(clean_db: Any) -> None:
    """Counting per recipient must not merge two genuine V1 attempts into one."""
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    twice = [
        {
            "campaign_id": CAMPAIGN_1A,
            "email_norm": UNION[0],
            "id": "r1",
            "result": "accepted",
            "attempted_at": "2026-01-01T00:00:00Z",
        },
        {"campaign_id": CAMPAIGN_1A, "email_norm": UNION[0], "id": "r2", "result": "failed"},
    ]
    plan = build_plan(make_inputs(attempts_1a=twice))
    target = assert_local_target(_DSN)
    apply_plan(plan, target)

    with clean_db.cursor() as cur:
        # Scoped to the Wave 1A campaign: this address is also in the Wave 1B overlap.
        cur.execute(
            """
            select count(*) from outbound.send_attempt a
              join outbound.campaign c on c.id = a.campaign_id
             where a.address_norm = %s and c.name = 'Wave 1A campaign'
            """,
            (UNION[0],),
        )
        assert cur.fetchone()[0] == 2

    assert apply_plan(plan, target).total_inserted == 0


# --------------------------------------------------------------------------- #
# Provenance-scoped idempotency: an existing row is verified, never assumed
# --------------------------------------------------------------------------- #


def _seed_unrelated_campaign(conn: Any, name: str) -> tuple[str, str]:
    """Create a campaign an operator could plausibly have made, outside the import.

    It shares the mailbox address and the name with an imported campaign and carries **no**
    `origin_source_record_id`, which is exactly the collision the reuse rule must survive.

    Returns:
        ``(campaign_id, mailbox_id)``.
    """
    with conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            insert into comms.mailbox (address_norm, display_name, provider,
                                       is_production_sender, authorization_state)
            values (%s, %s, 'gmail', false, 'unauthorized')
            on conflict (address_norm) do nothing
            """,
            (SENDER, "Wave sender"),
        )
        cur.execute("select id from comms.mailbox where address_norm = %s", (SENDER,))
        mailbox_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into outbound.campaign (name, status, mailbox_id, max_sends,
                                           recontact_interval_days, policy_include_suppliers)
            values (%s, 'draft', %s, 7, 90, true)
            returning id
            """,
            (name, mailbox_id),
        )
        return cur.fetchone()[0], mailbox_id


@requires_db
def test_an_unrelated_campaign_with_the_same_name_is_neither_reused_nor_changed(
    clean_db: Any,
) -> None:
    """Regression: reuse on `(mailbox_id, name)` adopted a campaign the import never made.

    `name` is a business label. An operator may reuse one, and if the import adopts that
    campaign it attaches a whole historical audience and ledger to live operator work. The
    import identity is the migration provenance instead, so the unrelated campaign is
    invisible to it.
    """
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    intruder_id, mailbox_id = _seed_unrelated_campaign(clean_db, "Wave 1A campaign")

    result = apply_plan(build_plan(make_inputs()), assert_local_target(_DSN))
    assert result.inserted["outbound.campaign"] == 2, "the import made its own campaigns"

    with clean_db.cursor() as cur:
        # The unrelated campaign is untouched, field for field.
        cur.execute(
            """
            select name, status, mailbox_id, max_sends, recontact_interval_days,
                   policy_include_suppliers, origin_source_record_id, version
              from outbound.campaign where id = %s
            """,
            (intruder_id,),
        )
        assert cur.fetchone() == (
            "Wave 1A campaign", "draft", mailbox_id, 7, 90, True, None, 1
        )
        # …and it received nothing.
        cur.execute(
            "select count(*) from outbound.campaign_recipient where campaign_id = %s",
            (intruder_id,),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "select count(*) from outbound.send_attempt where campaign_id = %s", (intruder_id,)
        )
        assert cur.fetchone()[0] == 0

    # And the second apply still reconciles to zero with the intruder in place.
    assert apply_plan(build_plan(make_inputs()), assert_local_target(_DSN)).total_inserted == 0


@requires_db
def test_a_campaign_whose_stored_fields_drifted_is_refused_not_reused(clean_db: Any) -> None:
    """An imported campaign that no longer matches the plan is not this plan's campaign."""
    from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused, apply_plan

    plan = build_plan(make_inputs())
    target = assert_local_target(_DSN)
    apply_plan(plan, target)

    with clean_db.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "update outbound.campaign set max_sends = max_sends + 1 "  # noqa: S608 - fixture
            "where name = 'Wave 1A campaign' and origin_source_record_id is not null"
        )
        assert cur.rowcount == 1

    with pytest.raises(ApplyRefused, match="max_sends"):
        apply_plan(plan, target)


@requires_db
def test_an_unrelated_attempt_on_the_recipient_does_not_hide_imported_history(
    clean_db: Any,
) -> None:
    """Regression: counting every attempt under a recipient silently dropped history.

    With one unrelated attempt already present, a count-based reconciliation writes one
    fewer imported attempt than V1 recorded — and reports success. Scoping the multiset to
    `origin_source_record_id` makes the unrelated row invisible instead.
    """
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    twice = [
        {
            "campaign_id": CAMPAIGN_1A,
            "email_norm": UNION[0],
            "id": "r1",
            "result": "accepted",
            "attempted_at": "2026-01-01T00:00:00Z",
        },
        {"campaign_id": CAMPAIGN_1A, "email_norm": UNION[0], "id": "r2", "result": "failed"},
    ]
    plan = build_plan(make_inputs(attempts_1a=twice))
    target = assert_local_target(_DSN)

    # Seed the campaign, its audience and one unrelated attempt — no migration origin.
    apply_plan(plan, target)
    with clean_db.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            select r.id, r.campaign_id, c.mailbox_id
              from outbound.campaign_recipient r
              join outbound.campaign c on c.id = r.campaign_id
             where r.address_norm = %s and c.name = 'Wave 1A campaign'
            """,
            (UNION[0],),
        )
        recipient_id, campaign_id, mailbox_id = cur.fetchone()
        cur.execute("delete from outbound.send_attempt where campaign_recipient_id = %s",
                    (recipient_id,))
        cur.execute(
            """
            insert into outbound.send_attempt (purpose, campaign_id, campaign_recipient_id,
                                               mailbox_id, address_norm, submission_state,
                                               delivery_state, accepted_at)
            values ('marketing', %s, %s, %s, %s, 'accepted', 'sent_copy_confirmed',
                    '2030-06-01T00:00:00Z')
            """,
            (campaign_id, recipient_id, mailbox_id, UNION[0]),
        )

    result = apply_plan(plan, target)
    assert result.inserted["outbound.send_attempt"] == 2, "both V1 attempts were rewritten"
    assert result.provenance_notes["send_attempts_with_another_origin_left_untouched"] >= 1

    with clean_db.cursor() as cur:
        cur.execute(
            "select count(*) from outbound.send_attempt "
            "where campaign_recipient_id = %s and origin_source_record_id is null",
            (recipient_id,),
        )
        assert cur.fetchone()[0] == 1, "the unrelated attempt is still there, unchanged"
        cur.execute(
            "select count(*) from outbound.send_attempt "
            "where campaign_recipient_id = %s and origin_source_record_id is not null",
            (recipient_id,),
        )
        assert cur.fetchone()[0] == 2

    assert apply_plan(plan, target).total_inserted == 0


@requires_db
def test_a_partly_present_imported_ledger_is_completed_not_doubled(clean_db: Any) -> None:
    """A run interrupted after one of two attempts must write the missing one only."""
    from origenlab_email_pipeline.migration.v2_import.apply import apply_plan

    twice = [
        {
            "campaign_id": CAMPAIGN_1A,
            "email_norm": UNION[0],
            "id": "r1",
            "result": "accepted",
            "attempted_at": "2026-01-01T00:00:00Z",
        },
        {"campaign_id": CAMPAIGN_1A, "email_norm": UNION[0], "id": "r2", "result": "failed"},
    ]
    plan = build_plan(make_inputs(attempts_1a=twice))
    target = assert_local_target(_DSN)
    apply_plan(plan, target)

    with clean_db.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            delete from outbound.send_attempt
             where id in (
               select a.id from outbound.send_attempt a
                 join outbound.campaign c on c.id = a.campaign_id
                where a.address_norm = %s and c.name = 'Wave 1A campaign'
                  and a.submission_state = 'rejected'
             )
            """,
            (UNION[0],),
        )
        assert cur.rowcount == 1

    result = apply_plan(plan, target)
    assert result.inserted["outbound.send_attempt"] == 1, "only the missing attempt"
    assert apply_plan(plan, target).total_inserted == 0


@requires_db
def test_an_imported_attempt_that_does_not_match_the_plan_is_refused(clean_db: Any) -> None:
    """An attempt under this origin that the plan does not contain stops the run.

    The import completes a ledger; it never reconciles one by removing a row. So a stored
    imported multiset that is not a subset of the plan is a refusal, and nothing is written.
    """
    from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused, apply_plan

    plan = build_plan(make_inputs())
    target = assert_local_target(_DSN)
    apply_plan(plan, target)

    with clean_db.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            update outbound.send_attempt set accepted_at = accepted_at + interval '1 day'
             where id in (
               select a.id from outbound.send_attempt a
                where a.origin_source_record_id is not null
                  and a.submission_state = 'accepted'
                limit 1
             )
            """
        )
        assert cur.rowcount == 1
        cur.execute("select count(*) from outbound.send_attempt")
        before = cur.fetchone()[0]

    with pytest.raises(ApplyRefused, match="send-ledger drift"):
        apply_plan(plan, target)

    with clean_db.cursor() as cur:
        cur.execute("select count(*) from outbound.send_attempt")
        assert cur.fetchone()[0] == before, "a refused run writes nothing"


@requires_db
def test_a_recipient_whose_state_drifted_never_receives_an_attempt(clean_db: Any) -> None:
    """An audience row that disagrees with the plan is refused before any child row."""
    from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused, apply_plan

    plan = build_plan(make_inputs())
    target = assert_local_target(_DSN)
    apply_plan(plan, target)

    with clean_db.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "update outbound.campaign_recipient set state = 'replied' "
            "where address_norm = %s and state = 'sent'",
            (UNION[0],),
        )
        assert cur.rowcount >= 1

    with pytest.raises(ApplyRefused, match="state"):
        apply_plan(plan, target)


@requires_db
def test_a_source_record_whose_payload_drifted_is_refused(clean_db: Any) -> None:
    """A `dedupe_key` that is present but describes another artifact is not idempotency."""
    from origenlab_email_pipeline.migration.v2_import.apply import ApplyRefused, apply_plan

    plan = build_plan(make_inputs())
    target = assert_local_target(_DSN)
    apply_plan(plan, target)

    with clean_db.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "update evidence.source_record set payload_sha256 = repeat('a', 64) "
            "where dedupe_key = %s",
            (plan.anchor_dedupe_key,),
        )
        assert cur.rowcount == 1

    with pytest.raises(ApplyRefused, match="payload_sha256"):
        apply_plan(plan, target)


@requires_db
def test_a_target_without_the_owning_role_refuses_with_a_reason(clean_db: Any) -> None:
    """The seven tables grant nothing to the CLI login; say so once, not per statement."""
    from origenlab_email_pipeline.migration.v2_import.apply import (
        ApplyRefused,
        assume_import_role,
    )

    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_DSN, autocommit=False) as conn:
        assume_import_role(conn)
        conn.rollback()

    class _Refusing:
        """A connection whose `set local role` fails, as a non-member's would."""

        def cursor(self) -> Any:
            raise psycopg.errors.InsufficientPrivilege(
                'permission denied to set role "origenlab_owner"'
            )

    with pytest.raises(ApplyRefused, match="origenlab_owner"):
        assume_import_role(_Refusing())
