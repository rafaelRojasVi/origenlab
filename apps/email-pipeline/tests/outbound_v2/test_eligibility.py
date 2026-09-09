"""evaluate_recipient_eligibility: pure, complete, closed-vocabulary, fail-closed."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from origenlab_email_pipeline.outbound_v2 import (
    CampaignPolicy,
    ContactControlIndex,
    RecipientCandidate,
    RecontactOverride,
    evaluate_recipient_eligibility,
    normalize_address,
)
from origenlab_email_pipeline.outbound_v2.reasons import (
    EXCLUSION_VOCABULARY,
    REASON_ALREADY_IN_AUDIENCE,
    REASON_BLOCK,
    REASON_BLOCK_DOMAIN,
    REASON_COOLDOWN,
    REASON_INVALID_ADDRESS,
    REASON_MANUAL_HOLD,
    REASON_MANUAL_INACTIVE,
    REASON_POLICY_INTERNAL_DOMAIN,
    REASON_POLICY_NO_CHANNEL,
    REASON_POLICY_NOISE_ADDRESS,
    REASON_POLICY_NOISE_ORGANIZATION,
    REASON_POLICY_SUPPLIER,
    REASON_PRECHECK_BLOCK,
    REASON_PRECHECK_SWITCH,
    REASON_PRIOR_CONTACT,
    REASON_PRIOR_REPLY,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _policy(**kwargs) -> CampaignPolicy:
    base = dict(
        max_sends=100,
        recontact_interval_days=180,
        internal_domains=frozenset({"origenlab.cl"}),
    )
    base.update(kwargs)
    return CampaignPolicy(**base)


def _candidate(address: str = "compras@universidad.cl", **kwargs) -> RecipientCandidate:
    return RecipientCandidate(address_norm=address, **kwargs)


def _evaluate(candidate=None, controls=None, policy=None, **kwargs):
    return evaluate_recipient_eligibility(
        candidate=candidate or _candidate(),
        controls=controls or ContactControlIndex(),
        policy=policy or _policy(),
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


# ── shape ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Compras@Universidad.CL", "compras@universidad.cl"),
        ("  lab@uni.example  ", "lab@uni.example"),
        ("not-an-email", None),
        ("two@addresses.cl, other@x.cl", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_address_matches_the_slice0_shape(raw, expected) -> None:
    assert normalize_address(raw) == expected


def test_clean_candidate_is_eligible_with_no_reasons() -> None:
    verdict = _evaluate()
    assert verdict.eligible is True
    assert verdict.reasons == ()
    assert verdict.evaluated_at == NOW


def test_every_reason_is_in_the_database_vocabulary() -> None:
    controls = ContactControlIndex(
        blocked_addresses=frozenset({"noreply@ohaus.com"}),
        blocked_domains=frozenset({"ohaus.com"}),
        prior_contact_addresses=frozenset({"noreply@ohaus.com"}),
        replied_addresses=frozenset({"noreply@ohaus.com"}),
        cooldown_until={"noreply@ohaus.com": NOW + timedelta(days=30)},
        manual_status={"noreply@ohaus.com": "hold"},
    )
    verdict = _evaluate(
        candidate=_candidate(
            "noreply@ohaus.com", organization_name="DHL Express", precheck_verdict="block"
        ),
        controls=controls,
        policy=_policy(supplier_domains=frozenset({"ohaus.com"}), require_contact_point=True),
    )
    assert set(verdict.reasons) <= EXCLUSION_VOCABULARY


# ── purity and determinism ──────────────────────────────────────────────────────────────


def test_same_inputs_always_produce_the_same_verdict() -> None:
    args = dict(
        candidate=_candidate("lab@uni.example"),
        controls=ContactControlIndex(prior_contact_addresses=frozenset({"lab@uni.example"})),
        policy=_policy(),
    )
    assert _evaluate(**args) == _evaluate(**args)


def test_now_is_an_argument_not_a_clock_read() -> None:
    """The same cooldown reads differently at two instants — and only because ``now`` moved."""
    controls = ContactControlIndex(
        cooldown_until={"lab@uni.example": NOW + timedelta(days=1)}
    )
    during = _evaluate(candidate=_candidate("lab@uni.example"), controls=controls, now=NOW)
    after = _evaluate(
        candidate=_candidate("lab@uni.example"),
        controls=controls,
        now=NOW + timedelta(days=2),
    )
    assert during.reasons == (REASON_COOLDOWN,)
    assert after.eligible is True


# ── completeness ────────────────────────────────────────────────────────────────────────


def test_evaluation_is_complete_and_ordered() -> None:
    """Clearing one reason must never reveal a surprise second one."""
    controls = ContactControlIndex(
        blocked_addresses=frozenset({"noreply@ohaus.com"}),
        blocked_domains=frozenset({"ohaus.com"}),
        prior_contact_addresses=frozenset({"noreply@ohaus.com"}),
        replied_addresses=frozenset({"noreply@ohaus.com"}),
        manual_status={"noreply@ohaus.com": "inactive"},
    )
    verdict = _evaluate(
        candidate=_candidate(
            "noreply@ohaus.com", organization_name="DHL Express", precheck_verdict="switch"
        ),
        controls=controls,
        policy=_policy(supplier_domains=frozenset({"ohaus.com"}), require_contact_point=True),
    )
    assert verdict.reasons == (
        REASON_MANUAL_INACTIVE,
        REASON_BLOCK,
        REASON_BLOCK_DOMAIN,
        REASON_PRIOR_CONTACT,
        REASON_PRIOR_REPLY,
        REASON_PRECHECK_SWITCH,
        REASON_POLICY_NO_CHANNEL,
        REASON_POLICY_SUPPLIER,
        REASON_POLICY_NOISE_ADDRESS,
        REASON_POLICY_NOISE_ORGANIZATION,
    )


def test_invalid_address_is_the_one_terminal_reason() -> None:
    verdict = _evaluate(
        candidate=_candidate("not-an-email", organization_name="DHL Express"),
        controls=ContactControlIndex(manual_status={"not-an-email": "hold"}),
    )
    assert verdict.reasons == (REASON_INVALID_ADDRESS,)


# ── individual rules ────────────────────────────────────────────────────────────────────


def test_internal_domain_is_excluded() -> None:
    assert _evaluate(candidate=_candidate("ventas@origenlab.cl")).reasons == (
        REASON_POLICY_INTERNAL_DOMAIN,
    )


@pytest.mark.parametrize(
    "status,expected",
    [("inactive", REASON_MANUAL_INACTIVE), ("hold", REASON_MANUAL_HOLD)],
)
def test_manual_sidecar_hard_blocks(status: str, expected: str) -> None:
    controls = ContactControlIndex(manual_status={"lab@uni.example": status})
    assert _evaluate(candidate=_candidate("lab@uni.example"), controls=controls).reasons == (
        expected,
    )


def test_manual_active_is_not_consent_and_does_not_relax_any_rule() -> None:
    controls = ContactControlIndex(
        manual_status={"lab@uni.example": "active"},
        blocked_addresses=frozenset({"lab@uni.example"}),
    )
    assert _evaluate(candidate=_candidate("lab@uni.example"), controls=controls).reasons == (
        REASON_BLOCK,
    )


def test_a_blocked_registrable_domain_covers_its_subdomains() -> None:
    controls = ContactControlIndex(blocked_domains=frozenset({"competidor.cl"}))
    verdict = _evaluate(candidate=_candidate("x@lab.competidor.cl"), controls=controls)
    assert verdict.reasons == (REASON_BLOCK_DOMAIN,)


def test_supplier_domain_excluded_unless_the_campaign_includes_suppliers() -> None:
    policy = _policy(supplier_domains=frozenset({"ohaus.com"}))
    assert _evaluate(candidate=_candidate("ventas@ohaus.com"), policy=policy).reasons == (
        REASON_POLICY_SUPPLIER,
    )
    including = _policy(
        supplier_domains=frozenset({"ohaus.com"}), policy_include_suppliers=True
    )
    assert _evaluate(candidate=_candidate("ventas@ohaus.com"), policy=including).eligible is True


def test_missing_contact_point_is_fail_closed_when_the_campaign_requires_one() -> None:
    policy = _policy(require_contact_point=True)
    assert _evaluate(policy=policy).reasons == (REASON_POLICY_NO_CHANNEL,)
    assert _evaluate(candidate=_candidate(contact_point_id="cp-1"), policy=policy).eligible is True


@pytest.mark.parametrize(
    "verdict_value,expected",
    [("block", REASON_PRECHECK_BLOCK), ("switch", REASON_PRECHECK_SWITCH)],
)
def test_commercial_precheck_verdicts_carry_through(verdict_value: str, expected: str) -> None:
    assert _evaluate(candidate=_candidate(precheck_verdict=verdict_value)).reasons == (expected,)


def test_noise_rules_can_be_skipped_wholesale() -> None:
    noisy = _candidate("noreply@uni.example", organization_name="DHL Express")
    assert set(_evaluate(candidate=noisy).reasons) == {
        REASON_POLICY_NOISE_ADDRESS,
        REASON_POLICY_NOISE_ORGANIZATION,
    }
    assert _evaluate(candidate=noisy, policy=_policy(skip_noise_filter=True)).eligible is True


def test_already_in_audience_is_reported_like_any_other_reason() -> None:
    verdict = _evaluate(already_in_audience=True)
    assert verdict.reasons == (REASON_ALREADY_IN_AUDIENCE,)


# ── recontact override ──────────────────────────────────────────────────────────────────


def test_override_clears_only_the_recontact_family() -> None:
    controls = ContactControlIndex(
        prior_contact_addresses=frozenset({"lab@uni.example"}),
        replied_addresses=frozenset({"lab@uni.example"}),
        cooldown_until={"lab@uni.example": NOW + timedelta(days=10)},
    )
    override = RecontactOverride(operator_id="op-1", reason="cliente pidió seguimiento")
    verdict = _evaluate(
        candidate=_candidate("lab@uni.example"), controls=controls, override=override
    )
    assert verdict.eligible is True
    assert verdict.overridden_reasons == (
        REASON_PRIOR_CONTACT,
        REASON_PRIOR_REPLY,
        REASON_COOLDOWN,
    )
    assert verdict.override is override


def test_override_never_clears_a_block_or_a_policy_rule() -> None:
    controls = ContactControlIndex(
        blocked_addresses=frozenset({"lab@uni.example"}),
        prior_contact_addresses=frozenset({"lab@uni.example"}),
        manual_status={"lab@uni.example": "hold"},
    )
    verdict = _evaluate(
        candidate=_candidate("lab@uni.example"),
        controls=controls,
        override=RecontactOverride(operator_id="op-1", reason="insistir"),
    )
    assert verdict.eligible is False
    assert verdict.reasons == (REASON_MANUAL_HOLD, REASON_BLOCK)
    assert verdict.overridden_reasons == (REASON_PRIOR_CONTACT,)


def test_an_override_that_clears_nothing_is_not_recorded() -> None:
    verdict = _evaluate(override=RecontactOverride(operator_id="op-1", reason="por si acaso"))
    assert verdict.eligible is True
    assert verdict.override is None
    assert verdict.overridden_reasons == ()
