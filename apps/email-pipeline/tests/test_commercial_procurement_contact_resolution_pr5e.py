"""PR5E — procurement contact-resolution contract tests."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from origenlab_email_pipeline.candidate_export_gate import GateContext
from origenlab_email_pipeline.commercial_procurement_contact_resolution import (
    planner as contact_planner,
)
from origenlab_email_pipeline.commercial_identity.schema import (
    ensure_commercial_identity_tables,
)
from origenlab_email_pipeline.commercial_procurement.link_routes import (
    build_account_index,
)
from origenlab_email_pipeline.commercial_procurement.sources import (
    disable_require_active_read_transaction,
    enable_require_active_read_transaction,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    CoalescedProcurementTender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.constants import (
    CONTACT_RESOLUTION_DEFERRED,
    FORBIDDEN_CLI_FLAGS,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.contact_search import (
    deferred_summary,
    resolve_contacts_for_tender,
    select_final_status,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.fingerprint import (
    contact_input_fingerprint,
    contact_rules_fingerprint,
    contact_semantic_digest,
    rules_fingerprint_payload,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.frozen_sources import (
    FrozenContactProjection,
    FrozenEvidenceProjection,
    FrozenSourceIndex,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
    ContactCandidate,
    ContactResolutionEvidence,
    ContactResolutionPlanResult,
    ContactResolutionSummary,
    OrganizationResolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.organization import (
    Pr4ProvenanceError,
    _parse_candidate_account_ids,
    assess_buyer_field_sufficiency,
    buyer_domain_candidate,
    resolve_organization_for_tender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.planner import (
    empty_frozen_source_index,
    reconcile_contact_resolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
    classify_role_suitability,
    contact_has_explicit_verification,
    contact_resolution_policy_spec,
    evidence_satisfies_verification_policy,
    next_action_for_status,
    tender_allows_gated_lead_or_research,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    evaluate_contact_safety,
    parse_usable_email,
    safety_snapshot_from_gate_context,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.walkthrough import (
    build_contact_resolution_walkthrough,
)
from origenlab_email_pipeline.commercial_procurement_product_relevance.models import (
    TenderRelevanceDecision,
)


def _tender(
    *,
    tid: str = "t1",
    buyer_display: str | None = "Hospital Demo",
    buyer_source_id: str | None = "hospital.demo.cl",
    pr4_ids: tuple[str, ...] = (),
) -> CoalescedProcurementTender:
    return CoalescedProcurementTender(
        coalesced_tender_id=tid,
        canonical_tender_key="k1",
        identity_namespace="ns",
        tender_key_kind="chilecompra",
        candidate_source_kind="live_snapshot" if not pr4_ids else "both",
        pr4_procurement_id=pr4_ids[0] if pr4_ids else None,
        pr4_procurement_ids=pr4_ids,
        acquisition_snapshot_ids=(),
        acquisition_instance_ids=(),
        acquisition_observation_ids=(),
        coalescence_status="live_only" if not pr4_ids else "exact_agreement",
        source_precedence_reason="only",
        currentness_class="current_authoritative_snapshot",
        lifecycle_class="open",
        closing_soon_bucket="none",
        publication_timestamp_selected=None,
        close_timestamp_selected=None,
        status_code_selected=None,
        status_name_selected=None,
        status_value_selected=None,
        source_status_system_selected=None,
        buyer_display_selected=buyer_display,
        buyer_source_id_selected=buyer_source_id,
        title_selected="title",
        selected_field_provenance={},
        buyer_display_variance=False,
        lifecycle_status_evidence_ref_id=None,
        lifecycle_close_evidence_ref_id=None,
        lifecycle_publication_evidence_ref_id=None,
        lifecycle_evidence_currentness_class=None,
        lifecycle_reason_codes=(),
        evidence_ref_ids=("eref1",),
        conflict_ids=(),
    )


def _relevance(tid: str = "t1", **kwargs) -> TenderRelevanceDecision:
    base = dict(
        decision_id=f"trd_{tid}",
        coalesced_tender_id=tid,
        relevance_class="strong_equipment_class",
        canonical_equipment_classes=("centrifuge",),
        product_resolution_status="equipment_class_only",
        evidence_tier="title_only",
        confidence_band="low",
        positive_reason_codes=(),
        negative_reason_codes=(),
        ambiguity_reason_codes=(),
        aggregation_reason_codes=("single_unit_decision",),
        matched_spans=(),
        contributing_evidence_ref_ids=(),
        unit_decision_ids=(),
        taxonomy_version="t",
        rules_version="r",
        input_fingerprint="i",
        semantic_fingerprint="s",
        lifecycle_class_echo="open",
    )
    base.update(kwargs)
    return TenderRelevanceDecision(**base)


def _empty_gate(**overrides) -> GateContext:
    base = dict(
        sent_recipient_norms=frozenset(),
        suppressed_norms=frozenset(),
        outreach_state_by_email={},
        supplier_domains=frozenset(),
        blocked_domains=frozenset({"origenlab.cl", "labdelivery.cl"}),
        suppressed_contact_domains=frozenset(),
        skip_noise_filter=True,
        skip_supplier_domain_filter=True,
        strict_contact_graph_noise=False,
    )
    base.update(overrides)
    return GateContext(**base)


def _clear_safety(**overrides) -> object:
    return safety_snapshot_from_gate_context(_empty_gate(**overrides))


def _org(
    *,
    tid: str = "t1",
    status: str = "linked",
    account_id: str | None = "acct_a",
    decision_id: str = "trd_t1",
) -> OrganizationResolution:
    return OrganizationResolution(
        organization_resolution_id=f"org_{tid}",
        coalesced_tender_id=tid,
        relevance_decision_id=decision_id,
        resolution_status=status,
        resolution_source="live_link_route",
        account_id=account_id,
        link_route="A_exact_institutional_domain",
        reason_code="ok",
        candidate_account_ids=(),
        evidence_ref_ids=("eref1",),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_and_domain",
        identity_fingerprint="ifp",
    )


def _seed_identity(conn: sqlite3.Connection) -> None:
    ensure_commercial_identity_tables(conn)
    conn.execute(
        """
        INSERT INTO commercial_identity_account(
          account_id, canonical_name, normalized_name, primary_domain,
          identity_confidence, identity_status
        ) VALUES ('acct_a','Hospital Demo','hospital demo','hospital.demo.cl','high','resolved')
        """
    )


def _insert_contact(
    conn: sqlite3.Connection,
    *,
    contact_id: str,
    email: str | None,
    role: str | None,
    identity_status: str = "resolved",
    identity_confidence: str = "high",
) -> None:
    conn.execute(
        """
        INSERT INTO commercial_identity_contact(
          contact_id, normalized_email, display_name, role, account_id,
          account_link_method, identity_confidence, identity_status, email_domain
        ) VALUES (?, ?, ?, ?, 'acct_a', 'exact', ?, ?, 'hospital.demo.cl')
        """,
        (
            contact_id,
            email,
            contact_id,
            role,
            identity_confidence,
            identity_status,
        ),
    )


def _insert_verification_evidence(
    conn: sqlite3.Connection,
    *,
    evidence_id: str,
    contact_id: str,
    evidence_type: str = "contact_identity",
    matching_reason: str = "exact_email",
    source_table: str = "contact_master",
    source_plane: str = "contact_master",
    confidence: str = "high",
) -> None:
    conn.execute(
        """
        INSERT INTO commercial_identity_evidence(
          evidence_id, subject_kind, subject_id, source_table, source_record_id,
          source_plane, origin_plane, evidence_type, evidence_at,
          matching_reason_code, confidence
        ) VALUES (?, 'contact', ?, ?, 'src1', ?, 'business_mart', ?, '2026-01-01T00:00:00Z', ?, ?)
        """,
        (
            evidence_id,
            contact_id,
            source_table,
            source_plane,
            evidence_type,
            matching_reason,
            confidence,
        ),
    )


def test_forbidden_flags_include_label_and_send() -> None:
    assert "--label" in FORBIDDEN_CLI_FLAGS
    assert "--send" in FORBIDDEN_CLI_FLAGS
    assert "--apply" in FORBIDDEN_CLI_FLAGS


def test_shareable_outputs_redact_contact_resolution_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_tender_id = "1234-56-LE26"
    raw_relevance_id = "trd_hospital_demo_real"
    raw_org_id = "org_hospital_demo_real"
    raw_account_id = "acct_hospital_demo_real"
    raw_contact_id = "contact_buyer_real"
    raw_candidate_id = "cand_buyer_real"
    raw_resolution_id = "cr_hospital_demo_real"

    organization = OrganizationResolution(
        organization_resolution_id=raw_org_id,
        coalesced_tender_id=raw_tender_id,
        relevance_decision_id=raw_relevance_id,
        resolution_status="linked",
        resolution_source="live_link_route",
        account_id=raw_account_id,
        link_route="A_exact_institutional_domain",
        reason_code="ok",
        candidate_account_ids=(),
        evidence_ref_ids=("eref_real",),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_and_domain",
        identity_fingerprint="identity_fp",
    )
    summary = ContactResolutionSummary(
        contact_resolution_id=raw_resolution_id,
        coalesced_tender_id=raw_tender_id,
        relevance_decision_id=raw_relevance_id,
        organization_resolution_id=raw_org_id,
        account_id=raw_account_id,
        final_contact_status="existing_verified_contact",
        selected_contact_id=raw_contact_id,
        selected_candidate_id=raw_candidate_id,
        search_stages_completed=("commercial_identity_contact",),
        next_action="use_existing_contact",
        reason_code="verified_suitable_contact_selected",
        considered_contact_count=1,
        suitable_contact_count=1,
        blocked_contact_count=0,
        relevance_class_echo="strong_equipment_class",
        lifecycle_class_echo="open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="validated",
        input_fingerprint="input_fp",
        semantic_fingerprint="semantic_fp",
        rules_version="rules_v1",
        resolver_version="resolver_v1",
    )
    candidate = ContactCandidate(
        candidate_id=raw_candidate_id,
        contact_resolution_id=raw_resolution_id,
        coalesced_tender_id=raw_tender_id,
        account_id=raw_account_id,
        contact_id=raw_contact_id,
        rank=1,
        ranking_tier="verified_suitable_clear",
        role_raw_digest="role_digest",
        role_suitability="suitable_procurement",
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="explicitly_verified",
        evidence_ids=("evidence_real",),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=False,
        safety_unknown=False,
        selectable=True,
        ranking_reason_codes=("verified_suitable_clear",),
    )
    result = ContactResolutionPlanResult(
        as_of_utc="2026-01-01T00:00:00Z",
        run_context="test",
        planner_version="pr5e_test",
        organization_resolutions=(organization,),
        contact_summaries=(summary,),
        contact_candidates=(candidate,),
        evidence=(),
        conflicts=(),
        reconciliation={"ok": True, "equations": {"summaries_match": True}},
        fingerprints={"input": "input_fp", "semantic": "semantic_fp"},
        dependency_fingerprints={"safety": "safety_fp"},
        counts={"contact_summaries": 1, "selected_contacts": 1},
        field_sufficiency_audit={"name_and_domain": 1},
        walkthrough=build_contact_resolution_walkthrough(
            organizations=(organization,),
            summaries=(summary,),
            candidates=(candidate,),
        ),
    )

    synthetic_root = tmp_path / "email-pipeline"
    (synthetic_root / "reports" / "out").mkdir(parents=True)
    fake_planner_file = (
        synthetic_root
        / "src"
        / "origenlab_email_pipeline"
        / "commercial_procurement_contact_resolution"
        / "planner.py"
    )
    fake_planner_file.parent.mkdir(parents=True)
    fake_planner_file.write_text("# synthetic planner path\n", encoding="utf-8")
    monkeypatch.setattr(contact_planner, "__file__", str(fake_planner_file))

    written = contact_planner.write_contact_resolution_outputs(
        result,
        synthetic_root / "reports" / "out" / "pr5e",
        require_git_ignored=False,
    )

    shareable_blob = "\n".join(
        [
            Path(written["summary"]).read_text(encoding="utf-8"),
            Path(written["walkthrough_json"]).read_text(encoding="utf-8"),
            Path(written["walkthrough_md"]).read_text(encoding="utf-8"),
        ]
    )
    for raw in (
        raw_tender_id,
        raw_relevance_id,
        raw_org_id,
        raw_account_id,
        raw_contact_id,
        raw_candidate_id,
        raw_resolution_id,
    ):
        assert raw not in shareable_blob

    walkthrough = json.loads(
        Path(written["walkthrough_json"]).read_text(encoding="utf-8")
    )
    case_b = next(case for case in walkthrough["cases"] if case["case"] == "B")
    assert case_b["source_evidence_redacted"]["coalesced_tender_token"].startswith(
        "tender_"
    )
    assert case_b["source_evidence_redacted"]["relevance_decision_token"].startswith(
        "trd_"
    )
    assert case_b["organization"]["account_token"].startswith("account_")
    assert case_b["selected_contact_token"].startswith("contact_")
    assert case_b["considered_contacts"][0]["contact_token"].startswith("contact_")


def test_buyer_source_id_never_treated_as_account_id() -> None:
    assert buyer_domain_candidate("acct_12345") is None
    assert buyer_domain_candidate("hospital.demo.cl") == "hospital.demo.cl"
    assert buyer_domain_candidate("gmail.com") is None


def test_usable_email_requires_canonical_parse() -> None:
    assert parse_usable_email("buyer@hospital.demo.cl") == "buyer@hospital.demo.cl"
    assert parse_usable_email("not-an-email") is None
    assert parse_usable_email("@@@") is None
    assert parse_usable_email("") is None
    assert parse_usable_email(None) is None


def test_role_suitability_ignores_email_local_part_authority() -> None:
    assert classify_role_suitability("Jefa de Adquisiciones") == "suitable_procurement"
    assert classify_role_suitability("laboratorio") == "suitable_laboratory"
    assert classify_role_suitability("") == "unknown"
    assert classify_role_suitability("estudiante") == "unsuitable"
    assert classify_role_suitability(None) == "unknown"


def test_verification_rejects_non_accepted_evidence_types() -> None:
    bad = {
        "evidence_id": "ev1",
        "subject_kind": "contact",
        "subject_id": "c1",
        "source_table": "fixture",
        "source_record_id": "src1",
        "source_plane": "identity",
        "origin_plane": "identity",
        "evidence_type": "contact_link",
        "evidence_at": "2026-01-01T00:00:00Z",
        "matching_reason_code": "resolved_contact",
        "confidence": "high",
    }
    assert evidence_satisfies_verification_policy(bad, contact_id="c1") is False
    good = {
        **bad,
        "evidence_type": "contact_identity",
        "matching_reason_code": "exact_email",
        "source_table": "contact_master",
        "source_plane": "contact_master",
        "origin_plane": "business_mart",
    }
    assert evidence_satisfies_verification_policy(good, contact_id="c1") is True
    assert (
        contact_has_explicit_verification(
            identity_status="resolved",
            identity_confidence="high",
            evidence_rows=[bad],
            contact_id="c1",
        )
        is False
    )
    assert (
        contact_has_explicit_verification(
            identity_status="resolved",
            identity_confidence="high",
            evidence_rows=[good],
            contact_id="c1",
        )
        is True
    )


def test_policy_fingerprint_moves_on_precedence_change() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution import (
        policy as policy_mod,
    )

    base = contact_rules_fingerprint()
    original = policy_mod.contact_resolution_policy_spec
    mutated = copy.deepcopy(original())
    mutated["status_precedence"] = list(reversed(mutated["status_precedence"]))

    def _mut():
        return mutated

    policy_mod.contact_resolution_policy_spec = _mut  # type: ignore[assignment]
    try:
        assert contact_rules_fingerprint() != base
    finally:
        policy_mod.contact_resolution_policy_spec = original  # type: ignore[assignment]
    assert contact_rules_fingerprint() == base
    assert rules_fingerprint_payload()["policy"] == contact_resolution_policy_spec()


def test_policy_mutations_affect_rules_fingerprint_axes() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution import (
        policy as policy_mod,
    )

    original = policy_mod.contact_resolution_policy_spec
    base = contact_rules_fingerprint()

    def _with(mutator):
        spec = copy.deepcopy(original())
        mutator(spec)

        def _mut():
            return spec

        policy_mod.contact_resolution_policy_spec = _mut  # type: ignore[assignment]
        try:
            return contact_rules_fingerprint()
        finally:
            policy_mod.contact_resolution_policy_spec = original  # type: ignore[assignment]

    assert (
        _with(lambda s: s["ranking_tiers"].__setitem__(0, {**s["ranking_tiers"][0], "rank": 7}))
        != base
    )
    assert (
        _with(
            lambda s: s["verification_policy"].__setitem__(
                "accepted_evidence_types", ["other"]
            )
        )
        != base
    )
    assert (
        _with(
            lambda s: s.__setitem__(
                "material_ambiguity_tiers", ["role_review"]
            )
        )
        != base
    )
    assert (
        _with(
            lambda s: s["actionability_policy"].__setitem__(
                "non_actionable_lifecycle_classes", ["open"]
            )
        )
        != base
    )


def test_deferred_has_no_candidates_or_stages() -> None:
    org = _org(status="unlinked", account_id=None)
    summary = deferred_summary(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class="current_authoritative_snapshot",
    )
    assert summary.final_contact_status == CONTACT_RESOLUTION_DEFERRED
    assert summary.search_stages_completed == ()
    assert summary.considered_contact_count == 0
    assert summary.next_action == "resolve_account"
    assert summary.selected_contact_id is None


def test_organization_pr4_conflict_missing_and_candidate_conflict() -> None:
    index = build_account_index(
        accounts=[
            {
                "account_id": "acct_a",
                "canonical_name_norm": "hospital demo",
                "primary_domain_norm": "hospital.demo.cl",
            }
        ],
        aliases=[],
        domains=[{"account_id": "acct_a", "domain_norm": "hospital.demo.cl"}],
    )
    known = frozenset({"acct_a", "acct_b"})

    org_conflict = resolve_organization_for_tender(
        _tender(pr4_ids=("p1", "p2")),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=known,
        pr4_by_procurement={
            "p1": {
                "resolution_id": "r1",
                "resolution_status": "linked",
                "account_id": "acct_a",
                "link_route": "A_exact_institutional_domain",
                "reason_code": "ok",
                "candidate_account_ids": (),
            },
            "p2": {
                "resolution_id": "r2",
                "resolution_status": "linked",
                "account_id": "acct_b",
                "link_route": "B_exact_canonical_name",
                "reason_code": "ok",
                "candidate_account_ids": (),
            },
        },
        identity_fingerprint="ifp",
    )
    assert org_conflict.resolution_status == "ambiguous"
    assert org_conflict.account_id is None

    org_missing = resolve_organization_for_tender(
        _tender(pr4_ids=("p1", "p_missing")),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=known,
        pr4_by_procurement={
            "p1": {
                "resolution_id": "r1",
                "resolution_status": "linked",
                "account_id": "acct_a",
                "link_route": "A_exact_institutional_domain",
                "reason_code": "ok",
                "candidate_account_ids": (),
            }
        },
        identity_fingerprint="ifp",
    )
    assert org_missing.resolution_source == "pr4_constituent_incomplete"
    assert org_missing.account_id is None

    org_cand = resolve_organization_for_tender(
        _tender(pr4_ids=("p1", "p2")),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=known,
        pr4_by_procurement={
            "p1": {
                "resolution_id": "r1",
                "resolution_status": "linked",
                "account_id": "acct_a",
                "link_route": "A_exact_institutional_domain",
                "reason_code": "ok",
                "candidate_account_ids": (),
            },
            "p2": {
                "resolution_id": "r2",
                "resolution_status": "ambiguous",
                "account_id": None,
                "link_route": None,
                "reason_code": "multi",
                "candidate_account_ids": ("acct_b",),
            },
        },
        identity_fingerprint="ifp",
    )
    assert org_cand.resolution_status == "ambiguous"
    assert "acct_b" in org_cand.candidate_account_ids

    org_multi_route = resolve_organization_for_tender(
        _tender(pr4_ids=("p1", "p2")),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=known,
        pr4_by_procurement={
            "p1": {
                "resolution_id": "r1",
                "resolution_status": "linked",
                "account_id": "acct_a",
                "link_route": "A_exact_institutional_domain",
                "reason_code": "ok",
                "candidate_account_ids": (),
            },
            "p2": {
                "resolution_id": "r2",
                "resolution_status": "linked",
                "account_id": "acct_a",
                "link_route": "B_exact_canonical_name",
                "reason_code": "ok",
                "candidate_account_ids": (),
            },
        },
        identity_fingerprint="ifp",
    )
    assert org_multi_route.resolution_status == "linked"
    assert org_multi_route.account_id == "acct_a"
    assert org_multi_route.link_route == (
        "A_exact_institutional_domain|B_exact_canonical_name"
    )

    org_live = resolve_organization_for_tender(
        _tender(),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=known,
        pr4_by_procurement={},
        identity_fingerprint="ifp",
    )
    assert org_live.resolution_status == "linked"
    assert org_live.account_id == "acct_a"

    org_insuff = resolve_organization_for_tender(
        _tender(buyer_display=None, buyer_source_id=None),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=known,
        pr4_by_procurement={},
        identity_fingerprint="ifp",
    )
    assert org_insuff.resolution_status == "deferred_insufficient_buyer_fields"
    assert (
        assess_buyer_field_sufficiency(_tender(buyer_display=None, buyer_source_id=None))
        == "insufficient"
    )


def test_contact_search_verified_and_safety_regressions(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _seed_identity(conn)
    _insert_contact(
        conn,
        contact_id="c_verified",
        email="buyer@hospital.demo.cl",
        role="Jefa de Adquisiciones",
    )
    _insert_contact(
        conn,
        contact_id="c_review",
        email="other@hospital.demo.cl",
        role=None,
        identity_status="needs_review",
        identity_confidence="medium",
    )
    # Non-accepted evidence type must NOT yield verified.
    _insert_verification_evidence(
        conn,
        evidence_id="ev_bad",
        contact_id="c_verified",
        evidence_type="contact_link",
        matching_reason="resolved_contact",
        source_table="fixture",
        source_plane="identity",
    )
    conn.commit()
    conn.execute("BEGIN")
    enable_require_active_read_transaction(conn)

    org = _org()
    safety = _clear_safety()
    summary, cands, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=safety,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    # Without accepted verification evidence → role review, not verified.
    assert summary.final_contact_status == "existing_contact_needs_role_review"
    assert summary.selected_contact_id == "c_verified"
    needs_review = next(c for c in cands if c.contact_id == "c_review")
    assert needs_review.selectable is False  # identity needs_review

    # Add genuine verification evidence.
    _insert_verification_evidence(
        conn, evidence_id="ev_good", contact_id="c_verified"
    )
    summary_v, _, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=safety,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    assert summary_v.final_contact_status == "existing_verified_contact"
    assert summary_v.selected_contact_id == "c_verified"

    # Invalid email string is not usable.
    conn.execute(
        "UPDATE commercial_identity_contact SET normalized_email='not-an-email' "
        "WHERE contact_id='c_verified'"
    )
    summary_inv, cands_inv, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=safety,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    verified_inv = next(c for c in cands_inv if c.contact_id == "c_verified")
    assert verified_inv.has_usable_email is False
    assert summary_inv.selected_contact_id != "c_verified" or not verified_inv.selectable

    conn.execute(
        "UPDATE commercial_identity_contact SET normalized_email='buyer@hospital.demo.cl' "
        "WHERE contact_id='c_verified'"
    )

    # Internal domain.
    internal = _clear_safety()
    conn.execute(
        "UPDATE commercial_identity_contact SET normalized_email='x@origenlab.cl' "
        "WHERE contact_id='c_verified'"
    )
    summary_int, cands_int, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=internal,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    blocked_int = next(c for c in cands_int if c.contact_id == "c_verified")
    assert blocked_int.safety_blocked is True
    assert blocked_int.selectable is False
    assert "internal_domain" in evaluate_contact_safety(
        email_norm="x@origenlab.cl",
        institution_name="Hospital Demo",
        safety=internal,
    )["reasons"]

    conn.execute(
        "UPDATE commercial_identity_contact SET normalized_email='buyer@hospital.demo.cl' "
        "WHERE contact_id='c_verified'"
    )

    # Sent history.
    sent = _clear_safety(
        sent_recipient_norms=frozenset({"buyer@hospital.demo.cl"}),
    )
    _, cands_sent, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=sent,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    assert next(c for c in cands_sent if c.contact_id == "c_verified").selectable is False

    # Email suppression.
    supp = _clear_safety(suppressed_norms=frozenset({"buyer@hospital.demo.cl"}))
    _, cands_supp, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=supp,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    assert next(c for c in cands_supp if c.contact_id == "c_verified").selectable is False

    # Domain suppression.
    dsupp = _clear_safety(
        suppressed_contact_domains=frozenset({"hospital.demo.cl"}),
    )
    _, cands_d, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=dsupp,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    assert next(c for c in cands_d if c.contact_id == "c_verified").selectable is False

    # Blocking outreach states.
    for state in ("contacted", "replied", "snoozed"):
        o = _clear_safety(
            outreach_state_by_email={"buyer@hospital.demo.cl": state},
        )
        gate = evaluate_contact_safety(
            email_norm="buyer@hospital.demo.cl",
            institution_name="Hospital Demo",
            safety=o,
        )
        assert gate["safety_blocked"] is True
        assert f"outreach_{state}" in gate["reasons"]

    # Missing safety truth → unknown / nonselectable.
    incomplete = safety_snapshot_from_gate_context(
        _empty_gate(), truth_complete=False
    )
    gate_u = evaluate_contact_safety(
        email_norm="buyer@hospital.demo.cl",
        institution_name="Hospital Demo",
        safety=incomplete,
    )
    assert gate_u["safety_unknown"] is True
    assert gate_u["selectable_by_safety"] is False

    # Ambiguous identity not selectable.
    _insert_contact(
        conn,
        contact_id="c_amb",
        email="amb@hospital.demo.cl",
        role="Jefa de Adquisiciones",
        identity_status="ambiguous",
    )
    _, cands_amb, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=_clear_safety(),
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    amb = next(c for c in cands_amb if c.contact_id == "c_amb")
    assert amb.selectable is False
    assert amb.ranking_tier == "identity_incompatible"

    disable_require_active_read_transaction(conn)
    conn.rollback()
    conn.close()
    assert summary_int.final_contact_status in {
        "contact_blocked",
        "existing_contact_needs_role_review",
        "no_contact_found",
        "contact_research_required",
    }


def test_material_ambiguity_does_not_use_contact_id_tiebreak(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _seed_identity(conn)
    for cid, email in (
        ("c_a", "a@hospital.demo.cl"),
        ("c_b", "b@hospital.demo.cl"),
    ):
        _insert_contact(
            conn,
            contact_id=cid,
            email=email,
            role="Jefa de Adquisiciones",
        )
        _insert_verification_evidence(
            conn, evidence_id=f"ev_{cid}", contact_id=cid
        )
    conn.commit()
    conn.execute("BEGIN")
    enable_require_active_read_transaction(conn)
    summary, cands, _, confs = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=_org(),
        conn=conn,
        safety=_clear_safety(),
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="current_authoritative_snapshot",
    )
    assert summary.final_contact_status == "ambiguous_contact"
    assert summary.selected_contact_id is None
    assert len(cands) == 2
    assert confs
    disable_require_active_read_transaction(conn)
    conn.close()


def test_reconcile_binding_counterexamples() -> None:
    decision = _relevance()
    tender = _tender()
    # Force unlinked org for deferred path.
    org_u = _org(status="unlinked", account_id=None, decision_id=decision.decision_id)
    summary_u = deferred_summary(
        tender_id="t1",
        relevance=decision,
        organization=org_u,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class="current_authoritative_snapshot",
    )
    ok = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org_u],
        summaries=[summary_u],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert ok["ok"] is True

    # Swapped / fabricated decision id.
    bad_org = _org(decision_id="fabricated")
    bad = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[bad_org],
        summaries=[
            deferred_summary(
                tender_id="t1",
                relevance=decision,
                organization=bad_org,
                input_fingerprint="x",
                reason_code="account_unresolved",
                currentness_class="current_authoritative_snapshot",
            )
        ],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert bad["ok"] is False
    assert any(
        f.get("error") == "organization_relevance_decision_mismatch"
        for f in bad["failures"]
    )

    # Deferred with candidates.
    bad_cand = ContactCandidate(
        candidate_id="cc_1",
        contact_resolution_id=summary_u.contact_resolution_id,
        coalesced_tender_id="t1",
        account_id="acct_a",
        contact_id="c1",
        rank=1,
        ranking_tier="role_review",
        role_raw_digest="x",
        role_suitability="unknown",
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="unverified",
        evidence_ids=(),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=False,
        safety_unknown=False,
        selectable=True,
        ranking_reason_codes=(),
    )
    bad2 = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org_u],
        summaries=[summary_u],
        candidates=[bad_cand],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert any(
        f.get("error")
        in {
            "deferred_has_candidates",
            "candidate_row_count_mismatch",
            "orphan_candidate",
            "global_candidate_union_mismatch",
        }
        for f in bad2["failures"]
    )

    # Linked path with mismatched candidate tender/account and bad selection.
    linked_org = _org(decision_id=decision.decision_id)
    linked_summary = ContactResolutionSummary(
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        relevance_decision_id=decision.decision_id,
        organization_resolution_id=linked_org.organization_resolution_id,
        account_id="acct_a",
        final_contact_status="existing_contact_needs_role_review",
        selected_contact_id="c1",
        selected_candidate_id="cc_missing",
        search_stages_completed=("pr2_account_contacts", "safety_gate"),
        next_action="review_contact_role",
        reason_code="contact_requires_role_review",
        considered_contact_count=1,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo=decision.relevance_class,
        lifecycle_class_echo="open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="",
        input_fingerprint="x",
        semantic_fingerprint="s",
        rules_version="r",
        resolver_version="v",
    )
    mismatch_cand = ContactCandidate(
        candidate_id="cc_1",
        contact_resolution_id="crs_1",
        coalesced_tender_id="OTHER",
        account_id="acct_other",
        contact_id="c1",
        rank=2,
        ranking_tier="role_review",
        role_raw_digest="x",
        role_suitability="unknown",
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="unverified",
        evidence_ids=("ev_missing",),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=False,
        safety_unknown=False,
        selectable=True,
        ranking_reason_codes=(),
    )
    bad3 = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[linked_org],
        summaries=[linked_summary],
        candidates=[mismatch_cand],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    errs = {f.get("error") for f in bad3["failures"]}
    assert bad3["ok"] is False
    assert "invalid_selected_candidate_pointer" in errs
    assert (
        "candidate_tender_mismatch" in errs
        or "orphan_candidate" in errs
        or "candidate_contact_set_mismatch" in errs
        or "candidate_field_mismatch" in errs
    )
    assert (
        "candidate_account_mismatch" in errs
        or "orphan_candidate" in errs
        or "candidate_contact_set_mismatch" in errs
    )
    assert (
        "missing_evidence" in errs
        or "plan_evidence_union_mismatch" in errs
        or "candidate_evidence_ids_mismatch" in errs
        or "orphan_candidate" in errs
    )

    # Ambiguous without candidates/conflict.
    amb_summary = ContactResolutionSummary(
        contact_resolution_id="crs_amb",
        coalesced_tender_id="t1",
        relevance_decision_id=decision.decision_id,
        organization_resolution_id=linked_org.organization_resolution_id,
        account_id="acct_a",
        final_contact_status="ambiguous_contact",
        selected_contact_id=None,
        selected_candidate_id=None,
        search_stages_completed=("pr2_account_contacts",),
        next_action="resolve_contact_ambiguity",
        reason_code="multiple_competing_contacts",
        considered_contact_count=0,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo=decision.relevance_class,
        lifecycle_class_echo="open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="",
        input_fingerprint="x",
        semantic_fingerprint="s",
        rules_version="r",
        resolver_version="v",
    )
    bad4 = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[linked_org],
        summaries=[amb_summary],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    errs4 = {f.get("error") for f in bad4["failures"]}
    assert bad4["ok"] is False
    assert (
        "ambiguous_without_competing_candidates" in errs4
        or "summary_field_mismatch" in errs4
        or "conflict_set_mismatch" in errs4
        or "candidate_contact_set_mismatch" in errs4
    )


def test_semantic_digest_sensitive_to_safety_and_selection() -> None:
    base_summary = ContactResolutionSummary(
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        relevance_decision_id="trd_t1",
        organization_resolution_id="org_1",
        account_id="acct_a",
        final_contact_status="existing_contact_needs_role_review",
        selected_contact_id="c1",
        selected_candidate_id="cc_1",
        search_stages_completed=("pr2_account_contacts", "safety_gate"),
        next_action="review_contact_role",
        reason_code="x",
        considered_contact_count=1,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo="strong_equipment_class",
        lifecycle_class_echo="open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="",
        input_fingerprint="i",
        semantic_fingerprint="s",
        rules_version="r",
        resolver_version="v",
    )

    def _cand(**kw):
        base = dict(
            candidate_id="cc_1",
            contact_resolution_id="crs_1",
            coalesced_tender_id="t1",
            account_id="acct_a",
            contact_id="c1",
            rank=1,
            ranking_tier="role_review",
            role_raw_digest="r",
            role_suitability="unknown",
            identity_status="resolved",
            identity_confidence="high",
            has_usable_email=True,
            verification_status="unverified",
            evidence_ids=("ev1",),
            suppression_result="clear",
            outreach_state_result="clear",
            safety_blocked=False,
            safety_unknown=False,
            selectable=True,
            ranking_reason_codes=("usable_email",),
        )
        base.update(kw)
        return ContactCandidate(**base)

    ev = ContactResolutionEvidence(
        evidence_id="ev1",
        subject_kind="contact",
        subject_id="c1",
        source_table="contact_master",
        source_record_id="src1",
        source_plane="contact_master",
        origin_plane="business_mart",
        evidence_type="contact_identity",
        evidence_at="2026-01-01T00:00:00Z",
        matching_reason_code="exact_email",
        confidence="high",
    )
    d1 = contact_semantic_digest(
        summaries=[base_summary],
        candidates=[_cand()],
        evidence=[ev],
        conflicts=[],
    )
    d2 = contact_semantic_digest(
        summaries=[base_summary],
        candidates=[_cand(outreach_state_result="contacted")],
        evidence=[ev],
        conflicts=[],
    )
    d3 = contact_semantic_digest(
        summaries=[base_summary],
        candidates=[_cand(outreach_state_result="replied")],
        evidence=[ev],
        conflicts=[],
    )
    assert d1 != d2 != d3
    d4 = contact_semantic_digest(
        summaries=[base_summary],
        candidates=[_cand(identity_status="ambiguous", selectable=False)],
        evidence=[ev],
        conflicts=[],
    )
    assert d4 != d1
    ev2 = ContactResolutionEvidence(
        **{**ev.to_dict(), "evidence_type": "other", "evidence_id": "ev1"}
    )
    d5 = contact_semantic_digest(
        summaries=[base_summary],
        candidates=[_cand()],
        evidence=[ev2],
        conflicts=[],
    )
    assert d5 != d1
    # Order stability.
    d_order = contact_semantic_digest(
        summaries=[base_summary],
        candidates=[_cand(), _cand(candidate_id="cc_2", contact_id="c2", rank=2)],
        evidence=[ev],
        conflicts=[],
    )
    d_order2 = contact_semantic_digest(
        summaries=[base_summary],
        candidates=[_cand(candidate_id="cc_2", contact_id="c2", rank=2), _cand()],
        evidence=[ev],
        conflicts=[],
    )
    assert d_order == d_order2


def test_shuffle_equivalent_inputs_stable_org_id() -> None:
    index = build_account_index(
        accounts=[
            {
                "account_id": "acct_a",
                "canonical_name_norm": "hospital demo",
                "primary_domain_norm": "hospital.demo.cl",
            }
        ],
        aliases=[],
        domains=[{"account_id": "acct_a", "domain_norm": "hospital.demo.cl"}],
    )
    a = resolve_organization_for_tender(
        _tender(),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=frozenset({"acct_a"}),
        pr4_by_procurement={},
        identity_fingerprint="ifp",
    )
    b = resolve_organization_for_tender(
        _tender(),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=frozenset({"acct_a"}),
        pr4_by_procurement={},
        identity_fingerprint="ifp",
    )
    assert a.organization_resolution_id == b.organization_resolution_id
    assert a.account_id == b.account_id


def _cand_for_status(
    *,
    contact_id: str,
    tier: str,
    selectable: bool,
    safety_blocked: bool = False,
    safety_unknown: bool = False,
    role_suitability: str = "suitable_procurement",
    verification_status: str = "unverified",
    has_usable_email: bool = True,
) -> ContactCandidate:
    return ContactCandidate(
        candidate_id=f"cc_{contact_id}",
        contact_resolution_id="crs_x",
        coalesced_tender_id="t1",
        account_id="acct_a",
        contact_id=contact_id,
        rank=1,
        ranking_tier=tier,
        role_raw_digest="r",
        role_suitability=role_suitability,
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=has_usable_email,
        verification_status=verification_status,
        evidence_ids=(),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=safety_blocked,
        safety_unknown=safety_unknown,
        selectable=selectable,
        ranking_reason_codes=(),
    )


def test_status_precedence_blocked_beats_role_known_email_missing() -> None:
    """Mixed blocked + role_known_email_missing → contact_blocked via precedence."""
    policy = contact_resolution_policy_spec()
    ranked = [
        _cand_for_status(
            contact_id="c_missing",
            tier="role_known_email_missing",
            selectable=False,
            has_usable_email=False,
        ),
        _cand_for_status(
            contact_id="c_blocked",
            tier="blocked",
            selectable=False,
            safety_blocked=True,
        ),
    ]
    status, selected, reason, conflicts = select_final_status(
        ranked=ranked, policy=policy, tender_id="t1"
    )
    assert status == "contact_blocked"
    assert selected is None
    assert reason == "all_selectable_paths_blocked"
    assert conflicts == []


def test_contact_research_required_keeps_status_when_research_gated(
    tmp_path: Path,
) -> None:
    """Non-actionable tender keeps analysis status; next_action becomes none."""
    db = tmp_path / "research.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _seed_identity(conn)
    # Unsuitable role with email → identity-compatible but non-selectable tier path
    # that exhausts to contact_research_required (or role_review if selectable).
    # Use identity_incompatible / non-suitable email-missing-like non-selectable:
    # blocked=false, no email, unsuitable role → no role_known_email_missing (needs suitable),
    # no blocked → contact_research_required.
    _insert_contact(
        conn,
        contact_id="c_noise",
        email="not-an-email",
        role="estudiante",
        identity_status="resolved",
    )
    conn.commit()
    conn.execute("BEGIN")
    enable_require_active_read_transaction(conn)
    summary, cands, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(lifecycle_class_echo="closed"),
        organization=_org(),
        conn=conn,
        safety=_clear_safety(),
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
        currentness_class="historical_pr4_only",
        label_status="proposed",
        independently_reviewed=False,
    )
    assert summary.final_contact_status == "contact_research_required"
    assert summary.next_action == "none"
    assert summary.currentness_class_echo == "historical_pr4_only"
    assert summary.relevance_validation_status_echo == "proposed"
    assert cands
    disable_require_active_read_transaction(conn)
    conn.close()


def test_actionability_gated_actions_fail_closed_matrix() -> None:
    """tender_allows_gated_lead_or_research / next_action_for_status fail closed."""
    cases = [
        # lifecycle=awarded, relevance=unrelated → gated becomes none
        dict(
            lifecycle="awarded",
            relevance="unrelated",
            currentness="current_authoritative_snapshot",
            label_status="reviewed",
            independently_reviewed=True,
        ),
        # lifecycle=status_conflict, relevance=ambiguous → none
        dict(
            lifecycle="status_conflict",
            relevance="ambiguous",
            currentness="current_authoritative_snapshot",
            label_status="reviewed",
            independently_reviewed=True,
        ),
        # lifecycle=unknown, relevance=unknown → none
        dict(
            lifecycle="unknown",
            relevance="unknown",
            currentness="current_authoritative_snapshot",
            label_status="reviewed",
            independently_reviewed=True,
        ),
        # active_open + strong + historical → none
        dict(
            lifecycle="active_open",
            relevance="strong_equipment_class",
            currentness="historical_pr4_only",
            label_status="reviewed",
            independently_reviewed=True,
        ),
        # active_open + strong + current but unvalidated → none
        dict(
            lifecycle="active_open",
            relevance="strong_equipment_class",
            currentness="current_authoritative_snapshot",
            label_status=None,
            independently_reviewed=False,
        ),
        dict(
            lifecycle="active_open",
            relevance="strong_equipment_class",
            currentness="current_authoritative_snapshot",
            label_status="proposed",
            independently_reviewed=False,
        ),
    ]
    for case in cases:
        allowed, _ = tender_allows_gated_lead_or_research(
            lifecycle_class=case["lifecycle"],
            relevance_class=case["relevance"],
            currentness_class=case["currentness"],
            label_status=case["label_status"],
            independently_reviewed=case["independently_reviewed"],
        )
        assert allowed is False
        action = next_action_for_status(
            "no_contact_found",
            lifecycle_class=case["lifecycle"],
            relevance_class=case["relevance"],
            currentness_class=case["currentness"],
            label_status=case["label_status"],
            independently_reviewed=case["independently_reviewed"],
        )
        assert action == "none"

    # reviewed + independently_reviewed unlocks research for no_contact_found
    allowed_ok, reasons = tender_allows_gated_lead_or_research(
        lifecycle_class="active_open",
        relevance_class="strong_equipment_class",
        currentness_class="current_authoritative_snapshot",
        label_status="reviewed",
        independently_reviewed=True,
    )
    assert allowed_ok is True
    assert reasons == ()
    assert (
        next_action_for_status(
            "no_contact_found",
            lifecycle_class="active_open",
            relevance_class="strong_equipment_class",
            currentness_class="current_authoritative_snapshot",
            label_status="reviewed",
            independently_reviewed=True,
        )
        == "research_contact_if_active"
    )


def test_pr4_malformed_candidate_account_ids_raises() -> None:
    import pytest

    with pytest.raises(Pr4ProvenanceError):
        _parse_candidate_account_ids(None)
    with pytest.raises(Pr4ProvenanceError):
        _parse_candidate_account_ids("")
    with pytest.raises(Pr4ProvenanceError):
        _parse_candidate_account_ids("{not-json")
    with pytest.raises(Pr4ProvenanceError):
        _parse_candidate_account_ids('"string"')
    with pytest.raises(Pr4ProvenanceError):
        _parse_candidate_account_ids("null")

    index = build_account_index(
        accounts=[
            {
                "account_id": "acct_a",
                "canonical_name_norm": "hospital demo",
                "primary_domain_norm": "hospital.demo.cl",
            }
        ],
        aliases=[],
        domains=[{"account_id": "acct_a", "domain_norm": "hospital.demo.cl"}],
    )
    org = resolve_organization_for_tender(
        _tender(pr4_ids=("p1",)),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=frozenset({"acct_a"}),
        pr4_by_procurement={
            "p1": {
                "resolution_id": "r1",
                "resolution_status": "linked",
                "account_id": "acct_a",
                "link_route": "A_exact_institutional_domain",
                "reason_code": "ok",
                "candidate_account_ids_json": "{bad",
            }
        },
        identity_fingerprint="ifp",
    )
    assert org.resolution_source == "pr4_provenance_malformed"
    assert org.account_id is None


def test_pr4_unresolved_constituents_do_not_live_link() -> None:
    index = build_account_index(
        accounts=[
            {
                "account_id": "acct_a",
                "canonical_name_norm": "hospital demo",
                "primary_domain_norm": "hospital.demo.cl",
            }
        ],
        aliases=[],
        domains=[{"account_id": "acct_a", "domain_norm": "hospital.demo.cl"}],
    )
    org = resolve_organization_for_tender(
        _tender(pr4_ids=("p1", "p_missing")),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=frozenset({"acct_a"}),
        pr4_by_procurement={
            "p1": {
                "resolution_id": "r1",
                "resolution_status": "linked",
                "account_id": "acct_a",
                "link_route": "A_exact_institutional_domain",
                "reason_code": "ok",
                "candidate_account_ids": (),
            }
        },
        identity_fingerprint="ifp",
    )
    assert org.resolution_source == "pr4_constituent_incomplete"
    assert org.account_id is None
    assert not org.resolution_source.startswith("live_")


def _frozen_ev(
    *,
    evidence_id: str = "ev1",
    contact_id: str = "c1",
    evidence_type: str = "contact_identity",
    matching_reason: str = "exact_email",
    source_table: str = "contact_master",
    source_plane: str = "contact_master",
) -> FrozenEvidenceProjection:
    return FrozenEvidenceProjection(
        evidence_id=evidence_id,
        subject_kind="contact",
        subject_id=contact_id,
        source_table=source_table,
        source_record_id="src1",
        source_plane=source_plane,
        origin_plane="business_mart",
        evidence_type=evidence_type,
        evidence_at="2026-01-01T00:00:00Z",
        matching_reason_code=matching_reason,
        confidence="high",
    )


def _frozen_contact(
    *,
    contact_id: str = "c1",
    account_id: str = "acct_a",
    evidence_ids: tuple[str, ...] = ("ev1",),
    has_usable_email: bool = True,
    role: str | None = "Jefa de Adquisiciones",
) -> FrozenContactProjection:
    return FrozenContactProjection(
        contact_id=contact_id,
        account_id=account_id,
        email_digest="deadbeef" if has_usable_email else None,
        has_usable_email=has_usable_email,
        role_digest="role",
        role_raw=role,
        identity_status="resolved",
        identity_confidence="high",
        evidence_ids=evidence_ids,
        email_norm="buyer@hospital.demo.cl" if has_usable_email else None,
    )


def _linked_summary_for_reconcile(
    *,
    status: str = "existing_contact_needs_role_review",
    selected_candidate_id: str | None = "cc_1",
    selected_contact_id: str | None = "c1",
    next_action: str = "review_contact_role",
    semantic: str = "s",
    considered: int = 1,
) -> ContactResolutionSummary:
    decision = _relevance(lifecycle_class_echo="active_open")
    org = _org(decision_id=decision.decision_id)
    return ContactResolutionSummary(
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        relevance_decision_id=decision.decision_id,
        organization_resolution_id=org.organization_resolution_id,
        account_id="acct_a",
        final_contact_status=status,
        selected_contact_id=selected_contact_id,
        selected_candidate_id=selected_candidate_id,
        search_stages_completed=("pr2_account_contacts", "safety_gate"),
        next_action=next_action,
        reason_code="contact_requires_role_review",
        considered_contact_count=considered,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo=decision.relevance_class,
        lifecycle_class_echo="active_open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="",
        input_fingerprint="x",
        semantic_fingerprint=semantic,
        rules_version="r",
        resolver_version="v",
    )


def test_reconcile_fabricated_evidence_fields_fail() -> None:
    decision = _relevance(lifecycle_class_echo="active_open")
    tender = _tender()
    org = _org(decision_id=decision.decision_id)
    frozen = FrozenSourceIndex(
        contacts_by_id={"c1": _frozen_contact()},
        evidence_by_id={"ev1": _frozen_ev()},
        contacts_by_account={"acct_a": ("c1",)},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )
    plan_ev = ContactResolutionEvidence(
        evidence_id="ev1",
        subject_kind="contact",
        subject_id="c1",
        source_table="fabricated_table",
        source_record_id="src1",
        source_plane="contact_master",
        origin_plane="business_mart",
        evidence_type="contact_identity",
        evidence_at="2026-01-01T00:00:00Z",
        matching_reason_code="exact_email",
        confidence="high",
    )
    cand = ContactCandidate(
        candidate_id="cc_1",
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        account_id="acct_a",
        contact_id="c1",
        rank=1,
        ranking_tier="suitable_role_unverified",
        role_raw_digest="r",
        role_suitability="suitable_procurement",
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="unverified",
        evidence_ids=("ev1",),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=False,
        safety_unknown=False,
        selectable=True,
        ranking_reason_codes=(),
    )
    summary = _linked_summary_for_reconcile()
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=[cand],
        evidence=[plan_ev],
        conflicts=[],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    assert any(f.get("error") == "evidence_field_mismatch" for f in result["failures"])


def test_reconcile_verification_claim_without_frozen_predicate_fails() -> None:
    decision = _relevance(lifecycle_class_echo="active_open")
    tender = _tender()
    org = _org(decision_id=decision.decision_id)
    # Frozen evidence is non-verifying type
    frozen = FrozenSourceIndex(
        contacts_by_id={"c1": _frozen_contact()},
        evidence_by_id={
            "ev1": _frozen_ev(
                evidence_type="contact_link",
                matching_reason="resolved_contact",
                source_table="fixture",
                source_plane="identity",
            )
        },
        contacts_by_account={"acct_a": ("c1",)},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )
    plan_ev = ContactResolutionEvidence(**frozen.evidence_by_id["ev1"].to_dict())
    cand = ContactCandidate(
        candidate_id="cc_1",
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        account_id="acct_a",
        contact_id="c1",
        rank=1,
        ranking_tier="verified_suitable",
        role_raw_digest="r",
        role_suitability="suitable_procurement",
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="explicit_verification",
        evidence_ids=("ev1",),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=False,
        safety_unknown=False,
        selectable=True,
        ranking_reason_codes=(),
    )
    summary = _linked_summary_for_reconcile(
        status="existing_verified_contact",
        next_action="none",
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=[cand],
        evidence=[plan_ev],
        conflicts=[],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    assert any(
        f.get("error")
        in {
            "verification_claim_without_declared_frozen_predicate",
            "verification_claim_without_frozen_predicate",
            "candidate_field_mismatch",
        }
        for f in result["failures"]
    )


def test_reconcile_swapped_evidence_pointer_fails() -> None:
    decision = _relevance(lifecycle_class_echo="active_open")
    tender = _tender()
    org = _org(decision_id=decision.decision_id)
    frozen = FrozenSourceIndex(
        contacts_by_id={
            "c1": _frozen_contact(evidence_ids=("ev1",)),
            "c2": _frozen_contact(contact_id="c2", evidence_ids=("ev2",)),
        },
        evidence_by_id={
            "ev1": _frozen_ev(evidence_id="ev1", contact_id="c1"),
            "ev2": _frozen_ev(evidence_id="ev2", contact_id="c2"),
        },
        contacts_by_account={"acct_a": ("c1", "c2")},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )
    # Candidate c1 points at c2's evidence
    plan_ev = ContactResolutionEvidence(**frozen.evidence_by_id["ev2"].to_dict())
    cand = ContactCandidate(
        candidate_id="cc_1",
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        account_id="acct_a",
        contact_id="c1",
        rank=1,
        ranking_tier="suitable_role_unverified",
        role_raw_digest="r",
        role_suitability="suitable_procurement",
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="unverified",
        evidence_ids=("ev2",),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=False,
        safety_unknown=False,
        selectable=True,
        ranking_reason_codes=(),
    )
    summary = _linked_summary_for_reconcile()
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=[cand],
        evidence=[plan_ev],
        conflicts=[],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "frozen_evidence_subject_mismatch" in errs
        or "candidate_evidence_ids_not_subset_of_frozen" in errs
        or "evidence_contact_mismatch" in errs
        or "candidate_evidence_ids_mismatch" in errs
        or "orphan_or_fabricated_plan_evidence" in errs
    )


def test_reconcile_rank_order_inverted_vs_tier_fails() -> None:
    decision = _relevance(lifecycle_class_echo="active_open")
    tender = _tender()
    org = _org(decision_id=decision.decision_id)
    frozen = FrozenSourceIndex(
        contacts_by_id={
            "c_hi": _frozen_contact(contact_id="c_hi", evidence_ids=()),
            "c_lo": _frozen_contact(contact_id="c_lo", evidence_ids=()),
        },
        evidence_by_id={},
        contacts_by_account={"acct_a": ("c_hi", "c_lo")},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )

    def _c(cid: str, tier: str, rank: int) -> ContactCandidate:
        return ContactCandidate(
            candidate_id=f"cc_{cid}",
            contact_resolution_id="crs_1",
            coalesced_tender_id="t1",
            account_id="acct_a",
            contact_id=cid,
            rank=rank,
            ranking_tier=tier,
            role_raw_digest="r",
            role_suitability="suitable_procurement",
            identity_status="resolved",
            identity_confidence="high",
            has_usable_email=True,
            verification_status="unverified",
            evidence_ids=(),
            suppression_result="clear",
            outreach_state_result="clear",
            safety_blocked=False,
            safety_unknown=False,
            selectable=True,
            ranking_reason_codes=(),
        )

    # Rank 1 is worse tier than rank 2 → inverted
    cands = [
        _c("c_lo", "role_review", 1),
        _c("c_hi", "verified_suitable", 2),
    ]
    decision_id = decision.decision_id
    org_id = org.organization_resolution_id
    summary = ContactResolutionSummary(
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        relevance_decision_id=decision_id,
        organization_resolution_id=org_id,
        account_id="acct_a",
        final_contact_status="existing_contact_needs_role_review",
        selected_contact_id="c_lo",
        selected_candidate_id="cc_c_lo",
        search_stages_completed=("pr2_account_contacts", "safety_gate"),
        next_action="review_contact_role",
        reason_code="contact_requires_role_review",
        considered_contact_count=2,
        suitable_contact_count=2,
        blocked_contact_count=0,
        relevance_class_echo=decision.relevance_class,
        lifecycle_class_echo="active_open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="",
        input_fingerprint="x",
        semantic_fingerprint="s",
        rules_version="r",
        resolver_version="v",
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=cands,
        evidence=[],
        conflicts=[],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    assert any(
        f.get("error")
        in {
            "candidate_rank_order_inverted",
            "candidate_field_mismatch",
            "summary_field_mismatch",
        }
        for f in result["failures"]
    )


def test_policy_mutation_changes_execution_status_and_rules_fingerprint() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution import (
        policy as policy_mod,
    )

    original = policy_mod.contact_resolution_policy_spec
    base_fp = contact_rules_fingerprint()
    ranked = [
        _cand_for_status(
            contact_id="c_missing",
            tier="role_known_email_missing",
            selectable=False,
            has_usable_email=False,
        ),
        _cand_for_status(
            contact_id="c_blocked",
            tier="blocked",
            selectable=False,
            safety_blocked=True,
        ),
    ]
    status_default, _, _, _ = select_final_status(
        ranked=ranked, policy=original(), tender_id="t1"
    )
    assert status_default == "contact_blocked"

    mutated = copy.deepcopy(original())
    precedence = list(mutated["status_precedence"])
    # Move role_known_email_missing before contact_blocked
    precedence.remove("role_known_email_missing")
    idx = precedence.index("contact_blocked")
    precedence.insert(idx, "role_known_email_missing")
    mutated["status_precedence"] = precedence

    def _mut():
        return mutated

    policy_mod.contact_resolution_policy_spec = _mut  # type: ignore[assignment]
    try:
        status_mut, _, _, _ = select_final_status(
            ranked=ranked, policy=mutated, tender_id="t1"
        )
        assert status_mut == "role_known_email_missing"
        assert contact_rules_fingerprint() != base_fp
    finally:
        policy_mod.contact_resolution_policy_spec = original  # type: ignore[assignment]
    assert contact_rules_fingerprint() == base_fp


def test_frozen_source_fingerprint_moves_on_email_digest_change() -> None:
    org = _org()
    base_kwargs = dict(
        pr5c_semantic_digest="a" * 64,
        pr5d_semantic_digest="b" * 64,
        identity_fingerprint="c" * 64,
        safety_fingerprint="d" * 64,
        organization_resolutions=[org],
        pr4_resolution_ids=[],
        pr2_contact_ids=["c1"],
        pr2_evidence_ids=["ev1"],
    )
    fp1 = contact_input_fingerprint(
        frozen_source_fingerprint="e" * 64,
        **base_kwargs,
    )
    fp2 = contact_input_fingerprint(
        frozen_source_fingerprint="f" * 64,
        **base_kwargs,
    )
    assert fp1 != fp2

    s1 = ContactResolutionSummary(
        contact_resolution_id="crs_1",
        coalesced_tender_id="t1",
        relevance_decision_id="trd_t1",
        organization_resolution_id="org_t1",
        account_id="acct_a",
        final_contact_status="no_contact_found",
        selected_contact_id=None,
        selected_candidate_id=None,
        search_stages_completed=("pr2_account_contacts", "safety_gate"),
        next_action="none",
        reason_code="x",
        considered_contact_count=0,
        suitable_contact_count=0,
        blocked_contact_count=0,
        relevance_class_echo="strong_equipment_class",
        lifecycle_class_echo="active_open",
        currentness_class_echo="current_authoritative_snapshot",
        relevance_validation_status_echo="",
        input_fingerprint="i",
        semantic_fingerprint="s",
        rules_version="r",
        resolver_version="v",
    )
    d1 = contact_semantic_digest(
        summaries=[s1], candidates=[], evidence=[], conflicts=[]
    )
    s2 = ContactResolutionSummary(
        **{
            **s1.to_dict(),
            "currentness_class_echo": "historical_pr4_only",
            "relevance_validation_status_echo": "reviewed",
            "selected_candidate_id": "cc_x",
        }
    )
    d2 = contact_semantic_digest(
        summaries=[s2], candidates=[], evidence=[], conflicts=[]
    )
    assert d1 != d2


def _linked_frozen_pair() -> tuple[
    FrozenSourceIndex,
    OrganizationResolution,
    TenderRelevanceDecision,
    CoalescedProcurementTender,
]:
    decision = _relevance(lifecycle_class_echo="active_open")
    tender = _tender()
    org = _org(decision_id=decision.decision_id)
    c1 = _frozen_contact(contact_id="c1", evidence_ids=("ev1",), role="Jefa de Adquisiciones")
    c2 = _frozen_contact(
        contact_id="c2",
        evidence_ids=("ev2",),
        role="Encargado de Laboratorio",
        has_usable_email=True,
    )
    # Distinct email for c2
    c2 = FrozenContactProjection(
        contact_id="c2",
        account_id="acct_a",
        email_digest="cafebabe",
        has_usable_email=True,
        role_digest="role2",
        role_raw="Encargado de Laboratorio",
        identity_status="resolved",
        identity_confidence="high",
        evidence_ids=("ev2",),
        email_norm="lab@hospital.demo.cl",
    )
    frozen = FrozenSourceIndex(
        contacts_by_id={"c1": c1, "c2": c2},
        evidence_by_id={
            "ev1": _frozen_ev(evidence_id="ev1", contact_id="c1"),
            "ev2": _frozen_ev(evidence_id="ev2", contact_id="c2"),
        },
        contacts_by_account={"acct_a": ("c1", "c2")},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )
    return frozen, org, decision, tender


def _project_golden(frozen, org, decision, tender, safety=None):
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.contact_search import (
        project_linked_contact_resolution,
    )

    return project_linked_contact_resolution(
        tender_id="t1",
        relevance=decision,
        organization=org,
        frozen_index=frozen,
        safety=safety or _clear_safety(),
        buyer_email_norm=None,
        institution_name=tender.buyer_display_selected,
        input_fingerprint="x",
        currentness_class=tender.currentness_class or "current_authoritative_snapshot",
        label_status=None,
        independently_reviewed=False,
    )


def test_reconcile_omitted_all_frozen_contacts_as_no_contact_found_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    # Omit all candidates; force no_contact_found
    from dataclasses import replace

    empty = replace(
        summary,
        final_contact_status="no_contact_found",
        selected_contact_id=None,
        selected_candidate_id=None,
        considered_contact_count=0,
        suitable_contact_count=0,
        blocked_contact_count=0,
        reason_code="internal_search_exhausted_empty",
        next_action="none",
        semantic_fingerprint="fabricated",
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[empty],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    errs = {f.get("error") for f in result["failures"]}
    assert result["ok"] is False
    assert "candidate_contact_set_mismatch" in errs or "no_contact_found_despite_frozen_contacts" in errs
    assert "missing_expected_candidate" in errs


def test_reconcile_highest_ranked_contact_omitted_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    assert len(cands) == 2
    top = cands[0]
    kept = [c for c in cands if c.contact_id != top.contact_id]
    from dataclasses import replace

    kept = [
        replace(c, rank=i + 1, contact_resolution_id=summary.contact_resolution_id)
        for i, c in enumerate(kept)
    ]
    kept_evs = [e for e in evs if e.subject_id != top.contact_id]
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[
            replace(
                summary,
                considered_contact_count=1,
                selected_contact_id=kept[0].contact_id if kept else None,
                selected_candidate_id=kept[0].candidate_id if kept else None,
            )
        ],
        candidates=kept,
        evidence=kept_evs,
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert "candidate_contact_set_mismatch" in errs or "missing_expected_candidate" in errs


def test_reconcile_omitted_verifying_evidence_while_claiming_verified_fails() -> None:
    decision = _relevance(lifecycle_class_echo="active_open")
    tender = _tender()
    org = _org(decision_id=decision.decision_id)
    c1 = _frozen_contact(contact_id="c1", evidence_ids=("ev_ok", "ev_extra"), role="Jefa de Adquisiciones")
    frozen = FrozenSourceIndex(
        contacts_by_id={"c1": c1},
        evidence_by_id={
            "ev_ok": _frozen_ev(evidence_id="ev_ok", contact_id="c1"),
            "ev_extra": _frozen_ev(evidence_id="ev_extra", contact_id="c1"),
        },
        contacts_by_account={"acct_a": ("c1",)},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    assert cands
    from dataclasses import replace

    # Drop verifying evidence ids from candidate but keep verification claim
    mutated = [
        replace(
            c,
            evidence_ids=(),
            verification_status="explicit_verification",
        )
        for c in cands
    ]
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=mutated,
        evidence=[],
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "candidate_evidence_ids_mismatch" in errs
        or "verification_claim_without_declared_frozen_predicate" in errs
    )


def test_reconcile_orphan_fabricated_plan_evidence_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    fake = ContactResolutionEvidence(
        evidence_id="ev_fake",
        subject_kind="contact",
        subject_id="c1",
        source_table="contact_master",
        source_record_id="x",
        source_plane="contact_master",
        origin_plane="business_mart",
        evidence_type="contact_identity",
        evidence_at="2026-01-01T00:00:00Z",
        matching_reason_code="exact_email",
        confidence="high",
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=cands,
        evidence=[*evs, fake],
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "orphan_or_fabricated_plan_evidence" in errs
        or "plan_evidence_union_mismatch" in errs
    )


def test_reconcile_safety_clear_when_suppressed_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    safety = _clear_safety(suppressed_norms=frozenset({"buyer@hospital.demo.cl"}))
    summary, cands, evs, confs = _project_golden(
        frozen, org, decision, tender, safety=safety
    )
    from dataclasses import replace

    # Emit as safety-clear despite suppression
    mutated = [
        replace(
            c,
            suppression_result="clear",
            safety_blocked=False,
            safety_unknown=False,
            selectable=True,
            ranking_tier="role_review",
        )
        for c in cands
    ]
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=mutated,
        evidence=evs,
        conflicts=confs,
        frozen_index=frozen,
        safety=safety,
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    assert any(
        f.get("error") == "candidate_field_mismatch"
        and f.get("field")
        in {
            "suppression_result",
            "safety_blocked",
            "selectable",
            "ranking_tier",
            "ranking_reason_codes",
        }
        for f in result["failures"]
    )


def test_reconcile_mutated_role_identity_email_selectability_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    from dataclasses import replace

    mutated = [
        replace(
            c,
            role_raw_digest="mutated",
            role_suitability="unsuitable",
            identity_status="needs_review",
            has_usable_email=False,
            selectable=True,
            ranking_reason_codes=("mutated",),
        )
        for c in cands
    ]
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=mutated,
        evidence=evs,
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    fields = {
        f.get("field")
        for f in result["failures"]
        if f.get("error") == "candidate_field_mismatch"
    }
    assert fields & {
        "role_raw_digest",
        "role_suitability",
        "identity_status",
        "has_usable_email",
        "selectable",
        "ranking_reason_codes",
    }


def test_reconcile_fabricated_ids_with_recomputed_fingerprints_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    from dataclasses import replace
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.contact_search import (
        recompute_summary_semantic_fingerprint,
    )

    fab_cands = [
        replace(c, candidate_id=f"fabricated_{c.contact_id}", contact_resolution_id="fab_crs")
        for c in cands
    ]
    fab_summary = replace(
        summary,
        contact_resolution_id="fab_crs",
        organization_resolution_id="fab_org",
        selected_candidate_id=fab_cands[0].candidate_id if fab_cands else None,
    )
    fab_summary = replace(
        fab_summary,
        semantic_fingerprint=recompute_summary_semantic_fingerprint(
            contact_resolution_id=fab_summary.contact_resolution_id,
            final_contact_status=fab_summary.final_contact_status,
            account_id=fab_summary.account_id,
            selected_contact_id=fab_summary.selected_contact_id,
            selected_candidate_id=fab_summary.selected_candidate_id,
            search_stages_completed=fab_summary.search_stages_completed,
            next_action=fab_summary.next_action,
            reason_code=fab_summary.reason_code,
            candidate_ids=[c.candidate_id for c in fab_cands],
        ),
    )
    fab_org = replace(org, organization_resolution_id="fab_org")
    fab_summary = replace(fab_summary, organization_resolution_id="fab_org")
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[fab_org],
        summaries=[fab_summary],
        candidates=fab_cands,
        evidence=evs,
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "candidate_id_mismatch" in errs
        or "semantic_fingerprint_not_bound_to_source_projection" in errs
        or "summary_field_mismatch" in errs
    )


def test_reconcile_echo_drift_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    from dataclasses import replace

    drifted = replace(
        summary,
        relevance_class_echo="ambiguous",
        lifecycle_class_echo="awarded",
        currentness_class_echo="historical_pr4_only",
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[drifted],
        candidates=cands,
        evidence=evs,
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    assert any(
        f.get("error") == "summary_field_mismatch"
        and f.get("field")
        in {
            "relevance_class_echo",
            "lifecycle_class_echo",
            "currentness_class_echo",
        }
        for f in result["failures"]
    )


def test_reconcile_org_pr4_ids_removed_or_swapped_fails() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.frozen_sources import (
        FrozenPr4ResolutionProjection,
    )
    from dataclasses import replace

    decision = _relevance(lifecycle_class_echo="active_open")
    tender = _tender()
    # Give tender PR4 constituents
    tender = replace(
        tender,
        pr4_procurement_ids=("p1", "p2"),
        pr4_procurement_id="p1",
    )
    org = _org(decision_id=decision.decision_id)
    org = replace(
        org,
        resolution_status="unlinked",
        account_id=None,
        pr4_procurement_ids=("p1", "p2"),
        pr4_resolution_ids=("r1", "r2"),
        resolution_source="pr4_constituents_unresolved",
    )
    frozen = FrozenSourceIndex(
        contacts_by_id={},
        evidence_by_id={},
        contacts_by_account={},
        pr4_by_procurement={
            "p1": FrozenPr4ResolutionProjection(
                resolution_id="r1",
                procurement_id="p1",
                resolution_status="unlinked",
                account_id=None,
                link_route=None,
                reason_code="x",
                candidate_account_ids=(),
            ),
            "p2": FrozenPr4ResolutionProjection(
                resolution_id="r2",
                procurement_id="p2",
                resolution_status="unlinked",
                account_id=None,
                link_route=None,
                reason_code="x",
                candidate_account_ids=(),
            ),
        },
        known_account_ids=frozenset(),
        source_fingerprint="fp",
    )
    swapped = replace(org, pr4_procurement_ids=("p2", "p9"))
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[swapped],
        summaries=[
            deferred_summary(
                tender_id="t1",
                relevance=decision,
                organization=swapped,
                input_fingerprint="x",
                reason_code="account_unresolved",
                currentness_class=tender.currentness_class or "",
            )
        ],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    assert any(
        f.get("error") == "organization_pr4_procurement_ids_mismatch"
        for f in result["failures"]
    )


def test_reconcile_wrong_or_orphan_conflict_fails() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    # Make both suitable same tier to force ambiguity
    c1 = _frozen_contact(contact_id="c1", evidence_ids=("ev1",), role="Jefa de Adquisiciones")
    c2 = FrozenContactProjection(
        contact_id="c2",
        account_id="acct_a",
        email_digest="cafebabe",
        has_usable_email=True,
        role_digest="role2",
        role_raw="Jefa de Compras",
        identity_status="resolved",
        identity_confidence="high",
        evidence_ids=("ev2",),
        email_norm="lab@hospital.demo.cl",
    )
    frozen = FrozenSourceIndex(
        contacts_by_id={"c1": c1, "c2": c2},
        evidence_by_id={
            "ev1": _frozen_ev(evidence_id="ev1", contact_id="c1"),
            "ev2": _frozen_ev(evidence_id="ev2", contact_id="c2"),
        },
        contacts_by_account={"acct_a": ("c1", "c2")},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    assert summary.final_contact_status == "ambiguous_contact"
    assert confs
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
        ContactResolutionConflict,
    )

    orphan = ContactResolutionConflict(
        conflict_id="fabricated_conflict",
        coalesced_tender_id="t1",
        conflict_type="wrong_type",
        reason_code="wrong_reason",
        subject_keys=("nobody",),
        evidence_ids=(),
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=cands,
        evidence=evs,
        conflicts=[orphan],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    assert any(f.get("error") == "conflict_set_mismatch" for f in result["failures"])


def test_candidate_id_independent_of_provisional_parent() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.stable_ids import (
        candidate_id_for,
    )
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.contact_search import (
        project_candidates_from_frozen,
    )

    frozen, org, decision, tender = _linked_frozen_pair()
    cands_a, _ = project_candidates_from_frozen(
        tender_id="t1",
        account_id="acct_a",
        contact_resolution_id="provisional_A",
        frozen_index=frozen,
        safety=_clear_safety(),
        buyer_email_norm=None,
        institution_name="Hospital Demo",
    )
    cands_b, _ = project_candidates_from_frozen(
        tender_id="t1",
        account_id="acct_a",
        contact_resolution_id="provisional_B",
        frozen_index=frozen,
        safety=_clear_safety(),
        buyer_email_norm=None,
        institution_name="Hospital Demo",
    )
    assert {c.candidate_id for c in cands_a} == {c.candidate_id for c in cands_b}
    for c in cands_a:
        assert c.candidate_id == candidate_id_for(
            coalesced_tender_id="t1",
            contact_id=c.contact_id,
            account_id=c.account_id,
            ranking_tier=c.ranking_tier,
        )


def test_policy_also_requires_and_role_review_tiers_are_executable() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution import (
        policy as policy_mod,
    )

    original = policy_mod.contact_resolution_policy_spec
    base_fp = contact_rules_fingerprint()
    top = _cand_for_status(
        contact_id="c1",
        tier="exact_buyer_email",
        selectable=True,
        role_suitability="suitable_procurement",
        verification_status="explicit_verification",
    )
    status_ok, _, _, _ = select_final_status(
        ranked=[top], policy=original(), tender_id="t1"
    )
    assert status_ok == "existing_verified_contact"

    mutated = copy.deepcopy(original())
    mutated["status_selection"]["exact_buyer_verified_path"]["also_requires"] = [
        "suitable_role",
        "explicit_verification",
        "usable_email",
        "not_safety_blocked",
        "identity_resolved",
        # Add impossible requirement so path fails
        "buyer_email_exact_impossible",
    ]

    def _mut():
        return mutated

    policy_mod.contact_resolution_policy_spec = _mut  # type: ignore[assignment]
    try:
        # With impossible also_requires, exact buyer path must not verify
        status_mut, sel, reason, _ = select_final_status(
            ranked=[top], policy=mutated, tender_id="t1"
        )
        assert status_mut != "existing_verified_contact" or (
            # If still verified via verified_requires_tier only when tier matches —
            # exact_buyer_email is NOT in verified_requires_tier, so must be role review
            status_mut == "existing_contact_needs_role_review"
        )
        assert status_mut == "existing_contact_needs_role_review"
        assert contact_rules_fingerprint() != base_fp
    finally:
        policy_mod.contact_resolution_policy_spec = original  # type: ignore[assignment]

    # role_review_tiers mutation
    mutated2 = copy.deepcopy(original())
    mutated2["status_selection"]["role_review_tiers"] = ["role_review"]  # drop suitable_role_unverified
    top2 = _cand_for_status(
        contact_id="c1",
        tier="suitable_role_unverified",
        selectable=True,
        role_suitability="suitable_procurement",
        verification_status="unverified",
    )
    status_before, _, _, _ = select_final_status(
        ranked=[top2], policy=original(), tender_id="t1"
    )
    assert status_before == "existing_contact_needs_role_review"

    def _mut2():
        return mutated2

    policy_mod.contact_resolution_policy_spec = _mut2  # type: ignore[assignment]
    try:
        status_after, _, reason, _ = select_final_status(
            ranked=[top2], policy=mutated2, tender_id="t1"
        )
        # Still role review via fail-closed else branch, but fingerprint moved
        assert contact_rules_fingerprint() != base_fp
        assert status_after == "existing_contact_needs_role_review"
        assert reason == "contact_requires_role_review"
    finally:
        policy_mod.contact_resolution_policy_spec = original  # type: ignore[assignment]


def test_reconcile_rejects_changed_input_fingerprint() -> None:
    decision = _relevance()
    tender = _tender()
    org_u = _org(status="unlinked", account_id=None, decision_id=decision.decision_id)
    summary = deferred_summary(
        tender_id="t1",
        relevance=decision,
        organization=org_u,
        input_fingerprint="expected_fp",
        reason_code="account_unresolved",
        currentness_class=tender.currentness_class or "",
    )
    from dataclasses import replace

    mutated = replace(summary, input_fingerprint="mutated_fp")
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org_u],
        summaries=[mutated],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="expected_fp",
    )
    assert result["ok"] is False
    assert any(
        f.get("error") == "summary_field_mismatch"
        and f.get("field") == "input_fingerprint"
        for f in result["failures"]
    )


def test_reconcile_rejects_changed_deferred_reason_with_recomputed_ids() -> None:
    decision = _relevance()
    tender = _tender()
    org_u = _org(status="unlinked", account_id=None, decision_id=decision.decision_id)
    correct = deferred_summary(
        tender_id="t1",
        relevance=decision,
        organization=org_u,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class=tender.currentness_class or "",
    )
    # Fabricate alternate reason and recompute ID + semantic as if honest
    forged = deferred_summary(
        tender_id="t1",
        relevance=decision,
        organization=org_u,
        input_fingerprint="x",
        reason_code="fabricated_reason",
        currentness_class=tender.currentness_class or "",
    )
    assert forged.contact_resolution_id != correct.contact_resolution_id
    assert forged.semantic_fingerprint != correct.semantic_fingerprint
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org_u],
        summaries=[forged],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    fields = {
        f.get("field")
        for f in result["failures"]
        if f.get("error") == "summary_field_mismatch"
    }
    assert "reason_code" in fields
    assert "contact_resolution_id" in fields or "semantic_fingerprint" in fields


def test_reconcile_rejects_nonnull_deferred_account_or_selected() -> None:
    decision = _relevance()
    tender = _tender()
    org_u = _org(status="unlinked", account_id=None, decision_id=decision.decision_id)
    summary = deferred_summary(
        tender_id="t1",
        relevance=decision,
        organization=org_u,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class=tender.currentness_class or "",
    )
    from dataclasses import replace

    mutated = replace(
        summary,
        account_id="acct_a",
        selected_contact_id="c1",
        selected_candidate_id="cc_1",
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org_u],
        summaries=[mutated],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    fields = {
        f.get("field")
        for f in result["failures"]
        if f.get("error") == "summary_field_mismatch"
    }
    assert fields & {"account_id", "selected_contact_id", "selected_candidate_id"}


def test_reconcile_rejects_changed_deferred_counts_and_not_persisted() -> None:
    decision = _relevance()
    tender = _tender()
    org_u = _org(status="unlinked", account_id=None, decision_id=decision.decision_id)
    summary = deferred_summary(
        tender_id="t1",
        relevance=decision,
        organization=org_u,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class=tender.currentness_class or "",
    )
    from dataclasses import replace

    mutated = replace(
        summary,
        considered_contact_count=3,
        suitable_contact_count=1,
        blocked_contact_count=2,
        not_persisted=False,
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org_u],
        summaries=[mutated],
        candidates=[],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    fields = {
        f.get("field")
        for f in result["failures"]
        if f.get("error") == "summary_field_mismatch"
    }
    assert "considered_contact_count" in fields
    assert "not_persisted" in fields


def test_reconcile_rejects_duplicated_blocked_candidate() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    from dataclasses import replace

    # Duplicate a non-selected / blocked-style row (same contact twice)
    dup = replace(cands[0], rank=len(cands) + 1)
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[replace(summary, considered_contact_count=len(cands) + 1)],
        candidates=[*cands, dup],
        evidence=evs,
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "duplicate_candidate_ids_global" in errs
        or "duplicate_tender_contact_pair" in errs
        or "duplicate_tender_contact_pair_global" in errs
        or "candidate_row_count_mismatch" in errs
    )


def test_reconcile_rejects_candidate_on_nonexistent_tender() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    from dataclasses import replace

    orphan = replace(cands[0], coalesced_tender_id="t_missing", candidate_id="orphan_cand")
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=[*cands, orphan],
        evidence=evs,
        conflicts=confs,
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "candidate_unknown_tender" in errs
        or "global_candidate_union_mismatch" in errs
    )


def test_candidate_ids_differ_across_tenders_same_contact() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.stable_ids import (
        candidate_id_for,
    )

    a = candidate_id_for(
        coalesced_tender_id="t1",
        contact_id="c1",
        account_id="acct_a",
        ranking_tier="role_review",
    )
    b = candidate_id_for(
        coalesced_tender_id="t2",
        contact_id="c1",
        account_id="acct_a",
        ranking_tier="role_review",
    )
    assert a != b


def test_reconcile_rejects_forced_old_style_same_candidate_id_across_tenders() -> None:
    """Two tenders with forced identical candidate_id must fail global uniqueness."""
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
        ContactCandidate,
    )
    from dataclasses import replace

    d1 = _relevance(tid="t1", lifecycle_class_echo="active_open")
    d2 = _relevance(tid="t2", lifecycle_class_echo="active_open")
    # Fix decision ids for distinct tenders
    d1 = replace(d1, coalesced_tender_id="t1", decision_id="trd_t1")
    d2 = replace(d2, coalesced_tender_id="t2", decision_id="trd_t2")
    tender1 = _tender(tid="t1")
    tender2 = _tender(tid="t2")
    org1 = _org(tid="t1", decision_id="trd_t1", status="unlinked", account_id=None)
    org2 = _org(tid="t2", decision_id="trd_t2", status="unlinked", account_id=None)
    s1 = deferred_summary(
        tender_id="t1",
        relevance=d1,
        organization=org1,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class=tender1.currentness_class or "",
    )
    s2 = deferred_summary(
        tender_id="t2",
        relevance=d2,
        organization=org2,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class=tender2.currentness_class or "",
    )
    # Force same old-style candidate id onto both (should be deferred-empty, so add extras)
    shared = ContactCandidate(
        candidate_id="old_style_shared",
        contact_resolution_id=s1.contact_resolution_id,
        coalesced_tender_id="t1",
        account_id="acct_a",
        contact_id="c1",
        rank=1,
        ranking_tier="role_review",
        role_raw_digest="r",
        role_suitability="unknown",
        identity_status="resolved",
        identity_confidence="high",
        has_usable_email=True,
        verification_status="unverified",
        evidence_ids=(),
        suppression_result="clear",
        outreach_state_result="clear",
        safety_blocked=False,
        safety_unknown=False,
        selectable=False,
        ranking_reason_codes=(),
    )
    twin = replace(
        shared,
        contact_resolution_id=s2.contact_resolution_id,
        coalesced_tender_id="t2",
    )
    result = reconcile_contact_resolution(
        decisions=[d1, d2],
        tenders_by_id={"t1": tender1, "t2": tender2},
        organizations=[org1, org2],
        summaries=[s1, s2],
        candidates=[shared, twin],
        evidence=[],
        conflicts=[],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert "duplicate_candidate_ids_global" in errs


def test_reconcile_rejects_duplicated_identical_conflict() -> None:
    frozen, org, decision, tender = _linked_frozen_pair()
    c1 = _frozen_contact(contact_id="c1", evidence_ids=("ev1",), role="Jefa de Adquisiciones")
    c2 = FrozenContactProjection(
        contact_id="c2",
        account_id="acct_a",
        email_digest="cafebabe",
        has_usable_email=True,
        role_digest="role2",
        role_raw="Jefa de Compras",
        identity_status="resolved",
        identity_confidence="high",
        evidence_ids=("ev2",),
        email_norm="lab@hospital.demo.cl",
    )
    frozen = FrozenSourceIndex(
        contacts_by_id={"c1": c1, "c2": c2},
        evidence_by_id={
            "ev1": _frozen_ev(evidence_id="ev1", contact_id="c1"),
            "ev2": _frozen_ev(evidence_id="ev2", contact_id="c2"),
        },
        contacts_by_account={"acct_a": ("c1", "c2")},
        pr4_by_procurement={},
        known_account_ids=frozenset({"acct_a"}),
        source_fingerprint="fp",
    )
    summary, cands, evs, confs = _project_golden(frozen, org, decision, tender)
    assert confs
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org],
        summaries=[summary],
        candidates=cands,
        evidence=evs,
        conflicts=[*confs, confs[0]],
        frozen_index=frozen,
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "duplicate_conflict_ids_global" in errs
        or "duplicate_identical_conflicts" in errs
        or "global_conflict_union_mismatch" in errs
        or "conflict_set_mismatch" in errs
    )


def test_reconcile_rejects_conflict_on_nonexistent_tender() -> None:
    decision = _relevance()
    tender = _tender()
    org_u = _org(status="unlinked", account_id=None, decision_id=decision.decision_id)
    summary = deferred_summary(
        tender_id="t1",
        relevance=decision,
        organization=org_u,
        input_fingerprint="x",
        reason_code="account_unresolved",
        currentness_class=tender.currentness_class or "",
    )
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
        ContactResolutionConflict,
    )

    conf = ContactResolutionConflict(
        conflict_id="c_orphan",
        coalesced_tender_id="t_missing",
        conflict_type="ambiguous_contact",
        reason_code="x",
        subject_keys=("c1",),
        evidence_ids=(),
    )
    result = reconcile_contact_resolution(
        decisions=[decision],
        tenders_by_id={"t1": tender},
        organizations=[org_u],
        summaries=[summary],
        candidates=[],
        evidence=[],
        conflicts=[conf],
        frozen_index=empty_frozen_source_index(),
        safety=_clear_safety(),
        expected_input_fingerprint="x",
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert "conflict_unknown_tender" in errs or "global_conflict_union_mismatch" in errs
