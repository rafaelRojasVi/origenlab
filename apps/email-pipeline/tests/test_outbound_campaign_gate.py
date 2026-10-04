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


# --- audience kind: warm campaigns waive only the two cold-list memories --------------------

from origenlab_email_pipeline.candidate_export_gate import (  # noqa: E402
    REASON_NOISE_EMAIL,
    REASON_OUTREACH_CONTACTED,
    REASON_OUTREACH_REPLIED,
    REASON_OUTREACH_SNOOZED,
)
from origenlab_email_pipeline.outbound_campaign_gate import (  # noqa: E402
    AUDIENCE_COLD,
    AUDIENCE_WARM,
    WARM_WAIVED_REASONS,
)

import pytest  # noqa: E402


def _prior_contact_ctx(**overrides) -> GateContext:
    """A mailbox contacto@ already wrote to, and that an earlier batch marked contacted."""
    base = dict(
        sent_recipient_norms=frozenset({"cliente@lab.cl"}),
        outreach_state_by_email={"cliente@lab.cl": "contacted"},
    )
    base.update(overrides)
    return _permissive_ctx(**base)


def test_cold_campaign_still_refuses_prior_contact() -> None:
    result = evaluate_campaign_eligibility(
        contact_email="cliente@lab.cl", institution_name=None, gate_ctx=_prior_contact_ctx(),
        manual_status_by_email={}, audience_kind=AUDIENCE_COLD,
    )
    assert result.eligible is False
    assert result.reasons == (REASON_SENT_HISTORY, REASON_OUTREACH_CONTACTED)


def test_default_audience_is_cold() -> None:
    explicit = evaluate_campaign_eligibility(
        contact_email="cliente@lab.cl", institution_name=None, gate_ctx=_prior_contact_ctx(),
        manual_status_by_email={}, audience_kind=AUDIENCE_COLD,
    )
    implicit = evaluate_campaign_eligibility(
        contact_email="cliente@lab.cl", institution_name=None, gate_ctx=_prior_contact_ctx(),
        manual_status_by_email={},
    )
    assert implicit == explicit


def test_warm_campaign_accepts_prior_contact() -> None:
    result = evaluate_campaign_eligibility(
        contact_email="cliente@lab.cl", institution_name=None, gate_ctx=_prior_contact_ctx(),
        manual_status_by_email={}, audience_kind=AUDIENCE_WARM,
    )
    assert result.eligible is True
    assert result.reasons == ()


def test_warm_waives_exactly_sent_history_and_outreach_contacted() -> None:
    assert WARM_WAIVED_REASONS == frozenset({REASON_SENT_HISTORY, REASON_OUTREACH_CONTACTED})


@pytest.mark.parametrize(
    ("ctx", "expected"),
    [
        (_prior_contact_ctx(suppressed_norms=frozenset({"cliente@lab.cl"})), REASON_SUPPRESSION),
        (_prior_contact_ctx(suppressed_contact_domains=frozenset({"lab.cl"})), REASON_DOMAIN_SUPPRESSION),
        (_prior_contact_ctx(supplier_domains=frozenset({"lab.cl"})), REASON_SUPPLIER_DOMAIN),
        (_prior_contact_ctx(outreach_state_by_email={"cliente@lab.cl": "replied"}), REASON_OUTREACH_REPLIED),
        (_prior_contact_ctx(outreach_state_by_email={"cliente@lab.cl": "snoozed"}), REASON_OUTREACH_SNOOZED),
    ],
)
def test_warm_keeps_every_other_refusal(ctx: GateContext, expected: str) -> None:
    result = evaluate_campaign_eligibility(
        contact_email="cliente@lab.cl", institution_name=None, gate_ctx=ctx,
        manual_status_by_email={}, audience_kind=AUDIENCE_WARM,
    )
    assert result.eligible is False
    assert expected in result.reasons
    assert not (set(result.reasons) & WARM_WAIVED_REASONS)


def test_warm_keeps_noise_mailboxes_out() -> None:
    ctx = _permissive_ctx(sent_recipient_norms=frozenset({"no-reply@lab.cl"}), strict_contact_graph_noise=True)
    result = evaluate_campaign_eligibility(
        contact_email="no-reply@lab.cl", institution_name=None, gate_ctx=ctx,
        manual_status_by_email={}, audience_kind=AUDIENCE_WARM,
    )
    assert result.eligible is False
    assert REASON_NOISE_EMAIL in result.reasons


def test_warm_manual_inactive_still_hard_blocks() -> None:
    result = evaluate_campaign_eligibility(
        contact_email="cliente@lab.cl", institution_name=None, gate_ctx=_prior_contact_ctx(),
        manual_status_by_email={"cliente@lab.cl": "inactive"}, audience_kind=AUDIENCE_WARM,
    )
    assert result.eligible is False
    assert result.reasons == (REASON_MANUAL_INACTIVE,)


def test_unknown_audience_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        evaluate_campaign_eligibility(
            contact_email="cliente@lab.cl", institution_name=None, gate_ctx=_permissive_ctx(),
            manual_status_by_email={}, audience_kind="lukewarm",
        )
