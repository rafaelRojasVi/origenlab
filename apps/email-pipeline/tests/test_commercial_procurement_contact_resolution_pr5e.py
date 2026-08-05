"""PR5E — procurement contact-resolution tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path


from origenlab_email_pipeline.commercial_identity.schema import (
    ensure_commercial_identity_tables,
)
from origenlab_email_pipeline.commercial_procurement.link_routes import (
    build_account_index,
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
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.fingerprint import (
    contact_rules_fingerprint,
    rules_fingerprint_payload,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.organization import (
    assess_buyer_field_sufficiency,
    buyer_domain_candidate,
    resolve_organization_for_tender,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.planner import (
    reconcile_contact_resolution,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.policy import (
    classify_role_suitability,
    contact_resolution_policy_spec,
)
from origenlab_email_pipeline.commercial_procurement_contact_resolution.safety import (
    SafetySnapshot,
    evaluate_contact_safety,
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
        currentness_class="current",
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


def _relevance(tid: str = "t1") -> TenderRelevanceDecision:
    return TenderRelevanceDecision(
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


def test_forbidden_flags_include_label_and_send() -> None:
    assert "--label" in FORBIDDEN_CLI_FLAGS
    assert "--send" in FORBIDDEN_CLI_FLAGS
    assert "--apply" in FORBIDDEN_CLI_FLAGS


def test_buyer_source_id_never_treated_as_account_id() -> None:
    assert buyer_domain_candidate("acct_12345") is None
    assert buyer_domain_candidate("hospital.demo.cl") == "hospital.demo.cl"
    assert buyer_domain_candidate("gmail.com") is None


def test_role_suitability_ignores_email_local_part_authority() -> None:
    assert classify_role_suitability("Jefa de Adquisiciones") == "suitable_procurement"
    assert classify_role_suitability("laboratorio") == "suitable_laboratory"
    assert classify_role_suitability("") == "unknown"
    assert classify_role_suitability("estudiante") == "unsuitable"
    # Local-part-looking strings are NOT auto-suitable without role field context.
    assert classify_role_suitability(None) == "unknown"


def test_policy_fingerprint_moves_on_precedence_change() -> None:
    import copy

    from origenlab_email_pipeline.commercial_procurement_contact_resolution import (
        fingerprint as fp_mod,
    )
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
    fp_mod.contact_resolution_policy_spec = _mut  # type: ignore[assignment]
    try:
        assert contact_rules_fingerprint() != base
    finally:
        policy_mod.contact_resolution_policy_spec = original  # type: ignore[assignment]
        fp_mod.contact_resolution_policy_spec = original  # type: ignore[assignment]
    assert contact_rules_fingerprint() == base
    assert rules_fingerprint_payload()["policy"] == contact_resolution_policy_spec()


def test_deferred_has_no_candidates_or_stages() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
        OrganizationResolution,
    )

    org = OrganizationResolution(
        organization_resolution_id="org_x",
        coalesced_tender_id="t1",
        relevance_decision_id="trd_t1",
        resolution_status="unlinked",
        resolution_source="live_link_route",
        account_id=None,
        link_route="F_no_match",
        reason_code="no_match",
        candidate_account_ids=(),
        evidence_ref_ids=(),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_only",
        identity_fingerprint="ifp",
    )
    summary = deferred_summary(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        input_fingerprint="x",
        reason_code="account_unresolved",
    )
    assert summary.final_contact_status == CONTACT_RESOLUTION_DEFERRED
    assert summary.search_stages_completed == ()
    assert summary.considered_contact_count == 0
    assert summary.next_action == "resolve_account"
    assert summary.selected_contact_id is None


def test_organization_pr4_conflict_and_live_link(tmp_path: Path) -> None:
    index = build_account_index(
        accounts=[
            {
                "account_id": "acct_a",
                "canonical_name_norm": "hospital demo",
                "primary_domain_norm": "hospital.demo.cl",
            }
        ],
        aliases=[],
        domains=[
            {
                "account_id": "acct_a",
                "domain_norm": "hospital.demo.cl",
            }
        ],
    )
    known = frozenset({"acct_a", "acct_b"})
    # Conflicting PR4 linked accounts.
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
            },
            "p2": {
                "resolution_id": "r2",
                "resolution_status": "linked",
                "account_id": "acct_b",
                "link_route": "B_exact_canonical_name",
                "reason_code": "ok",
            },
        },
        identity_fingerprint="ifp",
    )
    assert org_conflict.resolution_status == "ambiguous"
    assert org_conflict.account_id is None
    assert set(org_conflict.candidate_account_ids) == {"acct_a", "acct_b"}

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
    assert org_live.resolution_source == "live_link_route"

    org_insuff = resolve_organization_for_tender(
        _tender(buyer_display=None, buyer_source_id=None),
        relevance_decision_id="trd_t1",
        account_index=index,
        known_account_ids=known,
        pr4_by_procurement={},
        identity_fingerprint="ifp",
    )
    assert org_insuff.resolution_status == "deferred_insufficient_buyer_fields"
    assert assess_buyer_field_sufficiency(
        _tender(buyer_display=None, buyer_source_id=None)
    ) == "insufficient"


def test_contact_search_and_safety_gate(tmp_path: Path) -> None:
    db = tmp_path / "id.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    ensure_commercial_identity_tables(conn)
    conn.execute(
        """
        INSERT INTO commercial_identity_account(
          account_id, canonical_name, normalized_name, primary_domain,
          identity_confidence, identity_status
        ) VALUES ('acct_a','Hospital Demo','hospital demo','hospital.demo.cl','high','resolved')
        """
    )
    conn.execute(
        """
        INSERT INTO commercial_identity_contact(
          contact_id, normalized_email, display_name, role, account_id,
          account_link_method, identity_confidence, identity_status, email_domain
        ) VALUES
        ('c_verified','buyer@hospital.demo.cl','Buyer','Jefa de Adquisiciones','acct_a',
         'exact','high','resolved','hospital.demo.cl'),
        ('c_review','other@hospital.demo.cl','Other',NULL,'acct_a',
         'exact','medium','needs_review','hospital.demo.cl')
        """
    )
    conn.execute(
        """
        INSERT INTO commercial_identity_evidence(
          evidence_id, subject_kind, subject_id, source_table, source_record_id,
          source_plane, origin_plane, evidence_type, matching_reason_code, confidence
        ) VALUES (
          'ev1','contact','c_verified','fixture','src1',
          'identity','identity','contact_link','resolved_contact','high'
        )
        """
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS contact_email_suppression ("
        "email_norm TEXT PRIMARY KEY, reason TEXT, created_at TEXT)"
    )
    conn.commit()

    from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
        OrganizationResolution,
    )

    org = OrganizationResolution(
        organization_resolution_id="org_1",
        coalesced_tender_id="t1",
        relevance_decision_id="trd_t1",
        resolution_status="linked",
        resolution_source="live_link_route",
        account_id="acct_a",
        link_route="A_exact_institutional_domain",
        reason_code="ok",
        candidate_account_ids=(),
        evidence_ref_ids=(),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_and_domain",
        identity_fingerprint="ifp",
    )
    safety = SafetySnapshot(
        suppressed_norms=frozenset(),
        suppressed_domains=frozenset(),
        outreach_state_by_email={},
    )
    summary, cands, _evs, _confs = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=safety,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
    )
    assert summary.final_contact_status == "existing_verified_contact"
    assert summary.selected_contact_id == "c_verified"
    assert summary.search_stages_completed
    assert len(cands) == 2
    assert all(c.account_id == "acct_a" for c in cands)

    # Suppression blocks selection.
    blocked_safety = SafetySnapshot(
        suppressed_norms=frozenset({"buyer@hospital.demo.cl"}),
        suppressed_domains=frozenset(),
        outreach_state_by_email={},
    )
    summary_b, cands_b, _, _ = resolve_contacts_for_tender(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        conn=conn,
        safety=blocked_safety,
        buyer_email_norm=None,
        institution_name="Hospital Demo",
        input_fingerprint="x",
    )
    verified = next(c for c in cands_b if c.contact_id == "c_verified")
    assert verified.safety_blocked is True
    assert verified.selectable is False
    assert summary_b.selected_contact_id != "c_verified"
    gate = evaluate_contact_safety(
        email_norm="buyer@hospital.demo.cl",
        institution_name="Hospital Demo",
        safety=blocked_safety,
    )
    assert gate["safety_blocked"] is True
    conn.close()


def test_reconcile_equations_and_deferred_invariants() -> None:
    from origenlab_email_pipeline.commercial_procurement_contact_resolution.models import (
        ContactCandidate,
        OrganizationResolution,
    )

    org = OrganizationResolution(
        organization_resolution_id="org_1",
        coalesced_tender_id="t1",
        relevance_decision_id="trd_t1",
        resolution_status="unlinked",
        resolution_source="live_link_route",
        account_id=None,
        link_route="F_no_match",
        reason_code="no_match",
        candidate_account_ids=(),
        evidence_ref_ids=(),
        pr4_procurement_ids=(),
        pr4_resolution_ids=(),
        buyer_field_sufficiency="name_only",
        identity_fingerprint="ifp",
    )
    summary = deferred_summary(
        tender_id="t1",
        relevance=_relevance(),
        organization=org,
        input_fingerprint="x",
        reason_code="account_unresolved",
    )
    ok = reconcile_contact_resolution(
        relevance_decision_ids=["trd_t1"],
        organization_resolution_ids=["org_1"],
        contact_summary_ids=[summary.contact_resolution_id],
        tender_ids_from_relevance=["t1"],
        tender_ids_from_org=["t1"],
        tender_ids_from_contact=["t1"],
        organizations=[org],
        summaries=[summary],
        candidates=[],
    )
    assert ok["ok"] is True

    # Deferred with candidates must fail.
    bad_cand = ContactCandidate(
        candidate_id="cc_1",
        contact_resolution_id=summary.contact_resolution_id,
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
        selectable=True,
        ranking_reason_codes=(),
    )
    bad = reconcile_contact_resolution(
        relevance_decision_ids=["trd_t1"],
        organization_resolution_ids=["org_1"],
        contact_summary_ids=[summary.contact_resolution_id],
        tender_ids_from_relevance=["t1"],
        tender_ids_from_org=["t1"],
        tender_ids_from_contact=["t1"],
        organizations=[org],
        summaries=[summary],
        candidates=[bad_cand],
    )
    assert bad["ok"] is False
    assert any(f.get("error") == "deferred_has_candidates" for f in bad["failures"])


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
