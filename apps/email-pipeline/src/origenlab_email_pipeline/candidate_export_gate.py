"""Shared pre-export eligibility for cold outreach (lead path + contact_master path).

Evaluation is complete, not short-circuiting: ``ExportGateResult.reasons`` carries every
rule the candidate failed, in a fixed order. ``reasons[0]`` remains the first-triggered
rule, so callers that report a single reason keep their current behaviour.

Single policy implementation: operator CLIs and read modules must not duplicate these rules.
Reuses email suppression, optional domain suppression (``contact_domain_suppression``),
Sent parse, outreach state, supplier_master, and noise heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.marketing_contact_noise import (
    marketing_outreach_noise_email,
    marketing_outreach_noise_organization_guess,
)
from origenlab_email_pipeline.marketing_supplier_domains import is_supplier_email_domain

# Stable reason codes (CSV, logs, tests).
REASON_INVALID_EMAIL = "invalid_email"
REASON_INTERNAL_DOMAIN = "internal_domain"
REASON_SUPPRESSION = "suppression"
REASON_DOMAIN_SUPPRESSION = "domain_suppression"
REASON_SENT_HISTORY = "sent_history"
REASON_OUTREACH_CONTACTED = "outreach_contacted"
REASON_OUTREACH_REPLIED = "outreach_replied"
REASON_OUTREACH_SNOOZED = "outreach_snoozed"
REASON_SUPPLIER_DOMAIN = "supplier_domain"
REASON_NOISE_EMAIL = "noise_email"
REASON_NOISE_ORGANIZATION = "noise_organization"

_OUTREACH_REASON = {
    "contacted": REASON_OUTREACH_CONTACTED,
    "replied": REASON_OUTREACH_REPLIED,
    "snoozed": REASON_OUTREACH_SNOOZED,
}


@dataclass(frozen=True)
class GateContext:
    """Inputs for eligibility. Build once per export run."""

    sent_recipient_norms: frozenset[str]
    suppressed_norms: frozenset[str]
    outreach_state_by_email: dict[str, str]
    supplier_domains: frozenset[str]
    blocked_domains: frozenset[str]
    #: Registrable domains (and subdomains) blocked via ``contact_domain_suppression``.
    suppressed_contact_domains: frozenset[str] = frozenset()
    skip_noise_filter: bool = False
    skip_supplier_domain_filter: bool = False
    #: Tighter ``marketing_contact_noise`` rules for ``contact_master`` mail-graph exports.
    strict_contact_graph_noise: bool = False


@dataclass(frozen=True)
class ExportGateResult:
    eligible: bool
    """Every rule this candidate failed, in the fixed evaluation order.

    Evaluation is **complete**: it does not stop at the first failure, so an operator who
    fixes one reason does not then discover a second. ``reasons[0]`` is still the
    first-triggered rule, which is the field every current caller reads.

    ``REASON_INVALID_EMAIL`` is the one terminal reason: with no usable mailbox there is
    nothing left to evaluate, so it is returned alone.
    """

    reasons: tuple[str, ...]


def email_domain_under_operator_domain_suppression(email_domain: str, suppressed: frozenset[str]) -> bool:
    """True when ``email_domain`` matches or is a subdomain of a suppressed registrable domain."""
    d = (email_domain or "").strip().lower()
    if not d or not suppressed:
        return False
    if d in suppressed:
        return True
    return any(d.endswith("." + s) for s in suppressed if s)


def normalize_export_email(contact_email: str) -> str | None:
    """Match ``marketing_export_context.norm_lead_email`` / contact_master export: first mailbox."""
    raw = (contact_email or "").strip()
    if not raw:
        return None
    found = emails_in(raw)
    if not found:
        return None
    return found[0]


def evaluate_export_eligibility(
    *,
    contact_email: str,
    institution_name: str | None,
    ctx: GateContext,
) -> ExportGateResult:
    """Evaluate every rule and report every failure, in the fixed evaluation order."""
    em = normalize_export_email(contact_email)
    if not em or "@" not in em:
        # Terminal: no mailbox, so no domain, suppression or noise rule can be evaluated.
        return ExportGateResult(eligible=False, reasons=(REASON_INVALID_EMAIL,))

    reasons: list[str] = []
    dom = em.rsplit("@", 1)[-1].lower()

    if dom in ctx.blocked_domains:
        reasons.append(REASON_INTERNAL_DOMAIN)

    if em in ctx.suppressed_norms:
        reasons.append(REASON_SUPPRESSION)

    if email_domain_under_operator_domain_suppression(dom, ctx.suppressed_contact_domains):
        reasons.append(REASON_DOMAIN_SUPPRESSION)

    if em in ctx.sent_recipient_norms:
        reasons.append(REASON_SENT_HISTORY)

    st = (ctx.outreach_state_by_email or {}).get(em)
    if st:
        outreach_reason = _OUTREACH_REASON.get(st)
        if outreach_reason:
            reasons.append(outreach_reason)

    if not ctx.skip_supplier_domain_filter and ctx.supplier_domains:
        if is_supplier_email_domain(em, ctx.supplier_domains):
            reasons.append(REASON_SUPPLIER_DOMAIN)

    if not ctx.skip_noise_filter:
        if marketing_outreach_noise_email(
            em, strict_contact_graph=ctx.strict_contact_graph_noise
        ):
            reasons.append(REASON_NOISE_EMAIL)
        if marketing_outreach_noise_organization_guess(institution_name or ""):
            reasons.append(REASON_NOISE_ORGANIZATION)

    return ExportGateResult(eligible=not reasons, reasons=tuple(reasons))
