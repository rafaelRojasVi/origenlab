"""Focused tests for PR3 procurement-method eligibility classification."""

from __future__ import annotations

import pytest

from origenlab_email_pipeline.commercial_procurement_institution_prospects.procurement_eligibility import (
    ELIGIBILITY_OPEN_PUBLIC,
    ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED,
    ELIGIBILITY_UNKNOWN,
    TICKET_DIRECT_TIPO_CODES,
    TICKET_PRIVATE_TIPO_CODES,
    TICKET_PUBLIC_TIPO_CODES,
    classify_procurement_eligibility,
)


@pytest.mark.parametrize("code", sorted(TICKET_PUBLIC_TIPO_CODES))
def test_ticket_public_codes_are_open_public(code: str) -> None:
    """Required regression 7 (LR) plus every other documented public code:
    L1, LE, LP, LS, LR -> open_public.
    """
    assert classify_procurement_eligibility(code) == ELIGIBILITY_OPEN_PUBLIC


@pytest.mark.parametrize("code", sorted(TICKET_PRIVATE_TIPO_CODES))
def test_ticket_private_codes_are_restricted(code: str) -> None:
    assert (
        classify_procurement_eligibility(code)
        == ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
    )


@pytest.mark.parametrize("code", sorted(TICKET_DIRECT_TIPO_CODES))
def test_ticket_direct_nonopen_codes_are_restricted(code: str) -> None:
    """Required regression 10: Ticket direct/non-open type is blocked."""
    assert (
        classify_procurement_eligibility(code)
        == ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
    )


def test_ocds_open_is_allowed_selective_limited_direct_are_restricted() -> None:
    """Required regression 11."""
    assert classify_procurement_eligibility("open") == ELIGIBILITY_OPEN_PUBLIC
    for method in ("selective", "limited", "direct"):
        assert (
            classify_procurement_eligibility(method)
            == ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
        )


def test_ticket_codes_are_case_insensitive() -> None:
    assert classify_procurement_eligibility("co") == (
        ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
    )
    assert classify_procurement_eligibility("lp") == ELIGIBILITY_OPEN_PUBLIC


def test_ocds_methods_are_case_insensitive() -> None:
    assert classify_procurement_eligibility("OPEN") == ELIGIBILITY_OPEN_PUBLIC
    assert classify_procurement_eligibility("SELECTIVE") == (
        ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
    )


@pytest.mark.parametrize("value", [None, "", "   ", "XYZ_UNMAPPED", "co2", "opened"])
def test_missing_or_unmapped_method_is_unknown(value: str | None) -> None:
    """Required regression 12: missing/unmapped method is classified unknown
    (queue-blocking behavior is asserted separately at the planner/queue level).
    """
    assert classify_procurement_eligibility(value) == ELIGIBILITY_UNKNOWN


def test_isp_sag_chiloe_real_codes_classify_correctly() -> None:
    """Direct classification check for the three PR3 evidence tenders."""
    assert classify_procurement_eligibility("CO") == (  # ISP
        ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
    )
    assert classify_procurement_eligibility("LP") == ELIGIBILITY_OPEN_PUBLIC  # SAG
    assert classify_procurement_eligibility("LE") == ELIGIBILITY_OPEN_PUBLIC  # Chiloé
