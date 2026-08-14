"""Participation-eligibility classification from procurement-method evidence.

Equipment recognition, acquisition truth, and participation eligibility are
independent questions (see PR1/PR2). A tender can be a genuine equipment
purchase and still be one OrigenLab cannot quote on, if the procurement is a
closed/restricted procedure OrigenLab was not confirmed to be invited to.

``procurement_method`` carries either a raw ChileCompra Ticket ``Tipo`` code
(two-letter, e.g. "LP"/"CO") or a raw OCDS ``procurementMethod`` string
(lowercase English word, e.g. "open"/"selective") depending on which source
produced the coalesced tender. The two vocabularies do not overlap lexically,
so classification does not need to track which source contributed the value.

This module only classifies; it never asserts a specific invited-supplier
list or count. No invitee-identity data or OrigenLab canonical supplier
identifier is currently ingested anywhere in this pipeline (see PR3 audit) —
"restricted" here means "invitation required, OrigenLab's inclusion is
unconfirmed by available evidence", not "OrigenLab was excluded".
"""

from __future__ import annotations

from typing import Final

# ChileCompra Ticket API ``Tipo`` codes.
# Source: https://www.chilecompra.cl/api/ (official ChileCompra API documentation).
# LR additionally confirmed public per the current ChileCompra glossary:
# https://www.chilecompra.cl/glosario/lr/
# Deliberately does not use CodigoTipo: the cached ISP value (CodigoTipo=3) does
# not fit the older, undocumented CodigoTipo binary convention this repo used
# to assume, so only the lettered Tipo code is treated as authoritative here.
TICKET_PUBLIC_TIPO_CODES: Final[frozenset[str]] = frozenset(
    {"L1", "LE", "LP", "LS", "LR"}
)
TICKET_PRIVATE_TIPO_CODES: Final[frozenset[str]] = frozenset(
    {"A1", "B1", "E1", "F1", "J1", "CO", "B2", "E2"}
)
TICKET_DIRECT_TIPO_CODES: Final[frozenset[str]] = frozenset(
    {"A2", "D1", "C2", "C1", "F2", "F3", "G2", "G1", "R1", "CA", "SE"}
)
TICKET_RESTRICTED_TIPO_CODES: Final[frozenset[str]] = (
    TICKET_PRIVATE_TIPO_CODES | TICKET_DIRECT_TIPO_CODES
)

# OCDS procurementMethod codelist.
# Source: https://standard.open-contracting.org/latest/en/schema/codelists/#method
# "open" is available to all interested suppliers; "selective", "limited", and
# "direct" are not unrestricted quotation opportunities per the OCDS definition.
OCDS_OPEN_METHODS: Final[frozenset[str]] = frozenset({"open"})
OCDS_RESTRICTED_METHODS: Final[frozenset[str]] = frozenset(
    {"selective", "limited", "direct"}
)

ELIGIBILITY_OPEN_PUBLIC: Final[str] = "open_public"
ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED: Final[str] = (
    "restricted_invitation_unconfirmed"
)
ELIGIBILITY_UNKNOWN: Final[str] = "unknown"

PROCUREMENT_ELIGIBILITY_STATES: Final[tuple[str, ...]] = (
    ELIGIBILITY_OPEN_PUBLIC,
    ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED,
    ELIGIBILITY_UNKNOWN,
)

# Deterministic blocker/reason codes for current_opportunity_blockers().
RESTRICTED_PROCUREMENT_INVITATION_UNCONFIRMED_REASON: Final[str] = (
    "restricted_procurement_invitation_unconfirmed"
)
PROCUREMENT_METHOD_UNKNOWN_OR_UNMAPPED_REASON: Final[str] = (
    "procurement_method_unknown_or_unmapped"
)


def classify_procurement_eligibility(procurement_method: str | None) -> str:
    """Classify raw procurement-method evidence into one of three states.

    1. Ticket public codes (including LR) -> open_public.
    2. Ticket private or direct/non-open codes -> restricted_invitation_unconfirmed.
    3. OCDS "open" -> open_public.
    4. OCDS "selective"/"limited"/"direct" -> restricted_invitation_unconfirmed.
    5. Missing or unmapped method evidence -> unknown.
    """
    method = (procurement_method or "").strip()
    if not method:
        return ELIGIBILITY_UNKNOWN
    upper = method.upper()
    if upper in TICKET_PUBLIC_TIPO_CODES:
        return ELIGIBILITY_OPEN_PUBLIC
    if upper in TICKET_RESTRICTED_TIPO_CODES:
        return ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
    lower = method.lower()
    if lower in OCDS_OPEN_METHODS:
        return ELIGIBILITY_OPEN_PUBLIC
    if lower in OCDS_RESTRICTED_METHODS:
        return ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED
    return ELIGIBILITY_UNKNOWN


__all__ = [
    "ELIGIBILITY_OPEN_PUBLIC",
    "ELIGIBILITY_RESTRICTED_INVITATION_UNCONFIRMED",
    "ELIGIBILITY_UNKNOWN",
    "OCDS_OPEN_METHODS",
    "OCDS_RESTRICTED_METHODS",
    "PROCUREMENT_ELIGIBILITY_STATES",
    "PROCUREMENT_METHOD_UNKNOWN_OR_UNMAPPED_REASON",
    "RESTRICTED_PROCUREMENT_INVITATION_UNCONFIRMED_REASON",
    "TICKET_DIRECT_TIPO_CODES",
    "TICKET_PRIVATE_TIPO_CODES",
    "TICKET_PUBLIC_TIPO_CODES",
    "TICKET_RESTRICTED_TIPO_CODES",
    "classify_procurement_eligibility",
]
