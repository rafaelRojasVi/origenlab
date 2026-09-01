"""Tests for the campaign eligibility wrapper: canonical gate + manual hard block."""

from __future__ import annotations

from origenlab_email_pipeline.candidate_export_gate import (
    REASON_DOMAIN_SUPPRESSION,
    REASON_SENT_HISTORY,
    REASON_SUPPLIER_DOMAIN,
    REASON_SUPPRESSION,
    GateContext,
)
from origenlab_email_pipeline.outbound_campaign_gate import (
    REASON_MANUAL_HOLD,
    REASON_MANUAL_INACTIVE,
    evaluate_campaign_eligibility,
)


def _permissive_ctx(**overrides) -> GateContext:
    base = dict(
        sent_recipient_norms=frozenset(),
        suppressed_norms=frozenset(),
        outreach_state_by_email={},
        supplier_domains=frozenset(),
        blocked_domains=frozenset(),
    )
    base.update(overrides)
    return GateContext(**base)


def test_manual_inactive_blocks_even_with_permissive_gate() -> None:
    result = evaluate_campaign_eligibility(
        contact_email="carolinalobo@pharmaisa.cl", institution_name="Pharma Isa",
        gate_ctx=_permissive_ctx(), manual_status_by_email={"carolinalobo@pharmaisa.cl": "inactive"},
    )
    assert result.eligible is False
    assert result.reasons == (REASON_MANUAL_INACTIVE,)


def test_carolina_lobo_cannot_be_selected() -> None:
    """Direct regression for the seeded Pharma Isa fact."""
    result = evaluate_campaign_eligibility(
        contact_email="CarolinaLobo@PharmaIsa.CL", institution_name="Pharma Isa - Control de Calidad",
        gate_ctx=_permissive_ctx(), manual_status_by_email={"carolinalobo@pharmaisa.cl": "inactive"},
    )
    assert result.eligible is False
    assert result.reasons == (REASON_MANUAL_INACTIVE,)


def test_manual_hold_blocks() -> None:
    result = evaluate_campaign_eligibility(
        contact_email="a@b.cl", institution_name=None,
        gate_ctx=_permissive_ctx(), manual_status_by_email={"a@b.cl": "hold"},
    )
    assert result.eligible is False
    assert result.reasons == (REASON_MANUAL_HOLD,)


def test_active_manual_status_does_not_imply_consent_or_bypass_other_gates() -> None:
    """cristianrios@pharmaisa.cl is 'active' but was already Sent-history contacted — still blocked."""
    ctx = _permissive_ctx(sent_recipient_norms=frozenset({"cristianrios@pharmaisa.cl"}))
    result = evaluate_campaign_eligibility(
        contact_email="cristianrios@pharmaisa.cl", institution_name="Pharma Isa",
        gate_ctx=ctx, manual_status_by_email={"cristianrios@pharmaisa.cl": "active"},
    )
    assert result.eligible is False
    assert result.reasons == (REASON_SENT_HISTORY,)


def test_active_manual_status_with_permissive_gate_is_eligible() -> None:
    result = evaluate_campaign_eligibility(
        contact_email="jeanettetorres@pharmaisa.cl", institution_name="Pharma Isa",
        gate_ctx=_permissive_ctx(), manual_status_by_email={"jeanettetorres@pharmaisa.cl": "active"},
    )
    assert result.eligible is True


def test_no_manual_status_falls_through_to_canonical_gate_suppression() -> None:
    ctx = _permissive_ctx(suppressed_norms=frozenset({"x@y.cl"}))
    result = evaluate_campaign_eligibility(
        contact_email="x@y.cl", institution_name=None, gate_ctx=ctx, manual_status_by_email={},
    )
    assert result.eligible is False
    assert result.reasons == (REASON_SUPPRESSION,)


def test_domain_suppression_delegates_to_canonical_gate() -> None:
    ctx = _permissive_ctx(suppressed_contact_domains=frozenset({"blocked.cl"}))
    result = evaluate_campaign_eligibility(
        contact_email="a@blocked.cl", institution_name=None, gate_ctx=ctx, manual_status_by_email={},
    )
    assert result.eligible is False
    assert result.reasons == (REASON_DOMAIN_SUPPRESSION,)


def test_supplier_domain_delegates_to_canonical_gate() -> None:
    ctx = _permissive_ctx(supplier_domains=frozenset({"kalstein.cl"}))
    result = evaluate_campaign_eligibility(
        contact_email="sales@kalstein.cl", institution_name=None, gate_ctx=ctx, manual_status_by_email={},
    )
    assert result.eligible is False
    assert result.reasons == (REASON_SUPPLIER_DOMAIN,)
