"""PR5E — procurement contact-resolution contract tests."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from origenlab_email_pipeline.candidate_export_gate import GateContext
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
        evidence_ref_ids=(),
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
    )
    assert any(f.get("error") == "deferred_has_candidates" for f in bad2["failures"])

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
    )
    errs = {f.get("error") for f in bad3["failures"]}
    assert "invalid_selected_candidate_pointer" in errs
    assert "candidate_tender_mismatch" in errs
    assert "candidate_account_mismatch" in errs
    assert "missing_evidence" in errs
    assert "candidate_rank_drift" in errs

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
    )
    errs4 = {f.get("error") for f in bad4["failures"]}
    assert "ambiguous_without_competing_candidates" in errs4
    assert "ambiguous_without_conflict_row" in errs4
    assert "existing_status_without_candidates" in errs4


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
    )
    assert result["ok"] is False
    assert any(
        f.get("error") == "verification_claim_without_frozen_predicate"
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
    )
    assert result["ok"] is False
    errs = {f.get("error") for f in result["failures"]}
    assert (
        "frozen_evidence_subject_mismatch" in errs
        or "candidate_evidence_ids_not_subset_of_frozen" in errs
        or "evidence_contact_mismatch" in errs
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
    )
    assert result["ok"] is False
    assert any(
        f.get("error") == "candidate_rank_order_inverted" for f in result["failures"]
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
